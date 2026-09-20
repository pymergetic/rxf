"""Structural packing regressions: linear assembly and class-only metadata."""

import enum
import json
import struct
from pathlib import Path
from time import perf_counter
from typing import Annotated

import pytest
from pydantic import Field
from pydantic.fields import FieldInfo

from pymergetic.rxf.bridge import container_to_layout
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.output import base
from pymergetic.rxf.output.base import Struct
from pymergetic.rxf.output.cell import CELL_HEADER_SIZE, HeapImage
from pymergetic.rxf.output.engine import pack_layout, unpack_layout
from pymergetic.rxf.output.types import int16_t, uint32_t, uint64_t


ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("alignment", (1, 8, 64))
def test_batch_cells_match_incremental_allocation(monkeypatch, alignment) -> None:
    container = Container.from_dict(
        json.loads((ROOT / "tests/counter.json").read_text())
    )
    container.heap.align = alignment
    container.nodes.reverse()  # Input order must not affect compact heap order.
    before = container.to_dict()

    def no_incremental_allocation(*args, **kwargs):
        pytest.fail("compact images must assemble cells once, not allocate per node")

    with monkeypatch.context() as context:
        context.setattr(HeapImage, "allocate", no_incremental_allocation)
        layout = container_to_layout(container)

    reference = HeapImage(
        committed_size=layout.heap.committed_size,
        limit=layout.heap.limit,
        align=alignment,
        page_size=layout.heap.page_size,
    )
    for node in sorted(container.nodes, key=lambda item: item.id):
        if node.data:
            reference.allocate(
                node_id=node.id,
                type_id=node.type_id,
                payload=node.data,
                parent=node.parent,
                generation=node.generation,
                state=node.state.emitted(),
            )
    assert layout.heap == reference
    expected = pack_layout(layout.model_copy(update={"heap": reference}))
    assert pack_layout(layout) == expected
    assert container.to_dict() == before

    # Neither cells nor offsets are cached for a graph's identity.
    source = next(node for node in container.nodes if node.data)
    source.data += b"\x00"
    updated = container_to_layout(container)
    assert next(
        cell.payload for cell in updated.heap.cells if cell.header.id == source.id
    ) == source.data
    assert pack_layout(updated) != expected


def test_batch_cells_reject_duplicate_payload_ids() -> None:
    container = Container.from_dict(
        json.loads((ROOT / "tests/counter.json").read_text())
    )
    source = next(node for node in container.nodes if node.data)
    container.nodes.append(source)
    with pytest.raises(ValueError, match=f"node {source.id} is already allocated"):
        container_to_layout(container)


def test_allocate_still_checks_mutated_public_cells() -> None:
    heap = HeapImage(committed_size=4 * CELL_HEADER_SIZE)
    first = heap.allocate(node_id=1, type_id=1, payload=b"")
    second = heap.allocate(node_id=2, type_id=1, payload=b"")
    heap.cells.reverse()
    second.header.id = 3
    with pytest.raises(ValueError, match="node 3 is already allocated"):
        heap.allocate(node_id=3, type_id=1, payload=b"")
    third = heap.allocate(node_id=2, type_id=1, payload=b"")
    assert heap.cells == [first, second, third]


class WireFlag(enum.IntFlag):
    SET = 1


def test_struct_metadata_is_cached_per_class_and_rebuilt(monkeypatch) -> None:
    class Wire(Struct):
        signed: Annotated[int, int16_t]
        flags: WireFlag
        tag: bytes = Field(max_length=2)

    class Child(Wire):
        extra: Annotated[int, uint64_t]

    calls = []
    resolve = base.get_type_hints

    def counted(cls, **kwargs):
        calls.append(cls)
        return resolve(cls, **kwargs)

    monkeypatch.setattr(base, "get_type_hints", counted)
    value = Wire(signed=-4, flags=WireFlag.SET, tag=b"xy")
    child = Child(signed=-4, flags=WireFlag.SET, tag=b"xy", extra=17)
    expected = struct.pack("<hI2s", -4, 1, b"xy")
    for _ in range(4):
        assert value.to_wire() == expected
        assert Wire.from_wire(expected) == value
        assert Wire.body_size() == Wire.wire_size() == 8
        assert Wire.padding_size() == 0
        assert child.to_wire() == expected + struct.pack("<Q", 17)
        assert Child.from_wire(child.to_wire()) == child
    assert calls == [Wire, Child]
    widths = Wire._field_widths()
    widths["signed"] = 100
    assert Wire._field_widths()["signed"] == 2
    assert value.to_wire() == expected

    # A schema rebuild invalidates cached field metadata, including fixed bytes.
    Wire.model_fields["tag"] = FieldInfo(annotation=bytes, max_length=3)
    Wire.model_rebuild(force=True)
    rebuilt = Wire(signed=-4, flags=WireFlag.SET, tag=b"xyz")
    assert rebuilt.to_wire() == struct.pack("<hI3s7x", -4, 1, b"xyz")
    assert Wire.from_wire(rebuilt.to_wire()) == rebuilt
    assert calls == [Wire, Child, Wire]
    assert child.to_wire() == expected + struct.pack("<Q", 17)
    Wire.__pad_after__ = False
    assert rebuilt.to_wire() == struct.pack("<hI3s", -4, 1, b"xyz")
    assert Wire.wire_size() == 9
    assert calls == [Wire, Child, Wire]


def test_struct_metadata_does_not_hide_validation() -> None:
    class Wire(Struct):
        value: Annotated[int, uint32_t]

    value = Wire(value=1)
    value.to_wire()  # Warm class metadata before exercising invalid inputs.
    with pytest.raises(ValueError):
        Wire(value=1 << 32)
    with pytest.raises(ValueError, match="out of bounds"):
        Wire.from_wire(bytes(8), -1)
    with pytest.raises(ValueError, match="out of bounds"):
        Wire.from_wire(bytes(7))
    value.value = 1 << 32
    with pytest.raises(struct.error):
        value.to_wire()

    class MissingWidth(Struct):
        value: bytes

    class Unsupported(Struct):
        value: str

    for _ in range(2):
        with pytest.raises(TypeError, match="needs max_length"):
            MissingWidth.wire_size()
        with pytest.raises(TypeError, match="unsupported type"):
            Unsupported.wire_size()


def test_complete_starter_packing(monkeypatch) -> None:
    container = Container.from_dict(
        json.loads((ROOT / "examples/starter.json").read_text())
    )

    def no_incremental_allocation(*args, **kwargs):
        pytest.fail("complete images must not scan/sort per cell")

    monkeypatch.setattr(HeapImage, "allocate", no_incremental_allocation)
    started = perf_counter()
    layout = container_to_layout(container)
    assembled = perf_counter()
    blob = pack_layout(layout)
    packed = perf_counter()
    assert len(layout.nodes) == len(container.nodes)
    assert blob == (ROOT / "examples/starter.rxf").read_bytes()
    restored = unpack_layout(blob)
    assert pack_layout(restored) == blob
    print(
        f"\ncomplete starter: {len(container.nodes)} nodes, "
        f"{len(layout.heap.cells)} cells, {len(blob)} bytes; "
        f"container_to_layout={assembled - started:.3f}s, "
        f"pack_layout={packed - assembled:.3f}s, total={packed - started:.3f}s"
    )
