"""The language `.rxfs` (tools/native/rxfc.py): programs that a person writes, run as the interpreter runs them.

Each sample in ``tests/rxfc`` is made into a program, run by the Python simulator of
``rxf_sim``, by the interpreter of ``native/graph.c`` on x86_64 and aarch64 (in an emulator) and
in node as wasm32, and held to what a plain Python function of the same sample computes.
"""

import shutil
import struct
from pathlib import Path

import pytest
import rxfc

from pymergetic.rxf.core import tree

from . import graph_world as world
from . import rxf_sim, rxf_world
from .snippets import STARTER

SAMPLES = Path(__file__).parent / "rxfc"
MASK = (1 << 64) - 1
HEAP = rxf_world.DATA  # memory a sample may write
MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]


def arith(a: int, b: int) -> int:
    r = (((a + b - 1) << 3) | 5) & MASK
    s = (
        (a == b)
        + (a < b) * 2
        + (a > b) * 4
        + (a <= b) * 8
        + (a >= b) * 16
        + (a != b) * 32
    )
    r += s << 40
    r += (-a & MASK) + (a == 0) + 3007 + ((2**32 - 1) >> 28)
    return r & MASK


def table(base: int, n: int) -> int:
    memory = bytearray(0x1000)

    def put(at: int, data: bytes) -> None:
        memory[at - base : at - base + len(data)] = data

    for i in range(n):
        put(base + 4 * i, struct.pack("<I", i * 4 + 1))
    p = base + 4 * n
    put(p, b"\xff")
    put(p + 2, struct.pack("<H", 513))
    put(p + 8, struct.pack("<Q", 0x1122334455667788))
    put(p + 32, bytes(memory[p - base : p - base + 16]))
    put(p + 48, b"\x07" * 4)

    def read(at: int, size: int) -> int:
        return int.from_bytes(memory[at - base : at - base + size], "little")

    acc = sum(read(base + 4 * i, 4) for i in range(n) if i % 2 == 0)
    skipped = n // 2
    acc += read(p, 1) + read(p + 2, 2) + read(p + 40, 8) + read(p + 48, 4)
    acc += skipped << 56
    put(p + 64, struct.pack("<Q", acc & MASK))
    put(p + 72, struct.pack("<I", 1))  # `put` moved the pointer on by 8
    return (read(p + 64, 8) + read(p + 32, 8)) & MASK


def branches(kind: int, value: int) -> tuple[int, int]:
    if kind == 9:
        return 77, 0
    r = {0: 1, 1: 10 + value, 2: 10 + value, 3: 10 + value}.get(kind, 5)
    r &= MASK
    if r > 100:
        r = 100
    elif r == 5:
        r = 6
    else:
        r += 1
    return 0, r


def procs(n: int, cut: int) -> int:
    x, y = n, 0
    for _ in range(2):
        x, y = x * 2, y + 1
        if x <= 1000:
            y += 100
    depth = 5
    x, y = x * 2, y + 1
    if x <= 1000:
        y += 100
    return x + y if x > cut else depth * 8 + x + y


RUNS = [
    ("arith", (5, 9), (0, arith(5, 9))),
    ("arith", (9, 5), (0, arith(9, 5))),
    ("arith", (7, 7), (0, arith(7, 7))),
    ("arith", (0, 0), (0, arith(0, 0))),
    ("table", (HEAP, 10), (0, table(HEAP, 10))),
    ("table", (HEAP, 1), (0, table(HEAP, 1))),
    ("procs", (3, 10, HEAP + 0x1000, HEAP + 0x2000), (0, procs(3, 10))),
    ("procs", (3, 1000, HEAP + 0x1000, HEAP + 0x2000), (0, procs(3, 1000))),
    ("procs", (400, 10, HEAP + 0x1000, HEAP + 0x2000), (0, procs(400, 10))),
    ("branches", (0, 0), branches(0, 0)),
    ("branches", (2, 5), branches(2, 5)),
    ("branches", (3, 500), branches(3, 500)),
    ("branches", (9, 0), branches(9, 0)),
    ("branches", (4, 0), branches(4, 0)),
]


def program(name: str) -> bytes:
    return rxfc.compile_file(SAMPLES / f"{name}.rxfs")


@pytest.fixture(scope="module")
def code():
    return world.code_of(tree.load(STARTER))


@pytest.mark.parametrize(("name", "args", "expected"), RUNS)
def test_the_simulator_computes_what_python_computes(name, args, expected):
    got = rxf_sim.run(
        program(name), struct.pack(f"<{len(args)}Q", *args), rxf_sim.Memory()
    )
    assert got == expected


@pytest.mark.parametrize("machine", MACHINES)
def test_the_interpreter_of_every_machine_computes_it_too(machine, code):
    for name, args, (status, value) in RUNS:
        result, _ = rxf_world.run_once(
            machine, code, program(name), rxf_world.arguments(*args)
        )
        assert result.status == status, (name, args)
        if status == 0:
            assert result.value == value, (name, args)


@pytest.mark.parametrize("machine", MACHINES)
def test_the_compiler_of_the_image_makes_code_of_it_that_computes_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for name, args, (status, value) in RUNS:
            session.install(program(name), rxf_world.arguments(*args))
            session.write(
                HEAP, bytes(0x3000)
            )  # what a sample may write starts as zeroes
            compiled, size, at = session.compile()
            assert compiled == 0, (name, at)
            result = session.execute(size)
            assert size > 0 and result.status == status, (name, args)
            if status == 0:
                assert result.value == value, (name, args)
    finally:
        session.close()


def test_what_is_wrong_is_told_with_its_line():
    for text, line in [
        ("program p() {\n  x = 1;\n}", "2"),
        ("program p() {\n  var a;\n  a = 1\n}", "4"),
        ("const A = 1;\nprogram p(a) {\n  return a * 3;\n}", "3"),
        ("program p() { var a; fill(0, a, 2); }", "1"),
    ]:
        with pytest.raises(rxfc.Error, match=line):
            rxfc.compile_source(text, "t")


def test_a_frame_that_is_too_large_is_refused():
    names = ", ".join(f"v{i}" for i in range(200))
    with pytest.raises(rxfc.Error, match="frame"):
        rxfc.compile_source(f"program p() {{ var {names}; }}", "t")
