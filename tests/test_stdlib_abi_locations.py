from copy import deepcopy

import pytest

from pymergetic.rxf.checker import check
from pymergetic.rxf.execution.decode import decode_abi_signature
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.model.contracts import (
    ABIPassingMode,
    ABIRegisterBank,
    ABIRole,
    ABIValueLocation,
    verify_abi_locations,
)
from pymergetic.rxf.model.stdlib_corpus import NATIVE_SYMBOLS
from pymergetic.rxf.ty.builtins import (
    ABI_SIGNATURE_TYPE,
    ABI_VALUE_LOCATION_TYPE,
    FUNCTION_TYPE,
)


def _abi(container, function_name):
    fn = next(
        n
        for n in container.nodes
        if n.type_id == FUNCTION_TYPE and n.name == function_name
    )
    return [
        n
        for n in container.nodes
        if n.type_id == ABI_SIGNATURE_TYPE and n.parent == fn.id
    ]


def test_all_native_abis_have_ordered_typed_locations():
    c = starter_template().build()
    records = [
        n
        for n in c.nodes
        if n.type_id == ABI_SIGNATURE_TYPE
        and n.parent
        in {
            x.id
            for x in c.nodes
            if x.type_id == FUNCTION_TYPE and x.name in NATIVE_SYMBOLS
        }
    ]
    assert len(records) == 90
    for node in records:
        abi = decode_abi_signature(node)
        locations = [
            ABIValueLocation.from_node(c.node_by_id(r.target))  # pyright: ignore[reportArgumentType]
            for r in node.refs
            if r.to_off == 269
        ]
        assert len(locations) == abi.location_count
        assert locations[-1].role == ABIRole.STATUS_RETURN
        assert locations[-1].register_bank == ABIRegisterBank.RETURN
        assert locations[-1].width == 4
        assert all(
            not location.register_indices
            for location in locations
            if location.register_bank == ABIRegisterBank.STACK
        )
    # Authored stdlib SemanticGraphs supersede stale ordinary compatibility Calls.
    assert not [error for error in check(c) if not error.startswith("Call ")]


def test_allocate_abi_has_handle_output_and_explicit_resolution_modes():
    c = starter_template().build()
    for node in _abi(c, "runtime_allocate_prepare"):
        locations = [
            ABIValueLocation.from_node(c.node_by_id(r.target))  # pyright: ignore[reportArgumentType]
            for r in node.refs
            if r.to_off == 269
        ]
        output = locations[-2]
        assert (output.width, output.alignment, output.pointee_type) == (
            24,
            8,
            12,
        ) and output.passing == ABIPassingMode.INDIRECT_BY_REFERENCE
        transaction = locations[-3]
        assert (
            transaction.passing == ABIPassingMode.TRANSACTION_POINTER
            and transaction.width == 8
            and transaction.chunk_count == 1
        )


def test_abi_location_roundtrip_and_malformed_rejection():
    c = starter_template().build()
    node = next(n for n in c.nodes if n.type_id == ABI_VALUE_LOCATION_TYPE)
    value = ABIValueLocation.from_node(node)
    assert ABIValueLocation.from_node(value.to_node()) == value
    broken = deepcopy(node)
    broken.data = broken.data[:-1]
    with pytest.raises(ValueError):
        ABIValueLocation.from_node(broken)


def test_context_and_physical_register_policy_matches_prototypes():
    c = starter_template().build()
    for name in NATIVE_SYMBOLS:
        for node in _abi(c, name):
            locations = [
                ABIValueLocation.from_node(c.node_by_id(r.target))  # pyright: ignore[reportArgumentType]
                for r in node.refs
                if r.to_off == 269
            ]
            contexts = [v for v in locations if v.role == ABIRole.HIDDEN_CONTEXT]
            assert bool(contexts) == (name.startswith("runtime_"))
            limit = 6 if decode_abi_signature(node).kind.value == 1 else 8
            assert all(
                i < limit
                for v in locations
                if v.register_bank == ABIRegisterBank.INTEGER
                for i in v.register_indices
            )
            assert verify_abi_locations(c, node) == []


def test_all_composed_entries_have_three_target_abis():
    from pymergetic.rxf.compiler.semantic import decode_strict_abi
    from pymergetic.rxf.model.stdlib_corpus import STDLIB_SEMANTIC_GRAPH_TYPE
    from pymergetic.rxf.model.stdlib_semantics import SemanticGraph

    c = starter_template().build()
    graphs = [
        SemanticGraph.from_node(n)
        for n in c.nodes
        if n.type_id == STDLIB_SEMANTIC_GRAPH_TYPE
    ]
    assert len(graphs) == 53
    locations = []
    for graph in graphs:
        target_ids = {
            decode_abi_signature(node).target_id
            for node in c.nodes
            if node.type_id == ABI_SIGNATURE_TYPE and node.parent == graph.function_id
        }
        assert target_ids == {817, 818, 823}
        for architecture in (1, 2):
            _, decoded = decode_strict_abi(c, graph.function_id, architecture)
            locations.extend(decoded)
            assert decoded[0].role == ABIRole.HIDDEN_CONTEXT
            assert decoded[-2].role == ABIRole.TRANSIENT_OUTPUT
            assert decoded[-1].role == ABIRole.STATUS_RETURN
    expected_locations = sum(
        decode_strict_abi(c, graph.function_id, architecture)[0].location_count
        for graph in graphs
        for architecture in (1, 2)
    )
    assert len(locations) == expected_locations
    assert len({location.id for location in locations}) == expected_locations


def test_receipt_composed_entry_geometry_is_exact():
    c = starter_template().build()
    for record in _abi(c, "build_receipt"):
        locations = [
            ABIValueLocation.from_node(c.node_by_id(ref.target))  # pyright: ignore[reportArgumentType]
            for ref in record.refs
            if ref.to_off == 269
        ]
        assert [location.role for location in locations] == [
            ABIRole.HIDDEN_CONTEXT,
            ABIRole.TRANSIENT_OUTPUT,
            ABIRole.STATUS_RETURN,
        ]
        assert [location.register_indices for location in locations] == [
            (0,),
            (1,),
            (0,),
        ]


def test_operational_payloads_require_physical_abi_outputs():
    from pymergetic.rxf.model.contracts import ABIRole, ContractRole
    from pymergetic.rxf.model.stdlib_corpus import STDLIB_SEMANTIC_OPERATION_TYPE
    from pymergetic.rxf.model.stdlib_semantics import SemanticOperation

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    mismatches = []
    operational_calls = 0
    for node in container.nodes:
        if node.type_id != STDLIB_SEMANTIC_OPERATION_TYPE:
            continue
        operation = SemanticOperation.from_node(node)
        if not operation.callee_id:
            continue
        operational_calls += 1
        payloads = [
            value
            for value in operation.result_value_ids
            if value != operation.status_value_id
        ]
        target_abis = [
            record
            for record in container.nodes
            if record.type_id == ABI_SIGNATURE_TYPE
            and record.parent == operation.callee_id
        ]
        has_outputs = all(
            any(
                ABIValueLocation.from_node(by_id[ref.target]).role
                in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}
                for ref in record.refs
                if ref.to_off == int(ContractRole.ABI_LOCATION)
            )
            for record in target_abis
        )
        if payloads and not has_outputs:
            mismatches.append(operation.id)
    assert operational_calls == 192
    assert mismatches == []


def test_begin_private_is_concrete_native_transaction_call():
    from pymergetic.rxf.model.stdlib_corpus import (
        NATIVE_TRANSACTION_TYPE,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_VALUE_TYPE,
    )
    from pymergetic.rxf.model.stdlib_semantics import (
        SemanticBlock,
        SemanticGraph,
        SemanticOpcode,
        SemanticOperation,
        SemanticValue,
        SemanticValueKind,
    )
    from pymergetic.rxf.ty.builtins import U32_TYPE

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    transaction_owners: dict[str, tuple[SemanticOperation, ...]] = {}
    for node in container.nodes:
        if node.type_id != STDLIB_SEMANTIC_GRAPH_TYPE:
            continue
        graph = SemanticGraph.from_node(node)
        blocks = tuple(
            SemanticBlock.from_node(by_id[block_id]) for block_id in graph.block_ids
        )
        operations = tuple(
            SemanticOperation.from_node(by_id[operation_id])
            for block in blocks
            for operation_id in block.operation_ids
        )
        begins = tuple(
            operation
            for operation in operations
            if operation.opcode == SemanticOpcode.BEGIN_PRIVATE
        )
        if not begins:
            continue
        transaction_owners[graph.name.removesuffix("_graph")] = operations
        assert len(begins) == 1, graph.name
        begin = begins[0]
        owner_block = next(block for block in blocks if begin.id in block.operation_ids)
        assert begin.callee_id == 60165, graph.name
        assert begin.result_types == (NATIVE_TRANSACTION_TYPE, U32_TYPE), graph.name
        assert begin.status_value_id == begin.result_value_ids[-1], graph.name
        assert owner_block.operation_ids[-1] == begin.id, graph.name
        assert owner_block.terminator.name == "BRANCH", graph.name
        assert owner_block.condition_value_id == begin.status_value_id, graph.name
        assert {
            SemanticOpcode.VALIDATE,
            SemanticOpcode.PUBLISH,
            SemanticOpcode.ROLLBACK,
            SemanticOpcode.CLEANUP,
        } <= {operation.opcode for operation in operations}, graph.name

    # These graphs gained real transaction ownership when their implementations
    # became concrete; keep their inclusion explicit in failure diagnostics.
    assert {"array_new", "build_receipt"} <= transaction_owners.keys()
    transaction_literals = [
        SemanticValue.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_VALUE_TYPE
        and SemanticValue.from_node(node).type_id == NATIVE_TRANSACTION_TYPE
        and SemanticValue.from_node(node).kind == SemanticValueKind.LITERAL
    ]
    assert transaction_literals == []

    records = _abi(container, "runtime_begin_private")
    assert len(records) == 2
    for record in records:
        location_nodes = [
            container.node_by_id(ref.target) for ref in record.refs if ref.to_off == 269
        ]
        assert all(node is not None for node in location_nodes)
        locations = [
            ABIValueLocation.from_node(node)
            for node in location_nodes
            if node is not None
        ]
        assert any(
            location.role == ABIRole.TRANSIENT_OUTPUT
            and location.pointee_type == NATIVE_TRANSACTION_TYPE
            and location.width == 48
            for location in locations
        )
