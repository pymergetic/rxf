"""The 3D world renderer of the RXF runtime: the arena as a navigable world.

The world IS the arena — a projection, never a second truth. ``export`` turns one image
(read-only) into the scene input schema (documented in its docstring); the
Babylon.js scene under ``static/scene`` renders that JSON and invents nothing. Vendored
Babylon.js lives under ``static/vendor`` — no CDN, no network at runtime.

Wiring into the server (the app in ``server/app.py``)::

    from pymergetic.rxftk.world import attach_world  # with the other imports
    ...
    attach_world(app, inspector)                   # in create_app, after the other routes

That registers: ``GET /world`` (the page), ``GET /world/scene.bin`` (the image itself: the raw
``.rxfb`` bytes, which the page reads in the browser with ``static/scene/rxf-reader.js`` — no
export on the host's blocking path at all), ``GET /world/scene.json`` (the scene session of the
image the app inspects — see ``_Session`` for the query protocol: ``?parts=1`` the plan,
``?part=…&offset=…&limit=…`` one slice, ``?region=`` one district), ``GET
/world/scene.json/progress`` (where the layer the page is waiting on is), and ``/world/static``
(the scene, the HUD chrome, and the vendored Babylon.js bundle). Nothing outside this package
changes.

The page loads the world as the interactive session it is: the raw image first (one request,
byte progress on the loading face), the world laid out in the browser from those bytes, and —
when the host serves the JSON session — layer by layer (buildings, cables, ducts, streets),
one slice per request, the layer it wants next computed on demand and kept until the image
changes. The plain ``scene.json`` (no query) is the one-shot face of the same session — every
layer, assembled — and stays the dump/test seam. The document the browser's reader computes is
the exporter's client face (``export_world(..., packing=False)``): the same document without
the slow duct packing — ``test_reader_js`` pins the two languages to the same output.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pymergetic.rxftk.world.export import (
    SCHEMA,
    Layers,
    SchemaError,
    export_world,
    scene_json,
    validate_scene,
)

if TYPE_CHECKING:
    from pymergetic.rxftk.server.inspector import Inspector

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
    "Layers",
    "SchemaError",
    "attach_world",
    "export_world",
    "scene_json",
    "validate_scene",
]


# The session's parts, in the order the page loads them (dependency-safe: the buildings stand
# before the streets know them, the cables route on the streets, the ducts pack the cables and
# then say which legs each cable draws itself — the ``stubs``).
PLAN = ("cells", "voids", "blocks", "streets", "frame", "edges", "bundles", "stubs", "counts")
SLICE = 2000  # the default slice: one page of buildings or cables per request


class _Session:
    """The interactive session between the page and the host: one image, its scene layers
    computed on demand and memoized (``export.Layers``), served one slice at a time.

    The page asks for the part it wants next — ``?parts=1`` lists the plan, ``?part=cells``
    is one page of buildings (``offset``/``limit``, or one district with ``region=``), one
    page of cables, one district at a time — and the host computes just that layer, once.
    While a layer computes, ``progress`` says where it is; a reload or a second view pays
    nothing, the layers stay until the image changes (its body digest is the key). The plain
    ``scene.json`` (no query) is the one-shot face of the SAME session: every layer,
    assembled — what tests and tools dump."""

    def __init__(self, inspector: Inspector) -> None:
        self.inspector = inspector
        self._mutex = threading.RLock()
        self._layers: Layers | None = None
        self._digest = ""
        self._payload: bytes | None = None
        self.stage = "idle"
        self.done = 0
        self.total = 0
        self.started = 0.0
        self.working = False

    def manifest(self) -> dict[str, Any]:
        with self._mutex:
            layers = self._layers_of()
            return {**layers.manifest(), "plan": list(PLAN), "slice": SLICE}

    def part(self, name: str, offset: int, limit: int, region: str | None) -> dict[str, Any]:
        if name not in PLAN:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"no such part: {name}")
        with self._mutex:
            layers = self._layers_of()
            self._begin(name)
            try:
                if name == "frame":
                    return {"part": name, "value": layers.frame()}
                if name == "counts":
                    return {"part": name, "value": layers.counts()}
                if name == "stubs":
                    # the packing's verdict per cable: which legs the cable draws itself once
                    # the ducts carry the rest (export: edge.stubs)
                    layers.bundles()
                    return {
                        "part": name,
                        "value": {
                            edge["id"]: edge["stubs"]
                            for edge in layers.routed()
                            if edge["stubs"] is not None
                        },
                    }
                items = {
                    "cells": layers.cells,
                    "voids": layers.voids,
                    "blocks": layers.blocks,
                    "streets": layers.streets,
                    "edges": layers.routed,
                    "bundles": layers.bundles,
                }[name]()
                if region and name == "cells":
                    items = [cell for cell in items if cell["region"] == region]
                page = items[offset : offset + max(1, limit)]
                return {
                    "part": name,
                    "offset": offset,
                    "count": len(page),
                    "total": len(items),
                    "items": page,
                }
            finally:
                self.working = False

    def payload(self) -> bytes:
        """The whole document, assembled and cached: the one-shot face of the session."""
        with self._mutex:
            self._layers_of()
            if self._payload is not None:
                return self._payload
            self._begin("document")
            try:
                payload = json.dumps(
                    self._layers.document(),
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode()
            finally:
                self.working = False
            self._payload = payload
            return payload

    def progress(self) -> dict[str, Any]:
        return {
            "state": "working" if self.working else "idle",
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "elapsed": round(time.monotonic() - self.started, 1) if self.working else 0.0,
        }

    def _layers_of(self) -> Layers:
        digest = hashlib.sha256(self.inspector.image.to_bytes()).hexdigest()
        if self._layers is None or digest != self._digest:
            self._layers = Layers(self.inspector, self._report)
            self._digest = digest
            self._payload = None
        return self._layers

    def _begin(self, stage: str) -> None:
        self.working = True
        self.stage, self.done, self.total = stage, 0, 0
        self.started = time.monotonic()

    def _report(self, stage: str, done: int, total: int) -> None:
        self.stage, self.done, self.total = stage, done, total


def attach_world(app: Any, inspector: Inspector) -> None:
    """Wires the world page into a FastAPI app: the page, the raw image bytes, the scene
    session, and the static assets.

    The page is served at ``/world/`` (``/world`` redirects there) so its relative asset URLs
    resolve; the page's fast lane is ``/world/scene.bin`` (the raw ``.rxfb`` the browser reads
    with ``static/scene/rxf-reader.js``), the scene session lives at ``/world/scene.json``
    (see ``_Session`` for the query protocol) and the assets under ``/world/static``.
    """
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    from fastapi.staticfiles import StaticFiles

    session = _Session(inspector)

    def page() -> HTMLResponse:
        return HTMLResponse(WORLD_PAGE.read_text(encoding="utf-8"))

    def scene_bin() -> Response:
        """The image itself: the raw ``.rxfb`` bytes of the image the app inspects.

        This is the page's fast lane — the browser lays the world out from these bytes with
        ``static/scene/rxf-reader.js`` and the host computes nothing. The bytes are the arena
        verbatim (``Image.to_bytes()``): the one truth, not a projection."""
        return Response(inspector.image.to_bytes(), media_type="application/octet-stream")

    def scene(
        parts: int = 0,
        part: str | None = None,
        offset: int = 0,
        limit: int = SLICE,
        region: str | None = None,
    ) -> Response:
        if parts:
            return JSONResponse(session.manifest())
        if part is not None:
            return JSONResponse(session.part(part, offset, limit, region))
        return Response(session.payload(), media_type="application/json")

    def scene_progress() -> JSONResponse:
        return JSONResponse(session.progress())

    app.get("/world", include_in_schema=False)(
        lambda: RedirectResponse("/world/", status_code=307)
    )
    app.get("/world/", response_class=HTMLResponse, include_in_schema=False)(page)
    app.get("/world/scene.bin", include_in_schema=False)(scene_bin)
    app.get("/world/scene.json", include_in_schema=False)(scene)
    app.get("/world/scene.json/progress", include_in_schema=False)(scene_progress)
    app.mount("/world/static", StaticFiles(directory=STATIC_DIR))
