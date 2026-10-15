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

from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.server.live.session import Session

PAGE = Path(__file__).with_name("templates") / "inspector.html"
MODEL_PAGE = Path(__file__).with_name("templates") / "model.html"


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

    app.get("/", response_class=HTMLResponse)(page)
    app.get("/model", response_class=HTMLResponse)(model_page)
    app.get("/api/info")(info)
    app.get("/api/children")(children)
    app.get("/api/object")(obj)
    app.get("/api/detail")(detail)
    app.get("/api/memory")(memory)
    app.get("/api/search")(search)
    app.get("/api/delta")(delta)
    return app
