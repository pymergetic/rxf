"""Thread-safe, lazily parsed catalog of mounted RXF binaries."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from threading import Condition, RLock, Thread
from time import monotonic, time

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.executable.artifact import extract_rxf
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.module import derived_fqns
from pymergetic.rxf.model.node import NodeDef
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
from pymergetic.rxf.output.node import NodeEntry
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


class LoadPhase(StrEnum):
    IDLE = "idle"
    READING = "reading"
    HEADER = "header"
    REFERENCES = "references"
    SECTIONS = "sections"
    NODES = "nodes"
    HEAP = "heap"
    TYPES = "types"
    CODE = "code"
    CONTAINER = "container"
    INDEXING = "indexing"
    NODE_INDEX = "node_index"
    OBJECT_INDEX = "object_index"
    TREE_INDEX = "tree_index"
    SEARCH_INDEX = "search_index"
    MEMORY_INDEX = "memory_index"
    DONE = "done"
    ERROR = "error"


@dataclass
class LoadProgress:
    entry: CatalogEntry
    phase: LoadPhase = LoadPhase.IDLE
    completed: int = 0
    total: int = 0
    unit: str = ""
    started_at: float | None = None
    updated_at: float | None = None
    started_monotonic: float | None = None
    done: bool = False
    error: str | None = None
    work_plan: tuple[tuple[LoadPhase, int], ...] = ()
    overall_completed: int = 0
    overall_total: int = 0

    def update(
        self, phase: str | LoadPhase, completed: int, total: int, unit: str
    ) -> None:
        now = time()
        if self.started_at is None:
            self.started_at = now
            self.started_monotonic = monotonic()
        self.phase = LoadPhase(phase)
        self.completed = completed
        self.total = total
        self.unit = unit
        self.updated_at = now
        if self.work_plan:
            offset = 0
            weight = 0
            for planned_phase, planned_weight in self.work_plan:
                if planned_phase == self.phase:
                    weight = planned_weight
                    break
                offset += planned_weight
            fraction = 1.0 if total == 0 else min(1.0, completed / total)
            self.overall_completed = max(
                self.overall_completed,
                min(self.overall_total, offset + int(weight * fraction)),
            )

    def set_work_plan(self, plan: tuple[tuple[LoadPhase, int], ...]) -> None:
        self.work_plan = plan
        self.overall_total = sum(weight for _, weight in plan)

    def inspect(self) -> dict[str, object]:
        elapsed = (
            0.0
            if self.started_monotonic is None
            else max(0.0, monotonic() - self.started_monotonic)
        )
        percent = (
            None
            if self.overall_total <= 0
            else min(100.0, self.overall_completed * 100.0 / self.overall_total)
        )
        throughput = (
            None
            if elapsed <= 0 or self.overall_completed <= 0
            else self.overall_completed / elapsed
        )
        eta = None
        if throughput and self.overall_total > self.overall_completed:
            eta = (self.overall_total - self.overall_completed) / throughput
        return {
            "state": "error"
            if self.error
            else "done"
            if self.done
            else "loading"
            if self.started_at
            else "idle",
            "phase": self.phase.value,
            "completed": self.overall_completed,
            "total": self.overall_total,
            "unit": "work bytes",
            "phase_completed": self.completed,
            "phase_total": self.total,
            "phase_unit": self.unit,
            "percent": percent,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "elapsed_seconds": elapsed,
            "throughput": throughput,
            "eta_seconds": eta,
            "done": self.done,
            "error": self.error,
        }


@dataclass(frozen=True)
class MemoryCellIndex:
    offset: int
    end: int
    node_id: str
    type_id: str
    owner_kind: str
    disposition: str


@dataclass
class RXFSnapshot:
    entry: CatalogEntry
    blob: bytes
    layout: BinaryLayout
    container: Container
    memory_cells: tuple[MemoryCellIndex, ...] = ()
    layout_nodes_by_id: dict[int, NodeEntry] = field(default_factory=dict)
    container_nodes_by_id: dict[int, NodeDef] = field(default_factory=dict)
    children_by_parent: dict[int, tuple[NodeEntry, ...]] = field(default_factory=dict)
    fqns: dict[int, str] = field(default_factory=dict)
    search_records: tuple[tuple[str, str, str], ...] = ()
    defer_indexes: bool = False
    memory_cache: OrderedDict[tuple[int, int, int], dict[str, object]] = field(
        default_factory=OrderedDict, repr=False
    )
    memory_lock: RLock = field(default_factory=RLock, repr=False)

    def __post_init__(self) -> None:
        if not self.defer_indexes:
            self.build_indexes()

    def build_indexes(
        self, progress: Callable[[str, int, int, str], None] | None = None
    ) -> None:
        layout_nodes = self.layout.nodes
        total_nodes = len(layout_nodes)
        self.layout_nodes_by_id = {node.id: node for node in layout_nodes}
        if progress is not None:
            progress("node_index", total_nodes, total_nodes, "nodes")
        self.container_nodes_by_id = {node.id: node for node in self.container.nodes}
        if progress is not None:
            progress("object_index", total_nodes, total_nodes, "nodes")
        children: dict[int, list[NodeEntry]] = {}
        for index, node in enumerate(layout_nodes, 1):
            children.setdefault(node.parent, []).append(node)
            if progress is not None and (index % 128 == 0 or index == total_nodes):
                progress("tree_index", index, total_nodes, "nodes")
        self.children_by_parent = {
            parent: tuple(sorted(values, key=lambda item: (item.name, item.id)))
            for parent, values in children.items()
        }
        self.fqns = derived_fqns(layout_nodes)
        self.search_records = tuple(
            (str(node.id), self.fqns[node.id].lower(), node.name.lower())
            for node in layout_nodes
        )
        if progress is not None:
            progress("search_index", total_nodes, total_nodes, "nodes")
        self.memory_cells = tuple(
            MemoryCellIndex(
                cell.offset,
                cell.end,
                str(cell.header.id),
                str(cell.header.type_id),
                self.layout_nodes_by_id[cell.header.id].owner_kind.name,
                self.layout_nodes_by_id[cell.header.id].state.disposition.name,
            )
            for cell in self.layout.heap.cells
        )
        if progress is not None:
            progress(
                "memory_index",
                len(self.memory_cells),
                len(self.layout.heap.cells),
                "cells",
            )


class RXFLibrary:
    """Discover mounted RXFs and cache immutable parsed snapshots."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self._mounts = {mount.name: mount for mount in config.mounts}
        self._catalog: dict[str, CatalogEntry] = {}
        self._cache: dict[str, RXFSnapshot] = {}
        self._loading: dict[str, LoadProgress] = {}
        self._progress: dict[str, LoadProgress] = {}
        self._lock = RLock()
        self._condition = Condition(self._lock)
        self.scan()

    def scan(self) -> tuple[CatalogEntry, ...]:
        catalog: dict[str, CatalogEntry] = {}
        for mount in self.config.mounts:
            for candidate in mount.root.rglob("*"):
                if candidate.suffix.lower() not in (".rxf", ".elf", ".efi", ".img", ""):
                    continue
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
            self._progress = {
                entry_id: progress
                for entry_id, progress in self._progress.items()
                if entry_id in catalog and progress.entry == catalog[entry_id]
            }
            self._loading = {
                entry_id: progress
                for entry_id, progress in self._loading.items()
                if entry_id in catalog and progress.entry == catalog[entry_id]
            }
            self._condition.notify_all()
        return self.list_entries()

    @staticmethod
    def _has_supported_header(path: Path, file_size: int) -> bool:
        try:
            with path.open("rb") as stream:
                # Reject unrelated extensionless files after four bytes, and
                # unrelated named files after at most one boot sector/header.
                prefix = stream.read(4)
                if not path.suffix and prefix != b"\x7fELF":
                    return False
                raw = prefix + stream.read(max(512, HEADER_SIZE) - len(prefix))
                if not raw.startswith(MAGIC):
                    if not (
                        raw.startswith((b"\x7fELF", b"MZ"))
                        or raw[510:512] == b"\x55\xaa"
                    ):
                        return False
                    # The common extractor certifies wrapper geometry and its
                    # embedded RXF; arbitrary magic in trailing data is not enough.
                    extract_rxf(raw + stream.read())
                    return True
            if len(raw) < HEADER_SIZE:
                return False
            header = BinaryHeader.from_wire(raw[:HEADER_SIZE])
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

    def cached(self, entry_id: str) -> RXFSnapshot | None:
        entry = self.get_entry(entry_id)
        with self._lock:
            snapshot = self._cache.get(entry_id)
            return (
                snapshot if snapshot is not None and snapshot.entry == entry else None
            )

    def load_progress(self, entry_id: str) -> dict[str, object]:
        entry = self.get_entry(entry_id)
        with self._lock:
            snapshot = self._cache.get(entry_id)
            if snapshot is not None and snapshot.entry == entry:
                progress = self._progress.setdefault(entry_id, LoadProgress(entry))
                if not progress.done:
                    progress.update(LoadPhase.DONE, 1, 1, "snapshot")
                    progress.done = True
                return progress.inspect()
            progress = self._progress.get(entry_id)
            return (progress or LoadProgress(entry)).inspect()

    def preload(self, entry_id: str) -> None:
        entry = self.get_entry(entry_id)
        with self._condition:
            if entry_id in self._loading or (
                entry_id in self._cache and self._cache[entry_id].entry == entry
            ):
                return
            progress = LoadProgress(entry)
            self._progress[entry_id] = progress
            self._loading[entry_id] = progress
        Thread(
            target=self._preload_owned,
            args=(entry_id, entry, progress),
            name=f"rxf-load:{entry_id}",
            daemon=True,
        ).start()

    def _preload_owned(
        self, entry_id: str, entry: CatalogEntry, progress: LoadProgress
    ) -> None:
        try:
            self._load_owned(entry_id, entry, progress)
        except (OSError, ValueError):
            # Error state is recorded by _load_owned for nonblocking clients.
            return

    def _load_owned(
        self, entry_id: str, entry: CatalogEntry, progress: LoadProgress
    ) -> RXFSnapshot:
        def update(phase: str, completed: int, total: int, unit: str) -> None:
            with self._lock:
                progress.update(phase, completed, total, unit)

        try:
            update(LoadPhase.READING, 0, entry.size, "bytes")
            # Preloads must recheck the mount too: a cataloged file can have
            # been replaced by a symlink or grown beyond the configured limit.
            current = self._resolve_entry(entry.mount, entry.relative_path)
            if current != entry:
                raise ValueError("mounted RXF artifact changed; rescan before loading")
            source = current.path.read_bytes()
            blob = extract_rxf(source)
            header = BinaryHeader.from_wire(blob)
            node_bytes = header.node_count * NODE_ENTRY_SIZE
            progress.set_work_plan(
                (
                    (LoadPhase.READING, entry.size),
                    (LoadPhase.HEADER, HEADER_SIZE),
                    (LoadPhase.REFERENCES, header.ref_table_count * 24),
                    (LoadPhase.SECTIONS, header.section_table_count * 40),
                    (LoadPhase.NODES, node_bytes),
                    (LoadPhase.HEAP, header.frontier),
                    (LoadPhase.TYPES, header.type_table_count * TYPE_TABLE_ENTRY_SIZE),
                    (LoadPhase.CODE, header.code_table_count * 32),
                    (LoadPhase.CONTAINER, node_bytes),
                    (LoadPhase.NODE_INDEX, node_bytes),
                    (LoadPhase.OBJECT_INDEX, node_bytes),
                    (LoadPhase.TREE_INDEX, node_bytes),
                    (LoadPhase.SEARCH_INDEX, node_bytes),
                    (LoadPhase.MEMORY_INDEX, header.frontier),
                    (LoadPhase.DONE, 1),
                )
            )
            update(LoadPhase.READING, len(source), entry.size, "bytes")
            layout = unpack_layout(blob, progress=update)
            update(LoadPhase.CONTAINER, 0, len(layout.nodes), "nodes")
            container = layout_to_container(
                layout,
                progress=lambda completed, total: update(
                    LoadPhase.CONTAINER, completed, total, "nodes"
                ),
            )
            update(LoadPhase.INDEXING, 0, len(layout.heap.cells), "cells")
            loaded = RXFSnapshot(
                entry=entry,
                blob=blob,
                layout=layout,
                container=container,
                defer_indexes=True,
            )
            loaded.build_indexes(update)
        except BaseException as error:
            with self._condition:
                progress.phase = LoadPhase.ERROR
                progress.error = str(error)
                progress.updated_at = time()
                if self._loading.get(entry_id) is progress:
                    del self._loading[entry_id]
                self._condition.notify_all()
            raise
        with self._condition:
            if (
                self._loading.get(entry_id) is progress
                and self._catalog.get(entry_id) == entry
            ):
                self._cache[entry_id] = loaded
                progress.update(LoadPhase.DONE, 1, 1, "snapshot")
                progress.done = True
                del self._loading[entry_id]
            self._condition.notify_all()
        return loaded

    def load(self, entry_id: str) -> RXFSnapshot:
        entry = self.get_entry(entry_id)
        current = self._resolve_entry(entry.mount, entry.relative_path)
        if current != entry:
            self.scan()
            entry = self.get_entry(entry_id)
        with self._condition:
            while entry_id in self._loading:
                self._condition.wait()
                cached = self._cache.get(entry_id)
                if cached is not None and cached.entry == entry:
                    return cached
                progress = self._progress.get(entry_id)
                if progress is not None and progress.error:
                    raise ValueError(progress.error)
            cached = self._cache.get(entry_id)
            if cached is not None and cached.entry == entry:
                return cached
            progress = LoadProgress(entry)
            self._progress[entry_id] = progress
            self._loading[entry_id] = progress
        return self._load_owned(entry_id, entry, progress)

    def _resolve_entry(self, mount_name: str, relative_path: str) -> CatalogEntry:
        mount = self._mounts.get(mount_name)
        if mount is None:
            raise KeyError(mount_name)
        relative = PurePosixPath(relative_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() not in (".rxf", ".elf", ".efi", ".img", "")
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
