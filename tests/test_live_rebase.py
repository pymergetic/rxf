"""The editor's poke is part of the image state: poke, then compile/install on one session.

A poke writes through the live channel outside the front end's own sends (`server/editor.py`
`poke` -> `live/client.py` `apply`), and the front end stages every delta against its mirror of
the image (`live/front.py` `_image`/`_send`). The mirror used to go past the poke, so
`POST /api/editor/poke` followed by `POST /api/editor/compile` or `POST /api/editor/install` on
the same session was refused its base ("the base digest is not the digest of the image").
`Front.rebase` is the mirror tracking that apply, and `editor.poke` calls it with the session's
front. The poke also writes what text deltas never do (a payload word at an offset), which the
delta's own link check reads back as untyped — so `physical.from_xml` and the mirror replay
their deltas with `links=False`, the running image's policy (the image checks the links when it
takes the delta). These pins hold:

* after any poke, a compile on the same session is staged against the new base and succeeds;
* after any poke, an install on the same session succeeds and the installed code RUNS in the
  image — the poke is part of the state the program was installed into;
* a genuinely stale base stays refused with the same digest refusal: a poke staged against a
  base another mutation left, and a compile staged against a mirror another mutation left, are
  both refused exactly as before. The mirror tracks the applies that land; the base check is
  never disabled.

Everything is real: the `starter/` tree, `server/live/runner.py` running it (its own link per
pin, so pins never share mutation state), and the app's own routes over `TestClient`.
"""

from __future__ import annotations

import struct
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import physical
from pymergetic.rxftk.live.client import REASONS, Refused
from pymergetic.rxftk.live.front import identity
from pymergetic.rxftk.server import editor
from pymergetic.rxftk.server.app import PokeBody, SourceBody, create_app
from pymergetic.rxftk.server.live.runner import Runner

# Pokes, edits and installs over a live app session and its routes: the live class
# of test_live_concurrency and test_serve_real.
pytestmark = pytest.mark.live

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
MODULE = "pymergetic.rxf.selfedit"
POKE_PATH = "pymergetic.rxf.application.main"
POKE_VALUE = 0xA5A5A5A5A5A5A5A5
POKE_VALUE_2 = 0x5A5A5A5A5A5A5A5A

TRIPLE = """\
program triple(x) {
    return x + x + x;
}
"""


@pytest.fixture(scope="module")
def image() -> Image:
    """The starter, the same ground truth every image test runs (read-only: every runner links
    its own seat of it)."""
    return tree.load(STARTER)


def _link() -> str:
    """One link per runner: the registry is per process and the suite shares it."""
    return f"shm:live-rebase-{uuid.uuid4().hex[:8]}"


@contextmanager
def running(image: Image) -> Iterator[Runner]:
    """One running image of its own: what `rxf serve` runs behind the app, at its own link."""
    made = Runner(image, _link())
    try:
        yield made
    finally:
        made.close()


def _poke(value: int) -> dict[str, int]:
    return PokeBody(
        path=POKE_PATH, field="payload", value=value, offset=0
    ).model_dump()


def _reads_back(runner: Runner) -> int:
    """The word a payload poke wrote, read back through the channel of the running image."""
    handle = runner.client.resolve(POKE_PATH)
    assert handle is not None
    (written,) = struct.unpack("<Q", runner.client.payload(handle)[:8])
    return written


# ---- (a) after any poke, the next compile and install are staged against the new base ---------


def test_a_compile_after_a_poke_is_staged_against_the_poked_image(image: Image):
    """The bug, pinned: `POST /api/editor/poke` and then `POST /api/editor/compile` on the same
    session. The poke is part of the image state, so the compile is staged against the base the
    poke left and succeeds — before the mirror tracked the poke, this very compile was refused
    409 "the base digest is not the digest of the image"."""
    with running(image) as runner:
        client = TestClient(create_app(image, session=runner.session))
        poked = client.post("/api/editor/poke", json=_poke(POKE_VALUE))
        assert poked.status_code == 200 and poked.json()["ok"] is True
        assert _reads_back(runner) == POKE_VALUE  # the poke is in the image

        compiled = client.post(
            "/api/editor/compile", json=SourceBody(source=TRIPLE).model_dump()
        )
        assert compiled.status_code == 200, compiled.text
        body = compiled.json()
        assert body["ok"] is True and body["name"] == "triple" and body["words"] > 0


def test_an_install_after_a_poke_installs_and_the_code_runs_in_the_image(image: Image):
    """`POST /api/editor/poke` and then `POST /api/editor/install` on the same session: the
    install is staged against the poked base and succeeds, and the words it installed run on
    the image's own interpreter — the poke is part of the state the program was installed into."""
    with running(image) as runner:
        client = TestClient(create_app(image, session=runner.session))
        poked = client.post("/api/editor/poke", json=_poke(POKE_VALUE))
        assert poked.status_code == 200 and poked.json()["ok"] is True

        installed = client.post(
            "/api/editor/install", json=SourceBody(source=TRIPLE).model_dump()
        )
        assert installed.status_code == 200, installed.text
        body = installed.json()
        assert body["ok"] is True and body["path"] == f"{MODULE}.triple"
        assert body["digest"] == runner.client.digest()

        raw, _ = runner.client.emit()
        running_image = Image.from_bytes(raw)
        wanted = identity(
            running_image, running_image.handle(f"{MODULE}.triple.bytecode")
        )
        assert runner.machine.run(wanted, struct.pack("<Q", 7)) == (0, 21)


def test_a_chain_of_pokes_and_edits_stays_coherent(image: Image):
    """Not one poke but a chain on one session — poke, compile, poke, install, compile. Every
    poke is tracked by the mirror it moves past, so every later edit is staged against the base
    the last mutation left: the mirror follows the applies that land, and never guesses."""
    with running(image) as runner:
        client = TestClient(create_app(image, session=runner.session))
        source = SourceBody(source=TRIPLE).model_dump()

        assert client.post("/api/editor/poke", json=_poke(POKE_VALUE)).json()["ok"] is True
        assert client.post("/api/editor/compile", json=source).json()["ok"] is True
        assert client.post("/api/editor/poke", json=_poke(POKE_VALUE_2)).json()["ok"] is True
        installed = client.post("/api/editor/install", json=source)
        assert installed.status_code == 200 and installed.json()["ok"] is True
        assert client.post("/api/editor/compile", json=source).json()["ok"] is True

        raw, _ = runner.client.emit()
        running_image = Image.from_bytes(raw)
        wanted = identity(
            running_image, running_image.handle(f"{MODULE}.triple.bytecode")
        )
        assert runner.machine.run(wanted, struct.pack("<Q", 7)) == (0, 21)
        assert _reads_back(runner) == POKE_VALUE_2  # the last poke is the state that is there


# ---- (b) a genuinely stale base is still refused, with the same digest refusal ----------------


def test_a_poke_staged_against_a_base_another_mutation_left_is_still_refused(image: Image):
    """The honest refusal stays: a poke whose snapshot another mutation left (its base is not
    the image's) is refused by the image itself with the digest refusal, the refusal changes
    nothing (the image keeps its digest), and it rebases nothing — the session still compiles
    against its real base. Tracking the mirror is not a disabled check."""
    with running(image) as runner:
        client = TestClient(create_app(image, session=runner.session))
        left_base = Image.from_bytes(runner.client.emit()[0])

        # another mutation lands and leaves the snapshot above behind
        poked = client.post("/api/editor/poke", json=_poke(POKE_VALUE))
        assert poked.status_code == 200 and poked.json()["ok"] is True
        digest_after = runner.client.digest()

        with pytest.raises(Refused) as problem:
            editor.poke(
                runner.client,
                left_base,
                POKE_PATH,
                "payload",
                POKE_VALUE_2,
                front=runner.front,
            )
        assert (
            REASONS[problem.value.reason]
            == "the base digest is not the digest of the image"
        )
        assert "the base digest is not the digest of the image" in str(problem.value)
        assert runner.client.digest() == digest_after  # a refusal leaves the image as it was

        compiled = client.post(
            "/api/editor/compile", json=SourceBody(source=TRIPLE).model_dump()
        )
        assert compiled.status_code == 200 and compiled.json()["ok"] is True


def test_a_compile_staged_against_a_mirror_another_mutation_left_is_still_refused(
    image: Image,
):
    """The genuine conflict: a change that is not a poke and not a front end's send (a patch
    applied straight through the channel, `rxf live patch`'s shape) leaves the mirror behind,
    and the next compile is refused the same digest words. The mirror tracks out-of-band applies
    through the poke seam; what it was never told about is refused, exactly as before."""
    with running(image) as runner:
        client = TestClient(create_app(image, session=runner.session))
        before = Image.from_bytes(runner.client.emit()[0])
        after = Image.from_bytes(before.to_bytes())
        handle = after.handle(POKE_PATH)
        patched = editor.poke_word_payload(
            bytes(after.get(handle)["payload"]), 0, POKE_VALUE
        )
        after.set_payload(handle, patched)
        runner.client.apply(physical.compile(before, after).data)  # not a poke: no rebase

        refused = client.post(
            "/api/editor/compile", json=SourceBody(source=TRIPLE).model_dump()
        )
        assert refused.status_code == 409, refused.text
        assert (
            "the base digest is not the digest of the image"
            in refused.json()["detail"]
        )
