"""The console seat's digest story, proven in a real browser against the real page.

The headless gate for the showcase's console pane. What is asserted is the story the
user sees: the ``digest`` command answers the image's canonical SHA-256 and the same
digest comes back before and after a read act; a modification stamps the strip with
the new digest and keeps the old one beside it; the write slice (STAGE_RESET,
STAGE_WRITE, STAGE_COMMIT) and COMPACT round-trip through the seat with their result
printed in the page - no silent failures; and the module tree and the object list
re-read the world after the acts with no manual reload.

Every wait is a readiness condition on the page (the command bar paints, the seat
prints "the image is up", the act's own answer line lands) - never a blind sleep.

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

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).parents[1]
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")
SHOTS = Path("/tmp/rxf-night")


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


def term(page: Page) -> str:
    """Everything the console's terminal has printed so far."""
    return page.locator("#term").inner_text()


def last_digest(page: Page) -> str:
    """The most recent 64-hex the terminal printed (the digest commands and acts)."""
    found = DIGEST.findall(term(page))
    assert found, "the terminal never printed a digest"
    return found[-1]


def stamped(page: Page) -> str:
    """The digest the strip shows after the acts (the full word, not the mini one)."""
    found = DIGEST.findall(page.locator("#digest-line").inner_text())
    assert found, "the digest strip shows no SHA-256"
    return found[0]


def press_digest(page: Page) -> str:
    """Click the digest command and wait for one more digest line in the terminal."""
    before = len(DIGEST.findall(term(page)))
    page.locator('#cmds .item[data-name="digest"]').click()
    page.wait_for_function(
        "n => (document.getElementById('term').textContent.match(/[0-9a-f]{64}/g) || [])"
        ".length > n",
        arg=before,
        timeout=90_000,
    )
    return last_digest(page)


def press(page: Page, pane: str, name: str, expect: str, timeout: int = 90) -> None:
    """Click one console item and wait for the act's own answer line to land."""
    before = term(page)
    page.locator(f'{pane} .item[data-name="{name}"]').click()
    page.wait_for_function(
        "want => document.getElementById('term').textContent.includes(want)",
        arg=expect,
        timeout=timeout * 1000,
    )
    after = term(page)
    assert len(after) > len(before), f"{name} printed nothing new"


def prove_round(page: Page) -> None:
    """The digest story: read, change, refresh, the write slice, compact."""
    # the read act: DIGEST answers the same word before and after
    first = press_digest(page)
    assert len(first) == 64
    assert press_digest(page) == first, "DIGEST changed the image digest"

    # the modification: the strip stamps the new digest, the old one beside it
    press(page, "#mods", "change a value", "now ")
    now = last_digest(page)
    assert now != first, "the act did not change the image"
    assert stamped(page) == now, "the strip is not the world's digest"
    was = page.locator("#digest-was").inner_text()
    assert first in was, "the strip does not keep the digest the act replaced"

    # the auto-refresh: the post-act read-back lands and the panes show the state
    page.wait_for_function(
        "want => {"
        " const line = document.getElementById('digest-line').textContent;"
        " const checked = document.getElementById('digest-checked').textContent;"
        " return line.includes(want) && !checked.includes('reading');"
        " }",
        arg=now,
        timeout=90_000,
    )
    checked = page.locator("#digest-checked").inner_text()
    assert "canonical bytes" in checked and "failed" not in checked, (
        f"the canonical bytes were not read back: {checked!r}"
    )
    assert page.locator("#tree .node").count() > 0, "the tree pane did not refresh"
    assert page.locator("#objects .row").count() > 0, "the object list did not refresh"

    # the write slice: the staged delta round-trips through the seat. Its base is
    # the digest the act replaced, so re-committing it is refused with the reason
    # printed - the stage protects the image and says why, never a silent failure
    press(page, "#channel", "stage reset", "stage_reset ok: the stage is cleared")
    press(page, "#channel", "stage write", "stage_write ok:")
    press(
        page,
        "#channel",
        "stage commit",
        "delta refused: the base digest is not the digest of the image",
    )
    assert stamped(page) == now, "a refused commit must not move the digest"

    # COMPACT is answered in the page either way: the seat's words when it compacts,
    # its explicit refusal when the seat has no compaction - never silence
    before = term(page)
    page.locator('#cmds .item[data-name="compact"]').click()
    page.wait_for_function(
        "() => { const t = document.getElementById('term').textContent;"
        " return t.includes('compact ok:') || t.includes('answered no frame to 35'); }",
        timeout=90_000,
    )
    assert len(term(page)) > len(before), "compact printed nothing new"

    # no silent failures anywhere: nothing in the terminal is an error line
    assert not re.search(r"status [1-9]", term(page)), "an act answered a failure"


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
            page.goto(f"http://127.0.0.1:{port}/")
            # readiness: the command bar paints and the seat is up
            page.wait_for_selector("#cmds .item", timeout=60_000)
            page.wait_for_function(
                "() => document.getElementById('term').textContent.includes('image is up')",
                timeout=60_000,
            )
            try:
                prove_round(page)
            except Exception:
                print("\n--- the console terminal said:\n" + term(page))
                print("--- digest-line:", page.locator("#digest-line").inner_text())
                print("--- digest-checked:", page.locator("#digest-checked").inner_text())
                raise
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(SHOTS / "console-prove.png"))
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)
