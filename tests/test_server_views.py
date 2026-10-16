"""The server views: the memory map bins and the decoded detail of one object.

The main suite reads the `starter/` module tree, a real image of the format; the small edge
cases (truncation, an empty reference list) build their own image.
"""

import asyncio
import io
import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from pymergetic.rxftk.console import world
from pymergetic.rxftk.console.session import Console
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import (
    CELL_ALIGN,
    CELL_SIZE,
    HANDLE_NONE,
    HANDLE_ROOT,
    REF_SIZE,
)
from pymergetic.rxftk.core.kinds import Binding, Mobility, NodeKind, OwnerKind, RefKind
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector
from pymergetic.rxftk.server.live import Session

from .test_console import MACHINES, MODULE, TRIPLE, Seat, booted, seat_of

STARTER = Path(__file__).parents[1] / "starter"


@pytest.fixture(scope="module")
def inspector() -> Inspector:
    return Inspector(tree.load(STARTER))


@pytest.fixture(scope="module")
def image(inspector: Inspector) -> Image:
    return inspector.image


def align16(value: int) -> int:
    return (value + CELL_ALIGN - 1) // CELL_ALIGN * CELL_ALIGN


def cell_bytes(payload_size: int, ref_count: int) -> int:
    """The cell size formula of ground.h, spelled out independently of the core."""
    raw = CELL_SIZE + align16(payload_size) + ref_count * REF_SIZE
    return align16(raw)


def first_with_refs(image: Image) -> int:
    for handle in range(image.handle_count):
        if image.live(handle) and image.get(handle)["refs"]:
            return handle
    raise AssertionError("no object with references in the starter tree")


# ---- the memory map ------------------------------------------------------------------------------


def test_the_memory_map_covers_the_whole_arena(inspector: Inspector):
    memory = inspector.memory()
    header = memory["arena"]
    assert memory["start"] == 0 and memory["end"] == header["body_size"]
    assert [r["name"] for r in memory["regions"]] == ["header", "index", "cells"]
    assert memory["regions"][0] == {"name": "header", "start": 0, "end": header["header_size"]}
    assert memory["regions"][1]["start"] == header["index_off"]
    assert memory["regions"][2] == {
        "name": "cells",
        "start": header["cells_off"],
        "end": header["body_size"],
    }
    bins = memory["bins"]
    assert len(bins) == 256
    for i, row in enumerate(bins):
        assert row["start"] == (0 if i == 0 else bins[i - 1]["end"])
        width = row["end"] - row["start"]
        assert 0 <= row["used"] <= width
        assert 0 <= row["payload"] <= row["used"]
        assert 0.0 <= row["occupancy"] <= 1.0
        assert (row["count"] > 0) == bool(row["kinds"])
        if row["count"]:
            assert row["dominant_kind"] is not None
            assert sum(row["kinds"].values()) == row["used"]
        else:
            assert row["dominant_kind"] is None
    assert bins[-1]["end"] == header["body_size"]
    # The whole cell stream is accounted for, byte for byte.
    assert sum(memory["kinds"].values()) == header["frontier"]
    assert set(memory["kinds"]) >= {"Names", "Index", "Root", "Type", "Field"}


def test_the_memory_map_knows_its_objects(inspector: Inspector, image: Image):
    memory = inspector.memory(bins=1024)
    named = [row for row in memory["bins"] if row["path"] is not None]
    assert named
    for row in named:
        handle = image.handle(row["path"])
        assert handle == row["handle"]
        got = image.get(handle)
        assert row["objects"] == 1
        assert row["payload_size"] == len(got["payload"])
        assert row["ref_count"] == len(got["refs"])
        assert row["dominant"]["path"] == row["path"]
    dominant = [row for row in memory["bins"] if row["dominant"] is not None]
    assert dominant
    for row in dominant[:20]:
        assert image.resolve(row["dominant"]["path"]) == row["dominant"]["handle"]
        assert row["dominant"]["kind"] == NodeKind(image.get(row["dominant"]["handle"])["kind"]).name


def test_the_memory_map_zooms_and_clamps(inspector: Inspector, image: Image):
    memory = inspector.memory(start=1000, end=2000, bins=16)
    assert (memory["start"], memory["end"]) == (1000, 2000)
    bins = memory["bins"]
    assert len(bins) == 16
    assert bins[0]["start"] == 1000 and bins[-1]["end"] == 2000
    wide = inspector.memory(start=-5, end=10**9, bins=1)
    assert wide["start"] == 0 and wide["end"] == image.body_size
    assert len(wide["bins"]) == 16  # the grid size is clamped
    assert len(inspector.memory(bins=10**6)["bins"]) == 4096


def test_the_memory_bins_describe_dead_bytes_as_dead():
    """A destroyed object stays in the stream until compaction; the map must not paint it live."""
    image = Image.empty()
    handle = image.add(HANDLE_ROOT, "gone", kind=NodeKind.Data, payload=b"x" * 64)
    offset = next(c["offset"] for c in image.arena_cells() if c["handle"] == handle)
    image.remove(handle)
    view = Inspector(image).memory(start=offset, end=offset + 256, bins=16)
    kinds = set(view["kinds"])
    assert "Dead" in kinds and "Data" not in kinds
    for row in view["bins"]:
        assert row["path"] != "gone"


# ---- one object, decoded --------------------------------------------------------------------------


def test_detail_decodes_the_cell_and_flags(inspector: Inspector, image: Image):
    detail = inspector.detail("pymergetic.rxf")
    got = image.get(image.handle("pymergetic.rxf"))
    assert detail["path"] == "pymergetic.rxf" == got["path"]
    assert detail["name"] == "rxf" == got["name"]
    assert detail["handle"] == got["handle"]
    assert detail["generation"] == got["generation"]
    assert detail["kind"] == NodeKind(got["kind"]).name == "Module"
    assert detail["kind_value"] == got["kind"]
    assert detail["parent"] == "pymergetic"
    assert detail["type"] == "Module"

    flags = detail["flags"]
    raw = flags["raw"]
    assert raw == (got["kind"] | got["state"] << 8 | got["owner_kind"] << 14)
    assert (raw >> 0) & 0xFF == detail["kind_value"]
    state = flags["state"]
    assert state["value"] == (raw >> 8) & 0x3F == got["state"]
    assert flags["owner"] == OwnerKind(got["owner_kind"]).name
    assert state["provenance"] in ("Runtime", "Image")
    assert state["disposition"] in ("Retain", "Transient", "Tombstone")
    assert state["cleanliness"] in ("Clean", "Dirty")
    assert state["mobility"] == Mobility((state["value"] >> 4) & 0x3).name

    cell = detail["cell"]
    walk = {c["handle"]: c for c in image.arena_cells() if c["handle"] is not None}
    at = walk[detail["handle"]]
    assert (cell["offset"], cell["size"]) == (at["offset"], at["size"])
    assert cell["size"] == cell_bytes(len(got["payload"]), len(got["refs"]))
    assert cell["offset"] % CELL_ALIGN == 0
    assert (cell["payload_size"], cell["ref_count"]) == (len(got["payload"]), len(got["refs"]))
    assert cell["flags"] == raw
    assert cell["forward"] == 0 and cell["name_off"] != 0


def test_detail_shows_the_payload_as_hex_rows(inspector: Inspector, image: Image):
    detail = inspector.detail("uint64_t")
    payload = image.get(image.handle("uint64_t"))["payload"]
    dump = detail["payload"]
    assert dump["size"] == len(payload) == 48
    assert dump["displayed"] == 48 and not dump["truncated"]
    rows = dump["rows"]
    assert len(rows) == 3  # 48 bytes, 16 per row
    for i, row in enumerate(rows):
        assert row["offset"] == i * 16
        assert len(row["bytes"]) == len(row["ascii"]) == len(row["highlighted"])
        assert all(re.fullmatch(r"[0-9a-f]{2}", b) for b in row["bytes"])
    assert "".join(b for row in rows for b in row["bytes"]) == payload.hex()
    # Every byte of the 48-byte Type payload is covered exactly once.
    assert sum(len(row["bytes"]) for row in rows) == 48


def test_detail_decodes_every_reference_row(inspector: Inspector, image: Image):
    handle = first_with_refs(image)
    detail = inspector.detail(image.path(handle))
    got = image.get(handle)
    assert len(detail["refs"]) == len(got["refs"]) > 0
    for row, (target, role, index, kind, binding) in zip(detail["refs"], got["refs"], strict=True):
        assert row["target_handle"] == (None if target == HANDLE_NONE else target)
        assert row["target"] == (None if target == HANDLE_NONE else image.path(target))
        assert row["role_handle"] == role
        assert row["role"] == (None if role is None else image.path(role))
        assert row["kind"] == RefKind(kind).name
        assert row["binding"] == Binding(binding).name
        assert row["index"] == index


def test_detail_lists_who_points_at_the_object(inspector: Inspector, image: Image):
    # A type is named by its fields and by every object of that type.
    users = inspector.detail("uint64_t")["used_by"]
    assert any(u["how"] == "field" for u in users)
    handle = image.handle("pymergetic.rxf")
    type_handle = image.get(handle)["type"]
    theirs = inspector.detail(image.path(type_handle))["used_by"]
    assert {"path": "pymergetic.rxf", "how": "type"} in theirs
    # The root is the parent of the top level.
    root = inspector.detail("")
    assert {"path": "pymergetic", "how": "parent"} in root["used_by"]
    assert root["used_by_count"] >= len(root["used_by"])
    for entry in root["used_by"]:
        assert entry["how"] in ("ref", "role", "field", "alias", "parent", "type")
        assert image.resolve(entry["path"]) is not None


def test_the_root_has_no_parent_and_no_type(inspector: Inspector):
    root = inspector.detail("")
    assert root["handle"] == HANDLE_ROOT
    assert (root["path"], root["name"]) == ("", "")
    assert root["parent"] is None and root["parent_handle"] is None
    assert root["type"] is None and root["type_handle"] is None
    assert root["payload"]["rows"] == []
    with pytest.raises(KeyError):
        inspector.detail("nothing.here")


def test_detail_links_into_the_model_documentation(inspector: Inspector):
    """Every detail names the Model sections that explain the cell it shows: its face
    (run cell / object cell / named cell / channel frame), its header, its handle, and the
    step that touches it — plus ABA and compaction guides when the cell's words say so."""
    module = inspector.detail("pymergetic.rxf")
    model = module["model"]
    assert model[0]["anchor"] == "named-cells"  # a Module under the root
    hrefs = {entry["anchor"]: entry["href"] for entry in model}
    assert hrefs["cell-anatomy"] == "/model?path=pymergetic.rxf#cell-anatomy"
    assert {"handles", "step-loop"} <= set(hrefs)
    for entry in model:
        assert entry["label"] and entry["cite"] and entry["why"]
    # a nested object is an object cell (the REPR A face), not a named one
    alias = inspector.detail("pymergetic.rxf.numeric.numeric")
    assert alias["model"][0]["anchor"] == "object-cells"
    # the root is the named cell of named cells
    assert inspector.detail("")["model"][0]["anchor"] == "named-cells"


def test_a_payload_hex_dump_truncates():
    image = Image.empty()
    image.add(HANDLE_ROOT, "big", kind=NodeKind.Data, payload=bytes(range(256)) * 40)
    dump = Inspector(image).detail("big")["payload"]
    assert dump["size"] == 10240
    assert dump["displayed"] == 4096 and dump["truncated"]
    assert len(dump["rows"]) == 256
    assert dump["rows"][-1]["offset"] == 4080 and len(dump["rows"][-1]["bytes"]) == 16


def test_an_object_without_references_says_none():
    image = Image.empty()
    image.add(HANDLE_ROOT, "plain", kind=NodeKind.Data, payload=b"\x00")
    detail = Inspector(image).detail("plain")
    assert detail["refs"] == []
    assert detail["used_by"] == [] and detail["used_by_count"] == 0


# ---- the app --------------------------------------------------------------------------------------


def test_the_app_serves_memory_and_detail(inspector: Inspector):
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.server.app import create_app

    app = create_app(inspector.image)
    handlers = {
        route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)
    }
    assert {"/", "/api/memory", "/api/detail"} <= set(handlers)
    memory = handlers["/api/memory"](None, None, 32)
    assert set(memory) == {"arena", "regions", "start", "end", "bins", "kinds"}
    assert len(memory["bins"]) == 32
    detail = handlers["/api/detail"]("pymergetic.rxf")
    assert detail["path"] == "pymergetic.rxf"
    json.dumps(memory)
    json.dumps(detail)
    with pytest.raises(Exception, match="no object"):
        handlers["/api/detail"]("nothing.here")


# ---- the serve instance: the world of the running image ------------------------------------------


@pytest.fixture
def solo(image: Image) -> Iterator[Seat]:
    """One machine of the harness: what the serve instance runs its image on."""
    made = seat_of(image, MACHINES[0])
    try:
        yield made
    finally:
        made.machine.close()


def test_the_serve_instance_registers_the_world_while_it_serves(image: Image, solo: Seat):
    """What `rxf serve` runs: while the app serves, the world of the image is registered at its
    link — the console attaches the channel and the front end of the running image — and shutting
    down takes the registration with it."""
    from pymergetic.rxftk.server.app import create_app

    booted(solo)  # what the image's own `rxf live` group boots before a console attaches
    session = Session("shm:serve-runs", solo.channel.client, solo.front)
    app = create_app(image, session=session)

    async def serving() -> None:
        async with app.router.lifespan_context(app):
            attached = world.attach("shm:serve-runs")
            assert attached is session.world
            assert attached.client.ping(b"the channel") == b"the channel"
            assert attached.client.resolve("") == HANDLE_ROOT
            assert attached.client.digest() == solo.channel.loaded().digest()
            outcome = Console(attached.front, io.StringIO()).submit(TRIPLE)
            assert outcome["ok"] is True
            assert outcome["path"] == f"{MODULE}.triple"

    asyncio.run(serving())
    with pytest.raises(world.ConsoleError, match="gives no runner"):
        world.attach("shm:serve-runs")
    fresh = Session("shm:serve-runs", solo.channel.client, solo.front).open()
    assert world.attach("shm:serve-runs") is fresh.world
    fresh.close()


def test_the_serve_instance_surfaces_a_taken_link_honestly(image: Image, solo: Seat):
    """A link whose image already runs refuses a second registration with the seam's own refusal
    — typed, and naming the link — and the runner that is there keeps it."""
    from pymergetic.rxftk.server.app import create_app

    first = Session("shm:taken-link", solo.channel.client, solo.front).open()
    try:
        app = create_app(
            image, session=Session("shm:taken-link", solo.channel.client, solo.front)
        )

        async def serving() -> None:
            async with app.router.lifespan_context(app):
                raise AssertionError("the serve instance must not come up on a taken link")

        with pytest.raises(world.WorldIsRegistered, match="shm:taken-link"):
            asyncio.run(serving())
        assert world.attach("shm:taken-link") is first.world
    finally:
        first.close()


# ---- the live slot's feed: the session spec the shell renders (docs/21) -------------------------


LIVE_LEAF = "pymergetic.rxf.foundation.live.pump"


def live_feed(image: Image, session: Session | None):
    """The endpoint behind the shell's live slot (`GET /api/live`), called as the app calls it."""
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.server.app import create_app

    app = create_app(image, session=session)
    handlers = {
        route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)
    }
    assert "/api/live" in handlers, "the live slot has no feed to render"
    return handlers["/api/live"]


def test_the_live_feed_is_exactly_no_image_without_a_session(image: Image):
    """`no image` (docs/21) is the answer only when the app runs no session: the shape of the
    spec stays, every field of it nothing."""
    got = live_feed(image, None)()
    assert got == {
        "attached": False,
        "spec": None,
        "image": None,
        "interpreter": None,
        "leaf": None,
        "counters": None,
        "refusal": None,
    }
    json.dumps(got)


def test_the_live_feed_reports_the_attached_image_and_its_runtime(image: Image, solo: Seat):
    """One session, one spec: the link it runs at, the identity of the image at it, the version
    its channel answers, the handle and state of its live leaf, and its live counters — every
    word of it an answer of the running image (`rxf live info` of the link and `Client.get` of
    the leaf), nothing computed here."""
    client = solo.channel.client
    session = Session("shm:live-spec", client, solo.front)
    got = live_feed(image, session)()
    hello = client.hello()
    assert got["attached"] is True and got["spec"] == "shm:live-spec"
    assert got["refusal"] is None
    # what is attached is the very image the app inspects, named the way `rxf live info` names it
    assert got["image"] == {
        "digest": image.digest(),
        "handles": hello.handles,
        "body_size": hello.body_size,
    }
    assert (got["image"]["handles"], got["image"]["body_size"]) == (
        image.handle_count,
        image.body_size,
    )
    # `interpreter.version` is `Hello.version`: the version the running image answers
    assert got["interpreter"] == {"version": hello.version}
    assert got["counters"] == {
        "commits": hello.commits,
        "rejects": hello.rejects,
        "committed": hello.committed,
        "arena_capacity": hello.arena_capacity,
        "stage_capacity": hello.stage_capacity,
        "emit_capacity": hello.emit_capacity,
        "work_capacity": hello.work_capacity,
    }
    # the live leaf of the image: its handle and its state word, in the one vocabulary the
    # inspector spells for the same object of the same image
    handle = client.resolve(LIVE_LEAF)
    assert handle is not None
    assert got["leaf"]["path"] == LIVE_LEAF and got["leaf"]["handle"] == handle
    running = solo.channel.loaded()
    assert got["leaf"]["state"] == Inspector(running).detail(LIVE_LEAF)["flags"]["state"]
    assert got["leaf"]["state"]["value"] == running.get(handle)["state"]
    json.dumps(got)


def test_the_live_feed_reports_a_session_whose_channel_is_gone(image: Image, solo: Seat):
    """A session that does not answer is not `no image`: it is attached, and the channel's own
    refusal is what the slot shows (docs/21: the attached session or `no image` — no third
    wording invented for a dead link)."""
    import os

    from pymergetic.rxftk.live.client import Client
    from pymergetic.rxftk.live.link import FdLink

    read_end, write_end = os.pipe()  # a link nothing answers on, as a rig gone quiet
    try:
        session = Session("shm:gone", Client(FdLink(read_end), timeout=0.05), solo.front)
        got = live_feed(image, session)()
    finally:
        os.close(read_end)
        os.close(write_end)
    assert got["attached"] is True and got["spec"] == "shm:gone"
    assert got["image"] is None and got["interpreter"] is None
    assert got["leaf"] is None and got["counters"] is None
    assert got["refusal"], "a channel that gives no answer must say so, not show `no image`"
    json.dumps(got)


def test_the_editor_and_the_inspector_render_the_one_feed_through_the_shell():
    """The wiring of the live slot: `static/ui/ui.js` is the ONE feed component of
    `GET /api/live`, and the editor and the inspector are its two consumers — both carry the
    shell's slot and link the feed, neither keeps a page-local copy of it (docs/21)."""
    server = Path(__file__).parents[1] / "src" / "pymergetic" / "rxftk" / "server"
    feed = (server / "static" / "ui" / "ui.js").read_text(encoding="utf-8")
    assert "/api/live" in feed
    assert "no image" in feed  # the default the shell's slot holds until the feed knows better
    for page in ("editor.html", "inspector.html"):
        html = (server / "templates" / page).read_text(encoding="utf-8")
        assert 'src="/static/ui/ui.js"' in html, f"{page} does not link the one feed"
        assert '<div class="live" id="live-slot">no image</div>' in html, (
            f"{page} does not carry the shell's live slot"
        )


# ---- the semantic highlighter of the web panes --------------------------------------------------
#
# static/ui/highlight.js colors the code panes by what their tokens MEAN (docs/21 palette,
# docs/15 deltas, docs/22 docs): a handle or cell reference is addressable, a RefKind value is
# what an edge is for, an opcode word is what runs, a Doc marker is recorded intent. The
# vocabulary is data and must not drift from the format: the core enumerations are pinned to
# pymergetic.rxftk.core.kinds at runtime, the starter enumerations to the one meaning table
# tools/highlight/meaning.json. node runs the module's pure surface (the classifier never
# touches the DOM), so these pins need node; the markup pins below do not.

HIGHLIGHT = Path(__file__).parents[1] / "src/pymergetic/rxftk/server/static/ui/highlight.js"
HIGHLIGHT_UI = HIGHLIGHT.parent
MEANING = Path(__file__).parents[1] / "tools/highlight/meaning.json"
TEMPLATES = Path(__file__).parents[1] / "src/pymergetic/rxftk/server/templates"

needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node runs the highlighter's pure surface"
)

# type name -> the semantic class its words carry (the class register of highlight.js)
CORE_KINDS = {
    "NodeKind": "kind",
    "RefKind": "refkind",
    "Binding": "kind",
    "OwnerKind": "kind",
    "Mobility": "kind",
    "Form": "kind",
    "TypeFlags": "kind",
    "FieldFlags": "kind",
    "DocKind": "doc",
    "DocStatus": "doc",
    "DocCodec": "doc",
    "DocRole": "doc",
}
STARTER_KINDS = {
    "Effects": "effects",
    "Opcode": "opcode",
    "NumericOperation": "opcode",
    "Intrinsic": "opcode",
    "Terminator": "opcode",
    "RefusalCode": "kind",
    "ModuleCategory": "kind",
    "ModuleFlags": "kind",
    "AliasFlags": "kind",
}


def in_node(call: str) -> Any:
    """Run one call of highlight.js's pure surface in node and read its JSON answer."""
    run = subprocess.run(
        [
            "node",
            "-e",
            f"const h=require(process.argv[1]);process.stdout.write(JSON.stringify({call}))",
            str(HIGHLIGHT),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def classify_values(values: list[str]) -> dict[str, str | None]:
    """classifyValue over a list of values, in one node run."""
    payload = json.dumps(json.dumps(values))  # the list as a JS string literal of JSON
    return in_node(
        f"Object.fromEntries(JSON.parse({payload}).map(v => [v, h.classifyValue(v)]))"
    )


@needs_node
def test_the_register_carries_every_word_of_the_core_kinds():
    """Every enumerator of core/kinds is a word of the register, under its semantic class."""
    from pymergetic.rxftk.core import kinds as core

    enums = in_node("h.REGISTER.enums")
    for name, cls in CORE_KINDS.items():
        assert enums[name]["c"] == cls, f"{name} lost its semantic class"
        assert set(enums[name]["words"]) == set(getattr(core, name).__members__), (
            f"the register's {name} drifted from core/kinds"
        )


@needs_node
def test_the_register_carries_every_word_of_the_meaning_table():
    """The starter enumerations are the meaning table's, and every enumerator of the table —
    of any type — is classified as a word of the format (never a plain identifier)."""
    table = json.loads(MEANING.read_text(encoding="utf-8"))
    transcribed = {e["type"]: set(e["enumerators"]) for e in table["rxfd"]["enums"]}
    enums = in_node("h.REGISTER.enums")
    for name, cls in STARTER_KINDS.items():
        assert enums[name]["c"] == cls, f"{name} lost its semantic class"
        assert set(enums[name]["words"]) == transcribed[name], (
            f"the register's {name} drifted from the meaning table"
        )
    values = [
        f"{name}.{word}" for name, words in transcribed.items() for word in sorted(words)
    ]
    got = classify_values(values)
    for value in values:
        name = value.split(".")[0]
        want = enums[name]["c"] if name in enums else "kind"
        assert got[value] == want, f"{value} does not carry its meaning"


EXPECTED_VALUES = {
    "RefKind.Data": "refkind",
    "RefKind.Doc": "refkind",
    "@kinds.Effects.Write|Allocate": "effects",
    "Form.Struct": "kind",
    "DocRole.Narrates": "doc",
    "DocKind.Todo": "doc",
    "Opcode.MoveSlot": "opcode",
    "FunctionLayer.Library": "kind",
    "pymergetic.rxf.roles.call.callee": "path",
    "old.thing": "path",
    "42": "number",
    "0x2a": "number",
    "true": "builtin",
    "MODULE": "kind",
    "a": "path",
}
EXPECTED_WORDS = {
    "MODULE": "kind",  # rxf:kind names the NodeKind of a cell in shouty words
    "Module": "kind",
    "Pinned": "kind",  # a bare word is a kind word before it is an edge word
    "Call": "refkind",
    "Write": "effects",
    "Todo": "doc",
    "NothingLikeThis": None,
}


@needs_node
def test_the_classifier_says_what_every_token_means():
    """The fixed battery of the pure surface: what each value and word classifies to, and what
    each lexer of a pane emits. The scan of every pane drops nothing and overlaps nothing."""
    battery = in_node("h.selftest()")
    assert battery["values"] == EXPECTED_VALUES
    assert battery["words"] == EXPECTED_WORDS
    for lang, tokens in battery["tokens"].items():
        assert "".join(text for text, _ in tokens) == battery["samples"][lang]
    xml = battery["tokens"]["xml"]
    assert xml[:3] == [["<", "punct"], ["rxf:", "ns"], ["doc", "doc"]]
    assert ["role", "attr"] in xml and ["DocRole.Narrates", "doc"] in xml
    assert ["was once Python", "doc"] in xml  # the provenance marker (docs/22)
    rxfs = battery["tokens"]["rxfs"]
    assert ["program", "keyword"] in rxfs and ["double", "fn"] in rxfs
    assert ["mul", "fn"] in rxfs and ["=", "operator"] in rxfs
    assert ["call", "keyword"] in rxfs and ["twice", "path"] in rxfs
    assert ["# TODO: fold", "doc"] in rxfs
    llvm = battery["tokens"]["llvm"]
    assert ["@f", "handle"] in llvm and ["%x", "handle"] in llvm
    assert ["define", "keyword"] in llvm and ["ret", "opcode"] in llvm
    text = battery["tokens"]["text"]
    assert ["#42", "handle"] in text  # a cell handle is addressable
    assert ["pymergetic.rxf.selfedit.triple", "path"] in text
    c = battery["tokens"]["c"]
    assert ["uint64_t", "type"] in c and ["// a comment", "comment"] in c
    python = battery["tokens"]["python"]
    assert ["len", "builtin"] in python and ["# TODO", "doc"] in python
    assert in_node("h.REGISTER.languages") == [
        "text",
        "xml",
        "rxfd",
        "rxfs",
        "c",
        "cpp",
        "llvm",
        "ll",
        "python",
        "py",
    ]


def test_the_code_panes_host_the_semantic_highlighter():
    """The editor and the inspector load the one highlighter, every code pane declares its
    language (or says data-nohl), and the semantic classes are styled by the one stylesheet —
    the pages paint no class of their own and no palette literal (docs/21)."""
    assert HIGHLIGHT.is_file()
    css = (HIGHLIGHT_UI / "ui.css").read_text(encoding="utf-8")
    for cls in (
        "m-handle",
        "m-path",
        "m-refkind",
        "m-effects",
        "m-kind",
        "m-opcode",
        "m-doc",
        "m-tag",
        "m-attr",
    ):
        assert f".{cls}" in css, f"ui.css styles no {cls} token class"
    for var in ("--hl-tag", "--hl-path", "--hl-kind", "--hl-opcode", "--hl-doc"):
        assert var in css, f"ui.css defines no {var} semantic color"
    for name, marks in (
        ("editor.html", ("data-lang=", "RXFHighlight.")),
        ("inspector.html", ("data-nohl", "RXFHighlight.")),
    ):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert '<script src="/static/ui/highlight.js"></script>' in text
        for mark in marks:
            assert mark in text, f"{name} hosts no {mark} pane"
