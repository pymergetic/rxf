"""A function of the linked starter, run on three machines.

The image is linked once per machine (``tools/native/link.py``): it is the memory,
not an input to something that builds memory. The test writes it at its base,
points the interpreter at the Code cell of a function, and runs it. What a run
leaves in the image is compared by digest, except the function slots, which
are addresses of the machine.
"""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
from dataclasses import dataclass

import link
import programs

from pymergetic.rxftk.core.object import Image

from . import graph_world as world
from .emulator import Blob, Emulator, Integer
from .graph_reference import Reference
from .wasm import module_of

BASE = 0x100000
PAGE = 0x10000


@dataclass(frozen=True)
class Run:
    function: str  # the path of the function
    args: bytes
    handle: tuple[
        int, int, int
    ]  # how the program is found: entry id, generation, offset


@dataclass(frozen=True)
class Result:
    status: int
    out: bytes
    sha256: str


def runs(image: Image, linked: link.Linked, picks: list[str]) -> list[Run]:
    """Each function with three sets of arguments."""
    objects = [p for p in linked.ids if p.startswith("pymergetic.rxf.application.")]
    found = []
    for path in picks:
        program = bytes.fromhex(
            programs.fields_of(image, image.handle(f"{path}.bytecode"))["data"]
        )
        for variant in range(3):
            target = linked.ids[objects[variant % len(objects)]]
            args, _ = world.arguments(program, variant, target)
            found.append(Run(path, args, (linked.ids[f"{path}.bytecode"], 1, 0)))
    return found


def digest(linked: link.Linked, memory: bytes) -> str:
    hashed = hashlib.sha256()
    for start, end in linked.compared():
        hashed.update(memory[start:end])
    return hashed.hexdigest()


def result(linked: link.Linked, status: int, out: bytes, memory: bytes) -> Result:
    return Result(status, out, digest(linked, memory))


def machine_code(image: Image, linked: link.Linked, emulator: Emulator) -> None:
    """The memory of the image, mapped where the machine code in it expects to be."""
    emulator.map(linked.base, len(linked.data))
    emulator.write(linked.base, linked.data)


def emulated(machine: str, linked: link.Linked, image: Image, run: Run) -> Result:
    emulator = Emulator(machine)
    machine_code(image, linked, emulator)
    assert linked.run is not None
    emulator.write(linked.args, run.args)
    status = emulator.call(
        linked.run,
        [
            Integer(linked.context),
            Blob(struct.pack("<3Q", *run.handle)),
            Integer(linked.args),
            Integer(len(run.args) // 8),
            Integer(linked.out),
        ],
    )
    return result(
        linked,
        status,
        emulator.read(linked.out, world.OUT_BYTES),
        emulator.read(linked.base, len(linked.data)),
    )


def referenced(linked: link.Linked, image: Image, run: Run) -> Result:
    emulator = Emulator("x86_64")
    machine_code(image, linked, emulator)
    emulator.write(linked.args, run.args)
    interpreter = Reference(
        emulator, linked.context, linked.frame, linked.slots, world.letters()
    )
    program = bytes.fromhex(
        programs.fields_of(image, image.handle(f"{run.function}.bytecode"))["data"]
    )
    status, out = interpreter.run(program, run.args, len(run.args) // 8)
    emulator.write(linked.out, out)
    return result(
        linked,
        status,
        emulator.read(linked.out, world.OUT_BYTES),
        emulator.read(linked.base, len(linked.data)),
    )


def report_line(image: Image, linked: link.Linked, pick: str) -> bytes:
    """The line a seat of the image reports for the entry ``pick``.

    What the interpreter of the same entry answers, spelled the way
    ``native/wrap/wrap.h`` reports it: every boot and every run of an artifact
    must say exactly this, byte for byte.
    """
    answered = referenced(linked, image, runs(image, linked, [pick])[0])
    out = int.from_bytes(answered.out[:8], "little")
    return f"rxf result {answered.status:08x} {out:016x}\n".encode()


def in_node(code: world.Code, linked: link.Linked, batch: list[Run]) -> list[Result]:
    """The same runs in wasm32: the image in linear memory, the leaves through table 0."""
    names = sorted(code.leaves["wasm32"])
    spec = world.letters()
    by_id = {value: key for key, value in link.graph_header.leaves().items()}
    module = module_of(
        [
            *(
                (
                    f"leaf{i}",
                    [world.wasm_type(c) for c in spec[by_id[i]]],
                    code.leaves["wasm32"][i],
                )
                for i in names
            ),
            ("run", ["i32", "i32", "i64", "i64", "i32"], code.run["wasm32"]),
        ],
        world.call_types(),
    )
    copy = (
        linked.scratch + 0x800
    )  # the handle a wasm caller passes by value, by address
    steps = [
        {
            "fn": "run",
            "args": [
                ["i32", str(linked.context)],
                ["i32", str(copy)],
                ["i64", str(linked.args)],
                ["i64", str(len(run.args) // 8)],
                ["i32", str(linked.out)],
            ],
            "writes": [
                [linked.args, run.args.hex()],
                [copy, struct.pack("<3Q", *run.handle).hex()],
            ],
            "read": [linked.out, world.OUT_BYTES],
        }
        for run in batch
    ]
    request = {
        "module": module.hex(),
        "base": linked.base,
        "image": linked.data.hex(),
        "pages": (linked.base + len(linked.data)) // PAGE + 2,
        "restore": True,
        "digest": [[linked.base + a, linked.base + b] for a, b in linked.compared()],
        "table": {str(i): f"leaf{i}" for i in names},
        "steps": steps,
    }
    done = subprocess.run(
        ["node", str(world.HARNESS)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return [
        Result(r["status"], bytes.fromhex(r["out"]), r["sha256"])
        for r in json.loads(done.stdout)
    ]
