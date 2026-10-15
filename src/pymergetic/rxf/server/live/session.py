"""The session of an image this process runs: its channel, and the world `rxf console` attaches.

A run is not a frame: whoever runs the image is the host of its Functions (docs/19, "who runs
when") and hands the session its channel (`Client`) and the front end on top (`Front`). While the
session is open its world is registered at the link the console names (`pty:...`, `tty:...`,
`fd:...`, `shm:name`): `rxf console --link=SPEC` attaches the running image from there. Closing
takes the registration with it — the link gives no runner again, and a fresh register at the same
spec works. One link has one runner: a spec that is taken is refused with the seam's own refusal
(`WorldIsRegistered` of `console/world.py`, the one registry) and keeps its runner.

`rxf live` runs one session per image it drives; `rxf serve` runs one behind its inspector
(`server/app.py` holds it for the lifetime of the app). The console does not own the channel of
such a world, so attaching and leaving changes nothing about the registration.
"""

from __future__ import annotations

import atexit
from typing import Self

from pymergetic.rxf.console import world
from pymergetic.rxf.live.client import Client
from pymergetic.rxf.live.front import Front


class Session:
    """One running image, its channel, and the registration a console attaches through."""

    def __init__(self, spec: str, client: Client, front: Front) -> None:
        self.spec = spec
        self._world = world.World(client, front)
        self._open = False

    @property
    def world(self) -> world.World:
        """The channel of the running image and its front end: what `attach` answers at the spec."""
        return self._world

    def open(self) -> Self:
        """Register the world at its link: `rxf console --link=SPEC` attaches it from here on.

        Open when it is open is the same session, not a second registration. A link whose image
        already runs answers `WorldIsRegistered` and keeps the runner that is there.
        """
        if self._open:
            return self
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
        world.forget(self.spec)

    def _build(self, spec: str, timeout: float) -> world.World:
        """The world of the image that runs here; `attach` calls this once per console."""
        return self._world

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(self, *problem: object) -> None:
        self.close()
