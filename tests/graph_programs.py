"""Programs written by hand with a small assembler, for what the composed ones do not reach.

The programs of the starter come from graphs that were imported, and their
literals are what they are: an allocation of capacity 1 and size 8 always
refuses. These run every opcode, every shape of leaf and every refusal of the
interpreter on purpose. They are the same format (``native/graph.h``) and go
through the same interpreters.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import graph_header

from .graph_world import PROGRAM_ID, Case

N = graph_header.numbers()
LEAF = graph_header.leaves()
NONE = N["RXF_GRAPH_NONE"]
# The refusals of native/stdlib_runtime.h and native/basics.h that the interpreter itself returns.
OUT_OF_RANGE, STALE_GENERATION, UNBOUND, TYPE_MISMATCH = 6, 12, 13, 14
BUDGET = N["RXF_REFUSE_CELL_BUDGET"]
SLOT, LITERAL, OBJECT = (N[f"RXF_OPERAND_{k}"] for k in ("SLOT", "LITERAL", "OBJECT"))
ZERO, NONZERO = N["RXF_MODE_ZERO"], N["RXF_MODE_NONZERO"]
OP = {name: N[f"RXF_OP_{name}"] for name in (
    "CALL", "MOVE", "MAP", "FIELD", "CONSTRUCT", "LESS", "INCREMENT", "GOTO", "BRANCH",
    "SWITCH", "RETURN", "REFUSE",
)}  # fmt: skip


@dataclass(frozen=True)
class Label:
    name: str


Word = int | Label
Item = str | list[Word]


def slot(offset: int, width: int = 8) -> list[Word]:
    return [SLOT, offset, width]


def lit(value: int, width: int = 8) -> list[Word]:
    return [LITERAL, value, width]


def obj(identity: int, width: int = 24) -> list[Word]:
    return [OBJECT, identity, width]


def edge(target: str, *copies: tuple[list[Word], int, int]) -> list[Word]:
    words: list[Word] = [Label(target), len(copies)]
    for operand, destination, width in copies:
        words += [*operand, destination, width]
    return words


def call(
    leaf: str,
    operands: list[list[Word]],
    out: tuple[int, int] | None = None,
    status: int | None = None,
) -> list[Word]:
    words: list[Word] = [OP["CALL"], LEAF[leaf], len(operands)]
    words += list(out) if out else [NONE, 0]
    words.append(NONE if status is None else status)
    for operand in operands:
        words += operand
    return words


def move(destination: int, width: int, source: list[Word]) -> list[Word]:
    return [OP["MOVE"], destination, width, *source]


def assemble(
    frame: int,
    parameters: list[tuple[int, int]],
    items: list[Item],
    magic: int | None = None,
) -> bytes:
    """A program: the instructions, with labels (a string) between them."""
    header = 5 + 2 * len(parameters)
    where: dict[str, int] = {}
    at = header
    for item in items:
        if isinstance(item, str):
            where[item] = at
        else:
            at += len(item)
    words: list[int] = [
        N["RXF_GRAPH_MAGIC"] if magic is None else magic,
        frame,
        len(parameters),
        sum((width + 7) // 8 for _, width in parameters),
        header,
    ]
    for offset, width in parameters:
        words += [offset, width]
    for item in items:
        if not isinstance(item, str):
            words += [where[w.name] if isinstance(w, Label) else w for w in item]
    return struct.pack(f"<{len(words)}Q", *words)


def handle(identity: int, generation: int = 1, offset: int = 0) -> bytes:
    return struct.pack("<3Q", identity, generation, offset)


def number(value: int) -> bytes:
    return value.to_bytes(8, "little")


# ---- every leaf, in one transaction -------------------------------------------------------------

FIRST, NEEDLE, IDENT, SET, MAP = 0, 24, 48, 56, 80
TX, REF, VALUE, RESOLVED, READ, FOUND, HASHED, OPTION_SET, OPTION_MAP = (
    104, 152, 176, 184, 192, 200, 208, 216, 232,
)  # fmt: skip
RESULT = 264
CHAIN_PARAMETERS = [(FIRST, 24), (NEEDLE, 24), (IDENT, 8), (SET, 24), (MAP, 24)]
CHAIN_ARGUMENTS = (
    handle(1) + handle(1, 1, 6) + number(7) + handle(5) + handle(7)
)  # text, a word of it, an identity, a hash set, a hash map


def chain() -> bytes:
    """begin, allocate, write, append, format, read, resolve, publish, borrow, pin, search, ..."""
    n = 0

    def status() -> int:
        nonlocal n
        n += 1
        return RESULT + 8 * (n - 1)

    tx, ref = slot(TX, 48), slot(REF, 24)
    items: list[Item] = [
        call(
            "runtime_begin_private", [slot(FIRST, 24), slot(IDENT)], (TX, 48), status()
        ),
        call(
            "runtime_allocate_prepare",
            [lit(4), lit(100), lit(0), lit(64), lit(8), tx],
            (REF, 24),
            status(),
        ),
        call("runtime_write", [ref, tx, lit(0), obj(1, 8), lit(5)], None, status()),
        call("runtime_append", [ref, tx, obj(1, 8), lit(6)], None, status()),
        call("runtime_format_u32", [ref, tx, lit(4097, 4)], None, status()),
        call("runtime_read", [ref, tx, lit(0), lit(5)], (READ, 8), status()),
        call("runtime_resolve", [ref, lit(4)], (RESOLVED, 8), status()),
        call("runtime_publish", [tx], None, status()),
        call("runtime_borrow", [ref, lit(1)], None, status()),
        call("runtime_release_borrow", [ref, lit(1)], None, status()),
        call("runtime_pin", [ref], None, status()),
        call("runtime_unpin", [ref], None, status()),
        call(
            "runtime_search",
            [slot(FIRST, 24), lit(15), slot(NEEDLE, 24), lit(5), lit(0)],
            (FOUND, 8),
            status(),
        ),
        call("hash_u64", [lit(5), lit(7)], (HASHED, 8), status()),
        call(
            "runtime_hash_set_lookup",
            [slot(SET, 24), lit(0), lit(3), lit(200)],
            (OPTION_SET, 16),
            status(),
        ),
        call(
            "runtime_hash_map_lookup",
            [slot(MAP, 24), lit(0), lit(3), lit(100)],
            (OPTION_MAP, 32),
            status(),
        ),
        call("wrapping_add_uint64_t", [slot(READ), slot(HASHED)], (VALUE, 8), status()),
        call("less_uint64_t", [slot(READ), slot(HASHED)], (VALUE + 8, 8), status()),
        call("bit_not_uint64_t", [slot(VALUE)], (VALUE + 16, 8), status()),
        call("runtime_cleanup", [tx], None, status()),
        [OP["RETURN"], *slot(VALUE), 8],
    ]
    return assemble(512, CHAIN_PARAMETERS, items)


def rolled_back() -> bytes:
    """An allocation that is rolled back, then one that is cleaned up."""
    tx, ref = slot(TX, 48), slot(REF, 24)
    items: list[Item] = [
        call("runtime_begin_private", [slot(FIRST, 24), slot(IDENT)], (TX, 48), RESULT),
        call(
            "runtime_allocate_prepare",
            [lit(4), lit(100), lit(0), lit(64), lit(8), tx],
            (REF, 24),
            RESULT + 8,
        ),
        call("runtime_rollback", [tx], None, RESULT + 16),
        call(
            "runtime_begin_private",
            [slot(FIRST, 24), slot(IDENT)],
            (TX, 48),
            RESULT + 24,
        ),
        call(
            "runtime_allocate_prepare",
            [lit(4), lit(100), lit(0), lit(64), lit(8), tx],
            (REF, 24),
            RESULT + 32,
        ),
        call("runtime_cleanup", [tx], None, RESULT + 40),
        [OP["RETURN"], *ref, 24],
    ]
    return assemble(512, CHAIN_PARAMETERS, items)


# ---- control: loops, branches, switches, edges --------------------------------------------------

N_, I, SUM, FLAG, SEL, MAPPED, PICK, A, B, ST = 0, 8, 16, 24, 32, 48, 56, 64, 72, 80


def counting() -> bytes:
    """A loop that adds 0..n, then a map, a construct, a field, a switch and a swap."""
    items: list[Item] = [
        move(I, 8, lit(0)),
        move(SUM, 8, lit(0)),
        "loop",
        [OP["LESS"], FLAG, *slot(I), *slot(N_)],
        [OP["BRANCH"], *slot(FLAG, 1), NONZERO, *edge("body"), *edge("done")],
        "body",
        call("wrapping_add_uint64_t", [slot(SUM), slot(I)], (SUM, 8)),
        [OP["INCREMENT"], I, 8, *slot(I)],
        [OP["GOTO"], *edge("loop")],
        "done",
        [OP["MAP"], MAPPED, 8, *slot(SUM), 0, 3, 6, 99, 10, 100, 0, 5],
        [OP["CONSTRUCT"], SEL, 16, 0, 1, *slot(MAPPED), 8, 8],
        [OP["FIELD"], PICK, 8, *slot(SEL), 8, 8],
        move(A, 8, lit(11)),
        move(B, 8, lit(22)),
        [
            OP["SWITCH"], *slot(SEL, 4), 2, 0, 1,
            *edge("none"),
            *edge("some", (slot(A), B, 8), (slot(B), A, 8)),
            *edge("none"),
        ],
        "none",
        [OP["REFUSE"], *lit(14)],
        "some",
        [OP["RETURN"], *slot(PICK), 8],
    ]  # fmt: skip
    return assemble(128, [(N_, 8)], items)


def bits() -> bytes:
    """The numeric leaves a program reads and builds words with: and, or, shifts, subtract, equal."""
    items: list[Item] = [
        call("bit_and_uint64_t", [slot(N_), lit(0xFF00)], (A, 8)),
        call("shift_left_uint64_t", [slot(N_), slot(I, 4)], (B, 8)),
        call("shift_right_uint64_t", [slot(B), lit(8)], (B, 8)),
        call("bit_or_uint64_t", [slot(A), slot(B)], (SUM, 8)),
        call("wrapping_subtract_uint64_t", [slot(SUM), lit(1)], (SUM, 8)),
        call("equal_uint64_t", [slot(SUM), slot(N_)], (FLAG, 1)),
        [OP["RETURN"], *slot(SUM), 16],
    ]
    return assemble(128, [(N_, 8), (I, 8)], items)


def indirect() -> bytes:
    """A word read and written at an address that the program computed: the address of an object
    is a number (`wrapping_add` with 0), and a slot of eight bytes given as a pointer is that number."""
    where, past, loaded, successor = 0, 8, 16, 32
    items: list[Item] = [
        call("wrapping_add_uint64_t", [obj(BINDING, 8), lit(0)], (where, 8)),
        call(
            "memory_copy",
            [slot(loaded, 16), lit(16), slot(where), lit(8), lit(8)],
        ),  # loaded = *where
        call("wrapping_add_uint64_t", [slot(where), lit(8)], (past, 8)),
        call("wrapping_add_uint64_t", [slot(loaded), lit(1)], (successor, 8)),
        call(
            "memory_copy",
            [slot(past), lit(8), slot(successor, 16), lit(16), lit(8)],
        ),  # *(where + 8) = loaded + 1
        [OP["RETURN"], *slot(successor), 8],
    ]
    return assemble(128, [], items)


def swapping() -> bytes:
    """The two sides of an edge are read before either is written."""
    items: list[Item] = [
        move(A, 8, lit(11)),
        move(B, 8, lit(22)),
        [
            OP["GOTO"],
            *edge("done", (slot(A), B, 8), (slot(B), A, 8), (lit(7, 4), ST, 8)),
        ],
        "done",
        [OP["RETURN"], *slot(A, 24), 24],
    ]
    return assemble(128, [], items)


def zero_branch() -> bytes:
    """A branch on the status of a call: 0 takes the first edge."""
    items: list[Item] = [
        call("less_uint64_t", [slot(N_), lit(1)], (A, 8), ST),
        [OP["BRANCH"], *slot(ST, 4), ZERO, *edge("ok"), *edge("no")],
        "ok",
        [OP["RETURN"], *slot(A), 8],
        "no",
        [OP["REFUSE"], *slot(ST, 4)],
    ]
    return assemble(128, [(N_, 8)], items)


def empty_block() -> bytes:
    """A switch whose first edge lands on the next instruction: the block under its label is
    empty, so the jump distance is 0. This is the witness of the writer's regression: `put32`
    spliced its `o_size`-dependent argument into each of its four puts, and a distance of 0
    came out 0xffffff00 (the borrow of every put reached the next byte), so the jump landed
    one byte into the next instruction. Only a distance of 0 mod 256 shows it -- every other
    distance writes the same bytes the C writer does."""
    items: list[Item] = [
        move(A, 8, lit(0)),
        [OP["SWITCH"], *slot(A, 4), 1, 0, *edge("empty"), *edge("later")],
        "empty",
        "later",
        [OP["RETURN"], *slot(A), 8],
    ]
    return assemble(128, [], items)


# ---- the arithmetic leaves of the flat profile: multiply, divide, remainder ---------------------

# W/C are the products and Q64/R64/QI/RI the quotients and remainders of a run, Q32/R32/RU32 the
# 32-bit ones, SA/SB/SS/SD the sign bits of operands and results, TMP/TMP2/FLIP the flags an
# overflow computation is built from, and F0..F3 the four with.overflow flags it returns.
W, C = 88, 96
Q64, R64, QI, RI = 104, 112, 120, 128
Q32, R32, RU32 = 88, 92, 96
SA, SB, SS, SD = 120, 128, 136, 144
TMP, TMP2, FLIP = 152, 160, 168
F0, F1, F2, F3 = 176, 184, 192, 200
DIFF = 208
SIGN = 1 << 63
MASK64 = (1 << 64) - 1


def guarded(calls: list[tuple[str, list[list[Word]], tuple[int, int]]]) -> list[Item]:
    """Each call with the branch that ends the run with the status it refused with: the shape
    of a composed call (tools/native/compose.py writes the same)."""
    items: list[Item] = []
    for n, (leaf, operands, out) in enumerate(calls):
        items.append(call(leaf, operands, out, ST))
        items.append([OP["BRANCH"], *slot(ST, 4), ZERO, *edge(f"step{n}"), *edge("no")])
        items.append(f"step{n}")
    return items


def products() -> bytes:
    """The multiply leaves: the wrapping product and both checked products. The checked ones
    are llvm.umul/smul.with.overflow: their status is the overflow bit (see native/graph.h)."""
    items: list[Item] = guarded(
        [
            ("wrapping_multiply_uint64_t", [slot(N_), slot(I)], (W, 8)),
            ("checked_multiply_int64_t", [slot(N_), slot(I)], (C, 8)),
            ("checked_multiply_uint64_t", [slot(N_), slot(I)], (C, 8)),
        ]
    ) + [
        [OP["RETURN"], *slot(W), 16],
        "no",
        [OP["REFUSE"], *slot(ST, 4)],
    ]
    return assemble(256, [(N_, 8), (I, 8)], items)


def quotients() -> bytes:
    """The divide and remainder leaves of 64 bits, unsigned and signed, returned as four words:
    q and r of the unsigned division, q and r of the signed one (which truncates toward zero)."""
    items: list[Item] = guarded(
        [
            ("divide_uint64_t", [slot(N_), slot(I)], (Q64, 8)),
            ("remainder_uint64_t", [slot(N_), slot(I)], (R64, 8)),
            ("divide_int64_t", [slot(N_), slot(I)], (QI, 8)),
            ("remainder_int64_t", [slot(N_), slot(I)], (RI, 8)),
        ]
    ) + [
        [OP["RETURN"], *slot(Q64), 32],
        "no",
        [OP["REFUSE"], *slot(ST, 4)],
    ]
    return assemble(256, [(N_, 8), (I, 8)], items)


def word_quotients() -> bytes:
    """The divide and remainder leaves of 32 bits: the operands are the low word of a slot.
    The run returns three words: q and r of the signed division, r of the unsigned one."""
    items: list[Item] = guarded(
        [
            ("divide_int32_t", [slot(N_, 4), slot(I, 4)], (Q32, 4)),
            ("remainder_int32_t", [slot(N_, 4), slot(I, 4)], (R32, 4)),
            ("remainder_uint32_t", [slot(N_, 4), slot(I, 4)], (RU32, 4)),
        ]
    ) + [
        [OP["RETURN"], *slot(Q32), 12],
        "no",
        [OP["REFUSE"], *slot(ST, 4)],
    ]
    return assemble(256, [(N_, 8), (I, 8)], items)


def overflow_flags() -> bytes:
    """The with.overflow shapes of the adds and subtracts, as leaf and compare: the two flags
    of llvm.uadd/usub.with.overflow are the carry and the borrow of a wrapping operation, and
    the two of llvm.sadd/ssub.with.overflow are the sign compare of the operands and result.
    The run returns the four flags as four words (F0 uadd, F1 usub, F2 sadd, F3 ssub)."""
    items: list[Item] = [
        # uadd.with.overflow: the carry of a wrapping add is its overflow bit
        call("wrapping_add_uint64_t", [slot(N_), slot(I)], (SUM, 8)),
        call("less_uint64_t", [slot(SUM), slot(N_)], (F0, 8)),
        # usub.with.overflow: the borrow of a wrapping subtract is its overflow bit
        call("wrapping_subtract_uint64_t", [slot(N_), slot(I)], (DIFF, 8)),
        call("less_uint64_t", [slot(N_), slot(I)], (F1, 8)),
        # sadd.with.overflow: the signs of the operands agree and the sign of the sum flips
        call("bit_and_uint64_t", [slot(N_), lit(SIGN)], (SA, 8)),
        call("bit_and_uint64_t", [slot(I), lit(SIGN)], (SB, 8)),
        call("bit_and_uint64_t", [slot(SUM), lit(SIGN)], (SS, 8)),
        call("equal_uint64_t", [slot(SA), slot(SB)], (TMP, 8)),
        call("equal_uint64_t", [slot(SS), slot(SA)], (TMP2, 8)),
        call("wrapping_subtract_uint64_t", [lit(1), slot(TMP2)], (FLIP, 8)),
        call("bit_and_uint64_t", [slot(TMP), slot(FLIP)], (F2, 8)),
        # ssub.with.overflow: the signs of the operands differ and the sign of the difference flips
        call("bit_and_uint64_t", [slot(DIFF), lit(SIGN)], (SD, 8)),
        call("equal_uint64_t", [slot(SD), slot(SA)], (TMP2, 8)),
        call("wrapping_subtract_uint64_t", [lit(1), slot(TMP2)], (FLIP, 8)),
        call("wrapping_subtract_uint64_t", [lit(1), slot(TMP)], (TMP2, 8)),
        call("bit_and_uint64_t", [slot(TMP2), slot(FLIP)], (F3, 8)),
        [OP["RETURN"], *slot(F0), 32],
    ]
    return assemble(256, [(N_, 8), (I, 8)], items)


def wide_return() -> bytes:
    items: list[Item] = [
        [OP["CONSTRUCT"], SEL, 16, 4, 1, *slot(N_), 8, 8],
        [OP["RETURN"], *slot(SEL, 16), 16],
    ]
    return assemble(128, [(N_, 8)], items)


def object_value() -> bytes:
    """An object is its payload: moved, read as a field, returned."""
    items: list[Item] = [
        move(SEL, 24, obj(BINDING)),
        [OP["FIELD"], PICK, 8, *obj(BINDING), 8, 8],
        [OP["RETURN"], *obj(BINDING), 24],
    ]
    return assemble(128, [], items)


BINDING = 700001


# ---- a call card: a callee that is a program of its own (graph.h's RXF_GRAPH_CALL) ----------------

CALLEE, NESTED = 700002, 700003
CAUGHT = 2  # a code the callee refuses with: DIVISION_BY_ZERO of native/basics.h


def call_card(
    callee: int,
    operands: list[list[Word]],
    out: tuple[int, int] | None = None,
    status: int | None = None,
) -> list[Word]:
    """A call card: the callee is a program of its own, named by the object id of its
    words (the program operand of graph.h's RXF_GRAPH_CALL), its parameters the
    arguments that follow as run-entry ARGS shapes (a scalar one word, a handle three)."""
    words: list[Word] = [OP["CALL"], N["RXF_GRAPH_CALL"], len(operands)]
    words += list(out) if out else [NONE, 0]
    words.append(NONE if status is None else status)
    words += [OBJECT, callee, 0]
    for operand in operands:
        words += operand
    return words


def callee_step() -> bytes:
    """A callee: one scalar parameter, answered as the parameter plus one (a leaf runs in
    the callee's own frame, like any program)."""
    return assemble(
        64,
        [(0, 8)],
        [
            call("wrapping_add_uint64_t", [slot(0), lit(1)], (8, 8)),
            [OP["RETURN"], *slot(8), 8],
        ],
    )


def callee_identity() -> bytes:
    """A callee whose parameter is a handle (its three words of frame), answered as its
    first word -- the identity the handle names."""
    return assemble(64, [(0, 24)], [[OP["RETURN"], *slot(0), 8]])


def callee_middle() -> bytes:
    """A callee that calls a callee: two cards deep, the answers stack back."""
    return assemble(
        64,
        [(0, 8)],
        [
            call_card(NESTED, [slot(0)], out=(8, 8)),
            [OP["RETURN"], *slot(8), 8],
        ],
    )


def callee_refuses() -> bytes:
    """A callee whose every run is its Refuse (the shape of every mp_raise_*): the call's
    status slot gets the code, or the run ends with it when nothing catches it."""
    return assemble(64, [(0, 8)], [[OP["REFUSE"], *lit(CAUGHT)]])


def call_cases() -> list[tuple[Case, int, int]]:
    """The call card on purpose: every shape of its run and every card that cannot go on
    -- the case, the status the run must answer, and the out word (0 where the run
    refused or answers nothing)."""
    recurses = assemble(
        64,
        [(0, 8)],
        [
            call_card(CALLEE, [slot(0)], out=(8, 8)),
            [OP["RETURN"], *slot(8), 8],
        ],
    )
    return [
        (
            Case(
                "call answers",
                assemble(
                    64,
                    [(0, 8)],
                    [
                        call_card(CALLEE, [slot(0)], out=(8, 8), status=16),
                        [OP["RETURN"], *slot(8), 8],
                    ],
                ),
                number(41),
                {CALLEE: callee_step()},
            ),
            0,
            42,
        ),
        (
            Case(
                "call with a handle",
                assemble(
                    64,
                    [],
                    [
                        call_card(CALLEE, [obj(BINDING)], out=(8, 8)),
                        [OP["RETURN"], *slot(8), 8],
                    ],
                ),
                b"",
                {CALLEE: callee_identity(), BINDING: handle(BINDING)},
            ),
            0,
            BINDING,
        ),
        (
            Case(
                "call nested",
                assemble(
                    64,
                    [(0, 8)],
                    [
                        call_card(CALLEE, [slot(0)], out=(8, 8)),
                        [OP["RETURN"], *slot(8), 8],
                    ],
                ),
                number(41),
                {CALLEE: callee_middle(), NESTED: callee_step()},
            ),
            0,
            42,
        ),
        (
            Case(
                "call catches a refusal",
                assemble(
                    64,
                    [],
                    [
                        call_card(CALLEE, [lit(7)], out=(8, 8), status=16),
                        move(24, 8, slot(16)),
                        [OP["RETURN"], *slot(24), 8],
                    ],
                ),
                b"",
                {CALLEE: callee_refuses()},
            ),
            0,
            CAUGHT,
        ),
        (
            Case(
                "call lets a refusal through",
                assemble(
                    64,
                    [],
                    [
                        call_card(CALLEE, [lit(7)]),
                        [OP["RETURN"], *lit(0), 8],
                    ],
                ),
                b"",
                {CALLEE: callee_refuses()},
            ),
            CAUGHT,
            0,
        ),
        (
            Case("refuses call depth", recurses, number(1), {CALLEE: recurses}),
            N["RXF_REFUSE_CALL_DEPTH"],
            0,
        ),
        (
            Case(
                "refuses an unbound callee",
                assemble(
                    64,
                    [(0, 8)],
                    [
                        call_card(31337, [slot(0)]),
                        [OP["RETURN"], *lit(0), 8],
                    ],
                ),
                number(1),
                {BINDING: handle(BINDING)},
            ),
            UNBOUND,
            0,
        ),
        (
            Case(
                "refuses a callee that is no program",
                assemble(
                    64,
                    [(0, 8)],
                    [
                        call_card(CALLEE, [slot(0)]),
                        [OP["RETURN"], *lit(0), 8],
                    ],
                ),
                number(1),
                {CALLEE: bytes(40)},
            ),
            TYPE_MISMATCH,
            0,
        ),
    ]


# ---- the variable-length cells of the run cell: a frame over 1024 bytes, and the budget --------

FRAMED, LOOPED = 700004, 700005


def frame_heavy() -> bytes:
    """A frame of 1608 bytes, well over the 1024 the old run cell gave every level: its
    slots lie past that wall (1592, 1600), and the program calls itself (a call card of
    its own object id) so a chain of these frames rides the run cell. ``f(n)`` answers
    ``1000 + n``: every level computes its answer in the far slot of its own frame."""
    far, out, flag, bottom = 1592, 16, 8, 1600
    items: list[Item] = [
        [OP["LESS"], flag, *slot(0), *lit(1)],
        [OP["BRANCH"], *slot(flag, 1), NONZERO, *edge("bottom"), *edge("down")],
        "down",
        call("wrapping_subtract_uint64_t", [slot(0), lit(1)], (far, 8)),
        call_card(FRAMED, [slot(far)], out=(out, 8)),
        call("wrapping_add_uint64_t", [slot(out), lit(1)], (far, 8)),
        [OP["RETURN"], *slot(far), 8],
        "bottom",
        move(bottom, 8, lit(1000)),
        [OP["RETURN"], *slot(bottom), 8],
    ]
    return assemble(1608, [(0, 8)], items)


def linked_depth() -> bytes:
    """A chain that spends the eight frame links on sixty-four byte frames: refused
    RXF_REFUSE_CALL_DEPTH -- the depth bound of graph.h is a link bound, not a byte wall."""
    return assemble(
        64,
        [(0, 8)],
        [
            call_card(LOOPED, [slot(0)], out=(8, 8)),
            [OP["RETURN"], *slot(8), 8],
        ],
    )


def frame_cases() -> list[tuple[Case, int, int]]:
    """The variable-length cells of the run cell on purpose: a frame of 1608 bytes runs as
    it declares -- alone, five deep (8040 bytes of frames, inside the cell), and as a callee
    under a small entry. These run on the host runner (tests/host/runner.c, which compiles
    native/graph.c per checkout), not in synthetic()/refusals(): those cases run on the
    interpreter bytes of the starter, which the importer lane regenerates. The tuple is
    (the case, the status the run must answer, the out word)."""
    heavy = frame_heavy()
    called = assemble(
        64,
        [],
        [
            call_card(FRAMED, [lit(4)], out=(8, 8)),
            [OP["RETURN"], *slot(8), 8],
        ],
    )
    return [
        (Case("a frame of 1608 bytes", heavy, number(0), {FRAMED: heavy}), 0, 1000),
        (Case("five frames of 1608 bytes", heavy, number(4), {FRAMED: heavy}), 0, 1004),
        (Case("a 1608-byte frame as a callee", called, b"", {FRAMED: heavy}), 0, 1004),
    ]


def budget_cases() -> list[tuple[Case, int, int]]:
    """The budget is cell bytes and it refuses, never silently overruns: a declared frame the
    cell cannot hold at all, and a call chain whose frames exceed the cell while the eight
    links are nowhere near spent (the refusal is RXF_REFUSE_CELL_BUDGET, not the link bound).
    The link bound is proven beside it: eight links of tiny frames refuse CALL_DEPTH."""
    heavy, budget = frame_heavy(), N["RXF_REFUSE_CELL_BUDGET"]
    oversized = assemble(8200, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])
    return [
        (Case("a frame past the cell budget", oversized, number(3), {}), budget, 0),
        (
            Case("a chain past the cell budget", heavy, number(6), {FRAMED: heavy}),
            budget,
            0,
        ),
        (
            Case(
                "the eight links, not the bytes",
                linked_depth(),
                number(1),
                {LOOPED: linked_depth()},
            ),
            N["RXF_REFUSE_CALL_DEPTH"],
            0,
        ),
    ]


# ---- refusals of the interpreter ----------------------------------------------------------------


def broken() -> dict[str, tuple[bytes, int]]:
    """Programs the interpreter must refuse, and the refusal each one gets."""
    ok = assemble(64, [(0, 8)], [[OP["RETURN"], *slot(0), 8]])
    words = list(struct.unpack(f"<{len(ok) // 8}Q", ok))

    def program(*items: Item, frame: int = 64, magic: int | None = None) -> bytes:
        return assemble(frame, [(0, 8)], list(items), magic)

    ret = [OP["RETURN"], *slot(0), 8]
    return {
        "magic": (program(ret, magic=1), TYPE_MISMATCH),
        "unknown opcode": (program([99]), OUT_OF_RANGE),
        "past the end": (program(move(0, 8, slot(0))), OUT_OF_RANGE),
        # the frame is what the program declares, exactly (docs/20 S6): the refusal of a
        # frame is the budget, not a wall -- 9000 bytes no run cell could hold
        "frame": (program(ret, frame=9000), BUDGET),
        "frame not whole words": (program(ret, frame=60), OUT_OF_RANGE),
        "slot out of the frame": (program([OP["RETURN"], *slot(64), 8]), OUT_OF_RANGE),
        "unbound object": (program([OP["RETURN"], *obj(31337, 8), 8]), UNBOUND),
        "literal too wide": (program([OP["RETURN"], *lit(1, 16), 16]), OUT_OF_RANGE),
        "unknown leaf": (program([OP["CALL"], 99, 0, NONE, 0, NONE]), OUT_OF_RANGE),
        "wrong operand count": (
            program(call("hash_u64", [lit(1)], (8, 8))),
            OUT_OF_RANGE,
        ),
        "out missing": (program(call("hash_u64", [lit(1), lit(2)])), OUT_OF_RANGE),
        "out out of the frame": (
            program(call("hash_u64", [lit(1), lit(2)], (64, 8))),
            OUT_OF_RANGE,
        ),
        "edge out of the program": (program([OP["GOTO"], 0, 5, 0]), OUT_OF_RANGE),
        "too many copies": (program([OP["GOTO"], 0, 9999]), OUT_OF_RANGE),
        "the program ends": (program([OP["GOTO"], *edge("a")], "a"), OUT_OF_RANGE),
        "parameters": (ok[:16] + number(5) + ok[24:], OUT_OF_RANGE),
        "short": (struct.pack("<2Q", *words[:2]), TYPE_MISMATCH),
    }


def cases_of(name: str, program: bytes, args: bytes) -> Case:
    return Case(name, program, args, {BINDING: handle(BINDING)})


def refused_arithmetic() -> dict[str, tuple[bytes, bytes, int]]:
    """A call of an arithmetic leaf that refuses: the program, the arguments that make the leaf
    refuse, and the refusal the run must get (the codes of native/basics.h)."""
    return {
        "a product that leaves the type": (
            products(),
            number(MASK64) + number(MASK64),
            1,
        ),
        "INT64_MIN * -1": (
            products(),
            number(1 << 63) + number(MASK64),
            1,
        ),
        "a signed product that leaves the type where the unsigned fits": (
            products(),
            number(3) + number(1 << 62),
            1,
        ),
        "a division by zero": (
            quotients(),
            number(5) + number(0),
            2,
        ),
        "INT64_MIN / -1": (
            quotients(),
            number(1 << 63) + number(MASK64),
            1,
        ),
        "a 32-bit division by zero": (
            word_quotients(),
            number(5) + number(0),
            2,
        ),
        "INT32_MIN / -1": (
            word_quotients(),
            number(1 << 31) + number(0xFFFFFFFF),
            1,
        ),
    }


def refusals() -> list[tuple[Case, int]]:
    """Each refused program as a case, with the refusal it must get: the refusals of the
    interpreter, and the refusals of the arithmetic leaves (divisions by zero, INT_MIN / -1
    and the products that leave their type)."""
    return (
        [
            (cases_of(f"refuses {name}", program, number(3)), status)
            for name, (program, status) in broken().items()
        ]
        + [
            (cases_of(f"refuses {name}", program, args), status)
            for name, (program, args, status) in refused_arithmetic().items()
        ]
        + [(case, status) for case, status, _ in call_cases() if status]
    )


def synthetic() -> list[Case]:
    """Every program above, with the arguments that make it do something."""
    found: list[Case] = []
    for name, build in (
        ("chain", chain),
        ("rolled back", rolled_back),
    ):
        found.append(cases_of(name, build(), CHAIN_ARGUMENTS))
    for count in (0, 1, 5, 40):
        found.append(cases_of(f"counting {count}", counting(), number(count)))
    for name, build, args in (
        ("bits 0x1234 << 4", bits, number(0x1234) + number(4)),
        ("bits 0", bits, number(0) + number(0)),
        ("bits 1", bits, number(1) + number(1)),
        ("bits count 64", bits, number(5) + number(64)),
        ("indirect", indirect, b""),
        ("swapping", swapping, b""),
        ("zero branch 0", zero_branch, number(0)),
        ("zero branch 5", zero_branch, number(5)),
        ("empty block edge", empty_block, b""),
        ("wide return", wide_return, number(9)),
        ("object value", object_value, b""),
        ("products 7 6", products, number(7) + number(6)),
        ("products 3 5", products, number(3) + number(5)),
        ("products high 1", products, number((1 << 63) + 1) + number(1)),
        ("quotients 100 7", quotients, number(100) + number(7)),
        ("quotients -7 2", quotients, number(MASK64 - 6) + number(2)),
        ("quotients min 1", quotients, number(1 << 63) + number(1)),
        ("word quotients 100 7", word_quotients, number(100) + number(7)),
        ("word quotients -7 2", word_quotients, number(MASK64 - 6) + number(2)),
        ("overflow flags 5 3", overflow_flags, number(5) + number(3)),
        ("overflow flags wrap add", overflow_flags, number(MASK64) + number(1)),
        ("overflow flags sadd", overflow_flags, number((1 << 63) - 1) + number(1)),
        ("overflow flags smul", overflow_flags, number(1 << 63) + number(MASK64)),
        ("overflow flags ssub", overflow_flags, number(1 << 63) + number(1)),
    ):
        found.append(cases_of(name, build(), args))
    for (
        case,
        _,
        _,
    ) in call_cases():  # the call card: a callee that is a program of its own
        found.append(case)
    return found + [case for case, _ in refusals()]


# ---- how the program is found --------------------------------------------------------------------


def lookups() -> list[tuple[Case, int]]:
    """The same program called by handles that must not reach it, and the refusal for each."""
    base = cases_of("lookup", counting(), number(3))

    def by(handle_: tuple[int, int, int], absent: frozenset[int] = frozenset()) -> Case:
        return Case(
            base.name,
            base.program,
            base.args,
            base.objects,
            handle=handle_,
            absent=absent,
        )

    return [
        (by((PROGRAM_ID, 2, 0)), STALE_GENERATION),
        (by((PROGRAM_ID + 1, 1, 0)), UNBOUND),
        (by((PROGRAM_ID, 1, 4)), OUT_OF_RANGE),
        (by((PROGRAM_ID, 1, 1 << 20)), OUT_OF_RANGE),
        (by((PROGRAM_ID, 1, 0), frozenset({LEAF["wrapping_add_uint64_t"]})), UNBOUND),
    ]
