"""Commands of the inspector: `rxf server <command> SOURCE`."""

from __future__ import annotations

from pymergetic.rxf import source


def browse(src: str, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Serves the read-only inspector on HOST:PORT (needs the `server` extra)."""
    import uvicorn

    from pymergetic.rxf.server.app import create_app

    app = create_app(source.load(src))
    print(f"inspector on http://{host}:{int(port)}  (Ctrl+C stops it)", flush=True)
    uvicorn.run(app, host=host, port=int(port), log_level="warning")
