"""The tutor: the run-cell machine, stepped by hand (docs/21, the teaching surface).

The page is static except for one seam: the shell's live slot (docs/21: the attached session or
exactly ``no image``), which is filled here at serve time. The engine behind the page lives in
``static/tutor/tutor.js`` and runs on the facts the page pins as ``data-pin`` chips — the same
constants ``tests/test_tutor.py`` checks against ``tests/host/smp_*.h``, ``core/ground.h`` and
``docs/20-EXECUTION-POLICY.md``, the sources the Model page (/model) is pinned to.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pymergetic.rxf.core.object import Image
    from pymergetic.rxf.server.live.session import Session

TEMPLATES = Path(__file__).with_name("templates")
STATIC = Path(__file__).with_name("static") / "tutor"
PAGE = TEMPLATES / "tutor.html"
LIVE_SLOT = "<!--LIVE_SLOT-->"

# The wording of docs/21, "the live slot": the same two answers on every surface.
NO_IMAGE = "no image"


def script() -> str:
    """The tutor's engine and UI wiring (plain DOM/SVG, no WebGPU, no Math.random)."""
    return (STATIC / "tutor.js").read_text(encoding="utf-8")


def style() -> str:
    """The tutor's page CSS: the docs/21 tokens, named as the spec names them."""
    return (STATIC / "tutor.css").read_text(encoding="utf-8")


def live_slot(info: dict[str, Any], spec: str | None) -> str:
    """The one live slot of docs/21: the attached session, or exactly ``no image``.

    ``spec`` is the link the session runs at (``pymergetic.rxf.console.world``); ``info`` is the
    inspector's summary, which carries the cell count and the state of the image's check.
    """
    if spec is None:
        return NO_IMAGE
    mark = "ok" if info.get("issue_count", 0) == 0 else "bad"
    cells = info.get("objects", 0)
    return (
        f"live: <code>{html.escape(spec)}</code> &middot; {cells} cells &middot; "
        f'checksum <span class="{mark}">&bull; {mark}</span>'
    )


def render(image: Image, session: Session | None) -> str:
    """The /tutor page with the live slot filled in (one link, one truth, docs/21).

    The shell's live slot reads exactly ``no image`` and a serve-time fill only ever replaces
    that default (server/static/ui/shell.html); the ``<!--LIVE_SLOT-->`` marker in the template
    is the seam this fill consumes. So an attached session replaces the default everywhere the
    shell spells it and the slot names the session; with no session the default is the truth.
    """
    spec = None if session is None or session.world is None else session.spec
    from pymergetic.rxf.server.inspector import Inspector

    slot = live_slot(Inspector(image).info(), spec)
    page = PAGE.read_text(encoding="utf-8").replace(LIVE_SLOT, "")
    return page.replace(NO_IMAGE, slot)
