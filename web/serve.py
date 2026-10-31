"""Serve the RXF preview page: the wasm32 seat, in a tab.

`python web/serve.py [port]` serves this directory plus the seat's artifacts
(`boot/build/rxf_boot.wasm`, the packed image, and the leaves module of the
function table) on one port -- 1337, because 80 and 443 are taken on the host
this runs on.

The page is not a mock: it instantiates the image's own wasm32 seat and runs
it. What the browser shows is what the machine boots.
"""

from __future__ import annotations

import hashlib
import http.server
import importlib
import json
import sys
import threading
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BUILD = ROOT / "boot" / "build"

SEAT = {
    "rxf_boot.wasm": BUILD / "rxf_boot.wasm",
    "image_wasm32.bin": BUILD / "image_wasm32.bin",
    "rxf_leaves.wasm": BUILD / "rxf_leaves.wasm",
}


def host_compiler() -> ModuleType:
    """web/acts.py - the host's compiler door - imported whether this file runs
    as a script (``python web/serve.py``) or as ``web.serve``."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module("web.acts")


class Handler(http.server.SimpleHTTPRequestHandler):
    """The web directory, with the seat's artifacts served beside it."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB), **kwargs)

    def do_GET(self) -> None:
        name = self.path.lstrip("/").split("?")[0]
        if name in SEAT:
            artifact = SEAT[name]
            if not artifact.exists():
                self.send_error(404, f"{artifact} is not built")
                return
            body = artifact.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def do_POST(self) -> None:
        """The host's compiler: `POST /compile` answers the delta of one act.

        The page sends {base, history, act} (web/acts.py); the answer is the
        delta's bytes with its SHA-256 in `X-Rxf-Sha256` - the word the seat's
        STAGE_COMMIT checks the staged bytes against. A refused act is a 400
        whose body says why.
        """
        if self.path.split("?")[0] != "/compile":
            self.send_error(404, "the host compiles at /compile")
            return
        acts = host_compiler()  # the model's weight is paid by the first act
        try:
            size = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict):
                raise TypeError("a request is a JSON object")
            body = acts.compile_delta(request)
        except (ValueError, TypeError) as error:  # an ActError is a ValueError
            reason = str(error).encode()
            self.send_response(400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(reason)))
            self.end_headers()
            self.wfile.write(reason)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Rxf-Sha256", hashlib.sha256(body).hexdigest())
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        print(f"rxf: {self.address_string()} {format % args}")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1337
    for name, artifact in SEAT.items():
        where = "is built" if artifact.exists() else f"is MISSING ({artifact})"
        print(f"rxf: {name} {where}")
    if not SEAT["rxf_boot.wasm"].exists():
        print("rxf: build the seat first (make -C boot)", file=sys.stderr)
        raise SystemExit(1)
    # the compiler's model takes a while to build: do it before the first act asks
    threading.Thread(target=host_compiler().base_bytes, daemon=True).start()
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler) as server:
        print(f"rxf: preview on http://0.0.0.0:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
