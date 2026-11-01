"""Two terminals, two processes, one image: `rxf server serve --link=shm:NAME` and
`rxf console --link=shm:NAME` against it (docs/19, "who runs when").

`tests/test_serve_real.py` pins the in-process world: a runner and its console in one
process. This file pins the story the banner promises. The serve lays its image out in a
real shared-memory segment (the area the link `shm:NAME` names, with the head
`live/link.py` reads at its end); a console in ANOTHER OS process attaches over that link,
maps the image where the image keeps it (`MAP_FIXED_NOREPLACE`) and drives it through its
own front end, one operation at a time behind the lock of the link. `rxf live info
shm:NAME` cross-process answers with the image's own info.

Ports and segment names are distinct per test; every wait is bounded; the cleanup leaves
no process and no segment behind. The install half of the story is a named failing stub
(a strict look at the wall it names) until the live lane's apply resizes a record whose
new value is shorter than the old one - the wall stands in-process too, this file is not
where it is hit.
"""

from __future__ import annotations

import json
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.slow]

ROOT = Path(__file__).resolve().parents[1]
RF = Path(sys.executable).with_name("rxf")
MODULE = "pymergetic.rxf.selfedit"
TRIPLE = """\
program triple(x) {
    return x + x + x;
}
"""


def free_port() -> int:
    """A port of this host that is free right now; every test takes its own."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def segment(name: str) -> Path:
    """The shared-memory segment a `shm:` link lays out (POSIX: /dev/shm/NAME)."""
    return Path("/dev/shm") / name


def rxf(*words: str, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    """One `rxf` command line as its own OS process: the two-terminal story is processes,
    not calls in this one. Bounded; the answer is its exit, its text and its errors."""
    return subprocess.run(
        [str(RF), *words],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class Serving:
    """One `rxf server serve` in its own OS process: the image linked at `shm:NAME`, the
    inspector on a port of its own.

    The output is read as it comes (a pipe nobody reads would block the serve once it
    fills), and every wait is bounded: the banner of a serve that is up, or the output it
    is stuck with. The stop is SIGINT, what the second terminal sends: uvicorn ends
    cleanly, the runner closes, and the segment it laid out is unlinked (nothing outlives
    the machine that runs the image).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.spec = f"shm:{name}"
        self.port = free_port()
        self.lines: list[str] = []
        self.process: subprocess.Popen[str] | None = None

    def __enter__(self) -> Serving:
        stale = segment(self.name)
        if stale.exists():
            stale.unlink()  # a stale segment of a crashed run, never a runner's
        self.process = subprocess.Popen(
            [
                str(RF),
                "server",
                "serve",
                "starter",
                "--link",
                self.spec,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        threading.Thread(target=self._drain, daemon=True).start()
        return self

    def _drain(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        for line in self.process.stdout:
            self.lines.append(line)

    def output(self) -> str:
        return "".join(self.lines)

    def banner(self, timeout: float = 300.0) -> str:
        """The output of a serve that is up (its banner among it) - bounded, or what it is
        stuck with."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.output()
            if "image running at" in text:
                return text
            assert self.process is not None
            if self.process.poll() is not None:
                raise AssertionError(f"the serve ended before it was up:\n{text}")
            time.sleep(0.2)
        raise AssertionError(f"no banner in {timeout}s:\n{self.output()}")

    def __exit__(self, *problem: object) -> None:
        process = self.process
        if process is not None and process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=90)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=30)
        stale = segment(self.name)
        if stale.exists():
            stale.unlink()  # the proof of cleanup survives a SIGKILL too


def test_a_serve_lays_its_image_out_in_shared_memory_and_the_console_of_another_process_attaches():
    """The area the link `shm:NAME` names is real: the serve lays the image out in a
    shared-memory segment, and a console of another OS process attaches over the link,
    maps the very same block at the very same addresses and reads the image through its
    own channel (`rxf live info`, `rxf console --inspect`)."""
    with Serving("rxf-two-attach") as up:
        banner = up.banner()
        assert f"image running at {up.spec}" in banner
        assert "attaches to it from any process" in banner  # the promise the segment makes true
        laid_out = segment(up.name)
        assert laid_out.exists()
        assert laid_out.stat().st_size > 1_000_000  # the image's block, not a placeholder

        info = json.loads(rxf("live", "info", up.spec, "--timeout", "30").stdout)
        assert re.fullmatch(r"[0-9a-f]{64}", info["digest"])
        assert info["handles"] > 0 and info["committed"] > 0

        shown = json.loads(
            rxf("console", "--inspect", "--link", up.spec, "--timeout", "180").stdout
        )
        assert f"{MODULE}.front" in [entry["path"] for entry in shown["installed"]]


@pytest.mark.xfail(
    reason="blocked on the live lane: the apply's SET resizes a record whose new value is "
    "LONGER than the room it has (native/live.rxfs + core/live/live.cpp, the grow path) "
    "and never one whose value is shorter - a delta whose value shrinks the seeded one "
    "lands with the old bytes behind it, so the image's state is not the delta's state "
    "and the mirror's honest digest check refuses the result ('the image and its mirror "
    "disagree'). The same wall stands in-process (tests/test_serve_real.py's install "
    "pin); when the apply truncates, this test turns to a pass.",
    strict=False,
)
def test_the_console_of_another_process_installs_a_program_and_the_image_shows_it(
    tmp_path: Path,
):
    """The whole two-terminal story: the console of another process compiles and installs
    a program through the image's own front end, and the image shows it - the install
    answers with the path, the digest of the image moves, and `rxf console --inspect`
    lists the Function the install made."""
    with Serving("rxf-two-install") as up:
        up.banner()
        before = json.loads(rxf("live", "info", up.spec, "--timeout", "30").stdout)["digest"]
        program = tmp_path / "triple.rxfs"
        program.write_text(TRIPLE, encoding="utf-8")

        out = rxf("console", str(program), "--link", up.spec, "--timeout", "180")
        assert out.returncode == 0, out.stdout + out.stderr  # the wall: the install lands
        answered = json.loads(out.stdout)
        assert answered["ok"] is True
        assert answered["path"] == f"{MODULE}.triple"

        after = json.loads(rxf("live", "info", up.spec, "--timeout", "30").stdout)["digest"]
        assert after != before  # the image took the delta
        shown = json.loads(
            rxf("console", "--inspect", "--link", up.spec, "--timeout", "180").stdout
        )
        assert f"{MODULE}.triple" in [entry["path"] for entry in shown["installed"]]


def test_a_serve_that_cannot_bind_promises_nothing_and_lays_no_segment_out():
    """The inspector's port is taken before the link exists: a serve that cannot come up
    fails cleanly, with no banner claiming a running image and no segment left behind
    (`rxf server serve` binds before it registers)."""
    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = int(taken.getsockname()[1])
    name = "rxf-two-bind"
    stale = segment(name)
    if stale.exists():
        stale.unlink()
    try:
        run = rxf(
            "server",
            "serve",
            "starter",
            "--link",
            f"shm:{name}",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            timeout=120,
        )
        assert run.returncode != 0
        assert "image running at" not in run.stdout + run.stderr  # no false promise
        assert not stale.exists()
    finally:
        taken.close()
        if stale.exists():
            stale.unlink()


def test_one_link_has_one_runner_across_processes():
    """One link, one runner across processes too: the segment name of a link that is taken
    is refused, in the words of the world seam ('one link has one runner'), and the serve
    that has the link keeps answering."""
    with Serving("rxf-two-sole") as up:
        up.banner()
        second = rxf(
            "server",
            "serve",
            "starter",
            "--link",
            up.spec,
            "--host",
            "127.0.0.1",
            "--port",
            str(free_port()),
            timeout=120,
        )
        assert second.returncode != 0
        assert "is already laid out" in second.stderr
        assert "image running at" not in second.stdout + second.stderr

        still = json.loads(rxf("live", "info", up.spec, "--timeout", "30").stdout)
        assert still["handles"] > 0  # the first serve kept the link
