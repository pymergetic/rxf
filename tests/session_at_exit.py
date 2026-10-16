"""What a runner that ends without closing leaves behind: a session must not outlive its process.

Run as a module (`python -m tests.session_at_exit` from the package root): it runs a real image on
one machine of the harness (`tests/live_world`) and registers its world at `shm:at-exit`, then
simply ends — no close. The checker is registered before the session opens, so at exit it runs
after the session's own hook (the handlers of a process run last in, first out) and says whether
the link still has a runner. `tests/test_console.py` reads the answer.
"""

from __future__ import annotations

import atexit
from pathlib import Path

from pymergetic.rxftk.console import world
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.server.live import Session

from .test_console import seat_of

SPEC = "shm:at-exit"
STARTER = Path("starter")
MACHINE = "x86_64"


def checked() -> None:
    """What the registry holds at exit, after every exit hook has run."""
    try:
        world.attach(SPEC)
    except world.ConsoleError:
        print("registered at exit: no")
    else:
        print("registered at exit: yes")


def main() -> None:
    atexit.register(checked)  # registered first: it runs after the hook of the session
    seat = seat_of(tree.load(STARTER), MACHINE)
    Session(SPEC, seat.channel.client, seat.front).open()  # the runner ends without closing


if __name__ == "__main__":
    main()
