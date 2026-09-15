"""Thread-safe, lazily parsed catalog of mounted RXF binaries."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from threading import RLock

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.output.binary import BinaryLayout
from pymergetic.rxf.output.engine import (
    NODE_ENTRY_SIZE,
    unpack_layout,
)
from pymergetic.rxf.output.header import (
    FORMAT_VERSION,
    HEADER_SIZE,
    MAGIC,
    BinaryHeader,
)
from pymergetic.rxf.output.type_table import TYPE_TABLE_ENTRY_SIZE
from pymergetic.rxf.server.config import ServerConfig


@dataclass(frozen=True)
class CatalogEntry:
    id: str
    mount: str
    relative_path: str
    path: Path
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class RXFSnapshot:
    entry: CatalogEntry
    blob: bytes
    layout: BinaryLayout
    container: Container


class RXFLibrary:
    """Discover mounted RXFs and cache immutable parsed snapshots."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self._mounts = {mount.name: mount for mount in config.mounts}
        self._catalog: dict[str, CatalogEntry] = {}
        self._cache: dict[str, RXFSnapshot] = {}
        self._lock = RLock()
        self.scan()

    def scan(self) -> tuple[CatalogEntry, ...]:
        catalog: dict[str, CatalogEntry] = {}
        for mount in self.config.mounts:
            for candidate in mount.root.rglob("*.rxf"):
                try:
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(mount.root)
                    stat = resolved.stat()
                except (OSError, ValueError):
                    continue
                if (
                    not resolved.is_file()
                    or stat.st_size > self.config.max_file_size
                    or not self._has_supported_header(resolved, stat.st_size)
                ):
                    continue
                relative = resolved.relative_to(mount.root).as_posix()
                entry_id = f"{mount.name}:{relative}"
                catalog[entry_id] = CatalogEntry(
                    id=entry_id,
                    mount=mount.name,
                    relative_path=relative,
                    path=resolved,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
        with self._lock:
            self._catalog = catalog
            self._cache = {
                entry_id: snapshot
                for entry_id, snapshot in self._cache.items()
                if entry_id in catalog and snapshot.entry == catalog[entry_id]
            }
        return self.list_entries()

    @staticmethod
    def _has_supported_header(path: Path, file_size: int) -> bool:
        try:
            with path.open("rb") as stream:
                raw = stream.read(HEADER_SIZE)
            if len(raw) != HEADER_SIZE:
                return False
            header = BinaryHeader.from_wire(raw)
        except (OSError, ValueError):
            return False
        if header.magic != MAGIC or header.version not in (4, FORMAT_VERSION):
            return False
        tables = (
            (header.node_table_off, header.node_count * NODE_ENTRY_SIZE),
            (header.code_table_off, header.code_table_count * 32),
            (header.heap_off, header.frontier),
            (header.ref_table_off, header.ref_table_count * 16),
            (header.section_table_off, header.section_table_count * 40),
            (header.type_table_off, header.type_table_count * TYPE_TABLE_ENTRY_SIZE),
            (header.attr_table_off, header.attr_table_size),
            (header.string_table_off, header.string_table_size),
        )
        return all(
            offset <= file_size and size <= file_size - offset
            for offset, size in tables
        )

    def list_entries(self) -> tuple[CatalogEntry, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._catalog.values(),
                    key=lambda item: (item.mount, item.relative_path),
                )
            )

    def get_entry(self, entry_id: str) -> CatalogEntry:
        with self._lock:
            entry = self._catalog.get(entry_id)
        if entry is None:
            raise KeyError(entry_id)
        return entry

    def load(self, entry_id: str) -> RXFSnapshot:
        entry = self.get_entry(entry_id)
        current = self._resolve_entry(entry.mount, entry.relative_path)
        if current != entry:
            self.scan()
            entry = self.get_entry(entry_id)
        with self._lock:
            cached = self._cache.get(entry_id)
            if cached is not None and cached.entry == entry:
                return cached
        blob = entry.path.read_bytes()
        layout = unpack_layout(blob)
        snapshot = RXFSnapshot(
            entry=entry,
            blob=blob,
            layout=layout,
            container=layout_to_container(layout),
        )
        with self._lock:
            self._cache[entry_id] = snapshot
        return snapshot

    def _resolve_entry(self, mount_name: str, relative_path: str) -> CatalogEntry:
        mount = self._mounts.get(mount_name)
        if mount is None:
            raise KeyError(mount_name)
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() != ".rxf"
        ):
            raise ValueError("invalid mounted RXF path")
        path = (mount.root / Path(*relative.parts)).resolve(strict=True)
        try:
            path.relative_to(mount.root)
        except ValueError as error:
            raise ValueError("RXF path escapes its mount") from error
        stat = path.stat()
        if not path.is_file() or stat.st_size > self.config.max_file_size:
            raise ValueError("RXF is not a loadable mounted file")
        return CatalogEntry(
            id=f"{mount_name}:{relative.as_posix()}",
            mount=mount_name,
            relative_path=relative.as_posix(),
            path=path,
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
        )
