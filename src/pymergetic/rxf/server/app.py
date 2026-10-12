"""FastAPI wiring of the core inspector: one image, read only."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, Response

from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector

PAGE = Path(__file__).with_name("templates") / "inspector.html"


def found[Result](call: Callable[..., Result], *args: object) -> Result:
    """The call, with a missing object answered as 404."""
    try:
        return call(*args)
    except KeyError as error:
        raise HTTPException(404, f"no object at {error.args[0]!r}") from None


def create_app(image: Image) -> FastAPI:
    inspector = Inspector(image)
    app = FastAPI(title="RXF inspector")

    def page() -> str:
        return PAGE.read_text(encoding="utf-8")

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

    def search(
        q: str = Query(min_length=1), limit: int = Query(100, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        return inspector.search(q, limit)

    def delta() -> Response:
        return Response(inspector.delta(), media_type="application/xml")

    app.get("/", response_class=HTMLResponse)(page)
    app.get("/api/info")(info)
    app.get("/api/children")(children)
    app.get("/api/object")(obj)
    app.get("/api/search")(search)
    app.get("/api/delta")(delta)
    return app
