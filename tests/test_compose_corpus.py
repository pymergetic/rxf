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
from typing import NamedTuple

import arena_export
import compose
import pytest
import rxfc

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import ObjectArena

from . import rxf_native
from .snippets import STARTER
from .test_compose import with_object_handle

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


class Arena(NamedTuple):
    """The v7 arena of a model (docs/32): its bytes, and where each object of the model lies in it
    (model handle -> arena handle, the handle the composer is asked about and answers in)."""

    data: bytes
    handles: dict[int, int]


def arena_of(model) -> Arena:
    """The canonical arena the linker places for a model (``arena_export.place_world``)."""
    made, placed = arena_export.place_world(model)
    return Arena(made.to_bytes(), dict(placed))


def cell_of_arena(arena: Arena, handle: int) -> dict:
    """The world cell of a handle as the arena holds it (docs/32 5.3): its record, its row of
    sys/specs and the rows of its relation table -- what the in-image reader walks the arena to."""
    book = ObjectArena.attach(arena.data)
    placed = arena.handles[handle]
    _kind, _gen, _flags, name, parent, _link, _refs, payload_size = book.load(placed)
    type_h, flags, _index, _generation = book.spec(placed)
    rows = book.refs_of(placed)
    return {
        "type": type_h,
        "parent": parent,
        "name_off": name,
        "payload_size": payload_size,
        "flags_word": flags | (len(rows) << 32),
        "ref_count": len(rows),
        "refs": [(target, role) for target, role, _kind2, _binding, _index2 in rows],
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
def arena(starter) -> Arena:
    return arena_of(starter)


def run_compose(
    program: bytes, arena: Arena, target: int, kind: int = 0
) -> tuple[int, list[int]]:
    """Run the in-image composer on an object of the model, named by its arena handle:
    (status, the words it wrote)."""
    memory = rxf_native.Memory(OUT_AT + OUT_CAPACITY)
    memory.write(ARENA_AT, arena.data)
    memory.write(
        REQUEST_AT,
        make_request(memory.at, len(arena.data), arena.handles[target], kind),
    )
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
        assert decoded[0] == arena.handles[c.target(fields["entry_block"])], starter.path(target)
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
        assert decoded[0] == arena.handles[c.target(c.fields(target)["entry_block"])]


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
        assert arena.handles[handle] in placed, starter.path(handle)
        assert placed[arena.handles[handle]] == (value.slot, value.width), starter.path(handle)

    # the blocks in order
    nblocks = words[2 + 3 * nvalues]
    order = words[3 + 3 * nvalues : 3 + 3 * nvalues + nblocks]
    assert order == [arena.handles[block] for block in plan.blocks]


@pytest.mark.slow
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


@pytest.mark.slow
@pytest.mark.parametrize("variant", ["pointer", "handle", "field"])
def test_the_in_image_plan_writes_the_marked_inputs_word_for_word(
    program, starter, tmp_path, variant
):
    """An input whose InputType is ObjectHandle is the HANDLE operand of the program where its
    callee takes a pointer or a word (and the source of a FIELD), and a slot where the callee takes a
    handle by value (docs/23 4.9): the composer as a program writes those words, byte for byte, as
    tools/native/compose.py does -- on every graph the marker changes, and the ``handle`` variant
    changes none (the same words as the starter's)."""
    patched = tree.load(
        with_object_handle(
            tmp_path,
            pointer=variant == "pointer",
            handle=variant == "handle",
            field=variant == "field",
        )
    )
    before = compose.programs(starter)
    after = compose.programs(patched)
    changed = {name for name in after if after[name] != before[name]}
    assert bool(changed) == (variant != "handle"), (variant, sorted(changed))
    arena = arena_of(patched)
    c = compose.Composer(patched)
    graphs = [
        h
        for h in graph_handles(patched)
        if c.fields(h).get("function") in changed
        or (variant == "handle" and patched.path(h).endswith("heap_resize_graph"))
    ]
    assert graphs
    for target in graphs:
        status, words = run_compose(program, arena, target, kind=4)
        assert status == 0, f"refused with status {status} on {patched.path(target)}"
        expected = list(compose.describe(compose.Plan(c, target).encode()))
        assert words == expected, patched.path(target)


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


@pytest.mark.slow
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

