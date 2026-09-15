"""Global heap analysis and ownership/lifecycle relations."""

from pymergetic.rxf.bridge import container_to_layout
from pymergetic.rxf.model.container import Container


def cell_map(container: Container, cols: int = 64) -> str:
    layout = container_to_layout(container)
    heap = layout.heap
    lines = [
        f"GLOBAL HEAP image={heap.image_size}B committed={heap.committed_size}B frontier={heap.frontier}B limit={'unknown' if heap.limit is None else heap.limit}"
    ]
    chars = ["."] * heap.committed_size
    for cell in heap.cells:
        marker = hex(cell.header.id % 16)[2:]
        for i in range(cell.offset, min(cell.end, len(chars))):
            chars[i] = marker
    for start in range(0, len(chars), cols):
        lines.append(f"{start:08x}  {''.join(chars[start : start + cols])}")
    return "\n".join(lines)


def relations(container: Container) -> str:
    lines = []
    for node in container.nodes:
        parts = [
            f"[{node.id}] {node.kind.name} '{node.name}'",
            f"owner={node.owner_kind.name}",
            "/".join(
                x.name
                for x in (
                    node.state.provenance,
                    node.state.disposition,
                    node.state.cleanliness,
                    node.state.mobility,
                )
            ),
        ]
        if node.parent != 0xFFFF_FFFF_FFFF_FFFF:
            parts.append(f"parent=node[{node.parent}]")
        parts.extend(f"-> node[{r.target}] ({r.kind.name})" for r in node.refs)
        parts.extend(
            f"section:'{s.name}' global_off={s.off} size={s.size}"
            for s in node.sections
        )
        lines.append("  ".join(parts))
    return "\n".join(lines)
