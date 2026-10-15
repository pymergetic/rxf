"""The 3D world renderer of the RXF runtime: the arena as a navigable world.

The world IS the arena — a projection, never a second truth. ``export`` turns one image
(read-only) into the scene input schema ("rxf-world-1", documented in its docstring); the
Babylon.js scene under ``static/scene`` renders that JSON and invents nothing. Vendored
Babylon.js lives under ``static/vendor`` — no CDN, no network at runtime.

Wiring into the server (the app in ``server/app.py``)::

    from pymergetic.rxf.world import attach_world  # with the other imports
    ...
    attach_world(app, inspector)                   # in create_app, after the other routes

That registers: ``GET /world`` (the page), ``GET /world/scene.json`` (the exported scene of the
image the app inspects), and ``/world/static`` (the scene, the HUD chrome, and the vendored
Babylon.js bundle). Nothing outside this package changes.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymergetic.rxf.world.export import (
    SCHEMA,
    SchemaError,
    export_world,
    scene_json,
    validate_scene,
)

if TYPE_CHECKING:
    from pymergetic.rxf.server.inspector import Inspector

WORLD_DIR = Path(__file__).parent
WORLD_PAGE = WORLD_DIR / "templates" / "world.html"
STATIC_DIR = WORLD_DIR / "static"
VENDOR_BABYLON = STATIC_DIR / "vendor" / "babylon" / "babylon.js"

__all__ = [
    "SCHEMA",
    "STATIC_DIR",
    "VENDOR_BABYLON",
    "WORLD_DIR",
    "WORLD_PAGE",
    "SchemaError",
    "attach_world",
    "export_world",
    "scene_json",
    "validate_scene",
]


def attach_world(app: Any, inspector: Inspector) -> None:
    """Wires the world page into a FastAPI app: the page, the scene JSON, and the static assets.

    The page is served at ``/world/`` (``/world`` redirects there) so its relative asset URLs
    resolve; the scene lives at ``/world/scene.json`` and the assets under ``/world/static``.
    """
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles

    def page() -> HTMLResponse:
        return HTMLResponse(WORLD_PAGE.read_text(encoding="utf-8"))

    def scene() -> JSONResponse:
        return JSONResponse(export_world(inspector))

    app.get("/world", include_in_schema=False)(
        lambda: RedirectResponse("/world/", status_code=307)
    )
    app.get("/world/", response_class=HTMLResponse, include_in_schema=False)(page)
    app.get("/world/scene.json", include_in_schema=False)(scene)
    app.mount("/world/static", StaticFiles(directory=STATIC_DIR))
