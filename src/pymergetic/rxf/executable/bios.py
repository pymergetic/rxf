"""Deterministic legacy BIOS raw disk image transport."""

from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from pymergetic.rxf.executable.models import (
    ExecutableImage,
    ExecutablePlatform,
    SegmentKind,
    SegmentPermissions,
)
from pymergetic.rxf.output.engine import unpack_layout
from pymergetic.rxf.output.header import HEADER_SIZE, BinaryHeader
from pymergetic.rxf.output.header import MAGIC as RXF_MAGIC

SECTOR_SIZE = 512
STAGE2_SIZE = 4096
MANIFEST_SIZE = 4096
MAGIC = b"RXFBIOS1"
VERSION = 1
_HEADER = struct.Struct("<8sHHIQQIIIIIII")
_SEGMENT = struct.Struct("<QQQIIII")
BOOT_LOADER_SECTORS_OFFSET = 492
MANIFEST_OFFSET = SECTOR_SIZE + STAGE2_SIZE
PAYLOAD_OFFSET = MANIFEST_OFFSET + MANIFEST_SIZE
IDENTITY_MAP_MIN = 0x100000
# Keep mapped runtime segments below the 64 MiB disk staging area.
IDENTITY_MAP_LIMIT = 0x04000000
_ALLOWED_PERMISSIONS = {
    int(SegmentPermissions.READ),
    int(SegmentPermissions.READ | SegmentPermissions.WRITE),
    int(SegmentPermissions.READ | SegmentPermissions.EXECUTE),
}
_ALLOWED_KINDS = frozenset(int(kind) for kind in SegmentKind)
_UINT32_LIMIT = 1 << 32
_UINT64_LIMIT = 1 << 64


class BiosImageError(ValueError):
    """Malformed or unpackageable BIOS image."""


@dataclass(frozen=True)
class BiosSegment:
    virtual_address: int
    memory_size: int
    data: bytes
    permissions: SegmentPermissions
    kind: SegmentKind


@dataclass(frozen=True)
class BiosManifest:
    entry_address: int
    entry_function_id: int
    segments: tuple[BiosSegment, ...]
    payload_size: int
    payload_checksum: int

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(b"".join(s.data for s in self.segments)).hexdigest()


def _checked_end(start: int, size: int, limit: int, field: str) -> int:
    if start < 0 or size < 0 or start >= limit or size >= limit:
        raise BiosImageError(f"{field} is out of range")
    end = start + size
    if end > limit:
        raise BiosImageError(f"{field} overflows")
    return end


def _validate_segment(
    virtual_address: int,
    memory_size: int,
    file_size: int,
    payload_offset: int,
    permissions: int,
    kind: int,
    payload_size: int,
) -> tuple[int, int]:
    virtual_end = _checked_end(
        virtual_address, memory_size, _UINT64_LIMIT, "segment virtual extent"
    )
    if virtual_address < IDENTITY_MAP_MIN or virtual_end > IDENTITY_MAP_LIMIT:
        raise BiosImageError("segment lies outside the identity map")
    if file_size > memory_size:
        raise BiosImageError("segment file size exceeds memory size")
    payload_end = _checked_end(
        payload_offset, file_size, _UINT32_LIMIT, "segment payload extent"
    )
    if payload_end > payload_size:
        raise BiosImageError("segment payload extent is out of bounds")
    if permissions not in _ALLOWED_PERMISSIONS:
        raise BiosImageError("invalid or writable-executable segment permissions")
    if kind not in _ALLOWED_KINDS:
        raise BiosImageError("invalid BIOS segment kind")
    return virtual_end, payload_end


def _native_blobs() -> tuple[bytes, bytes]:
    root = Path(__file__).resolve().parents[4]
    native = root / "native" / "executable" / "bios"
    clang = shutil.which("clang-18") or shutil.which("clang")
    lld = shutil.which("ld.lld-18") or shutil.which("ld.lld")
    if not clang or not lld:
        raise BiosImageError("clang/lld are required to build BIOS envelope")
    with tempfile.TemporaryDirectory(prefix="rxf-bios-") as tmp:
        b = Path(tmp)

        def run(*a: str) -> None:
            subprocess.run(a, check=True, capture_output=True)

        run(
            clang,
            "--target=i386-none-elf",
            "-c",
            str(native / "boot.S"),
            "-o",
            str(b / "boot.o"),
        )
        run(
            lld,
            "-T",
            str(native / "boot.ld"),
            "--oformat=binary",
            str(b / "boot.o"),
            "-o",
            str(b / "boot.bin"),
        )
        flags = (
            "--target=x86_64-none-elf",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-stack-protector",
            "-fno-pic",
            "-mno-red-zone",
            "-mgeneral-regs-only",
            "-mgeneral-regs-only",
        )
        run(clang, *flags, "-c", str(native / "stage2.S"), "-o", str(b / "stage2.o"))
        run(
            clang,
            *flags,
            "-Os",
            "-c",
            str(native / "runtime.c"),
            "-o",
            str(b / "runtime.o"),
        )
        run(
            lld,
            "-T",
            str(native / "stage2.ld"),
            "--oformat=binary",
            str(b / "stage2.o"),
            str(b / "runtime.o"),
            "-o",
            str(b / "stage2.bin"),
        )
        boot = (b / "boot.bin").read_bytes()
        stage = (b / "stage2.bin").read_bytes()
    if len(boot) != 512 or boot[510:] != b"\x55\xaa":
        raise BiosImageError("native build produced invalid boot sector")
    if len(stage) > STAGE2_SIZE:
        raise BiosImageError("stage2 exceeds 4096 bytes")
    return boot, stage.ljust(STAGE2_SIZE, b"\0")


def build_bios_image(image: ExecutableImage) -> bytes:
    """Package an x86-64 ExecutableImage as deterministic raw BIOS disk bytes."""
    if image.target.platform is not ExecutablePlatform.BIOS:
        raise BiosImageError("image target must be legacy BIOS")
    if not image.segments:
        raise BiosImageError("image must contain segments")
    if not 0 < image.entry_function_id < _UINT64_LIMIT:
        raise BiosImageError("entry Function ID must be nonzero uint64")
    payload = bytearray()
    descriptors: list[bytes] = []
    previous_virtual_end = 0
    entry_matches = 0
    for segment in image.segments:
        offset = len(payload)
        virtual_end, payload_end = _validate_segment(
            segment.virtual_address,
            segment.memory_size,
            len(segment.data),
            offset,
            int(segment.permissions),
            int(segment.kind),
            _UINT32_LIMIT - 1,
        )
        if segment.virtual_address < previous_virtual_end:
            raise BiosImageError("segments must be sorted and must not overlap")
        previous_virtual_end = virtual_end
        if segment.virtual_address <= image.entry_address < virtual_end:
            if not segment.permissions & SegmentPermissions.EXECUTE:
                raise BiosImageError("entry lies in a non-executable segment")
            entry_matches += 1
        descriptors.append(
            _SEGMENT.pack(
                segment.virtual_address,
                segment.memory_size,
                len(segment.data),
                offset,
                int(segment.permissions),
                int(segment.kind),
                0,
            )
        )
        payload.extend(segment.data)
        if payload_end != len(payload):
            raise BiosImageError("payload extent accounting failure")
    if entry_matches != 1:
        raise BiosImageError("entry must belong exactly once to an executable segment")
    descriptor_bytes = b"".join(descriptors)
    if _HEADER.size + len(descriptor_bytes) > MANIFEST_SIZE:
        raise BiosImageError("segment manifest exceeds 4096 bytes")
    payload_end = PAYLOAD_OFFSET + len(payload)
    if payload_end >= _UINT32_LIMIT:
        raise BiosImageError("BIOS payload geometry overflows uint32")
    total = (payload_end + SECTOR_SIZE - 1) & -SECTOR_SIZE
    checksum = zlib.crc32(payload) & 0xFFFFFFFF
    header = _HEADER.pack(
        MAGIC,
        VERSION,
        _HEADER.size,
        total,
        image.entry_address,
        image.entry_function_id,
        len(image.segments),
        _HEADER.size,
        PAYLOAD_OFFSET,
        len(payload),
        checksum,
        0,
        0,
    )
    boot, stage = _native_blobs()
    body = stage + (header + descriptor_bytes).ljust(MANIFEST_SIZE, b"\0") + payload
    body = body.ljust(total - SECTOR_SIZE, b"\0")
    sectors = len(body) // SECTOR_SIZE
    if not 1 <= sectors <= 0x7FFF:
        raise BiosImageError("BIOS image exceeds loader capacity")
    boot = (
        boot[:BOOT_LOADER_SECTORS_OFFSET]
        + struct.pack("<H", sectors)
        + boot[BOOT_LOADER_SECTORS_OFFSET + 2 :]
    )
    raw = boot + body
    parse_bios_image(raw)
    return raw


def parse_bios_image(raw: bytes, *, verify_checksum: bool = True) -> BiosManifest:
    """Independently parse and validate raw BIOS disk bytes."""
    if len(raw) < PAYLOAD_OFFSET or len(raw) % SECTOR_SIZE:
        raise BiosImageError("invalid padded BIOS disk geometry")
    if raw[510:512] != b"\x55\xaa":
        raise BiosImageError("invalid BIOS boot signature")
    loader_sectors = struct.unpack_from("<H", raw, BOOT_LOADER_SECTORS_OFFSET)[0]
    if not 16 <= loader_sectors <= 0x7FFF:
        raise BiosImageError("boot loader sector count exceeds loader capacity")
    if loader_sectors != (len(raw) - SECTOR_SIZE) // SECTOR_SIZE:
        raise BiosImageError("boot loader sector count disagrees with image")
    values = _HEADER.unpack_from(raw, MANIFEST_OFFSET)
    (
        magic,
        version,
        header_size,
        total,
        entry,
        function_id,
        count,
        descriptor_offset,
        payload_offset,
        payload_size,
        checksum,
        flags,
        reserved,
    ) = values
    if magic != MAGIC or version != VERSION or header_size != _HEADER.size:
        raise BiosImageError("invalid BIOS manifest header or version")
    if flags or reserved:
        raise BiosImageError("unsupported BIOS manifest flags")
    if total != len(raw):
        raise BiosImageError("manifest total size disagrees with image")
    if function_id == 0:
        raise BiosImageError("entry Function ID must be nonzero")
    if descriptor_offset != _HEADER.size or payload_offset != PAYLOAD_OFFSET:
        raise BiosImageError("invalid BIOS manifest layout")
    maximum_count = (MANIFEST_SIZE - _HEADER.size) // _SEGMENT.size
    if count == 0 or count > maximum_count:
        raise BiosImageError("invalid BIOS segment count")
    descriptor_end = descriptor_offset + count * _SEGMENT.size
    absolute_payload_end = _checked_end(
        payload_offset, payload_size, _UINT32_LIMIT, "BIOS payload"
    )
    expected_total = (absolute_payload_end + SECTOR_SIZE - 1) & -SECTOR_SIZE
    if (
        descriptor_end > MANIFEST_SIZE
        or expected_total != total
        or absolute_payload_end > len(raw)
    ):
        raise BiosImageError("invalid padded BIOS payload geometry")
    if any(raw[MANIFEST_OFFSET + descriptor_end : PAYLOAD_OFFSET]):
        raise BiosImageError("nonzero bytes in manifest padding")
    if any(raw[absolute_payload_end:]):
        raise BiosImageError("nonzero bytes in disk padding")
    payload = raw[payload_offset:absolute_payload_end]
    if verify_checksum and zlib.crc32(payload) & 0xFFFFFFFF != checksum:
        raise BiosImageError("invalid BIOS payload checksum")
    segments: list[BiosSegment] = []
    previous_virtual_end = 0
    previous_payload_end = 0
    entry_matches = 0
    for index in range(count):
        at = MANIFEST_OFFSET + descriptor_offset + index * _SEGMENT.size
        va, ms, fs, off, permissions, kind, segment_reserved = _SEGMENT.unpack_from(
            raw, at
        )
        if segment_reserved:
            raise BiosImageError("nonzero BIOS segment reserved field")
        virtual_end, payload_end = _validate_segment(
            va, ms, fs, off, permissions, kind, payload_size
        )
        if va < previous_virtual_end:
            raise BiosImageError("BIOS descriptors are unsorted or overlap in memory")
        if off != previous_payload_end:
            raise BiosImageError(
                "BIOS payload extents overlap, alias, or lack deterministic ownership"
            )
        previous_virtual_end = virtual_end
        previous_payload_end = payload_end
        if va <= entry < virtual_end:
            if not permissions & int(SegmentPermissions.EXECUTE):
                raise BiosImageError("entry lies in a non-executable segment")
            entry_matches += 1
        segments.append(
            BiosSegment(
                va,
                ms,
                payload[off:payload_end],
                SegmentPermissions(permissions),
                SegmentKind(kind),
            )
        )
    if previous_payload_end != payload_size:
        raise BiosImageError("BIOS payload has unowned bytes")
    if entry_matches != 1:
        raise BiosImageError("entry must belong exactly once to an executable segment")
    return BiosManifest(entry, function_id, tuple(segments), payload_size, checksum)


def extract_bios_rxf(data: bytes) -> bytes:
    """Extract the canonical graph from the validated BIOS segment manifest.

    Segment kind, permissions and file extent are authority, never a search for
    magic embedded in arbitrary native code or runtime data.
    """
    manifest = parse_bios_image(data)
    graphs = [s for s in manifest.segments if s.kind is SegmentKind.RXF_IMAGE]
    if len(graphs) != 1:
        raise BiosImageError("BIOS image requires exactly one RXF_IMAGE segment")
    graph = graphs[0]
    if graph.permissions != SegmentPermissions.READ | SegmentPermissions.EXECUTE:
        raise BiosImageError("RXF_IMAGE must be read-execute")
    if len(graph.data) < HEADER_SIZE or graph.data[:4] != RXF_MAGIC:
        raise BiosImageError("RXF_IMAGE lacks a canonical RXF header")
    try:
        header = BinaryHeader.from_wire(graph.data)
        if not HEADER_SIZE <= header.image_size <= len(graph.data):
            raise ValueError("RXF image_size exceeds mapped file extent")
        raw = graph.data[: header.image_size]
        unpack_layout(raw)
    except (ValueError, struct.error) as error:
        raise BiosImageError(f"invalid RXF_IMAGE: {error}") from error
    return raw
