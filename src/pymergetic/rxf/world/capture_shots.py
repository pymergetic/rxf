"""Re-capture the world README shots: headless, deterministic, palette-quantized.

One script, one truth: it serves ``static/scene`` from this package over loopback, drives the
same page the user opens (WebGL only — no network), freezes the tram animation
(``setMotion(false)``), flies the named screenshot poses, and writes the PNGs with a fixed
palette quantization so the files stay small and byte-reproducible.

The raster is probed, not assumed: a machine with a GPU stack renders through it (the full
Chromium's hardware WebGL first), and only where no GPU stack answers does the capture fall
back to SwiftShader — it prints which raster drew the shots and never pretends otherwise.

This is a capture tool, not runtime code: it runs on the system Python (Playwright + Pillow),
not the package venv.

    python -m pymergetic.rxf.world.capture_shots [--out DIR] [--colors N] [--scene demo|starter]

``--scene`` is what the capture runs against. ``demo`` (the default) is the checked-in demo
golden — the world the README pins. ``starter`` is the REAL starter image (``starter/``),
exported live through the package in the same run: the capture path against real data, which
needs the package importable in the capture interpreter and falls back to the demo scene with a
note where it is not. The README shots stay on the demo scene: their captions name it.

Shots (the pins of the README, plus the text proof at the extreme angles):

    world-overview.png       pose "fly-ne"    the hero city view (diagonal ring, readable labels)
    world-tactical.png       pose "tactical"  top-down on a selected run tower, HUD fields filled
    world-proof-topdown.png  raw view         pitch 90 straight down: name labels + checksums
    world-proof-inspect.png  raw view         steep inspect (pitch 68): name labels + checksums
    world-ring.png           raw view         the ring road's corner: clean chamfers, no spikes
    world-streets.png        raw view         the street paint: flat markings between the blocks
    world-datasheet.png      pose "inspect"   the selected cell's datasheet pane open
"""

# pyright: reportMissingImports=false
# (Playwright and Pillow are the capture host's tools, installed outside the package venv)

from __future__ import annotations

import argparse
import functools
import http.server
import io
import json
import socketserver
import threading
from pathlib import Path
from typing import Any

WORLD_DIR = Path(__file__).resolve().parent
PKG_ROOT = WORLD_DIR.parents[3]  # packages/rxf
GOLDEN = WORLD_DIR / "static" / "scene" / "demo-scene.json"
STARTER = PKG_ROOT / "starter"
DEFAULT_OUT = PKG_ROOT / "docs" / "images"

SHOTS = (
    "world-overview.png",
    "world-tactical.png",
    "world-proof-topdown.png",
    "world-proof-inspect.png",
    "world-ring.png",
    "world-streets.png",
    "world-datasheet.png",
)
TOWER_PATH = "system.runs.tick"  # the run tower the README's tactical shot selects


def focus_views(doc: dict[str, Any]) -> dict[str, dict[str, float]]:
    """The deterministic close-up views of the proof shots: the ring road's farthest corner
    (the chamfers), and the middle of the first avenue (the street paint)."""
    ring = doc["ring"]["path"]
    center = doc["ring"]["center"]
    corner = max(
        ring,
        key=lambda point: (point["x"] - center["x"]) ** 2 + (point["z"] - center["z"]) ** 2,
    )
    avenue = next(
        (street for street in doc["streets"] if street["kind"] == "avenue"),
        doc["streets"][0],
    )
    return {
        "ring": {"x": corner["x"], "z": corner["z"], "altitude": 48, "pitch": 68, "yaw": 35},
        "streets": {
            "x": (avenue["from"] + avenue["to"]) / 2,
            "z": avenue["at"],
            "altitude": 34,
            "pitch": 60,
            "yaw": 25,
        },
    }


def serve(root: Path) -> tuple[socketserver.ThreadingTCPServer, int]:
    """A quiet loopback file server for the scene assets (no network, no cache games)."""

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass

    handler = functools.partial(Quiet, directory=str(root))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def quantized(screenshot: bytes, colors: int) -> bytes:
    """Palette-quantized PNG: small, deterministic, same look as the other doc images."""
    from PIL import Image

    image = Image.open(io.BytesIO(screenshot)).convert("RGB")
    palette = image.quantize(colors=colors, dither=Image.Dither.FLOYDSTEINBERG)
    out = io.BytesIO()
    palette.save(out, format="PNG", optimize=True)
    return out.getvalue()


def settle(page: Any, frames: int = 3) -> None:
    """Let the render loop draw `frames` deterministic frames before reading pixels."""
    page.evaluate(
        "frames => new Promise((resolve) => {"
        "  let seen = 0;"
        "  const tick = () => (++seen >= frames ? resolve(seen) : requestAnimationFrame(tick));"
        "  requestAnimationFrame(tick);"
        "})",
        frames,
    )


def steady_shot(page: Any, frames: int = 24) -> bytes:
    """A screenshot of a frame that is done moving: shoot, draw, shoot again, and only accept
    the bytes once two shots `frames` apart are identical (the first shot after a pose change
    can land mid-damping while the engine warms up). Bounded — determinism, not a hang.
    The timeout is generous because software WebGL renders the shadowed city slowly."""
    last = page.screenshot(timeout=180000)
    for _ in range(2):
        settle(page, frames)
        again = page.screenshot(timeout=180000)
        if again == last:
            return again
        last = again
    return last


def scene_document(kind: str) -> tuple[bytes, dict[str, Any]]:
    """The scene bytes the page boots on and the document the poses read: ``demo`` is the
    checked-in golden, ``starter`` is the REAL starter image exported live in this run (the
    capture path against real data). Where the capture interpreter cannot import the package,
    the starter capture is not feasible here: the capture says so and runs the demo scene."""
    if kind == "starter":
        try:
            from pymergetic.rxf.core import tree
            from pymergetic.rxf.server.inspector import Inspector
            from pymergetic.rxf.world.export import scene_json
        except ImportError as error:  # the capture host's Python carries no compiled core
            print(f"the starter capture is not feasible here ({error}); capturing the demo scene")
        else:
            raw = scene_json(Inspector(tree.load(STARTER))).encode()
            return raw, json.loads(raw)
    raw = GOLDEN.read_bytes()
    return raw, json.loads(raw)


GPU_ARGS = [
    "--use-gl=angle",
    "--use-angle=gl",
    "--enable-gpu",
    "--ignore-gpu-blocklist",
    "--enable-unsafe-swiftshader",  # in-process fallback allowed; the probe still reports truth
]
SWIFTSHADER_ARGS = [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
]
SOFTWARE_MARKERS = ("swiftshader", "llvmpipe", "software")


def _webgl_renderer(page: Any) -> str:
    """The renderer string of the page's WebGL context: the honest name of the raster."""
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


def _launch_raster(playwright: Any) -> tuple[Any, str]:
    """Launch on the best raster this machine has: hardware WebGL first, SwiftShader last.

    Every candidate is probed on an empty page before a single byte of scene is loaded; a
    candidate that fails to launch or answers with a software raster is dropped for the next.
    The returned string names the raster that actually draws the shots."""
    candidates = (
        ("chromium-gpu", {"channel": "chromium", "args": GPU_ARGS}),
        ("chromium-gpu-headful", {"channel": "chromium", "headless": False, "args": GPU_ARGS}),
        ("swiftshader", {"args": SWIFTSHADER_ARGS}),
    )
    for name, kwargs in candidates:
        try:
            browser = playwright.chromium.launch(**kwargs)
            probe = browser.new_page()
            renderer = _webgl_renderer(probe)
            probe.close()
        except Exception as miss:  # noqa: BLE001 - a missing channel or GPU stack is an expected miss
            print(f"raster miss ({name}): {miss}")
            continue
        if _is_software(renderer) and name != "swiftshader":
            browser.close()
            continue
        return browser, f"{name}: {renderer}"
    raise RuntimeError("no WebGL raster answered — neither a GPU stack nor SwiftShader")


def _is_software(renderer: str) -> bool:
    lowered = renderer.lower()
    return any(marker in lowered for marker in SOFTWARE_MARKERS)


def capture(out_dir: Path, colors: int, scene: str = "demo") -> list[Path]:
    from playwright.sync_api import sync_playwright

    raw, doc = scene_document(scene)
    tower = next(
        (cell for cell in doc["cells"] if cell.get("path") == TOWER_PATH),
        next(cell for cell in doc["cells"] if cell["family"] == "tower"),
    )
    server, port = serve(WORLD_DIR)
    written: list[Path] = []
    try:
        with sync_playwright() as playwright:
            browser, raster = _launch_raster(playwright)
            print(f"raster: {raster}")
            # the world page is ONE dark theme (the scene is always dark ground); shoot it in
            # the dark scheme so the chrome and the datasheet pane read as they are designed
            page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme="dark")
            # the page boots on the capture's scene: the same page and the same config, served
            # the document this run was asked for (demo golden or the real starter export)
            page.route(
                "**/demo-scene.json*",
                lambda route: route.fulfill(
                    status=200, content_type="application/json", body=raw
                ),
            )
            page.goto(f"http://127.0.0.1:{port}/static/scene/index.html")
            # the boot wait scales with the scene it boots on: the demo golden is one page,
            # a real image's export is megabytes of buildings and cables to lay out first
            page.wait_for_function(
                "window.RXFWorld && window.RXFWorld.api",
                timeout=30_000 + len(raw) // 1_000,
            )
            page.evaluate("RXFWorld.api.setMotion(false)")  # frozen trams: byte-stable frames

            plan = (
                ("world-overview.png", "fly-ne", None),
                ("world-tactical.png", "tactical", tower["id"]),
                (
                    "world-proof-topdown.png",
                    {"x": tower["plot"]["x"], "z": tower["plot"]["z"], "altitude": 34, "pitch": 90, "yaw": 0},
                    tower["id"],
                ),
                (
                    "world-proof-inspect.png",
                    {"x": tower["plot"]["x"], "z": tower["plot"]["z"], "altitude": 34, "pitch": 68, "yaw": 20},
                    tower["id"],
                ),
                ("world-ring.png", focus_views(doc)["ring"], None),
                ("world-streets.png", focus_views(doc)["streets"], None),
                (
                    "world-datasheet.png",
                    {"x": tower["plot"]["x"], "z": tower["plot"]["z"], "altitude": 30, "pitch": 58, "yaw": 30},
                    tower["id"],
                ),
            )
            for name, action, select in plan:
                if isinstance(action, str):
                    page.evaluate("pose => RXFWorld.api.setPose(pose, true)", action)
                else:
                    page.evaluate("view => RXFWorld.api.setView(view)", action)
                if select is not None:
                    page.evaluate("id => RXFWorld.api.highlight(id)", select)
                settle(page)
                shot = steady_shot(page)
                path = out_dir / name
                path.write_bytes(quantized(shot, colors))
                written.append(path)
            browser.close()
    finally:
        server.shutdown()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--colors", type=int, default=128, help="palette size for quantization")
    parser.add_argument(
        "--scene",
        choices=("demo", "starter"),
        default="demo",
        help="the scene to capture: the demo golden or the real starter image",
    )
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for path in capture(args.out, args.colors, args.scene):
        print(f"{path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
