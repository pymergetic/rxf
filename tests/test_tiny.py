"""The compiler that is a program (docs/19, WP12): `native/tiny/*.rxf`, made into a program by
`tools/native/rxfc.py`, and what it makes of itself.

The program is run by the interpreter of each machine and writes the code of that machine (x86_64,
aarch64, the body of a wasm32 function). Four stages, on every machine:

  A  what `native/compile.c` (leaf 31) writes for the program of the compiler
  I  what the interpreter writes when it runs the compiler on its own program
  B  what the code I writes when it is run on the same program
  C  what the code B writes

I, B and C are the same bytes, and they are the bytes of A: the compiler that is a program writes
what the compiler of the image writes, and the code it makes of itself makes the same code again.
"""

import random
import struct
from dataclasses import dataclass
from pathlib import Path

import pytest
import rxfc

from pymergetic.rxf.core import tree

from . import graph_world as world
from . import rxf_world as w
from .graph_programs import OP, assemble, lit, move, slot
from .snippets import STARTER
from .test_rxfc import MACHINES, SAMPLES, program

TINY = Path(__file__).parents[1] / "native" / "tiny" / "tiny.rxf"
IDS = {"x86_64": 1, "aarch64": 2, "wasm32": 3}
UNSUPPORTED = 16
MORE = (
    w.AGAIN
)  # the program that is compiled, in the memory of the machine that runs the compiler
RESULT = MORE + 0x100000  # what the compiler that is code writes
ROOM = MORE + 0x300000  # and its tables


@pytest.fixture(scope="module")
def code() -> world.Code:
    return world.code_of(tree.load(STARTER))


@pytest.fixture(scope="module")
def tiny() -> bytes:
    return rxfc.compile_file(TINY)


@dataclass(frozen=True)
class Made:
    status: int
    size: int
    at: int
    code: bytes


def request(machine: str, session: w.Session, program: bytes, out: int, work: int):
    return w.compile_request(
        len(program),
        out=out,
        capacity=w.CODE_BYTES,
        work=work,
        work_bytes=w.WORK_BYTES,
        machine=IDS[machine],
        table=w.TABLE,
        count=len(session.slots),
        program=MORE,
    )


def by_interpreter(session: w.Session, compiler: bytes, program: bytes) -> Made:
    """The compiler (a program) run by the interpreter of the machine, on `program`."""
    session.install(compiler, w.arguments(w.CONTEXT, w.TINY_REQUEST))
    session.write(MORE, program)
    session.write(w.TABLE, w.table_of(session.slots))
    session.write(
        w.TINY_REQUEST, request(session.machine, session, program, w.CODE, w.WORK)
    )
    result = session.run()
    return made(session, result.status, w.CODE)


def by_code(session: w.Session, compiler_code: bytes, program: bytes) -> Made:
    """The compiler as the machine code `compiler_code`, run on `program`: the code it writes
    is in RESULT. The memory of the session is the one that `by_interpreter` set up."""
    session.write(w.CODE, compiler_code)
    session.write(w.ARGS, w.arguments(w.CONTEXT, w.TINY_REQUEST))
    session.words = 2
    session.write(w.TABLE, w.table_of(session.slots))
    session.write(MORE, program)
    session.write(
        w.TINY_REQUEST, request(session.machine, session, program, RESULT, ROOM)
    )
    result = session.execute(len(compiler_code))
    return made(session, result.status, RESULT)


def made(session: w.Session, status: int, at: int) -> Made:
    size, word = struct.unpack("<2Q", session.read(w.TINY_REQUEST + 48, 16))
    return Made(status, size, word, session.read(at, size) if status == 0 else b"")


@dataclass
class Stages:
    session: w.Session
    A: bytes
    I: bytes
    B: bytes
    C: bytes


@pytest.fixture(scope="module", params=MACHINES)
def stages(request, code, tiny):
    machine = request.param
    session = w.session_of(machine, code)
    try:
        session.install(tiny)
        status, size, _ = session.compile()
        assert status == 0
        a = session.read(w.CODE, size)
        made_i = by_interpreter(session, tiny, tiny)
        assert made_i.status == 0, made_i
        made_b = by_code(session, made_i.code, tiny)
        assert made_b.status == 0, made_b
        made_c = by_code(session, made_b.code, tiny)
        assert made_c.status == 0, made_c
        yield Stages(session, a, made_i.code, made_b.code, made_c.code)
    finally:
        session.close()


def test_the_starter_holds_the_compiler_that_the_source_makes(code, tiny):
    image = tree.load(STARTER)
    path = "pymergetic.rxf.foundation.tiny.compile"
    function = image.resolve(path)
    codes = [
        dict(image.fields(h))
        for h in range(image.handle_count)
        if image.live(h)
        and image.type_name(h) == "Code"
        and image.get(h)["parent"] == function
    ]
    assert [bytes.fromhex(c["data"]) for c in codes] == [tiny]


def test_the_compiler_of_the_image_and_the_one_that_is_a_program_write_the_same_code(
    stages,
):
    assert 0 < len(stages.A) < 400_000
    assert stages.I == stages.A


def test_the_code_of_the_compiler_makes_the_same_code_of_itself_again(stages):
    assert stages.B == stages.I
    assert stages.C == stages.B


def compilers(stages: Stages):
    """The compiler as the interpreter runs it (slowly) and as the code it made of itself."""
    return stages.session, stages.I


@pytest.mark.parametrize("name", sorted(p.stem for p in SAMPLES.glob("*.rxf")))
def test_the_compiler_writes_what_the_compiler_of_the_image_writes(stages, name):
    session, compiler = compilers(stages)
    sample = program(name)
    by_image = stages_compile(stages, sample)
    assert by_image.status == 0
    assert by_code(session, compiler, sample) == by_image


def stages_compile(stages: Stages, sample: bytes) -> Made:
    """What `compile.c` writes for the program, in the same form."""
    session = stages.session
    session.install(sample)
    status, size, at = session.compile()
    return Made(status, size, at, session.read(w.CODE, size) if status == 0 else b"")


def test_what_it_cannot_compile_it_refuses_where_it_stopped(stages):
    """A jump to the middle of an instruction and a slot that is not aligned: the refusal and the word,
    as compile.c says them."""
    session, compiler = compilers(stages)
    middle = assemble(16, [], [move(0, 8, lit(1)), [OP["GOTO"], 6, 0]])
    unaligned = assemble(
        16, [], [[OP["LESS"], 8, *slot(1), *lit(5)], [OP["RETURN"], *slot(8, 1), 1]]
    )
    for sample in (middle, unaligned):
        want = stages_compile(stages, sample)
        assert want.status == UNSUPPORTED
        assert by_code(session, compiler, sample) == want


def mutations(sample: bytes, count: int, seed: int) -> list[bytes]:
    rng = random.Random(seed)
    words = list(struct.unpack(f"<{len(sample) // 8}Q", sample))
    found = []
    for _ in range(count):
        changed = list(words)
        for _ in range(rng.choice([1, 1, 2])):
            at = rng.randrange(len(changed))
            changed[at] = rng.choice(
                [
                    0,
                    1,
                    2,
                    3,
                    5,
                    6,
                    7,
                    8,
                    9,
                    13,
                    16,
                    24,
                    255,
                    1 << 40,
                    (1 << 64) - 1,
                    changed[at] ^ (1 << rng.randrange(12)),
                    changed[at] + rng.choice([1, -1, 8, -8]),
                ]
            ) & ((1 << 64) - 1)
        found.append(struct.pack(f"<{len(changed)}Q", *changed))
    return found


def test_it_says_what_the_compiler_of_the_image_says_on_words_that_were_changed(stages):
    """The same code when it compiles, the same refusal and the same word when it refuses."""
    session, compiler = compilers(stages)
    seen = {"compiled": 0, "refused": 0}
    for name in ("arith", "branches", "procs"):
        for sample in mutations(program(name), 40, seed=len(name)):
            want = stages_compile(stages, sample)
            assert by_code(session, compiler, sample) == want, name
            seen["compiled" if want.status == 0 else "refused"] += 1
    assert seen["compiled"] > 10 and seen["refused"] > 10


def test_what_is_not_a_program_and_what_is_too_small(stages):
    session, compiler = compilers(stages)
    assert by_code(session, compiler, bytes(64)).status == 14
    sample = program("branches")
    session.write(w.CODE, compiler)
    session.write(MORE, sample)
    session.write(
        w.TINY_REQUEST,
        w.compile_request(
            len(sample),
            out=RESULT,
            capacity=16,
            work=ROOM,
            work_bytes=w.WORK_BYTES,
            machine=IDS[session.machine],
            table=w.TABLE,
            count=len(session.slots),
            program=MORE,
        ),
    )
    result = session.execute(len(compiler))
    size, _ = struct.unpack("<2Q", session.read(w.TINY_REQUEST + 48, 16))
    assert result.status == 9 and size == len(stages_compile(stages, sample).code)


def test_the_words_of_the_compiler_are_a_program_of_the_interpreter(tiny):
    magic, frame, parameters, argwords, entry = struct.unpack_from("<5Q", tiny)
    assert magic == 0x31475852 and frame <= 1024 and parameters == 2 and argwords == 2
    assert entry == 5 + 2 * parameters and len(tiny) % 8 == 0
