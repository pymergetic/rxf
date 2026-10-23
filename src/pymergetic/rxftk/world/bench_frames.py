"""Frame bench for the 3D world: boot time and frame time per zoom level.

The bench boots the world on a chosen scene, settles at the three standard zoom
levels, and reports the frame times an interactive session actually pays. It
prints its own ground truth with every run:

  * which scene (the demo world or the real starter export) and how many megabytes
    the page fetched (a probe must never mistake the demo for the real city);
  * which raster drew the frames (a GPU stack by preference, SwiftShader if that
    is all the machine has) — a frame number without its raster is a rumor;
  * the instance census: how many thin instances each mesh carries and which
    LOD level had them enabled.

The scene is served as a real file by the HTTP server (the page's own fetch,
no Playwright request interception): route marshaling a 73 MB body through the
browser driver adds a minute of transfer that has nothing to do with the world's
boot, and a bench that measures its own harness is worse than no bench.

Usage:
    .venv/bin/python -m pymergetic.rxftk.world.bench_frames --scene starter
"""

from __future__ import annotations

import argparse
import functools
import http.server
import socketserver
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .capture_shots import WORLD_DIR, _launch_raster, scene_document

# the three zoom levels the world's LOD ladder is tuned around: the strategic
# overview, the operational mid view, and the tactical close view
VIEWS = (
    ("L0 overview", {"altitude": 260, "pitch": 55, "yaw": 40}),
    ("L1 mid", {"altitude": 80, "pitch": 60, "yaw": 25}),
    ("L2 tactical", {"altitude": 26, "pitch": 68, "yaw": 20}),
)


class _SceneSwap(http.server.SimpleHTTPRequestHandler):
    """Serve the bench's scene file at the URL the page fetches (``scene.bin``, the raw ``.rxfb``)."""

    def __init__(self, *args, scene: Path, **kwargs):
        self._scene = scene
        super().__init__(*args, **kwargs)

    def send_head(self):
        if self.path.split("?")[0].endswith("/scene.bin"):
            self.path = "/__bench-scene.bin"
        return super().send_head()

    def translate_path(self, path: str) -> str:
        if path.endswith("/__bench-scene.bin"):
            return str(self._scene)
        return super().translate_path(path)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def _serve(scene: Path) -> tuple[socketserver.ThreadingTCPServer, int]:
    handler = functools.partial(_SceneSwap, scene=scene, directory=str(WORLD_DIR))
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _frame_samples(page, count: int) -> list[float]:
    """Frame deltas over `count` frames, sampled inside the page's own rAF loop."""
    return page.evaluate(
        """count => new Promise((resolve) => {
        const times = [];
        let last = performance.now();
        const tick = () => {
            const now = performance.now();
            times.push(now - last);
            last = now;
            if (times.length < count) requestAnimationFrame(tick);
            else resolve(times);
        };
        requestAnimationFrame(tick);
    })""",
        count,
    )


def bench(scene: str, frames: int) -> None:
    from playwright.sync_api import sync_playwright  # pyright: ignore

    raw, _doc = scene_document(scene)
    with tempfile.NamedTemporaryFile(suffix="-world-scene.bin") as handle:
        handle.write(raw)
        handle.flush()
        server, port = _serve(Path(handle.name))
        try:
            with sync_playwright() as playwright:
                browser, raster = _launch_raster(playwright)
                print(f"scene: {scene} ({len(raw) / 1e6:.1f} MB)  raster: {raster}")
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                started = time.time()
                page.goto(f"http://127.0.0.1:{port}/static/scene/index.html")
                page.wait_for_function(
                    "window.RXFWorld && window.RXFWorld.api",
                    timeout=180_000,
                )
                print(f"boot: {time.time() - started:.1f}s")
                page.evaluate("RXFWorld.api.setMotion(false)")  # frozen trams: no noise
                census = page.evaluate(
                    """() => {
                    const out = [];
                    for (const mesh of RXFWorld.api.scene.meshes) {
                        let n = 0;
                        try { n = mesh.thinInstanceCount || 0; } catch (e) {}
                        if (n > 0) out.push([mesh.name, n]);
                    }
                    out.sort((a, b) => b[1] - a[1]);
                    return out;
                }"""
                )
                print(f"thin instances: {sum(n for _, n in census)} in {len(census)} meshes")
                for name, n in census[:8]:
                    print(f"   {name:<28} {n:>8}")
                for name, view in VIEWS:
                    page.evaluate("v => RXFWorld.api.setView(v)", view)
                    time.sleep(1.0)  # settle past the level's first frame work
                    times = _frame_samples(page, frames)
                    warm = times[2:]  # the first two frames carry the level switch
                    print(f"{name:<14} mean {statistics.mean(warm):7.1f} ms  max {max(warm):7.1f} ms")
                browser.close()
        finally:
            server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=("demo", "starter"), default="demo")
    parser.add_argument("--frames", type=int, default=14, help="frames sampled per zoom level")
    args = parser.parse_args()
    bench(args.scene, args.frames)


if __name__ == "__main__":
    sys.exit(main())
