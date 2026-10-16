"""The `rxf console` command: type `.rxfs`, the running image compiles and installs it.

One command speaks for its group (the console lives in `console/cli.py`): `rxf console [FILE]`
is the console itself, not `rxf console console [FILE]`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pymergetic.rxf.cli import Problems
from pymergetic.rxf.console.session import Console, beside
from pymergetic.rxf.console.world import attach


def console(
    source: str | None = None,
    *,
    link: str = "shm:rxf",
    name: str | None = None,
    timeout: float = 30.0,
    inspect: bool = False,
) -> Any:
    """The terminal on a running image: type `.rxfs`, the image compiles and installs it.

    This is the world face of a link: compiling and installing runs the image (a run is not a
    frame, docs/19), so it attaches the world of whoever runs it (`console/world`) — `rxf live`
    is the frames face of the same link and works without a runner here.

    With SOURCE, that one file goes through the same path once. Without SOURCE the loop reads
    programs until a blank line ends each (a line of one `.` does too). An `include` names the
    `.rxfs` sources beside SOURCE (or beside the terminal). `--inspect` only says what the image
    has installed. `--link` is where the running image is (docs/19): `shm:name`,
    `pty:/dev/pts/3`, `tty:/dev/ttyUSB0`, `fd:3`. `--name` names the Function of SOURCE when
    its source names no program.
    """
    if source is not None and inspect:
        raise Problems({"ok": False, "message": "--inspect reads; it takes no source"})
    world = attach(link, timeout=timeout)
    session = Console(world.front, sys.stdout, files=beside(Path.cwd()))
    try:
        if inspect:
            return session.listed()
        if source is not None:
            outcome = session.one_shot(source, name=name)
            if not outcome["ok"]:
                raise Problems(outcome)
            return outcome
        return session.repl(sys.stdin, prompt=sys.stdin.isatty())
    finally:
        world.close()
