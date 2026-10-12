"""Where an image comes from and goes to: a module tree, a delta, or RXFB 6 bytes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.object import Image

DELTA_SUFFIX = ".xml"
IMAGE_SUFFIX = ".rxfb"


LIVE_PREFIX = "live:"


def load(source: str | Path) -> Image:
    """A directory is a module tree, `.xml` a delta on the empty image, `live:LINK` the dump of a
    running image (`pty:/dev/pts/3`, `shm:name`, see `live.link`), else image bytes."""
    if isinstance(source, str) and source.startswith(LIVE_PREFIX):
        from pymergetic.rxf.live import link

        raw, _ = link.connect(source[len(LIVE_PREFIX) :]).emit()
        return Image.from_bytes(raw)
    path = Path(source)
    if path.is_dir():
        return tree.load(path)
    data = path.read_bytes()
    if path.suffix == DELTA_SUFFIX:
        image = Image.empty()
        image.apply(data.decode("utf-8"))
        return image
    return Image.from_bytes(data)


def save(image: Image, target: str | Path, overwrite: bool = False) -> str:
    """Writes `.xml` as one delta, `.rxfb` as image bytes, anything else as a module tree."""
    path = Path(target)
    if path.suffix == DELTA_SUFFIX:
        path.write_text(image.export(), encoding="utf-8", newline="\n")
        return "delta"
    if path.suffix == IMAGE_SUFFIX:
        path.write_bytes(image.compact().to_bytes())
        return "image"
    tree.write(image, path, overwrite=overwrite)
    return "tree"


def report(image: Image) -> dict[str, Any]:
    """What the commands tell about an image."""
    issues = image.check()
    return {
        "ok": not issues,
        "objects": sum(1 for h in range(image.handle_count) if image.live(h)),
        "entry": image.entry_path or None,
        "digest": image.digest(),
        "delta_digest": image.delta_digest(),
        "issues": [{"path": path, "message": message} for path, message in issues],
    }
