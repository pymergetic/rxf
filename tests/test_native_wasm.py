"""The wasm32 and x86_64 snippets of the starter compute the same.

Both are read from the starter, run (wasm in node, x86_64 as machine code in
this process) on the same inputs, and compared on status and output bytes. This
is the proof that a snippet is a unit that only needs calling.
"""

import ctypes
import json
import platform
import random
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from .snippets import C_TYPES, INTEGERS, SIZES, Machine, snippets, values
from .wasm import module_of

HARNESS = Path(__file__).with_name("wasm_harness.js")

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or platform.machine() != "x86_64",
    reason="needs node and an x86_64 host",
)


def bits(type_name: str, value: float) -> tuple[str, str]:
    """(wasm value type, decimal bit pattern or value) of one argument."""
    if type_name in INTEGERS:
        width, _ = INTEGERS[type_name]
        return ("i64" if width == 64 else "i32"), str(value)
    if type_name == "float":
        return "f32", str(int.from_bytes(struct.pack("<f", value), "little"))
    return "f64", str(int.from_bytes(struct.pack("<d", value), "little"))


def test_the_starter_has_numeric_snippets_to_compare():
    assert len(snippets()) >= 286


def test_a_wasm_snippet_computes_what_the_x86_64_snippet_computes():
    rng = random.Random(7)
    machine = Machine()
    chosen = snippets()
    calls, expected, owners = [], [], []
    for snippet in chosen:
        native = machine.function(
            bytes.fromhex(snippet["x86"]),
            ctypes.c_uint32,
            [C_TYPES[k] for k in snippet["kinds"]] + [ctypes.c_void_p],
        )
        sets = [values(k, rng) for k in snippet["kinds"]]
        size = SIZES[snippet["out"]]
        for _ in range(48):
            chosen_args = [rng.choice(options) for options in sets]
            buffer = (ctypes.c_uint8 * 64)(*([0xAA] * 64))
            status = native(
                *[C_TYPES[k](a).value for k, a in zip(snippet["kinds"], chosen_args)],
                ctypes.cast(buffer, ctypes.c_void_p),
            )
            expected.append({"status": status, "out": bytes(buffer[:size]).hex()})
            calls.append(
                {
                    "fn": snippet["name"],
                    "args": [bits(k, a) for k, a in zip(snippet["kinds"], chosen_args)],
                    "out": size,
                }
            )
            owners.append(snippet["name"])
    module = module_of(
        [(s["name"], s["params"], bytes.fromhex(s["wasm"])) for s in chosen]
    )
    process = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps({"module": module.hex(), "calls": calls}),
        text=True,
        capture_output=True,
        check=True,
    )
    got = json.loads(process.stdout)
    assert len(got) == len(expected)
    different = [
        (owner, call["args"], want, have)
        for owner, call, want, have in zip(owners, calls, expected, got)
        if want != have
    ]
    assert different[:5] == []


def test_the_bios_bytes_are_the_x86_64_bytes():
    assert all(s["bios"] == s["x86"] for s in snippets())
