"""Where the MicroPython reference of the differentials lives (docs/23, decision 10).

The unix ports of the workspace are siblings of the worktrees: ``<os-sdk>/packages/<seat>/ports/
unix/build-standard/micropython``. A worktree sits at ``<os-sdk>/worktrees/<name>``, the main
checkout at ``<os-sdk>``, so the packages directory is found by walking up, never by a fixed
number of parents (a fixed count found nothing from a worktree and silently skipped every
differential pin).
"""

import os
from pathlib import Path

SEATS = ("micropython-wasmmod", "micropython")


def packages_dir() -> Path | None:
    """The ``packages`` directory of the workspace this file lives in, or None."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "packages").is_dir() and (parent / "packages" / "micropython").is_dir():
            return parent / "packages"
    return None


def micropythons() -> list[str]:
    """The MicroPython unix binaries of the workspace: both seats where both exist.

    ``RXPY_MPY`` names one to pin against explicitly; without it the wasmmod seat is tried first,
    then the upstream checkout. Nothing here is a golden table: where no binary exists the
    differential tests skip and say so."""
    named = os.environ.get("RXPY_MPY")
    if named:
        return [named]
    root = packages_dir()
    if root is None:
        return []
    found = []
    for seat in SEATS:
        binary = root / seat / "ports" / "unix" / "build-standard" / "micropython"
        if binary.exists():
            found.append(str(binary))
    return found


def seat_of(binary: str) -> str:
    """The seat a binary belongs to (its checkout's name), for test ids."""
    parts = Path(binary).parts
    return parts[parts.index("ports") - 1] if "ports" in parts else Path(binary).name
