"""Commands on one image: `rxf core object <command> SOURCE`.

SOURCE is a module tree (directory), a delta (`.xml`), or RXFB 6 bytes.
"""

from __future__ import annotations

from typing import Any

from pymergetic.rxf import source
from pymergetic.rxf.cli import Problems


def info(src: str) -> dict[str, Any]:
    """Objects, entry, digests and problems of the image."""
    return source.report(source.load(src))


def check(src: str) -> dict[str, Any]:
    """The link check: only the verdict and the issues. Exit status 1 when the image has problems."""
    report = source.report(source.load(src))
    verdict = {"ok": report["ok"], "issues": report["issues"]}
    if not report["ok"]:
        raise Problems(verdict)
    return verdict


def digest(src: str) -> dict[str, str]:
    """The image digest (compacted bytes) and the delta digest (canonical text)."""
    image = source.load(src)
    return {"digest": image.digest(), "delta_digest": image.delta_digest()}


def save(src: str, target: str, overwrite: bool = False) -> dict[str, str]:
    """Writes the image to TARGET: `.xml` one delta, `.rxfb` bytes, else a module tree."""
    image = source.load(src)
    return {"target": target, "format": source.save(image, target, overwrite)}
