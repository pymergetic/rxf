"""Pack and unpack canonical RXF v5 with one global heap stream."""

from __future__ import annotations

import json
import struct
from collections.abc import Iterable

from pymergetic.rxf.model.state import ObjectState, OwnerKind
from pymergetic.rxf.output.binary import BinaryLayout
from pymergetic.rxf.output.cell import HeapImage
from pymergetic.rxf.output.code_table import CODE_TABLE_ENTRY_SIZE, CodeLocation
from pymergetic.rxf.output.header import (
    FORMAT_VERSION,
    HEADER_CAPACITY,
    HEADER_SIZE,
    MAGIC,
    UNKNOWN_LIMIT,
    BinaryHeader,
)
from pymergetic.rxf.output.node import NodeEntry, NodeKind, RefEntry
from pymergetic.rxf.output.region import SectionMap, SectionPerm, SectionSpan
from pymergetic.rxf.output.type_table import TYPE_TABLE_ENTRY_SIZE, TypeLocation
from pymergetic.rxf.schema import NODE_INVALID
from pymergetic.rxf.schema import NodeKind as SchemaNodeKind
from pymergetic.rxf.ty.align import align_up

NODE_ENTRY_FORMAT = "<QQIIQQQQIIQQQQQQQ"
NODE_ENTRY_SIZE = struct.calcsize(NODE_ENTRY_FORMAT)
REF_ENTRY_FORMAT = "<QQII"
REF_ENTRY_SIZE = struct.calcsize(REF_ENTRY_FORMAT)
SECTION_ENTRY_FORMAT = "<QQQQII"
SECTION_ENTRY_SIZE = struct.calcsize(SECTION_ENTRY_FORMAT)


def _validate_range(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError(f"{label} is outside the RXF: offset={offset}, size={size}")


def _bounded_slice(values: list, first: int, count: int, label: str) -> list:
    if first > len(values) or count > len(values) - first:
        raise ValueError(f"{label} is outside its table")
    return values[first : first + count]


def _read_string(table: bytes, offset: int) -> str:
    if offset >= len(table):
        raise ValueError("string offset is outside string table")
    end = table.find(b"\0", offset)
    if end < 0:
        raise ValueError("string is not NUL-terminated")
    return table[offset:end].decode("utf-8")


def _build_strings(layout: BinaryLayout) -> tuple[bytes, dict[str, int]]:
    buffer = bytearray()
    offsets: dict[str, int] = {}

    def add(value: str) -> None:
        if value not in offsets:
            offsets[value] = len(buffer)
            buffer.extend(value.encode("utf-8") + b"\0")

    for node in layout.nodes:
        add(node.name)
        for section in node.sections:
            add(section.name)
    return bytes(buffer), offsets


def _build_attributes(
    nodes: Iterable[NodeEntry],
) -> tuple[bytes, list[tuple[int, int]]]:
    buffer = bytearray()
    spans: list[tuple[int, int]] = []
    for node in nodes:
        if not node.attrs:
            spans.append((NODE_INVALID, 0))
            continue
        encoded = json.dumps(
            node.attrs, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        spans.append((len(buffer), len(encoded)))
        buffer.extend(encoded)
    return bytes(buffer), spans


def _validate_heap_geometry(
    *, image_size: int, committed_size: int, frontier: int, limit: int | None
) -> None:
    if image_size > committed_size:
        raise ValueError("image_size exceeds committed_size")
    if frontier > committed_size:
        raise ValueError("frontier exceeds committed_size")
    if limit is not None and committed_size > limit:
        raise ValueError("committed_size exceeds known heap limit")


def pack_layout(layout: BinaryLayout) -> bytes:
    """Serialize without mutating the caller-owned layout or its heap geometry."""
    references = [reference for node in layout.nodes for reference in node.refs]
    sections = [section for node in layout.nodes for section in node.sections]
    attributes, attribute_spans = _build_attributes(layout.nodes)
    strings, string_offsets = _build_strings(layout)
    type_table = sorted(layout.type_table, key=lambda entry: entry.type_id)
    code_table = sorted(
        layout.code_table,
        key=lambda entry: (entry.function_id, entry.target_id, entry.code_id),
    )

    node_table_off = HEADER_CAPACITY
    ref_table_off = node_table_off + len(layout.nodes) * NODE_ENTRY_SIZE
    section_table_off = ref_table_off + len(references) * REF_ENTRY_SIZE
    type_table_off = section_table_off + len(sections) * SECTION_ENTRY_SIZE
    code_table_off = type_table_off + len(type_table) * TYPE_TABLE_ENTRY_SIZE
    attr_table_off = code_table_off + len(code_table) * CODE_TABLE_ENTRY_SIZE
    string_table_off = attr_table_off + len(attributes)
    heap_off = align_up(string_table_off + len(strings), layout.heap.align)
    heap_bytes = layout.heap.to_wire()

    # Image base is file offset zero, so image_size is the exact serialized file extent.
    image_size = heap_off + len(heap_bytes)
    committed_size = max(layout.heap.committed_size, image_size)
    _validate_heap_geometry(
        image_size=image_size,
        committed_size=committed_size,
        frontier=layout.heap.frontier,
        limit=layout.heap.limit,
    )

    node_table = bytearray(len(layout.nodes) * NODE_ENTRY_SIZE)
    ref_index = 0
    section_index = 0
    for index, node in enumerate(layout.nodes):
        attr_first, attr_size = attribute_spans[index]
        struct.pack_into(
            NODE_ENTRY_FORMAT,
            node_table,
            index * NODE_ENTRY_SIZE,
            node.id,
            string_offsets[node.name],
            int(node.kind),
            node.state.pack(),
            node.parent,
            node.type_id,
            node.generation,
            node.owner,
            int(node.owner_kind),
            0,
            ref_index if node.refs else NODE_INVALID,
            len(node.refs),
            section_index if node.sections else NODE_INVALID,
            len(node.sections),
            attr_first,
            attr_size,
            node.size,
        )
        ref_index += len(node.refs)
        section_index += len(node.sections)

    ref_table = bytearray(len(references) * REF_ENTRY_SIZE)
    for index, reference in enumerate(references):
        struct.pack_into(
            REF_ENTRY_FORMAT,
            ref_table,
            index * REF_ENTRY_SIZE,
            reference.target,
            reference.to_off,
            int(reference.kind),
            int(reference.binding),
        )

    section_table = bytearray(len(sections) * SECTION_ENTRY_SIZE)
    for index, section in enumerate(sections):
        struct.pack_into(
            SECTION_ENTRY_FORMAT,
            section_table,
            index * SECTION_ENTRY_SIZE,
            section.node_id,
            string_offsets[section.name],
            section.offset,
            section.size,
            int(section.map),
            int(section.perm),
        )

    header = BinaryHeader(
        version=FORMAT_VERSION,
        header_size=HEADER_SIZE,
        header_capacity=HEADER_CAPACITY,
        heap_off=heap_off,
        image_size=image_size,
        committed_size=committed_size,
        heap_limit=UNKNOWN_LIMIT if layout.heap.limit is None else layout.heap.limit,
        frontier=layout.heap.frontier,
        heap_align=layout.heap.align,
        page_size=layout.heap.page_size,
        node_count=len(layout.nodes),
        node_table_off=node_table_off,
        string_table_off=string_table_off,
        string_table_size=len(strings),
        entry_node=layout.header.entry_node,
        flags=layout.header.flags,
        ref_table_off=ref_table_off,
        ref_table_count=len(references),
        section_table_off=section_table_off,
        section_table_count=len(sections),
        attr_table_off=attr_table_off,
        attr_table_size=len(attributes),
        type_table_off=type_table_off,
        type_table_count=len(type_table),
        code_table_off=code_table_off,
        code_table_count=len(code_table),
    )

    output = bytearray(header.to_wire())
    output.extend(b"\0" * (header.header_capacity - len(output)))
    output.extend(node_table)
    output.extend(ref_table)
    output.extend(section_table)
    for entry in type_table:
        output.extend(entry.to_wire())
    for entry in code_table:
        output.extend(entry.to_wire())
    output.extend(attributes)
    output.extend(strings)
    output.extend(b"\0" * (heap_off - len(output)))
    output.extend(heap_bytes)
    if len(output) != image_size:
        raise AssertionError("packed image extent disagrees with header image_size")
    return bytes(output)


def unpack_layout(data: bytes) -> BinaryLayout:
    """Parse and validate a canonical RXF v5 image."""
    bootstrap_size = 16
    _validate_range(data, 0, bootstrap_size, "header bootstrap")
    magic, version, header_size, header_capacity = struct.unpack_from("<4s3I", data)
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    if version not in (4, FORMAT_VERSION):
        raise ValueError(f"unsupported RXF version {version}")
    if header_size != HEADER_SIZE:
        raise ValueError(
            f"header_size disagrees with current schema: {header_size}!={HEADER_SIZE}"
        )
    if header_capacity < header_size:
        raise ValueError("header_capacity is smaller than header_size")
    if header_capacity % BinaryHeader.__align__:
        raise ValueError("header_capacity is not header-aligned")
    _validate_range(data, 0, header_capacity, "reserved header prefix")
    if any(data[header_size:header_capacity]):
        raise ValueError("reserved header bytes must be zero")
    header = BinaryHeader.from_wire(data)
    header.version = version  # preserve explicit v4 inspection translation
    if header.image_size != len(data):
        raise ValueError(
            f"image_size disagrees with RXF extent: {header.image_size}!={len(data)}"
        )
    limit = None if header.heap_limit == UNKNOWN_LIMIT else header.heap_limit
    _validate_heap_geometry(
        image_size=header.image_size,
        committed_size=header.committed_size,
        frontier=header.frontier,
        limit=limit,
    )

    ranges = (
        (header.node_table_off, header.node_count * NODE_ENTRY_SIZE, "node table"),
        (
            header.ref_table_off,
            header.ref_table_count * REF_ENTRY_SIZE,
            "reference table",
        ),
        (
            header.section_table_off,
            header.section_table_count * SECTION_ENTRY_SIZE,
            "section table",
        ),
        (
            header.type_table_off,
            header.type_table_count * TYPE_TABLE_ENTRY_SIZE,
            "type table",
        ),
        (
            header.code_table_off,
            header.code_table_count * CODE_TABLE_ENTRY_SIZE,
            "code table",
        ),
        (header.attr_table_off, header.attr_table_size, "attribute blob"),
        (header.string_table_off, header.string_table_size, "string table"),
        (header.heap_off, header.frontier, "heap"),
    )
    previous_end = header.header_capacity
    for offset, size, label in ranges:
        _validate_range(data, offset, size, label)
        if offset < header.header_capacity:
            raise ValueError(f"{label} overlaps reserved header prefix")
        if offset < previous_end:
            raise ValueError(f"{label} is out of canonical order or overlaps")
        previous_end = offset + size

    strings = data[
        header.string_table_off : header.string_table_off + header.string_table_size
    ]
    attribute_blob = data[
        header.attr_table_off : header.attr_table_off + header.attr_table_size
    ]

    references: list[RefEntry] = []
    for index in range(header.ref_table_count):
        target, to_off, kind, binding = struct.unpack_from(
            REF_ENTRY_FORMAT, data, header.ref_table_off + index * REF_ENTRY_SIZE
        )
        references.append(
            RefEntry(target=target, to_off=to_off, kind=kind, binding=binding)
        )

    sections: list[SectionSpan] = []
    for index in range(header.section_table_count):
        node_id, name_off, offset, size, map_value, perm_value = struct.unpack_from(
            SECTION_ENTRY_FORMAT,
            data,
            header.section_table_off + index * SECTION_ENTRY_SIZE,
        )
        sections.append(
            SectionSpan(
                node_id=node_id,
                name=_read_string(strings, name_off),
                offset=offset,
                size=size,
                map=SectionMap(map_value),
                perm=SectionPerm(perm_value),
            )
        )

    nodes: list[NodeEntry] = []
    for index in range(header.node_count):
        values = struct.unpack_from(
            NODE_ENTRY_FORMAT, data, header.node_table_off + index * NODE_ENTRY_SIZE
        )
        state = ObjectState.unpack(values[3])
        attrs: dict[str, object] = {}
        if values[15]:
            _validate_range(attribute_blob, values[14], values[15], "node attributes")
            decoded = json.loads(attribute_blob[values[14] : values[14] + values[15]])
            if not isinstance(decoded, dict):
                raise ValueError("node attributes are not a JSON object")
            attrs = decoded
        if (values[10] == NODE_INVALID) != (values[11] == 0):
            raise ValueError("node reference span sentinel/count disagreement")
        if (values[12] == NODE_INVALID) != (values[13] == 0):
            raise ValueError("node section span sentinel/count disagreement")
        nodes.append(
            NodeEntry(
                id=values[0],
                name=_read_string(strings, values[1]),
                kind=NodeKind(values[2]),
                state=state,
                parent=values[4],
                type_id=values[5],
                generation=values[6],
                owner=values[7],
                owner_kind=OwnerKind(values[8]),
                ref_first=values[10],
                ref_count=values[11],
                section_first=values[12],
                section_count=values[13],
                attr_first=values[14],
                attr_count=values[15],
                size=values[16],
                refs=[]
                if values[10] == NODE_INVALID
                else _bounded_slice(
                    references, values[10], values[11], "node reference span"
                ),
                sections=[]
                if values[12] == NODE_INVALID
                else _bounded_slice(
                    sections, values[12], values[13], "node section span"
                ),
                attrs=attrs,
            )
        )

    heap = HeapImage.from_wire(
        data[header.heap_off : header.heap_off + header.frontier],
        image_size=header.image_size,
        committed_size=header.committed_size,
        frontier=header.frontier,
        limit=limit,
        align=header.heap_align,
        page_size=header.page_size,
    )
    nodes_by_id = {node.id: node for node in nodes}
    if len(nodes_by_id) != len(nodes):
        raise ValueError("node table contains duplicate IDs")
    cells_by_id = {cell.header.id: cell for cell in heap.cells}
    for cell_id, cell in cells_by_id.items():
        node = nodes_by_id.get(cell_id)
        if node is None:
            raise ValueError(f"cell {cell_id} has no matching node-table object")
        expected = (
            node.type_id,
            node.parent,
            node.generation,
            node.size,
            node.state.pack(),
        )
        actual = (
            cell.header.type_id,
            cell.header.parent,
            cell.header.generation,
            cell.header.payload_size,
            cell.header.flags,
        )
        if actual != expected:
            raise ValueError(f"cell {cell_id} disagrees with node table")

    type_table = [
        TypeLocation.from_wire(
            data, header.type_table_off + index * TYPE_TABLE_ENTRY_SIZE
        )
        for index in range(header.type_table_count)
    ]
    type_ids = [entry.type_id for entry in type_table]
    if type_ids != sorted(set(type_ids)):
        raise ValueError("type table must be sorted with unique type IDs")
    for entry in type_table:
        cell = cells_by_id.get(entry.type_id)
        node = nodes_by_id.get(entry.type_id)
        if node is None or node.kind != NodeKind.TYPE:
            raise ValueError(f"type-table entry {entry.type_id} is not a TYPE object")
        if cell is None or cell.offset != entry.cell_offset:
            raise ValueError(
                f"type-table entry {entry.type_id} has an invalid cell location"
            )

    code_table = [
        CodeLocation.from_wire(
            data, header.code_table_off + index * CODE_TABLE_ENTRY_SIZE
        )
        for index in range(header.code_table_count)
    ]
    code_keys = [
        (entry.function_id, entry.target_id, entry.code_id) for entry in code_table
    ]
    if code_keys != sorted(set(code_keys)):
        raise ValueError("code table must be sorted with unique lookup keys")
    for entry in code_table:
        cell = cells_by_id.get(entry.code_id)
        node = nodes_by_id.get(entry.code_id)
        if node is None or node.kind != NodeKind.CODE:
            raise ValueError(f"code-table entry {entry.code_id} is not a CODE object")
        if cell is None or cell.offset != entry.cell_offset:
            raise ValueError(
                f"code-table entry {entry.code_id} has an invalid cell location"
            )
        from pymergetic.rxf.execution.decode import decode_code
        from pymergetic.rxf.model.node import NodeDef

        semantic_code = decode_code(
            NodeDef(
                id=node.id,
                name=node.name,
                kind=SchemaNodeKind.CODE,
                parent=node.parent,
                type_id=node.type_id,
                data=cell.payload,
            )
        )
        if (entry.function_id, entry.target_id) != (
            semantic_code.owner_function,
            semantic_code.target_id,
        ):
            raise ValueError(
                f"code-table entry {entry.code_id} payload agreement is invalid"
            )

    return BinaryLayout(
        header=header,
        nodes=nodes,
        type_table=type_table,
        code_table=code_table,
        heap=heap,
    )
