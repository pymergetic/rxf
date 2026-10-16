"""The live channel of a served image is safe on its own: one transaction at a time, one runner.

The web app polls the feed of a running image (`GET /api/live`: session spec, digest, interpreter,
leaf state) while the editor drives the SAME image's delta channel (compile / install / poke).
These pins hold the backend to that safety WITHOUT any frontend mitigation — no
`body[data-channel-busy]` pausing the feed poll, no reliance on `live_seam`'s 409 mapping:

* a feed read concurrent with a compile / install / poke must complete (never a refusal, never a
  500) and the mutation must land INTACT (the installed Function runs, the poked word reads back);
* two concurrent mutations serialize: both land, in order, as two intact commits — no LiveError;
* one link has one runner: a second attach at a link whose runner is there is refused honestly
  (`WorldIsRegistered`, the seam's own refusal) and keeps the runner that is there — even when
  the two attaches race.

The concurrency is the app's own: the sync endpoints run in a threadpool, so the pins fire the
requests at the same instant over the ASGI app (`httpx.ASGITransport` + `asyncio.gather` behind a
barrier). Everything is real: the image is the `starter/` tree and the machine is
`server/live/runner.py` — the linked image with its own interpreter and live leaf. Every pin owns
its runner (its own link), so pins never share mutation state — note the separate, non-racy
refusal a poke leaves behind (a poke applies outside the front end's mirror, so a LATER compile
on the same session is refused with a stale base: `live/front.py` `_image`/`_send`); that seam
coherence bug is not what these pins are about and must not leak into them.
"""

from __future__ import annotations

import asyncio
import re
import struct
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from pymergetic.rxftk.console import world
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import cli, physical
from pymergetic.rxftk.live.front import identity
from pymergetic.rxftk.server.app import PokeBody, SourceBody, create_app
from pymergetic.rxftk.server.live.runner import Runner

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
MODULE = "pymergetic.rxf.selfedit"
POKE_PATH = "pymergetic.rxf.application.main"
POKE_VALUE = 0xA5A5A5A5A5A5A5A5

TRIPLE = """\
program triple(x) {
    return x + x + x;
}
"""

TWICE = """\
program twice(x) {
    return x + x;
}
"""

QUAD = """\
program quad(x) {
    return x + x + x + x;
}
"""


@pytest.fixture(scope="module")
def image() -> Image:
    """The starter, the same ground truth every image test runs (read-only: every runner links
    its own seat of it)."""
    return tree.load(STARTER)


def _link() -> str:
    """One link per runner: the registry is per process and the suite shares it."""
    return f"shm:live-race-{uuid.uuid4().hex[:8]}"


@contextmanager
def running(image: Image) -> Iterator[Runner]:
    """One running image of its own: what `rxf serve` runs behind the app, at its own link."""
    made = Runner(image, _link())
    try:
        yield made
    finally:
        made.close()


# ---- firing requests at the same instant --------------------------------------------------------


async def _feed_vs_mutation(app, mutate) -> tuple[httpx.Response, list[httpx.Response], int]:
    """One mutation fired at the same instant two pollers hammer `GET /api/live` — the shell's
    live slot polling while the editor works. Returns the mutation's answer, every feed answer,
    and how many polls began while the mutation was in flight (that the race really raced)."""
    pollers = 2
    barrier = asyncio.Barrier(pollers + 1)
    in_flight = asyncio.Event()
    done = asyncio.Event()
    answers: list[httpx.Response] = []
    during = 0

    async def feed() -> None:
        nonlocal during
        await barrier.wait()
        while not done.is_set():
            if in_flight.is_set():
                during += 1
            answers.append(await http.get("/api/live"))
            await asyncio.sleep(0)

    async def mutation() -> httpx.Response:
        await barrier.wait()
        in_flight.set()
        try:
            return await mutate(http)
        finally:
            in_flight.clear()
            done.set()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as http:
        async with asyncio.timeout(300):
            results = await asyncio.gather(mutation(), *(feed() for _ in range(pollers)))
    answered = results[0]
    assert answered is not None
    return answered, answers, during


def _assert_feed_healthy(polls: list[httpx.Response]) -> None:
    """Every poll of the feed answers the attached image, in one piece: a read that tore the
    channel shows up as the spec's own refusal (or a crash of the route), never silently."""
    assert polls, "the feed poller never ran"
    for poll in polls:
        assert poll.status_code == 200, poll.text
        body = poll.json()
        assert body["attached"] is True
        assert body["refusal"] is None, body["refusal"]
        assert body["image"] is not None
        assert re.fullmatch(r"[0-9a-f]{64}", body["image"]["digest"])


# ---- (a) a feed read concurrent with a mutation: both complete, the mutation intact -------------


def test_a_feed_poll_never_fails_or_corrupts_a_compile_install(image: Image):
    """The install route (compile + install through the running image's front end) fires while
    the feed polls: every poll answers the attached image, the install lands, and the installed
    Function RUNS — the mutation is intact, not half-applied words of a torn transaction."""
    with running(image) as runner:
        app = create_app(image, session=runner.session)

        async def run():
            async def mutate(http: httpx.AsyncClient) -> httpx.Response:
                return await http.post(
                    "/api/editor/install", json=SourceBody(source=TRIPLE).model_dump()
                )

            return await _feed_vs_mutation(app, mutate)

        installed, polls, during = asyncio.run(run())
        assert during >= 1, "the pin never overlapped the mutation with a feed read"
        assert installed.status_code == 200, installed.text
        body = installed.json()
        assert body["ok"] is True and body["path"] == f"{MODULE}.triple"
        _assert_feed_healthy(polls)

        raw, _ = runner.client.emit()
        running_image = Image.from_bytes(raw)
        wanted = identity(running_image, running_image.handle(f"{MODULE}.triple.bytecode"))
        assert runner.machine.run(wanted, struct.pack("<Q", 7)) == (0, 21)


def test_a_feed_poll_never_fails_or_corrupts_a_poke(image: Image):
    """The poke route (emit the running image, compile one delta against it, apply) fires while
    the feed polls: every poll answers, the poke lands, and the word it wrote reads back — the
    emit-to-apply transaction was never entered by the poller."""
    with running(image) as runner:
        app = create_app(image, session=runner.session)
        poke = PokeBody(path=POKE_PATH, field="payload", value=POKE_VALUE, offset=0)

        async def run():
            async def mutate(http: httpx.AsyncClient) -> httpx.Response:
                return await http.post("/api/editor/poke", json=poke.model_dump())

            return await _feed_vs_mutation(app, mutate)

        out, polls, during = asyncio.run(run())
        assert during >= 1, "the pin never overlapped the mutation with a feed read"
        assert out.status_code == 200, out.text
        body = out.json()
        assert body["ok"] is True and body["field"] == "payload"
        _assert_feed_healthy(polls)

        handle = runner.client.resolve(POKE_PATH)
        assert handle is not None
        (written,) = struct.unpack("<Q", runner.client.payload(handle)[:8])
        assert written == POKE_VALUE
        assert body["digest"] == runner.client.digest()


# ---- (b) two concurrent mutations: two intact commits, in order ---------------------------------


def test_two_concurrent_mutations_serialize_as_two_intact_commits(image: Image):
    """Two installs fired at the same instant: both land (no LiveError, no 409), as two commits
    on top of each other — the digests differ and the later one is what runs now, so neither was
    compiled against a base the other had already left — and both Functions run intact."""
    with running(image) as runner:
        app = create_app(image, session=runner.session)
        before = runner.client.hello()

        async def run():
            barrier = asyncio.Barrier(2)

            async def install(http: httpx.AsyncClient, source: str) -> httpx.Response:
                await barrier.wait()
                return await http.post(
                    "/api/editor/install", json=SourceBody(source=source).model_dump()
                )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://app"
            ) as http:
                async with asyncio.timeout(300):
                    return await asyncio.gather(install(http, TWICE), install(http, QUAD))

        one, two = asyncio.run(run())
        for response in (one, two):
            assert response.status_code == 200, response.text
            assert response.json()["ok"] is True
        digest_one, digest_two = one.json()["digest"], two.json()["digest"]
        assert digest_one != digest_two, "two serialized commits cannot report one digest"
        assert runner.client.digest() in (digest_one, digest_two)

        hello = runner.client.hello()
        assert hello.rejects == before.rejects, "the image refused a delta mid-race (LiveError)"
        assert hello.commits > before.commits

        raw, _ = runner.client.emit()
        running_image = Image.from_bytes(raw)
        for name, want in (("twice", 14), ("quad", 28)):
            wanted = identity(running_image, running_image.handle(f"{MODULE}.{name}.bytecode"))
            assert runner.machine.run(wanted, struct.pack("<Q", 7)) == (0, want)


# ---- (c) one link, one runner -------------------------------------------------------------------


def test_a_second_runner_attach_is_refused_honestly_and_the_first_keeps_it(image: Image):
    """The chosen semantics: a second attach at a taken link is REFUSED honestly (the seam's own
    `WorldIsRegistered`, naming the link) — no joining a runner whose machine is another's — and
    the runner that is there keeps the link and keeps answering. After it closes, the link gives
    a runner again."""
    link = _link()
    first = Runner(image, link)
    try:
        with pytest.raises(world.WorldIsRegistered, match=re.escape(link)):
            Runner(image, link)
        assert first.client.ping(b"still here") == b"still here"
        assert world.registered(link)
        assert world.attach(link) is first.session.world
    finally:
        first.close()
    assert world.registered(link) is False

    fresh = Runner(image, link)
    try:
        assert fresh.client.ping(b"again") == b"again"
    finally:
        fresh.close()


def test_two_racing_runner_attaches_race_to_exactly_one_runner(image: Image):
    """The attach RACE: two runners race for one free link behind a barrier — exactly one runs
    and registers its world, the other is refused with `WorldIsRegistered`, and no second runner
    state ever interleaves with the first on the link (the winner is the only world there)."""
    link = _link()
    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}

    def attach(tag: str) -> None:
        barrier.wait(timeout=60)
        try:
            outcomes[tag] = Runner(image, link)
        except BaseException as problem:  # noqa: BLE001 - the race outcome is what is pinned
            outcomes[tag] = problem

    racers = [threading.Thread(target=attach, args=(tag,)) for tag in ("one", "two")]
    for racer in racers:
        racer.start()
    for racer in racers:
        racer.join(timeout=300)
        assert not racer.is_alive(), "a runner attach never finished"

    winners = [r for r in outcomes.values() if isinstance(r, Runner)]
    refused = [r for r in outcomes.values() if isinstance(r, world.WorldIsRegistered)]
    assert len(winners) == 1, outcomes
    assert len(refused) == 1, outcomes
    winner = winners[0]
    try:
        assert winner.client.ping(b"one runner") == b"one runner"
        assert world.registered(link)
        assert world.attach(link) is winner.session.world
    finally:
        winner.close()
    assert world.registered(link) is False


# ---- one composite holds the gate once ----------------------------------------------------------

PROBE_DELTA = """\
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="LiveGateProbe" form="Form.Struct" size="8" align="8">
    <Field name="x" value_type="uint64_t"/>
  </Type>
  <LiveGateProbe name="gate_probe" x="7"/>
</delta>
"""


def test_a_live_apply_holds_the_transaction_around_its_whole_composite(
    image: Image, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """`rxf live apply` is one dump-compile-take composite; the world's transaction must be
    held across the host-side compile gap, not just around each request. The probe stands in
    that gap (inside `physical.from_xml`, between the dump and the take) and tries to take the
    gate: post-wrap it waits until the composite is done, pre-wrap it took the gate instantly.
    The exclusion is timed in the failing direction only (the console suite's 2 s margin)."""
    link = _link()
    runner = Runner(image, link)
    try:
        client: Any = world.attach(link).client
        acquired = threading.Event()
        probers: list[threading.Thread] = []

        def probe_the_gate() -> None:
            with client.transaction():
                acquired.set()

        real_from_xml = physical.from_xml

        def standing_in_the_gap(*args: Any, **kwargs: Any) -> Any:
            prober = threading.Thread(target=probe_the_gate)
            probers.append(prober)
            prober.start()
            prober.join(timeout=2.0)
            assert not acquired.is_set(), "the gate opened in the compile gap of rxf live apply"
            return real_from_xml(*args, **kwargs)

        monkeypatch.setattr(physical, "from_xml", standing_in_the_gap)
        delta = tmp_path / "gate.rxfd"
        delta.write_text(PROBE_DELTA, encoding="utf-8")

        report = cli.apply(link, str(delta))
        assert report["creates"] == 3, report  # the type cell, its field cell, the probe object
        assert report["digest"], report

        # once the composite has released, the probe's take goes through — the gate reopens
        assert acquired.wait(timeout=30), "the transaction never reopened after rxf live apply"
        for prober in probers:
            prober.join(timeout=30)
            assert not prober.is_alive()
    finally:
        runner.close()
