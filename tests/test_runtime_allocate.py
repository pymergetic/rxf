"""The allocation leaves of the handle lane (docs/23 4.9): ``runtime_allocate`` and ``runtime_release``.

``runtime_allocate`` is ``allocate_prepare`` + ``publish`` in one step on a row the leaf picks
itself: no transaction, no caller-chosen id. The allocator state is a cell behind the Allocator
capability (the context struct is untouched), the payload is carved from the bump region and
**zero-filled**, the row goes LIVE with a generation one higher than it had (so a handle of the
row's earlier life is refused 12), and a pin flag keeps the sliding pass off the payload.
``runtime_release`` is the existing release as leaf 51: the row returns to the pool, and no memory
comes back (compaction, not free lists).

Every case is a program through ``tests/graph_world.py`` -- a context with a heap, a pool of free
rows and the capability. The Seed builds the leaves and the interpreter NOW, so the cases hold
before any starter regeneration: the second interpreter (``tests/graph_reference.py``) runs each
over the x86_64 leaves, and x86_64, aarch64 and wasm32 run the interpreter and the leaves
themselves; status, out and the whole memory must be equal. What the leaf did is held to a model
written here from the design, not from the leaf.
"""

from __future__ import annotations

import shutil
import struct
from dataclasses import dataclass

import graph_header
import link
import pytest
import seed_build

from pymergetic.rxftk.core import tree

from . import graph_world as world
from . import rxf_native, scenario
from .graph_programs import (
    OP,
    OUT_OF_RANGE,
    STALE_GENERATION,
    UNBOUND,
    Item,
    Word,
    assemble,
    call,
    edge,
    lit,
    move,
    slot,
)
from .snippets import STARTER

N = graph_header.numbers()
HANDLE = N["RXF_OPERAND_HANDLE"]
OUT_OF_MEMORY, PINNED = 9, 11
LIVE = scenario.LIVE
LEAVES = [
    "runtime_allocate",
    "runtime_release",
    "runtime_unpin",
    "runtime_resolve",
    "memory_copy",
]
TYPE = 7
FRAME = 256
H1, H2, H3, SRC = 0, 24, 48, 72
STATUS = 96  # status slots, 8 bytes apart
RESULT = 160  # the words a case answers, after the statuses
HEAP = world.HEAP - world.BASE
HEAP_SIZE = world.HEAP_SIZE
POOL_FIRST = world.POOL_FIRST
PATTERN = 0x1122334455667788


def status_at(n: int) -> int:
    return STATUS + 8 * n


def hnd(offset: int, width: int = 64) -> list[Word]:
    return [HANDLE, offset, width]


def allocate(
    out: int,
    n: int,
    initial: int = 24,
    capacity: int = 32,
    align: int = 8,
    flags: int = 0,
    kind: int = TYPE,
    zero: int = 0,
) -> Item:
    """``runtime_allocate`` into the handle at ``out``, its status in slot ``n``."""
    operands = [
        lit(kind),
        lit(initial),
        lit(capacity),
        lit(align),
        lit(flags),
        lit(zero),
    ]
    return call("runtime_allocate", operands, (out, 24), status_at(n))


def release(handle: int, n: int) -> Item:
    return call("runtime_release", [slot(handle, 24)], None, status_at(n))


def unpin(handle: int, n: int) -> Item:
    return call("runtime_unpin", [slot(handle, 24)], None, status_at(n))


def answer(first: int = STATUS, last: int = RESULT) -> Item:
    return [OP["RETURN"], *slot(first, last - first), last - first]


def program(items: list[Item]) -> bytes:
    return assemble(FRAME, [], items)


@dataclass(frozen=True)
class Probe:
    name: str
    program: bytes
    pool: int | None = 4
    frontier: int = 0
    heap: bytes = b""
    status: int = 0  # the status of the run


def words_at(out: bytes, at: int, count: int) -> tuple[int, ...]:
    return struct.unpack_from(f"<{count}Q", out, at - STATUS)


def statuses(out: bytes, count: int) -> list[int]:
    return [struct.unpack_from("<I", out, 8 * n)[0] for n in range(count)]


# ---- the programs ---------------------------------------------------------------------------------


def lifecycle(stale: bool) -> bytes:
    """Allocate, write through the handle, read back, release, allocate again on a pool of one row
    (the same row: its generation moves on); the old handle then reads -- or, the program of
    ``stale``, ends on the refusal of that read."""
    items: list[Item] = [
        allocate(H1, 0, initial=24, capacity=32),
        move(SRC, 8, lit(PATTERN)),
        call("memory_copy", [hnd(H1), lit(32), slot(SRC, 16), lit(16), lit(8)]),
        [OP["FIELD"], RESULT, 8, *hnd(H1), 0, 8],
        release(H1, 1),
        allocate(H2, 2, initial=8, capacity=8),
        move(RESULT + 8, 8, slot(H1)),
        move(RESULT + 16, 8, slot(H1 + 8)),
        move(RESULT + 24, 8, slot(H2)),
        move(RESULT + 32, 8, slot(H2 + 8)),
    ]
    if stale:
        items.append([OP["FIELD"], RESULT + 40, 8, *hnd(H1), 0, 8])
    items.append(answer(STATUS, RESULT + 40))
    return program(items)


def exhaustion() -> bytes:
    """Three allocations on a pool of two rows: the third has no row (9)."""
    return program(
        [
            allocate(H1, 0),
            allocate(H2, 1),
            allocate(H3, 2),
            move(RESULT, 8, slot(H1)),
            move(RESULT + 8, 8, slot(H2)),
            move(RESULT + 16, 8, slot(H3)),
            answer(STATUS, RESULT + 24),
        ]
    )


def heap_edge() -> bytes:
    """What the heap has left: 32 do not fit in 16, 16 do (exactly), then 1 does not (9)."""
    return program(
        [
            allocate(H1, 0, initial=0, capacity=32),
            allocate(H2, 1, initial=0, capacity=16),
            allocate(H3, 2, initial=0, capacity=1),
            answer(STATUS, STATUS + 24),
        ]
    )


def pinned() -> bytes:
    """A pinned row is not released (11) until it is unpinned; a released row is unbound (13); the
    one row of the pool is then allocated again."""
    return program(
        [
            allocate(H1, 0, flags=1),
            release(H1, 1),
            unpin(H1, 2),
            release(H1, 3),
            release(H1, 4),
            allocate(H2, 5),
            answer(STATUS, STATUS + 48),
        ]
    )


def refusals() -> bytes:
    """Each leaf refusal on its own: type 0, initial over capacity, align 0, align 3, a flag
    above bit 0, a non-zero pointer parameter (all 6), and a capacity beyond the heap (9)."""
    return program(
        [
            allocate(H1, 0, kind=0),
            allocate(H1, 1, initial=9, capacity=8),
            allocate(H1, 2, align=0),
            allocate(H1, 3, align=3),
            allocate(H1, 4, flags=2),
            allocate(H1, 5, zero=1),
            allocate(H1, 6, initial=0, capacity=HEAP_SIZE + 1),
            allocate(H1, 7),
            answer(STATUS, STATUS + 64),
        ]
    )


def aligned() -> bytes:
    """The payload starts at the aligned frontier: ask where (runtime_resolve) after each."""
    return program(
        [
            allocate(H1, 0, initial=8, capacity=8, align=64),
            call("runtime_resolve", [slot(H1, 24), lit(8)], (RESULT, 8), status_at(1)),
            allocate(H2, 2, initial=8, capacity=8, align=16),
            call(
                "runtime_resolve", [slot(H2, 24), lit(8)], (RESULT + 8, 8), status_at(3)
            ),
            answer(STATUS, RESULT + 16),
        ]
    )


def zeroed() -> bytes:
    """A dirty heap: the payload of capacity 24 reads as zero words and a neighbour stays."""
    return program(
        [
            allocate(H1, 0, initial=24, capacity=24),
            [OP["FIELD"], RESULT, 8, *hnd(H1), 0, 8],
            [OP["FIELD"], RESULT + 8, 8, *hnd(H1), 8, 8],
            [OP["FIELD"], RESULT + 16, 8, *hnd(H1), 16, 8],
            answer(STATUS, RESULT + 24),
        ]
    )


def ordered() -> bytes:
    """The ids are the rows of the pool in order; a released row is found by the scan that wraps."""
    return program(
        [
            allocate(H1, 0),
            allocate(H2, 1),
            allocate(H3, 2),
            release(H2, 3),
            allocate(SRC, 4),
            move(RESULT, 8, slot(H1)),
            move(RESULT + 8, 8, slot(H2)),
            move(RESULT + 16, 8, slot(H3)),
            move(RESULT + 24, 8, slot(SRC)),
            move(RESULT + 32, 8, slot(SRC + 8)),
            answer(STATUS, RESULT + 40),
        ]
    )


def reused() -> bytes:
    """A handle of the row's earlier life is refused 12 -- on the release leaf too."""
    return program(
        [
            allocate(H1, 0),
            release(H1, 1),
            allocate(H2, 2),
            release(H1, 3),
            release(H2, 4),
            answer(STATUS, STATUS + 40),
        ]
    )


def edge_copies() -> bytes:
    """An edge copies the words of an allocated cell through its handle (the loop shape)."""
    return program(
        [
            allocate(H1, 0, initial=8, capacity=8),
            move(SRC, 8, lit(PATTERN)),
            call("memory_copy", [hnd(H1), lit(8), slot(SRC, 16), lit(16), lit(8)]),
            [OP["GOTO"], *edge("done", (hnd(H1, 8), RESULT, 8))],
            "done",
            answer(STATUS, RESULT + 8),
        ]
    )


PROBES = [
    Probe("lifecycle", lifecycle(False), pool=1),
    Probe(
        "lifecycle, the old handle read",
        lifecycle(True),
        pool=1,
        status=STALE_GENERATION,
    ),
    Probe("exhaustion of the pool", exhaustion(), pool=2),
    Probe("exhaustion of the heap", heap_edge(), frontier=HEAP_SIZE - 16),
    Probe("the pin flag", pinned(), pool=1),
    Probe("every refusal of the leaf", refusals(), pool=4),
    Probe("the leaf has no allocator", refusals(), pool=None),
    Probe("alignment", aligned(), frontier=3),
    Probe("zero fill", zeroed(), heap=b"\xaa" * 64, frontier=8),
    Probe("ids are the rows of the pool in order", ordered(), pool=3),
    Probe("a handle of an earlier life", reused(), pool=1),
    Probe("an edge copies through the handle", edge_copies(), pool=1),
]


def case_of(probe: Probe) -> world.Case:
    return world.Case(
        probe.name,
        probe.program,
        b"",
        {},
        pool=probe.pool,
        frontier=probe.frontier,
        heap=probe.heap,
    )


# ---- reading the memory a run left ----------------------------------------------------------------


ROW = (
    "id",
    "generation",
    "type",
    "payload",
    "size",
    "capacity",
    "state",
    "borrows",
    "pins",
    "owner",
)


def row(memory: bytes, identity: int) -> dict[str, int] | None:
    count = struct.unpack_from("<Q", memory, world.CONTEXT - world.BASE + 32)[0]
    for index in range(count):
        at = world.OBJECTS - world.BASE + index * 80
        found: dict[str, int] = dict(
            zip(ROW, struct.unpack_from("<10Q", memory, at), strict=True)
        )
        if found["id"] == identity:
            return found
    return None


def frontier(memory: bytes) -> int:
    return struct.unpack_from("<Q", memory, world.CONTEXT - world.BASE + 88)[0]


def allocator(memory: bytes) -> tuple[int, ...]:
    return struct.unpack_from("<4Q", memory, world.ALLOCATOR - world.BASE)


def heap(memory: bytes, size: int = 64) -> bytes:
    return memory[HEAP : HEAP + size]


@pytest.fixture(scope="module")
def code() -> world.Code:
    return world.seed_code(LEAVES)


@pytest.fixture(scope="module")
def cases() -> list[world.Case]:
    return [case_of(p) for p in PROBES]


@pytest.fixture(scope="module")
def reference(code, cases):
    return {
        p.name: world.referenced(code, c)[0] for p, c in zip(PROBES, cases, strict=True)
    }


def test_every_probe_ends_with_the_status_it_is_for(reference):
    assert {p.name: reference[p.name].status for p in PROBES} == {
        p.name: p.status for p in PROBES
    }


# ---- what the leaf did, from the design -----------------------------------------------------------


def test_allocate_write_read_release_allocate_again(reference):
    out = reference["lifecycle"].out
    assert statuses(out, 3) == [0, 0, 0]
    word, id1, gen1, id2, gen2 = words_at(out, RESULT, 5)
    assert word == PATTERN
    assert (id1, gen1) == (POOL_FIRST, 1)
    assert (id2, gen2) == (
        POOL_FIRST,
        2,
    )  # the same row: its generation moved on, never reset
    memory = reference["lifecycle"].memory
    found = row(memory, POOL_FIRST)
    assert found is not None
    assert (found["generation"], found["type"], found["state"]) == (2, TYPE, LIVE)
    assert (found["size"], found["capacity"], found["pins"], found["borrows"]) == (
        8,
        8,
        0,
        0,
    )
    # no memory came back: the second payload was carved behind the first (32 bytes), not over it
    assert found["payload"] == world.HEAP + 32
    assert frontier(memory) == 32 + 8


def test_the_handle_of_an_earlier_life_is_refused_by_a_read(reference):
    assert reference["lifecycle, the old handle read"].status == STALE_GENERATION


def test_the_payload_is_what_the_program_wrote(reference):
    memory = reference["lifecycle"].memory
    assert (
        struct.unpack_from("<Q", heap(memory), 0)[0] == PATTERN
    )  # the first payload, intact
    assert heap(memory)[8:32] == bytes(24)  # the rest of its capacity is still zero


def test_the_pool_is_exhausted_before_the_heap(reference):
    out = reference["exhaustion of the pool"].out
    assert statuses(out, 3) == [0, 0, OUT_OF_MEMORY]
    ids = words_at(out, RESULT, 3)
    assert (
        ids[:2] == (POOL_FIRST, POOL_FIRST + 1) and ids[2] == 0
    )  # a refusal writes no handle
    memory = reference["exhaustion of the pool"].memory
    assert frontier(memory) == 64  # two payloads of 32; the refused one took nothing


def test_the_heap_is_exhausted_where_its_limit_is(reference):
    out = reference["exhaustion of the heap"].out
    assert statuses(out, 3) == [OUT_OF_MEMORY, 0, OUT_OF_MEMORY]
    assert frontier(reference["exhaustion of the heap"].memory) == HEAP_SIZE
    # the refusals left the pool alone: the one allocation that went took the first row
    assert allocator(reference["exhaustion of the heap"].memory)[2] == POOL_FIRST + 1


def test_a_pinned_row_is_released_only_after_it_is_unpinned(reference):
    out = reference["the pin flag"].out
    assert statuses(out, 6) == [0, PINNED, 0, 0, UNBOUND, 0]
    found = row(reference["the pin flag"].memory, POOL_FIRST)
    # allocated twice (the second time on the row the release returned): generation 2, unpinned
    assert found is not None and found["generation"] == 2 and found["pins"] == 0


def test_every_refusal_of_the_leaf(reference):
    out = reference["every refusal of the leaf"].out
    assert statuses(out, 8) == [
        OUT_OF_RANGE,  # type_id 0
        OUT_OF_RANGE,  # initial_size over capacity
        OUT_OF_RANGE,  # align 0
        OUT_OF_RANGE,  # align not a power of two
        OUT_OF_RANGE,  # a flag above bit 0
        OUT_OF_RANGE,  # the pointer parameter must be 0
        OUT_OF_MEMORY,  # a capacity beyond the heap
        0,  # the last is fine: nothing before it took a row or a byte
    ]
    memory = reference["every refusal of the leaf"].memory
    assert frontier(memory) == 32  # only the last allocation took bytes
    states = [(row(memory, POOL_FIRST + i) or {}).get("state") for i in range(4)]
    assert states == [LIVE, 0, 0, 0]  # and only the first row of the pool


def test_without_an_allocator_capability_the_leaf_answers_unbound(reference):
    """The checks of the arguments come first (6), then the capability (13): a seat that runs no
    dynamic guest binds none, and what would have been a refusal of the heap is UNBOUND."""
    out = reference["the leaf has no allocator"].out
    assert statuses(out, 8) == [OUT_OF_RANGE] * 6 + [UNBOUND, UNBOUND]


def test_the_payload_starts_at_the_aligned_frontier(reference):
    out = reference["alignment"].out
    assert statuses(out, 4) == [0, 0, 0, 0]
    first, second = words_at(out, RESULT, 2)
    assert first == world.HEAP + 64  # frontier 3 -> 64
    assert second == world.HEAP + 80  # 64 + 8 = 72 -> 80
    assert frontier(reference["alignment"].memory) == 88


def test_the_payload_is_zero_for_its_whole_capacity_and_no_further(reference):
    out = reference["zero fill"].out
    assert statuses(out, 1) == [0]
    assert words_at(out, RESULT, 3) == (0, 0, 0)
    memory = reference["zero fill"].memory
    assert heap(memory)[:8] == b"\xaa" * 8  # below the frontier: not ours
    assert heap(memory)[8:32] == bytes(24)  # the payload: zero for its capacity
    assert heap(memory)[32:40] == b"\xaa" * 8  # behind it: untouched


def test_ids_are_the_rows_of_the_pool_in_order_and_the_scan_wraps(reference):
    out = reference["ids are the rows of the pool in order"].out
    assert statuses(out, 5) == [0, 0, 0, 0, 0]
    ids = words_at(out, RESULT, 5)
    assert ids[:3] == (POOL_FIRST, POOL_FIRST + 1, POOL_FIRST + 2)
    # the pool ends at POOL_FIRST + 3: the scan wraps and takes the released middle row, one
    # generation up
    assert ids[3:] == (POOL_FIRST + 1, 2)


def test_a_handle_of_an_earlier_life_is_refused_by_release_too(reference):
    out = reference["a handle of an earlier life"].out
    assert statuses(out, 5) == [0, 0, 0, STALE_GENERATION, 0]


def test_an_edge_copies_through_the_handle(reference):
    out = reference["an edge copies through the handle"].out
    assert words_at(out, RESULT, 1) == (PATTERN,)


# ---- the machines ---------------------------------------------------------------------------------


def different(cases, want, have):
    return [
        (case.name, w.status, h.status, w.out == h.out, w.memory == h.memory)
        for case, w, h in zip(cases, want, have, strict=True)
        if w != h
    ]


def test_x86_64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("x86_64", code, c) for c in cases]
    assert different(cases, [reference[c.name] for c in cases], have) == []


def test_aarch64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("aarch64", code, c) for c in cases]
    assert different(cases, [reference[c.name] for c in cases], have) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wasm32_runs_what_the_reference_runs(code, cases, reference):
    have = world.in_node(code, cases)
    assert different(cases, [reference[c.name] for c in cases], have) == []


# ---- the host seat --------------------------------------------------------------------------------


def host(items: list[Item], pool: int | None, frontier: int = 0) -> tuple[int, int]:
    with rxf_native.Memory(1 << 16) as memory:
        return rxf_native.run(
            program(items),
            b"",
            memory,
            heap=(0x4000, HEAP_SIZE, frontier),
            pool=None if pool is None else (POOL_FIRST, pool),
        )


def test_the_host_seat_allocates_writes_reads_and_releases():
    status, value = host(
        [
            allocate(H1, 0, initial=24, capacity=32),
            move(SRC, 8, lit(PATTERN)),
            call("memory_copy", [hnd(H1), lit(32), slot(SRC, 16), lit(16), lit(8)]),
            [OP["FIELD"], RESULT, 8, *hnd(H1), 0, 8],
            release(H1, 1),
            [OP["RETURN"], *slot(RESULT), 8],
        ],
        pool=1,
    )
    assert (status, value) == (0, PATTERN)


def test_the_host_seat_refuses_a_reused_handle_and_an_empty_pool():
    status, _ = host(
        [
            allocate(H1, 0),
            release(H1, 1),
            allocate(H2, 2),
            [OP["FIELD"], RESULT, 8, *hnd(H1), 0, 8],
        ],
        pool=1,
    )
    assert status == STALE_GENERATION
    status, value = host(
        [allocate(H1, 0), allocate(H2, 1), [OP["RETURN"], *slot(status_at(1), 4), 4]],
        pool=1,
    )
    assert (status, value) == (0, OUT_OF_MEMORY)
    status, value = host(
        [allocate(H1, 0), [OP["RETURN"], *slot(status_at(0), 4), 4]], pool=None
    )
    assert (status, value) == (0, UNBOUND)


def test_the_host_seat_zero_fills_the_payload_it_carves():
    with rxf_native.Memory(1 << 16) as memory:
        memory.write(0x4000, b"\xaa" * 64)
        status, value = rxf_native.run(
            program(
                [
                    allocate(H1, 0, initial=16, capacity=16),
                    [OP["FIELD"], RESULT, 8, *hnd(H1), 8, 8],
                    [OP["RETURN"], *slot(RESULT), 8],
                ]
            ),
            b"",
            memory,
            heap=(0x4000, HEAP_SIZE, 0),
            pool=(POOL_FIRST, 1),
        )
        assert (status, value) == (0, 0)
        assert memory.read(0x4000, 16) == bytes(16)
        assert memory.read(0x4010, 8) == b"\xaa" * 8


# ---- what must not move ---------------------------------------------------------------------------


@pytest.mark.parametrize("machine", ["x86_64", "aarch64", "wasm32"])
def test_the_transactional_writers_are_byte_identical_to_the_starters(machine):
    """``allocate_prepare``, ``publish``, ``rollback`` and the rest of the writers stay the
    transactional API of the live engine: the bytes the Seed builds are the bytes the starter
    stores (the pin of a byte-identical lane), and ``runtime_release`` -- now leaf 51 -- is the
    same function it was."""
    stored = scenario.stdlib_code()
    for key in (
        "runtime_allocate_prepare",
        "runtime_publish",
        "runtime_rollback",
        "runtime_begin_private",
        "runtime_cleanup",
        "runtime_release",
        "runtime_pin",
        "runtime_unpin",
    ):
        assert bytes.fromhex(seed_build.code(machine, key)) == stored[key][machine], key


# ---- the seats: the linker binds the allocator where a seat asks for one ----------------------------


def context_words(linked: link.Linked, at: int, count: int) -> tuple[int, ...]:
    return struct.unpack_from(f"<{count}Q", linked.data, at)


def capabilities(linked: link.Linked) -> dict[int, tuple[int, int]]:
    """The capability table of a linked context: id -> (generation, address)."""
    count, table = context_words(linked, 48, 2)
    rows = struct.unpack_from(f"<{3 * count}Q", linked.data, table - linked.base)
    return {rows[3 * i]: (rows[3 * i + 1], rows[3 * i + 2]) for i in range(count)}


def entries(linked: link.Linked) -> list[tuple[int, ...]]:
    count, table = context_words(linked, 32, 2)
    return [
        struct.unpack_from("<10Q", linked.data, table - linked.base + 80 * i)
        for i in range(count)
    ]


@pytest.mark.slow
@pytest.mark.parametrize("machine", ["x86_64", "aarch64", "wasm32"])
def test_a_seat_that_asks_for_a_pool_gets_the_capability_and_the_free_rows(machine):
    """``LiveSpec.pool`` reserves that many free rows of the sorted entry table and binds the
    Allocator capability to a cell naming them; a seat that asks for none binds none (the leaf then
    answers UNBOUND, which is what a seat that runs no dynamic guest wants), and its image is the one
    it was."""
    image = tree.load(STARTER)
    plain = link.link(image, machine, 0x100000, link.LiveSpec())
    pooled = link.link(image, machine, 0x100000, link.LiveSpec(pool=4))
    assert graph_header.numbers()["RXF_CAPABILITY_ALLOCATOR"] not in capabilities(plain)
    bound = capabilities(pooled)
    assert set(bound) - set(capabilities(plain)) == {
        graph_header.numbers()["RXF_CAPABILITY_ALLOCATOR"]
    }
    generation, address = bound[graph_header.numbers()["RXF_CAPABILITY_ALLOCATOR"]]
    assert generation == 1
    cell = struct.unpack_from("<4Q", pooled.data, address - pooled.base)
    assert cell == (link.POOL_FIRST, 4, link.POOL_FIRST, 0)
    rows = entries(pooled)
    assert [r[0] for r in rows] == sorted(
        r[0] for r in rows
    )  # the table stays sorted by id
    assert len(rows) == len(entries(plain)) + 4
    free = [r for r in rows if link.POOL_FIRST <= r[0] < link.POOL_FIRST + 4]
    assert len(free) == 4 and all(r[1:] == (0,) * 9 for r in free)
    assert {r[0] for r in rows} - {r[0] for r in free} == {r[0] for r in entries(plain)}
