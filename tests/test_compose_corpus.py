"""The in-image composer reads the graphs the host composer reads (docs/19).

``native/compose/compose.rxfs`` is the composer as a program of the interpreter: it takes a Graph of
the live image and reads out the ground the host composer (``tools/native/compose.py``) reads for
the same graph. Every graph of the starter is read twice -- by ``compose.py`` over its Image model,
and by the composer as a program run on the interpreter of ``native/graph.c`` (``rxf_native``) over
the raw arena -- and the two agree on every field and every reference.

The final words of a program are what ``tools/native/compose.py`` writes; that byte exactness is
held by ``tests/test_compose.py`` (the programs stored in the starter are what its graphs compose
to). What is proved here is that the in-image reader decodes the arena to the same ground the host
composer works from, which is the foundation every word of the program is written from.
"""

from __future__ import annotations

import functools
import hashlib
import struct
from pathlib import Path

import compose
import link
import pytest
import rxfc

from pymergetic.rxftk.core import tree

from . import rxf_native
from .snippets import STARTER

COMPOSE = Path(__file__).parents[1] / "native" / "compose" / "compose.rxfs"
REQUEST_AT = 0x10000
ARENA_AT = 0x20000
WORK_AT = 0x8000000
OUT_AT = 0x9000000
OUT_CAPACITY = 0x10000
WORK_CAPACITY = 0x90000
# The step budget of one composer run. The plan of a big graph walks the arena's handles for every
# block, so the heaviest (build_receipt, the heap graphs) take hundreds of millions of steps; the
# budget is high enough that each ends and writes its words rather than being cut off mid-plan.
LIMIT = 800_000_000


def cell_of_arena(arena: bytes, handle: int) -> dict:
    """The raw cell of a handle: what the in-image reader walks the arena to."""
    cells = link.arena_cells(arena)
    off = cells[handle]
    type_h, parent, payload_size, _gen, name_off, flags, ref_count, _fwd, _idx, _rsv = (
        struct.unpack_from("<QQQQQIIQII", arena, off)
    )
    refs_off = off + 64 + ((payload_size + 15) & ~15)
    refs = [
        struct.unpack_from("<QQ", arena, refs_off + 24 * i) for i in range(ref_count)
    ]
    return {
        "type": type_h,
        "parent": parent,
        "name_off": name_off,
        "payload_size": payload_size,
        "flags_word": flags | (ref_count << 32),
        "ref_count": ref_count,
        "refs": refs,
    }


def make_request(at, arena_size: int, target: int, kind: int = 0) -> bytes:
    return struct.pack(
        "<12Q",
        at(ARENA_AT),
        arena_size,  # R_ARENA, R_ARENA_SIZE
        target,
        kind,  # R_TARGET, R_TARGET_KIND
        at(OUT_AT),
        OUT_CAPACITY,  # R_OUT, R_OUT_CAPACITY
        at(WORK_AT),
        WORK_CAPACITY,  # R_WORK, R_WORK_CAPACITY
        0,
        0,  # R_OUT_SIZE, R_AT (written by the run)
        0,
        0,
    )


@pytest.fixture(scope="module")
def program() -> bytes:
    return rxfc.compile_file(COMPOSE)


@pytest.fixture(scope="module")
def starter():
    return tree.load(Path(STARTER))


@pytest.fixture(scope="module")
def arena(starter) -> bytes:
    return starter.to_bytes()


def run_compose(
    program: bytes, arena: bytes, target: int, kind: int = 0
) -> tuple[int, list[int]]:
    """Run the in-image composer on a handle of the arena: (status, the words it wrote)."""
    memory = rxf_native.Memory(OUT_AT + OUT_CAPACITY)
    memory.write(ARENA_AT, arena)
    memory.write(REQUEST_AT, make_request(memory.at, len(arena), target, kind))
    status, _ = rxf_native.run(
        program, struct.pack("<2Q", 0, memory.at(REQUEST_AT)), memory, limit=LIMIT
    )
    size = memory.u64(REQUEST_AT + 64)
    words = [memory.u64(OUT_AT + 8 * i) for i in range(size // 8)]
    memory.close()
    return status, words


def graph_handles(image) -> list[int]:
    return [
        h
        for h in range(image.handle_count)
        if image.live(h) and image.type_name(h) == "SemanticGraph"
    ]


@functools.lru_cache(maxsize=1)
def graph_paths() -> tuple[str, ...]:
    """The path of every SemanticGraph of the starter, sorted -- the corpus the plan is proved on."""
    image = tree.load(Path(STARTER))
    return tuple(sorted(image.path(h) for h in graph_handles(image)))


def test_the_in_image_reader_reads_the_ground_the_host_composer_reads(program, starter, arena):
    """For every SemanticGraph of the starter the composer as a program writes the descriptor and
    the fields of the graph that ``compose.py`` reads: its references, its entry block, its block
    count."""
    for target in graph_handles(starter):
        expected = cell_of_arena(arena, target)
        status, words = run_compose(program, arena, target)
        assert status == 0, f"refused with status {status} on {starter.path(target)}"

        # the raw descriptor: the cell header and its references
        descriptor = words[: 6 + 2 * expected["ref_count"]]
        assert descriptor[:6] == [
            expected["type"],
            expected["parent"],
            expected["name_off"],
            expected["payload_size"],
            expected["flags_word"],
            expected["ref_count"],
        ], starter.path(target)
        refs = [
            (descriptor[6 + 2 * i], descriptor[6 + 2 * i + 1])
            for i in range(expected["ref_count"])
        ]
        assert refs == expected["refs"], starter.path(target)

        # the decoded fields, against the host composer's own read of the same graph
        decoded = words[6 + 2 * expected["ref_count"] :]
        c = compose.Composer(starter)
        fields = c.fields(target)
        assert decoded[0] == c.target(fields["entry_block"]), starter.path(target)
        assert decoded[1] == int(fields["block_count"]), starter.path(target)


def test_the_in_image_reader_agrees_with_the_host_composer_on_the_entry_blocks(
    program, starter, arena
):
    """The entry block the composer resolves is the one the host composer resolves, on every
    graph of the starter -- the value the program begins at."""
    c = compose.Composer(starter)
    for target in graph_handles(starter):
        status, words = run_compose(program, arena, target)
        assert status == 0
        ref_count = cell_of_arena(arena, target)["ref_count"]
        decoded = words[6 + 2 * ref_count :]
        assert decoded[0] == c.target(c.fields(target)["entry_block"])


def test_the_object_id_is_the_sha256_of_the_path(program, starter, arena):
    """The id of a Bound value is the SHA-256 of the path of the object it stands for -- the same
    ``object_id`` tools/native/compose.py computes. Proved here on the six data objects of
    checkout_total (the only values whose id is a hash of a path; every other value carries its id
    in the arena). The in-image hash writes the id compose.py reads, byte for byte."""
    for name in ("discount", "hundred", "quantity", "shipping", "tax_percent", "unit_price"):
        handle = next(
            h
            for h in range(starter.handle_count)
            if starter.live(h) and starter.path(h).endswith("." + name)
        )
        path = starter.path(handle)
        expected = compose.object_id(starter, handle)
        # this is the SHA-256 of the path read little-endian, exactly what the in-image hash does
        assert expected == int.from_bytes(
            hashlib.sha256(path.encode()).digest()[:8], "little"
        ), path
        status, words = run_compose(program, arena, handle, kind=2)
        assert status == 0, f"refused with status {status} on {path}"
        assert words[0] == expected, path


def test_the_in_image_plan_places_the_values_and_orders_the_blocks(program, starter, arena):
    """The in-image plan of clamp_graph gives every frame value the slot ``compose.py``'s Plan gives
    it and orders the blocks the same way -- the ground the program words are laid on. This is the
    first increment of the Plan (place_values and ordered_blocks); the words come next."""
    target = next(
        h
        for h in range(starter.handle_count)
        if starter.live(h)
        and starter.path(h) == "pymergetic.rxf.algorithms.clamp.clamp_graph"
    )
    status, words = run_compose(program, arena, target, kind=3)
    assert status == 0, f"refused with status {status}"
    c = compose.Composer(starter)
    plan = compose.Plan(c, target)

    # the frame, and the slot and width of every value it places
    assert words[0] == plan.frame
    nvalues = words[1]
    assert nvalues == len(plan.values)
    placed = {}
    for i in range(nvalues):
        handle, slot, width = words[2 + 3 * i : 5 + 3 * i]
        placed[handle] = (slot, width)
    for handle, value in plan.values.items():
        assert handle in placed, starter.path(handle)
        assert placed[handle] == (value.slot, value.width), starter.path(handle)

    # the blocks in order
    nblocks = words[2 + 3 * nvalues]
    order = words[3 + 3 * nvalues : 3 + 3 * nvalues + nblocks]
    assert order == list(plan.blocks)


@pytest.mark.parametrize("target_path", graph_paths())
def test_the_in_image_plan_writes_the_program_word_for_word(program, starter, arena, target_path):
    """The in-image composer writes the words of a graph's program byte for byte what
    tools/native/compose.py writes: the header with its parameter table, every operation with its
    operands, and the terminators with their edge copies. Every graph of the corpus is held to
    compose.py's output, word for word -- including build_receipt_graph, whose values all hang off
    the graph and whose owned parameters gave the plan's scan its own room."""
    target = next(
        h
        for h in range(starter.handle_count)
        if starter.live(h) and starter.path(h) == target_path
    )
    status, words = run_compose(program, arena, target, kind=4)
    assert status == 0, f"refused with status {status}"
    c = compose.Composer(starter)
    plan = compose.Plan(c, target)
    expected = list(compose.describe(plan.encode()))
    assert len(words) == len(expected), f"{len(words)} words vs {len(expected)}"
    for i, (got, want) in enumerate(zip(words, expected)):
        assert got == want, f"word {i}: got {got}, want {want}"


def expression_handles(image) -> list[int]:
    """The Functions whose body is one Call -- the Expression programs of the corpus. A function
    that has a graph is skipped: its body is a call to itself, for readers that have no graphs."""
    c = compose.Composer(image)
    graphed = {
        c.fields(h).get("function") for h in graph_handles(image)
    }
    found = []
    for h in range(image.handle_count):
        if not image.live(h) or image.type_name(h) != "Function":
            continue
        values = c.fields(h)
        if compose.word(values.get("implementation")) != "Semantic" or not values.get("body"):
            continue
        if image.path(h) in graphed:
            continue
        if image.type_name(c.target(values["body"])) != "Call":
            continue
        found.append(h)
    return found


@functools.lru_cache(maxsize=1)
def expression_paths() -> tuple[str, ...]:
    """The path of every Function whose program is an Expression, sorted -- the corpus the call
    emitter is proved on."""
    image = tree.load(Path(STARTER))
    return tuple(sorted(image.path(h) for h in expression_handles(image)))


@pytest.mark.parametrize("target_path", expression_paths())
def test_the_in_image_expression_writes_the_program_word_for_word(
    program, starter, arena, target_path
):
    """The in-image composer writes the words of a Function's call body byte for byte what
    tools/native/compose.py's Expression writes: the parameter table and the status slot, the moves
    of every Bound object, each call with its operands and its status-check branch, the return and
    the refusal trailer. Every Expression of the corpus is held to compose.py's output, word for
    word -- the Semantic callee of ``main`` inlined, and the common subexpressions of
    ``checkout_total`` written once and taken from the memo after."""
    target = next(
        h
        for h in range(starter.handle_count)
        if starter.live(h) and starter.path(h) == target_path
    )
    status, words = run_compose(program, arena, target, kind=1)
    assert status == 0, f"refused with status {status} on {target_path}"
    c = compose.Composer(starter)
    expected = list(compose.describe(compose.Expression(c, target).program()))
    assert len(words) == len(expected), f"{len(words)} words vs {len(expected)}"
    for i, (got, want) in enumerate(zip(words, expected)):
        assert got == want, f"word {i}: got {got}, want {want}"

