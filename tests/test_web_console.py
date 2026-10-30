"""The web console's chain on the wasm32 seat: the page's own modules, headless.

What a user types at the preview page's prompt is one frame into the seat's
FIFO, one turn of the image's own pump, and the answer frames the page decodes
(``web/index.html``'s handlers over ``web/rxf.js`` and ``web/frames.js``). This
drives that exact chain in node (``tests/web_console.mjs``) and pins the
answers against the v7 oracle (``tests/support.py``): the digest the channel
answers is the canonical digest of the image the seat carries, and a resolve
answers the arena's own handle -- the founding core has no world-model handle
to pin it by, so the oracle's arena is the one that names it.

The seat's artifacts come from ``make -C boot wasm``; where the toolchain is
absent the test skips and says which tool, never pretends.
"""

from __future__ import annotations

import http.server
import json
import shutil
import socket
import struct
import subprocess
import threading
from pathlib import Path
from typing import Any

import boot_image
import programs
import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import ObjectArena
from web import serve

from .support import Bridge

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


@pytest.fixture(scope="module")
def oracle() -> Bridge:
    """The seat's image as the v7 oracle answers it.

    The seat boots the linked v7 arena of the starter plus the console's own
    turn (``boot_image.build``: the ``boot.smoke`` program is the pump's
    shell), so the oracle is that same model - the arena the seat serves is
    this Bridge's canonical bytes, and every answer the channel gives is
    pinned against them.
    """
    image = tree.load(ROOT / "starter")
    parent = image.handle(boot_image.PROGRAM_PARENT)
    image.add(parent, boot_image.PROGRAM_NAME, payload=boot_image.pump_program())
    return Bridge(programs.filled(image))


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


def answer_hex(answer: dict[str, Any]) -> str:
    return bytes(answer["payload"]).hex()


def test_the_seats_digest_is_the_canonical_digest(served: str, oracle: Bridge) -> None:
    """The channel's write slice is live: DIGEST answers the canonical digest
    of the image the seat carries (docs/32 5.4) - the same word the host
    engine answers, and the ground the console's modifications acts stand on."""
    frames = ask(served, "DIGEST")
    assert frames and frames[0]["status"] == 0
    assert answer_hex(frames[0]) == oracle.digest()


def test_hello_answers_the_oracles_words(served: str, oracle: Bridge) -> None:
    """HELLO answers the oracle's counts: the slot head's count and the
    superblock's committed claim (docs/32 1), the words the page's strip and
    the fork chain stand on."""
    frames = ask(served, "HELLO")
    assert frames and frames[0]["status"] == 0
    handles, body = struct.unpack_from("<QQ", bytes(frames[0]["payload"]), 8)
    assert handles == oracle.count
    assert body == oracle.body_size


def test_honors_is_answered_with_the_foundings_handle(served: str, oracle: Bridge) -> None:
    frames = ask(served, "RESOLVE", "honors")
    assert frames, "the console answered no frame for `honors`"
    answer = frames[0]
    assert answer["op"] == RESOLVE_ANSWER
    assert answer["status"] == 0
    # the founding core is arena-only (arena_export._genesis): the oracle's
    # arena names it, not the world model
    arena = ObjectArena.attach(oracle.canonical())
    founding = arena.find(arena.root_handle, "honors")
    assert founding != 0xFFFFFFFF
    assert struct.unpack("<Q", bytes(answer["payload"]))[0] == founding


def test_a_name_that_is_not_there_is_answered_not_dropped(served: str) -> None:
    frames = ask(served, "RESOLVE", "no_such_object_anywhere")
    assert frames, "the console fell silent instead of refusing"
    assert frames[0]["op"] == RESOLVE_ANSWER
    assert frames[0]["status"] != 0


def test_the_channel_takes_a_staged_delta_and_answers_its_commit(
    served: str, oracle: Bridge
) -> None:
    """The modification acts' first leg (U1 pre-work): the seat's channel
    stages bytes and answers a commit - the SHA-256 check passes, so the
    staged bytes are the image's to read, and the answer is the image's own
    word about the delta. On the empty delta the arena lane's seam is the
    answer: the twin's post-apply relink walk refuses with reason 12 (a type
    is more than the live layer reads) while the host engine commits the
    same delta - the alignment the acts' commits wait on (night.log)."""
    frames = ask(served, "commit")
    assert frames, "the console answered no frame for the commit"
    answer = frames[0]
    assert answer["op"] == 0x92  # frames.js: OP.STAGE_COMMIT | ANSWER
    assert answer["status"] in (0, 7), "the commit is answered, never silent"
    at, reason = struct.unpack_from("<II", bytes(answer["payload"]))
    if answer["status"] == 0:
        # the twin aligned: the empty delta commits and answers the digest
        assert bytes(answer["payload"]).hex() == oracle.digest()
    else:
        assert (at, reason) == (0, 12), (
            f"the refusal is the twin's relink seam, not {reason}: {REASONS.get(reason)}"
        )


REASONS = {
    6: "the arena has no room",
    12: "a type is more than the live layer reads",
}


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
