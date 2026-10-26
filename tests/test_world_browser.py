"""The world page boots from the raw image bytes in a real browser: the binary lane, end to end.

The page's fast lane is ``GET /world/scene.bin`` — the arena verbatim — plus one reader file
(``static/scene/rxf-reader.js``): the host exports nothing on the page's path. These gates
drive headless Chromium at a served app and pin exactly that: the world builds from the bytes
and NOT ONE request touches the JSON session (``scene.json``) — the export is off the page's
path, not merely cached away.

Two scales, two truths: the lane itself is pinned on the bare ground image (any raster can
draw it), and the real ``starter/`` tree — 17,924 cells, 79,711 cables, the image every image
test runs — is booted where a GPU raster answers (the software raster cannot draw a city that
size inside a gate; the capture tool says which raster drew a shot, and so does this). Where
no browser is installed at all, the file skips: the JSON pins and the Node reader pin stand.
"""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

playwright_sync = pytest.importorskip(
    "playwright.sync_api", reason="playwright is not installed"
)

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.app import create_app
from pymergetic.rxftk.server.inspector import Inspector

pytestmark = pytest.mark.live

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"

GPU_ARGS = [
    "--use-gl=angle",
    "--use-angle=gl",
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",
]
VULKAN_ARGS = [
    "--use-gl=angle",
    "--use-angle=vulkan",
    "--enable-features=Vulkan",
    "--disable-vulkan-surface",
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",
]
SWIFTSHADER_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
]
SOFTWARE_MARKERS = ("swiftshader", "llvmpipe", "software")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# the loopback face one test boots its page at: its url, its server, the thread running it
Served = tuple[str, uvicorn.Server, threading.Thread]


@pytest.fixture
def serve_app():
    """One app (of the caller's image) on a loopback socket, per test: the served face."""
    running: list[Served] = []

    def serve(image: Image) -> Served:
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(image), host="127.0.0.1", port=port, log_level="error"
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            assert time.monotonic() < deadline, "the serve runner did not come up"
            time.sleep(0.02)
        started = (f"http://127.0.0.1:{port}/world/", server, thread)
        running.append(started)
        return started

    yield serve
    for _, server, thread in running:
        server.should_exit = True
        thread.join(timeout=15)


def _renderer(page) -> str:
    """The honest name of the page's raster (the capture tool reads it the same way)."""
    return page.evaluate(
        """() => {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (!gl) return 'no-webgl';
        const ext = gl.getExtension('WEBGL_debug_renderer_info');
        return ext
            ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
            : String(gl.getParameter(gl.RENDERER));
        }"""
    )


def _launch(playwright, raster: str):
    """The named raster of the gate: gpu (gl), vulkan, or any (software last)."""
    candidates = {
        "gpu": [("chromium", GPU_ARGS), ("chromium", VULKAN_ARGS)],
        "any": [("chromium", GPU_ARGS), ("chromium", VULKAN_ARGS), (None, SWIFTSHADER_ARGS)],
    }[raster]
    for channel, args in candidates:
        try:
            browser = (
                playwright.chromium.launch(channel=channel, args=args)
                if channel
                else playwright.chromium.launch(args=args)
            )
            page = browser.new_page()
            renderer = _renderer(page)
            page.close()
        except Exception as miss:  # noqa: BLE001 - a missing raster is an expected miss
            continue
        if raster == "gpu" and any(m in renderer.lower() for m in SOFTWARE_MARKERS):
            browser.close()
            continue
        return browser, renderer
    return None, ""


def _boot(page, base: str, waited: list[str]) -> None:
    page.on("request", lambda request: waited.append(request.url))
    page.goto(base)
    page.wait_for_function("window.RXFWorld && window.RXFWorld.api", timeout=240_000)
    _scene_stood(page)


def _scene_stood(page) -> None:
    """``create()`` resolves ``api`` before a single frame runs, so a world that dies on a
    *per-frame* throw (a ``ReferenceError`` in the render loop) still "boots" and reads as a
    pass to a gate that only waits for ``api``. The truth is in the page's own failure face:
    the global ``__rxErrors`` collector (index.html) and the HUD's "scene failed" line. A
    world that errored did not stand."""
    page.wait_for_timeout(400)  # several frames: a per-frame throw lands here, not in create()
    state = page.evaluate(
        """() => {
        const selection = document.querySelector('[data-selection]');
        const overlay = document.querySelector('[data-loading]');
        return {
            errors: (window.__rxErrors || []).slice(),
            selection: selection ? selection.textContent : '',
            failed: !!(overlay && overlay.classList.contains('failed')),
        };
    }"""
    )
    assert not state["errors"], f"the page raised: {state['errors']}"
    assert "scene failed" not in state["selection"], (
        f"the scene failed to render: {state['selection']}"
    )
    assert not state["failed"], "the HUD marked the scene as failed"


def _asked_for(requested: list[str], path: str) -> bool:
    """Did any request hit this route? The config cache-busts with ?v=..., so match the path,
    not the whole URL."""
    return any(url.split("?", 1)[0].endswith(path) for url in requested)


def _counts(page) -> dict[str, str]:
    return page.evaluate(
        "() => Object.fromEntries("
        "['cells', 'edges', 'bundles', 'stale_edges'].map("
        "name => [name, document.querySelector('[data-count=\"' + name + '\"]').textContent]))"
    )


def test_the_page_boots_the_ground_image_from_the_raw_bytes(serve_app) -> None:
    """The lane, on the bare ground image: the bytes land, the reader lays the world out in
    the page — and the JSON export is never asked for."""
    image = Image.empty()
    cells = sum(1 for _ in Inspector(image).cells())
    base, _, _ = serve_app(image)
    requested: list[str] = []

    with playwright_sync.sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(args=SWIFTSHADER_ARGS)
        except playwright_sync.Error as miss:
            pytest.skip(f"no chromium for playwright here ({str(miss).splitlines()[0]})")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _boot(page, base, requested)
        counts = _counts(page)
        browser.close()

    assert _asked_for(requested, "/world/scene.bin"), (
        "the page never asked for the raw image bytes"
    )
    assert not _asked_for(requested, "/world/scene.json"), (
        "the page fell back to the JSON session instead of reading the bytes"
    )
    assert int(re.sub(r"\D", "", counts["cells"])) == cells
    assert re.sub(r"\D", "", counts["bundles"]) == "0", (
        "the client face carries ducts (the packing is a Python pass, never the page's)"
    )


def test_the_real_tree_boots_from_the_raw_bytes_on_a_gpu_raster(serve_app) -> None:
    """The scale the demo cannot show: the REAL `starter/` tree (17,924 cells, 79,711 cables,
    5.5 MB of arena) read in the browser and built into the world — on a GPU raster, which is
    what draws a city this size in a gate (software renders it, in hours not minutes)."""
    image = tree.load(STARTER)
    cells = sum(1 for _ in Inspector(image).cells())
    base, _, _ = serve_app(image)
    requested: list[str] = []

    with playwright_sync.sync_playwright() as playwright:
        browser, renderer = _launch(playwright, "gpu")
        if browser is None:
            pytest.skip("no GPU raster here — the software raster cannot draw the tree in a gate")
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        _boot(page, base, requested)
        counts = _counts(page)
        browser.close()

    print(f"raster: {renderer}")
    assert _asked_for(requested, "/world/scene.bin"), (
        "the page never asked for the raw image bytes"
    )
    assert not _asked_for(requested, "/world/scene.json"), (
        "the page fell back to the JSON session instead of reading the bytes"
    )
    assert int(re.sub(r"\D", "", counts["cells"])) == cells, (
        "the world the page built is not the inspected image"
    )
