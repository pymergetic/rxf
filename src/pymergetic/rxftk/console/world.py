"""The world a console talks to: the channel of a running image and the machine that runs it.

The frames are the link (`live.link`, `pty:`/`tty:`/`fd:`/`shm:`). The machine is what the host
of the image registers here: a run is not a frame, whoever runs the image runs its Functions
(docs/19, "who runs when"). A test harness, a firmware rig or a future run operation of the
channel is that host; the console does not care which.

One link has one world, and the registration is a check-and-set taken atomically HERE — for
every caller, a `Session.open` and a direct `world.register` alike. Two of them racing cannot
both pass the check and end up registered at one link.

The channel of a world is used behind one transaction gate (`World.transaction`, the session's
gate when the world belongs to one — `server/live/session.py`). Every request that enters the
channel through the views of a world — `client` and `front` alike, the console's programs and
`rxf live`'s commands — enters that gate first, so two callers never interleave inside one
another: no stage buffer torn under a racing `apply`, no emit read torn in half. An operation
that spans requests (compile then install, emit then compile then apply) holds the gate once
around all of them; the view carries it, so no caller can forget to take it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.front import Front
from pymergetic.rxftk.live.link import LinkError

Builder = Callable[[str, float], "World"]
Transaction = Callable[[], AbstractContextManager[None]]


class ConsoleError(LinkError):
    """The console cannot reach a running image."""


class WorldIsRegistered(ConsoleError):
    """The spec already has a runner: one link has one image, and a second one is refused."""


def _nothing() -> None:
    """The channel of a world that the console does not own."""


class _Gated:
    """The channel of a world as its callers see it: `client` and `front` alike.

    Every request that enters the channel enters the world's transaction first — the gate the
    session holds its operations behind (`server/live/session.py`) — so two callers never land
    inside one another's request. `transaction` travels with the view: an operation that spans
    requests holds it once around all of them.
    """

    def __init__(self, inner: Any, transaction: Transaction) -> None:
        self._inner = inner
        self.transaction = transaction

    def __getattr__(self, name: str) -> Any:
        found = getattr(self._inner, name)
        if not callable(found):
            return found

        def inside(*args: Any, **kwargs: Any) -> Any:
            with self.transaction():
                return found(*args, **kwargs)

        return inside


@dataclass(frozen=True)
class World:
    """The channel of a running image and the front end of its language on top.

    `transaction` is the gate the channel is used behind (a session's `transaction` when the
    world belongs to one): `client` and `front` enter it on every request, and an operation that
    spans requests holds it around them. A world whose channel is shared with nobody has none
    (`nullcontext`) and needs none.
    """

    client: Client
    front: Front
    close: Callable[[], None] = field(default=_nothing)
    transaction: Transaction = nullcontext

    def __post_init__(self) -> None:
        object.__setattr__(self, "client", _Gated(self.client, self.transaction))
        object.__setattr__(self, "front", _Gated(self.front, self.transaction))


_BUILDERS: dict[str, Builder] = {}
_REGISTRY = threading.Lock()


def register(spec: str, build: Builder) -> None:
    """Attach the world of the image at SPEC: whoever runs the image builds it here.

    `build(spec, timeout)` connects the channel and wraps the machine of the host in a `Front`.
    A spec whose image already runs is refused, not stolen (`WorldIsRegistered`): the runner that
    registered first keeps its link until it forgets it. The check-and-set is atomic for every
    caller — a `Session.open` and a direct `register` racing here cannot both pass the check.
    """
    with _REGISTRY:
        if spec in _BUILDERS:
            raise WorldIsRegistered(
                f"{spec} already has a runner: whoever runs the image registered its machine first "
                "(one link has one image)"
            )
        _BUILDERS[spec] = build


def forget(spec: str) -> None:
    """What was registered at SPEC is no longer the world of a running image."""
    with _REGISTRY:
        _BUILDERS.pop(spec, None)


def registered(spec: str) -> bool:
    """Whether a runner runs at SPEC: `attach` would find the world of an image here.

    A command that only speaks frames (`rxf live`) asks this before it opens a wire of its own:
    the channel of the runner is the same link, and it is the only one that can run.
    """
    with _REGISTRY:
        return spec in _BUILDERS


def attach(spec: str, timeout: float = 30.0) -> World:
    """The world of the running image at SPEC (`pty:...`, `tty:...`, `fd:...`, `shm:name`)."""
    with _REGISTRY:
        build = _BUILDERS.get(spec)
    if build is None:
        raise ConsoleError(
            f"{spec} gives no runner: whoever runs the image attaches its machine with "
            f"pymergetic.rxftk.console.world.register (running a Function is not a frame yet)"
        )
    return build(spec, timeout)
