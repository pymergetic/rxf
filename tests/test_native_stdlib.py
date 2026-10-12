"""The stdlib leaves (memory, runtime, collections, text) behave the same on every machine.

One script of calls, with refusals as well as successes, runs on a context in
memory: as x86_64 and aarch64 machine code in an emulator, and as wasm32 in
node. After every call the status and the whole memory must be equal across the
machines. The leaves take structures by value, so this is also the proof that
the calling convention of each machine reaches them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from . import scenario
from .emulator import Blob, Emulator, Integer
from .scenario import Handle, Step
from .wasm import module_of

HARNESS = Path(__file__).with_name("wasm_steps.js")
MASK = 2**64 - 1
Outcome = tuple[int, bytes]


def handle_bytes(handle: Handle) -> bytes:
    return b"".join(part.to_bytes(8, "little") for part in handle)


def emulated(machine: str) -> list[Outcome]:
    emulator = Emulator(machine)
    emulator.map(scenario.BASE, 0x10000)
    emulator.write(scenario.BASE, scenario.image())
    code = scenario.stdlib_code()
    addresses = {symbol: emulator.load(code[symbol][machine]) for symbol in code}
    outcomes: list[Outcome] = []
    for step in scenario.steps():
        arguments = []
        for letter, value in zip(
            scenario.SPEC[step.symbol], step.arguments, strict=True
        ):
            if letter == "H":
                assert isinstance(value, tuple)
                arguments.append(Blob(handle_bytes(value)))
            else:
                assert isinstance(value, int)
                arguments.append(Integer(value & MASK))
        status = emulator.call(addresses[step.symbol], arguments)
        outcomes.append((status, emulator.read(scenario.BASE, scenario.SIZE)))
    return outcomes


def wasm_type(letter: str) -> str:
    return "i64" if letter == "q" else "i32"


def wasm_request(step: Step, slot: int) -> dict:
    """The call of one step: handles go to memory, their address is the argument."""
    args, writes = [], []
    for letter, value in zip(scenario.SPEC[step.symbol], step.arguments, strict=True):
        if letter == "H":
            assert isinstance(value, tuple)
            where = scenario.COPIES + slot * 32
            slot += 1
            writes.append([where, handle_bytes(value).hex()])
            value = where
        assert isinstance(value, int)
        args.append([wasm_type(letter), str(value)])
    return {"fn": step.symbol, "args": args, "writes": writes}


def in_node() -> list[Outcome]:
    code = scenario.stdlib_code()
    module = module_of(
        [
            (
                symbol,
                [wasm_type(letter) for letter in scenario.SPEC[symbol]],
                code[symbol]["wasm32"],
            )
            for symbol in code
        ]
    )
    request = {
        "module": module.hex(),
        "base": scenario.BASE,
        "image": scenario.image().hex(),
        "snapshot": scenario.SIZE,
        "steps": [wasm_request(step, 0) for step in scenario.steps()],
    }
    process = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return [
        (r["status"], bytes.fromhex(r["memory"])) for r in json.loads(process.stdout)
    ]


def test_the_script_covers_every_stdlib_leaf():
    assert set(scenario.stdlib_code()) == scenario.SYMBOLS
    assert {step.symbol for step in scenario.steps()} == scenario.SYMBOLS
    assert set(scenario.SPEC) == scenario.SYMBOLS


def test_the_script_succeeds_and_refuses_where_it_means_to():
    statuses = [status for status, _ in emulated("x86_64")]
    assert 0 in statuses and len(set(statuses)) >= 6
    by_symbol: dict[str, set[int]] = {}
    for step, status in zip(scenario.steps(), statuses, strict=True):
        by_symbol.setdefault(step.symbol, set()).add(status)
    assert all(0 in seen for seen in by_symbol.values()), by_symbol


def test_aarch64_leaves_do_what_the_x86_64_leaves_do():
    want, have = emulated("x86_64"), emulated("aarch64")
    steps = scenario.steps()
    different = [
        (index, steps[index].symbol, w[0], h[0])
        for index, (w, h) in enumerate(zip(want, have, strict=True))
        if w != h
    ]
    assert different == []


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wasm_leaves_do_what_the_x86_64_leaves_do():
    want, have = emulated("x86_64"), in_node()
    steps = scenario.steps()
    different = [
        (index, steps[index].symbol, w[0], h[0])
        for index, (w, h) in enumerate(zip(want, have, strict=True))
        if w != h
    ]
    assert different == []
