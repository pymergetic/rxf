"""A one-shot capture of the 3D world page: the scene, served, drawn, screenshotted.

The tool the top-row fix is proven with: it serves the world (the showcase's ``/world/``
page of the real tree by default, or the checked-in demo scene with ``--image demo``),
opens it in headless Chromium, waits for the scene to stand (``window.RXFWorld.api`` and no
``__rxErrors``), and writes one PNG.

    python tests/world_shot.py --shot /tmp/rxf-night/world-top-row.png

It is a tool, not a test: pytest collects only ``test_*.py``. The gates that assert on the
page live in ``tests/test_web_console_browser.py`` and ``tests/test_world_browser.py``.
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import uvicorn
# playwright is a seat-optional dependency (only a seat with a browser records
# shots): the checker resolves nothing on a seat that does not install it.
from playwright.sync_api import sync_playwright  # pyright: ignore[reportMissingImports]

ROOT = Path(__file__).parents[1]
if str(ROOT) not in sys.path:  # run as a tool: the tree's `tools` package lives here
    sys.path.insert(0, str(ROOT))

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.app import create_app

STATIC = ROOT / "src" / "pymergetic" / "rxftk" / "world" / "static"
STARTER = ROOT / "starter"

GPU_ARGS = [
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


class Quiet(SimpleHTTPRequestHandler):
    """The static server's face: the world's bytes, no log noise."""

    def log_message(self, format: str, *args: object) -> None:
        pass


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve(directory: Path) -> tuple[str, ThreadingHTTPServer, threading.Thread]:
    port = free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Quiet, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}/scene/index.html", server, thread


def serve_image(image: Image) -> tuple[str, uvicorn.Server, threading.Thread]:
    """The showcase's own face: the world app of one image, on a loopback socket."""
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(image), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise SystemExit("the world app did not come up")
        time.sleep(0.02)
    return f"http://127.0.0.1:{port}/world/", server, thread


def capture(
    url: str,
    shot: Path,
    width: int,
    height: int,
    settle: float,
    keys: list[str],
    evals: list[str],
    frames: int = 1,
) -> str:
    with sync_playwright() as playwright:
        browser = None
        renderer = ""
        for args in (GPU_ARGS, SWIFTSHADER_ARGS):
            try:
                browser = playwright.chromium.launch(args=args)
                page = browser.new_page(viewport={"width": width, "height": height})
                renderer = page.evaluate(
                    "() => { const gl = document.createElement('canvas').getContext('webgl');"
                    " if (!gl) return 'no-webgl';"
                    " const ext = gl.getExtension('WEBGL_debug_renderer_info');"
                    " return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)"
                    " : String(gl.getParameter(gl.RENDERER)); }"
                )
                page.close()
                break
            except Exception:  # noqa: BLE001 - a missing raster is an expected miss
                if browser:
                    browser.close()
                browser = None
        if browser is None:
            raise SystemExit("no chromium for playwright here")
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url)
        page.wait_for_function("window.RXFWorld && window.RXFWorld.api", timeout=240_000)
        page.wait_for_timeout(int(settle * 1000))
        canvas = page.locator("#rxf-world-canvas")
        for key in keys:
            canvas.press(key)
            page.wait_for_timeout(400)
        for expression in evals:
            print(f"eval {expression} ->", page.evaluate(expression))
            page.wait_for_timeout(200)
        errors = page.evaluate("() => (window.__rxErrors || []).slice()")
        for frame in range(max(1, frames)):
            out = shot if frames == 1 else shot.with_name(f"{shot.stem}-{frame}.png")
            page.wait_for_timeout(150)
            page.screenshot(path=str(out))
            print("shot:", out)
        browser.close()
    if errors:
        raise SystemExit(f"the page raised: {errors}")
    return renderer


def main() -> None:
    parser = argparse.ArgumentParser(description="screenshot the 3D world scene")
    parser.add_argument("--shot", type=Path, required=True, help="the PNG to write")
    parser.add_argument(
        "--image",
        choices=("starter", "empty", "demo"),
        default="starter",
        help="the world to shoot: the real starter tree (the showcase), a bare image, "
        "or the checked-in demo scene",
    )
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--settle", type=float, default=3.0, help="seconds after boot")
    parser.add_argument(
        "--key",
        action="append",
        default=[],
        help="a key to press on the canvas before the shot (e.g. --key o for ortho)",
    )
    parser.add_argument(
        "--eval",
        action="append",
        default=[],
        help="a JS expression to run on the page before the shot (diagnosis)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=1,
        help="capture this many frames (shot-0.png, shot-1.png, ...) to see z-fight flicker",
    )
    args = parser.parse_args()

    if args.image == "demo":
        url, server, thread = serve(STATIC)
    else:
        image = tree.load(STARTER) if args.image == "starter" else Image.empty()
        url, server, thread = serve_image(image)
    try:
        renderer = capture(
            url,
            args.shot,
            args.width,
            args.height,
            args.settle,
            args.key,
            args.eval,
            args.frames,
        )
    finally:
        if isinstance(server, uvicorn.Server):
            server.should_exit = True
        else:
            server.shutdown()
        thread.join(timeout=30)
    print(f"raster: {renderer}")
    print(f"shot: {args.shot}")


if __name__ == "__main__":
    sys.exit(main())
