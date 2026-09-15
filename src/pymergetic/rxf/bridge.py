"""Copy between the semantic RXF v5 graph and compact global heap layout."""

from pymergetic.rxf.execution.decode import decode_code
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.container import Header as ContainerHeader
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.state import Disposition
from pymergetic.rxf.model.storage import HeapDef, SectionDef
from pymergetic.rxf.output.binary import BinaryLayout
from pymergetic.rxf.output.cell import CELL_HEADER_SIZE, HeapImage
from pymergetic.rxf.output.code_table import CodeLocation
from pymergetic.rxf.output.header import (
    FORMAT_VERSION,
    HEADER_CAPACITY,
    BinaryHeader,
)
from pymergetic.rxf.output.node import NodeEntry, NodeKind, RefEntry
from pymergetic.rxf.output.region import SectionMap, SectionPerm, SectionSpan
from pymergetic.rxf.output.type_table import TypeLocation
from pymergetic.rxf.schema import NodeKind as SchemaNodeKind
from pymergetic.rxf.schema import RefBinding as SchemaRefBinding
from pymergetic.rxf.schema import RefKind as SchemaRefKind
from pymergetic.rxf.schema import SectionMap as SchemaSectionMap
from pymergetic.rxf.schema import SectionPerm as SchemaSectionPerm
from pymergetic.rxf.ty.align import align_up
from pymergetic.rxf.ty.builtins import CODE_TYPE
from pymergetic.rxf.ty.table import TypeTable

_KIND_MAP = {kind: NodeKind[kind.name] for kind in SchemaNodeKind}
_KIND_REVERSE = {wire: semantic for semantic, wire in _KIND_MAP.items()}


def _required_heap_bytes(nodes: list[NodeDef], align: int) -> int:
    frontier = 0
    for node in nodes:
        frontier = align_up(frontier, align) + CELL_HEADER_SIZE + len(node.data)
    return frontier


def container_to_layout(container: Container) -> BinaryLayout:
    """Build a non-mutating compact image, omitting transient/tombstone objects."""
    kept = [
        node for node in container.nodes if node.state.disposition == Disposition.RETAIN
    ]
    kept_ids = {node.id for node in kept}
    for node in kept:
        for reference in node.refs:
            if (
                reference.binding == SchemaRefBinding.MANDATORY
                and reference.target not in kept_ids
            ):
                raise ValueError(
                    f"node {node.id}: mandatory ref targets excluded object "
                    f"{reference.target}"
                )

    emitted_states = {node.id: node.state.emitted() for node in kept}
    payload_nodes = sorted(
        (node for node in kept if node.data), key=lambda node: node.id
    )
    required_heap = _required_heap_bytes(payload_nodes, container.heap.align)

    # The exact image size is known only after metadata tables are packed. Reserve a
    # conservative metadata floor now; pack_layout computes the exact file extent.
    metadata_floor = HEADER_CAPACITY + len(kept) * 120
    minimum_commitment = metadata_floor + required_heap
    committed_size = max(container.heap.committed_size, minimum_commitment)
    if container.heap.limit is not None and committed_size > container.heap.limit:
        raise ValueError("compacted image exceeds known heap limit")

    heap = HeapImage(
        image_size=0,
        committed_size=committed_size,
        limit=container.heap.limit,
        align=container.heap.align,
        page_size=container.heap.page_size,
    )
    nodes: list[NodeEntry] = []
    for node in sorted(kept, key=lambda item: item.id):
        sections = [
            SectionSpan(
                node_id=node.id,
                name=section.name,
                offset=section.off,
                size=section.size,
                map=SectionMap(section.map.value),
                perm=SectionPerm(section.perm.value),
            )
            for section in node.sections
        ]
        references = [
            RefEntry(
                target=reference.target,
                to_off=reference.to_off,
                kind=reference.kind.value,
                binding=reference.binding.value,
            )
            for reference in node.refs
            if reference.target in kept_ids
            or reference.binding == SchemaRefBinding.OPTIONAL
        ]
        nodes.append(
            NodeEntry(
                id=node.id,
                name=node.name,
                kind=_KIND_MAP[node.kind],
                parent=node.parent,
                type_id=node.type_id,
                generation=node.generation,
                owner=node.owner,
                owner_kind=node.owner_kind,
                state=emitted_states[node.id],
                sections=sections,
                refs=references,
                attrs=dict(node.attrs),
                size=len(node.data),
            )
        )

    for node in payload_nodes:
        heap.allocate(
            node_id=node.id,
            type_id=node.type_id,
            payload=node.data,
            parent=node.parent,
            generation=node.generation,
            state=emitted_states[node.id],
        )

    offsets = {cell.header.id: cell.offset for cell in heap.cells}
    semantic_types = TypeTable.from_container(
        Container(header=container.header, heap=container.heap, nodes=kept)
    )
    type_table = [
        TypeLocation(type_id=type_id, cell_offset=offsets[type_id])
        for type_id in sorted(semantic_types.types)
    ]
    code_table: list[CodeLocation] = []
    for node in sorted(kept, key=lambda item: item.id):
        if node.type_id != CODE_TYPE:
            continue
        code = decode_code(node)
        code_table.append(
            CodeLocation(
                function_id=code.owner_function,
                target_id=code.target_id,
                code_id=node.id,
                cell_offset=offsets[node.id],
            )
        )
    return BinaryLayout(
        header=BinaryHeader(
            version=FORMAT_VERSION,
            entry_node=container.header.entry_node,
            flags=container.header.flags,
        ),
        nodes=nodes,
        type_table=type_table,
        code_table=code_table,
        heap=heap,
    )


def layout_to_container(layout: BinaryLayout) -> Container:
    payloads = {cell.header.id: cell.payload for cell in layout.heap.cells}
    nodes: list[NodeDef] = []
    for node in layout.nodes:
        sections = [
            SectionDef(
                name=section.name,
                off=section.offset,
                size=section.size,
                map=SchemaSectionMap(section.map.value),
                perm=SchemaSectionPerm(section.perm.value),
            )
            for section in node.sections
        ]
        references = [
            RefDef(
                source=node.id,
                target=reference.target,
                kind=SchemaRefKind(reference.kind),
                binding=SchemaRefBinding(reference.binding),
                to_off=reference.to_off,
            )
            for reference in node.refs
        ]
        nodes.append(
            NodeDef(
                id=node.id,
                name=node.name,
                kind=_KIND_REVERSE[node.kind],
                parent=node.parent,
                type_id=node.type_id,
                generation=node.generation,
                owner=node.owner,
                owner_kind=node.owner_kind,
                state=node.state,
                sections=sections,
                refs=references,
                attrs=dict(node.attrs),
                data=payloads.get(node.id, b""),
            )
        )

    header = layout.header
    return Container(
        header=ContainerHeader(
            version=header.version,
            node_count=header.node_count,
            node_table_off=header.node_table_off,
            string_table_off=header.string_table_off,
            string_table_size=header.string_table_size,
            entry_node=header.entry_node,
            flags=header.flags,
        ),
        heap=HeapDef(
            image_size=layout.heap.image_size,
            committed_size=layout.heap.committed_size,
            limit=layout.heap.limit,
            frontier=layout.heap.frontier,
            align=layout.heap.align,
            page_size=layout.heap.page_size,
        ),
        nodes=nodes,
    )
