"""The compiler that is a program on every program there is (docs/19, WP12).

`native/tiny` writes, for every program of the starter (the 58 composed ones, three sets of
arguments each, which do not change the code) and for every program of `graph_programs` (all the
opcodes, every shape of leaf, loops, switches, edges with copies, programs that are refused), what
`native/compile.c` writes: the same status, the same word it stopped at, the same size, the same
bytes, on x86_64, aarch64 and wasm32. The compiler that is a program here is the code that it made
of itself (stage B of `test_tiny.py`), so no interpreter is slow.
"""

import json
import shutil
import struct
import subprocess

import graph_header
import pytest
import rxfc

from pymergetic.rxftk.core import tree

from . import graph_programs as programs
from . import graph_world as world
from . import rxf_world as w
from .emulator import Emulator, Integer
from .snippets import STARTER
from .test_rxfc import MACHINES
from .test_tiny import IDS, TINY

pytestmark = [pytest.mark.nightly, pytest.mark.slow]

COMPILE = graph_header.leaves()["compile_program"]
CODE_LIMIT = 0x4000  # what is read of the code of a program that is compiled


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def code(image):
    return world.code_of(image)


@pytest.fixture(scope="module")
def tiny() -> bytes:
    return rxfc.compile_file(TINY)


@pytest.fixture(scope="module")
def cases(image):
    seen: dict[bytes, world.Case] = {}
    synthetic = programs.synthetic()
    for case in world.cases(image) + synthetic + world.channeled(synthetic):
        seen.setdefault(
            case.program + bytes(sorted(case.absent)) + bytes([case.channel]), case
        )
    return list(seen.values())


def tiny_request(machine: str) -> bytes:
    """The request of the compiler that is a program: that of compile.c, and three words more."""
    return struct.pack(
        "<11Q",
        world.PROGRAM,
        0,
        world.CODE_AT,
        world.CODE_BYTES,
        world.TABLES_AT,
        world.TABLE_BYTES,
        0,
        0,
        IDS[machine],
        0,  # the table of the context
        0,
    )


def request_for(case: world.Case, machine: str) -> bytes:
    words = list(struct.unpack("<11Q", tiny_request(machine)))
    words[1] = len(case.program)
    return struct.pack("<11Q", *words)


def made(status: int, request: bytes, code_bytes: bytes) -> tuple[int, int, int, bytes]:
    size, at = struct.unpack_from("<2Q", request, 48)
    return status, size, at, code_bytes[:size] if status == 0 else b""


SPARE = (
    0x400000  # behind the memory of a case: the program of the compiler and its code
)


def both_emulated(machine: str, code, tiny: bytes, cases):
    """compile.c and the compiler that is code, on every case, in one emulator. Code holds the
    addresses of the leaves of its machine, so the compiler is made (by compile.c) in the same one."""
    emulator = Emulator(machine)
    emulator.map(world.BASE, world.MAPPED + world.REACH + SPARE)
    addresses = {
        identity: emulator.load(data) for identity, data in code.leaves[machine].items()
    }
    spare = world.BASE + world.MAPPED + world.REACH
    emulator.write(
        world.BASE,
        world.memory(world.Case("tiny", b"", b"", {}), sorted(addresses.items())),
    )
    emulator.write(spare, tiny)
    out, work = spare + 0x100000, spare + 0x200000
    emulator.write(
        world.REQUEST_AT,
        struct.pack("<8Q", spare, len(tiny), out, 0xF0000, work, 0x100000, 0, 0),
    )
    status = emulator.call(
        addresses[COMPILE], [Integer(world.CONTEXT), Integer(world.REQUEST_AT)]
    )
    assert status == 0
    size = struct.unpack_from("<Q", emulator.read(world.REQUEST_AT, 64), 48)[0]
    tiny_at = emulator.load(emulator.read(out, size))
    found = []
    for case in cases:
        present = sorted((i, a) for i, a in addresses.items() if i not in case.absent)
        emulator.write(world.BASE, world.memory(case, present))
        emulator.write(world.REQUEST_AT, world.request_of(case))
        reference = emulator.call(
            addresses[COMPILE], [Integer(world.CONTEXT), Integer(world.REQUEST_AT)]
        )
        want = made(
            reference,
            emulator.read(world.REQUEST_AT, 64),
            emulator.read(world.CODE_AT, CODE_LIMIT),
        )
        emulator.write(world.BASE, world.memory(case, present))
        emulator.write(world.REQUEST_AT, request_for(case, machine))
        emulator.write(world.ARGS, struct.pack("<2Q", world.CONTEXT, world.REQUEST_AT))
        status = emulator.call(
            tiny_at,
            [
                Integer(world.CONTEXT),
                Integer(world.ARGS),
                Integer(2),
                Integer(world.OUT),
            ],
        )
        have = made(
            status,
            emulator.read(world.REQUEST_AT, 64),
            emulator.read(world.CODE_AT, CODE_LIMIT),
        )
        found.append((case.name, want, have))
    return found


def tiny_body_in_node(code, tiny: bytes) -> bytes:
    """The compiler as the body of a wasm32 function, made by compile.c on that machine."""
    session = w.session_of("wasm32", code)
    try:
        session.install(tiny)
        status, size, _ = session.compile()
        assert status == 0
        return session.read(w.CODE, size)
    finally:
        session.close()


def both_in_node(code, tiny_body: bytes, cases):
    names = sorted(code.leaves["wasm32"])
    module = world.node_module(code, [tiny_body])  # c0 is the compiler that is code
    steps = []
    for case in cases:
        slots = [(i, i) for i in names if i not in case.absent]
        memory = world.memory(case, slots).hex()
        steps.append(
            {
                "fn": f"leaf{COMPILE}",
                "args": [["i32", str(world.CONTEXT)], ["i32", str(world.REQUEST_AT)]],
                "writes": [
                    [world.BASE, memory],
                    [world.REQUEST_AT, world.request_of(case).hex()],
                ],
                "reads": [[world.REQUEST_AT, 64], [world.CODE_AT, CODE_LIMIT]],
            }
        )
        steps.append(
            {
                "fn": "c0",
                "args": [
                    ["i32", str(world.CONTEXT)],
                    ["i32", str(world.ARGS)],
                    ["i64", "2"],
                    ["i32", str(world.OUT)],
                ],
                "writes": [
                    [world.BASE, memory],
                    [world.REQUEST_AT, request_for(case, "wasm32").hex()],
                    [
                        world.ARGS,
                        struct.pack("<2Q", world.CONTEXT, world.REQUEST_AT).hex(),
                    ],
                ],
                "reads": [[world.REQUEST_AT, 64], [world.CODE_AT, CODE_LIMIT]],
            }
        )
    pages = (world.BASE + world.MAPPED + world.REACH + 0xFFFF) // 0x10000
    request = {
        "module": module.hex(),
        "base": world.BASE,
        "image": "",
        "snapshot": 0,
        "pages": pages,
        "table": {str(i): f"leaf{i}" for i in names},
        "steps": steps,
    }
    process = subprocess.run(
        ["node", str(world.HARNESS)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    results = json.loads(process.stdout)
    found = []
    for case, want, have in zip(cases, results[0::2], results[1::2], strict=True):
        found.append(
            (
                case.name,
                made(want["status"], *map(bytes.fromhex, want["outs"])),
                made(have["status"], *map(bytes.fromhex, have["outs"])),
            )
        )
    return found


@pytest.mark.parametrize("machine", MACHINES)
def test_the_compiler_that_is_code_writes_what_compile_c_writes_for_every_program(
    machine,
    code,
    tiny,
    cases,
):
    if machine == "wasm32":
        if shutil.which("node") is None:
            pytest.skip("needs node")
        found = both_in_node(code, tiny_body_in_node(code, tiny), cases)
    else:
        found = both_emulated(machine, code, tiny, cases)
    wrong = [name for name, want, have in found if want != have]
    assert not wrong, wrong[:5]
    refused = sum(1 for _, want, _ in found if want[0] != 0)
    assert len(found) - refused > 60  # most are compiled, the others are refused alike
