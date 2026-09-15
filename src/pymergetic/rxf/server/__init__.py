"""Mounted FastAPI inspector for RXF binaries.

Start it with ``rxf serve --config rxf-server.toml``. Programmatic use::

    from pathlib import Path
    from pymergetic.rxf.server.api import create_app
    from pymergetic.rxf.server.config import ServerConfig
    from pymergetic.rxf.server.state import RXFLibrary

    config = ServerConfig.from_toml(Path("rxf-server.toml"))
    app = create_app(RXFLibrary(config))
"""
