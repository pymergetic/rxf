"""One writer at a time in ``boot/build``.

``boot/build`` is a shared directory: ``boot_sync.build`` runs ``make`` in it and reads the
artifacts back, ``boot/run_smoke.sh`` builds and boots every seat, and a test deletes an artifact
to see it rebuilt. Two of them at once (the lanes run in parallel) corrupt each other: a ``make``
that writes while another reads, an artifact that disappears between a build and its read.

``writing()`` takes an exclusive ``flock`` on ``boot/.build.lock`` for the whole of the work.
``run_smoke.sh`` takes the same lock with the ``flock`` command, so the shell and Python writers
exclude each other. The lock is reentrant within a thread, so a writer may call another.
"""

from __future__ import annotations

import contextlib
import fcntl
import threading
from collections.abc import Iterator
from pathlib import Path

LOCK = Path(__file__).resolve().parents[2] / "boot" / ".build.lock"

_held = threading.local()


@contextlib.contextmanager
def writing() -> Iterator[None]:
    """Hold the boot build lock; wait for whoever has it."""
    depth = getattr(_held, "depth", 0)
    if depth:
        _held.depth = depth + 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    with LOCK.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
            fcntl.flock(handle, fcntl.LOCK_UN)
