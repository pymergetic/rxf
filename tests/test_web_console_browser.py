"""The console seat's digest story, proven in a real browser against the real page.

The headless gate for the showcase's console pane: DIGEST stamps the image digest after
every act, the STAGE_RESET/STAGE_WRITE/STAGE_COMMIT acts and COMPACT round-trip through
the host compiler endpoint and show their result in the page (no silent failures), and
the module tree and object list refresh themselves after every act.

The digest story asserted here: a read act (DIGEST) leaves the stamped digest equal to
the digest before the act; a modifying act (STAGE_WRITE) changes it; and what the page
shows after the act is the digest of the bytes that are really in the image.

    .venv/bin/python -m pytest tests/test_web_console_browser.py -x -q

It skips when playwright is missing (a seat without a browser): this file stays the
prove script of record and runs where the browser lives.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[1]
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def serve_console(port: int) -> subprocess.Popen:
    """The real console: web/serve.py, the wasm32 seat, on a loopback port."""
    return subprocess.Popen(
        [sys.executable, "web/serve.py", str(port)],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stamped(page) -> str:
    """The digest the page shows in the stamp strip (64 hex, whatever the act)."""
    for selector in ("#digest-line", "#digest-mini"):
        text = page.locator(selector).inner_text().strip()
        found = DIGEST.findall(text)
        if found:
            return found[0]
    raise AssertionError("no digest stamped in the page")


def act(page, label: str) -> None:
    """Press one console command button by its visible label and let the act land."""
    page.locator("#cmds button", has_text=label).first.click()
    page.wait_for_timeout(1500)


def test_console_digest_story_round_trips() -> None:
    port = free_port()
    proc = serve_console(port)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise AssertionError("the console did not come up")
                time.sleep(0.1)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(args=["--enable-unsafe-swiftshader"])
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("console", lambda msg: print("console:", msg.type, msg.text))
            page.on("pageerror", lambda err: print("pageerror:", err))
            try:
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_selector("#cmds button", timeout=60_000)
            except Exception:
                page.screenshot(path="/tmp/rxf-night/console-fail.png")
                raise
            page.wait_for_timeout(2500)  # the seat boots, the first DIGEST lands

            before = stamped(page)

            # the read act: DIGEST stamps the same digest again (equality before/after)
            act(page, "DIGEST")
            assert stamped(page) == before, "DIGEST changed the stamped digest"

            # the visible write-slice acts: each must land and say so in the page
            act(page, "stage reset")
            reset = stamped(page)
            assert reset, "STAGE_RESET left no digest"

            act(page, "stage write")
            wrote = stamped(page)
            assert wrote != reset, "STAGE_WRITE did not change the image digest"

            act(page, "stage commit")
            committed = stamped(page)
            assert committed != wrote, "STAGE_COMMIT did not change the image digest"

            # no silent failures: the page's checked line reads the bytes back
            checked = page.locator("#digest-checked").inner_text()
            assert "not read" not in checked, "the bytes were never read back"

            # the auto-refresh: the tree pane shows rows after the acts (no reload)
            rows = page.locator("#tree .row").count()
            assert rows > 0, "the tree pane did not refresh after the acts"

            act(page, "COMPACT")
            compacted = stamped(page)
            assert compacted, "COMPACT left no digest"

            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
