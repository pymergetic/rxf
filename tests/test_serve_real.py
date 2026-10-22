"""The web app serves the REAL image end to end: its world, and a real running image behind it.

Two things were demo before this file pinned them otherwise:

* the 3D world fell back to the checked-in synthetic demo scene
  no matter which image the app inspected — now `create_app` wires `attach_world` and the world
  page loads the image under test: `GET /world/scene.bin` hands the browser the raw `.rxfb`
  (read in the page by `rxf-reader.js` — no export on the host at all), and the scene SESSION
  face stays beside it: `GET /world/scene.json?parts=1` the plan, then the layers one after
  another, slice by slice (buildings, cables, ducts — computed on demand, never one blob).
  The full-starter pin runs in the gate at that shape:
  the buildings and cables of the real `starter/` tree, never the demo;
* the editor's compile / install / poke answered "no running image" — now `rxf server serve`
  runs one (`server/live/runner.py`: the linked image, its own interpreter and live leaf, the
  front end seeded once per docs/19), and those same routes run FOR REAL against it.

Nothing here is synthetic: the image is the `starter/` tree every image test runs, the words of
an installed program are the image's own (the host compiler only seeds once, docs/19), and a run
of the installed Function is the image's interpreter executing it.
"""

from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live.front import identity
from pymergetic.rxftk.server.app import PokeBody, SourceBody, create_app
from pymergetic.rxftk.server.inspector import Inspector
from pymergetic.rxftk.world import scene_json
from pymergetic.rxftk.world.demo import demo_inspector

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
# The synthetic demo, computed live — the anti-demo-drift reference. It is never a checked-in
# file: the world must serve the inspected image, not this (so the tests compare against the
# demo's own content, not a stale golden on disk).
DEMO_SCENE = json.loads(scene_json(demo_inspector()))
DEMO_JSON = scene_json(demo_inspector()).encode()
DEMO_IMAGE = demo_inspector().image.to_bytes()
MODULE = "pymergetic.rxf.selfedit"

TRIPLE = """\
program triple(x) {
    return x + x + x;
}
"""

POKE_PATH = "pymergetic.rxf.application.main"


@pytest.fixture(scope="module")
def image() -> Image:
    """The starter, the same ground truth every image test runs."""
    return tree.load(STARTER)


def _link() -> str:
    """One link per test: the registry is per process and the suite shares it."""
    return f"shm:serve-real-{uuid.uuid4().hex[:8]}"


# ---- A: the world of the real image, not the demo scene -----------------------------------------


def test_the_world_of_the_real_starter_app_is_wired_to_its_inspector(image: Image):
    """The flagship app (`create_app` over the real `starter/` tree) carries the world seam:
    `/world`, `/world/scene.json` and `/world/static` come from `attach_world(app, inspector)` —
    the world of the image under inspection, wired after the routes."""
    app = create_app(image)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {"/world", "/world/", "/world/scene.bin", "/world/scene.json"} <= paths
    assert any(path.startswith("/world/static") for path in paths)
    page = TestClient(app).get("/world/", follow_redirects=False)
    assert page.status_code == 200


def test_the_world_serves_the_raw_image_for_the_browser_reader(image: Image):
    """`GET /world/scene.bin` is the image itself: the raw `.rxfb` bytes the page reads in the
    browser with `static/scene/rxf-reader.js` — the very bytes `Image.to_bytes()` hands out
    (the arena verbatim, never a projection, never the demo scene). The reader is served too:
    the fast lane is bytes plus one reader file, nothing else."""
    client = TestClient(create_app(image))
    response = client.get("/world/scene.bin")
    assert response.status_code == 200
    assert response.content == image.to_bytes()
    assert response.content[:4] == b"RXFB"
    assert response.content != DEMO_IMAGE
    reader = client.get("/world/static/scene/rxf-reader.js")
    assert reader.status_code == 200
    assert "export function readWorld" in reader.text


def test_the_world_serves_the_scene_of_the_inspected_image_not_the_demo():
    """`GET /world/scene.json` is the exported scene of the image the app inspects — the very
    `scene_json` of that image — and is NOT the checked-in demo scene (the anti-demo-drift pin:
    the demo golden may not stand in for real data). The bare ground image is a real image the
    export answers for today; the pin on the full `starter/` tree is the next test."""
    image = Image.empty()
    response = TestClient(create_app(image)).get("/world/scene.json")
    assert response.status_code == 200
    served = response.json()
    assert served == json.loads(scene_json(Inspector(image)))
    demo = DEMO_SCENE
    assert served != demo, (
        "the world served the synthetic demo scene instead of the inspected image"
    )
    assert response.content != DEMO_JSON


def _pull(client: TestClient, name: str) -> list[dict]:
    """One layer, slice by slice — the way the page loads it (the session's shape)."""
    items: list[dict] = []
    at = 0
    while True:
        part = client.get(f"/world/scene.json?part={name}&offset={at}&limit=2000").json()
        items.extend(part["items"])
        at += part["count"]
        if at >= part["total"]:
            return items


def test_the_world_session_serves_the_real_starter_tree_not_the_demo(image: Image):
    """The flagship pin, at the session's shape (the layer protocol the world page speaks):
    the parts of the real `starter/` tree are the real image's scale — every cell of the
    stream, every cable of the refs, routed — and are NOT the checked-in demo scene (the
    anti-demo-drift pin: the demo golden may not stand in for real data). The layers come
    one after another, slice by slice: the host never has to hand out one blob."""
    client = TestClient(create_app(image))
    manifest = client.get("/world/scene.json?parts=1").json()
    demo = DEMO_SCENE
    assert manifest["regions"] != demo["regions"], "the session served the demo's districts"

    stream = list(Inspector(image).cells())
    buildings = _pull(client, "cells")
    assert len(buildings) == len(stream) > len(demo["cells"])
    assert {cell["handle"] for cell in buildings if cell["handle"] is not None} == {
        int(cell["handle"]) for cell in stream if cell["handle"] is not None
    }

    cables = _pull(client, "edges")
    assert cables and len(cables) != demo["counts"]["edges"]
    assert all("route" in cable for cable in cables)
    assert any(cable["route"] for cable in cables), "the cables came unrouted"


# ---- B + C: the serve runner, end to end ----------------------------------------------------------


def test_the_serve_runner_boots_the_front_end_and_installs_for_real(image: Image):
    """`rxf server serve`'s runner, against the app's own routes: the front end of the image is
    seeded once (docs/19), the editor's install route compiles TRIPLE **through the running
    image's front end**, the Function shows up in the image, and the image's own interpreter
    runs it — a run is not a frame (docs/19)."""
    from pymergetic.rxftk.server.live.runner import Runner

    with Runner(image, _link()) as runner:
        assert [entry.path for entry in runner.front.installed()] == [f"{MODULE}.front"]

        client = TestClient(create_app(image, session=runner.session))
        installed = client.post(
            "/api/editor/install", json=SourceBody(source=TRIPLE).model_dump()
        ).json()
        assert installed["ok"] is True
        assert installed["path"] == f"{MODULE}.triple"

        # it shows up in the RUNNING image (what a console would see at the link)
        paths = {entry.path for entry in runner.front.installed()}
        assert f"{MODULE}.triple" in paths

        # and the installed Function answers, executed by the image's own interpreter
        raw, _ = runner.client.emit()
        running = Image.from_bytes(raw)
        wanted = identity(running, running.handle(f"{MODULE}.triple.bytecode"))
        assert runner.machine.run(wanted, struct.pack("<Q", 7)) == (0, 21)


def test_an_editor_poke_writes_to_the_running_image_and_refuses_without_one(
    image: Image,
):
    """One poke, two apps: with a session the poke is a real delta into the running image (the
    word is readable back through the channel); without one the very same poke is the honest
    refusal — no second channel, no pretending."""
    from pymergetic.rxftk.server.live.runner import Runner

    poke = PokeBody(path=POKE_PATH, field="payload", value=0xA5A5A5A5A5A5A5A5, offset=0)

    without = (
        TestClient(create_app(image))
        .post("/api/editor/poke", json=poke.model_dump())
        .json()
    )
    assert without["ok"] is False
    assert "no running image" in without["refusal"]

    with Runner(image, _link()) as runner:
        client = TestClient(create_app(image, session=runner.session))
        out = client.post("/api/editor/poke", json=poke.model_dump()).json()
        assert out["ok"] is True and out["field"] == "payload"
        assert out["digest"]

        # the write is in the running image: read the word back through its channel
        handle = runner.client.resolve(POKE_PATH)
        assert handle is not None
        (written,) = struct.unpack("<Q", runner.client.payload(handle)[:8])
        assert written == 0xA5A5A5A5A5A5A5A5
        assert out["digest"] == runner.client.digest()


def test_the_install_refusal_stays_honest_without_a_session(image: Image):
    """The honest refusals stay where they were: with no session, compile and install refuse
    plainly instead of inventing a machine to run on."""
    client = TestClient(create_app(image))
    for route in ("/api/editor/compile", "/api/editor/install"):
        out = client.post(route, json=SourceBody(source=TRIPLE).model_dump()).json()
        assert out["ok"] is False
        assert "no running image" in out["refusal"]
