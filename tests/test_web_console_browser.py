"""The console seat's digest story, proven in a real browser against the real page.

The headless gate for the showcase's console pane. What is asserted is the story the
user sees: the ``digest`` command answers the image's canonical SHA-256 and the same
digest comes back before and after a read act; a modification stamps the strip with
the new digest and keeps the old one beside it; the write slice (STAGE_RESET,
STAGE_WRITE, STAGE_COMMIT) and COMPACT round-trip through the seat with their result
printed in the page - no silent failures; and the module tree and the object list
re-read the world after the acts with no manual reload.

The drag is the browser's own modification (U2): with a hand on a value the
digest and the byte diff move live while the pointer is down, the bytes the
browser holds are hashed against the word the seat answers at every settled
step ("checked against the digest" - refused bytes are never drawn), and when
the drag ends the panes re-read the seat's bytes after the last queued act.

Every wait is a readiness condition on the page (the command bar paints, the seat
prints "the image is up", the act's own answer line lands) - never a blind sleep.

    .venv/bin/python -m pytest tests/test_web_console_browser.py -x -q

It skips when playwright is missing or no browser binary is installed (a seat
without a browser): this file stays the prove script of record and runs where the
browser lives.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")

# playwright is a seat-optional dependency (the importorskip above): a seat
# without a browser does not install it, and the checker resolves nothing there.
from playwright.sync_api import (  # pyright: ignore[reportMissingImports]
    Page,
    sync_playwright,
)

ROOT = Path(__file__).parents[1]
DIGEST = re.compile(r"\b[0-9a-f]{64}\b")
# the leaf the console's own `set` acts name (web/acts.js's default path): the
# descent the object list makes to offer a draggable value
DRAG_PATH = "pymergetic.rxf.application.discount"
SHOTS = Path("/tmp/rxf-night")


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


CHROMIUM = (
    "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
    "chromium-*/chrome-linux64/chrome",
)


def browsers_path() -> str | None:
    """Where playwright must look for the browser binary, or None on a seat
    that has none.

    ``PLAYWRIGHT_BROWSERS_PATH`` can name a sandbox cache that is already gone
    while the real install sits in playwright's own default directory: the
    prove then dies on a missing executable before it tests anything. The
    lookup walks the configured path first and the default one second and
    answers the first root that actually holds a chromium - and only a seat
    with no browser anywhere skips, in words.
    """
    wanted = []
    if os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        wanted.append(Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"]))
    wanted.append(Path.home() / ".cache" / "ms-playwright")
    for root in wanted:
        if any(any(root.glob(pattern)) for pattern in CHROMIUM):
            return str(root)
    return None


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


# ---- the drag (U2): the browser digest while the hand moves --------------------------------------


def find_draggable(page: Page):
    """A value a hand can drag: walk the object list down the module path the
    console's own `set` act names (``DRAG_PATH``) and the leaf is offered - the
    page marks every draggable value with its path (``data-drag``)."""
    for segment in DRAG_PATH.split(".")[:-1]:
        rows = page.locator("#objects .row")
        names = [rows.nth(at).locator(".name").inner_text() for at in range(rows.count())]
        assert segment in names, f"the object list offers no {segment} (only {names})"
        rows.nth(names.index(segment)).click()
    values = page.locator("#objects .value[data-drag]")
    rows = page.locator("#objects .row")
    seen = [
        rows.nth(at).inner_text().replace("\n", " | ") for at in range(rows.count())
    ]
    assert values.count() > 0, f"the leaf the acts name is not draggable in the panes: {seen}"
    return values.first


def prove_drag(page: Page) -> None:
    """A drag shows the byte diff and the digest updating live. The pin runs at
    every settled step: the canonical bytes the browser holds are hashed in the
    browser against the word the seat answers, and the diff of the two pins says
    which bytes moved - never the act's word about itself. At the end of the
    drag the panes re-read after the last queued act, so what they draw is the
    state the digest names."""
    value = find_draggable(page)
    path = value.get_attribute("data-drag")
    assert path, "the draggable value names no path"
    value.scroll_into_view_if_needed()
    start = int(value.inner_text())
    size = int(re.sub(r"\D", "", value.locator("xpath=../span[@class='size']").inner_text()))
    before = stamped(page)
    box = value.bounding_box()
    assert box is not None
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    page.mouse.move(x, y)
    page.mouse.down()
    for step in range(1, 9):
        page.mouse.move(x + 4 * step, y)
    # the strip and the diff move while the hand still holds the value: one
    # settled step committed, its pin checked the browser's own bytes
    page.wait_for_function(
        "before => {"
        " const text = (id) => document.getElementById(id).textContent;"
        " const line = text('digest-line').trim();"
        " return text('term').includes('drag set')"
        "   && /^[0-9a-f]{64}$/.test(line) && line !== before;"
        " }",
        arg=before,
        timeout=120_000,
    )
    mid = {
        "digest": stamped(page),
        "diff": page.locator("#digest-diff").inner_text(),
        "checked": page.locator("#digest-checked").inner_text(),
    }
    assert mid["digest"] != before, "the digest stood still while the hand dragged"
    assert "byte diff:" in mid["diff"] and "no bytes moved" not in mid["diff"], (
        f"the drag showed no byte diff: {mid['diff']!r}"
    )
    assert "checked against the digest" in mid["checked"], (
        f"the browser did not pin its own bytes mid-drag: {mid['checked']!r}"
    )

    # the hand keeps moving; the release settles the last value and the chain
    # ends with the panes re-reading the seat's bytes ("drag end:")
    for step in range(9, 25):
        page.mouse.move(x + 4 * step, y)
    page.mouse.up()
    page.wait_for_function(
        "() => document.getElementById('term').textContent.includes('drag end:')",
        timeout=120_000,
    )
    assert "checked against the digest" in page.locator("#digest-checked").inner_text()
    assert page.locator("#tree .node").count() > 0, "the tree pane did not re-read"
    assert page.locator("#objects .row").count() > 0, "the object list did not re-read"
    # the panes draw the end of the drag: the value the hand settled on, and
    # the digest strip is the word the last step printed
    settled = min(2 ** (8 * size) - 1, max(0, start + 24))
    after = page.locator(f'#objects .value[data-drag="{path}"]')
    assert after.count() == 1 and int(after.inner_text()) == settled, (
        f"the panes do not show the dragged value {settled}: {after.all_inner_texts()}"
    )
    assert stamped(page) == last_digest(page), "the strip is not the drag's own last word"


def test_console_digest_story_round_trips() -> None:
    found = browsers_path()
    if found is None:
        pytest.skip("a seat without a browser")
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = found
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
            page.goto(f"http://127.0.0.1:{port}/", timeout=60_000)
            # readiness: the command bar paints and the seat is up
            page.wait_for_selector("#cmds .item", timeout=60_000)
            page.wait_for_function(
                "() => document.getElementById('term').textContent.includes('image is up')",
                timeout=60_000,
            )
            try:
                prove_round(page)
                prove_drag(page)
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
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
