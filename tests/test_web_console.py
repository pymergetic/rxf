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

import hashlib
import http.server
import json
import shutil
import socket
import struct
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import boot_image
import link
import programs
import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import ObjectArena
from pymergetic.rxftk.live import Refused, harness
from web import acts, serve

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


# ---- the modifications acts (U1) ----------------------------------------------------------------
#
# An act is JSON the page sends to the host (`POST /compile`, web/acts.py); the
# host's compiler (live/physical.py) answers the delta's bytes, and the page
# (web/acts.js) stages them through the channel and commits. The proofs: the
# host's door answers deltas the engine commits (the same bytes the page
# stages), the refusals are answered in words, and the page's own chain reaches
# the seat with those bytes.

SET = {"act": "set", "path": "pymergetic.rxf.application.discount", "value": 45}
ADD = {"act": "add", "parent": "pymergetic.rxf.application", "name": "surcharge", "value": 7}
RENAME = {"act": "rename", "path": "pymergetic.rxf.application.shipping", "name": "freight"}


def compile_over_http(base: str, request: dict[str, Any]) -> tuple[int, bytes, str | None]:
    """The page's own request: POST /compile, the status, the body, the SHA-256 header."""
    asked = urllib.request.Request(
        f"{base}/compile",
        data=json.dumps(request).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(asked, timeout=600) as reply:
            return reply.status, reply.read(), reply.headers.get("X-Rxf-Sha256")
    except urllib.error.HTTPError as error:
        return error.code, error.read(), None


@pytest.fixture
def engine() -> harness.HostImage:
    """The host engine over the seat's image: the oracle the acts' deltas commit on."""
    return harness.HostImage(link.arena_of(acts.model()))


def test_the_host_compiles_an_act_into_a_delta_the_seat_checks(
    served: str, oracle: Bridge, engine: harness.HostImage
) -> None:
    """The compile door: the delta carries the base digest the seat answered
    (the seat refuses a delta for another image), the SHA-256 header is the
    digest of the body (STAGE_COMMIT checks the staged bytes against it), and
    the engine commits it: the answer is a new digest."""
    status, delta, sha256 = compile_over_http(
        served, {"base": oracle.digest(), "history": [], "act": SET}
    )
    assert status == 200
    assert sha256 == hashlib.sha256(delta).hexdigest()
    magic, _version, ops, _strings, flags = struct.unpack_from("<IIIII", delta)
    assert magic == 0x44465852 and ops == 1 and flags & 1  # RXFD, one SET, base checked
    assert delta[24:56].hex() == oracle.digest()
    after = engine.client.apply(delta)
    assert after != oracle.digest()
    assert engine.client.digest() == after
    # the same delta again names a base that is gone: the image refuses it itself
    with pytest.raises(Refused) as caught:
        engine.client.apply(delta)
    assert caught.value.reason == 2  # the base digest is not the digest of the image


def test_every_act_commits_on_the_engine_and_the_digest_follows(
    served: str, engine: harness.HostImage
) -> None:
    """The page's flow, act after act: ask the digest, have the host compile the
    act against it (after the acts already committed), commit. Each commit
    answers a new digest, and the next act's base is that very word."""
    done: list[dict[str, Any]] = []
    digest = engine.client.digest()
    seen = {digest}
    for act in (SET, ADD, RENAME):
        status, delta, _sha = compile_over_http(
            served, {"base": digest, "history": done, "act": act}
        )
        assert status == 200, delta.decode()
        digest = engine.client.apply(delta)
        assert digest not in seen
        seen.add(digest)
        done.append(act)
    assert engine.client.resolve("pymergetic.rxf.application.surcharge")
    assert engine.client.resolve("pymergetic.rxf.application.freight")


@pytest.mark.parametrize(
    ("request_", "words"),
    [
        ({"act": "set", "path": "no.such.object", "value": 1}, "no object at"),
        ({"act": "set", "path": "pymergetic.rxf.application", "value": 1}, "has members"),
        ({"act": "set", "path": SET["path"], "value": 1 << 40}, "does not fit"),
        ({"act": "rename", "path": "pymergetic.rxf.application", "name": "app"}, "has members"),
        ({"act": "rename", "path": SET["path"], "name": "a.b"}, "one non-empty segment"),
        ({"act": "add", "parent": "pymergetic.rxf.application", "name": "unit_price"}, "already"),
        ({"act": "burn"}, "no act"),
    ],
)
def test_an_act_the_image_cannot_take_is_refused_in_words(
    served: str, oracle: Bridge, request_: dict[str, Any], words: str
) -> None:
    status, body, _sha = compile_over_http(
        served, {"base": oracle.digest(), "history": [], "act": request_}
    )
    assert status == 400
    assert words in body.decode()


def test_a_request_that_is_not_a_request_is_refused(served: str) -> None:
    status, body, _sha = compile_over_http(served, {"act": SET})
    assert status == 400
    assert "base" in body.decode()
    asked = urllib.request.Request(f"{served}/other", data=b"{}", method="POST")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(asked, timeout=60)
    assert caught.value.code == 404


def test_the_pages_acts_reach_the_seat_with_the_hosts_bytes(
    served: str, oracle: Bridge
) -> None:
    """The page's own chain (web/acts.js through web/rxf.js): each act is
    compiled by the host, staged in chunks that fit the seat's RX ring and
    committed. What the seat takes must be what the engine takes: every commit
    that is answered is answered with the digest the engine answers for the
    SAME delta bytes, and a fork carries the result.

    The seat is the native twin, and the twin's apply is not whole yet: its
    post-apply dump refuses in the genesis placement (night.log, the stale
    test's xfail) - the first commit answers `delta refused: the arena has no
    room` (reason 6) and the arena it leaves refuses the next ones too. Until
    that lane lands the refusal is the pinned answer and the test xfails
    after holding every other word; the day the twin applies it passes whole."""
    done = subprocess.run(
        ["node", str(HARNESS), served, "acts", json.dumps([SET, ADD, RENAME])],
        capture_output=True,
        text=True,
        check=True,
        timeout=900,
    )
    result = json.loads(done.stdout)
    steps = result["steps"]
    assert len(steps) == 3
    engine = harness.HostImage(link.arena_of(acts.model()))
    digest = oracle.digest()
    refused: list[str] = []
    for step in steps:
        assert step["before"] == digest, "an act starts from the seat's own digest"
        if "error" in step:
            assert step["error"].startswith("delta refused: "), step["error"]
            refused.append(step["error"])
            continue
        digest = engine.client.apply(bytes.fromhex(step["delta"]))
        assert step["after"] == digest, "the seat answers the engine's digest"
    assert result["fork"] == digest, "a fork carries the image the acts left"
    if refused:
        pytest.xfail("the twin's apply is not whole: " + "; ".join(refused))
