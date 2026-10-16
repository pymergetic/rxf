"""`rxf console`: type `.rxf` source, the running image compiles it and installs the result.

The path under test is the console's own, end to end: the source travels as one object of the
arena (Phase 1: `rxf:data` through the rings), the image's own front end (`native/front`, the
fixpoint of ``tests/test_front_image.py``) compiles it, the words come back as ``read`` frames,
and one delta installs the Function the words name. ``tools/native/rxfc.py`` appears twice and
both are what docs/19 allows: it seeds the front end once, and it is the oracle the words the
image writes are held to. Nothing here compiles on behalf of the image.
"""

from __future__ import annotations

import functools
import io
import json
import os
import struct
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple, TextIO

import graph_header
import link
import pytest
import rxfc

from pymergetic.rxf.cli import main
from pymergetic.rxf.console import world
from pymergetic.rxf.console.session import Console
from pymergetic.rxf.core import tree
from pymergetic.rxf.core.arena import HANDLE_ROOT
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live.front import Front, NotReady, Region, identity, value_of
from pymergetic.rxf.server.live import Session

from . import live_world as lw
from .test_rxfc import MACHINES

ROOT = Path(__file__).parents[1]
FRONT = ROOT / "native" / "front" / "front.rxf"
STARTER = ROOT / "starter"

SCRATCH = 12 << 20  # the work memory of one request: its rows, then FRONT_WORK (words.rxf)
MODULE = "pymergetic.rxf.selfedit"

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

BROKEN = """\
program broken(x) {
    return x + ;
}
"""

SCALED = """\
include "scale.rxf";
program scaled(x) {
    return x + SCALE;
}
"""


class Seat(NamedTuple):
    """One machine of the harness, its channel, and where the console's request lives."""

    machine: lw.Machine
    channel: lw.Channel
    front: Front


@functools.cache
def _seed() -> tuple[bytes, bytes, dict[str, int]]:
    """The seed the fixpoint allows (docs/19): the words of the front end from the host
    compiler, the source that made them, and the numbers the source may use."""
    return rxfc.compile_file(FRONT), FRONT.read_bytes(), graph_header.numbers()


def seat_of(image: Image, machine: str) -> Seat:
    """One machine with its channel and the front end over it (tests/live_world)."""
    spec = link.LiveSpec(headroom=8 << 20, log=8 << 20, stage=8 << 20, scratch=SCRATCH)
    made = lw.machine_of(machine, image, spec)
    channel = lw.Channel(made)
    live = made.linked.live
    assert live is not None
    arena_at, arena_cap = live.regions["arena"]
    work_at, work_cap = live.regions["work"]
    return Seat(
        made,
        channel,
        Front(
            channel.client,
            made,
            Region(arena_at, arena_cap),
            Region(work_at + (work_cap - SCRATCH), SCRATCH),
        ),
    )


def booted(seat: Seat, out: TextIO | None = None) -> Console:
    """The terminal on a running image: the front end seeded once, the image compiles after
    that (the fixpoint of docs/19)."""
    words, source, consts = _seed()
    seat.front.boot(words, source, consts)
    return Console(seat.front, out if out is not None else io.StringIO())


def words_of(seat: Seat, path: str) -> bytes:
    """The words the image holds for one of its Functions."""
    image = seat.channel.loaded()
    return value_of(image, image.handle(f"{path}.bytecode"))


def call(seat: Seat, path: str, argument: int) -> tuple[int, int]:
    """Run what the image installed: its words under the id the runtime finds it by."""
    image = seat.channel.loaded()
    return seat.machine.run(
        identity(image, image.handle(f"{path}.bytecode")), struct.pack("<Q", argument)
    )


@contextmanager
def running_console(spec: str, seat: Seat) -> Iterator[None]:
    """The seam the CLI uses: whoever runs the image registers its world (docs/19)."""
    world.register(
        spec,
        lambda name, timeout: world.World(seat.channel.client, seat.front),
    )
    try:
        yield
    finally:
        world.forget(spec)


@pytest.fixture(scope="module")
def image() -> Image:
    """The starter, the same ground truth every image test runs."""
    return tree.load(Path(STARTER))


@pytest.fixture(params=MACHINES)
def seat(request, image: Image) -> Iterator[Seat]:
    """One machine per test: the image behaves the same on every one of them."""
    made = seat_of(image, request.param)
    try:
        yield made
    finally:
        made.machine.close()


@pytest.fixture
def solo(image: Image) -> Iterator[Seat]:
    """One machine only: the terminal and the CLI do not change per machine."""
    made = seat_of(image, MACHINES[0])
    try:
        yield made
    finally:
        made.machine.close()


def test_a_program_typed_at_the_prompt_is_compiled_by_the_image_and_runs(seat: Seat):
    """The whole path: the source as an object of the arena, the front end of the image
    compiles it, the words come back, one delta installs it, and the Function answers."""
    out = io.StringIO()
    console = booted(seat, out)
    summary = console.repl([*TRIPLE.splitlines(), "", ":quit"], prompt=False)
    assert summary == {"submitted": 1, "installed": [f"{MODULE}.triple"], "refused": 0}, (
        out.getvalue()
    )
    assert f"installed {MODULE}.triple" in out.getvalue()

    # the words the image wrote are the words the host compiler writes (the fixpoint's rule)
    assert words_of(seat, f"{MODULE}.triple") == rxfc.compile_source(TRIPLE)

    # and the Function the delta installed answers, without a restart
    assert call(seat, f"{MODULE}.triple", 7) == (0, 21)


def test_a_compile_error_says_the_line_it_stopped_at(seat: Seat):
    """A program the compiler refuses is reported, not installed, and the image stays whole."""
    with pytest.raises(rxfc.Error):
        rxfc.compile_source(BROKEN)  # the oracle refuses it too: the source is genuinely broken
    console = booted(seat)
    outcome = console.submit(BROKEN)
    assert outcome["ok"] is False
    assert outcome["line"] >= 1  # the line the front end stopped at
    assert outcome["refusal"]
    assert outcome["message"] == (
        f"compile error at line {outcome['line']}: refused {outcome['refusal']}"
    )
    assert [entry.path for entry in seat.front.installed()] == [f"{MODULE}.front"]


def test_the_loop_keeps_going_after_a_refusal(solo: Seat):
    """One refused program does not end the loop: the summary says what happened."""
    out = io.StringIO()
    console = booted(solo, out)
    summary = console.repl(
        [
            line
            for program in (TRIPLE, BROKEN, TWICE)
            for line in [*program.splitlines(), ""]
        ],
        prompt=False,
    )
    assert summary == {
        "submitted": 3,
        "installed": [f"{MODULE}.triple", f"{MODULE}.twice"],
        "refused": 1,
    }
    text = out.getvalue()
    assert f"installed {MODULE}.triple" in text
    assert f"installed {MODULE}.twice" in text
    assert "compile error at line" in text


def test_the_loop_lists_what_the_image_has_installed(solo: Seat):
    """:list is read-only: the Functions of the image and the ids the runtime finds them by."""
    out = io.StringIO()
    console = booted(solo, out)
    console.repl([*TRIPLE.splitlines(), "", ":list", ":quit"], prompt=False)
    assert f"{MODULE}.triple" in out.getvalue()
    assert f"{MODULE}.front" in out.getvalue()
    listed = console.listed()
    assert {entry["path"] for entry in listed["installed"]} == {
        f"{MODULE}.front",
        f"{MODULE}.triple",
    }


def test_one_shot_file_mode_installs_the_program_of_the_file(solo: Seat, tmp_path: Path):
    """`rxf console FILE` takes the same path the loop takes, once."""
    file = tmp_path / "triple.rxf"
    file.write_text(TRIPLE, encoding="utf-8")
    console = booted(solo)
    outcome = console.one_shot(file)
    assert outcome["ok"] is True
    assert outcome["path"] == f"{MODULE}.triple"
    assert outcome["identity"].startswith("0x")
    assert outcome["code_identity"].startswith("0x")
    assert words_of(solo, f"{MODULE}.triple") == rxfc.compile_source(TRIPLE)
    assert call(solo, f"{MODULE}.triple", 7) == (0, 21)


def test_an_include_travels_with_the_request(solo: Seat, tmp_path: Path):
    """The files an `include` names go with the source (Phase 1): the image compiles a program
    whose words the host compiler only writes when it reads the same file."""
    (tmp_path / "scale.rxf").write_text("const SCALE = 5;\n", encoding="utf-8")
    file = tmp_path / "scaled.rxf"
    file.write_text(SCALED, encoding="utf-8")
    console = booted(solo)
    outcome = console.one_shot(file)
    assert outcome["ok"] is True
    assert outcome["path"] == f"{MODULE}.scaled"
    assert words_of(solo, f"{MODULE}.scaled") == rxfc.compile_file(file)
    assert call(solo, f"{MODULE}.scaled", 4) == (0, 9)


def test_the_cli_runs_a_file_and_says_what_the_image_installed(
    solo: Seat, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """`rxf console FILE` and `rxf console --inspect`, the command as a user types it."""
    file = tmp_path / "twice.rxf"
    file.write_text(TWICE, encoding="utf-8")
    broken = tmp_path / "broken.rxf"
    broken.write_text(BROKEN, encoding="utf-8")
    booted(solo)  # what the image's own `rxf live` group boots before a console attaches
    with running_console("shm:console-cli", solo):
        assert main(["console", str(file), "--link=shm:console-cli"]) == 0
        outcome = json.loads(capsys.readouterr().out)
        assert outcome["ok"] is True
        assert outcome["path"] == f"{MODULE}.twice"

        # a refused program: the report is the problem, the exit status says so
        assert main(["console", str(broken), "--link=shm:console-cli"]) == 1
        refused = json.loads(capsys.readouterr().out)
        assert refused["ok"] is False
        assert refused["line"] >= 1

        assert main(["console", "--inspect", "--link=shm:console-cli"]) == 0
        listed = json.loads(capsys.readouterr().out)
        assert {entry["path"] for entry in listed["installed"]} == {
            f"{MODULE}.front",
            f"{MODULE}.twice",
        }


def test_the_cli_loop_reads_programs_from_stdin(
    solo: Seat,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    """`rxf console` alone is the loop: the terminal text goes to the terminal, the summary
    is the JSON after it."""
    monkeypatch.chdir(tmp_path)  # an `include` names the sources beside the terminal
    monkeypatch.setattr(
        "sys.stdin", io.StringIO("\n".join([*TRIPLE.splitlines(), "", ":quit", ""]))
    )
    booted(solo)  # what the image's own `rxf live` group boots before a console attaches
    with running_console("shm:console-loop", solo):
        assert main(["console", "--link=shm:console-loop"]) == 0
    written = capsys.readouterr().out
    assert f"installed {MODULE}.triple" in written
    assert json.loads(written[written.index("{") :]) == {
        "submitted": 1,
        "installed": [f"{MODULE}.triple"],
        "refused": 0,
    }


def test_a_link_with_no_running_image_is_refused(
    capsys: pytest.CaptureFixture[str],
):
    """No runner, no console: the CLI says so and does not pretend to compile anything."""
    with pytest.raises(world.ConsoleError):
        world.attach("shm:nothing-runs-here")
    assert main(["console", "--link=shm:nothing-runs-here"]) == 2
    assert "gives no runner" in capsys.readouterr().err


def test_without_a_front_end_the_answer_is_clear(solo: Seat):
    """An image that was never booted answers plainly, and the loop goes on."""
    console = Console(solo.front, io.StringIO())
    with pytest.raises(NotReady, match="boot it first"):
        console.submit(TRIPLE)
    out = io.StringIO()
    console = Console(solo.front, out)
    summary = console.repl([*TRIPLE.splitlines(), "", ":quit"], prompt=False)
    assert summary == {"submitted": 1, "installed": [], "refused": 1}
    assert "boot it first" in out.getvalue()


def test_a_source_that_names_no_program_is_refused_by_the_terminal(solo: Seat):
    """Without `program NAME(` there is no Function to name; `--name` is the way out."""
    console = booted(solo)
    outcome = console.submit("const N = 1;\n")
    assert outcome["ok"] is False
    assert "names no program" in outcome["message"]


# ---- what serve and live register (the seam of console/world.py) ---------------------------------


def test_a_live_session_registers_the_world_of_the_image_it_runs(solo: Seat):
    """The live instance (`rxf live` runs one) registers its world at its link (docs/19, "who
    runs when"): `attach` answers the channel of the running image and its front end, the frame
    and the root of the image answer, and what the console types is compiled by that image."""
    booted(solo)  # what the image's own `rxf live` group boots before a console attaches
    with Session("shm:live-runs", solo.channel.client, solo.front) as session:
        attached = world.attach("shm:live-runs")
        assert attached is session.world
        assert attached.client.ping(b"the channel") == b"the channel"
        assert attached.client.resolve("") == HANDLE_ROOT
        assert attached.client.digest() == solo.channel.loaded().digest()
        outcome = Console(attached.front, io.StringIO()).submit(TRIPLE)
        assert outcome["ok"] is True
        assert outcome["path"] == f"{MODULE}.triple"
        assert call(solo, f"{MODULE}.triple", 7) == (0, 21)


def test_the_cli_attaches_to_the_session_the_image_runs(
    solo: Seat, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """`rxf console --link=SPEC` attaches to a running session: the whole path against the world
    the live instance registered, with no harness of the test in between."""
    booted(solo)
    file = tmp_path / "twice.rxf"
    file.write_text(TWICE, encoding="utf-8")
    with Session("shm:live-cli", solo.channel.client, solo.front):
        assert main(["console", str(file), "--link=shm:live-cli"]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["ok"] is True
    assert outcome["path"] == f"{MODULE}.twice"
    assert words_of(solo, f"{MODULE}.twice") == rxfc.compile_source(TWICE)


def test_the_registration_leaves_with_the_session(solo: Seat):
    """Closing is clean: the link gives no runner again, closing twice is nothing, and a fresh
    register at the same spec works."""
    session = Session("shm:leaves-with-it", solo.channel.client, solo.front).open()
    assert world.attach("shm:leaves-with-it") is session.world
    session.close()
    session.close()  # closing twice is not a second refusal
    with pytest.raises(world.ConsoleError, match="gives no runner"):
        world.attach("shm:leaves-with-it")
    fresh = Session("shm:leaves-with-it", solo.channel.client, solo.front).open()
    assert world.attach("shm:leaves-with-it") is fresh.world
    fresh.close()


def test_a_second_world_at_the_same_link_is_refused_not_stolen(solo: Seat):
    """One link has one runner: a second registration is refused with the seam's own refusal,
    and the runner that is there keeps its link until it closes."""
    first = Session("shm:one-runner", solo.channel.client, solo.front).open()
    second = Session("shm:one-runner", solo.channel.client, solo.front)
    try:
        with pytest.raises(world.WorldIsRegistered, match="shm:one-runner"):
            second.open()
        assert world.attach("shm:one-runner") is first.world
    finally:
        first.close()
    second.open()  # the link is free again
    assert world.attach("shm:one-runner") is second.world
    second.close()


def test_two_racing_registrations_produce_exactly_one_world(solo: Seat):
    """The check-and-set of the one registry is atomic for every caller (`console/world.py`):
    two registrations racing for one free link — a direct `world.register` against another, or
    against the `open` of a `Session` — cannot both pass the check and take it. Exactly one
    world stands at the link, the other answers `WorldIsRegistered`, and nothing is stolen: what
    `attach` finds is the winner's world, round after round."""
    for round_number in range(12):  # the window is small: race it again and again
        spec = f"shm:register-race-{round_number}"
        session = Session(spec, solo.channel.client, solo.front)
        made = [world.World(solo.channel.client, solo.front) for _ in range(2)]
        barrier = threading.Barrier(2)
        outcomes: dict[str, object] = {}

        def run(
            tag: str,
            work: Callable[[], world.World],
            barrier: threading.Barrier = barrier,
            outcomes: dict[str, object] = outcomes,
        ) -> None:
            barrier.wait(timeout=60)
            try:
                outcomes[tag] = work()
            except BaseException as problem:  # noqa: BLE001 - the race outcome is what is pinned
                outcomes[tag] = problem

        def take(which: int, spec: str = spec, made: list[world.World] = made) -> world.World:
            world.register(spec, lambda name, timeout: made[which])
            return made[which]

        if round_number % 2 == 0:  # the named race: a direct register against a Session.open
            works = [
                ("session", lambda session=session: session.open().world),
                ("direct", lambda: take(0)),
            ]
        else:  # and the plain one: two direct registers
            works = [("one", lambda: take(0)), ("two", lambda: take(1))]

        racers = [threading.Thread(target=run, args=(tag, work)) for tag, work in works]
        for racer in racers:
            racer.start()
        for racer in racers:
            racer.join(timeout=60)
            assert not racer.is_alive(), "a registration never finished"

        worlds = [o for o in outcomes.values() if isinstance(o, world.World)]
        refused = [o for o in outcomes.values() if isinstance(o, world.WorldIsRegistered)]
        assert len(worlds) == 1, outcomes
        assert len(refused) == 1, outcomes
        assert world.attach(spec) is worlds[0]
        session.close()
        world.forget(spec)
        assert world.registered(spec) is False


def test_nothing_is_left_registered_when_a_runner_ends_without_closing():
    """What runs the image takes its registration with it even when it ends without closing: no
    process ends with a world still registered at its link (the exit hook of the session)."""
    done = subprocess.run(
        [sys.executable, "-m", "tests.session_at_exit"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT / "tools" / "native"), str(ROOT)]),
        },
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "registered at exit: no"


# ---- the two faces of one link: `rxf live` (frames) and `rxf console` (world) --------------------


def test_the_frames_face_sees_what_the_world_face_installed(
    solo: Seat, tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """`rxf live` and `rxf console` are two faces of one link, not two ways to one end. The
    world face compiles and installs through the session; the frames face (`rxf live digest`,
    `rxf live console`) speaks on the same channel — the seam's, not a second wire — and sees
    the same image and the Function the world face installed."""
    booted(solo)
    file = tmp_path / "twice.rxf"
    file.write_text(TWICE, encoding="utf-8")
    with Session("shm:two-faces", solo.channel.client, solo.front):
        assert main(["console", str(file), "--link=shm:two-faces"]) == 0
        capsys.readouterr()  # what the world face printed

        assert main(["live", "digest", "shm:two-faces"]) == 0
        assert capsys.readouterr().out.strip() == solo.channel.loaded().digest()

        script = tmp_path / "faces.txt"
        script.write_text(
            f"children {MODULE}\nresolve {MODULE}.twice\nquit\n", encoding="utf-8"
        )
        assert main(["live", "console", "shm:two-faces", str(script)]) == 0
        answered = capsys.readouterr().out
        assert f"{MODULE}.twice" in answered  # the frames face lists what the world face installed


def test_the_frames_face_follows_the_registration_lifecycle(
    solo: Seat, capsys: pytest.CaptureFixture[str]
):
    """Anmeldung and Ablmeldung are what both faces see: while the session is open the frames
    face speaks on its channel; when it closes, the link has no runner and the face would open
    a wire of its own (which this name has none of)."""
    session = Session("shm:faces-lifecycle", solo.channel.client, solo.front).open()
    try:
        assert world.registered("shm:faces-lifecycle") is True
        assert main(["live", "digest", "shm:faces-lifecycle"]) == 0
        assert capsys.readouterr().out.strip() == solo.channel.loaded().digest()
    finally:
        session.close()
    assert world.registered("shm:faces-lifecycle") is False
    assert main(["live", "digest", "shm:faces-lifecycle"]) == 2
    assert "rxf:" in capsys.readouterr().err


def test_the_frames_face_uses_the_wire_when_no_runner_runs_here(
    capsys: pytest.CaptureFixture[str],
):
    """Nothing runs here, but a rig or firmware may run at the far end of a line: without a
    registration the frames face opens the wire (`live.link`), and what no wire reaches is
    refused plainly instead of pretending a world is here."""
    name = "shm:s1-no-runner-here"
    assert world.registered(name) is False
    assert main(["live", "digest", name]) == 2
    assert "rxf:" in capsys.readouterr().err


# ---- one gate for the traffic of every face: the console, the app and `rxf live` serialize --------


def test_a_console_operation_waits_for_the_transaction_an_app_operation_holds(solo: Seat):
    """A console operation and an app mutation on one image serialize at one gate. The app side
    is the shape of `server/app.py`'s install route: compile-then-install inside one
    `Session.transaction()`. While that transaction is in flight the console's program does not
    run — not between the app's compile and its install — and once it is over the console's
    program runs, and BOTH mutations are intact: two Functions of the image that answer."""
    booted(solo)
    with Session("shm:one-gate", solo.channel.client, solo.front) as session:
        attached = world.attach("shm:one-gate")
        console = Console(attached.front, io.StringIO())
        holding = threading.Event()
        released = threading.Event()
        done = threading.Event()
        answered: dict[str, Any] = {}
        installed: dict[str, Any] = {}

        def app_operation() -> None:
            with session.transaction():  # one channel transaction at a time (session.py)
                verdict = attached.front.compile(TWICE.encode())
                if verdict.ok:
                    attached.front.install("twice", verdict.words, TWICE.encode())
                installed["verdict"] = verdict
                holding.set()
                released.wait(timeout=300)

        def console_operation() -> None:
            if holding.wait(timeout=300):
                answered["outcome"] = console.submit(QUAD)
                done.set()

        workers = [
            threading.Thread(target=app_operation),
            threading.Thread(target=console_operation),
        ]
        for worker in workers:
            worker.start()
        assert holding.wait(timeout=300)
        assert not done.wait(2), "a console operation landed inside the app's transaction"
        released.set()
        assert done.wait(300), "the console operation never ran once the app was done"
        for worker in workers:
            worker.join(timeout=300)
            assert not worker.is_alive()

        assert installed["verdict"].ok
        assert answered["outcome"]["ok"] is True
        assert answered["outcome"]["path"] == f"{MODULE}.quad"
        assert call(solo, f"{MODULE}.twice", 7) == (0, 14)
        assert call(solo, f"{MODULE}.quad", 7) == (0, 28)


def test_two_console_operations_at_one_world_serialize_as_two_intact_installs(solo: Seat):
    """Two console operations fired at the same instant (two terminals on one link) serialize at
    the same gate: both land as intact commits — no install compiles against the other's tail
    and the image refuses nothing mid-race — and both Functions run afterwards."""
    booted(solo)
    with Session("shm:console-race", solo.channel.client, solo.front):
        attached = world.attach("shm:console-race")
        before = attached.client.hello()
        barrier = threading.Barrier(2)
        answers: list[dict[str, Any]] = []

        def submit(source: str) -> None:
            barrier.wait(timeout=60)
            answers.append(Console(attached.front, io.StringIO()).submit(source))

        racers = [
            threading.Thread(target=submit, args=(source,)) for source in (TWICE, QUAD)
        ]
        for racer in racers:
            racer.start()
        for racer in racers:
            racer.join(timeout=300)
            assert not racer.is_alive(), "a console operation never finished"

        assert len(answers) == 2, answers
        for answer in answers:
            assert answer["ok"] is True, answer
        assert {answer["path"] for answer in answers} == {
            f"{MODULE}.twice",
            f"{MODULE}.quad",
        }
        hello = attached.client.hello()
        assert hello.rejects == before.rejects, "the image refused a delta mid-race"
        assert hello.commits >= before.commits + 2
        assert call(solo, f"{MODULE}.twice", 7) == (0, 14)
        assert call(solo, f"{MODULE}.quad", 7) == (0, 28)


def test_the_frames_face_waits_for_the_transaction_too(
    solo: Seat, capsys: pytest.CaptureFixture[str]
):
    """`rxf live` commands speak on the channel of the world a runner registered — inside the
    same gate every other face holds. While one operation is in flight a command waits at it
    instead of landing between its requests (a torn stage buffer, an emit read torn in half);
    when the operation is over the command answers."""
    with Session("shm:frames-gate", solo.channel.client, solo.front) as session:
        holding = threading.Event()
        released = threading.Event()
        done = threading.Event()
        status: list[int] = []

        def in_flight() -> None:
            with session.transaction():  # one operation on the channel, not over yet
                holding.set()
                released.wait(timeout=300)

        def command() -> None:
            if holding.wait(timeout=300):
                status.append(main(["live", "digest", "shm:frames-gate"]))
                done.set()

        workers = [threading.Thread(target=in_flight), threading.Thread(target=command)]
        for worker in workers:
            worker.start()
        assert holding.wait(timeout=300)
        assert not done.wait(2), "an `rxf live` command landed inside a running operation"
        released.set()
        assert done.wait(300), "the command never answered once the operation was over"
        for worker in workers:
            worker.join(timeout=300)
            assert not worker.is_alive()

        assert status == [0]
        assert capsys.readouterr().out.strip() == solo.channel.loaded().digest()
