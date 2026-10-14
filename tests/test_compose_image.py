"""The composer in the image (docs/19): a Graph of the arena in, the ground of a program out.

``native/compose/compose.rxf`` is the composer as a program: it walks the arena of the live image,
finds the Graph the request names, and writes the ground the host composer (``tools/native/
compose.py``) reads for that graph. Here the image does it and the arena it reads is its own: the
composer is installed as a Function of the image by one delta, and the words it writes are read
back and held to the same ground the host composer works from -- on x86_64, aarch64 and wasm32,
no restart.

What the harness writes is the call block (the request of ``native/compose/words.rxf`` and its
arguments) behind the lookup table of the live block; the composer reads the arena where the image
keeps it. This is the reader and the field decoder of the composer -- the foundation the words of
a program are written from. ``tests/test_compose_corpus.py`` holds it to the host composer on every
graph of the starter; here it runs in the image on every machine.

The three machines are three params of every test, and every test builds its own machine (a fresh
emulator or its own node process), so the params run in parallel:

    pytest tests/test_compose_image.py -n auto     # pytest-xdist; -n 3 is one worker per machine
"""

from __future__ import annotations

import struct
from pathlib import Path

import compose
import link
import pytest
import rxfc

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.object import Image

from . import live_world as lw
from .snippets import STARTER
from .test_front_image import (
    FRONT_FUNCTION,
    FRONT_XML,
    SCRATCH,
    authored,
    blob,
    identity,
    send,
    value_at,
)
from .test_rxfc import MACHINES

COMPOSE = Path(__file__).parents[1] / "native" / "compose" / "compose.rxf"
SELFEDIT_OUT = "pymergetic.rxf.selfedit.out"
OUT_ROOM = 0x10000
REQUEST_AT_SHIFT = 0x100  # the request lives in the work region behind the lookup table
WORK_AT_SHIFT = 0x100000


def cell_of_arena(arena: bytes, handle: int) -> dict:
    """The raw cell of a handle: the ground the in-image reader walks the arena to."""
    cells = link.arena_cells(arena)
    off = cells[handle]
    type_h, parent, payload_size, _gen, name_off, flags, ref_count, _f, _i, _r = (
        struct.unpack_from("<QQQQQIIQII", arena, off)
    )
    refs_off = off + 64 + ((payload_size + 15) & ~15)
    refs = [
        struct.unpack_from("<QQ", arena, refs_off + 24 * i) for i in range(ref_count)
    ]
    return dict(
        type=type_h,
        parent=parent,
        name_off=name_off,
        payload_size=payload_size,
        flags_word=flags | (ref_count << 32),
        ref_count=ref_count,
        refs=refs,
    )


def graph_handles(image: Image) -> list[int]:
    return [
        h
        for h in range(image.handle_count)
        if image.live(h) and image.type_name(h) == "SemanticGraph"
    ]


# The byte-exact in-image gate is one carrier per opcode class the emitters write. The host corpus
# (``test_compose_corpus``) holds every graph of the corpus to compose.py's words, and
# ``test_the_image_composes_its_graphs_as_the_host_composer_does`` walks all of them in-image; these
# carriers hold the byte-exact program of each opcode class to compose.py on every machine. A full
# byte-exact in-image walk of the corpus is a slow emulator proof (superlinear in the arena), so the
# class carriers are the per-machine byte-exact gate.
IMAGE_CARRIERS = (
    "pymergetic.rxf.algorithms.clamp.clamp_graph",  # CALL, BRANCH, MOVE, RETURN, REFUSE
    "pymergetic.rxf.algorithms.binary_search.binary_search_graph",  # CONSTRUCT
    "pymergetic.rxf.stdlib.flow.catch_refusal.catch_refusal_graph",  # MAP
    "pymergetic.rxf.stdlib.collections.array_get.array_get_graph",  # SWITCH
)

# The byte-exact in-image gate of the Expression face is one carrier per class of call emission.
# The host corpus (``test_compose_corpus``) holds every Expression of the corpus to compose.py's
# words; these carriers hold each class of emission to compose.py on every machine: a leaf whose
# result is the status, a leaf that writes an out slot, the Bound objects moved into slots with the
# subexpressions written once, and a Semantic callee inlined.
EXPRESSION_CARRIERS = (
    "pymergetic.rxf.algorithms.compare",  # a leaf call whose result is the status
    "pymergetic.rxf.algorithms.fill",  # a leaf call that writes an out slot
    "pymergetic.rxf.library.checkout_total",  # Bound objects moved in, results taken from the memo
    "pymergetic.rxf.application.main",  # a Semantic callee inlined
)


def resolve_graph(image: Image, path: str) -> int:
    for h in graph_handles(image):
        if image.path(h) == path:
            return h
    raise AssertionError(f"no SemanticGraph {path} in the image")


def resolve_function(image: Image, path: str) -> int:
    """The Function whose call body is one Expression -- the target of an Expression request."""
    for h in range(image.handle_count):
        if image.live(h) and image.type_name(h) == "Function" and image.path(h) == path:
            return h
    raise AssertionError(f"no Function {path} in the image")


@pytest.fixture(scope="module")
def image() -> Image:
    return tree.load(Path(STARTER))


@pytest.fixture(scope="module")
def base(image: Image) -> bytes:
    return image.to_bytes()


@pytest.fixture(params=MACHINES)
def running(request: pytest.FixtureRequest, image: Image):
    spec = link.LiveSpec(headroom=8 << 20, log=8 << 20, stage=8 << 20, scratch=SCRATCH)
    machine = lw.machine_of(request.param, image, spec)
    try:
        yield request.param, lw.Channel(machine)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_the_image_composes_its_graphs_as_the_host_composer_does(running, base: bytes):
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    # the composer becomes the image in one delta: the words of native/compose/compose.rxf, and an
    # object for what it writes. The host makes the words with rxfc.py; the composer is a program
    # of the interpreter, so it runs wherever the interpreter does.
    words = rxfc.compile_file(COMPOSE)
    compose_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        words,
        COMPOSE.read_bytes(),
        (blob("out", bytes(OUT_ROOM)),),
    )
    assert send(channel, expected, compose_text) > 0

    # the request points into the arena the image keeps (where its own bytes are) and into the work
    # memory of the request behind the lookup table.
    arena_at, _ = live.regions["arena"]
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at = free + REQUEST_AT_SHIFT
    work_at = free + WORK_AT_SHIFT
    out_at = value_at(channel, SELFEDIT_OUT)

    arena = channel.arena()
    live_image = Image.from_bytes(arena)
    targets = graph_handles(live_image)
    reference = {target: cell_of_arena(arena, target) for target in targets}
    c = compose.Composer(live_image)

    for target in targets:
        machine.write(
            request_at,
            struct.pack(
                "<12Q",
                arena_at,
                len(arena),  # R_ARENA, R_ARENA_SIZE
                target,
                0,  # R_TARGET, R_TARGET_KIND: a SemanticGraph
                out_at,
                OUT_ROOM,  # R_OUT, R_OUT_CAPACITY
                work_at,
                SCRATCH - WORK_AT_SHIFT,  # R_WORK, R_WORK_CAPACITY
                0,
                0,  # R_OUT_SIZE, R_AT
                0,
                0,
            ),
        )
        status, _ = machine.run(
            identity(expected, f"{FRONT_FUNCTION}.bytecode"),
            struct.pack("<2Q", 0, request_at),
        )
        assert status == 0, f"{name} refused with status {status} on {target}"
        (size,) = struct.unpack("<Q", machine.read(request_at + 64, 8))
        words_out = struct.unpack(f"<{size // 8}Q", machine.read(out_at, size))

        exp = reference[target]
        got = words_out[: 6 + 2 * exp["ref_count"]]
        assert got[:6] == (
            exp["type"],
            exp["parent"],
            exp["name_off"],
            exp["payload_size"],
            exp["flags_word"],
            exp["ref_count"],
        ), (name, target)
        refs = [(got[6 + 2 * i], got[6 + 2 * i + 1]) for i in range(exp["ref_count"])]
        assert refs == exp["refs"], (name, target)

        decoded = words_out[6 + 2 * exp["ref_count"] :]
        fields = c.fields(target)
        assert decoded[0] == c.target(fields["entry_block"]), (name, target)
        assert decoded[1] == int(fields["block_count"]), (name, target)


BOUND_DATA = ("discount", "hundred", "quantity", "shipping", "tax_percent", "unit_price")


def bound_targets(image: Image) -> list[int]:
    """The six data objects of checkout_total: the only values whose id is a hash of a path."""
    return [
        h
        for name in BOUND_DATA
        for h in range(image.handle_count)
        if image.live(h) and image.path(h).endswith("." + name)
    ]


def test_the_image_object_id_is_the_sha256_of_the_path(running, base: bytes):
    """The id of a Bound value is the SHA-256 of the path of the object it stands for, computed in
    the image by native/compose/hash.rxf -- the same ``object_id`` tools/native/compose.py reads,
    on x86_64, aarch64 and wasm32. The request asks for the id of a handle (kind 2) and the answer
    is one word."""
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    words = rxfc.compile_file(COMPOSE)
    compose_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        words,
        COMPOSE.read_bytes(),
        (blob("out", bytes(OUT_ROOM)),),
    )
    assert send(channel, expected, compose_text) > 0

    arena_at, _ = live.regions["arena"]
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at = free + REQUEST_AT_SHIFT
    work_at = free + WORK_AT_SHIFT
    out_at = value_at(channel, SELFEDIT_OUT)

    arena = channel.arena()
    live_image = Image.from_bytes(arena)
    for handle in bound_targets(live_image):
        machine.write(
            request_at,
            struct.pack(
                "<12Q",
                arena_at,
                len(arena),  # R_ARENA, R_ARENA_SIZE
                handle,
                2,  # R_TARGET, R_TARGET_KIND: the id of this handle
                out_at,
                OUT_ROOM,  # R_OUT, R_OUT_CAPACITY
                work_at,
                SCRATCH - WORK_AT_SHIFT,  # R_WORK, R_WORK_CAPACITY
                0,
                0,  # R_OUT_SIZE, R_AT
                0,
                0,
            ),
        )
        status, _ = machine.run(
            identity(expected, f"{FRONT_FUNCTION}.bytecode"),
            struct.pack("<2Q", 0, request_at),
        )
        assert status == 0, f"{name} refused with status {status} on {handle}"
        (oid,) = struct.unpack("<Q", machine.read(out_at, 8))
        assert oid == compose.object_id(live_image, handle), (name, live_image.path(handle))


def test_the_image_writes_the_program_of_clamp_word_for_word(running, base: bytes):
    """The composer writes the words of a graph's program byte for byte what tools/native/compose.py
    writes -- the header with its parameter table, the call, the branch with its edge copies, the
    move and the returns -- on x86_64, aarch64 and wasm32. One carrier per opcode class is held to
    compose.py's output word for word (the host corpus holds every graph to it, and
    ``test_the_image_composes_its_graphs_as_the_host_composer_does`` walks every graph in-image).
    The request asks for the program of a graph (kind 4) and the answer is its words."""
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    words = rxfc.compile_file(COMPOSE)
    compose_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        words,
        COMPOSE.read_bytes(),
        (blob("out", bytes(OUT_ROOM)),),
    )
    assert send(channel, expected, compose_text) > 0

    arena_at, _ = live.regions["arena"]
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at = free + REQUEST_AT_SHIFT
    work_at = free + WORK_AT_SHIFT
    out_at = value_at(channel, SELFEDIT_OUT)

    arena = channel.arena()
    live_image = Image.from_bytes(arena)
    c = compose.Composer(live_image)
    for path in IMAGE_CARRIERS:
        target = resolve_graph(live_image, path)
        print(f"  [image] composing {path}", flush=True)
        machine.write(
            request_at,
            struct.pack(
                "<12Q",
                arena_at,
                len(arena),  # R_ARENA, R_ARENA_SIZE
                target,
                4,  # R_TARGET, R_TARGET_KIND: the program of a SemanticGraph
                out_at,
                OUT_ROOM,  # R_OUT, R_OUT_CAPACITY
                work_at,
                SCRATCH - WORK_AT_SHIFT,  # R_WORK, R_WORK_CAPACITY
                0,
                0,  # R_OUT_SIZE, R_AT
                0,
                0,
            ),
        )
        status, _ = machine.run(
            identity(expected, f"{FRONT_FUNCTION}.bytecode"),
            struct.pack("<2Q", 0, request_at),
        )
        assert status == 0, f"{name} refused with status {status} on {path}"
        (size,) = struct.unpack("<Q", machine.read(request_at + 64, 8))
        got = list(struct.unpack(f"<{size // 8}Q", machine.read(out_at, size)))
        plan = compose.Plan(c, target)
        want = list(compose.describe(plan.encode()))
        assert len(got) == len(want), (name, path, len(got), len(want))
        for i, (g, w) in enumerate(zip(got, want)):
            assert g == w, (name, path, i, g, w)


def test_the_image_writes_the_expression_of_a_function_word_for_word(running, base: bytes):
    """The composer writes the words of a Function's call body byte for byte what
    tools/native/compose.py's Expression writes -- the parameter table and the status slot, the
    moves of every Bound object, each call with its operands and its status-check branch, the
    return and the refusal trailer -- on x86_64, aarch64 and wasm32. One carrier per class of call
    emission is held to compose.py's output word for word (the host corpus holds every Expression
    of the corpus to it). The request asks for the Expression of a Function (kind 1) and the
    answer is its words."""
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    words = rxfc.compile_file(COMPOSE)
    compose_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        words,
        COMPOSE.read_bytes(),
        (blob("out", bytes(OUT_ROOM)),),
    )
    assert send(channel, expected, compose_text) > 0

    arena_at, _ = live.regions["arena"]
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at = free + REQUEST_AT_SHIFT
    work_at = free + WORK_AT_SHIFT
    out_at = value_at(channel, SELFEDIT_OUT)

    arena = channel.arena()
    live_image = Image.from_bytes(arena)
    c = compose.Composer(live_image)
    for path in EXPRESSION_CARRIERS:
        target = resolve_function(live_image, path)
        print(f"  [image] composing the expression of {path}", flush=True)
        machine.write(
            request_at,
            struct.pack(
                "<12Q",
                arena_at,
                len(arena),  # R_ARENA, R_ARENA_SIZE
                target,
                1,  # R_TARGET, R_TARGET_KIND: the program of a Function (an Expression)
                out_at,
                OUT_ROOM,  # R_OUT, R_OUT_CAPACITY
                work_at,
                SCRATCH - WORK_AT_SHIFT,  # R_WORK, R_WORK_CAPACITY
                0,
                0,  # R_OUT_SIZE, R_AT
                0,
                0,
            ),
        )
        status, _ = machine.run(
            identity(expected, f"{FRONT_FUNCTION}.bytecode"),
            struct.pack("<2Q", 0, request_at),
        )
        assert status == 0, f"{name} refused with status {status} on {path}"
        (size,) = struct.unpack("<Q", machine.read(request_at + 64, 8))
        got = list(struct.unpack(f"<{size // 8}Q", machine.read(out_at, size)))
        want = list(compose.describe(compose.Expression(c, target).program()))
        assert len(got) == len(want), (name, path, len(got), len(want))
        for i, (g, w) in enumerate(zip(got, want)):
            assert g == w, (name, path, i, g, w)
