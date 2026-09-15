"""Annotated RXF v3 dump."""

from pymergetic.rxf.output.cell import CELL_HEADER_SIZE
from pymergetic.rxf.output.engine import NODE_ENTRY_SIZE, unpack_layout
from pymergetic.rxf.output.header import HEADER_SIZE


def dump(data: bytes) -> str:
    layout = unpack_layout(data)
    h = layout.header
    lines = [
        f"=== RXF v{h.version} ({len(data)} bytes) ===",
        f"HEADER {HEADER_SIZE}B",
        f"nodes={h.node_count} node_table=0x{h.node_table_off:x}",
        f"GLOBAL HEAP file=0x{h.heap_off:x} image={h.image_size} committed={h.committed_size} frontier={h.frontier} limit={'unknown' if layout.heap.limit is None else layout.heap.limit}",
        f"NODE TABLE {len(layout.nodes)} x {NODE_ENTRY_SIZE}B",
    ]
    lines.extend(
        f"node[{n.id}] {n.kind.name} '{n.name}' parent={n.parent} type={n.type_id} owner={n.owner_kind.name} state={n.state.to_dict()}"
        for n in layout.nodes
    )
    lines.append(
        f"CELL STREAM {len(layout.heap.cells)} cells x {CELL_HEADER_SIZE}B headers"
    )
    lines.extend(
        f"0x{c.offset:x}: cell[{c.header.id}] type={c.header.type_id} gen={c.header.generation} payload={c.header.payload_size} flags=0x{c.header.flags:x}"
        for c in layout.heap.cells
    )
    return "\n".join(lines)
