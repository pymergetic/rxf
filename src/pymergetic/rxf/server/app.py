"""FastAPI wiring of the core inspector: one image, read only.

With a live session (`server/live`) the app runs what `rxf serve` runs: the world of the image is
registered at the link of the session while the app serves, so `rxf console` attaches to it, and
shut down takes the registration with it (docs/19, "who runs when").
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from pymergetic.rxf.core.kinds import Mobility
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live.client import LiveError
from pymergetic.rxf.server import editor
from pymergetic.rxf.server import tutor as tutor_page
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.server.live.session import Session
from pymergetic.rxf.world import attach_world

PAGE = Path(__file__).with_name("templates") / "inspector.html"
MODEL_PAGE = Path(__file__).with_name("templates") / "model.html"
EDITOR_PAGE = Path(__file__).with_name("templates") / "editor.html"


class SourceBody(BaseModel):
    """The edited source the editor sends to check, compile or install."""

    source: str
    language: str = "rxfs"
    name: str | None = None
    default_parent: str = ""
    doc_source: str = ""


class PokeBody(BaseModel):
    """One cell poke: the field, and the value to write (a payload word carries an offset)."""

    path: str
    field: str
    value: int = 0
    offset: int = 0


def found[Result](call: Callable[..., Result], *args: object) -> Result:
    """The call, with a missing object answered as 404."""
    try:
        return call(*args)
    except KeyError as error:
        raise HTTPException(404, f"no object at {error.args[0]!r}") from None


def live_seam[Result](work: Callable[[], Result]) -> Result:
    """A call that drives the live channel: when the channel refuses mid-flight (another caller on
    the same link landed between the front's delta operations), the answer is the channel's own
    words as a 409 — never an unstructured 500 that buries them."""
    try:
        return work()
    except LiveError as problem:
        raise HTTPException(409, str(problem)) from problem


def create_app(image: Image, *, session: Session | None = None) -> FastAPI:
    """The inspector on one image, read only.

    With `session`, the app runs what `rxf serve` runs: while it serves, the world of the image
    that runs is registered at the link of the session (`rxf console --link=...` attaches to it),
    and shutting down takes the registration with it. A link whose image already runs refuses the
    registration with `WorldIsRegistered` instead of stealing it.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if session is None:
            yield
        else:
            with session:
                yield

    inspector = Inspector(image)
    app = FastAPI(title="RXF inspector", lifespan=lifespan)

    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

    def model_page() -> str:
        return MODEL_PAGE.read_text(encoding="utf-8")

    def tutor_page_html() -> str:
        """The tutor (docs/21): the run-cell machine stepped by hand, with the one live slot."""
        return tutor_page.render(image, session)

    def info() -> dict[str, Any]:
        return inspector.info()

    def children(
        path: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(500, ge=1, le=5000),
    ) -> dict[str, Any]:
        return found(inspector.children, path, offset, limit)

    def obj(path: str) -> dict[str, Any]:
        return found(inspector.object, path)

    def detail(path: str) -> dict[str, Any]:
        return found(inspector.detail, path)

    def memory(
        start: int | None = None,
        end: int | None = None,
        bins: int = 256,
    ) -> dict[str, Any]:
        return inspector.memory(start, end, bins)

    def search(
        q: str = Query(min_length=1), limit: int = Query(100, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        return inspector.search(q, limit)

    def delta() -> Response:
        return Response(inspector.delta(), media_type="application/xml")

    def editor_page() -> str:
        return EDITOR_PAGE.read_text(encoding="utf-8")

    def editor_check(body: SourceBody) -> dict[str, Any]:
        return editor.check_source(
            body.source,
            body.language,
            default_parent=body.default_parent,
            doc_source=body.doc_source,
        )

    def editor_faces(path: str = "") -> dict[str, Any]:
        return found(editor.faces, inspector.image, path)

    def editor_netz(path: str = "") -> dict[str, Any]:
        return found(editor.netz, inspector.image, path)

    def editor_compile(body: SourceBody) -> dict[str, Any]:
        front = _front(session)
        if front is None or session is None:
            return _no_session()
        with session.transaction():  # one channel transaction at a time (session.py)
            return live_seam(lambda: editor.compile_source(front, body.source, body.name))

    def editor_install(body: SourceBody) -> dict[str, Any]:
        front = _front(session)
        if front is None or session is None:
            return _no_session()
        with session.transaction():  # one channel transaction at a time (session.py)
            return live_seam(lambda: editor.install_source(front, body.source, body.name))

    def editor_poke(body: PokeBody) -> dict[str, Any]:
        if session is None or session.world is None:
            return _no_session()
        world = session.world

        def _poke() -> dict[str, Any]:
            # A delta is compiled against its base and the image refuses a base that is not it
            # (`live/front.py`: "the base of a delta that is not the image is refused"). What runs
            # has moved on from the inspected snapshot (the boot of the front end, the image's own
            # writes), so the poke is compiled against the running image, read through the seam.
            # `world.front` is the front end of that same image: `editor.poke` rebases its mirror
            # around the apply (the poke is part of the image state), or the next compile or
            # install on this session would be refused a stale base.
            running = Image.from_bytes(world.client.emit()[0])
            return editor.poke(
                world.client,
                running,
                body.path,
                body.field,
                body.value,
                body.offset,
                front=world.front,
            )

        with session.transaction():  # emit → compile → apply is one transaction (session.py)
            return live_seam(_poke)

    def editor_numbers() -> dict[str, Any]:
        """The numbers the source may use (the const table of the image's front end)."""
        front = _front(session)
        if front is None or session is None:
            return _no_session()
        with session.transaction():  # one channel transaction at a time (session.py)
            return {"numbers": front.numbers()}

    def session_spec() -> dict[str, Any]:
        """The live session spec behind the shell's live slot (docs/21): the link of the session
        and what the image that runs at it says about itself."""
        return _session_spec(session)

    app.get("/", response_class=HTMLResponse)(page)
    app.get("/model", response_class=HTMLResponse)(model_page)
    app.get("/tutor", response_class=HTMLResponse)(tutor_page_html)
    app.mount("/tutor/static", StaticFiles(directory=tutor_page.STATIC))
    app.mount("/static", StaticFiles(directory=Path(__file__).with_name("static")))
    app.get("/editor", response_class=HTMLResponse)(editor_page)
    app.get("/api/info")(info)
    app.get("/api/children")(children)
    app.get("/api/object")(obj)
    app.get("/api/detail")(detail)
    app.get("/api/memory")(memory)
    app.get("/api/search")(search)
    app.get("/api/delta")(delta)
    app.get("/api/editor/faces")(editor_faces)
    app.get("/api/editor/netz")(editor_netz)
    app.get("/api/editor/numbers")(editor_numbers)
    app.get("/api/live")(session_spec)
    app.post("/api/editor/check")(editor_check)
    app.post("/api/editor/compile")(editor_compile)
    app.post("/api/editor/install")(editor_install)
    app.post("/api/editor/poke")(editor_poke)
    # The 3D world of the SAME image the app inspects (world/__init__.py): its exported scene
    # at /world/scene.json, never the demo scene — one image, one truth.
    attach_world(app, inspector)
    return app


def _front(session: Session | None):
    """The in-image front end of the running image (the one seam), or None when no image runs."""
    if session is None or session.world is None:
        return None
    return session.world.front


def _no_session() -> dict[str, Any]:
    """The honest refusal of an editor action that needs a running image and has none."""
    return {
        "ok": False,
        "refusal": "no running image: the editor can check symbols and render faces, but "
        "compile, install and poke need a live session (rxf serve --link=...)",
    }


# ---- the live session spec (docs/21, the live slot) -------------------------------------------

LIVE_LEAF = "pymergetic.rxf.foundation.live.pump"  # the image's own live leaf (docs/18 `live.pump`)

# The state word of ground.h, six bits: provenance 1, disposition 2, cleanliness 1, mobility 2 —
# spelled as `Inspector.detail` spells it; tests/test_server_views.py pins the two together.
_PROVENANCE = ("Runtime", "Image")
_DISPOSITION = ("Retain", "Transient", "Tombstone")
_CLEANLINESS = ("Clean", "Dirty")


def _state_word(names: tuple[str, ...], value: int) -> str:
    """The name of a state word of ground.h; an unknown value stays a number."""
    return names[value] if value < len(names) else str(value)


def _cell_state(flags: int) -> dict[str, Any]:
    """The state word of one cell, as the channel reports it in the flags of `Client.get`."""
    state = (flags >> 8) & 0x3F
    try:
        mobility = Mobility((state >> 4) & 0x3).name
    except ValueError:
        mobility = str((state >> 4) & 0x3)
    return {
        "value": state,
        "provenance": _state_word(_PROVENANCE, state & 0x1),
        "disposition": _state_word(_DISPOSITION, (state >> 1) & 0x3),
        "cleanliness": _state_word(_CLEANLINESS, (state >> 3) & 0x1),
        "mobility": mobility,
    }


def _session_spec(session: Session | None) -> dict[str, Any]:
    """The live session spec of the shell's live slot (docs/21): the link of the session, the
    identity of the image that runs at it, and what its own channel answers about it.

    Every field is an answer of the running image — the same facts `rxf live info` reports for a
    link (`live/client.py` `Hello`), plus the live leaf of the image: its handle and its state
    word, as any object's (`LIVE_LEAF`, the leaf whose Code serves these very rings). Nothing is
    computed or guessed here. With no session the answer is exactly `no image` (docs/21); a
    session whose channel does not answer says so with the channel's own refusal instead of
    pretending that nothing is attached.
    """
    if session is None or session.world is None:
        return {
            "attached": False,
            "spec": None,
            "image": None,
            "interpreter": None,
            "leaf": None,
            "counters": None,
            "refusal": None,
        }
    client = session.world.client
    try:
        with session.transaction():  # the feed is one snapshot: one transaction (session.py)
            hello = client.hello()
            digest = client.digest()
            handle = client.resolve(LIVE_LEAF)
            leaf = None if handle is None else client.get(handle)
    except Exception as problem:  # noqa: BLE001 - a channel that stopped answering is reported
        # as the refusal of the spec, never raised over the wire: a session that is attached but
        # silent must not fall back to `no image` (docs/21)
        return {
            "attached": True,
            "spec": session.spec,
            "image": None,
            "interpreter": None,
            "leaf": None,
            "counters": None,
            "refusal": str(problem),
        }
    return {
        "attached": True,
        "spec": session.spec,
        "image": {
            "digest": digest,
            "handles": hello.handles,
            "body_size": hello.body_size,
        },
        "interpreter": {
            # `Hello.version` (live/client.py): the version the running image answers — the
            # live layer it serves its rings with (`PYMERGETIC_RXF_LIVE_VERSION` of
            # core/live/live.h). It is the only version number the channel produces, and
            # `rxf live info` reports the very same one.
            "version": hello.version,
        },
        "leaf": None
        if leaf is None
        else {
            "path": LIVE_LEAF,
            "handle": handle,
            "state": _cell_state(leaf.flags),
        },
        "counters": {
            "commits": hello.commits,
            "rejects": hello.rejects,
            "committed": hello.committed,
            "arena_capacity": hello.arena_capacity,
            "stage_capacity": hello.stage_capacity,
            "emit_capacity": hello.emit_capacity,
            "work_capacity": hello.work_capacity,
        },
        "refusal": None,
    }
