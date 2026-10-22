"""The 3D world renderer foundation: the scene exporter and the assets of the world page.

The exporter turns one image into the scene input JSON (documented in the
docstring of ``pymergetic.rxftk.world.export``). The world IS the arena — a projection, never a
second truth — so these tests pin the projection: deterministic and golden (a pinned content
digest of the demo export — never a checked-in JSON dump), schema-validated, and complete at the
edges the runtime leaves behind (poison, stale handles, empty regions).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.core.kinds import NodeKind
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector
from pymergetic.rxftk.world import (
    SCHEMA,
    STATIC_DIR,
    VENDOR_BABYLON,
    WORLD_DIR,
    WORLD_PAGE,
    Layers,
    SchemaError,
    attach_world,
    export_world,
    scene_json,
    validate_scene,
)
from pymergetic.rxftk.world.demo import demo_inspector
from pymergetic.rxftk.world.export import POISON_BYTE, POISON_RUN

# The golden is a content digest, not a checked-in dump: the demo export is regenerated in the
# test and hashed, so a routing or layout change trips this one line to confirm it was intended.
# scene.bin is the raw .rxfb the static seat reads — kept fresh beside it.
DEMO_DIGEST = "e66268e12ed6b1d81143f8557fc6643b337971948b5124f514e1eb2b87b94b47"
SCENE_BIN = STATIC_DIR / "scene" / "scene.bin"
SCRIPTS = (STATIC_DIR / "scene" / "rxf-world.js", STATIC_DIR / "scene" / "rxf-world-hud.js")
# the reader and its worker are ES modules (the page imports them on the binary lane); node
# checks them in module mode — same gate, the mode the files are loaded in
ESM_SCRIPTS = (
    STATIC_DIR / "scene" / "rxf-reader.js",
    STATIC_DIR / "scene" / "rxf-reader-worker.js",
)


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


def test_the_demo_export_matches_the_pinned_digest():
    digest = hashlib.sha256(scene_json(demo_inspector()).encode()).hexdigest()
    assert digest == DEMO_DIGEST, (
        "the demo world changed — confirm it was intended, then set DEMO_DIGEST to:\n" + digest
    )


def test_the_checked_in_scene_bin_is_current():
    """The static seat (``static/scene/index.html``) reads the checked-in ``scene.bin``. Refresh
    it with ``python -m pymergetic.rxftk.world.demo > static/scene/scene.bin`` when the world
    changes, so the standalone seat never renders a stale world."""
    assert SCENE_BIN.read_bytes() == demo_inspector().image.to_bytes()


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
        assert cell["claim"] is None  # the v1 seam
        # the run words stay null unless the payload really is smp_run_payload (docs/20, S1)
        assert cell["run_state"] in (None, "FREE", "READY", "RUNNING", "PARKED", "TERMINAL")
        assert cell["checksum"] in (None, "ok", "bad")
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
    # docs/23 §4.5: a tram is one run cell sitting in a ring slot (READY/RUNNING) and a PARKED
    # run is off the road. The tram count is the ring's occupied slots — never one per tower.
    on_road = {cell["id"] for cell in towers if cell["run_state"] in ("READY", "RUNNING")}
    assert on_road and {tram["run"] for tram in scene["trams"]} == on_road
    assert all(tram["state"] in ("READY", "RUNNING") for tram in scene["trams"])
    assert len(scene["trams"]) == scene["ring"]["occupied"]
    parked = {cell["id"] for cell in towers if cell["run_state"] == "PARKED"}
    assert parked and parked.isdisjoint(on_road)  # the demo's parked run is off the road


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


# ---- cable packages ---------------------------------------------------------------------------------


def test_shared_street_stretches_become_one_package(scene):
    packages = scene["bundles"]
    assert packages, "the demo's cables share a street stretch"
    order = {edge["id"]: index for index, edge in enumerate(scene["edges"])}
    by_id = {edge["id"]: edge for edge in scene["edges"]}
    depth = {cell["id"]: cell["depth"] for cell in scene["cells"]}
    for package in packages:
        members = package["edges"]
        # exactly the cables it names, in edge order — and never a package of one
        assert package["count"] == len(members) >= 2
        positions = [order[member] for member in members]
        assert positions == sorted(set(positions))
        # the duct is one straight stretch of one street's centerline
        first, second = package["route"]
        assert first["x"] == second["x"] or first["z"] == second["z"]
        # marked by what it carries: one mark per cable
        assert sum(package["marks"].values()) == package["count"]
        assert set(package["marks"]) <= {"ref", "role", "field", "alias"}
        assert package["stale"] == sum(1 for member in members if not by_id[member]["fresh"])
        # the height class the duct rides at: the deepest module level of its members
        deepest = max(
            max(
                depth[by_id[member]["from"]],
                depth[by_id[member]["to"]] if by_id[member]["to"] else 0,
            )
            for member in members
        )
        assert package["level"] == deepest
    assert scene["counts"]["bundles"] == len(packages)


def test_a_package_shrinks_its_members_to_their_stubs(scene):
    carried = {member for package in scene["bundles"] for member in package["edges"]}
    assert carried
    for edge in scene["edges"]:
        for run in edge["stubs"]:
            assert len(run) >= 2 and all(set(point) == {"x", "z"} for point in run)
        if edge["id"] in carried:
            # what a package carries the cable does not draw again
            assert edge["stubs"] != [edge["route"]]
        else:
            # no package is invented where nothing is shared: the run stays the cable's own
            assert edge["stubs"] == [edge["route"]]


def test_validation_rejects_a_wrong_package(scene):
    package = scene["bundles"][0]
    for forged in (
        dict(package, count=package["count"] + 1),
        dict(package, level=package["level"] + 1),
        dict(package, stale=package["stale"] + 1),
        dict(package, marks={"nonsense": package["count"]}),
    ):
        with pytest.raises(SchemaError):
            validate_scene(dict(scene, bundles=[forged]))
    edge = dict(scene["edges"][0], stubs="nope")
    with pytest.raises(SchemaError):
        validate_scene(dict(scene, edges=[edge]))


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


def test_the_ring_road_reads_as_a_clean_boulevard(scene):
    """No needle spikes, no zigzag, no stutters: every leg is a leg and every turn is a
    chamfer-scale bend (the ragged corner artifacts were miter spikes on micro legs)."""
    path = [(point["x"], point["z"]) for point in scene["ring"]["path"]]
    count = len(path)
    assert count >= 8
    for index in range(count):
        previous, point, following = path[index - 1], path[index], path[(index + 1) % count]
        legs = (
            math.hypot(point[0] - previous[0], point[1] - previous[1]),
            math.hypot(following[0] - point[0], following[1] - point[1]),
        )
        assert min(legs) >= 0.25, f"ring point {index} sits on a micro leg"
        ux, uz = point[0] - previous[0], point[1] - previous[1]
        vx, vz = following[0] - point[0], following[1] - point[1]
        turn = math.degrees(math.atan2(ux * vz - uz * vx, ux * vx + uz * vz))
        assert abs(turn) < 100, f"ring point {index} is a spike ({turn:.1f}°)"


def test_the_ring_road_never_runs_through_a_building(scene):
    """Footprint-aware, in the document itself: no ring segment enters a building volume."""
    path = [(point["x"], point["z"]) for point in scene["ring"]["path"]]

    def hits(a: tuple[float, float], b: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
        x0, z0, x1, z1 = rect
        low, high = 0.0, 1.0
        for p, q, edge_low, edge_high in (
            (a[0], b[0] - a[0], x0, x1),
            (a[1], b[1] - a[1], z0, z1),
        ):
            if abs(q) < 1e-12:
                if p < edge_low or p > edge_high:
                    return False
                continue
            enter, leave = (edge_low - p) / q, (edge_high - p) / q
            if enter > leave:
                enter, leave = leave, enter
            low, high = max(low, enter), min(high, leave)
            if high <= low:
                return False
        return True

    for cell in scene["cells"]:
        half = float(cell["footprint"]) / 2 + 0.3
        x, z = float(cell["plot"]["x"]), float(cell["plot"]["z"])
        rect = (x - half, z - half, x + half, z + half)
        for a, b in zip(path, path[1:] + path[:1]):
            assert not hits(a, b, rect), f"the ring cuts through {cell['id']}"


def test_the_hud_metrics_are_wired_where_the_image_knows_them(scene):
    metrics = scene["metrics"]
    assert metrics["runs"] == scene["counts"]["towers"]  # available: wired
    # filled from words the image really carries (docs/20 S1/S8, read from the cells): the demo's
    # C_FORWARD compaction records and its three real run checksums
    assert metrics["moved_cells"] == 4
    assert metrics["checksum_status"] == "3 ok"
    assert scene["ring"]["state"] == {
        "FREE": 0, "READY": 1, "RUNNING": 1, "PARKED": 1, "TERMINAL": 0,
    }
    assert scene["ring"]["occupied"] == len(scene["trams"])
    # clean seams: the counters live outside every cell (a run of the machine keeps them), so no
    # image can carry them and the numbers stay null — never invented
    for seam in ("carriers", "steps_per_s", "claim_spins", "refusals"):
        assert metrics[seam] is None
    assert scene["carriers"] == []


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
    # no run words in the image: no numbers invented from their absence
    assert world["ring"]["state"] is None and world["ring"]["occupied"] == 0
    assert world["metrics"]["checksum_status"] is None
    assert world["metrics"]["moved_cells"] == 0


# ---- the page assets -------------------------------------------------------------------------------


def test_the_template_and_scripts_load_only_local_assets():
    html = WORLD_PAGE.read_text(encoding="utf-8")
    assert "http://" not in html and "https://" not in html and "cdn" not in html.lower()
    # the assets the page loads: <link>/<script> references. The shell's nav anchors are
    # navigation, not asset loads (the page is one surface of the ONE shell, docs/21), and the
    # shell's own chrome comes from the app's /static/ui mount — the same service, still local.
    shell_assets = {"/static/ui/ui.css", "/static/ui/ui.js"}
    references = [
        m.group(1) or m.group(2)
        for m in re.finditer(
            r'<link rel="stylesheet" href="([^"]+)"|<script src="([^"]+)"', html
        )
    ]
    for reference in references:
        if reference in shell_assets:
            continue
        assert not reference.startswith(("/", "http", "//")), reference
        # cache-bust queries (?v=...) are the same local file — strip before the file check
        assert (WORLD_DIR / reference.split("?")[0]).is_file(), reference
    config_match = re.search(r'id="rxf-world-config">\s*(\{.*?\})\s*<', html, re.DOTALL)
    assert config_match is not None
    config = json.loads(config_match.group(1))
    assert config["sceneUrl"].split("?")[0] == "scene.json"  # served by the /world/ route
    assert config["binUrl"].split("?")[0] == "scene.bin"  # the raw image, same route
    assert (WORLD_DIR / config["readerUrl"].split("?")[0]).is_file()  # the reader the page imports
    assert (WORLD_DIR / config["workerUrl"].split("?")[0]).is_file()  # the reader's own thread
    assert VENDOR_BABYLON.is_file()
    assert (VENDOR_BABYLON.parent / "license.md").is_file()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: path.name)
def test_the_scene_scripts_parse(script: Path):
    result = subprocess.run(
        ["node", "--check", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("script", ESM_SCRIPTS, ids=lambda path: path.name)
def test_the_scene_modules_parse(script: Path):
    """The reader is an ES module (the page imports it on the binary lane): checked in the
    mode it is loaded in, never as a classic script."""
    result = subprocess.run(
        ["node", "--input-type=module", "--check"],
        input=script.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_scene_scripts_stay_deterministic():
    for script in (*SCRIPTS, *ESM_SCRIPTS):
        source = script.read_text(encoding="utf-8")
        assert "Math.random(" not in source
        assert "http://" not in source and "https://" not in source


def test_the_click_ray_accepts_the_vendored_babylon_ray_shape():
    """Vendored Babylon 9.30's createPickingRay hands back the Ray itself, not a
    {ray: Ray} wrapper — the click handler must normalize before dereferencing,
    or every canvas click throws (found by the README capture lane)."""
    source = (STATIC_DIR / "scene" / "rxf-world.js").read_text(encoding="utf-8")
    assert "picked.ray || picked" in source
    assert not re.search(r"\bpick\.ray\.", source)


# ---- the wiring seam -------------------------------------------------------------------------------


def test_attach_world_registers_the_routes():
    from fastapi.routing import APIRoute, Mount

    from pymergetic.rxftk.server.app import create_app

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


# ---- the scene session: the layers, one slice at a time -------------------------------------------


def test_the_layers_compute_on_demand_never_the_whole_blob():
    """The session's contract (the user's "no blob"): the page asks for the layer it wants
    next and the host computes just that. Asking for the buildings computes neither cables
    nor streets; the cables compute when the cables are asked for (their route and their
    packing are one computation over the same street runs — a cable's drawn legs are a
    product of the ducts that carry it)."""
    stages: list[str] = []
    layers = Layers(demo_inspector(), report=lambda stage, done, total: stages.append(stage))
    layers.cells()
    assert not {"streets", "routing", "packages"} & set(stages), (
        "asking for the buildings computed the cables or the streets"
    )
    layers.routed()
    assert "routing" in stages, "the cables did not compute on demand"
    assert "packages" not in stages, "asking for the cables computed the ducts"
    layers.bundles()
    assert "packages" in stages, "the ducts did not compute on demand"


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    attach_world(app, demo_inspector())
    return TestClient(app)


def test_the_session_manifest_lists_the_plan_and_the_districts():
    manifest = _client().get("/world/scene.json?parts=1").json()
    assert manifest["schema"] == SCHEMA
    assert manifest["plan"] == [
        "cells",
        "voids",
        "blocks",
        "streets",
        "frame",
        "edges",
        "bundles",
        "stubs",
        "counts",
    ]
    assert manifest["slice"] >= 1
    assert manifest["regions"] == export_world(demo_inspector())["regions"]


def test_the_session_slices_reassemble_the_document():
    """The slices are the document, paged: whatever the page pulls slice by slice equals what
    the one-shot face serves — one truth, two shapes. The packing's verdict per cable (the
    ``stubs``) rides the ``stubs`` part and folds back onto the cables."""
    client = _client()
    whole = export_world(demo_inspector())
    pulled: dict[str, list] = {name: [] for name in ("cells", "voids", "blocks", "streets", "edges", "bundles")}
    for name in pulled:
        at = 0
        while True:
            part = client.get(
                f"/world/scene.json?part={name}&offset={at}&limit=40"
            ).json()
            assert part["total"] == len(whole[name])
            pulled[name].extend(part["items"])
            at += part["count"]
            if at >= part["total"]:
                break
    stubs = client.get("/world/scene.json?part=stubs").json()["value"]
    for edge in pulled["edges"]:
        edge["stubs"] = stubs.get(edge["id"])
    for name in pulled:
        assert pulled[name] == whole[name], f"the {name} slices do not reassemble"
    frame = client.get("/world/scene.json?part=frame").json()["value"]
    assert frame["ring"] == whole["ring"] and frame["metrics"] == whole["metrics"]
    counts = client.get("/world/scene.json?part=counts").json()["value"]
    assert counts == whole["counts"]


def test_a_session_slice_can_be_one_district():
    """The spatial pull: `region=` is what a viewport-driven load asks for — the buildings of
    one district, nothing else."""
    client = _client()
    whole = export_world(demo_inspector())
    region = max(whole["regions"], key=lambda item: item["cell_count"])
    part = client.get(f"/world/scene.json?part=cells&region={region['id']}&limit=500").json()
    assert part["total"] == region["cell_count"] > 0
    assert {cell["region"] for cell in part["items"]} == {str(region["id"])}
    assert part["items"] == [cell for cell in whole["cells"] if cell["region"] == str(region["id"])]


def test_the_session_answers_honestly_on_a_bad_part():
    client = _client()
    assert client.get("/world/scene.json?part=nope").status_code == 404
    progress = client.get("/world/scene.json/progress").json()
    assert set(progress) == {"state", "stage", "done", "total", "elapsed"}
