"""The world a console talks to: the channel of a running image and the machine that runs it.

The frames are the link (`live.link`, `pty:`/`tty:`/`fd:`/`shm:`). The machine is what the host
of the image registers here: a run is not a frame, whoever runs the image runs its Functions
(docs/19, "who runs when"). A test harness, a firmware rig or a future run operation of the
channel is that host; the console does not care which.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pymergetic.rxf.live.client import Client
from pymergetic.rxf.live.front import Front
from pymergetic.rxf.live.link import LinkError

Builder = Callable[[str, float], "World"]


class ConsoleError(LinkError):
    """The console cannot reach a running image."""


class WorldIsRegistered(ConsoleError):
    """The spec already has a runner: one link has one image, and a second one is refused."""


def _nothing() -> None:
    """The channel of a world that the console does not own."""


@dataclass(frozen=True)
class World:
    """The channel of a running image and the front end of its language on top."""

    client: Client
    front: Front
    close: Callable[[], None] = field(default=_nothing)


_BUILDERS: dict[str, Builder] = {}


def register(spec: str, build: Builder) -> None:
    """Attach the world of the image at SPEC: whoever runs the image builds it here.

    `build(spec, timeout)` connects the channel and wraps the machine of the host in a `Front`.
    A spec whose image already runs is refused, not stolen (`WorldIsRegistered`): the runner that
    registered first keeps its link until it forgets it.
    """
    if spec in _BUILDERS:
        raise WorldIsRegistered(
            f"{spec} already has a runner: whoever runs the image registered its machine first "
            "(one link has one image)"
        )
    _BUILDERS[spec] = build


def forget(spec: str) -> None:
    """What was registered at SPEC is no longer the world of a running image."""
    _BUILDERS.pop(spec, None)


def registered(spec: str) -> bool:
    """Whether a runner runs at SPEC: `attach` would find the world of an image here.

    A command that only speaks frames (`rxf live`) asks this before it opens a wire of its own:
    the channel of the runner is the same link, and it is the only one that can run.
    """
    return spec in _BUILDERS


def attach(spec: str, timeout: float = 30.0) -> World:
    """The world of the running image at SPEC (`pty:...`, `tty:...`, `fd:...`, `shm:name`)."""
    build = _BUILDERS.get(spec)
    if build is None:
        raise ConsoleError(
            f"{spec} gives no runner: whoever runs the image attaches its machine with "
            f"pymergetic.rxf.console.world.register (running a Function is not a frame yet)"
        )
    return build(spec, timeout)
