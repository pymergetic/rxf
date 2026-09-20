"""Typed, platform-neutral contracts for executable packaging."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Any

UINT64_LIMIT = 1 << 64


def _uint64(value: int, field: str) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 0 <= value < UINT64_LIMIT
    ):
        raise ValueError(f"{field} must be uint64")


def _positive_uint64(value: int, field: str) -> None:
    _uint64(value, field)
    if value == 0:
        raise ValueError(f"{field} must be positive")


def _power_of_two(value: int, field: str) -> None:
    _positive_uint64(value, field)
    if value & (value - 1):
        raise ValueError(f"{field} must be a power of two")


def _digest(value: bytes, field: str) -> None:
    if len(value) != 32:
        raise ValueError(f"{field} must be 32 bytes")


class ArtifactFormat(IntEnum):
    ELF64 = 1
    PE32_PLUS = 2
    BIOS_DISK_IMAGE = 3


class ExecutablePlatform(IntEnum):
    LINUX = 1
    UEFI = 2
    BIOS = 3


class SegmentPermissions(IntFlag):
    NONE = 0
    READ = 1 << 0
    WRITE = 1 << 1
    EXECUTE = 1 << 2


class SegmentKind(IntEnum):
    STARTUP = 1
    TEXT = 2
    READ_ONLY_DATA = 3
    DATA = 4
    BSS = 5
    HEAP = 6
    JOURNAL = 7
    CLEANUP = 8
    STACK = 9
    RUNTIME_TABLES = 10
    LEGACY_BOOT = 11
    RXF_IMAGE = 12


class SymbolKind(IntEnum):
    ENTRY = 1
    FUNCTION = 2
    OBJECT = 3
    RUNTIME = 4
    PLATFORM = 5


class RuntimeTableKind(IntEnum):
    FUNCTION = 1
    OBJECT = 2
    CAPABILITY = 3
    NATIVE_SLOT = 4
    TRANSACTION = 5


class DiagnosticSeverity(IntEnum):
    INFO = 1
    WARNING = 2
    ERROR = 3


@dataclass(frozen=True)
class ExecutableTarget:
    target_id: int
    name: str
    artifact_format: ArtifactFormat
    platform: ExecutablePlatform
    architecture_id: int
    abi_id: int
    environment_id: int
    legacy_shim: bool = False

    def __post_init__(self) -> None:
        for field in ("target_id", "architecture_id", "abi_id", "environment_id"):
            _uint64(getattr(self, field), field)
        if not self.name:
            raise ValueError("target name must not be empty")
        expected = {
            ExecutablePlatform.LINUX: ArtifactFormat.ELF64,
            ExecutablePlatform.UEFI: ArtifactFormat.PE32_PLUS,
            ExecutablePlatform.BIOS: ArtifactFormat.BIOS_DISK_IMAGE,
        }[self.platform]
        if self.artifact_format is not expected:
            raise ValueError("artifact format does not match platform")
        if self.legacy_shim is not (self.platform is ExecutablePlatform.BIOS):
            raise ValueError("legacy shim is required only for BIOS targets")

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "name": self.name,
            "artifact_format": self.artifact_format.name,
            "platform": self.platform.name,
            "architecture_id": self.architecture_id,
            "abi_id": self.abi_id,
            "environment_id": self.environment_id,
            "legacy_shim": self.legacy_shim,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ExecutableTarget:
        return cls(
            target_id=value["target_id"],
            name=value["name"],
            artifact_format=ArtifactFormat[value["artifact_format"]],
            platform=ExecutablePlatform[value["platform"]],
            architecture_id=value["architecture_id"],
            abi_id=value["abi_id"],
            environment_id=value["environment_id"],
            legacy_shim=value.get("legacy_shim", False),
        )


@dataclass(frozen=True)
class ExecutableSegment:
    name: str
    kind: SegmentKind
    virtual_address: int
    memory_size: int
    file_offset: int
    alignment: int
    permissions: SegmentPermissions
    data: bytes = b""

    def __post_init__(self) -> None:
        for field in ("virtual_address", "memory_size", "file_offset"):
            _uint64(getattr(self, field), field)
        _power_of_two(self.alignment, "alignment")
        if not self.name:
            raise ValueError("segment name must not be empty")
        if len(self.data) > self.memory_size:
            raise ValueError("segment data exceeds memory size")
        if (
            self.permissions & SegmentPermissions.WRITE
            and self.permissions & SegmentPermissions.EXECUTE
        ):
            raise ValueError("segment must not be writable and executable")
        if self.virtual_address % self.alignment or self.file_offset % self.alignment:
            raise ValueError("segment addresses must satisfy alignment")


@dataclass(frozen=True)
class ExecutableSymbol:
    object_id: int
    name: str
    kind: SymbolKind
    address: int
    size: int = 0

    def __post_init__(self) -> None:
        for field in ("object_id", "address", "size"):
            _uint64(getattr(self, field), field)
        if not self.name:
            raise ValueError("symbol name must not be empty")


@dataclass(frozen=True)
class RuntimeTable:
    kind: RuntimeTableKind
    address: int
    entry_size: int
    object_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        _uint64(self.address, "address")
        _positive_uint64(self.entry_size, "entry_size")
        for object_id in self.object_ids:
            _uint64(object_id, "object_id")
        if tuple(sorted(set(self.object_ids))) != self.object_ids:
            raise ValueError("runtime table object IDs must be sorted and unique")


@dataclass(frozen=True)
class ExecutableDiagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    object_id: int | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.message:
            raise ValueError("diagnostic code and message must not be empty")
        if self.object_id is not None:
            _uint64(self.object_id, "object_id")


@dataclass(frozen=True)
class ExecutableProvenance:
    source_digest: bytes
    output_digest: bytes
    toolchain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _digest(self.source_digest, "source_digest")
        _digest(self.output_digest, "output_digest")
        if any(not item for item in self.toolchain):
            raise ValueError("toolchain entries must not be empty")


@dataclass(frozen=True)
class ExecutableImage:
    target: ExecutableTarget
    entry_function_id: int
    entry_address: int
    segments: tuple[ExecutableSegment, ...]
    symbols: tuple[ExecutableSymbol, ...] = ()
    runtime_tables: tuple[RuntimeTable, ...] = ()
    diagnostics: tuple[ExecutableDiagnostic, ...] = ()
    provenance: ExecutableProvenance | None = None

    def __post_init__(self) -> None:
        _uint64(self.entry_function_id, "entry_function_id")
        _uint64(self.entry_address, "entry_address")
        segment_keys = tuple(
            (item.virtual_address, item.name) for item in self.segments
        )
        for left, right in zip(self.segments, self.segments[1:], strict=False):
            if left.virtual_address + left.memory_size > right.virtual_address:
                raise ValueError("executable segments must not overlap")
        if segment_keys != tuple(sorted(segment_keys)):
            raise ValueError("segments must be sorted by address and name")
        symbol_keys = tuple(
            (item.address, item.object_id, item.name) for item in self.symbols
        )
        if symbol_keys != tuple(sorted(symbol_keys)):
            raise ValueError("symbols must be deterministically sorted")
        table_kinds = tuple(item.kind for item in self.runtime_tables)
        if table_kinds != tuple(sorted(set(table_kinds))):
            raise ValueError("runtime tables must be sorted and unique by kind")
