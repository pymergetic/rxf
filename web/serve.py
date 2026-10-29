"""Serve the RXF preview page: the wasm32 seat, in a tab.

`python web/serve.py [port]` serves this directory plus the seat's artifacts
(`boot/build/rxf_boot.wasm`, the packed image, and the leaves module of the
function table) on one port -- 1337, because 80 and 443 are taken on the host
this runs on.

The page is not a mock: it instantiates the image's own wasm32 seat and runs
it. What the browser shows is what the machine boots.
"""

from __future__ import annotations

import http.server
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
BUILD = ROOT / "boot" / "build"

SEAT = {
    "rxf_boot.wasm": BUILD / "rxf_boot.wasm",
    "image_wasm32.bin": BUILD / "image_wasm32.bin",
    "rxf_leaves.wasm": BUILD / "rxf_leaves.wasm",
}


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
    with http.server.ThreadingHTTPServer(("0.0.0.0", port), Handler) as server:
        print(f"rxf: preview on http://0.0.0.0:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
