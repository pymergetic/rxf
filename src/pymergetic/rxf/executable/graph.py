"""Complete, recoverable RXF images used directly by executable runtimes.

Offsets here are relative to byte zero of the ordinary RXFB image, not to a
second native text/data copy. Packaging may move the segment in the file, but
must preserve its virtual address (bound Code can contain absolute addresses).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.execution.decode import decode_code
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.output.binary import BinaryLayout
from pymergetic.rxf.output.cell import CELL_HEADER_SIZE, Cell, CellHeader, HeapImage
from pymergetic.rxf.output.code_table import CodeLocation
from pymergetic.rxf.output.engine import pack_layout, unpack_layout
from pymergetic.rxf.output.header import MAGIC, BinaryHeader
from pymergetic.rxf.output.node import NodeEntry, NodeKind, RefEntry
from pymergetic.rxf.output.region import SectionMap, SectionPerm, SectionSpan
from pymergetic.rxf.output.type_table import TypeLocation
from pymergetic.rxf.ty.builtins import CODE_TYPE
from pymergetic.rxf.ty.table import TypeTable

CODE_HEADER_SIZE = struct.calcsize("<3Q4I2Q32s")


@dataclass(frozen=True)
class GraphImage:
    """Canonical bytes and exact embedded cell/raw-Code locations, keyed by ID."""

    data: bytes
    payload_offsets: dict[int, int]
    code_offsets: dict[int, int]
    code_entry_offsets: dict[int, int]

    def payload_address(self, object_id: int, base_address: int) -> int:
        return base_address + self.payload_offsets[object_id]

    def code_address(self, code_id: int, base_address: int) -> int:
        return base_address + self.code_entry_offsets[code_id]


def extract_graph_bytes(data: bytes) -> bytes:
    """Extract the RXF extent from an RXF_IMAGE segment, allowing page padding.

    This deliberately does not search for magic inside an arbitrary executable:
    the platform packager is responsible for locating its RXF_IMAGE segment.
    """
    header = BinaryHeader.from_wire(data)
    if header.magic != MAGIC:
        raise ValueError("RXF_IMAGE segment must start with RXF magic")
    if not header.header_capacity <= header.image_size <= len(data):
        raise ValueError("RXF_IMAGE extent is outside segment bytes")
    return data[: header.image_size]


def recover_graph(data: bytes) -> Container:
    """Recover the complete ordinary RXF graph from RXF_IMAGE segment bytes."""
    return layout_to_container(unpack_layout(extract_graph_bytes(data)))


def inspect_graph_image(data: bytes) -> GraphImage:
    """Validate an embedded image and expose payload and native-entry offsets."""
    data = extract_graph_bytes(data)
    layout = unpack_layout(data)
    payloads = {
        cell.header.id: layout.header.heap_off + cell.offset + CELL_HEADER_SIZE
        for cell in layout.heap.cells
    }
    codes, entries = {}, {}
    nodes = {node.id: node for node in layout_to_container(layout).nodes}
    for node in nodes.values():
        if node.type_id == CODE_TYPE:
            code = decode_code(node)
            codes[node.id] = payloads[node.id] + CODE_HEADER_SIZE
            entries[node.id] = codes[node.id] + code.entry_offset
    return GraphImage(data, payloads, codes, entries)


def encode_graph(container: Container) -> GraphImage:
    """Serialize every object, including empty/non-retained objects, losslessly.

    The ordinary compact-save bridge intentionally discards transient objects
    and normalizes states. Executables instead use the same wire models with a
    cell for *every* object and unchanged object state. Building the complete
    cell list once also avoids quadratic incremental heap allocation.
    """
    if len({node.id for node in container.nodes}) != len(container.nodes):
        raise ValueError("duplicate object IDs in executable graph")
    nodes, cells = [], []
    frontier = 0
    for source in sorted(container.nodes, key=lambda node: node.id):
        nodes.append(
            NodeEntry(
                id=source.id,
                name=source.name,
                kind=NodeKind[source.kind.name],
                parent=source.parent,
                type_id=source.type_id,
                generation=source.generation,
                owner=source.owner,
                owner_kind=source.owner_kind,
                state=source.state,
                size=len(source.data),
                attrs=dict(source.attrs),
                refs=[
                    RefEntry(
                        target=r.target,
                        to_off=r.to_off,
                        kind=int(r.kind),
                        binding=int(r.binding),
                    )
                    for r in source.refs
                ],
                sections=[
                    SectionSpan(
                        node_id=source.id,
                        name=s.name,
                        offset=s.off,
                        size=s.size,
                        map=SectionMap(int(s.map)),
                        perm=SectionPerm(int(s.perm)),
                    )
                    for s in source.sections
                ],
            )
        )
        offset = (frontier + container.heap.align - 1) & -container.heap.align
        cell = Cell(
            offset=offset,
            align=container.heap.align,
            header=CellHeader(
                id=source.id,
                type_id=source.type_id,
                generation=source.generation,
                parent=source.parent,
                payload_size=len(source.data),
                flags=source.state.pack(),
            ),
            payload=source.data,
        )
        cells.append(cell)
        frontier = cell.end
    offsets = {cell.header.id: cell.offset for cell in cells}
    types = TypeTable.from_container(container)
    code_table = []
    for node in container.nodes:
        if node.type_id == CODE_TYPE:
            code = decode_code(node)
            code_table.append(
                CodeLocation(
                    function_id=code.owner_function,
                    target_id=code.target_id,
                    code_id=node.id,
                    cell_offset=offsets[node.id],
                )
            )
    layout = BinaryLayout(
        header=BinaryHeader(
            entry_node=container.header.entry_node, flags=container.header.flags
        ),
        nodes=nodes,
        heap=HeapImage(
            committed_size=max(container.heap.committed_size, frontier),
            limit=container.heap.limit,
            align=container.heap.align,
            page_size=container.heap.page_size,
            frontier=frontier,
            cells=cells,
        ),
        type_table=[
            TypeLocation(type_id=tid, cell_offset=offsets[tid])
            for tid in sorted(types.types)
        ],
        code_table=code_table,
    )
    data = pack_layout(layout)
    header = BinaryHeader.from_wire(data)
    payloads = {
        cell.header.id: header.heap_off + cell.offset + CELL_HEADER_SIZE
        for cell in cells
    }
    codes, entries = {}, {}
    for node in container.nodes:
        if node.type_id == CODE_TYPE:
            code = decode_code(node)
            codes[node.id] = payloads[node.id] + CODE_HEADER_SIZE
            entries[node.id] = codes[node.id] + code.entry_offset
    return GraphImage(data, payloads, codes, entries)
