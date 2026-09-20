"""Deterministic PE32+ EFI formatter and independent checker."""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.executable.models import (
    ArtifactFormat,
    ExecutableImage,
    ExecutablePlatform,
    SegmentKind,
    SegmentPermissions,
)
from pymergetic.rxf.execution.decode import decode_code, decode_relocation
from pymergetic.rxf.model.execution import CallRole, RelocationKind
from pymergetic.rxf.model.target import AARCH64_ID, X86_64_ID
from pymergetic.rxf.output.cell import CELL_HEADER_SIZE
from pymergetic.rxf.output.engine import unpack_layout
from pymergetic.rxf.output.header import HEADER_SIZE, BinaryHeader
from pymergetic.rxf.output.header import MAGIC as RXF_MAGIC
from pymergetic.rxf.ty.builtins import CODE_TYPE

MACHINE_X86_64 = 0x8664
MACHINE_AARCH64 = 0xAA64
SUBSYSTEM_EFI_APPLICATION = 10
SECTION_ALIGNMENT = 0x1000
FILE_ALIGNMENT = 0x200
IMAGE_REL_BASED_DIR64 = 10
RX = 0x60000020
R = 0x40000040
RW = 0xC0000040


class PEFormatError(ValueError):
    pass


@dataclass(frozen=True)
class PESection:
    name: str
    virtual_size: int
    virtual_address: int
    raw_size: int
    raw_offset: int
    characteristics: int


@dataclass(frozen=True)
class PE32PlusInspection:
    machine: int
    entry_rva: int
    image_base: int
    section_alignment: int
    file_alignment: int
    size_of_image: int
    size_of_headers: int
    subsystem: int
    directories: tuple[tuple[int, int], ...]
    sections: tuple[PESection, ...]


def _align(v: int, a: int) -> int:
    return (v + a - 1) & -a


def _machine(image: ExecutableImage) -> int:
    if image.target.architecture_id == X86_64_ID:
        return MACHINE_X86_64
    if image.target.architecture_id == AARCH64_ID:
        return MACHINE_AARCH64
    raise PEFormatError("unsupported UEFI architecture")


def _characteristics(p: SegmentPermissions, data: bool) -> int:
    if p & SegmentPermissions.WRITE and p & SegmentPermissions.EXECUTE:
        raise PEFormatError("writable executable segment")
    if p & SegmentPermissions.EXECUTE:
        return RX
    if p & SegmentPermissions.WRITE:
        return RW
    return R if data else 0x40000080


def install_uefi_startup(image: ExecutableImage, startup: bytes) -> ExecutableImage:
    """Bind the EFI adapter to the shared RuntimeContext and entry address."""
    start = next((s for s in image.segments if s.kind is SegmentKind.STARTUP), None)
    runtime = next(
        (s for s in image.segments if s.kind is SegmentKind.RUNTIME_TABLES), None
    )
    if image.target.platform is not ExecutablePlatform.UEFI:
        raise PEFormatError("UEFI startup requires a UEFI image")
    if (
        start is None
        or runtime is None
        or len(startup) > start.memory_size
        or len(startup) < 16
    ):
        raise PEFormatError("UEFI image lacks a valid startup/runtime layout")
    bound = bytearray(startup)
    struct.pack_into(
        "<QQ", bound, len(bound) - 16, runtime.virtual_address, image.entry_address
    )
    segments = tuple(
        replace(segment, data=bytes(bound))
        if segment.kind is SegmentKind.STARTUP
        else segment
        for segment in image.segments
    )
    return replace(image, entry_address=start.virtual_address, segments=segments)


def _relocation_addresses(image: ExecutableImage) -> tuple[int, ...]:
    """Return every authored absolute image pointer that the PE loader must move."""
    result: list[int] = []
    function_addresses: dict[int, int] = {}
    for segment in image.segments:
        if segment.kind is SegmentKind.STARTUP:
            if len(segment.data) < 16:
                raise PEFormatError("bound UEFI startup is truncated")
            result.extend(
                (
                    segment.virtual_address + len(segment.data) - 16,
                    segment.virtual_address + len(segment.data) - 8,
                )
            )
        elif segment.kind is SegmentKind.RUNTIME_TABLES:
            data = segment.data
            if len(data) < 144:
                raise PEFormatError("runtime context is truncated")
            function_count = struct.unpack_from("<Q", data, 16)[0]
            object_count = struct.unpack_from("<Q", data, 32)[0]
            capability_count = struct.unpack_from("<Q", data, 48)[0]
            if (
                144 + function_count * 24 + object_count * 80 + capability_count * 24
                > len(data)
            ):
                raise PEFormatError("runtime tables are truncated")
            if struct.unpack_from("<QQ", data) != (2, 144):
                raise PEFormatError("unsupported runtime context")
            result.extend(
                segment.virtual_address + offset
                for offset in (24, 40, 56, 64, 112, 136)
                if struct.unpack_from("<Q", data, offset)[0]
            )
            cursor = 144
            for i in range(function_count):
                fid, _generation, address = struct.unpack_from(
                    "<3Q", data, cursor + i * 24
                )
                function_addresses[fid] = address
            result.extend(
                segment.virtual_address + cursor + i * 24 + 16
                for i in range(function_count)
                if struct.unpack_from("<Q", data, cursor + i * 24 + 16)[0]
            )
            cursor += function_count * 24
            result.extend(
                segment.virtual_address + cursor + i * 80 + 24
                for i in range(object_count)
                if struct.unpack_from("<Q", data, cursor + i * 80 + 24)[0]
            )
            cursor += object_count * 80
            for i in range(capability_count):
                if struct.unpack_from("<Q", data, cursor + i * 24 + 16)[0]:
                    result.append(segment.virtual_address + cursor + i * 24 + 16)
    # Only explicitly bound Code fixups may touch the canonical graph. IDs,
    # cell offsets, arbitrary integer payloads and inactive Code are not VAs.
    for segment in image.segments:
        if segment.kind is not SegmentKind.RXF_IMAGE:
            continue
        layout = unpack_layout(segment.data)
        container = layout_to_container(layout)
        nodes_by_id = {node.id: node for node in container.nodes}
        payload_addresses = {
            cell.header.id: segment.virtual_address
            + layout.header.heap_off
            + cell.offset
            + CELL_HEADER_SIZE
            for cell in layout.heap.cells
        }
        for node in container.nodes:
            if node.type_id != CODE_TYPE:
                continue
            code = decode_code(node)
            raw_address = payload_addresses[node.id] + len(node.data) - code.byte_count
            if (
                function_addresses.get(code.owner_function)
                != raw_address + code.entry_offset
            ):
                continue
            # Bound Code is an ordinary derived object; source Code owns the
            # authored relocation records and remains byte-for-byte unchanged.
            fixup_owner = node
            if not any(r.to_off == int(CallRole.RELOCATION) for r in node.refs):
                sources = [
                    nodes_by_id.get(r.target)
                    for r in node.refs
                    if r.to_off == int(CallRole.SOURCE)
                ]
                code_sources = [
                    s for s in sources if s is not None and s.type_id == CODE_TYPE
                ]
                if len(code_sources) > 1:
                    raise PEFormatError("bound Code source is ambiguous")
                if code_sources:
                    fixup_owner = code_sources[0]
            for ref in fixup_owner.refs:
                if ref.to_off != int(CallRole.RELOCATION):
                    continue
                relocation_node = nodes_by_id.get(ref.target)
                if relocation_node is None:
                    raise PEFormatError("bound Code relocation is missing")
                relocation = decode_relocation(relocation_node)
                if relocation.kind is RelocationKind.PC_RELATIVE:
                    continue
                if relocation.patch_width != 8:
                    raise PEFormatError("absolute PE Code fixup must be DIR64")
                if relocation.offset + 8 > code.byte_count:
                    raise PEFormatError("bound Code fixup exceeds raw bytes")
                result.append(raw_address + relocation.offset)
    return tuple(sorted(set(result)))


def _relocation_blob(addresses: tuple[int, ...], base: int) -> bytes:
    pages: dict[int, list[int]] = {}
    for address in addresses:
        rva = address - base
        if rva < 0:
            raise PEFormatError("relocation precedes image base")
        pages.setdefault(rva & -SECTION_ALIGNMENT, []).append(rva & 0xFFF)
    output = bytearray()
    for page in sorted(pages):
        entries = [
            (IMAGE_REL_BASED_DIR64 << 12) | offset for offset in sorted(pages[page])
        ]
        if len(entries) & 1:
            entries.append(0)
        output.extend(struct.pack("<II", page, 8 + 2 * len(entries)))
        output.extend(struct.pack(f"<{len(entries)}H", *entries))
    return bytes(output)


def format_pe32_plus(image: ExecutableImage) -> bytes:
    """Map an already-closed shared image into PE sections."""
    if (
        image.target.platform is not ExecutablePlatform.UEFI
        or image.target.artifact_format is not ArtifactFormat.PE32_PLUS
    ):
        raise PEFormatError("PE32+ requires UEFI target")
    machine = _machine(image)
    if not image.segments:
        raise PEFormatError("empty image")
    base = min(s.virtual_address for s in image.segments) - SECTION_ALIGNMENT
    if base < 0 or base % SECTION_ALIGNMENT:
        raise PEFormatError("unaligned image base")
    count = len(image.segments) + 1
    headers = _align(0x80 + 4 + 20 + 0xF0 + 40 * count, FILE_ALIGNMENT)
    cursor = headers
    sections = []
    for i, s in enumerate(image.segments):
        rva = s.virtual_address - base
        if rva % SECTION_ALIGNMENT or s.alignment > SECTION_ALIGNMENT:
            raise PEFormatError("unaligned segment")
        raw = _align(len(s.data), FILE_ALIGNMENT) if s.data else 0
        sections.append(
            (
                b".rxfimg" if s.kind is SegmentKind.RXF_IMAGE else f".rxf{i}".encode(),
                s.memory_size,
                rva,
                raw,
                cursor if raw else 0,
                _characteristics(s.permissions, bool(s.data)),
                s.data,
            )
        )
        cursor += raw
    end = max(
        rva + _align(s.memory_size, SECTION_ALIGNMENT)
        for (_, _, rva, _, _, _, _), s in zip(sections, image.segments, strict=True)
    )
    reloc_rva = _align(end, SECTION_ALIGNMENT)
    reloc = _relocation_blob(_relocation_addresses(image), base)
    if not reloc:
        raise PEFormatError("image has no absolute pointers to relocate")
    sections.append(
        (
            b".reloc",
            len(reloc),
            reloc_rva,
            _align(len(reloc), FILE_ALIGNMENT),
            cursor,
            R,
            reloc,
        )
    )
    cursor += _align(len(reloc), FILE_ALIGNMENT)
    entry = image.entry_address - base
    if not any(
        r <= entry < r + vs and ch & 0x20000000 for _, vs, r, _, _, ch, _ in sections
    ):
        raise PEFormatError("entry not executable")
    size_image = _align(reloc_rva + len(reloc), SECTION_ALIGNMENT)
    dos = bytearray(0x80)
    dos[:2] = b"MZ"
    struct.pack_into("<I", dos, 0x3C, 0x80)
    coff = struct.pack("<HHIIIHH", machine, count, 0, 0, 0, 0xF0, 0x22)
    opt = bytearray(0xF0)
    size_code = sum(raw for _, _, _, raw, _, ch, _ in sections if ch & 0x20)
    size_init = sum(raw for _, _, _, raw, _, ch, _ in sections if ch & 0x40)
    struct.pack_into(
        "<HBBIIIII", opt, 0, 0x20B, 0, 0, size_code, size_init, 0, entry, 0
    )
    struct.pack_into("<QII", opt, 24, base, SECTION_ALIGNMENT, FILE_ALIGNMENT)
    struct.pack_into(
        "<IIIHH", opt, 56, size_image, headers, 0, SUBSYSTEM_EFI_APPLICATION, 0x8160
    )
    struct.pack_into("<QQQQII", opt, 72, 0x10000, 0x1000, 0x10000, 0x1000, 0, 16)
    struct.pack_into("<II", opt, 112 + 5 * 8, reloc_rva, len(reloc))
    out = bytearray(cursor)
    out[:0x80] = dos
    out[0x80:0x84] = b"PE\0\0"
    out[0x84:0x98] = coff
    out[0x98:0x188] = opt
    sh = 0x188
    for name, vs, rva, raw, off, ch, data in sections:
        struct.pack_into(
            "<8sIIIIIIHHI",
            out,
            sh,
            name.ljust(8, b"\0"),
            vs,
            rva,
            raw,
            off,
            0,
            0,
            0,
            0,
            ch,
        )
        sh += 40
        if data:
            out[off : off + len(data)] = data
    blob = bytes(out)
    check_pe32_plus(blob, expected_machine=machine)
    return blob


def parse_pe32_plus(blob: bytes) -> PE32PlusInspection:
    if len(blob) < 0x188 or blob[:2] != b"MZ":
        raise PEFormatError("missing DOS signature")
    pe = struct.unpack_from("<I", blob, 0x3C)[0]
    if pe + 24 > len(blob) or blob[pe : pe + 4] != b"PE\0\0":
        raise PEFormatError("missing PE signature")
    machine, count, _time, psym, nsym, optsize, ch = struct.unpack_from(
        "<HHIIIHH", blob, pe + 4
    )
    if psym or nsym or optsize != 0xF0 or not ch & 2:
        raise PEFormatError("invalid COFF header")
    o = pe + 24
    if o + optsize > len(blob) or struct.unpack_from("<H", blob, o)[0] != 0x20B:
        raise PEFormatError("not PE32+")
    entry = struct.unpack_from("<I", blob, o + 16)[0]
    base = struct.unpack_from("<Q", blob, o + 24)[0]
    sa, fa = struct.unpack_from("<II", blob, o + 32)
    sizei, sizeh = struct.unpack_from("<II", blob, o + 56)
    subsystem = struct.unpack_from("<H", blob, o + 68)[0]
    ndir = struct.unpack_from("<I", blob, o + 108)[0]
    if ndir > 16:
        raise PEFormatError("too many directories")
    directories = tuple(
        struct.unpack_from("<II", blob, o + 112 + i * 8) for i in range(ndir)
    )
    sh = o + optsize
    if sh + count * 40 > len(blob):
        raise PEFormatError("truncated section table")
    sections = []
    for i in range(count):
        v = struct.unpack_from("<8sIIIIIIHHI", blob, sh + i * 40)
        sections.append(
            PESection(v[0].rstrip(b"\0").decode("ascii"), v[1], v[2], v[3], v[4], v[9])
        )
    return PE32PlusInspection(
        machine,
        entry,
        base,
        sa,
        fa,
        sizei,
        sizeh,
        subsystem,
        directories,
        tuple(sections),
    )


def _rva_data(blob: bytes, p: PE32PlusInspection, rva: int, size: int) -> bytes:
    for s in p.sections:
        if s.virtual_address <= rva and rva + size <= s.virtual_address + s.raw_size:
            off = s.raw_offset + rva - s.virtual_address
            if off + size <= len(blob):
                return blob[off : off + size]
    raise PEFormatError("directory not backed by section")


def check_pe32_plus(
    blob: bytes, *, expected_machine: int | None = None
) -> PE32PlusInspection:
    p = parse_pe32_plus(blob)
    if expected_machine is not None and p.machine != expected_machine:
        raise PEFormatError("machine mismatch")
    if (
        p.machine not in {MACHINE_X86_64, MACHINE_AARCH64}
        or p.subsystem != SUBSYSTEM_EFI_APPLICATION
    ):
        raise PEFormatError("not supported EFI application")
    if not p.sections:
        raise PEFormatError("empty section table")
    pe_offset = struct.unpack_from("<I", blob, 0x3C)[0]
    if pe_offset + 24 + 0xF0 + len(p.sections) * 40 > p.size_of_headers:
        raise PEFormatError("section table exceeds headers")
    if (
        (p.section_alignment, p.file_alignment) != (SECTION_ALIGNMENT, FILE_ALIGNMENT)
        or p.size_of_headers % FILE_ALIGNMENT
        or p.size_of_headers > len(blob)
        or p.size_of_image % SECTION_ALIGNMENT
    ):
        raise PEFormatError("invalid sizes or alignment")
    expected_image_size = _align(
        max(s.virtual_address + max(1, s.virtual_size) for s in p.sections),
        SECTION_ALIGNMENT,
    )
    if p.size_of_image != expected_image_size or p.entry_rva < SECTION_ALIGNMENT:
        raise PEFormatError("inconsistent image size or entry RVA")
    entries = [
        s
        for s in p.sections
        if s.virtual_address <= p.entry_rva < s.virtual_address + s.virtual_size
    ]
    if len(entries) != 1 or not entries[0].characteristics & 0x20000000:
        raise PEFormatError("entry not executable")
    ranges = []
    file_ranges = []
    for s in p.sections:
        if s.characteristics & 0x20000000 and s.characteristics & 0x80000000:
            raise PEFormatError("W^X violation")
        if (
            s.virtual_address < p.size_of_headers
            or s.virtual_address % SECTION_ALIGNMENT
            or (
                s.raw_size
                and (
                    s.raw_offset < p.size_of_headers
                    or s.raw_size % FILE_ALIGNMENT
                    or s.raw_offset % FILE_ALIGNMENT
                    or s.raw_offset + s.raw_size > len(blob)
                )
            )
        ):
            raise PEFormatError("invalid section extent")
        ext = (
            s.virtual_address,
            s.virtual_address + _align(max(1, s.virtual_size), SECTION_ALIGNMENT),
        )
        if any(ext[0] < b and a < ext[1] for a, b in ranges):
            raise PEFormatError("overlapping sections")
        ranges.append(ext)
        if s.raw_size:
            file_extent = (s.raw_offset, s.raw_offset + s.raw_size)
            if any(file_extent[0] < b and a < file_extent[1] for a, b in file_ranges):
                raise PEFormatError("overlapping section file extents")
            file_ranges.append(file_extent)
    if len(p.directories) <= 5:
        raise PEFormatError("missing reloc directory")
    rva, size = p.directories[5]
    if not rva or size < 12:
        raise PEFormatError("empty reloc directory")
    reloc = _rva_data(blob, p, rva, size)
    off = 0
    entries_count = 0
    while off < len(reloc):
        if off + 8 > len(reloc):
            raise PEFormatError("truncated reloc block")
        page, block = struct.unpack_from("<II", reloc, off)
        if (
            page % SECTION_ALIGNMENT
            or block < 8
            or block % 4
            or off + block > len(reloc)
        ):
            raise PEFormatError("invalid reloc block")
        for at in range(off + 8, off + block, 2):
            value = struct.unpack_from("<H", reloc, at)[0]
            if value >> 12 == IMAGE_REL_BASED_DIR64:
                target = page + (value & 0xFFF)
                if not any(
                    s.virtual_address <= target
                    and target + 8
                    <= s.virtual_address + min(s.raw_size, s.virtual_size)
                    for s in p.sections
                    if s.name != ".reloc"
                ):
                    raise PEFormatError(
                        "relocation target is outside mapped file bytes"
                    )
                entries_count += 1
            elif value:
                raise PEFormatError("unsupported reloc type")
        off += block
    if off != len(reloc) or not entries_count:
        raise PEFormatError("empty relocations")
    return p


def extract_pe_rxf(data: bytes) -> bytes:
    """Extract a validated RXF graph using its named, mapped PE descriptor.

    The canonical BinaryHeader bounds the RXF bytes within the section's file
    and virtual extents. Alignment padding is not part of the returned graph.
    """
    parsed = check_pe32_plus(data)
    graphs = [s for s in parsed.sections if s.name == ".rxfimg"]
    if len(graphs) != 1:
        raise PEFormatError("PE image requires exactly one RXF_IMAGE section")
    section = graphs[0]
    if section.characteristics != RX:
        raise PEFormatError("RXF_IMAGE must be read-execute")
    extent = min(section.virtual_size, section.raw_size)
    start = section.raw_offset
    if extent < HEADER_SIZE or data[start : start + 4] != RXF_MAGIC:
        raise PEFormatError("RXF_IMAGE lacks a canonical RXF header")
    try:
        header = BinaryHeader.from_wire(data, start)
        if not HEADER_SIZE <= header.image_size <= extent:
            raise ValueError("RXF image_size exceeds mapped file extent")
        raw = data[start : start + header.image_size]
        unpack_layout(raw)
    except (ValueError, struct.error) as error:
        raise PEFormatError(f"invalid RXF_IMAGE: {error}") from error
    return raw
