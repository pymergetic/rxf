"""Deterministic static ELF64 ET_EXEC formatter and independent checker."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, replace
from pathlib import Path

from pymergetic.rxf.executable.models import (
    ArtifactFormat,
    ExecutableImage,
    ExecutablePlatform,
    ExecutableSegment,
    ExecutableSymbol,
    ExecutableTarget,
    SegmentKind,
    SegmentPermissions,
    SymbolKind,
)
from pymergetic.rxf.model.target import AARCH64_ID, X86_64_ID

ET_EXEC, EM_X86_64, EM_AARCH64 = 2, 62, 183
PT_LOAD, PT_GNU_STACK = 1, 0x6474E551
PF_X, PF_W, PF_R = 1, 2, 4
PAGE_ALIGNMENT, ELF_HEADER_SIZE, PROGRAM_HEADER_SIZE = 0x1000, 64, 56
_EHDR = struct.Struct("<16sHHIQQQIHHHHHH")
_PHDR = struct.Struct("<IIQQQQQQ")


class ELFFormatError(ValueError):
    """An image cannot be formatted or fails strict ELF validation."""


@dataclass(frozen=True)
class ELF64Header:
    machine: int
    entry: int
    program_header_offset: int
    program_header_size: int
    program_header_count: int


@dataclass(frozen=True)
class ELF64ProgramHeader:
    segment_type: int
    flags: int
    file_offset: int
    virtual_address: int
    file_size: int
    memory_size: int
    alignment: int


@dataclass(frozen=True)
class ELF64Inspection:
    header: ELF64Header
    program_headers: tuple[ELF64ProgramHeader, ...]


def _flags(p: SegmentPermissions) -> int:
    return (
        (PF_R if p & SegmentPermissions.READ else 0)
        | (PF_W if p & SegmentPermissions.WRITE else 0)
        | (PF_X if p & SegmentPermissions.EXECUTE else 0)
    )


def _machine(image: ExecutableImage) -> int:
    if image.target.architecture_id == X86_64_ID:
        return EM_X86_64
    if image.target.architecture_id == AARCH64_ID:
        return EM_AARCH64
    raise ELFFormatError("unsupported Linux ELF64 architecture")


def layout_elf64_image(
    target: ExecutableTarget,
    entry_function_id: int,
    text: bytes,
    *,
    rodata: bytes = b"",
    data: bytes = b"",
    bss_size: int = 0,
    entry_offset: int = 0,
    base_address: int = 0x400000,
) -> ExecutableImage:
    """Adapt finalized native bytes to the shared deterministic segment model."""
    if base_address % PAGE_ALIGNMENT or not text or not 0 <= entry_offset < len(text):
        raise ELFFormatError("invalid ELF64 native image layout")
    if bss_size < 0:
        raise ELFFormatError("ELF64 BSS size must not be negative")
    text_address = base_address + PAGE_ALIGNMENT
    rodata_address = text_address + PAGE_ALIGNMENT
    data_address = rodata_address + PAGE_ALIGNMENT
    segments = (
        ExecutableSegment(
            "text",
            SegmentKind.TEXT,
            text_address,
            len(text),
            PAGE_ALIGNMENT,
            PAGE_ALIGNMENT,
            SegmentPermissions.READ | SegmentPermissions.EXECUTE,
            text,
        ),
        ExecutableSegment(
            "rodata",
            SegmentKind.READ_ONLY_DATA,
            rodata_address,
            len(rodata),
            2 * PAGE_ALIGNMENT,
            PAGE_ALIGNMENT,
            SegmentPermissions.READ,
            rodata,
        ),
        ExecutableSegment(
            "data",
            SegmentKind.DATA,
            data_address,
            len(data) + bss_size,
            3 * PAGE_ALIGNMENT,
            PAGE_ALIGNMENT,
            SegmentPermissions.READ | SegmentPermissions.WRITE,
            data,
        ),
    )
    entry_address = text_address + entry_offset
    symbol = ExecutableSymbol(
        entry_function_id,
        "entry",
        SymbolKind.ENTRY,
        entry_address,
        len(text) - entry_offset,
    )
    return ExecutableImage(
        target, entry_function_id, entry_address, segments, (symbol,)
    )


def install_linux_startup(image: ExecutableImage, startup: bytes) -> ExecutableImage:
    """Bind an authored process adapter to shared context and entry addresses."""
    offsets = {X86_64_ID: (0xA8, 0xB0), AARCH64_ID: (0xA0, 0xA8)}.get(
        image.target.architecture_id
    )
    start = next((s for s in image.segments if s.kind is SegmentKind.STARTUP), None)
    runtime = next(
        (s for s in image.segments if s.kind is SegmentKind.RUNTIME_TABLES), None
    )
    if image.target.platform is not ExecutablePlatform.LINUX or offsets is None:
        raise ELFFormatError("unsupported Linux startup target")
    if start is None or runtime is None or len(startup) > start.memory_size:
        raise ELFFormatError("Linux image lacks a valid startup/runtime layout")
    context_offset, entry_offset = offsets
    if entry_offset + 8 > len(startup):
        raise ELFFormatError("Linux startup adapter is truncated")
    bound = bytearray(startup)
    struct.pack_into("<Q", bound, context_offset, runtime.virtual_address)
    struct.pack_into("<Q", bound, entry_offset, image.entry_address)
    file_offset = PAGE_ALIGNMENT
    segments = []
    for segment in image.segments:
        data = bytes(bound) if segment.kind is SegmentKind.STARTUP else segment.data
        memory_size = (
            len(data) if segment.kind is SegmentKind.STARTUP else segment.memory_size
        )
        segments.append(
            replace(
                segment, file_offset=file_offset, memory_size=memory_size, data=data
            )
        )
        file_offset += (len(data) + PAGE_ALIGNMENT - 1) & -PAGE_ALIGNMENT
    return replace(image, entry_address=start.virtual_address, segments=tuple(segments))


def format_elf64(image: ExecutableImage) -> bytes:
    """Format shared image segments as a sectionless static ELF64 executable."""
    if (
        image.target.platform is not ExecutablePlatform.LINUX
        or image.target.artifact_format is not ArtifactFormat.ELF64
    ):
        raise ELFFormatError("ELF64 formatter requires a Linux ELF64 target")
    machine = _machine(image)
    permissions = {_flags(s.permissions) for s in image.segments}
    if not {PF_R | PF_X, PF_R | PF_W} <= permissions or not permissions <= {
        PF_R | PF_X,
        PF_R,
        PF_R | PF_W,
    }:
        raise ELFFormatError("ELF64 image requires separate RX and RW segments")
    header_end = ELF_HEADER_SIZE + PROGRAM_HEADER_SIZE * (len(image.segments) + 1)
    occupied: list[tuple[int, int]] = []
    entry_count = 0
    for s in image.segments:
        if (
            s.alignment != PAGE_ALIGNMENT
            or s.file_offset < header_end
            or s.file_offset % PAGE_ALIGNMENT != s.virtual_address % PAGE_ALIGNMENT
        ):
            raise ELFFormatError("ELF64 PT_LOAD offset/address layout is invalid")
        extent = (s.file_offset, s.file_offset + len(s.data))
        if extent[0] != extent[1] and any(
            extent[0] < end and start < extent[1] for start, end in occupied
        ):
            raise ELFFormatError("ELF64 segment file ranges overlap")
        occupied.append(extent)
        if s.virtual_address <= image.entry_address < s.virtual_address + s.memory_size:
            entry_count += 1
            if not s.permissions & SegmentPermissions.EXECUTE:
                raise ELFFormatError("ELF64 entry is not executable")
    if entry_count != 1:
        raise ELFFormatError(
            "ELF64 entry must belong to exactly one executable segment"
        )
    ident = b"ELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    output = bytearray(
        max(header_end, *(s.file_offset + len(s.data) for s in image.segments))
    )
    output[:ELF_HEADER_SIZE] = _EHDR.pack(
        ident,
        ET_EXEC,
        machine,
        1,
        image.entry_address,
        ELF_HEADER_SIZE,
        0,
        0,
        ELF_HEADER_SIZE,
        PROGRAM_HEADER_SIZE,
        len(image.segments) + 1,
        0,
        0,
        0,
    )
    cursor = ELF_HEADER_SIZE
    for s in image.segments:
        output[cursor : cursor + PROGRAM_HEADER_SIZE] = _PHDR.pack(
            PT_LOAD,
            _flags(s.permissions),
            s.file_offset,
            s.virtual_address,
            s.virtual_address,
            len(s.data),
            s.memory_size,
            PAGE_ALIGNMENT,
        )
        cursor += PROGRAM_HEADER_SIZE
        output[s.file_offset : s.file_offset + len(s.data)] = s.data
    output[cursor : cursor + PROGRAM_HEADER_SIZE] = _PHDR.pack(
        PT_GNU_STACK, PF_R | PF_W, 0, 0, 0, 0, 0, 16
    )
    blob = bytes(output)
    check_elf64(blob, expected_machine=machine)
    return blob


def parse_elf64(blob: bytes) -> ELF64Inspection:
    """Parse ELF metadata independently from the formatter."""
    if len(blob) < ELF_HEADER_SIZE:
        raise ELFFormatError("truncated ELF64 header")
    v = _EHDR.unpack_from(blob)
    if v[0][:7] != b"ELF" or v[1] != ET_EXEC or v[3] != 1:
        raise ELFFormatError("not a current little-endian ELF64 ET_EXEC")
    h = ELF64Header(v[2], v[4], v[5], v[9], v[10])
    if v[8] != ELF_HEADER_SIZE or h.program_header_size != PROGRAM_HEADER_SIZE:
        raise ELFFormatError("non-canonical ELF64 header sizes")
    if h.program_header_offset + h.program_header_size * h.program_header_count > len(
        blob
    ):
        raise ELFFormatError("truncated ELF64 program header table")
    ph = []
    for i in range(h.program_header_count):
        r = _PHDR.unpack_from(blob, h.program_header_offset + i * h.program_header_size)
        ph.append(ELF64ProgramHeader(r[0], r[1], r[2], r[3], r[5], r[6], r[7]))
    return ELF64Inspection(h, tuple(ph))


def check_elf64(blob: bytes, *, expected_machine: int | None = None) -> ELF64Inspection:
    """Certify static W^X loads, explicit entry, and non-executable stack."""
    result = parse_elf64(blob)
    if expected_machine is not None and result.header.machine != expected_machine:
        raise ELFFormatError("ELF64 machine does not match target")
    if result.header.machine not in {EM_X86_64, EM_AARCH64}:
        raise ELFFormatError("unsupported ELF64 machine")
    loads = tuple(p for p in result.program_headers if p.segment_type == PT_LOAD)
    stacks = tuple(p for p in result.program_headers if p.segment_type == PT_GNU_STACK)
    if (
        len(loads) + len(stacks) != len(result.program_headers)
        or len(stacks) != 1
        or stacks[0].flags != PF_R | PF_W
    ):
        raise ELFFormatError(
            "ELF64 requires only PT_LOAD and one non-executable GNU_STACK"
        )
    permissions = {p.flags for p in loads}
    if not {PF_R | PF_X, PF_R | PF_W} <= permissions or not permissions <= {
        PF_R | PF_X,
        PF_R,
        PF_R | PF_W,
    }:
        raise ELFFormatError("ELF64 requires separate RX and RW PT_LOAD segments")
    ranges: list[tuple[int, int]] = []
    file_ranges: list[tuple[int, int]] = []
    entry_count = 0
    for p in loads:
        if (
            p.alignment != PAGE_ALIGNMENT
            or p.file_size > p.memory_size
            or p.file_offset + p.file_size > len(blob)
        ):
            raise ELFFormatError("invalid ELF64 PT_LOAD extent or alignment")
        if p.file_offset % p.alignment != p.virtual_address % p.alignment:
            raise ELFFormatError("invalid ELF64 PT_LOAD congruence")
        if p.virtual_address + p.memory_size >= 1 << 64:
            raise ELFFormatError("ELF64 PT_LOAD address overflow")
        if p.file_size:
            file_extent = (p.file_offset, p.file_offset + p.file_size)
            if any(
                file_extent[0] < end and start < file_extent[1]
                for start, end in file_ranges
            ):
                raise ELFFormatError("overlapping ELF64 PT_LOAD file ranges")
            file_ranges.append(file_extent)
        extent = (p.virtual_address, p.virtual_address + p.memory_size)
        if any(extent[0] < end and start < extent[1] for start, end in ranges):
            raise ELFFormatError("overlapping ELF64 PT_LOAD ranges")
        ranges.append(extent)
        if extent[0] <= result.header.entry < extent[1]:
            entry_count += 1
            if not p.flags & PF_X:
                raise ELFFormatError("ELF64 entry is not executable")
    if entry_count != 1:
        raise ELFFormatError("ELF64 entry must belong to exactly one PT_LOAD")
    return result


def extract_elf_rxf(blob: bytes) -> bytes:
    """Recover the single mapped RXF image without section names or symbols."""
    from pymergetic.rxf.executable.artifact import rxf_segment_bytes
    from pymergetic.rxf.output.header import MAGIC

    parsed = check_elf64(blob)
    candidates = [
        blob[p.file_offset : p.file_offset + p.file_size]
        for p in parsed.program_headers
        if p.segment_type == PT_LOAD
        and p.file_size >= len(MAGIC)
        and blob[p.file_offset : p.file_offset + len(MAGIC)] == MAGIC
    ]
    if len(candidates) != 1:
        raise ELFFormatError("ELF64 must contain exactly one mapped RXF image")
    return rxf_segment_bytes(candidates[0])


def write_elf64(
    path: str | os.PathLike[str], image: ExecutableImage, *, executable: bool = True
) -> Path:
    """Write ELF bytes and add execute bits only at the final boundary."""
    destination = Path(path)
    destination.write_bytes(format_elf64(image))
    if executable:
        destination.chmod(destination.stat().st_mode | 0o111)
    return destination
