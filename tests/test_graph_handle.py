"""The handle operand of ``native/graph.h`` (docs/23 4.9): resolved at each use, never cached.

A HANDLE operand names a 24-byte handle in the frame (id, generation, offset) and is resolved by
the instruction that uses it: the halving search of the entry table runs for every use, a stale
generation is refused and never rebound, and the address it resolves to is the operand of that one
instruction -- never a value in a slot. Every rule of the design is a case below: a program, its
arguments and the rows it runs against, with the status (and the word) it must answer.

The second interpreter (``tests/graph_reference.py``, written from the header) answers each case
and is held to the table; then ``rxf_graph_run`` as the Seed builds it NOW (``native/graph.rxfs``
-- not the bytes a starter carries) runs the same cases as x86_64 and aarch64 in an emulator and as
wasm32 in node, and as x86_64 on the host seat (``tests/host/runner.c``): status, out and the whole
memory equal. The mid-flight slide of S8 (a pump that moves a cell between two instructions)
runs on the oracle and the host seat.
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass

import graph_header
import pytest

from . import graph_world as world
from . import rxf_native, rxf_sim
from .emulator import Emulator
from .graph_programs import (
    CALLEE,
    OP,
    OUT_OF_RANGE,
    STALE_GENERATION,
    UNBOUND,
    Item,
    Word,
    assemble,
    call,
    call_card,
    callee_identity,
    callee_step,
    edge,
    handle,
    lit,
    move,
    slot,
)

N = graph_header.numbers()
HANDLE = N["RXF_OPERAND_HANDLE"]
PENDING = N["RXF_OBJECT_PENDING"]
UNSUPPORTED = N["RXF_REFUSE_UNSUPPORTED"]

# The leaves these programs call; the seed builds their code for every machine.
LEAVES = ["memory_copy", "runtime_pin", "runtime_borrow", "wrapping_add_uint64_t"]

A, B, PEND, FREE, REUSED = 1001, 1002, 1003, 1004, 1005
WORDS_A = (10, 20, 30)
WORDS_B = (0x1122334455667788, 8, 9)
ROWS = (
    world.Row(A, struct.pack("<3Q", *WORDS_A)),
    world.Row(B, struct.pack("<3Q", *WORDS_B)),
    world.Row(PEND, struct.pack("<3Q", 1, 2, 3), state=PENDING),
    world.Row(FREE, bytes(24), state=0),
    world.Row(REUSED, struct.pack("<3Q", 5, 6, 7), generation=3),
)
PAYLOADS = (
    world.PAYLOADS - world.BASE
)  # where the first row's payload lies in the memory

FRAME = 128
H0, H1, OUT = 0, 24, 64  # two handles of the arguments, then the answer


def hnd(offset: int = H0, width: int = 64) -> list[Word]:
    """A HANDLE operand: the handle lies in the frame at ``offset``."""
    return [HANDLE, offset, width]


def program(items: list[Item], parameters: int = 1) -> bytes:
    return assemble(FRAME, [(H0, 24), (H1, 24)][:parameters], items)


def returns(width: int = 8) -> Item:
    return [OP["RETURN"], *slot(OUT, width), width]


def field(operand: list[Word], offset: int = 8, size: int = 8) -> Item:
    return [OP["FIELD"], OUT, 8, *operand, offset, size]


@dataclass(frozen=True)
class Probe:
    name: str
    program: bytes
    args: bytes
    status: int
    word: int | None = None  # the first word of the out buffer, where the run answers
    objects: tuple[
        int, ...
    ] = ()  # the ids of ROWS the host seat can bind (generation 1, LIVE)
    callee: bytes | None = None  # the program a call card names, at the id CALLEE
    holds: bool = True  # the program holds a HANDLE operand (the compilers leave it to the interpreter)

    def case(self) -> world.Case:
        """The world's case: the rows the host binds, and the ones only the world can express
        (a pending row, a free row, a row of another generation)."""
        rows = tuple(
            r for r in ROWS if r.identity in (*self.objects, PEND, FREE, REUSED)
        )
        objects = {} if self.callee is None else {CALLEE: self.callee}
        return world.Case(self.name, self.program, self.args, objects, rows=rows)


def probes() -> list[Probe]:
    both = (A, B)
    one = handle(A) + handle(B)
    found = [
        Probe("field", program([field(hnd()), returns()]), handle(A), 0, 20, both),
        Probe(
            "field at the handle's offset",
            program([field(hnd()), returns()]),
            handle(A, 1, 8),
            0,
            30,
            both,
        ),
        Probe(
            "field of another object",
            program([field(hnd()), returns()]),
            handle(B),
            0,
            8,
            both,
        ),
        # the refusals of the resolve, in the order the design lists them
        Probe(
            "a stale generation is refused",
            program([field(hnd()), returns()]),
            handle(A, 2, 0),
            STALE_GENERATION,
            None,
            both,
        ),
        Probe(
            "an id nothing has",
            program([field(hnd()), returns()]),
            handle(9999),
            UNBOUND,
            None,
            both,
        ),
        Probe(
            "a row of an older generation than the handle's",
            program([field(hnd()), returns()]),
            handle(REUSED, 4, 0),
            STALE_GENERATION,
        ),
        Probe(
            "a pending row is not readable here",
            program([field(hnd()), returns()]),
            handle(PEND),
            UNBOUND,
        ),
        Probe("a free row", program([field(hnd()), returns()]), handle(FREE), UNBOUND),
        Probe(
            "a handle of a reused row is never rebound",
            program([field(hnd()), returns()]),
            handle(REUSED, 1, 0),
            STALE_GENERATION,
        ),
        Probe(
            "the reused row answers its own generation",
            program([field(hnd()), returns()]),
            handle(REUSED, 3, 0),
            0,
            6,
        ),
        Probe(
            "an offset past the size",
            program([field(hnd()), returns()]),
            handle(A, 1, 25),
            OUT_OF_RANGE,
            None,
            both,
        ),
        Probe(
            "a width past the size",
            program([field(hnd(), 8, 8), returns()]),
            handle(A, 1, 16),
            OUT_OF_RANGE,
            None,
            both,
        ),
        Probe(
            "the end of the object holds no byte",
            program([field(hnd(), 0, 8), returns()]),
            handle(A, 1, 24),
            OUT_OF_RANGE,
            None,
            both,
        ),
        Probe(
            "a handle outside the frame",
            program([field(hnd(1000)), returns()]),
            handle(A),
            OUT_OF_RANGE,
            None,
            both,
        ),
        Probe(
            "a handle that crosses the end of the frame",
            program([field(hnd(FRAME - 8)), returns()]),
            handle(A),
            OUT_OF_RANGE,
            None,
            both,
        ),
        # the readers that share the one resolve
        Probe(
            "MOVE reads through it",
            program([move(OUT, 8, hnd(H0, 8)), returns()]),
            handle(A),
            0,
            10,
            both,
        ),
        Probe(
            "MOVE copies no more than the operand denotes",
            program([move(OUT, 8, hnd(H0, 4)), returns()]),
            handle(B),
            0,
            0x55667788,
            both,
        ),
        Probe(
            "a CONSTRUCT payload is read through it",
            program(
                [
                    [OP["CONSTRUCT"], OUT, 16, 0, 1, *hnd(H0, 8), 8, 8],
                    [OP["RETURN"], *slot(OUT, 16), 16],
                ]
            ),
            handle(A),
            0,
            1,
            both,
        ),
        Probe(
            "an edge copies through it",
            program(
                [
                    [OP["GOTO"], *edge("done", (hnd(H0, 8), OUT, 8))],
                    "done",
                    returns(),
                ]
            ),
            handle(A),
            0,
            10,
            both,
        ),
        Probe(
            "RETURN of a wide operand reads through it",
            program(
                [
                    [OP["RETURN"], *hnd(H0, 24), 24],
                ]
            ),
            handle(A),
            0,
            10,
            both,
        ),
        Probe(
            "a LESS operand is the address it resolves to",
            program(
                [
                    [OP["LESS"], OUT, *hnd(H0, 8), *lit(1)],
                    [OP["RETURN"], *slot(OUT, 1), 8],
                ]
            ),
            handle(A),
            0,
            0,
            both,
        ),
        # a pointer parameter of a leaf is the address, for that one call
        Probe(
            "a pointer parameter is the address it resolves to",
            program(
                [
                    call("memory_copy", [hnd(H0), lit(24), hnd(H1), lit(24), lit(16)]),
                    [OP["RETURN"], *lit(0), 8],
                ],
                2,
            ),
            one,
            0,
            0,
            both,
        ),
        Probe(
            "a pointer parameter of a pending row",
            program(
                [
                    call("memory_copy", [hnd(H0), lit(24), hnd(H1), lit(24), lit(16)]),
                    [OP["RETURN"], *lit(0), 8],
                ],
                2,
            ),
            handle(PEND) + handle(A),
            UNBOUND,
            None,
            (A,),
        ),
        # the handle is read again at every use
        Probe(
            "the handle in the frame is read at each use",
            program(
                [
                    [OP["FIELD"], OUT, 8, *hnd(H0), 8, 8],
                    [OP["MOVE"], H0, 24, *slot(H1, 24)],
                    [OP["FIELD"], OUT + 8, 8, *hnd(H0), 8, 8],
                    [OP["RETURN"], *slot(OUT, 16), 16],
                ],
                2,
            ),
            one,
            0,
            20,
            both,
        ),
        # a handle where a handle is taken by value is a dereference where a value is meant
        Probe(
            "a HANDLE operand in the handle position of a leaf",
            program(
                [
                    call("runtime_pin", [hnd(H0)], None, OUT),
                    [OP["RETURN"], *slot(OUT, 4), 8],
                ]
            ),
            handle(A),
            OUT_OF_RANGE,
            None,
            both,
        ),
        Probe(
            "the same leaf takes the slot of that handle",
            program(
                [
                    call("runtime_pin", [slot(H0, 24)], None, OUT),
                    [OP["RETURN"], *slot(OUT, 4), 8],
                ]
            ),
            handle(A),
            0,
            0,
            both,
            holds=False,
        ),
        Probe(
            "a HANDLE operand in the second position of a leaf that takes a handle",
            program(
                [
                    call("runtime_borrow", [hnd(H0), lit(0)], None, OUT),
                    [OP["RETURN"], *slot(OUT, 4), 8],
                ]
            ),
            handle(A),
            OUT_OF_RANGE,
            None,
            both,
        ),
    ]
    # a call card: a scalar parameter reads through the handle, a 24-byte one is a handle taken
    # by value and refuses
    found += [
        Probe(
            "a call card's scalar argument is read through it",
            program(
                [
                    call_card(CALLEE, [hnd(H0, 8)], out=(OUT, 8)),
                    returns(),
                ]
            ),
            handle(A),
            0,
            11,
            both,
            callee_step(),
        ),
        Probe(
            "a call card's handle argument cannot be a HANDLE operand",
            program(
                [
                    call_card(CALLEE, [hnd(H0, 24)], out=(OUT, 8)),
                    returns(),
                ]
            ),
            handle(A),
            OUT_OF_RANGE,
            None,
            both,
            callee_identity(),
        ),
    ]
    return found


PROBES = probes()
IDS = [p.name for p in PROBES]


@pytest.fixture(scope="module")
def code() -> world.Code:
    return world.seed_code(LEAVES)


@pytest.fixture(scope="module")
def cases() -> list[world.Case]:
    return [p.case() for p in PROBES]


@pytest.fixture(scope="module")
def reference(code, cases):
    return [world.referenced(code, case)[0] for case in cases]


def different(cases, want, have):
    return [
        (case.name, w.status, h.status, w.out == h.out, w.memory == h.memory)
        for case, w, h in zip(cases, want, have, strict=True)
        if w != h
    ]


def first_word(out: bytes) -> int:
    return int.from_bytes(out[:8], "little")


# ---- the table: what the second interpreter answers -----------------------------------------------


def test_the_oracle_answers_every_row_of_the_table(reference):
    wrong = []
    for probe, outcome in zip(PROBES, reference, strict=True):
        if outcome.status != probe.status:
            wrong.append((probe.name, "status", outcome.status, probe.status))
        elif probe.word is not None and first_word(outcome.out) != probe.word:
            wrong.append((probe.name, "word", first_word(outcome.out), probe.word))
    assert wrong == []


def test_the_table_has_every_refusal_of_the_design(reference):
    statuses = {r.status for r in reference}
    assert {0, UNBOUND, STALE_GENERATION, OUT_OF_RANGE} <= statuses


def test_a_pointer_parameter_wrote_the_bytes_of_the_other_object(reference):
    (at,) = [
        i for i, p in enumerate(PROBES) if p.name.startswith("a pointer parameter is")
    ]
    memory = reference[at].memory
    # the rows lie in the order of ROWS: A first (3 words), then B; A now holds B's first 16 bytes
    assert struct.unpack_from("<3Q", memory, PAYLOADS) == (*WORDS_B[:2], WORDS_A[2])
    assert struct.unpack_from("<3Q", memory, PAYLOADS + 24) == WORDS_B


# ---- the machines ---------------------------------------------------------------------------------


def test_x86_64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("x86_64", code, case) for case in cases]
    assert different(cases, reference, have) == []


def test_aarch64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("aarch64", code, case) for case in cases]
    assert different(cases, reference, have) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wasm32_runs_what_the_reference_runs(code, cases, reference):
    assert different(cases, reference, world.in_node(code, cases)) == []


def host_run(probe: Probe) -> tuple[int, int, dict[int, bytes]]:
    rows = {r.identity: r for r in ROWS if r.identity in probe.objects}
    cells = {i: bytearray(r.payload) for i, r in rows.items()}
    objects: dict[int, bytes | bytearray] = dict(cells)
    if probe.callee is not None:
        objects[CALLEE] = probe.callee
    with rxf_native.Memory(1 << 16) as memory:
        status, value = rxf_native.run(
            probe.program, probe.args, memory, objects=objects
        )
    return status, value, {i: bytes(c) for i, c in cells.items()}


def test_the_host_seat_answers_the_rows_it_can_express(reference):
    ran = 0
    for probe in PROBES:
        if not probe.objects:
            continue
        ran += 1
        status, value, _ = host_run(probe)
        assert status == probe.status, probe.name
        if probe.word is not None:
            assert value == probe.word, probe.name
    assert ran >= 20


def test_the_host_seat_writes_through_a_pointer_parameter():
    (probe,) = [p for p in PROBES if p.name.startswith("a pointer parameter is")]
    status, _, cells = host_run(probe)
    assert status == 0
    assert struct.unpack("<3Q", cells[A]) == (*WORDS_B[:2], WORDS_A[2])


# ---- S8, mid-flight: the cell slides between two instructions ---------------------------------------

SLIDES = 5
COUNTER = 8  # the offset of the word the loop counts in


def sliding() -> bytes:
    """Holds a handle across a loop: read a word through it, add 3, write it back through a second
    handle at the word's offset, five times, and answer the word. Nothing it keeps is an address."""
    i, flag, current = 64, 72, 80
    items: list[Item] = [
        move(i, 8, lit(0)),
        "loop",
        [OP["LESS"], flag, *slot(i), *lit(SLIDES)],
        [
            OP["BRANCH"],
            *slot(flag, 1),
            N["RXF_MODE_NONZERO"],
            *edge("body"),
            *edge("done"),
        ],
        "body",
        [OP["FIELD"], current, 8, *hnd(H0), COUNTER, 8],
        call("wrapping_add_uint64_t", [slot(current), lit(3)], (current, 8)),
        call("memory_copy", [hnd(H1), lit(8), slot(current, 16), lit(16), lit(8)]),
        [OP["INCREMENT"], i, 8, *slot(i)],
        [OP["GOTO"], *edge("loop")],
        "done",
        [OP["FIELD"], OUT, 8, *hnd(H0), COUNTER, 8],
        [OP["RETURN"], *slot(OUT, 8), 8],
    ]
    return assemble(FRAME + 32, [(H0, 24), (H1, 24)], items)


SLIDING_ARGS = handle(A) + handle(A, 1, COUNTER)
SLIDING_WORD = WORDS_A[1] + 3 * SLIDES


class Slider:
    """The pump of the oracle: before every instruction the cell of A moves between its home and
    its alternate place and its row follows (the sliding pass of S8, one cell at a time)."""

    def __init__(self, emulator: Emulator) -> None:
        self.emulator = emulator
        self.home, self.alternate = world.PAYLOADS, world.HEAP + 0x1000
        self.count = 0

    def row_of(self, identity: int) -> int:
        count = struct.unpack("<Q", self.emulator.read(world.CONTEXT + 32, 8))[0]
        for index in range(count):
            at = world.OBJECTS + index * 80
            if struct.unpack("<Q", self.emulator.read(at, 8))[0] == identity:
                return at
        raise AssertionError(identity)

    def __call__(self) -> None:
        at = self.row_of(A)
        payload, size = struct.unpack("<2Q", self.emulator.read(at + 24, 16))
        to = self.alternate if payload == self.home else self.home
        self.emulator.write(to, self.emulator.read(payload, size))
        self.emulator.write(at + 24, struct.pack("<Q", to))
        self.count += 1


def test_the_oracle_holds_a_handle_across_a_sliding_cell(code):
    case = world.Case("slide", sliding(), SLIDING_ARGS, {}, rows=ROWS[:1])
    sliders: list[Slider] = []

    def make(emulator: Emulator) -> Slider:
        sliders.append(Slider(emulator))
        return sliders[-1]

    outcome, _ = world.referenced(code, case, make)
    assert outcome.status == 0
    assert first_word(outcome.out) == SLIDING_WORD
    assert sliders[0].count > 5 * SLIDES  # a move before every instruction


def test_the_oracle_without_the_slide_gives_the_same_answer(code):
    case = world.Case("still", sliding(), SLIDING_ARGS, {}, rows=ROWS[:1])
    outcome, _ = world.referenced(code, case)
    assert (outcome.status, first_word(outcome.out)) == (0, SLIDING_WORD)


def test_the_host_seat_holds_a_handle_across_a_sliding_cell():
    cell = bytearray(ROWS[0].payload)
    with rxf_native.Memory(1 << 16) as memory:
        status, value = rxf_native.run(
            sliding(), SLIDING_ARGS, memory, objects={A: cell}, slide=A
        )
    assert (status, value) == (0, SLIDING_WORD)
    assert rxf_native.slides() > 5 * SLIDES


# ---- the simulator ----------------------------------------------------------


def test_the_simulator_refuses_a_handle_operand_by_name():
    """tests/rxf_sim.py has no context and no entry table: a HANDLE operand is left to the
    interpreter, answered RXF_REFUSE_UNSUPPORTED like the compilers do, never read as a slot."""
    words = program([move(OUT, 8, hnd(H0, 8)), returns()])
    status, _ = rxf_sim.run(words, handle(A), rxf_sim.Memory())
    assert status == UNSUPPORTED
