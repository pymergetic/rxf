"""Capture the web interface's semantic highlighting: headless, deterministic, quantized.

One script, one truth: it serves the REAL image (``rxf server serve starter`` — the linked
image, its own interpreter and front end) on loopback, drives the same pages the user opens
(DOM only — no GPU, no network), lets each pane settle on its answer, and writes the PNGs with
a fixed palette quantization so the files stay small and byte-reproducible.

This is a capture tool, not runtime code: it runs on the system Python (Playwright + Pillow),
not the package venv — the app itself serves from the venv's ``rxf``. It lives under
``server/static/ui/`` because that tree is the highlighter lane's (like ``shell.html``, it is
never fetched by a browser).

    python3 src/pymergetic/rxftk/server/static/ui/capture_shots.py [--out DIR] [--colors N]

Shots (the proof of the semantic register, one per language the panes declare):

    web-ui-editor.png     /editor          the source mirror in the rxfs register under the
                                           live check's marks (green known, red refused)
    web-ui-diagnostics.png /editor         what a refusal looks like: red marks that carry
                                           their reason, and the badges that count them
    web-ui-delta.png      /editor          the delta install would send: the rxfd register
                                           (tags are Type names, values say RefKind/Form/...)
    web-ui-faces.png      /editor          a function's C face in the c register (pseudo and
                                           python are the same pane, their own registers)
    web-ui-inspector.png  /?path=…         an object's detail: handles, paths and RefKind
                                           chips in the text register (docs/21, docs/15)
"""

# pyright: reportMissingImports=false
# (Playwright and Pillow are the capture host's tools, installed outside the package venv)

from __future__ import annotations

import argparse
import io
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

UI_DIR = Path(__file__).resolve().parent
PKG_ROOT = UI_DIR.parents[5]  # packages/rxf
STARTER = PKG_ROOT / "starter"
VENV_RXF = PKG_ROOT / ".venv" / "bin" / "rxf"
DEFAULT_OUT = PKG_ROOT / "docs" / "images"

SHOTS = (
    "web-ui-editor.png",
    "web-ui-diagnostics.png",
    "web-ui-delta.png",
    "web-ui-faces.png",
    "web-ui-inspector.png",
)
FUNCTION = "pymergetic.rxf.algorithms.compare"  # a flat graph: real faces and real references
REFS_PATH = "pymergetic.rxf.foundation.StdlibRefusals"  # a RefusalSet: a full references table
SOURCE = """\
program double(x) {
    return x + x;
}
"""
BROKEN = "program broken(x) = mul(x, @ ) % 2"  # what the language refuses, and where


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def serve(link: str, port: int) -> subprocess.Popen[Any]:
    """`rxf server serve` on the starter tree: the app AND the running image behind it."""
    return subprocess.Popen(
        [
            str(VENV_RXF),
            "server",
            "serve",
            str(STARTER),
            f"--link={link}",
            f"--port={port}",
        ],
        cwd=str(PKG_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ready(port: int, process: subprocess.Popen[Any], timeout: float = 120.0) -> None:
    """Wait until the app answers (the linked image and its front end seed first). Bounded."""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"the serve exited early ({process.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as page:
                if page.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("the serve never answered")


def quantized(screenshot: bytes, colors: int) -> bytes:
    """Palette-quantized PNG: small, deterministic, same look as the other doc images."""
    from PIL import Image

    image = Image.open(io.BytesIO(screenshot)).convert("RGB")
    palette = image.quantize(colors=colors, dither=Image.Dither.FLOYDSTEINBERG)
    out = io.BytesIO()
    palette.save(out, format="PNG", optimize=True)
    return out.getvalue()


def settle(page: Any, frames: int = 3) -> None:
    """Let the page draw `frames` frames and finish its fonts before reading pixels."""
    page.evaluate(
        "frames => Promise.all([document.fonts.ready, new Promise((resolve) => {"
        "  let seen = 0;"
        "  const tick = () => (++seen >= frames ? resolve(seen) : requestAnimationFrame(tick));"
        "  requestAnimationFrame(tick);"
        "})])",
        frames,
    )


def steady_shot(page: Any, colors: int) -> bytes:
    """Shoot, settle, shoot again — accept the quantized bytes only once two shots agree
    (a paint, a font swap or a blinking caret must not land in the proof)."""
    page.evaluate("document.activeElement && document.activeElement.blur()")
    settle(page)
    last = page.screenshot(timeout=60000)
    for _ in range(2):
        settle(page)
        again = page.screenshot(timeout=60000)
        if again == last:
            return quantized(again, colors)
        last = again
    return quantized(last, colors)


def capture(out_dir: Path, colors: int) -> list[Path]:
    from playwright.sync_api import sync_playwright

    port = free_port()
    process = serve(f"shm:webui-shots-{os.getpid()}", port)
    written: list[Path] = []
    try:
        ready(port, process)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            # The live-slot feed (ui.js, another lane's) polls the channel every few seconds,
            # and the running image refuses a compile that lands after such activity: the front
            # end compiles its deltas against the base it was seeded with and the image has
            # moved on ("delta refused: something points at an object that is gone" — the app
            # answers 500; the live lane's seam, reported). The shots are of the highlighter's
            # panes, so the capture keeps the channel quiet and the live slot at its pinned
            # `no image` default (docs/21) — never a part of these pictures.
            page.route("**/api/live", lambda route: route.abort())
            base = f"http://127.0.0.1:{port}"

            # 1. the source mirror: the rxfs register on load (never a blank first paint),
            #    under the live check's marks.
            page.goto(f"{base}/editor")
            page.wait_for_function("window.RXFHighlight && document.querySelector('#hl .m-keyword')", timeout=30000)
            page.wait_for_function("document.querySelector('#check-status').innerText.includes('token')", timeout=30000)
            written.append(write(out_dir, "web-ui-editor.png", steady_shot(page, colors)))

            # 1b. the refusal side of the same mirror: red marks are refusals that carry their
            #     reason (hover), the badges count them — and the text register still colors
            #     what the bad line means.
            page.fill("#src", BROKEN)
            page.wait_for_function("document.querySelector('#check-status').innerText.includes('refused')", timeout=30000)
            written.append(write(out_dir, "web-ui-diagnostics.png", steady_shot(page, colors)))

            # 2. the delta that install would send: compile through the running image's front
            #    end and preview — the rxfd register over the real delta. Preview compiles on
            #    its own; never fire a second compile at the channel alongside it (the image
            #    refuses one of two racing deltas and the app answers 500).
            page.fill("#src", SOURCE)
            page.click("#preview")
            page.wait_for_function("document.querySelector('#delta pre.code[data-lang=\"rxfd\"] .m-tag')", timeout=30000)
            page.eval_on_selector("#delta", "el => el.scrollIntoView({block: 'center'})")
            written.append(write(out_dir, "web-ui-delta.png", steady_shot(page, colors)))

            # 3. the faces: the C face of a flat graph in the c register (pseudo and python are
            #    the same pane with their own registers).
            page.fill("#path", FUNCTION)
            page.click("#load")
            page.wait_for_function("document.querySelector('#face-out pre.code')", timeout=30000)
            page.click("[data-face=\"c\"]")
            page.wait_for_function("document.querySelector('#face-out pre.code[data-lang=\"c\"] .m-type')", timeout=30000)
            page.eval_on_selector("#face-out", "el => el.scrollIntoView({block: 'center'})")
            written.append(write(out_dir, "web-ui-faces.png", steady_shot(page, colors)))

            # 4. the inspector's detail: handles, object paths and RefKind chips (the text
            #    register over the prose around the tables).
            page.goto(f"{base}/?path={REFS_PATH}")
            page.wait_for_function("document.querySelector('#main .m-refkind')", timeout=30000)
            written.append(write(out_dir, "web-ui-inspector.png", steady_shot(page, colors)))

            browser.close()
    finally:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=30)
    return written


def write(out_dir: Path, name: str, shot: bytes) -> Path:
    path = out_dir / name
    path.write_bytes(shot)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    parser.add_argument("--colors", type=int, default=128, help="palette size for quantization")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for attempt in range(3):
        try:
            written = capture(args.out, args.colors)
            break
        except Exception:  # the live seam's intermittent refusal: one boot retry, then raise
            if attempt == 2:
                raise
            print("the live seam refused a compile; booting a fresh image…")
    for path in written:
        print(f"{path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
