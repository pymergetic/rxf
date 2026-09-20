"""The executable's authoritative memory is the complete, recoverable RXF."""

import struct
from copy import deepcopy
from dataclasses import replace

import pytest

from pymergetic.rxf.checker import check
from pymergetic.rxf.executable.graph import (
    CODE_HEADER_SIZE,
    encode_graph,
    extract_graph_bytes,
    inspect_graph_image,
    recover_graph,
)
from pymergetic.rxf.executable.models import (
    RuntimeTableKind,
    SegmentKind,
    SegmentPermissions,
    SymbolKind,
)
from pymergetic.rxf.executable.pipeline import build_image, plan_executable
from pymergetic.rxf.executable.targets import resolve_executable_target
from pymergetic.rxf.execution.decode import decode_code
from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, Template, starter_template
from pymergetic.rxf.model.contracts import verify_abi_locations
from pymergetic.rxf.model.execution import (
    CallObject,
    CallRole,
    CodeFormat,
    CodeObject,
    Effect,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    RelocationKind,
    RelocationObject,
    SignatureObject,
    semantic_digest,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.state import Disposition
from pymergetic.rxf.model.target import native_target_nodes
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.builtins import CODE_TYPE, U32_TYPE

TARGET = resolve_executable_target(817)


@pytest.fixture(scope="module")
def built():
    source = starter_template().build()
    untouched = deepcopy(source)
    plan = plan_executable(source, STARTER_MAIN_FUNCTION_ID, TARGET)
    return source, untouched, plan, build_image(plan)


def test_complete_source_graph_retained_without_mutating_input(built):
    source, untouched, plan, image = built
    assert source == untouched
    segment = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    recovered = recover_graph(segment.data)
    by = {node.id: node for node in recovered.nodes}
    assert set(by) == set(plan.object_ids)
    assert {node.id for node in source.nodes} < set(by)
    for node in source.nodes:
        actual = by[node.id]
        assert replace(actual, refs=node.refs) == node
        assert actual.refs[: len(node.refs)] == node.refs
        assert all(
            r.to_off == CallRole.IMPLEMENTATION for r in actual.refs[len(node.refs) :]
        )
    assert check(recovered) == []


def test_runtime_pointers_are_real_embedded_cells_and_code(built):
    _, _, plan, image = built
    segment = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    assert segment.data.startswith(b"RXFB")
    assert segment.permissions == SegmentPermissions.READ | SegmentPermissions.EXECUTE
    assert not any(
        s.kind in (SegmentKind.TEXT, SegmentKind.READ_ONLY_DATA) for s in image.segments
    )
    graph = inspect_graph_image(segment.data)
    recovered = recover_graph(segment.data)
    by = {node.id: node for node in recovered.nodes}
    runtime = next(s for s in image.segments if s.kind is SegmentKind.RUNTIME_TABLES)
    object_table = next(
        t for t in image.runtime_tables if t.kind is RuntimeTableKind.OBJECT
    )
    assert object_table.object_ids == tuple(sorted(by))
    for index, oid in enumerate(object_table.object_ids):
        offset = (
            object_table.address
            - runtime.virtual_address
            + index * object_table.entry_size
        )
        entry = struct.unpack_from("<10Q", runtime.data, offset)
        assert entry[:3] == (oid, by[oid].generation, by[oid].type_id)
        assert entry[6] == 1  # Native runtime LIVE, not RXF Disposition.RETAIN (0).
        assert entry[3] == graph.payload_address(oid, segment.virtual_address)
        assert (
            segment.data[
                entry[3] - segment.virtual_address : entry[3]
                - segment.virtual_address
                + entry[4]
            ]
            == by[oid].data
        )
    function_table = next(
        t for t in image.runtime_tables if t.kind is RuntimeTableKind.FUNCTION
    )
    for index, function in enumerate(plan.functions):
        offset = (
            function_table.address
            - runtime.virtual_address
            + index * function_table.entry_size
        )
        fid, _, address = struct.unpack_from("<3Q", runtime.data, offset)
        assert fid == function.function_id
        if function.code_id is not None:
            assert address == graph.code_address(
                function.code_id, segment.virtual_address
            )
            code = decode_code(by[function.code_id])
            assert (
                address
                == segment.virtual_address
                + graph.payload_offsets[function.code_id]
                + CODE_HEADER_SIZE
                + code.entry_offset
            )
            symbol = next(
                s
                for s in image.symbols
                if s.object_id == fid
                and s.kind in (SymbolKind.ENTRY, SymbolKind.FUNCTION)
            )
            assert symbol.address == address


def test_generated_native_code_is_schema_valid_and_inspectable(built):
    source, _, plan, image = built
    recovered = recover_graph(
        next(s.data for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    )
    original_ids = {node.id for node in source.nodes}
    generated = [
        node
        for node in recovered.nodes
        if node.type_id == CODE_TYPE and node.id not in original_ids
    ]
    assert generated
    for node in generated:
        code = decode_code(node)
        assert code.owner_function == node.parent
        assert code.target_id == TARGET.target_id
        abi = next(r.target for r in node.refs if r.to_off == CallRole.ABI_SIGNATURE)
        abi_node = recovered.node_by_id(abi)
        assert abi_node is not None
        assert verify_abi_locations(recovered, abi_node) == []
        assert any(r.to_off == CallRole.PROVENANCE for r in node.refs)
        assert any(r.to_off == CallRole.SOURCE for r in node.refs)
        planned = next(f for f in plan.functions if f.code_id == node.id)
        assert code.raw_bytes == planned.text
        assert planned.native.object_ids == tuple(sorted(planned.native.object_ids))


def test_graph_codec_retains_empty_and_nonretained_objects():
    source = Template(include_lowered=False).build()
    sample = source.nodes[-1]
    source.nodes.extend(
        [
            NodeDef(1 << 62, "empty_object", NodeKind.DATA),
            replace(
                sample,
                id=(1 << 62) + 1,
                name="transient",
                refs=[],
                state=replace(sample.state, disposition=Disposition.TRANSIENT),
            ),
            replace(
                sample,
                id=(1 << 62) + 2,
                name="tombstone",
                refs=[],
                state=replace(sample.state, disposition=Disposition.TOMBSTONE),
            ),
        ]
    )
    # Use data objects here; copied Code contracts would intentionally be invalid.
    for node in source.nodes[-2:]:
        node.kind, node.type_id = NodeKind.DATA, 0
    encoded = encode_graph(source)
    recovered = recover_graph(encoded.data + bytes(17))
    assert {node.id: node for node in recovered.nodes} == {
        node.id: node for node in source.nodes
    }
    assert extract_graph_bytes(encoded.data + bytes(17)) == encoded.data
    assert encode_graph(recovered).data == encoded.data
    assert set(encoded.payload_offsets) == {node.id for node in source.nodes}
    assert inspect_graph_image(encoded.data) == encoded


def test_composed_materialization_is_complete_and_checker_valid_without_object_lookup():
    source = Template(include_lowered=False).build()
    source.nodes.extend(native_target_nodes(parent=0))
    fid, sid, cid, main, body = range(81_000_000, 81_000_005)
    source.header.entry_node = main
    source.nodes.extend(
        [
            SignatureObject(sid, "signature", fid, U32_TYPE).to_node(),
            FunctionObject(
                fid,
                "constant",
                0,
                sid,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.APPLICATION,
                implementation_ids=(cid,),
            ).to_node(),
            CodeObject(
                cid,
                "constant_code",
                fid,
                fid,
                TARGET.target_id,
                CodeFormat.NATIVE,
                b"\x31\xc0\xc3",
                sid,
                Effect.NONE,
                1,
                semantic_digest("constant", sid, Effect.NONE, 1),
            ).to_node(),
            FunctionObject(
                main,
                "main",
                0,
                sid,
                FunctionImplementation.COMPOSED,
                FunctionLayer.APPLICATION,
                body_id=body,
            ).to_node(),
            CallObject(
                body, "call_constant", main, fid, result_type=U32_TYPE
            ).to_node(),
        ]
    )
    untouched = deepcopy(source)
    plan = plan_executable(source, main, TARGET)
    image = build_image(plan)
    recovered = recover_graph(
        next(s.data for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    )
    assert source == untouched
    assert set(check(recovered)) == set(check(source))
    generated = next(
        n for n in recovered.nodes if n.type_id == CODE_TYPE and n.id != cid
    )
    assert decode_code(generated).owner_function == main
    abi_id = next(
        r.target for r in generated.refs if r.to_off == CallRole.ABI_SIGNATURE
    )
    assert verify_abi_locations(recovered, recovered.node_by_id(abi_id)) == []
    assert set(plan.object_ids) == {n.id for n in recovered.nodes}
    assert build_image(plan) == image
    source.nodes.reverse()
    assert build_image(plan_executable(source, main, TARGET)) == image
    segment = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    graph = inspect_graph_image(segment.data)
    assert image.entry_address == graph.code_address(
        generated.id, segment.virtual_address
    )
    runtime = next(s for s in image.segments if s.kind is SegmentKind.RUNTIME_TABLES)
    table = next(t for t in image.runtime_tables if t.kind is RuntimeTableKind.OBJECT)
    for index, oid in enumerate(table.object_ids):
        offset = table.address - runtime.virtual_address + index * table.entry_size
        assert struct.unpack_from("<Q", runtime.data, offset + 24)[
            0
        ] == graph.payload_address(oid, segment.virtual_address)


def test_bound_relocations_are_separate_ordinary_code_with_source_provenance():
    source = Template(include_lowered=False).build()
    source.nodes.extend(native_target_nodes(parent=0))
    fid, sid, cid, rid, oid = range(80_000_000, 80_000_005)
    source.header.entry_node = fid
    source.nodes.extend(
        [
            SignatureObject(sid, "entry_signature", fid, 0).to_node(),
            FunctionObject(
                fid,
                "entry",
                0,
                sid,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.APPLICATION,
                implementation_ids=(cid,),
            ).to_node(),
            CodeObject(
                cid,
                "original_code",
                fid,
                fid,
                TARGET.target_id,
                CodeFormat.NATIVE,
                bytes(8) + b"\xc3",
                sid,
                Effect.NONE,
                1,
                semantic_digest("entry", sid, Effect.NONE, 1),
                relocation_ids=(rid,),
            ).to_node(),
            RelocationObject(
                rid, "payload_address", cid, 0, RelocationKind.ABSOLUTE, oid, 8
            ).to_node(),
            NodeDef(
                oid, "unreachable_payload", NodeKind.DATA, data=b"preserved payload"
            ),
        ]
    )
    untouched = deepcopy(source)
    plan = plan_executable(source, fid, TARGET)
    image = build_image(plan)
    assert source == untouched
    assert build_image(plan) == image
    segment = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    graph = inspect_graph_image(segment.data)
    recovered = recover_graph(segment.data)
    original = recovered.node_by_id(cid)
    assert original == source.node_by_id(cid)
    materialized = recovered.node_by_id(plan.functions[0].code_id)
    assert materialized.id != cid
    assert struct.unpack_from("<Q", decode_code(materialized).raw_bytes)[
        0
    ] == graph.payload_address(oid, segment.virtual_address)
    assert any(
        r.to_off == CallRole.SOURCE and r.target == cid for r in materialized.refs
    )
    assert not any(r.to_off == CallRole.RELOCATION for r in materialized.refs)
    assert image.entry_address == graph.code_address(
        materialized.id, segment.virtual_address
    )


def test_graph_extraction_rejects_invalid_or_truncated_extent(built):
    image = built[-1]
    data = next(s.data for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    with pytest.raises(ValueError):
        recover_graph(b"NOPE" + data[4:])
    with pytest.raises(ValueError):
        extract_graph_bytes(data[:-1])
