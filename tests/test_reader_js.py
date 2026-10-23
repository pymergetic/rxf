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

import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.server.inspector import Inspector
from pymergetic.rxftk.world.demo import demo_image, demo_inspector
from pymergetic.rxftk.world.export import export_world, validate_scene

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
