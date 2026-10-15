"""The 3D world renderer foundation: the scene exporter and the assets of the world page.

The exporter turns one image into the scene input JSON ("rxf-world-1", documented in the
docstring of ``pymergetic.rxf.world.export``). The world IS the arena — a projection, never a
second truth — so these tests pin the projection: deterministic and golden (the checked-in
``static/scene/demo-scene.json``), schema-validated, and complete at the edges the runtime
leaves behind (poison, stale handles, empty regions).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.core.kinds import NodeKind
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.world import (
    SCHEMA,
    STATIC_DIR,
    VENDOR_BABYLON,
    WORLD_DIR,
    WORLD_PAGE,
    SchemaError,
    attach_world,
    export_world,
    scene_json,
    validate_scene,
)
from pymergetic.rxf.world.demo import demo_inspector
from pymergetic.rxf.world.export import POISON_BYTE, POISON_RUN

GOLDEN = STATIC_DIR / "scene" / "demo-scene.json"
SCRIPTS = (STATIC_DIR / "scene" / "rxf-world.js", STATIC_DIR / "scene" / "rxf-world-hud.js")


@pytest.fixture(scope="module")
def scene() -> dict:
    world = export_world(demo_inspector())
    validate_scene(world)
    return world


# ---- the golden projection ------------------------------------------------------------------------


def test_the_demo_export_is_deterministic():
    once = scene_json(demo_inspector())
    twice = scene_json(demo_inspector())
    assert once == twice
    assert once.endswith("\n") and json.loads(once)["schema"] == SCHEMA


def test_the_demo_export_matches_the_checked_in_golden():
    assert GOLDEN.is_file(), "regenerate with: python -m pymergetic.rxf.world.demo > demo-scene.json"
    assert scene_json(demo_inspector()) == GOLDEN.read_text(encoding="utf-8")


# ---- the schema -----------------------------------------------------------------------------------


def test_the_scene_document_matches_the_schema(scene):
    arena = scene["arena"]
    assert arena["version"] == 6 and arena["body_size"] > arena["cells_off"]
    assert [band["band"] for band in scene["regions"][:2]] == ["header", "index"]
    assert all(int(region["start"]) < int(region["end"]) for region in scene["regions"])
    assert scene["counts"]["cells"] == len(scene["cells"])
    assert scene["counts"]["edges"] == len(scene["edges"])


def test_every_cell_carries_the_schema_fields(scene):
    for cell in scene["cells"]:
        assert cell["family"] in ("tower", "monument", "block", "utility", "rubble")
        assert isinstance(cell["handle"], (int, type(None)))
        assert isinstance(cell["plot"]["x"], float) and isinstance(cell["plot"]["z"], float)
        assert cell["footprint"] > 0 and cell["height"] > 0
        assert cell["claim"] is None and cell["checksum"] is None  # the v1 seams
        assert cell["region"].startswith("r")


def test_validation_rejects_a_wrong_document(scene):
    broken = dict(scene, schema="other")
    with pytest.raises(SchemaError):
        validate_scene(broken)
    edge = dict(scene["edges"][0])
    edge["from"] = "nobody"
    with pytest.raises(SchemaError):
        validate_scene(dict(scene, edges=[edge]))


# ---- what the world draws -------------------------------------------------------------------------


def test_run_cells_are_towers_named_cells_are_monuments(scene):
    towers = [cell for cell in scene["cells"] if cell["family"] == "tower"]
    assert {cell["kind"] for cell in towers} == {"Fn", "Code"}
    assert scene["counts"]["towers"] == len(towers) == scene["metrics"]["runs"]
    monuments = [cell for cell in scene["cells"] if cell["family"] == "monument"]
    assert any(cell["path"] == "system" for cell in monuments)  # a named cell under the root
    blocks = [cell for cell in scene["cells"] if cell["family"] == "block"]
    assert any(cell["kind"] == "Channel" for cell in blocks)
    assert scene["trams"] and [tram["run"] for tram in scene["trams"]].count(towers[0]["id"]) <= 1
    assert all(tram["state"] is None for tram in scene["trams"])  # the animation seam


def test_poison_memory_becomes_rubble_and_void_tiles(scene):
    rubble = [cell for cell in scene["cells"] if cell["family"] == "rubble"]
    assert rubble, "the removed cell leaves dead cells behind"
    poisoned = [cell for cell in scene["cells"] if cell["poisoned"]]
    assert poisoned and all(cell["family"] == "rubble" for cell in poisoned)
    assert scene["voids"], "the 0xA5 payload of the removed cell is a void run"
    for void in scene["voids"]:
        assert void["size"] >= POISON_RUN and void["cell"] in {cell["id"] for cell in poisoned}
    body = demo_inspector().image.to_bytes()
    for void in scene["voids"]:
        run = body[void["offset"] : void["offset"] + void["size"]]
        assert run == bytes([POISON_BYTE]) * len(run)


def test_a_stale_handle_makes_a_ghost_edge(scene):
    stale = [edge for edge in scene["edges"] if not edge["fresh"]]
    assert {edge["how"] for edge in stale} == {"ref", "field"}  # a ref row and a payload slot
    assert all(edge["to"] is None for edge in stale)  # the handle resolves no more
    assert all(edge["to_handle"] is not None for edge in stale)
    ids = {cell["id"] for cell in scene["cells"]}
    assert all(edge["from"] in ids for edge in stale)
    assert scene["counts"]["stale_edges"] == len(stale) == 2


def test_fresh_edges_name_two_cells_of_the_world(scene):
    ids = {cell["id"] for cell in scene["cells"]}
    for edge in scene["edges"]:
        if edge["fresh"]:
            assert edge["from"] in ids and edge["to"] in ids
        assert edge["how"] in ("ref", "role", "field", "alias")


def test_regions_without_cells_still_get_a_plot(scene):
    empty = [region for region in scene["regions"] if region["empty"]]
    assert empty and all(region["cell_count"] == 0 for region in empty)
    assert all(region["plot"]["w"] > 0 for region in scene["regions"])


def test_the_placement_grid_tiles_every_plot(scene):
    grid = scene["grid"]
    assert grid["step"] > 0 and grid["margin"] >= 0
    for region in scene["regions"]:
        raster = region["tiles"]
        assert raster["cols"] >= 1 and raster["rows"] >= 1
        assert raster["step_x"] > 0 and raster["step_z"] > 0
        assert raster["cols"] * raster["rows"] >= 1
        plot = region["plot"]
        # the raster sits inside the plot, at most one step per GRID cell
        assert raster["cols"] * raster["step_x"] <= plot["w"] + 1e-6
        assert raster["rows"] * raster["step_z"] <= plot["d"] + 1e-6
    for cell in scene["cells"]:
        col, row = cell["tile"]["x"], cell["tile"]["z"]
        assert 0 <= col and 0 <= row
        region = next(r for r in scene["regions"] if r["id"] == cell["region"])
        assert col < region["tiles"]["cols"] and row < region["tiles"]["rows"]


def test_a_cell_anatomy_its_size_floor_by_floor(scene):
    for cell in scene["cells"]:
        anatomy = cell["anatomy"]
        assert anatomy, cell["id"]
        assert all(band["size"] > 0 for band in anatomy)
        assert all(band["offset"] >= 0 for band in anatomy)
        # the floors cover the cell byte for byte, in order, no gaps and no overlap
        assert sum(band["size"] for band in anatomy) == cell["size"]
        cursor = 0
        for band in anatomy:
            assert band["offset"] == cursor
            cursor += band["size"]
    roles = {band["role"] for cell in scene["cells"] for band in cell["anatomy"]}
    assert {"type", "parent", "payload_size", "generation"} <= roles


def test_the_ring_road_is_a_closed_path_around_the_plots(scene):
    path = scene["ring"]["path"]
    assert len(path) >= 4 and all(set(point) == {"x", "z"} for point in path)
    xs = [point["x"] for point in path]
    zs = [point["z"] for point in path]
    for region in scene["regions"]:
        plot = region["plot"]
        assert min(xs) <= plot["x"] and plot["x"] + plot["w"] <= max(xs)
        assert min(zs) <= plot["z"] and plot["z"] + plot["d"] <= max(zs)
    for tram in scene["trams"]:
        assert tram["run"] in {cell["id"] for cell in scene["cells"]}
        assert 0 <= tram["angle"] < 2 * 3.141593


def test_the_hud_metrics_are_wired_where_the_image_knows_them(scene):
    metrics = scene["metrics"]
    assert metrics["runs"] == scene["counts"]["towers"]  # available: wired
    for seam in (
        "carriers",
        "steps_per_s",
        "claim_spins",
        "moved_cells",
        "refusals",
        "checksum_status",
    ):
        assert metrics[seam] is None  # clean seam: the live data arrives in a later stage
    assert scene["carriers"] == [] and scene["ring"]["state"] is None


# ---- exporter edge cases ---------------------------------------------------------------------------


def scratch_world(poison_bytes: int) -> Image:
    """A small image with a dead cell of `poison_bytes` 0xA5 bytes and one of ordinary bytes."""
    image = Image.empty()
    image.add(0, "keep", kind=int(NodeKind.Data), payload=b"\x11" * 64)
    ordinary = image.add(0, "ordinary", kind=int(NodeKind.Data), payload=b"\x22" * 64)
    image.remove(ordinary)
    poisoned = image.add(
        0, "poisoned", kind=int(NodeKind.Data), payload=bytes([POISON_BYTE]) * poison_bytes
    )
    image.remove(poisoned)
    return image


def test_a_short_poison_run_is_rubble_but_not_a_void():
    world = export_world(Inspector(scratch_world(POISON_RUN // 2)))
    validate_scene(world)
    assert world["voids"] == []
    assert world["counts"]["rubble"] >= 2
    assert not any(cell["poisoned"] for cell in world["cells"])


def test_a_poison_run_over_the_threshold_becomes_a_void():
    world = export_world(Inspector(scratch_world(POISON_RUN * 4)))
    validate_scene(world)
    assert [void["size"] for void in world["voids"]] == [POISON_RUN * 4]
    owned = {void["cell"] for void in world["voids"]}
    assert owned
    assert all(cell["poisoned"] for cell in world["cells"] if cell["id"] in owned)


def test_a_bare_ground_image_still_exports():
    world = export_world(Inspector(Image.empty()))
    validate_scene(world)
    assert world["regions"] and world["cells"]
    assert world["counts"]["towers"] == 0 and world["trams"] == []


# ---- the page assets -------------------------------------------------------------------------------


def test_the_template_and_scripts_load_only_local_assets():
    html = WORLD_PAGE.read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html and "cdn" not in html.lower()
    for reference in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert not reference.startswith(("/", "http", "//")), reference
        assert (WORLD_DIR / reference).is_file(), reference
    config_match = re.search(r'id="rxf-world-config">\s*(\{.*?\})\s*<', html, re.DOTALL)
    assert config_match is not None
    config = json.loads(config_match.group(1))
    assert config["sceneUrl"] == "scene.json"  # served by the /world/ route, not a file
    assert (WORLD_DIR / config["fallbackUrl"]).is_file()
    assert VENDOR_BABYLON.is_file()
    assert (VENDOR_BABYLON.parent / "license.md").is_file()
    assert (STATIC_DIR / "scene" / "demo-scene.json") == GOLDEN


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scene_scripts_parse(script: Path):
    result = subprocess.run(
        ["node", "--check", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_the_scene_scripts_stay_deterministic():
    for script in SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert "Math.random(" not in source
        assert "http://" not in source and "https://" not in source


# ---- the wiring seam -------------------------------------------------------------------------------


def test_attach_world_registers_the_routes():
    from fastapi.routing import APIRoute, Mount

    from pymergetic.rxf.server.app import create_app

    inspector = demo_inspector()
    app = create_app(inspector.image)
    attach_world(app, inspector)
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    paths = {route.path for route in routes}
    assert {"/world", "/world/", "/world/scene.json"} <= paths
    assert any(isinstance(route, Mount) and route.path == "/world/static" for route in app.routes)
    handlers = {route.path: route.endpoint for route in routes}
    page = handlers["/world/"]()
    assert "<title>RXF world</title>" in page.body.decode()
    served = json.loads(handlers["/world/scene.json"]().body)
    assert served["schema"] == SCHEMA
    validate_scene(served)
    redirect = handlers["/world"]()
    assert redirect.headers["location"] == "/world/"
