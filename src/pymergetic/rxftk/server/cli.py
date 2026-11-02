"""Commands of the inspector: `rxf server <command> SOURCE`."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pymergetic.rxftk
from pymergetic.rxftk import source
from pymergetic.rxftk.live.link import segment_of


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


def _listening(host: str, port: int) -> socket.socket:
    """The inspector's socket, taken BEFORE anything is registered or promised at the link.

    A serve that cannot come up — the port is busy — fails here, cleanly: no world is
    registered, no segment is laid out, and no banner ever claims an image is running.
    """
    listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listening.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listening.bind((host, int(port)))  # raises here, before anything else exists
    listening.listen()
    return listening


def _promise(link: str) -> str:
    """What the banner may say about the link — once it is true (docs/19, "who runs when")."""
    if segment_of(link) is not None:
        return (
            f"image running at {link}: `rxf console --link={link}` attaches to it from any "
            "process (the image is laid out in shared memory)"
        )
    return (
        f"image running at {link}: `rxf console --link={link}` attaches IN THIS PROCESS only "
        f"({link.partition(':')[0]} carries frames; the machine that runs the image is here)"
    )


def browse(src: str, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Serves the read-only inspector on HOST:PORT (needs the `server` extra)."""
    import uvicorn

    _checkout_on_path()
    from pymergetic.rxftk.server.app import create_app

    listening = _listening(host, int(port))
    try:
        app = create_app(source.load(src))
        print(f"inspector on http://{host}:{int(port)}  (Ctrl+C stops it)", flush=True)
        uvicorn.Server(
            uvicorn.Config(app, host=host, port=int(port), log_level="warning")
        ).run(sockets=[listening])
    finally:
        listening.close()


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
    stops serving and takes the registration (and the machine) with it.

    A `shm:NAME` link lays the image out in a shared-memory segment of that name: `rxf console
    --link=shm:NAME` attaches from ANY process. The other links (`pty:`, `tty:`, `fd:`) carry
    frames only — the machine that runs the image is here, and the banner says so. The
    inspector's port is taken FIRST: a serve that cannot come up registers nothing and prints
    no promise."""
    import uvicorn

    _checkout_on_path()
    from pymergetic.rxftk.server.app import create_app
    from pymergetic.rxftk.server.live.runner import Runner

    listening = _listening(host, int(port))  # before the image and the link exist: no false promise
    image = source.load(src)
    try:
        with Runner(image, link) as runner:
            app = create_app(image, session=runner.session)
            print(f"inspector on http://{host}:{int(port)}  (Ctrl+C stops it)", flush=True)
            print(_promise(link), flush=True)
            uvicorn.Server(
                uvicorn.Config(app, host=host, port=int(port), log_level="warning")
            ).run(sockets=[listening])
    finally:
        listening.close()
