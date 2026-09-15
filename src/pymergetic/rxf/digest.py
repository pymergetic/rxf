"""Integrity digests for RXF binaries."""

import hashlib
from pathlib import Path


def digest_file(path: str) -> dict:
    blob = Path(path).read_bytes()
    digest = hashlib.sha256(blob).digest()
    return {"size": len(blob), "digest": digest.hex(), "digest_bytes": digest}
