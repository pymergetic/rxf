"""The page's world reader: rxf-reader.js pinned to the Python exporter, one contract in
two languages.

The world page loads a scene by reading raw ``.rxfb`` bytes in the browser — the same
reader file it serves (``static/scene/rxf-reader.js``) — instead of asking the host to
export JSON. This suite is the seam's oracle: the same demo bytes must yield the same
document in both languages (same ids, same names, same numbers), compared against the
exporter's client face ``export_world(..., packing=False)``. The duct packing is the one
deliberate difference: it stays a Python pass (``packing=True``), never on the page's
blocking path, so the client face carries no bundles and no stubs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import arena_export
import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.kinds import NodeKind
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector
from pymergetic.rxftk.world.demo import (
    DELTA_FILE,
    RUNS,
    demo_image,
    demo_inspector,
    run_payload,
)
from pymergetic.rxftk.world.export import ArenaScene, export_world, validate_scene

HERE = Path(__file__).resolve().parent
STARTER = HERE.parent / "starter"
READER = HERE.parent / "src" / "pymergetic" / "rxftk" / "world" / "static" / "scene" / "rxf-reader.js"
RUNNER = HERE / "reader_js" / "run.mjs"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _read_in_js(image: Path, tmp_path: Path) -> dict:
    """Run the browser's reader over the bytes and return the document it computes."""
    assert NODE is not None  # the module's skipif; the assertion also narrows NODE for the type checker
    out = tmp_path / "reader.json"
    subprocess.run(
        [NODE, str(RUNNER), str(READER), str(image), str(out)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(out.read_text())


def test_the_reader_file_is_the_one_the_page_serves() -> None:
    """The pin is worthless if it runs a reader the page does not load."""
    assert READER.is_file()
    assert "export function readWorld" in READER.read_text()
    assert "export class RxfReader" in READER.read_text()


def test_reader_js_reads_the_demo_bytes_like_the_exporter(tmp_path: Path) -> None:
    """One image, two languages, one document — byte-for-byte semantics (Python ==)."""
    image = tmp_path / "demo.rxfb"
    image.write_bytes(demo_image().to_bytes())

    document = _read_in_js(image, tmp_path)

    assert document == export_world(demo_inspector(), packing=False)


def test_reader_js_document_is_a_valid_scene(tmp_path: Path) -> None:
    """What the page builds from must satisfy the schema the host pins."""
    image = tmp_path / "demo.rxfb"
    image.write_bytes(demo_image().to_bytes())

    validate_scene(_read_in_js(image, tmp_path))


def test_reader_js_survives_the_poison_and_the_stale(tmp_path: Path) -> None:
    """The demo has stale edges and poisoned bytes; the reader's fresh/stale split and its
    cell anatomy must agree with the exporter exactly, not just on the pretty parts."""
    image = tmp_path / "demo.rxfb"
    image.write_bytes(demo_image().to_bytes())

    document = _read_in_js(image, tmp_path)
    expected = export_world(demo_inspector(), packing=False)

    assert sum(1 for edge in document["edges"] if not edge["fresh"]) == sum(
        1 for edge in expected["edges"] if not edge["fresh"]
    )
    assert [cell["id"] for cell in document["cells"]] == [
        cell["id"] for cell in expected["cells"]
    ]
    assert document["counts"] == expected["counts"]


def _demo_v7_arena(tmp_path: Path) -> Path:
    """The demo world as the v7 one-arena image (docs/32): the delta and the run payloads,
    written by the world->arena writer (tools/native/arena_export.py). The demo's v6-only
    traces — a dead cell of poison and a dangling ref — have no v7 form yet (the writer
    refuses a dangling target, and a free span carries no payload), so they stay pinned on
    the v6 face above; the Genesis names its inventor under `pymergetic`, which the demo
    scene itself does not carry (docs/36)."""
    image = Image.empty()
    image.apply(DELTA_FILE.read_text(encoding="utf-8"))
    for index, (path, state) in enumerate(sorted(RUNS.items())):
        image.set_payload(image.handle(path), run_payload(state, 3 + 4 * index))
    image.add(0, "pymergetic", kind=int(NodeKind.Namespace))
    out = tmp_path / "demo-v7.rxfa"
    out.write_bytes(arena_export.export_world(image).to_bytes())
    return out


def test_reader_js_reads_the_v7_arena_like_the_exporter(tmp_path: Path) -> None:
    """The one-arena image (docs/32), read by both languages from the same bytes: the
    superblock, the slot table, the records, the interned names (sys/syms), the semantic
    rows (sys/specs) and the relation tables (sys/relations/<path>) — one document."""
    image = _demo_v7_arena(tmp_path)
    document = _read_in_js(image, tmp_path)

    assert document == export_world(ArenaScene(image.read_bytes()), packing=False)
    validate_scene(document)


def test_reader_js_surfaces_the_spec_row_on_v7(tmp_path: Path) -> None:
    """The four words the semantic face must answer (docs/32 5): the kind word and the
    state word come from the spec row — the record's kind is only the storage role — the
    field edges are derived from the type's layout, and the anatomy bands are the v7
    object record's own words."""
    image = _demo_v7_arena(tmp_path)
    document = _read_in_js(image, tmp_path)

    kinds = {cell["kind"] for cell in document["cells"]}
    assert {"Fn", "Code", "Type"} <= kinds  # the kind word, from the rows
    assert any(cell["state"]["provenance"] == "Image" for cell in document["cells"])
    fields = [edge for edge in document["edges"] if edge["how"] == "field"]
    assert fields and all(edge["field"] for edge in fields)
    assert all(edge["fresh"] for edge in fields)  # the export carries live arena handles
    roles = {band["role"] for cell in document["cells"] for band in cell["anatomy"]}
    assert {"kind", "gen", "flags", "name", "parent", "link", "refs", "payload"} <= roles
    # the run words the towers carry survive the export: the trams and the checksum census
    assert document["ring"]["occupied"] == 2
    assert document["metrics"]["checksum_status"] == "3 ok"


@pytest.mark.slow
def test_reader_js_reads_the_real_tree_at_scale(tmp_path: Path) -> None:
    """The real `starter/` tree (17,924 cells, 79,711 cables): the scale the demo cannot show —
    the multi-chunk index (handles past 4096), the tie rounding of `round(value, 3)` (the
    milli-ties Python breaks to even), and 5.5 MB of bytes read the way the browser reads
    them. The whole document must match the exporter's client face — not a sample of it."""
    image = tree.load(STARTER)
    raw = image.to_bytes()
    path = tmp_path / "starter.rxfb"
    path.write_bytes(raw)

    document = _read_in_js(path, tmp_path)

    assert document["arena"]["handle_count"] > 4096  # the index has chunks to walk
    assert document["arena"]["index_chunks"] > 1
    assert document == export_world(Inspector(image), packing=False)
