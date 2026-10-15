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

from pymergetic.rxf.core.object import Image
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
        if front is None:
            return _no_session()
        return editor.compile_source(front, body.source, body.name)

    def editor_install(body: SourceBody) -> dict[str, Any]:
        front = _front(session)
        if front is None:
            return _no_session()
        return editor.install_source(front, body.source, body.name)

    def editor_poke(body: PokeBody) -> dict[str, Any]:
        if session is None or session.world is None:
            return _no_session()
        return editor.poke(
            session.world.client,
            inspector.image,
            body.path,
            body.field,
            body.value,
            body.offset,
        )

    def editor_numbers() -> dict[str, Any]:
        """The numbers the source may use (the const table of the image's front end)."""
        front = _front(session)
        if front is None:
            return _no_session()
        return {"numbers": front.numbers()}

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
