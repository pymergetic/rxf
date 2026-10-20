"""The seed `graph_run` (native/graph.rxfs) is the interpreter, and the C's twin.

`native/graph.rxfs` is the line-by-line Seed port of `native/graph.c` (docs/29),
built per machine by `tools/native/graph_build.py`; `seed_build.is_seed` makes it
the bytes the starter carries. The parity it must keep is the C's own: every
program of the starter (three sets of arguments each) and every program of
`graph_programs` -- all the opcodes, every shape of leaf, loops, switches, edges
with copies, call cards, safe points, and the programs that must be refused --
run on x86_64 and aarch64 in an emulator and on wasm32 in node, answers exactly
what the Python reference (tests/graph_reference.py) answers: the status, the
out bytes and the whole compared memory, byte for byte.

The call letters of the leaves are held twice: `seed_build.LEAF_LETTERS` (what
the codegen call types name) and `graph_world.letters()` (what the tests write).
They are the same table, and the first test keeps them that way.

Below the corpus cases, this file carries its own edge builders (cell budget,
depth, answer widths, refusals, edges, operand modes, MAP/FIELD/CONSTRUCT,
entry binding): every one is differential too. What the Python reference has no
model for -- a safe point's rebind (S7), the run's handle, the live words of a
program, and a move onto its own slot -- is pinned to the C's answers on the
seats the way `tests/test_graph.py` pins `programs.lookups()`.
"""

import shutil
import struct

import graph_build
import graph_header
import pytest
import seed_build

from pymergetic.rxftk.core import tree

from . import graph_programs as programs
from . import graph_world as world
from . import rxf_native
from .snippets import STARTER


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def cases(image):
    synthetic = programs.synthetic()
    return (
        world.cases(image)
        + synthetic
        + world.channeled(synthetic)
        + edge_cases()
    )


@pytest.fixture(scope="module")
def code(image):
    """The interpreter of the seed, on every machine, with the leaves of the starter."""
    built = world.code_of(image)
    run = {
        machine: bytes.fromhex(graph_build.code(machine)) for machine in world.MACHINES
    }
    return world.Code(run, built.leaves)


@pytest.fixture(scope="module")
def oracle(cases, code):
    """What the Python reference answers for every case, once."""
    return [world.referenced(code, case)[0] for case in cases]


def test_the_call_letters_are_the_ones_the_tests_write():
    assert seed_build.LEAF_LETTERS == world.letters()


def test_the_call_types_are_the_composers_order():
    assert graph_build.call_types() == [
        world.letters()[name]
        for name, _ in sorted(graph_header.leaves().items(), key=lambda item: item[1])
    ]


def test_the_starter_carries_the_seed_bytes(image, code):
    assert world.code_of(image).run == {
        machine: code.run[machine] for machine in world.MACHINES
    }


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_graph_run_is_bit_exact(machine, cases, code, oracle):
    for case, want in zip(cases, oracle, strict=True):
        have = world.emulated(machine, code, case)
        assert (have.status, have.out, have.memory) == (
            want.status,
            want.out,
            want.memory,
        ), case.name


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_graph_run_is_bit_exact_on_wasm32(cases, code, oracle):
    have = world.in_node(code, cases)
    for case, want, got in zip(cases, oracle, have, strict=True):
        assert (got.status, got.out, got.memory) == (
            want.status,
            want.out,
            want.memory,
        ), case.name


# ---- the edges of the C, in builders of this file (the corpus files stay read-only) -----------
# Every case below is differential too -- the reference answers what every seat answers --
# except the pins at the end, where the reference has no model and the C's answer stands.

N = graph_header.numbers()
LEAF = graph_header.leaves()
NONE = N["RXF_GRAPH_NONE"]
ZERO, NONZERO = N["RXF_MODE_ZERO"], N["RXF_MODE_NONZERO"]
OP = {name: N[f"RXF_OP_{name}"] for name in (
    "CALL", "MOVE", "MAP", "FIELD", "CONSTRUCT", "LESS", "INCREMENT", "GOTO", "BRANCH",
    "SWITCH", "RETURN", "REFUSE",
)}  # fmt: skip

assemble = programs.assemble
call = programs.call
call_card = programs.call_card
move = programs.move
edge = programs.edge
slot = programs.slot
lit = programs.lit
obj = programs.obj
handle = programs.handle
number = programs.number
Label = programs.Label
Case = world.Case

OUT_OF_RANGE, STALE, UNBOUND, TYPE_MISMATCH = 6, 12, 13, 14
DEPTH = N["RXF_REFUSE_CALL_DEPTH"]
BUDGET = N["RXF_REFUSE_CELL_BUDGET"]

BASE, AREA, OBJECTS = world.BASE, world.AREA, world.OBJECTS
PROGRAM_AT, PAYLOAD_AT, PROGRAM_ID = world.PROGRAM, world.PAYLOADS, world.PROGRAM_ID
RX_HEAD, RX_TAIL = AREA + 120, AREA + 124  # the doorbell words of the live area (docs/20 S7)
MAGIC = N["RXF_GRAPH_MAGIC"]
VAL = 8  # the slot a poked value is staged in (its address is what memory_copy reads)
BINDING = programs.BINDING
TEXT_AT = BASE + 0x5A00
A, B = 700002, 700003


def counted(frame: int, step: int) -> bytes:
    """A callee that calls itself (arg n): n == 0 answers 1000, else 1 + f(n-1).
    The frame is `frame` bytes at every level; the levels carve the run cell."""
    return assemble(
        frame,
        [(0, 8)],
        [
            [OP["LESS"], 16, *slot(0), *lit(1)],
            [OP["BRANCH"], *slot(16, 1), NONZERO, *edge("bottom"), *edge("down")],
            "down",
            call("wrapping_subtract_uint64_t", [slot(0), lit(1)], (16, 8)),
            call_card(A, [slot(16)], out=(24, 8)),
            call("wrapping_add_uint64_t", [slot(24), lit(1)], (32, 8)),
            [OP["RETURN"], *slot(32), 8],
            "bottom",
            move(16, 8, lit(1000)),
            [OP["RETURN"], *slot(16), 8],
        ],
    )


def leaf_program(width: int = 8) -> bytes:
    return assemble(width, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])


def two_calls(first: int, second: int) -> bytes:
    """Calls A then B one after the other (each answers its argument), and adds the two."""
    return assemble(
        64,
        [(0, 8)],
        [
            call_card(A, [slot(0)], out=(8, 8), status=16),
            call_card(B, [slot(0)], out=(24, 8), status=32),
            call("wrapping_add_uint64_t", [slot(8), slot(24)], (40, 8)),
            [OP["RETURN"], *slot(40), 8],
        ],
    )


def calls_callee_of(frame: int) -> bytes:
    """Entry (64) -> A (4096) -> B (frame): three regions of the cell at once."""
    return assemble(
        64,
        [(0, 8)],
        [
            call_card(A, [slot(0)], out=(8, 8), status=16),
            [OP["RETURN"], *slot(8), 8],
        ],
    )


def middle(frame: int) -> bytes:
    """A (frame) -> B (its own frame): the second link of the chain."""
    return assemble(
        frame,
        [(0, 8)],
        [
            call_card(B, [slot(0)], out=(frame - 8, 8)),
            [OP["RETURN"], *slot(frame - 8), 8],
        ],
    )


def cases_budget():
    """The cell is 8192 bytes and the frames of the chain carve it exactly; one byte over
    is refused RXF_REFUSE_CELL_BUDGET, never spilled. The base is
    frames[depth].base + frames[depth].frame_bytes at every link."""
    out = []
    # frames of 1024: 8 of them fill the cell exactly (entry + 7 calls)
    r = counted(1024, 1024)
    out.append((Case("budget 8 frames of 1024 exactly", r, number(7), {A: r}), 0, 1007))
    # the 9th frame is refused at the depth bound before the budget is looked at
    out.append((Case("depth 9th frame refused", r, number(8), {A: r}), DEPTH, 0))
    # frames of 1600: 5 fit (8000), the 6th is over the cell but under the depth bound
    r16 = counted(1600, 1600)
    out.append((Case("budget 5 frames of 1600 fit", r16, number(4), {A: r16}), 0, 1004))
    out.append((Case("budget 6th frame of 1600 refused", r16, number(5), {A: r16}), BUDGET, 0))
    # an entry that is the whole cell: alone it fits, its first call does not
    fat = assemble(8192, [(0, 8)], [
        call_card(A, [slot(0)], out=(8184, 8)),
        [OP["RETURN"], *slot(8184), 8],
    ])
    out.append((Case("budget entry frame of the whole cell", fat, number(3), {A: leaf_program()}), BUDGET, 0))
    # a zero-byte callee frame fits at the very end of the cell
    zero_frame = assemble(8192, [(0, 8)], [
        call_card(A, [], out=(8184, 8)),
        [OP["RETURN"], *slot(8184), 8],
    ])
    zero_callee = assemble(0, [], [[OP["RETURN"], *lit(9), 8]])
    out.append((Case("budget a zero frame at the end", zero_frame, number(3), {A: zero_callee}), 0, 9))
    # an entry frame past the cell is refused the budget on its own
    over = assemble(8200, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])
    out.append((Case("budget entry frame past the cell", over, number(3), {}), BUDGET, 0))
    # the cell shrinks back at the answer: two 4096-byte callees one after the other
    out.append((Case("budget the cell shrinks back", two_calls(0, 0), number(5), {A: leaf_program(4096), B: leaf_program(4096)}), 0, 10))
    # three regions at once: 64 + 4096 + 4032 = 8192 exactly
    out.append((Case("budget 64 + 4096 + 4032 exactly", calls_callee_of(0), number(5), {A: middle(4096), B: leaf_program(4032)}), 0, 5))
    # ... one byte more of inner frame and the third link is refused the budget
    out.append((Case("budget 64 + 4096 + 4040 over", calls_callee_of(0), number(5), {A: middle(4096), B: leaf_program(4040)}), BUDGET, 0))
    return out


def cases_answer():
    """answer(): what a callee's out word may be (native/graph.c), width quirks first."""
    out = []
    # a callee that answers 16 bytes to a call that named 8: refused 6, the run ends with it
    wide = assemble(64, [(0, 8)], [[OP["RETURN"], *slot(0, 16), 16]])
    caller = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], out=(8, 8), status=16),
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("answer wider than the out word", caller, number(5), {A: wide}), 6, 0))
    # ... the same wide answer with no destination named: dropped, the run goes on
    caller_none = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], status=16),
        [OP["RETURN"], *lit(3), 8],
    ])
    out.append((Case("answer wider than nothing", caller_none, number(5), {A: wide}), 0, 3))
    # a narrow answer is always 8 bytes: an out word of 4 cannot receive one (6)
    narrow = assemble(64, [(0, 8)], [[OP["RETURN"], *slot(0, 4), 4]])
    caller4 = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], out=(8, 4), status=16),
        [OP["RETURN"], *slot(8, 4), 4],
    ])
    out.append((Case("answer 8 bytes into a 4-byte word", caller4, number(5), {A: narrow}), 6, 0))
    # ... into an 8-byte word the low bytes hold the value (the argument was 0xFFFFFFFF)
    caller8 = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], out=(8, 8), status=16),
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("answer 8 bytes into an 8-byte word", caller8, number(0xFFFFFFFF), {A: narrow}), 0, 0xFFFFFFFF))
    # the rest of a wide destination word stays as the call zeroed it
    wide_out = assemble(64, [(0, 8)], [
        move(24, 8, lit((1 << 64) - 1)),
        move(32, 8, lit((1 << 64) - 1)),
        call_card(A, [slot(0)], out=(24, 16), status=16),
        [OP["RETURN"], *slot(24, 16), 16],
    ])
    echo = assemble(64, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])
    out.append((Case("answer leaves the rest zero", wide_out, number(7), {A: echo}), 0, 7))
    return out


def cases_refuse():
    """OP_REFUSE at every depth: the dynamic status, caught and uncaught."""
    out = []
    # OP_REFUSE at depth 0 returns the dynamic status, truncated to 32 bits
    trunc = assemble(64, [(0, 8)], [[OP["REFUSE"], *slot(0)]])
    out.append((Case("refuse the argument at depth 0", trunc, number(0x1_0000_0007), {}), 7, 0))
    trunc_lit = assemble(64, [], [[OP["REFUSE"], *lit((1 << 32) + 9)]])
    out.append((Case("refuse a literal truncated", trunc_lit, b"", {}), 9, 0))
    # refuse 0 at depth 0 ends the run with success and no output
    silent = assemble(64, [(0, 8)], [[OP["REFUSE"], *lit(0)]])
    out.append((Case("refuse 0 at depth 0", silent, number(5), {}), 0, 0))
    # a refusal with no status slot on its own call ends the whole run -- even though the
    # call above it has a status slot waiting
    deep_refuse = assemble(64, [(0, 8)], [[OP["REFUSE"], *lit(7)]])
    uncaught = assemble(64, [(0, 8)], [
        call_card(B, [slot(0)]),
        [OP["RETURN"], *slot(8), 8],
    ])
    outer = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], out=(8, 8), status=16),
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("an uncaught refusal skips the call above", outer, number(1), {A: uncaught, B: deep_refuse}), 7, 0))
    # ... and with a status slot on the callee's own call it is caught and the run goes on
    caught = assemble(64, [(0, 8)], [
        call_card(B, [slot(0)], status=8),
        call("wrapping_add_uint64_t", [slot(8), lit(100)], (16, 8)),
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("a caught refusal and the run goes on", outer, number(1), {A: caught, B: deep_refuse}), 0, 107))
    # refuse 0 in a callee with no status slot: the callee returns nothing and the caller
    # goes on after its call step (its out word stays as the call zeroed it)
    zero_refuse = assemble(64, [(0, 8)], [[OP["REFUSE"], *lit(0)]])
    zeroed = assemble(64, [(0, 8)], [
        move(8, 8, lit((1 << 64) - 1)),
        call_card(A, [slot(0)], out=(8, 8)),
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("refuse 0 in a callee is a quiet return", zeroed, number(5), {A: zero_refuse}), 0, 0))
    # a status slot outside the caller's frame is refused 6 at the answer, after the callee ran
    echo = assemble(64, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])
    bad_status = assemble(64, [(0, 8)], [
        call_card(A, [slot(0)], out=(8, 8), status=64),
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("a status slot past the caller frame", bad_status, number(5), {A: echo}), 6, 0))
    # a leaf call's status slot is checked after the leaf ran: the fill lands in memory,
    # then the run is refused 6
    filled = assemble(64, [(0, 8)], [
        call("memory_fill", [lit(BASE + 0x6100), lit(32), lit(0x5A), lit(8)], None, 64),
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("a leaf status slot past the frame", filled, b"", {}), 6, 0))
    # the call's argument is looked at before the out word: an unbound operand (13) is
    # reported before an out word past the frame (6)
    order = assemble(64, [(0, 8)], [
        call_card(A, [obj(31337)], out=(60, 8)),
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("a call binds its arguments first", order, number(5), {A: assemble(64, [(0, 24)], [[OP["RETURN"], *slot(0), 8]])}), 13, 0))
    return out


def cases_take():
    """take(): the two-pass staging of an edge's copies and the 256-byte bound."""
    out = []
    # staged bytes are bounded by RXF_GRAPH_COPY: 256 fit, one more is refused 6
    staged = assemble(512, [(0, 8)], [
        move(0, 8, lit(11)),
        move(256, 8, lit(22)),
        [OP["GOTO"], *edge("done", (slot(0, 256), 256, 256), (slot(256), 0, 1))],
        "done",
        [OP["RETURN"], *slot(256), 8],
    ])
    out.append((Case("a staged edge of 257 bytes", staged, b"", {}), 6, 0))
    staged_ok = assemble(512, [(0, 8)], [
        move(0, 8, lit(11)),
        move(256, 8, lit(22)),
        [OP["GOTO"], *edge("done", (slot(0, 256), 256, 256), (slot(256), 0, 0))],
        "done",
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a staged edge of 256 bytes", staged_ok, b"\x00" * 8, {}), 0, 11))
    # the operand is looked at before the staged width: an unbound source (13) is
    # reported before the 300-byte overflow (6)
    order = assemble(512, [], [
        [OP["GOTO"], *edge("done", (obj(31337), 0, 300))],
        "done",
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("an edge stages its sources first", order, b"", {}), 13, 0))
    # a partial operand width is staged as its bytes and the rest zero
    partial = assemble(128, [], [
        move(0, 8, lit(0xFFFFFFFFFFFFFFFF)),
        [OP["GOTO"], *edge("done", (slot(0, 3), 8, 8))],
        "done",
        [OP["RETURN"], *slot(8, 16), 16],
    ])
    out.append((Case("an edge copy of 3 present bytes", partial, b"", {}), 0, 0xFFFFFF))
    # every source is staged before any destination is written: the three copies rotate
    rotate = assemble(128, [], [
        move(0, 8, lit(1)),
        move(8, 8, lit(2)),
        move(16, 8, lit(3)),
        [OP["GOTO"], *edge("done", (slot(0), 8, 8), (slot(8), 16, 8), (slot(16), 0, 8))],
        "done",
        [OP["RETURN"], *slot(0, 24), 24],
    ])
    out.append((Case("an edge rotates three words", rotate, b"", {}), 0, 3))
    # a copy onto itself with a partial width zeroes the rest of the word
    onto = assemble(128, [], [
        move(0, 8, lit(0xFFFFFFFFFFFFFFFF)),
        [OP["GOTO"], *edge("done", (slot(0, 4), 0, 8))],
        "done",
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("an edge copy onto itself", onto, b"", {}), 0, 0xFFFFFFFF))
    # two copies into the same word: the later one wins
    twice = assemble(128, [], [
        move(0, 8, lit(1)),
        move(8, 8, lit(2)),
        [OP["GOTO"], *edge("done", (slot(0), 16, 8), (slot(8), 16, 8))],
        "done",
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("an edge writes its word twice", twice, b"", {}), 0, 2))
    return out


def cases_branch_switch():
    """Branch modes past the two the docs name, and the walk a switch takes to its edge."""
    out = []
    # a branch mode that is neither ZERO nor NONZERO behaves as NONZERO
    for mode in (2, 3):
        prog = assemble(64, [(0, 8)], [
            [OP["BRANCH"], *slot(0, 8), mode, *edge("first"), *edge("second")],
            "first",
            [OP["RETURN"], *lit(1), 8],
            "second",
            [OP["RETURN"], *lit(2), 8],
        ])
        out.append((Case(f"a branch mode {mode} with 0", prog, number(0), {}), 0, 2))
        out.append((Case(f"a branch mode {mode} with 5", prog, number(5), {}), 0, 1))
    # a switch: the first tag wins, the last edge is the default
    sw = assemble(64, [(0, 8)], [
        [OP["SWITCH"], *slot(0, 4), 3, 7, 7, 9,
         *edge("one"), *edge("two"), *edge("three"), *edge("default")],
        "one",
        [OP["RETURN"], *lit(1), 8],
        "two",
        [OP["RETURN"], *lit(2), 8],
        "three",
        [OP["RETURN"], *lit(3), 8],
        "default",
        [OP["RETURN"], *lit(4), 8],
    ])
    out.append((Case("a switch takes its first tag", sw, number(7), {}), 0, 1))
    out.append((Case("a switch takes its third tag", sw, number(9), {}), 0, 3))
    out.append((Case("a switch defaults", sw, number(10), {}), 0, 4))
    # ... the walk to the chosen edge looks at the edges before it: a fat empty edge is
    # refused 6 when the walk crosses it
    walk = assemble(64, [(0, 8)], [
        [OP["SWITCH"], *slot(0, 4), 2, 1, 2,
         *edge("first"), *edge("second"), *edge("default")],
        "first",
        [OP["RETURN"], *lit(1), 8],
        "second",
        [OP["RETURN"], *lit(2), 8],
        "default",
        [OP["RETURN"], *lit(3), 8],
    ])
    out.append((Case("a switch walks to its second tag", walk, number(2), {}), 0, 2))
    out.append((Case("a switch walks to its default", walk, number(3), {}), 0, 3))
    # a fat empty edge in the middle: a walk across it goes past the end (6)
    fat = assemble(64, [(0, 8)], [
        [OP["SWITCH"], *slot(0, 4), 2, 1, 2,
         *edge("first"), *[Label("second"), 99], *edge("default")],
        "first",
        [OP["RETURN"], *lit(1), 8],
        "second",
        [OP["RETURN"], *lit(2), 8],
        "default",
        [OP["RETURN"], *lit(3), 8],
    ])
    out.append((Case("a switch walk over a fat edge", fat, number(3), {}), 6, 0))
    out.append((Case("a switch to the edge before the fat one", fat, number(1), {}), 0, 1))
    return out


def cases_operands():
    """The three operand modes and the widths each one carries."""
    out = []
    # an Object operand is the address of its payload
    addr = assemble(64, [], [
        call("wrapping_add_uint64_t", [obj(BINDING, 8), lit(0)], (0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("an object operand is its address", addr, b"", {BINDING: handle(BINDING)}), 0, PAYLOAD_AT))
    # a literal wider than 8 bytes is refused wherever a value is read
    wide_lit = assemble(64, [], [[OP["LESS"], 0, *lit(5, 9), *lit(5, 9)]])
    out.append((Case("a literal of 9 bytes in a compare", wide_lit, b"", {}), 6, 0))
    # ... but a move takes what the operand has: a 9-wide literal moves 8 bytes into 8
    mov8 = assemble(64, [], [
        move(0, 8, lit(0xFFFFFFFFFFFFFFFF, 9)),
        [OP["RETURN"], *slot(0, 8), 8],
    ])
    out.append((Case("a move of 8 bytes from a 9-wide literal", mov8, b"", {}), 0, 0xFFFFFFFFFFFFFFFF))
    # ... and a move that would take 9 present bytes is refused 6
    mov9 = assemble(64, [], [
        move(0, 16, lit(0xFFFFFFFFFFFFFFFF, 9)),
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a move of 16 bytes from a 9-wide literal", mov9, b"", {}), 6, 0))
    # an increment writes at most 8 bytes: the rest of a wide word stays as it was
    incr = assemble(128, [(0, 16)], [
        move(16, 8, lit(0xAAAAAAAAAAAAAAAA)),
        move(24, 8, lit(0xAAAAAAAAAAAAAAAA)),
        [OP["INCREMENT"], 16, 16, *slot(0, 16)],
        [OP["RETURN"], *slot(16, 32), 32],
    ])
    out.append((Case("an increment of a wide word", incr, number(5) + number(0), {}), 0, 6))
    # a compare writes one byte of its word
    less = assemble(64, [(0, 8)], [
        move(8, 8, lit(0xAAAAAAAAAAAAAAAA)),
        [OP["LESS"], 8, *slot(0), *lit(10)],
        [OP["RETURN"], *slot(8, 16), 16],
    ])
    out.append((Case("a compare writes one byte", less, number(5), {}), 0, 0xAAAAAAAAAAAAAA01))
    return out


def cases_map():
    """MAP: the table at w[8..], the field at w[6] of the word the move wrote (the
    destination word sits away from the source slot -- a move onto its own slot is a C
    quirk pinned against the host runner below)."""
    out = []
    # the moved value is the field the table is looked up in
    hit = assemble(64, [(0, 8)], [
        [OP["MAP"], 16, 8, *slot(0), 0, 2, 5, 77, 9, 88],
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("a map finds its tag", hit, number(5), {}), 0, 77))
    miss = assemble(64, [(0, 8)], [
        [OP["MAP"], 16, 8, *slot(0), 0, 1, 6, 77],
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("a map with no matching tag", miss, number(5), {}), 0, 5))
    # the field is read as four bytes zero-extended: a tag over 32 bits never matches
    wide_tag = assemble(64, [(0, 8)], [
        [OP["MAP"], 16, 8, *slot(0), 0, 1, 0x1_0000_0005, 77],
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("a map tag over 32 bits", wide_tag, number(5), {}), 0, 5))
    # ... and the mapped value is written as four bytes
    trunc = assemble(64, [(0, 8)], [
        [OP["MAP"], 16, 8, *slot(0), 0, 1, 5, 0x1_0000_0042],
        [OP["RETURN"], *slot(16), 8],
    ])
    out.append((Case("a map value truncated to 4 bytes", trunc, number(5), {}), 0, 0x42))
    # the field offset is bounded by the frame, not by the word: it can reach past the
    # destination word and look at (and write into) the frame beside it
    past = assemble(64, [], [
        [OP["MAP"], 0, 8, *lit(0xAAAAAAAAAAAAAAAA), 8, 1, 0, 42],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a map field past its word", past, b"", {}), 0, 0xAAAAAAAAAAAAAAAA))
    edge = assemble(64, [(0, 8)], [
        [OP["MAP"], 0, 8, *lit(5), 60, 1, 0, 42],
        [OP["RETURN"], *slot(56), 8],
    ])
    out.append((Case("a map field at the frame edge", edge, number(5), {}), 0, 42 << 32))
    # ... and one byte further the field is refused
    off = assemble(64, [(0, 8)], [
        [OP["MAP"], 0, 8, *lit(5), 62, 1, 0, 42],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a map field past the frame", off, number(5), {}), 6, 0))
    # a table longer than the program is refused before the move
    huge = assemble(64, [], [
        [OP["MAP"], 0, 8, *lit(5), 0, 1_000_000],
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("a map table longer than the program", huge, b"", {}), 6, 0))
    # a table whose pairs run past the end of the program
    short = assemble(64, [], [
        [OP["MAP"], 0, 8, *lit(5), 0, 3, 5, 77],
    ])
    out.append((Case("a map table past the end", short, b"", {}), 6, 0))
    # the move is refused first: the table is never looked at
    unbound = assemble(64, [], [
        [OP["MAP"], 0, 8, *obj(31337), 0, 1, 0, 42],
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("a map whose move refuses", unbound, b"", {}), 13, 0))
    # the field is read after the move: a word whose tag lives in its high half
    inside = assemble(64, [(0, 8)], [
        [OP["MAP"], 16, 8, *slot(0), 4, 1, 2, 0xBBBBBBBB],
        [OP["RETURN"], *slot(16, 16), 16],
    ])
    out.append((Case("a map field inside its word", inside, number(0x2_0000_0001), {}), 0, 0xBBBBBBBB00000001))
    return out


def cases_field():
    """FIELD: the operand's bytes at w[6], w[7] of them, into a word it zeroes first."""
    out = []
    # the field is the operand's bytes at the offset, the rest of the word zero
    here = assemble(64, [(16, 8)], [
        [OP["FIELD"], 0, 8, *slot(16), 2, 3],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a field takes its slice", here, number(0x0807060504030201), {}), 0, 0x050403))
    # a field wider than the word it lands in is refused
    wide = assemble(64, [(16, 8)], [
        [OP["FIELD"], 0, 8, *slot(16), 0, 9],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field wider than its word", wide, number(5), {}), 6, 0))
    # the source is bounded by what the operand has: a literal is eight bytes
    past = assemble(64, [], [
        [OP["FIELD"], 0, 8, *lit(5), 6, 4],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field past a literal", past, b"", {}), 6, 0))
    # ... and an object operand is bounded by its payload
    too = assemble(64, [], [
        [OP["FIELD"], 0, 8, *obj(BINDING), 20, 8],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field past an object payload", too, b"", {BINDING: handle(BINDING)}), 6, 0))
    # the field of an object payload is the bytes the payload carries
    got = assemble(64, [], [
        [OP["FIELD"], 0, 8, *obj(BINDING), 8, 8],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field of an object payload", got, b"", {BINDING: handle(BINDING)}), 0, 1))
    # a field of zero bytes leaves the word it would have written zeroed
    zero = assemble(64, [(16, 8)], [
        [OP["FIELD"], 0, 8, *slot(16), 0, 0],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field of no bytes", zero, number(5), {}), 0, 0))
    # a slot operand is not bounded by its own width: the frame is what bounds the read
    beyond = assemble(64, [(16, 8)], [
        [OP["FIELD"], 0, 8, *slot(16), 8, 4],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field past a slot's width", beyond, number(5), {}), 0, 0))
    # the destination word is zeroed before the operand is read: a slice of the word it
    # writes into sees the zero (both the C and the reference)
    onto = assemble(64, [(0, 8)], [
        [OP["FIELD"], 0, 8, *slot(0), 0, 4],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a field of its own word", onto, number(5), {}), 0, 0))
    return out


def cases_construct():
    """CONSTRUCT: a four-byte tag at w[3] and the payload at w[8], both bounded by the word."""
    out = []
    # the tag beside the payload, in one word
    made = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 8, 7, *lit(0x0807060504030201), 0, 8],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a construct lays out its word", made, b"", {}), 0, 0x0807060504030201))
    made2 = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 8, 7, *lit(0x0807060504030201), 0, 8],
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("a construct writes its tag", made2, b"", {}), 0, 7))
    # the tag is four bytes: its high bits are dropped
    trunc = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 8, 0x1_0000_0007, *lit(1), 0, 8],
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("a construct tag truncated", trunc, b"", {}), 0, 7))
    # the payload takes the bytes the operand has
    narrow = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 8, 7, *lit(0x0807060504030201), 0, 3],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a construct payload of 3 bytes", narrow, b"", {}), 0, 0x030201))
    thin = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 8, 7, *lit(0x1234, 2), 0, 8],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a construct payload from a 2-byte operand", thin, b"", {}), 0, 0x1234))
    # the tag offset is bounded by the frame, not by the word: past the word but in frame
    outside = assemble(64, [], [
        [OP["CONSTRUCT"], 0, 8, 8, 42, *lit(7), 0, 8],
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("a construct tag past its word", outside, b"", {}), 0, 42))
    # ... and at the frame's edge
    edge = assemble(64, [], [
        [OP["CONSTRUCT"], 0, 8, 60, 42, *lit(7), 0, 8],
        [OP["RETURN"], *slot(56), 8],
    ])
    out.append((Case("a construct tag at the frame edge", edge, b"", {}), 0, 42 << 32))
    off = assemble(64, [], [
        [OP["CONSTRUCT"], 0, 8, 62, 42, *lit(7), 0, 8],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a construct tag past the frame", off, b"", {}), 6, 0))
    # the payload must fit the word: offset and width are bounded by it
    past = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 8, 0, 7, *lit(5), 4, 8],
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a construct payload past its word", past, b"", {}), 6, 0))
    # the payload lands last: it overwrites the tag where they meet
    over = assemble(32, [], [
        [OP["CONSTRUCT"], 0, 16, 0, 7, *lit(0x0807060504030201), 0, 8],
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a construct payload over its tag", over, b"", {}), 0, 0x0807060504030201))
    # the payload reads its operand after the word is zeroed: an operand inside the word
    # it constructs is the zero (both the C and the reference)
    onto = assemble(32, [(0, 8)], [
        [OP["CONSTRUCT"], 0, 16, 8, 7, *slot(0), 0, 8],
        [OP["RETURN"], *slot(8), 8],
    ])
    out.append((Case("a construct payload from its own word", onto, number(5), {}), 0, 7))
    return out


def cases_entry():
    """The entry program's binding: argument words are whole words, the counts must agree."""
    out = []
    # every parameter takes whole words of the arguments: the pad bytes between the
    # words are never read
    padded = assemble(32, [(0, 1), (8, 15)], [
        [OP["RETURN"], *slot(0, 24), 24],
    ])
    out.append((Case("parameters of 1 and 15 bytes", padded, b"\x11" + bytes(7) + b"\x22" * 15 + b"\xEE", {}), 0, 0x11))
    # ... a nine-byte parameter takes two words and copies nine bytes
    nine = assemble(32, [(0, 9)], [
        [OP["RETURN"], *slot(0, 16), 16],
    ])
    out.append((Case("a parameter of 9 bytes", nine, b"\x33" * 9 + b"\xEE" * 7, {}), 0, 0x3333333333333333))
    # the padded widths must fit the arguments the run was given
    short = assemble(32, [(0, 15), (16, 15)], [
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("parameters past the arguments", short, bytes(24), {}), 6, 0))
    # a parameter past the frame is refused at the binding
    over = assemble(16, [(0, 24)], [
        [OP["RETURN"], *lit(0), 8],
    ])
    out.append((Case("a parameter past the frame", over, bytes(24), {}), 6, 0))
    return out


def cases_pointer():
    """pointer() vs load(): where a pointer parameter wants a wide slot it gets its address."""
    out = []
    # a slot wider than eight bytes is its address where a pointer is wanted
    wide = assemble(64, [], [
        move(16, 16, lit(0)),
        call("memory_fill", [slot(16, 16), lit(8), lit(0x5A), lit(8)]),
        [OP["RETURN"], *slot(16, 16), 16],
    ])
    out.append((Case("a wide slot is its address", wide, b"", {}), 0, 0x5A5A5A5A5A5A5A5A))
    # a slot of eight bytes is its value there: the value is taken as the address
    narrow = assemble(64, [], [
        move(0, 8, lit(TEXT_AT)),
        call("memory_fill", [slot(0), lit(8), lit(0x5A), lit(8)]),
        [OP["RETURN"], *lit(1), 8],
    ])
    out.append((Case("a narrow slot is its value", narrow, b"", {}), 0, 1))
    # ... and a literal is its value
    direct = assemble(64, [], [
        move(16, 16, lit(0)),
        call("memory_fill", [lit(0), lit(0), lit(0x5A), lit(0)]),
        [OP["RETURN"], *lit(3), 8],
    ])
    out.append((Case("a literal is its value", direct, b"", {}), 0, 3))
    return out


def put_words(*words: int) -> bytes:
    return struct.pack(f"<{len(words)}Q", *(w & ((1 << 64) - 1) for w in words))


def poke(at: int, value: int) -> list:
    """A leaf call that writes the eight bytes of the staged value to the address `at`."""
    return call("memory_copy", [lit(at), lit(8), slot(VAL, 16), lit(8), lit(8)])


def poked(value: int) -> list:
    """The value a following poke writes: in the wide slot whose address memory_copy reads."""
    return move(VAL, 16, lit(value))


def entry_of(identities: tuple[int, ...], identity: int) -> int:
    """The object table row of a case object (sorted by id) or of the program (it sorts last)."""
    row = 8 + len(identities) if identity == PROGRAM_ID else 8 + sorted(identities).index(identity)
    return OBJECTS + 80 * row


def bell() -> list:
    """The doorbell: rx_head past rx_tail makes the next safe point serve (docs/20 S7)."""
    return [poked(1), poke(RX_HEAD, 1)]


def answer_of(value: int) -> bytes:
    return assemble(64, [(0, 8)], [[OP["RETURN"], *lit(value), 8]])


def embedded(*items: list) -> bytes:
    """A program with a second, complete header at byte offset 72: the run bound at that
    offset runs `items`, where the run bound at 0 answers 77."""
    words = [MAGIC, 64, 0, 0, 5, OP["RETURN"], 1, 77, 8, MAGIC, 64, 0, 0, 5]
    for item in items:
        words += item
    return put_words(*words)


def cases_protocol():
    """The doorbell of a safe point: what the reference models of the protocol too -- the
    ring, the pump it does not need, and the entry state a call card looks at."""
    out = []
    busy = assemble(64, [], [
        poked(1),
        poke(RX_HEAD, 1),
        call("wrapping_add_uint64_t", [lit(1), lit(1)], (0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a doorbell mid-run and the run goes on", busy, b"", {}, channel=True), 0, 2))
    out.append((Case("a doorbell with no pump in the table", busy, b"", {}, channel=True,
                     absent=frozenset({LEAF["live_pump"]})), 0, 2))
    out.append((Case("a doorbell with no channel", busy, b"", {}, channel=False), 0, 2))
    quiet = assemble(64, [], [
        poked(0x1_0000_0001),  # rx_head and rx_tail in one write: never a doorbell
        poke(RX_HEAD, 0x1_0000_0001),
        [OP["RETURN"], *lit(2), 8],
    ])
    out.append((Case("rx_head at rx_tail is no doorbell", quiet, b"", {}, channel=True), 0, 2))
    silent = assemble(64, [], [
        poked(2),
        poke(entry_of((), PROGRAM_ID) + 8, 2),  # the program's generation, and no doorbell
        [OP["RETURN"], *lit(77), 8],
    ])
    out.append((Case("a generation change no safe point looks at", silent, b"", {}, channel=True), 0, 77))
    return out + cases_call()


def cases_call():
    """A call card looks its callee up when the call runs: what a peer changed in the
    callee's entry since the run began is seen there (the run's own frames wait for a
    safe point -- the reference has no safe points)."""
    out = []
    callee, twin = answer_of(7), answer_of(42)
    where = PAYLOAD_AT + (len(callee) + 7) // 8 * 8  # where memory() puts the twin's words
    shifted = assemble(64, [], [
        poked(where),
        poke(entry_of((A, B), A) + 24, where),
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a callee whose payload moved", shifted, b"", {A: callee, B: twin}), 0, 42))
    shrunk = assemble(64, [], [
        poked(16),
        poke(entry_of((A,), A) + 32, 16),
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a callee whose object shrunk", shrunk, b"", {A: callee}), TYPE_MISMATCH, 0))
    renamed = assemble(64, [], [
        poked(55555),
        poke(entry_of((A,), A), 55555),
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a callee whose entry was renamed", renamed, b"", {A: callee}), UNBOUND, 0))
    aged = assemble(64, [], [
        poked(2),
        poke(entry_of((A,), A) + 8, 2),
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((Case("a callee generation the call card does not look at", aged, b"", {A: callee}), 0, 7))
    return out


def edge_cases() -> list:
    """Every differential case this file adds to the corpus."""
    out = []
    for build in (
        cases_budget,
        cases_answer,
        cases_refuse,
        cases_take,
        cases_branch_switch,
        cases_operands,
        cases_map,
        cases_field,
        cases_construct,
        cases_entry,
        cases_pointer,
        cases_protocol,
    ):
        out += [case for case, _, _ in build()]
    return out


def test_the_edge_cases_answer_what_they_are_for(code):
    """The documented answer of every edge case above: not just ref == seats, but the
    status and out word the case was built for."""
    for build in (
        cases_budget,
        cases_answer,
        cases_refuse,
        cases_take,
        cases_branch_switch,
        cases_operands,
        cases_map,
        cases_field,
        cases_construct,
        cases_entry,
        cases_pointer,
        cases_protocol,
    ):
        for case, status, out in build():
            want, _ = world.referenced(code, case)
            assert (want.status, int.from_bytes(want.out[:8], "little")) == (status, out), (
                case.name,
            )


# ---- what the reference has no model for: the C's answers, pinned on the seats ---------------


def protocol_pins() -> list[tuple[world.Case, int, int]]:
    """Safe points (S7), the run's handle, and the live words of a program: the reference
    snapshots its words and has no safe points, so these are the C's answers (as the
    line-faithful port native/graph.rxfs runs them), asserted the way test_graph.py pins
    programs.lookups()."""
    out = []
    simple = assemble(64, [], [[OP["RETURN"], *lit(77), 8]])

    def named(name, program, args=b"", objects=None, handle=(PROGRAM_ID, 1, 0)):
        return Case(name, program, args, objects or {}, handle=handle, channel=True)

    # how the run finds its program: an offset is only a program where a header stands
    out.append(
        (named("a handle at the end of the program", simple, handle=(PROGRAM_ID, 1, len(simple))), TYPE_MISMATCH, 0)
    )
    out.append(
        (named("a handle at the program's last word", simple, handle=(PROGRAM_ID, 1, len(simple) - 8)), TYPE_MISMATCH, 0)
    )
    out.append(
        (named("a handle at a second header", embedded([OP["RETURN"], 1, 42, 8, 8]), handle=(PROGRAM_ID, 1, 72)), 0, 42)
    )

    # the pump is a leaf the reference has no name for (it refuses 6 where the seats run
    # it): the pump's own answers are pinned here
    pumped = assemble(64, [], [
        call("live_pump", [lit(AREA)], None, 0),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((named("the pump of an idle area leaves it as it found it", pumped), 0, 0))
    ringing = assemble(64, [], [
        poked(1),
        poke(RX_HEAD, 1),
        call("live_pump", [lit(AREA)], None, 0),
        poked(0),
        poke(RX_HEAD, 0),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((named("the pump and the safe point on a ringing bell", ringing), 0, 0))

    # what a safe point rebinds: every frame's words are the objects' again (S7)
    entry = entry_of((), PROGRAM_ID)

    def reborn(name, *pokes, handle=(PROGRAM_ID, 1, 0), status=0):
        items = []
        for at, value in pokes:
            items += [poked(value), poke(at, value)]
        items += bell()
        items += [[OP["RETURN"], *lit(77), 8]]
        return (named(name, assemble(64, [], items), handle=handle), status, 0)

    out.append(reborn("a rebind of a changed generation", (entry + 8, 2), status=STALE))
    out.append(reborn("a rebind of an entry that is not live", (entry + 48, 0), status=UNBOUND))
    out.append(reborn("a rebind of a moved payload", (entry + 24, PROGRAM_AT + 4), status=OUT_OF_RANGE))
    out.append((
        named(
            "a rebind of an offset past the object",
            embedded(poked(64), poke(entry + 32, 64), *bell(), [OP["RETURN"], *lit(77), 8]),
            handle=(PROGRAM_ID, 1, 72),
        ),
        OUT_OF_RANGE,
        0,
    ))

    # a callee rebinds every frame above it, its caller's first
    outer = assemble(64, [], [
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    inward = assemble(64, [(0, 8)], [
        poked(2),
        poke(entry_of((A,), PROGRAM_ID) + 8, 2),
        *bell(),
        [OP["RETURN"], *lit(1), 8],
    ])
    out.append((named("a rebind of the caller from a callee", outer, objects={A: inward}), STALE, 0))

    # what a peer changes in the words themselves is looked at again: the run goes on
    # with the new words (S7), or refuses them when they are no instruction
    pre = len(poked(42)) + len(poke(0, 42)) + len(poked(1)) + len(poke(0, 1))
    patched = assemble(64, [], [
        poked(42),
        poke(PROGRAM_AT + 8 * (5 + pre + 2), 42),  # the literal of the Return below
        *bell(),
        [OP["RETURN"], *lit(7), 8],
    ])
    out.append((named("a code change behind a safe point", patched), 0, 42))
    broken = assemble(64, [], [
        poked(99),
        poke(PROGRAM_AT + 8 * (5 + pre), 99),  # the opcode of the Return below
        *bell(),
        [OP["RETURN"], *lit(7), 8],
    ])
    out.append((named("a code change that is no instruction", broken), OUT_OF_RANGE, 0))

    # the state check of a call card is the C's (the reference runs a callee not live)
    dead = assemble(64, [], [
        poked(0),
        poke(entry_of((A,), A) + 48, 0),
        call_card(A, [lit(1)], out=(0, 8)),
        [OP["RETURN"], *slot(0), 8],
    ])
    out.append((named("a callee that is no longer live", dead, objects={A: answer_of(7)}), UNBOUND, 0))
    return out


def move_alias_pins() -> list[tuple[world.Case, int, int]]:
    """move() onto a slot the destination overlaps (native/graph.c): the C zeroes its
    destination and then copies the operand's bytes -- the zero destroys the source first
    and the move lands zeros, so a move onto its own slot is NOT the identity. The Python
    reference reads the source before it writes and answers the identity: that is the one
    known divergence of the reference from the C, and the port keeps the C's behavior."""
    arg = (0x0807060504030201).to_bytes(8, "little")
    return [
        (
            Case(
                "a move onto its own slot",
                assemble(64, [(0, 8)], [[OP["MOVE"], 0, 8, *slot(0)], [OP["RETURN"], *slot(0), 8]]),
                arg,
                {},
            ),
            0,
            0,
        ),
        (
            Case(
                "a move onto the far half of its own word",
                assemble(64, [(0, 8)], [[OP["MOVE"], 0, 8, *slot(4, 4)], [OP["RETURN"], *slot(0), 8]]),
                arg,
                {},
            ),
            0,
            0,
        ),
        (
            Case(
                "a four-byte move onto its own slot",
                assemble(64, [(0, 8)], [[OP["MOVE"], 0, 4, *slot(0, 4)], [OP["RETURN"], *slot(0), 8]]),
                arg,
                {},
            ),
            0,
            0x0807060500000000,
        ),
    ]


def host_run(case: world.Case) -> tuple[int, int]:
    """The real interpreter of native/graph.c (tests/host/runner.c) on the same words."""
    with rxf_native.Memory(1 << 16) as memory:
        return rxf_native.run(
            case.program,
            case.args,
            memory,
            objects=dict[int, bytes | bytearray](case.objects),
        )


def test_the_safe_point_protocol_answers_the_cs_statuses(code):
    for case, status, out in protocol_pins():
        for machine in ("x86_64", "aarch64"):
            have = world.emulated(machine, code, case)
            assert (have.status, int.from_bytes(have.out[:8], "little")) == (status, out), (
                machine,
                case.name,
            )
    if shutil.which("node"):
        pins = protocol_pins()
        for (case, status, out), got in zip(pins, world.in_node(code, [c for c, _, _ in pins]), strict=True):
            assert (got.status, int.from_bytes(got.out[:8], "little")) == (status, out), case.name


def test_a_move_onto_its_own_slot_keeps_the_cs_zero(code):
    """The C zeroes a move's destination before it copies (native/graph.c move()): where the
    source is a slot the destination overlaps, the source is gone by the copy and the move
    lands zeros. Every seat and the host runner agree here; the Python reference, which
    reads the source first, answers the identity instead (see the docstring of
    move_alias_pins)."""
    for case, status, out in move_alias_pins():
        assert host_run(case) == (status, out), case.name
        for machine in ("x86_64", "aarch64"):
            have = world.emulated(machine, code, case)
            assert (have.status, int.from_bytes(have.out[:8], "little")) == (status, out), (
                machine,
                case.name,
            )
