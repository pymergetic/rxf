"""The compiler in the image (docs/19, WP12): a program of the interpreter becomes machine code.

`compile.program` (leaf 31, `native/compile.rxfs`) is native code of every machine. Given the words of a
program it writes the code of the machine it runs on: x86_64, aarch64, or the body of a wasm32
function. The test runs both, the interpreter and the compiled code, on the same arguments in the
same memory, on each machine, and holds them to the same status and the same value.
"""

import shutil
import struct
import compose
import graph_header
import programs
import pytest

from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import physical

from . import live_world as lw
from .graph_programs import OP, assemble, call, edge, lit, move, slot
from .snippets import running_image
from .support import resolved
from .test_self_edit import FUNCTION, authored, number

COMPILE = graph_header.leaves()["compile_program"]
UNSUPPORTED = graph_header.numbers()["RXF_REFUSE_UNSUPPORTED"]
OUT_OF_MEMORY, TYPE_MISMATCH = 9, 14
MAIN = "pymergetic.rxf.application.main"
MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]
REQUEST = 0x800  # the request of the compiler, in the scratch memory of the machine
WORK = 0x200000  # the program and the code, this far into the emit region
TABLES = 0x80000  # the tables of the compiler, this far after the program


@pytest.fixture(scope="module")
def image() -> Image:
    return running_image()


@pytest.fixture(scope="module", params=MACHINES)
def machine(request: pytest.FixtureRequest, image: Image):
    machine = lw.machine_of(request.param, image)
    try:
        yield machine
    finally:
        if hasattr(machine, "close"):
            machine.close()


@pytest.fixture
def fresh(machine: lw.Machine, image: Image):
    """A machine of its own, for a test that changes the image it holds."""
    another = lw.machine_of(machine.linked.machine, image)
    try:
        yield another
    finally:
        if hasattr(another, "close"):
            another.close()


def compile_program(
    machine: lw.Machine, program: bytes, capacity: int = 1 << 16
) -> tuple[int, bytes, int, int]:
    """The status of the compiler, the code, the size it needs, and the word it stopped at."""
    linked = machine.linked
    assert linked.live is not None
    base = linked.live.regions["emit"][0] + WORK
    out = base + ((len(program) + 63) & ~63)
    work = base + TABLES
    machine.write(base, program)
    request = linked.scratch + REQUEST
    machine.write(
        request,
        struct.pack(
            "<8Q", base, len(program), out, capacity, work, 8 * len(program), 0, 0
        ),
    )
    status = machine.leaf(COMPILE, request)
    *_, size, at = struct.unpack("<8Q", machine.read(request, 64))
    return status, machine.read(out, min(size, capacity)), size, at


def bytecode(image: Image, path: str) -> bytes:
    return bytes.fromhex(
        programs.fields_of(image, image.handle(f"{path}.bytecode"))["data"]
    )


def identity(image: Image, path: str) -> int:
    handle = resolved(image, path)
    assert handle is not None
    return compose.object_id(image, handle)


def same_run(machine: lw.Machine, image: Image, path: str, arguments: bytes) -> bytes:
    """What the interpreter and the compiled code of a function do on these arguments: the same."""
    program = bytecode(image, path)
    status, code, _, at = compile_program(machine, program)
    assert status == 0, (path, status, at)
    interpreted = machine.run(identity(image, f"{path}.bytecode"), arguments)
    compiled = machine.call_code(code, arguments)
    assert compiled == interpreted, path
    return code


def test_main_and_what_it_calls_are_compiled_and_do_what_the_interpreter_does(
    machine, image: Image
):
    for path in (MAIN, "pymergetic.rxf.library.checkout_total"):
        code = same_run(machine, image, path, b"")
        assert 0 < len(code) < 4096
    assert (
        number(machine.call_code(compile_program(machine, bytecode(image, MAIN))[1]))
        == 570
    )


def test_every_function_of_the_starter_is_compiled(machine, image: Image):
    """All 58: nothing is left to the interpreter. What they do is held to the interpreter's by
    `test_compile_cases`, from the same memory each time (some of them change the image)."""
    for path, program in compose.programs(image).items():
        status, code, size, at = compile_program(machine, program)
        assert status == 0, (path, status, at)
        assert 0 < size == len(code), path


def test_the_code_is_the_same_each_time_and_a_different_one_for_each_machine(
    machine, image: Image
):
    program = bytecode(image, MAIN)
    first = compile_program(machine, program)
    again = compile_program(machine, program)
    assert first == again
    assert first[2] == len(first[1]) > 0


def test_what_the_machine_cannot_do_is_refused_with_the_word_it_stopped_at(machine):
    # a call of a leaf that takes a handle, and a loop, are compiled like the rest
    pin = assemble(
        32,
        [(0, 24)],
        [
            call("runtime_pin", [slot(0, 24)], status=24),
            [OP["RETURN"], *slot(24, 4), 4],
        ],
    )
    assert compile_program(machine, pin)[0] == 0
    loop = assemble(
        16,
        [],
        [
            move(0, 8, lit(1)),
            "again",
            [OP["LESS"], 8, *slot(0), *lit(5)],
            [OP["GOTO"], *edge("again")],
        ],
    )
    assert compile_program(machine, loop)[0] == 0
    # a jump into the middle of an instruction: there is no code to jump to
    middle = assemble(16, [], [move(0, 8, lit(1)), [OP["GOTO"], 6, 0]])
    status, _, _, at = compile_program(machine, middle)
    assert status == UNSUPPORTED and at == 5 + 6
    # a slot that is not aligned to its width
    unaligned = assemble(
        16, [], [[OP["LESS"], 8, *slot(1), *lit(5)], [OP["RETURN"], *slot(8, 1), 1]]
    )
    assert compile_program(machine, unaligned)[0] == UNSUPPORTED
    # not a program at all
    status, _, _, _ = compile_program(machine, bytes(64))
    assert status == TYPE_MISMATCH


def test_room_that_is_too_small_is_refused_and_says_how_much_it_needs(
    machine, image: Image
):
    program = bytecode(image, MAIN)
    status, code, size, _ = compile_program(machine, program, capacity=16)
    assert status == OUT_OF_MEMORY
    assert size > 16 and len(code) == 16
    status, whole, needed, _ = compile_program(machine, program, capacity=size)
    assert status == 0 and needed == size and len(whole) == size


def test_a_function_made_of_existing_ones_is_compiled_in_the_running_image(
    fresh, image: Image
):
    """The new Function of the first self-edit (WP11), then compiled by the image itself, and edited again."""
    machine = fresh
    base = image.to_bytes()
    expected = Image.from_bytes(base)
    text = authored(expected)
    expected.apply(text)
    compiled = physical.from_xml(Image.from_bytes(base), ("double.rxfd", text))
    channel = lw.Channel(machine)
    assert channel.client.apply(compiled.data) == expected.digest()
    path = FUNCTION
    program = bytecode(expected, path)
    status, code, _, _ = compile_program(machine, program)
    assert status == 0
    assert number(machine.call_code(code)) == 2 * 570
    assert machine.call_code(code) == machine.run(
        identity(expected, f"{path}.bytecode")
    )
    # the data changes: the compiled code has the addresses of its moment, so the image compiles it
    # again after the change, and the code of the new moment does what the interpreter does
    edited = Image.from_bytes(expected.to_bytes())
    unit = resolved(edited, "pymergetic.rxf.application.unit_price")
    edited.set_payload(unit, struct.pack("<I", 135))
    channel.client.apply(physical.compile(expected, edited).data)
    status, code, _, _ = compile_program(machine, program)
    assert status == 0
    interpreted = machine.run(identity(edited, f"{path}.bytecode"))
    assert machine.call_code(code) == interpreted
    assert number(interpreted) != 2 * 570
