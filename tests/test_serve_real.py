"""The web app serves the REAL image end to end: its world, and a real running image behind it.

Two things were demo before this file pinned them otherwise:

* the 3D world fell back to the checked-in synthetic scene (`world/static/scene/demo-scene.json`)
  no matter which image the app inspected — now `create_app` wires `attach_world` and
  `GET /world/scene.json` is the exported scene of the image under test, never the demo (the
  full-starter pin runs in the gate: `world/export.py` buckets the cell stream per district and
  finds the rail junctions and nearest rails through indexes, so a real image's scene exports in
  about a minute);
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

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live.front import identity
from pymergetic.rxf.server.app import PokeBody, SourceBody, create_app
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.world import scene_json

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
DEMO_GOLDEN = (
    ROOT
    / "src"
    / "pymergetic"
    / "rxf"
    / "world"
    / "static"
    / "scene"
    / "demo-scene.json"
)
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
    assert {"/world", "/world/", "/world/scene.json"} <= paths
    assert any(path.startswith("/world/static") for path in paths)
    page = TestClient(app).get("/world/", follow_redirects=False)
    assert page.status_code == 200


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
    demo = json.loads(DEMO_GOLDEN.read_text(encoding="utf-8"))
    assert served != demo, (
        "the world served the synthetic demo scene instead of the inspected image"
    )
    assert response.content != DEMO_GOLDEN.read_bytes()


def test_the_world_serves_the_scene_of_the_real_starter_tree_not_the_demo(image: Image):
    """The flagship pin: `GET /world/scene.json` of the real `starter/` tree equals its
    `scene_json` and is not the demo golden — two exports of a real image's scale, in the
    gate's normal time (the wiring is pinned above)."""
    response = TestClient(create_app(image)).get("/world/scene.json")
    assert response.status_code == 200
    served = response.json()
    assert served == json.loads(scene_json(Inspector(image)))
    demo = json.loads(DEMO_GOLDEN.read_text(encoding="utf-8"))
    assert served != demo, (
        "the world served the synthetic demo scene instead of the real image"
    )
    assert response.content != DEMO_GOLDEN.read_bytes()


# ---- B + C: the serve runner, end to end ----------------------------------------------------------


def test_the_serve_runner_boots_the_front_end_and_installs_for_real(image: Image):
    """`rxf server serve`'s runner, against the app's own routes: the front end of the image is
    seeded once (docs/19), the editor's install route compiles TRIPLE **through the running
    image's front end**, the Function shows up in the image, and the image's own interpreter
    runs it — a run is not a frame (docs/19)."""
    from pymergetic.rxf.server.live.runner import Runner

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
    from pymergetic.rxf.server.live.runner import Runner

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
