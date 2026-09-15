"""RXF v5 one-heap roundtrip and compaction invariants."""

import json
import struct
from pathlib import Path
from typing import cast

import pytest

from pymergetic.rxf.bridge import container_to_layout, layout_to_container
from pymergetic.rxf.canon import canon
from pymergetic.rxf.checker import check
from pymergetic.rxf.expand import COUNTER, COUNTER_STATE_ID
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.state import (
    Cleanliness,
    Disposition,
    Mobility,
    ObjectState,
    Provenance,
)
from pymergetic.rxf.output.cell import CELL_HEADER_SIZE, CellHeader, HeapImage
from pymergetic.rxf.output.code_table import (
    CODE_TABLE_ENTRY_SIZE,
    CodeLocation,
)
from pymergetic.rxf.output.engine import pack_layout, unpack_layout
from pymergetic.rxf.output.header import (
    FORMAT_VERSION,
    HEADER_CAPACITY,
    HEADER_SIZE,
    BinaryHeader,
)
from pymergetic.rxf.output.type_table import (
    TYPE_TABLE_ENTRY_SIZE,
    TypeLocation,
)
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.table import TypeTable


def _load() -> dict:
    return json.loads(Path(__file__).with_name("counter.json").read_text())


def test_counter_roundtrip() -> None:
    first = Container.from_dict(canon(_load()))
    assert check(first) == []
    blob = pack_layout(container_to_layout(first))
    second = layout_to_container(unpack_layout(blob))
    assert check(second) == []
    assert blob == pack_layout(container_to_layout(second))


def test_v5_one_heap_and_reserved_kind() -> None:
    layout = container_to_layout(COUNTER.build())
    assert layout.header.version == 5
    assert not hasattr(layout, "heaps") and not hasattr(layout, "regions")
    assert NodeKind.RESERVED_9.value == 9
    assert layout.header.version == FORMAT_VERSION
    assert CELL_HEADER_SIZE == CellHeader.wire_size() == 48
    assert TYPE_TABLE_ENTRY_SIZE == TypeLocation.wire_size() == 16
    assert CODE_TABLE_ENTRY_SIZE == CodeLocation.wire_size() == 32


def test_v4_header_size_capacity_and_zero_reserve() -> None:
    header = BinaryHeader()
    assert header.header_size == HEADER_SIZE == BinaryHeader.wire_size()
    assert header.header_capacity == HEADER_CAPACITY == 256
    blob = pack_layout(container_to_layout(COUNTER.build()))
    restored = unpack_layout(blob)
    assert restored.header.header_size == HEADER_SIZE
    assert restored.header.header_capacity == HEADER_CAPACITY
    assert restored.header.node_table_off == HEADER_CAPACITY
    assert blob[HEADER_SIZE:HEADER_CAPACITY] == bytes(HEADER_CAPACITY - HEADER_SIZE)


@pytest.mark.parametrize(
    ("field_offset", "value", "message"),
    (
        (8, HEADER_SIZE + 8, "header_size disagrees"),
        (12, HEADER_SIZE - 8, "header_capacity is smaller"),
        (12, HEADER_CAPACITY + 1, "not header-aligned"),
        (12, 1 << 31, "reserved header prefix is outside"),
    ),
)
def test_reader_rejects_invalid_header_geometry(
    field_offset: int, value: int, message: str
) -> None:
    blob = bytearray(pack_layout(container_to_layout(COUNTER.build())))
    struct.pack_into("<I", blob, field_offset, value)
    with pytest.raises(ValueError, match=message):
        unpack_layout(bytes(blob))


def test_header_model_rejects_invalid_current_schema_values() -> None:
    with pytest.raises(ValueError, match="header_size"):
        BinaryHeader(header_size=HEADER_SIZE + 8)
    with pytest.raises(ValueError, match="smaller"):
        BinaryHeader(header_capacity=HEADER_SIZE - 8)
    with pytest.raises(ValueError, match="aligned"):
        BinaryHeader(header_capacity=HEADER_CAPACITY + 1)


def test_reader_rejects_nonzero_header_reserve() -> None:
    blob = bytearray(pack_layout(container_to_layout(COUNTER.build())))
    blob[HEADER_SIZE] = 1
    with pytest.raises(ValueError, match="reserved header bytes must be zero"):
        unpack_layout(bytes(blob))


def test_type_table_uses_global_offsets() -> None:
    container = COUNTER.build()
    layout = container_to_layout(container)
    assert [entry.type_id for entry in layout.type_table] == sorted(
        TypeTable.from_container(container).types
    )
    assert all(not hasattr(entry, "domain_node") for entry in layout.type_table)


def test_copying_compaction_is_non_mutating_and_filters() -> None:
    container = COUNTER.build()
    source = cast(NodeDef, container.node_by_id(COUNTER_STATE_ID))
    source.state = ObjectState(
        Provenance.RUNTIME, Disposition.RETAIN, Cleanliness.DIRTY, Mobility.PINNED
    )
    referenced = {
        reference.target for node in container.nodes for reference in node.refs
    }
    extra = next(
        node
        for node in reversed(container.nodes)
        if node.id not in referenced and node.id != source.id
    )
    extra.state = ObjectState(disposition=Disposition.TRANSIENT)
    before = source.state
    layout = container_to_layout(container)
    assert source.state == before
    assert extra.id not in {node.id for node in layout.nodes}
    emitted = next(node for node in layout.nodes if node.id == source.id)
    assert emitted.state == ObjectState(
        Provenance.IMAGE, Disposition.RETAIN, Cleanliness.CLEAN, Mobility.PINNED
    )


def test_mandatory_ref_to_excluded_rejected() -> None:
    container = COUNTER.build()
    root = cast(NodeDef, container.node_by_id(0))
    target = cast(NodeDef, container.node_by_id(root.refs[0].target))
    target.state = ObjectState(disposition=Disposition.TOMBSTONE)
    with pytest.raises(ValueError, match="mandatory ref targets excluded"):
        container_to_layout(container)


def test_heap_unknown_limit_does_not_extend_commit() -> None:
    heap = HeapImage(image_size=0, committed_size=CELL_HEADER_SIZE + 3, limit=None)
    with pytest.raises(MemoryError, match="committed boundary"):
        heap.allocate(node_id=1, type_id=1, payload=b"abcd")


def test_reader_rejects_cell_node_disagreement() -> None:
    blob = bytearray(Path(__file__).with_name("counter.rxf").read_bytes())
    layout = unpack_layout(bytes(blob))
    cell = layout.heap.cells[0]
    struct.pack_into("<Q", blob, layout.header.heap_off + cell.offset + 8, 0xFFFF)
    with pytest.raises(ValueError, match="disagrees"):
        unpack_layout(bytes(blob))


def test_heap_bounds_require_image_within_commitment_and_known_limit() -> None:
    with pytest.raises(ValueError, match="image_size exceeds committed_size"):
        HeapImage(image_size=65, committed_size=64)
    with pytest.raises(ValueError, match="committed_size exceeds known heap limit"):
        HeapImage(image_size=64, committed_size=65, limit=64)


def test_packed_image_size_is_exact_and_pack_does_not_mutate_layout() -> None:
    layout = container_to_layout(COUNTER.build())
    original_header = layout.header.model_copy(deep=True)
    original_heap = layout.heap.model_copy(deep=True)
    blob = pack_layout(layout)
    restored = unpack_layout(blob)
    assert restored.header.image_size == len(blob)
    assert restored.heap.image_size == len(blob)
    assert restored.heap.image_size <= restored.heap.committed_size
    assert layout.header == original_header
    assert layout.heap == original_heap


def test_pack_rejects_actual_image_beyond_known_limit() -> None:
    layout = container_to_layout(COUNTER.build())
    layout.heap.limit = layout.heap.committed_size
    with pytest.raises(ValueError, match="committed_size exceeds known heap limit"):
        pack_layout(layout)


def test_reader_rejects_invalid_state_flags() -> None:
    blob = bytearray(Path(__file__).with_name("counter.rxf").read_bytes())
    layout = unpack_layout(bytes(blob))
    struct.pack_into("<I", blob, layout.header.node_table_off + 20, 1 << 31)
    with pytest.raises(ValueError, match="unknown flags"):
        unpack_layout(bytes(blob))


def test_reader_rejects_duplicate_cell_ids() -> None:
    blob = bytearray(Path(__file__).with_name("counter.rxf").read_bytes())
    layout = unpack_layout(bytes(blob))
    first, second = layout.heap.cells[:2]
    struct.pack_into(
        "<Q",
        blob,
        layout.header.heap_off + second.offset,
        first.header.id,
    )
    with pytest.raises(ValueError, match="duplicate cell id"):
        unpack_layout(bytes(blob))


def test_reader_validates_code_table_global_offset() -> None:
    from pymergetic.rxf.model.execution import (
        CodeFormat,
        CodeObject,
        Effect,
        Endianness,
        FunctionImplementation,
        FunctionLayer,
        FunctionObject,
        SignatureObject,
        TargetObject,
        semantic_digest,
    )
    from pymergetic.rxf.ty.builtins import U32_TYPE

    high = 1 << 40
    container = COUNTER.build()
    container.nodes.extend(
        [
            SignatureObject(high + 1, "high_signature", high + 2, U32_TYPE).to_node(),
            FunctionObject(
                high + 2,
                "high_function",
                0,
                high + 1,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.FOUNDATION,
                implementation_ids=(high + 4,),
            ).to_node(),
            TargetObject(
                high + 3,
                "high_target",
                0,
                1,
                1,
                1,
                64,
                Endianness.LITTLE,
            ).to_node(),
            CodeObject(
                high + 4,
                "high_code",
                high + 2,
                high + 2,
                high + 3,
                CodeFormat.NATIVE,
                b"\xc3",
                high + 1,
                Effect.NONE,
                1,
                semantic_digest("high_function", high + 1, Effect.NONE, 1),
            ).to_node(),
        ]
    )
    blob = bytearray(pack_layout(container_to_layout(container)))
    layout = unpack_layout(bytes(blob))
    high_code = next(entry for entry in layout.code_table if entry.code_id == high + 4)
    assert high_code.function_id == high + 2
    assert high_code.target_id == high + 3
    assert high_code.cell_offset == next(
        cell.offset for cell in layout.heap.cells if cell.header.id == high + 4
    )
    entry_index = layout.code_table.index(high_code)
    struct.pack_into(
        "<Q",
        blob,
        layout.header.code_table_off + entry_index * 32 + 24,
        high_code.cell_offset + 8,
    )
    with pytest.raises(ValueError, match="code-table entry.*invalid cell location"):
        unpack_layout(bytes(blob))


def test_checker_enforces_one_canonical_reachable_parent_tree() -> None:
    from copy import deepcopy

    from pymergetic.rxf.schema import NODE_INVALID

    base = COUNTER.build()
    root = base.node_by_id(0)
    assert root is not None

    duplicate = deepcopy(base)
    duplicate.nodes[1].kind = NodeKind.ROOT
    assert any("exactly one ROOT" in error for error in check(duplicate))

    wrong_id = deepcopy(base)
    wrong_root = wrong_id.node_by_id(0)
    assert wrong_root is not None
    wrong_root.id = 1 << 54
    assert any("canonical id 0" in error for error in check(wrong_id))

    wrong_parent = deepcopy(base)
    wrong_root = wrong_parent.node_by_id(0)
    assert wrong_root is not None
    wrong_root.parent = 1
    assert any("parent must be NODE_INVALID" in error for error in check(wrong_parent))

    missing = deepcopy(base)
    missing.nodes[-1].parent = 1 << 63
    assert any(
        "parent 9223372036854775808 is missing" in error for error in check(missing)
    )

    self_parent = deepcopy(base)
    self_parent.nodes[-1].parent = self_parent.nodes[-1].id
    assert any("own parent" in error for error in check(self_parent))

    cycle = deepcopy(base)
    first, second = cycle.nodes[-2:]
    first.parent = second.id
    second.parent = first.id
    cycle_errors = check(cycle)
    assert any("not reachable from ROOT" in error for error in cycle_errors)

    disconnected = deepcopy(base)
    disconnected.nodes[-1].parent = NODE_INVALID
    assert any(
        "non-root parent is NODE_INVALID" in error for error in check(disconnected)
    )


def test_module_type_and_derived_fqns() -> None:
    from pymergetic.rxf.model.module import derived_fqns
    from pymergetic.rxf.ty.builtins import MODULE_TYPE, U64_TYPE

    container = COUNTER.build()
    fqns = derived_fqns(container.nodes)
    assert fqns[U64_TYPE] == "pymergetic.rxf.primitives.uint64_t"
    assert fqns[MODULE_TYPE] == "pymergetic.rxf.model.Module"
    module_type = container.node_by_id(MODULE_TYPE)
    assert module_type is not None and module_type.kind == NodeKind.TYPE
    assert all("fqn" not in node.attrs for node in container.nodes)


def test_module_validation() -> None:
    from copy import deepcopy

    from pymergetic.rxf.ty.builtins import PYMERGETIC_MODULE_ID

    container = deepcopy(COUNTER.build())
    module = container.node_by_id(PYMERGETIC_MODULE_ID)
    assert module is not None
    module.type_id = 1
    assert any("must be typed by Module" in error for error in check(container))


def test_reader_rejects_reference_slice_past_table():
    blob = bytearray(Path(__file__).with_name("counter.rxf").read_bytes())
    layout = unpack_layout(bytes(blob))
    # First node ref_first/count words are at 72/80 within the 120-byte entry.
    struct.pack_into(
        "<QQ", blob, layout.header.node_table_off + 64, layout.header.ref_table_count, 2
    )
    with pytest.raises(ValueError, match="reference span"):
        unpack_layout(bytes(blob))


def test_reader_rejects_section_slice_past_table():
    blob = bytearray(Path(__file__).with_name("counter.rxf").read_bytes())
    layout = unpack_layout(bytes(blob))
    struct.pack_into(
        "<QQ",
        blob,
        layout.header.node_table_off + 80,
        layout.header.section_table_count,
        2,
    )
    with pytest.raises(ValueError, match="section span"):
        unpack_layout(bytes(blob))


def test_v4_legacy_inspection_translation_preserves_version() -> None:
    blob = bytearray(pack_layout(container_to_layout(COUNTER.build())))
    struct.pack_into("<I", blob, 4, 4)
    layout = unpack_layout(bytes(blob))
    assert layout.header.version == 4
    translated = layout_to_container(layout)
    assert translated.header.version == 4
