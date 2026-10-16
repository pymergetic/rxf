"""Commands of the inspector: `rxf server <command> SOURCE`."""

from __future__ import annotations

import sys
from pathlib import Path

import pymergetic.rxftk
from pymergetic.rxftk import source


def _checkout_on_path() -> None:
    """The checkout root on the search path: `tools.highlight` (the editor's one meaning table,
    `server/editor.py`) is a script of the checkout, not an installed package — it imports the
    way pytest's `pythonpath` reaches it (`packages/rxf` as `.`). A wheel install ships no such
    root and is refused plainly here, not half-imported."""
    for start in (Path(__file__).resolve(), Path(pymergetic.rxftk.__file__).resolve()):
        for parent in start.parents:
            if (parent / "tools" / "highlight").is_dir():
                if str(parent) not in sys.path:
                    sys.path.insert(0, str(parent))
                return
    raise RuntimeError(
        "rxf server needs the checkout to serve the editor: tools/highlight (its meaning table) "
        "is not in this install"
    )


def browse(src: str, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Serves the read-only inspector on HOST:PORT (needs the `server` extra)."""
    import uvicorn

    _checkout_on_path()
    from pymergetic.rxftk.server.app import create_app

    app = create_app(source.load(src))
    print(f"inspector on http://{host}:{int(port)}  (Ctrl+C stops it)", flush=True)
    uvicorn.run(app, host=host, port=int(port), log_level="warning")


def serve(
    src: str,
    link: str = "shm:rxf",
    host: str = "127.0.0.1",
    port: int = 8420,
) -> None:
    """Serves the inspector on a REAL running image of SRC, registered at LINK (the `server`
    extra is needed).

    One link, one runner: the image of SRC is linked and run in this process (its own
    interpreter and its own live leaf — docs/19, "a run is not a frame"), its front end is
    seeded once (the fixpoint of docs/19) and compiles everything after that, and while the
    app serves, the world of that image is registered at LINK: `rxf console --link=LINK`
    attaches to it and the editor compiles, installs and pokes against it FOR REAL. Ctrl+C
    stops serving and takes the registration (and the machine) with it."""
    import uvicorn

    _checkout_on_path()
    from pymergetic.rxftk.server.app import create_app
    from pymergetic.rxftk.server.live.runner import Runner

    image = source.load(src)
    with Runner(image, link) as runner:
        app = create_app(image, session=runner.session)
        print(f"inspector on http://{host}:{int(port)}  (Ctrl+C stops it)", flush=True)
        print(
            f"image running at {runner.session.spec}: "
            f"`rxf console --link={runner.session.spec}` attaches to it",
            flush=True,
        )
        uvicorn.run(app, host=host, port=int(port), log_level="warning")
