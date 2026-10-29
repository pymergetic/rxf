"""The web console's chain on the wasm32 seat: the page's own modules, headless.

What a user types at the preview page's prompt is one frame into the seat's
FIFO, one turn of the image's own pump, and the answer frames the page decodes
(``web/index.html``'s handlers over ``web/rxf.js`` and ``web/frames.js``). This
drives that exact chain in node (``tests/web_console.mjs``) and pins the
answers: ``honors`` is answered with the founding core's handle, a name that
does not exist is answered as not found -- and both survive the trip as bytes,
because a frame is not text.

The seat's artifacts come from ``make -C boot wasm``; where the toolchain is
absent the test skips and says which tool, never pretends.
"""

from __future__ import annotations

import http.server
import json
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from web import serve

ROOT = Path(__file__).parents[1]
HARNESS = Path(__file__).with_name("web_console.mjs")
TOOLS = ["make", "clang", "wasm-ld", "node"]
RESOLVE_ANSWER = 0x84  # frames.js: OP.RESOLVE | ANSWER


@pytest.fixture(scope="module")
def artifacts() -> None:
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip("web console tools missing: " + ", ".join(missing))
    subprocess.run(
        ["make", "-C", str(ROOT / "boot"), "wasm"],
        check=True,
        capture_output=True,
        timeout=600,
    )
    for name in serve.SEAT:
        assert serve.SEAT[name].exists(), f"{name} is not built"


@pytest.fixture(scope="module")
def served(artifacts: None) -> Any:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), serve.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        thread.join(30)


def ask(base: str, op: str, payload: str = "") -> list[dict[str, Any]]:
    """One console request through the page's chain: the answer frames it got."""
    done = subprocess.run(
        ["node", str(HARNESS), base, op, payload],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    return json.loads(done.stdout)


def test_honors_is_answered_with_the_foundings_handle(served: str) -> None:
    frames = ask(served, "RESOLVE", "honors")
    assert frames, "the console answered no frame for `honors`"
    answer = frames[0]
    assert answer["op"] == RESOLVE_ANSWER
    assert answer["status"] == 0
    assert len(answer["payload"]) == 8  # one handle, the honors module's


def test_a_name_that_is_not_there_is_answered_not_dropped(served: str) -> None:
    frames = ask(served, "RESOLVE", "no_such_object_anywhere")
    assert frames, "the console fell silent instead of refusing"
    assert frames[0]["op"] == RESOLVE_ANSWER
    assert frames[0]["status"] != 0


def test_a_fork_carries_exactly_what_its_parent_had(served: str) -> None:
    """The chain the snapshot button makes: image -> fork 1 -> fork 2. A fork
    carries its parent's snapshot and no more -- a chain that grows (the
    screenshot's 812096 -> 812352) is the growth headroom leaking into it."""
    done = subprocess.run(
        ["node", str(HARNESS), served, "forks"],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    sizes = json.loads(done.stdout)["sizes"]
    assert len(sizes) == 3
    assert sizes[0] > 0
    assert sizes == [sizes[0]] * 3, f"the forks grew: {sizes}"
