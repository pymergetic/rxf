"""Stage 4-8 semantic corpus and foundation leaf proofs."""

import ctypes
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.bridge import container_to_layout, layout_to_container
from pymergetic.rxf.checker import check
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.model.stdlib import (
    BoundedView,
    HeapSlot,
    MovePlanEntry,
    ObjectHandle,
    StdlibRecord,
    aligned_frontier,
    specialize_value_type,
)
from pymergetic.rxf.model.stdlib_corpus import STDLIB_RECORD_TYPE, stdlib_inventory
from pymergetic.rxf.output.engine import pack_layout, unpack_layout
from pymergetic.rxf.stdlib_reference import (
    CanonicalMap,
    GenerationVector,
    checked_utf8_slice,
    stable_sort,
    utf8_scalar_boundaries,
)
from pymergetic.rxf.stdlib_runtime import (
    GlobalHeapDirectory,
    HeapOperationRefused,
    HeapRefusal,
)
from pymergetic.rxf.ty.objects import FieldObject
from pymergetic.rxf.ty.table import TypeTable

ROOT = Path(__file__).parents[1]


def test_stdlib_starter_inventory_checker_and_roundtrip():
    first = starter_template().build()
    assert check(first) == []
    inventory = stdlib_inventory(first.nodes)
    assert inventory == {
        "types": 23,
        "concrete_specializations": 7,
        "functions": 97,
        "templates": 12,
        "traits": 9,
        "conformances": 9,
        "semantic_records": 85,
        "native_functions": 30,
        "composed_functions": 55,
        "abstract_functions": 12,
        "declared_functions": 0,
        "api_functions": 85,
        "api_declared": 0,
        "native_code": 60,
    }
    for node in first.nodes:
        if node.type_id == STDLIB_RECORD_TYPE:
            assert (
                StdlibRecord.from_node(node).to_node(STDLIB_RECORD_TYPE).data
                == node.data
            )
    blob = pack_layout(container_to_layout(first))
    second = layout_to_container(unpack_layout(blob))
    assert check(second) == []
    assert blob == pack_layout(container_to_layout(second))


def test_movable_heap_interfaces_reject_bad_geometry_and_moves():
    assert aligned_frontier(3, 5, 8, 16) == (8, 13)
    with pytest.raises(MemoryError):
        aligned_frontier(9, 8, 8, 16)
    HeapSlot(7, 8, 8, 8).validate(16)
    with pytest.raises(ValueError, match="misaligned"):
        HeapSlot(7, 3, 2, 2).validate(16)
    MovePlanEntry(7, 0, 8, 4).validate(16, set())
    with pytest.raises(ValueError, match="cannot move"):
        MovePlanEntry(7, 0, 8, 4).validate(16, {7})


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    out = tmp_path_factory.mktemp("stdlib-native") / "stdlib.so"
    subprocess.run(
        [
            "clang-18",
            "-std=c11",
            "-O2",
            "-shared",
            "-fPIC",
            str(ROOT / "native/stdlib_memory.c"),
            "-o",
            str(out),
        ],
        check=True,
    )
    return ctypes.CDLL(str(out))


def test_native_memory_bounds_overlap_endian_and_output_unchanged(native):
    u8p = ctypes.POINTER(ctypes.c_uint8)
    native.rxf_memory_move.argtypes = [
        u8p,
        ctypes.c_uint64,
        u8p,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    data = (ctypes.c_uint8 * 8)(*range(8))
    assert (
        native.rxf_memory_move(ctypes.cast(ctypes.byref(data, 2), u8p), 6, data, 8, 6)
        == 0
    )
    assert bytes(data) == bytes([0, 1, 0, 1, 2, 3, 4, 5])
    native.rxf_load_u32.argtypes = [
        u8p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    out = ctypes.c_uint32(0xA5A5A5A5)
    assert (
        native.rxf_load_u32(data, 3, 0, 4, 1, ctypes.byref(out)) == 6
        and out.value == 0xA5A5A5A5
    )
    assert (
        native.rxf_load_u32(data, 8, 0, 4, 1, ctypes.byref(out)) == 0
        and out.value == 0x01000100
    )


def test_native_utf8_validation_and_hash(native):
    u8p = ctypes.POINTER(ctypes.c_uint8)
    native.rxf_utf8_validate.argtypes = [
        u8p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    ]
    good = (ctypes.c_uint8 * 8)(*"A€😀".encode())
    count = ctypes.c_uint64(99)
    assert (
        native.rxf_utf8_validate(good, len(good), ctypes.byref(count)) == 0
        and count.value == 3
    )
    bad = (ctypes.c_uint8 * 2)(0xC0, 0x80)
    count.value = 99
    assert (
        native.rxf_utf8_validate(bad, 2, ctypes.byref(count)) == 8 and count.value == 99
    )


def test_canonical_handle_and_views_are_generation_checked():
    handle = ObjectHandle(17, 4, 8)
    assert ObjectHandle.from_bytes(handle.to_bytes()) == handle
    assert BoundedView(handle, 2, 6).checked_extent(32) == (10, 16)
    with pytest.raises(ValueError, match="exceeds"):
        BoundedView(handle, 20, 8).checked_extent(24)


def test_stdlib_ref_and_view_layouts_are_canonical():
    table = TypeTable.from_container(starter_template().build())
    canonical_ref = table.get_by_name("Ref")
    assert (canonical_ref.size, canonical_ref.align) == (24, 8)
    mutable_ref = next(
        value for value in table.types.values() if value.name == "MutRef"
    )
    assert mutable_ref.size == 24
    assert [(f.name, f.offset) for f in table.fields[mutable_ref.id]] == [
        ("object_id", 0),
        ("generation", 8),
        ("offset", 16),
    ]
    for name in ("Slice", "MutSlice", "StringView"):
        value = table.get_by_name(name)
        assert value.size == 40
        assert [(f.name, f.offset) for f in table.fields[value.id]] == [
            ("object_id", 0),
            ("generation", 8),
            ("offset", 16),
            ("start", 24),
            ("length", 32),
        ]


def test_hash_storage_layout_and_authority_are_explicit_and_mutation_safe():
    from copy import deepcopy

    import pytest

    from pymergetic.rxf.model.stdlib import (
        HashCapacityRule,
        HashGenerationPolicy,
        HashProbePolicy,
        HashStorageAuthority,
        HashTerminationRule,
        HashVisibilityPolicy,
    )
    from pymergetic.rxf.model.stdlib_corpus import (
        STDLIB_HASH_AUTHORITY_ID,
        STDLIB_HASH_STORAGE_AUTHORITY_TYPE,
    )

    container = starter_template().build()
    authority_node = next(
        node
        for node in container.nodes
        if node.type_id == STDLIB_HASH_STORAGE_AUTHORITY_TYPE
    )
    authority = HashStorageAuthority.from_node(authority_node)
    authority.validate_model({node.id: node for node in container.nodes})
    assert (
        authority.minimum_capacity,
        authority.load_numerator,
        authority.load_denominator,
        authority.tombstone_numerator,
        authority.tombstone_denominator,
        authority.capacity_rule,
        authority.probe_policy,
        authority.termination_rule,
        authority.generation_policy,
        authority.visibility_policy,
        authority.hash_algorithm_id,
    ) == (
        8,
        3,
        4,
        1,
        4,
        HashCapacityRule.POWER_OF_TWO,
        HashProbePolicy.LINEAR,
        HashTerminationRule.EMPTY_OR_CAPACITY_PROBES,
        HashGenerationPolicy.INCREMENT_ON_MUTATION,
        HashVisibilityPolicy.LIVE_OR_MATCHING_OWNER_PENDING,
        STDLIB_HASH_AUTHORITY_ID,
    )
    authority.validate_counts(8, 5, 2)
    with pytest.raises(ValueError):
        authority.validate_counts(7, 1, 0)
    with pytest.raises(ValueError):
        authority.validate_counts(8, 7, 2)

    broken = deepcopy(container)
    field = next(
        node
        for node in broken.nodes
        if node.parent == authority.map_type and node.name == "capacity"
    )
    field.data = FieldObject(
        field.id, field.name, authority.map_type, 8, 25
    ).to_payload()
    with pytest.raises(ValueError, match="layout mismatch"):
        authority.validate_model({node.id: node for node in broken.nodes})

    with pytest.raises(ValueError, match="minimum capacity"):
        HashStorageAuthority(
            authority.id,
            authority.name,
            authority.parent,
            authority.map_type,
            authority.set_type,
            authority.map_bucket_type,
            authority.set_bucket_type,
            authority.bucket_state_type,
            minimum_capacity=7,
        ).validate()


def test_type_table_rejects_overlap_duplicate_names_and_inline_variable():
    from copy import deepcopy

    from pymergetic.rxf.ty.builtins import U64_TYPE

    base = starter_template().build()
    slice_type = TypeTable.from_container(base).get_by_name("Slice")
    field_nodes = [
        n for n in base.nodes if n.parent == slice_type.id and n.kind.name == "FIELD"
    ]
    broken = deepcopy(base)
    target = broken.node_by_id(field_nodes[1].id)
    assert target is not None
    target.data = FieldObject(
        target.id, target.name, slice_type.id, U64_TYPE, 4
    ).to_payload()
    assert any("misaligned" in error or "overlaps" in error for error in check(broken))

    duplicate = deepcopy(base)
    target = duplicate.node_by_id(field_nodes[1].id)
    assert target is not None
    target.name = field_nodes[0].name
    assert any("duplicate FIELD name" in error for error in check(duplicate))


def test_global_heap_atomicity_borrows_compaction_resize_and_stale_handles():
    heap = GlobalHeapDirectory(64)
    a = heap.allocate(8, 8, b"abcdefgh")
    b = heap.allocate(8, 8, b"ABCDEFGH")
    one = heap.borrow(a)
    two = heap.borrow(a)
    with pytest.raises(HeapOperationRefused) as conflict:
        heap.borrow(a, mutable=True)
    assert conflict.value.refusal == HeapRefusal.BORROW_CONFLICT
    heap.release_borrow(two)
    heap.release_borrow(one)
    mutable = heap.borrow(a, mutable=True)
    with pytest.raises(HeapOperationRefused):
        heap.borrow(a)
    heap.release_borrow(mutable)
    heap.release(a)
    heap.compact()
    assert heap.read(b, 8) == b"ABCDEFGH"
    resized = heap.resize(b, 12)
    assert resized.generation == b.generation + 1
    assert heap.read(resized, 8) == b"ABCDEFGH"
    with pytest.raises(HeapOperationRefused) as stale:
        heap.read(b, 1)
    assert stale.value.refusal == HeapRefusal.STALE_GENERATION
    before = (heap.frontier, heap.slots)
    with pytest.raises(HeapOperationRefused) as oom:
        heap.allocate(128, 8)
    assert oom.value.refusal == HeapRefusal.OUT_OF_MEMORY
    assert (heap.frontier, heap.slots) == before


def test_core_specializations_are_stable_distinct_and_overflow_checked():
    u8 = (5, 1, 1)
    u32 = (7, 4, 4)
    first = specialize_value_type("Option", (u32,))
    assert first == specialize_value_type("Option", (u32,))
    a16 = specialize_value_type("FixedArray", (u8,), (16,))
    a32 = specialize_value_type("FixedArray", (u8,), (32,))
    assert (a16.size, a32.size) == (16, 32)
    assert a16.id != a32.id
    result = specialize_value_type("TypedResult", (u32, u8))
    assert result.size == 8 and result.alignment == 4
    with pytest.raises(OverflowError):
        specialize_value_type("FixedArray", ((8, 8, 8),), ((1 << 64) - 1,))


def test_iterator_invalidation_and_canonical_map_serialization():
    values = GenerationVector(90, [1, 2])
    token = values.iterator()
    assert values.next(token)[0] == 1
    values.push(3)
    with pytest.raises(ValueError, match="invalidated"):
        values.next(token)
    first = CanonicalMap[str, int](lambda key: key.encode())
    second = CanonicalMap[str, int](lambda key: key.encode())
    for key, value in (("z", 1), ("a", 2)):
        first.set(key, value)
    for key, value in (("a", 2), ("z", 1)):
        second.set(key, value)
    encode = lambda value: value.to_bytes(4, "little")
    assert first.items() == second.items()
    assert first.serialize(encode) == second.serialize(encode)


def test_stable_sort_and_comparator_refusal_leave_output_unchanged():
    values = [(1, "a"), (0, "x"), (1, "b")]
    stable_sort(values, lambda a, b: (a[0] > b[0]) - (a[0] < b[0]))
    assert values == [(0, "x"), (1, "a"), (1, "b")]
    original = values[:]

    def refuse(_a, _b):
        raise RuntimeError("comparison refused")

    with pytest.raises(RuntimeError, match="refused"):
        stable_sort(values, refuse)
    assert values == original


@pytest.mark.parametrize(
    "bad", [b"\x80", b"\xc0\x80", b"\xed\xa0\x80", b"\xf4\x90\x80\x80", b"\xe2\x82"]
)
def test_utf8_malformed_corpus_and_scalar_boundaries(bad):
    with pytest.raises(UnicodeDecodeError):
        utf8_scalar_boundaries(bad)
    payload = "A€😀".encode()
    assert utf8_scalar_boundaries(payload) == (0, 1, 4, 8)
    assert checked_utf8_slice(payload, 1, 4) == "€"
    with pytest.raises(ValueError, match="boundaries"):
        checked_utf8_slice(payload, 2, 4)


def test_composed_aliases_have_typed_graphs_and_compile_status_edges():
    from pymergetic.rxf.compiler.compile import compile_function
    from pymergetic.rxf.compiler.ir import CompiledFunction
    from pymergetic.rxf.execution.decode import decode_function
    from pymergetic.rxf.model.execution import FunctionImplementation
    from pymergetic.rxf.ty.builtins import FUNCTION_TYPE

    container = starter_template().build()
    functions = {
        node.name: node for node in container.nodes if node.type_id == FUNCTION_TYPE
    }
    for name in ("copy", "fill", "compare"):
        function = functions[name]
        assert (
            decode_function(function).implementation == FunctionImplementation.COMPOSED
        )
        compiled = compile_function(container, function.id)
        assert isinstance(compiled, CompiledFunction)
        assert len(compiled.blocks) == 3
        operation = compiled.blocks[0].operations[0]
        assert operation.composed is False
        assert len(operation.arguments) > 0
