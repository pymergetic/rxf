"""The server views: the memory map bins and the decoded detail of one object.

The main suite reads the `starter/` module tree, a real image of the format; the small edge
cases (truncation, an empty reference list) build their own image.
"""

import json
import re
from pathlib import Path

import pytest

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.arena import (
    CELL_ALIGN,
    CELL_SIZE,
    HANDLE_NONE,
    HANDLE_ROOT,
    REF_SIZE,
)
from pymergetic.rxf.core.kinds import Binding, Mobility, NodeKind, OwnerKind, RefKind
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector

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

    from pymergetic.rxf.server.app import create_app

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
