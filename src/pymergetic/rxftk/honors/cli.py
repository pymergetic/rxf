"""The `rxf honors` commands: who made this system, and the ledger that stays open.

`rxf honors who IMAGE` is the founding core (roles, people, works, ledger),
`rxf honors about IMAGE NAME` one person or one work with its refs, `rxf honors
append IMAGE TEXT` one entry onto the ledger of IMAGE, and `rxf honors verify
IMAGE` re-checks the laws of docs/36 on the image as it stands. The commands work
on any image of the arena - the starter a seat boots and an image fresh out of the
export alike (docs/36 4: no unix-only shrine).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymergetic.rxftk.cli import Problems
from pymergetic.rxftk.honors import Honors


def _open(image: str) -> Honors:
    """The honors of the image at IMAGE (an arena image file)."""
    return Honors.attach(Path(image).read_bytes())


def who(image: str) -> Any:
    """The founding core of IMAGE: roles, people, works, and the ledger as it stands."""
    return _open(image).who()


def about(image: str, name: str) -> Any:
    """One person or one work of IMAGE, with its refs and the entries vouching for it."""
    return _open(image).about(name)


def append(
    image: str,
    text: str,
    *,
    about: str,
    role: str,
    by: str = "rxf",
    name: str | None = None,
) -> Any:
    """One entry onto the ledger of IMAGE: TEXT about ABOUT, vouched with ROLE, BY
    whoever says it. The ledger never rewrites; the entry takes the next ordinal as
    its name (its image time) unless NAME names it."""
    honors = _open(image)
    entry = honors.append(text, about=about, role=role, by=by, name=name)
    Path(image).write_bytes(honors.arena.to_bytes())
    return {"ok": True, "entry": entry}


def verify(image: str) -> Any:
    """The laws of docs/36 re-checked on IMAGE: every ref lands inside the image,
    an `embodies` lands on code, the Genesis is read-only. Exits 1 with the problems
    when the image has any."""
    report = _open(image).verify()
    if not report["ok"]:
        raise Problems(report)
    return report
