"""The session of an image this process runs: its channel, and the world `rxf console` attaches.

A run is not a frame: whoever runs the image is the host of its Functions (docs/19, "who runs
when") and hands the session its channel (`Client`) and the front end on top (`Front`). While the
session is open its world is registered at the link the console names (`pty:...`, `tty:...`,
`fd:...`, `shm:name`): `rxf console --link=SPEC` attaches the running image from there. Closing
takes the registration with it — the link gives no runner again, and a fresh register at the same
spec works. One link has one runner: a spec that is taken is refused with the seam's own refusal
(`WorldIsRegistered` of `console/world.py`, the one registry) and keeps its runner.

The channel of one image serves one request at a time and its operations are transactions — the
stage buffer, the base digest a delta is compiled against, the request memory of a run — so the
session owns ONE transaction gate (`transaction`): every operation that takes the channel, the
feed reads of `GET /api/live` and the editor's compile / install / poke alike, runs inside it.
Two of them never interleave inside one another; the caller waits (or skips by not entering).

`rxf serve` runs one behind its inspector (`server/app.py` holds it for the lifetime of the app),
and the host of `rxf live`'s channel (the harness, a rig) opens one at the link it serves — the
commands of `rxf live` then speak on the same channel (`live/cli` finds it through this seam). The
console does not own the channel of such a world, so attaching and leaving changes nothing about
the registration.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Self

from pymergetic.rxf.console import world
from pymergetic.rxf.live.client import Client
from pymergetic.rxf.live.front import Front

# The registration is a check-and-set on the one registry (`world.register` calls it), so it is
# taken here: two attaches that race cannot both pass the check and end up registered at one
# link. `Session.open` is where a runner attaches (server/live/runner.py reserves through it).
_LINKS = threading.Lock()


class Session:
    """One running image, its channel, and the registration a console attaches through."""

    def __init__(self, spec: str, client: Client, front: Front) -> None:
        self.spec = spec
        self._world = world.World(client, front)
        self._open = False
        self._gate = threading.RLock()

    @property
    def world(self) -> world.World:
        """The channel of the running image and its front end: what `attach` answers at the spec."""
        return self._world

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One operation on the channel of the running image at a time.

        Whoever enters holds the channel for the whole of one operation — a feed read's snapshot,
        an install's compile-then-apply, a poke's emit-then-apply — and everyone else waits
        instead of landing mid-flight inside it. The gate is a thread lock because the server
        drives the channel from its threadpool (sync endpoints run one thread per request); it is
        reentrant, so an operation that enters again (through the seam of another face) does not
        deadlock against itself.
        """
        with self._gate:
            yield

    def open(self) -> Self:
        """Register the world at its link: `rxf console --link=SPEC` attaches it from here on.

        Open when it is open is the same session, not a second registration. A link whose image
        already runs answers `WorldIsRegistered` and keeps the runner that is there — atomically,
        so racing attaches cannot both take one link.
        """
        if self._open:
            return self
        with _LINKS:
            world.register(self.spec, self._build)
        atexit.register(self.close)  # a runner that ends without closing leaves nothing behind
        self._open = True
        return self

    def close(self) -> None:
        """The session is over: what was registered at its link is no longer a running image.

        Closing twice is nothing, and after it a fresh register at the same spec works.
        """
        if not self._open:
            return
        self._open = False
        atexit.unregister(self.close)
        with _LINKS:
            world.forget(self.spec)

    def _build(self, spec: str, timeout: float) -> world.World:
        """The world of the image that runs here; `attach` calls this once per console."""
        return self._world

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *problem: object) -> None:
        self.close()
