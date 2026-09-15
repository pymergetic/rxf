"""Mount-only configuration for the RXF inspector."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

_MOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class Mount:
    """One named filesystem root exposed to the inspector."""

    name: str
    root: Path


@dataclass(frozen=True)
class ServerConfig:
    """Validated inspector configuration containing named mounts only."""

    mounts: tuple[Mount, ...]
    max_file_size: int = 512 * 1024 * 1024

    @classmethod
    def from_toml(cls, path: Path) -> ServerConfig:
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"cannot read server config {path}: {error}") from error
        mounts_raw = raw.get("mounts")
        if not isinstance(mounts_raw, dict) or not mounts_raw:
            raise ValueError("server config requires at least one [mounts.NAME] table")
        mounts: list[Mount] = []
        for name, value in mounts_raw.items():
            if not isinstance(name, str) or not _MOUNT_NAME.fullmatch(name):
                raise ValueError(f"invalid mount name: {name!r}")
            if not isinstance(value, dict) or not isinstance(value.get("path"), str):
                raise TypeError(f"mount {name!r} requires a string path")
            root = Path(value["path"]).expanduser()
            if not root.is_absolute():
                root = path.parent / root
            root = root.resolve()
            if not root.is_dir():
                raise ValueError(f"mount {name!r} is not a directory: {root}")
            mounts.append(Mount(name=name, root=root))
        limit = raw.get("max_file_size", cls.max_file_size)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise ValueError("max_file_size must be a positive integer")
        return cls(mounts=tuple(mounts), max_file_size=limit)
