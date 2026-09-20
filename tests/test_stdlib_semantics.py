"""Concrete typed semantic CFG, specialization, and receipt proofs."""

from copy import deepcopy
from dataclasses import replace

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import Effect, FunctionImplementation
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.stdlib_corpus import (
    STDLIB_RECEIPT_BYTES,
    STDLIB_RECEIPT_EXPECTED_ID,
    STDLIB_RECEIPT_FUNCTION_ID,
    STDLIB_SEMANTIC_BLOCK_TYPE,
    STDLIB_SEMANTIC_GRAPH_TYPE,
    STDLIB_SEMANTIC_OPERATION_TYPE,
    STDLIB_SEMANTIC_VALUE_TYPE,
    stdlib_inventory,
)
from pymergetic.rxf.model.stdlib_semantics import (
    SemanticBlock,
    SemanticGraph,
    SemanticOpcode,
    SemanticOperation,
    SemanticValue,
    verify_semantic_graphs,
)
from pymergetic.rxf.stdlib_reference import build_receipt_reference
from pymergetic.rxf.ty.builtins import FUNCTION_TYPE


def _node(container: Container, node_id: int) -> NodeDef:
    node = container.node_by_id(node_id)
    assert node is not None, f"required node {node_id} is missing"
    return node


def _graphs(container):
    return {
        SemanticGraph.from_node(node).name.removesuffix(
            "_graph"
        ): SemanticGraph.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_GRAPH_TYPE
    }


def _opcodes(container, graph):
    by_id = {node.id: node for node in container.nodes}
    return tuple(
        SemanticOperation.from_node(by_id[operation]).opcode
        for block_id in graph.block_ids
        for operation in SemanticBlock.from_node(by_id[block_id]).operation_ids
    )


def test_every_stdlib_api_is_native_or_concrete_composed_graph():
    container = starter_template().build()
    inventory = stdlib_inventory(container.nodes)
    assert inventory["native_functions"] == 30
    assert inventory["composed_functions"] == 55
    assert inventory["abstract_functions"] == 12
    assert inventory["declared_functions"] == inventory["api_declared"] == 0
    assert inventory["concrete_specializations"] == 7
    # Ordinary compatibility Call bodies are no longer compiler authority for
    # authored stdlib functions; validate their persisted SemanticGraphs.
    assert (
        verify_semantic_graphs(
            container,
            STDLIB_SEMANTIC_GRAPH_TYPE,
            STDLIB_SEMANTIC_BLOCK_TYPE,
            STDLIB_SEMANTIC_OPERATION_TYPE,
            STDLIB_SEMANTIC_VALUE_TYPE,
        )
        == []
    )
    assert len(_graphs(container)) == 53  # 52 APIs plus the receipt application


def test_private_transaction_edges_carry_typed_status_roles():
    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    values = {
        node.id: SemanticValue.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_VALUE_TYPE
    }
    transactional: set[str] = set()
    for graph in _graphs(container).values():
        blocks = [SemanticBlock.from_node(by_id[value]) for value in graph.block_ids]
        operations = {
            operation_id: SemanticOperation.from_node(by_id[operation_id])
            for block in blocks
            for operation_id in block.operation_ids
        }
        if not any(
            operation.opcode == SemanticOpcode.BEGIN_PRIVATE
            for operation in operations.values()
        ):
            continue
        transactional.add(graph.name.removesuffix("_graph"))
        rollback = next(
            block
            for block in blocks
            if any(
                operations[value].opcode == SemanticOpcode.ROLLBACK
                for value in block.operation_ids
            )
        )
        cleanup = next(
            block
            for block in blocks
            if any(
                operations[value].opcode == SemanticOpcode.CLEANUP
                for value in block.operation_ids
            )
        )
        for block in blocks:
            operation = (
                operations.get(block.operation_ids[-1]) if block.operation_ids else None
            )
            for target_id, arguments in zip(
                block.successor_ids, block.successor_arguments
            ):
                target = next(value for value in blocks if value.id == target_id)
                assert len(arguments) == len(target.parameter_value_ids)
                assert tuple(values[value].type_id for value in arguments) == tuple(
                    values[value].type_id for value in target.parameter_value_ids
                )
                if target_id in {rollback.id, cleanup.id}:
                    assert len(arguments) == 1
                    assert values[arguments[0]].type_id == 7
                    if (
                        operation is not None
                        and operation.status_value_id
                        and operation.opcode
                        not in {
                            SemanticOpcode.ROLLBACK,
                            SemanticOpcode.CLEANUP,
                        }
                    ):
                        assert arguments == (operation.status_value_id,)
    assert {"array_new", "build_receipt"} <= transactional

    # Representative mutation: a U64 payload may not feed a U32 rollback phi.
    graph = next(
        value
        for value in _graphs(container).values()
        if any(
            operations.opcode == SemanticOpcode.ROLLBACK
            for block_id in value.block_ids
            for operation_id in SemanticBlock.from_node(by_id[block_id]).operation_ids
            for operations in (SemanticOperation.from_node(by_id[operation_id]),)
        )
    )
    blocks = [SemanticBlock.from_node(by_id[value]) for value in graph.block_ids]
    rollback = next(
        block
        for block in blocks
        if any(
            SemanticOperation.from_node(by_id[operation_id]).opcode
            == SemanticOpcode.ROLLBACK
            for operation_id in block.operation_ids
        )
    )
    source = next(
        block
        for block in blocks
        if rollback.id in block.successor_ids
        and block.successor_arguments[block.successor_ids.index(rollback.id)]
    )
    edge = source.successor_ids.index(rollback.id)
    status = source.successor_arguments[edge][0]
    payload = next(
        value.id
        for value in values.values()
        if value.owner_function_id == graph.function_id and value.type_id == 8
    )
    assert values[status].type_id == 7 and values[payload].type_id == 8

    broken = deepcopy(container)
    broken_source_node = _node(broken, source.id)
    assert broken_source_node is not None
    broken_source = SemanticBlock.from_node(broken_source_node)
    arguments = list(broken_source.successor_arguments)
    arguments[edge] = (payload,)
    replacement = replace(broken_source, successor_arguments=tuple(arguments)).to_node(
        STDLIB_SEMANTIC_BLOCK_TYPE
    )
    broken.nodes[broken.nodes.index(broken_source_node)] = replacement
    assert any(
        "phi type mismatch" in error
        for error in verify_semantic_graphs(
            broken,
            STDLIB_SEMANTIC_GRAPH_TYPE,
            STDLIB_SEMANTIC_BLOCK_TYPE,
            STDLIB_SEMANTIC_OPERATION_TYPE,
            STDLIB_SEMANTIC_VALUE_TYPE,
        )
    )


def test_all_persisted_calls_match_signatures_without_markers():
    import struct

    from pymergetic.rxf.checker import check
    from pymergetic.rxf.execution.decode import decode_function, decode_signature
    from pymergetic.rxf.model.execution import CallRole
    from pymergetic.rxf.ty.builtins import CALL_TYPE, FUNCTION_TYPE

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    calls = [node for node in container.nodes if node.type_id == CALL_TYPE]
    assert len(calls) == 63
    assert all("marker" not in call.name for call in calls)
    assert not [error for error in check(container) if error.startswith("Call ")]
    for call in calls:
        callee_id, argument_count, result_type = struct.unpack("<3Q", call.data)
        callee = by_id[callee_id]
        assert callee.type_id == FUNCTION_TYPE
        signature = by_id[decode_function(callee).signature_id]
        decoded = decode_signature(signature)
        arguments = tuple(
            ref.target for ref in call.refs if ref.to_off == int(CallRole.ARGUMENT)
        )
        assert argument_count == decoded.parameter_count == len(arguments)
        assert result_type == decoded.return_type


def test_unrelated_apis_are_structurally_distinct():
    container = starter_template().build()
    graphs = _graphs(container)
    allocate = _opcodes(container, graphs["heap_allocate"])
    push = _opcodes(container, graphs["vector_push"])
    sort = _opcodes(container, graphs["stable_sort"])
    assert allocate != push != sort
    assert SemanticOpcode.ALLOCATE in allocate
    assert SemanticOpcode.WRITE in push
    assert {
        SemanticOpcode.MOVE_SLOT,
        SemanticOpcode.CALL_CALLBACK,
        SemanticOpcode.ITER_ADVANCE,
    } <= set(sort)
    assert all(
        {
            SemanticOpcode.BEGIN_PRIVATE,
            SemanticOpcode.VALIDATE,
            SemanticOpcode.PUBLISH,
            SemanticOpcode.ROLLBACK,
            SemanticOpcode.CLEANUP,
        }
        <= set(values)
        for values in (allocate, push, sort)
    )


def test_option_result_loops_and_refusal_mapping_have_explicit_cfg():
    container = starter_template().build()
    graphs = _graphs(container)
    by_id = {n.id: n for n in container.nodes}
    for name in ("hash_map_get", "catch_refusal", "map_success"):
        graph = graphs[name]
        blocks = [SemanticBlock.from_node(by_id[v]) for v in graph.block_ids]
        assert SemanticOpcode.READ_TAG in _opcodes(container, graph)
        assert any(
            block.terminator.name == "SWITCH_TAG"
            and len(block.case_tags) == 2
            and len(block.successor_ids) == 3
            for block in blocks
        )
    for name in ("collection_iter", "fold", "stable_sort"):
        values = set(_opcodes(container, graphs[name]))
        assert {
            SemanticOpcode.ITER_INIT,
            SemanticOpcode.ITER_CONDITION,
            SemanticOpcode.ITER_PROJECT,
            SemanticOpcode.ITER_ADVANCE,
        } <= values
    assert SemanticOpcode.MAP_REFUSAL in set(
        _opcodes(container, graphs["catch_refusal"])
    )


def test_all_iterator_headers_recompute_conditions_and_carry_bounds():
    container = starter_template().build()
    graphs = _graphs(container)
    for name in (
        "collection_iter",
        "string_scalars",
        "string_find",
        "string_split",
        "count",
        "map",
        "filter",
        "fold",
        "iterator_take",
        "iterator_skip",
        "iterator_zip",
    ):
        graph = graphs[name]
        blocks = [
            SemanticBlock.from_node(_node(container, block_id))
            for block_id in graph.block_ids
        ]
        header = next(block for block in blocks if block.terminator.name == "LOOP")
        condition = next(
            SemanticOperation.from_node(_node(container, op_id))
            for op_id in header.operation_ids
            if SemanticOperation.from_node(_node(container, op_id)).opcode
            == SemanticOpcode.ITER_CONDITION
        )
        assert len(header.parameter_value_ids) >= 2
        assert condition.input_value_ids[:2] == header.parameter_value_ids[:2]
        assert header.condition_value_id == condition.result_value_ids[0]
        incoming = [
            arguments
            for block in blocks
            for target, arguments in zip(
                block.successor_ids, block.successor_arguments, strict=True
            )
            if target == header.id
        ]
        assert incoming
        assert all(
            len(arguments) == len(header.parameter_value_ids) for arguments in incoming
        )
        advance_results = {
            SemanticOperation.from_node(_node(container, op_id)).result_value_ids[0]
            for block in blocks
            for op_id in block.operation_ids
            if SemanticOperation.from_node(_node(container, op_id)).opcode
            == SemanticOpcode.ITER_ADVANCE
        }
        assert any(arguments[0] in advance_results for arguments in incoming)


def test_callbacks_have_typed_static_bindings_without_placeholders():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph

    container = starter_template().build()
    graphs = _graphs(container)
    callbacks = []
    for name in ("stable_sort", "map", "filter", "fold"):
        function = normalize_semantic_graph(container, graphs[name].function_id)
        callbacks.extend(
            op
            for block in function.blocks
            for op in block.operations
            if op.opcode == SemanticOpcode.CALL_CALLBACK
        )
    assert len(callbacks) == 6
    assert all(op.callee_id != 0 for op in callbacks)
    assert all(
        not any(value.kind.name in {"LITERAL", "FUNCTION"} for value in op.inputs)
        for op in callbacks
    )
    assert {_node(container, op.callee_id).name for op in callbacks} == {
        "less_uint64_t",
        "bit_not_uint64_t",
        "wrapping_add_uint64_t",
    }
    clamp = normalize_semantic_graph(container, graphs["clamp"].function_id)
    assert not any(
        op.opcode == SemanticOpcode.CALL_CALLBACK
        for block in clamp.blocks
        for op in block.operations
    )


def test_semantic_verifier_rejects_broken_transaction_edges():
    container = starter_template().build()
    graph = _graphs(container)["vector_push"]
    broken = deepcopy(container)
    block = SemanticBlock.from_node(_node(broken, graph.block_ids[0]))
    replacement = SemanticBlock(
        block.id,
        block.name,
        block.parent,
        block.index,
        block.terminator,
        block.operation_ids,
        block.predecessor_ids,
        (),
        block.parameter_types,
        block.parameter_value_ids,
        block.condition_value_id,
        block.terminator_value_id,
        (),
        block.case_tags,
    ).to_node(STDLIB_SEMANTIC_BLOCK_TYPE)
    target = _node(broken, block.id)
    target.data, target.refs = replacement.data, replacement.refs
    assert verify_semantic_graphs(
        broken,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_BLOCK_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
    )


def test_all_composed_functions_frontend_normalize_without_markers():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph

    container = starter_template().build()
    marker_names = {
        "semantic_transaction",
        "semantic_branch",
        "semantic_loop",
        "semantic_cleanup",
        "semantic_callback",
        "semantic_pure",
    }
    assert not any(node.name in marker_names for node in container.nodes)
    graphs = _graphs(container)
    assert len(graphs) == 53
    for source in graphs.values():
        graph = normalize_semantic_graph(container, source.function_id)
        assert graph.blocks and graph.results


def test_receipt_contains_literal_and_reachable_concrete_workflow():
    container = starter_template().build()
    expected = _node(container, STDLIB_RECEIPT_EXPECTED_ID)
    assert expected is not None and expected.data == STDLIB_RECEIPT_BYTES
    literals = {
        node.name: node.data for node in container.nodes if node.id in {90007, 90008}
    }
    assert literals == {
        "receipt_prefix_bytes": b"Receipt total: ",
        "receipt_newline_bytes": b"\n",
    }
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph

    graph = normalize_semantic_graph(container, STDLIB_RECEIPT_FUNCTION_ID)
    opcodes = {
        operation.opcode for block in graph.blocks for operation in block.operations
    }
    assert {
        SemanticOpcode.BEGIN_PRIVATE,
        SemanticOpcode.ALLOCATE,
        SemanticOpcode.PUBLISH,
        SemanticOpcode.ROLLBACK,
        SemanticOpcode.CLEANUP,
        SemanticOpcode.RETURN_VALUE,
    } <= opcodes
    assert (
        build_receipt_reference([("tax", 95), ("subtotal", 475)])
        == STDLIB_RECEIPT_BYTES
    )


def test_only_template_model_constructors_are_abstract():
    container = starter_template().build()
    abstract = [
        node
        for node in container.nodes
        if node.type_id == FUNCTION_TYPE
        and node.parent == 41001
        and decode_function(node).implementation == FunctionImplementation.ABSTRACT
    ]
    assert len(abstract) == 12 and all(
        node.name.startswith("model_") for node in abstract
    )


def test_ssa_verifier_rejects_missing_operand_backref_and_phi():
    container = starter_template().build()
    graph = _graphs(container)["vector_push"]
    by = {n.id: n for n in container.nodes}
    block = SemanticBlock.from_node(by[graph.block_ids[0]])
    op = SemanticOperation.from_node(by[block.operation_ids[0]])
    broken = deepcopy(container)
    target = _node(broken, op.id)
    replacement = SemanticOperation(
        op.id,
        op.name,
        op.parent,
        op.opcode,
        op.index,
        op.input_types,
        op.result_types,
        (),
        op.result_value_ids,
        op.status_value_id,
        Effect(op.effects),
        op.refusal_set_id,
        op.callee_id,
        op.target_object_id,
        op.target_field_id,
    ).to_node(STDLIB_SEMANTIC_OPERATION_TYPE)
    target.data, target.refs = replacement.data, replacement.refs
    errors = verify_semantic_graphs(
        broken,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_BLOCK_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
        STDLIB_SEMANTIC_VALUE_TYPE,
    )
    assert any("typed arity lacks values" in error for error in errors)
    broken = deepcopy(container)
    target = _node(broken, block.id)
    replacement = SemanticBlock(
        block.id,
        block.name,
        block.parent,
        block.index,
        block.terminator,
        block.operation_ids,
        block.predecessor_ids,
        block.successor_ids,
        block.parameter_types,
        block.parameter_value_ids,
        block.condition_value_id,
        block.terminator_value_id,
        tuple(() for _ in block.successor_ids),
        block.case_tags,
    ).to_node(STDLIB_SEMANTIC_BLOCK_TYPE)
    target.data, target.refs = replacement.data, replacement.refs
    errors = verify_semantic_graphs(
        broken,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_BLOCK_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
        STDLIB_SEMANTIC_VALUE_TYPE,
    )
    assert any("phi argument mismatch" in error for error in errors)


def test_ssa_verifier_rejects_result_definition_and_literal_corruption():
    container = starter_template().build()
    graph = _graphs(container)["build_receipt"]
    by = {n.id: n for n in container.nodes}
    value = SemanticValue.from_node(by[graph.result_value_ids[0]])
    broken = deepcopy(container)
    target = _node(broken, value.id)
    replacement = SemanticValue(
        value.id,
        value.name,
        value.parent,
        value.type_id,
        value.kind,
        value.owner_function_id,
        value.owner_block_id,
        0,
        value.result_index,
        value.source_id,
        value.literal,
    ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
    target.data, target.refs = replacement.data, replacement.refs
    errors = verify_semantic_graphs(
        broken,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_BLOCK_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
        STDLIB_SEMANTIC_VALUE_TYPE,
    )
    assert any("result backref" in error for error in errors)
    literal = next(
        SemanticValue.from_node(n)
        for n in container.nodes
        if n.type_id == STDLIB_SEMANTIC_VALUE_TYPE and n.name == "capacity"
    )
    broken = deepcopy(container)
    target = _node(broken, literal.id)
    replacement = SemanticValue(
        literal.id,
        literal.name,
        literal.parent,
        literal.type_id,
        literal.kind,
        literal.owner_function_id,
        literal.owner_block_id,
        literal.owner_operation_id,
        literal.result_index,
        literal.source_id,
        b"",
    ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
    target.data, target.refs = replacement.data, replacement.refs
    errors = verify_semantic_graphs(
        broken,
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_BLOCK_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
        STDLIB_SEMANTIC_VALUE_TYPE,
    )
    assert any("literal is empty" in error for error in errors)


def test_all_operational_calls_match_persisted_callee_signatures():
    from pymergetic.rxf.compiler.semantic import (
        classify_semantic_operations,
        normalize_semantic_graph,
    )

    container = starter_template().build()
    graphs = _graphs(container)
    assert len(graphs) == 53
    classified_calls = 0
    for graph in graphs.values():
        normalized = normalize_semantic_graph(container, graph.function_id)
        classify_semantic_operations(container, normalized)
        classified_calls += sum(
            operation.callee_id != 0
            for block in normalized.blocks
            for operation in block.operations
        )
    assert classified_calls > 0


def test_signature_classifier_rejects_missing_reordered_wrong_and_extra_operands():
    from pymergetic.rxf.compiler.normalize import CompileError
    from pymergetic.rxf.compiler.semantic import (
        classify_semantic_operations,
        normalize_semantic_graph,
    )

    container = starter_template().build()
    graph = _graphs(container)["heap_allocate"]
    by = {n.id: n for n in container.nodes}
    block = SemanticBlock.from_node(by[graph.block_ids[0]])
    op = next(
        SemanticOperation.from_node(by[x])
        for x in block.operation_ids
        if SemanticOperation.from_node(by[x]).callee_id
    )
    variants = (
        op.input_value_ids[:-1],
        tuple(reversed(op.input_value_ids)),
        op.input_value_ids + (op.input_value_ids[0],),
    )
    for inputs in variants:
        broken = deepcopy(container)
        target = _node(broken, op.id)
        replacement = SemanticOperation(
            op.id,
            op.name,
            op.parent,
            op.opcode,
            op.index,
            tuple([op.input_types[0]] * len(inputs)),
            op.result_types,
            inputs,
            op.result_value_ids,
            op.status_value_id,
            Effect(op.effects),
            op.refusal_set_id,
            op.callee_id,
            op.target_object_id,
            op.target_field_id,
        ).to_node(STDLIB_SEMANTIC_OPERATION_TYPE)
        target.data, target.refs = replacement.data, replacement.refs
        try:
            classify_semantic_operations(
                broken, normalize_semantic_graph(broken, graph.function_id)
            )
        except CompileError:
            pass
        else:
            assert False


def test_executable_classifier_has_zero_null_refs_and_receipt_literal_flow():
    from pymergetic.rxf.compiler.semantic import (
        classify_semantic_operations,
        normalize_semantic_graph,
    )

    container = starter_template().build()
    graphs = _graphs(container)
    for graph in graphs.values():
        classify_semantic_operations(
            container, normalize_semantic_graph(container, graph.function_id)
        )
    receipt = normalize_semantic_graph(container, graphs["build_receipt"].function_id)
    append = next(
        op
        for block in receipt.blocks
        for op in block.operations
        if op.opcode == SemanticOpcode.APPEND_BYTES
    )
    format_op = next(
        op
        for block in receipt.blocks
        for op in block.operations
        if op.opcode == SemanticOpcode.FORMAT_INTEGER
    )
    assert any(value.source_id == 90007 for value in append.inputs)
    assert any(
        int.from_bytes(value.literal, "little") == 570 for value in format_op.inputs
    )
    assert all(
        not (value.type.id == 12 and value.literal == bytes(24) and not value.source_id)
        for block in receipt.blocks
        for op in block.operations
        for value in op.inputs
    )


def test_search_calls_persist_exact_ordered_handles_and_lengths():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph

    container = starter_template().build()
    graphs = _graphs(container)
    string_graph = normalize_semantic_graph(
        container, graphs["string_find"].function_id
    )
    string_search = next(
        op
        for block in string_graph.blocks
        for op in block.operations
        if op.opcode == SemanticOpcode.SEARCH
    )
    assert [value.type.id for value in string_search.inputs] == [12, 8, 12, 8, 12]
    assert [value.id for value in string_search.inputs[:4]] == list(
        graphs["string_find"].parameter_value_ids[:4]
    )

    receipt = normalize_semantic_graph(container, graphs["build_receipt"].function_id)
    receipt_search = next(
        op
        for block in receipt.blocks
        for op in block.operations
        if op.opcode == SemanticOpcode.SEARCH
    )
    assert [value.id for value in receipt_search.inputs] == [
        90101,
        90088,
        90061,
        90064,
        90089,
    ]
    assert receipt_search.inputs[2].source_id == 90007


def test_allocate_results_and_receipt_transaction_chain_are_exact():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph

    container = starter_template().build()
    graphs = _graphs(container)
    allocations = []
    for graph in graphs.values():
        normalized = normalize_semantic_graph(container, graph.function_id)
        allocations += [
            op
            for block in normalized.blocks
            for op in block.operations
            if op.opcode == SemanticOpcode.ALLOCATE
        ]
    assert len(allocations) == 3 and all(
        op.results[0].type.id == 12 and op.results[0].type.width == 24
        for op in allocations
    )
    receipt = normalize_semantic_graph(container, graphs["build_receipt"].function_id)
    operations = [op for block in receipt.blocks for op in block.operations]
    begin = next(op for op in operations if op.opcode == SemanticOpcode.BEGIN_PRIVATE)
    transaction = begin.results[0]
    assert all(
        op.inputs[0].id == transaction.id
        for op in operations
        if op.opcode
        in {SemanticOpcode.PUBLISH, SemanticOpcode.ROLLBACK, SemanticOpcode.CLEANUP}
    )
    formatter = next(
        op for op in operations if op.opcode == SemanticOpcode.FORMAT_INTEGER
    )
    assert (
        formatter.inputs[0].id == 90101
        and formatter.inputs[1].source_id == 90090
        and int.from_bytes(formatter.inputs[2].literal, "little") == 570
    )
    receipt_opcodes = [op.opcode for op in operations]
    assert SemanticOpcode.WRITE not in receipt_opcodes
    assert receipt_opcodes.count(SemanticOpcode.APPEND_BYTES) == 2
    appends = [op for op in operations if op.opcode == SemanticOpcode.APPEND_BYTES]
    assert [op.inputs[2].source_id for op in appends] == [90007, 90008]
    assert all(op.inputs[1].source_id == 90090 for op in appends)


def test_every_fallible_call_has_immediate_uint32_status_branch():
    container = starter_template().build()
    graphs = _graphs(container)
    checked = []
    for graph in graphs.values():
        for block_id in graph.block_ids:
            block = SemanticBlock.from_node(_node(container, block_id))
            for operation_id in block.operation_ids:
                operation = SemanticOperation.from_node(_node(container, operation_id))
                if operation.status_value_id:
                    status = SemanticValue.from_node(
                        _node(container, operation.status_value_id)
                    )
                    assert status.type_id == 7
                    assert block.terminator.name == "BRANCH"
                    assert block.condition_value_id == status.id
                    assert block.operation_ids[-1] == operation.id
                    checked.append(operation.id)
    assert checked

    receipt = graphs["build_receipt"]
    receipt_blocks = [
        SemanticBlock.from_node(_node(container, block_id))
        for block_id in receipt.block_ids
    ]
    receipt_fallible = [
        SemanticOperation.from_node(_node(container, operation_id))
        for block in receipt_blocks
        for operation_id in block.operation_ids
        if SemanticOperation.from_node(_node(container, operation_id)).status_value_id
    ]
    assert any(
        operation.opcode == SemanticOpcode.BEGIN_PRIVATE
        for operation in receipt_fallible
    )
    refusal = next(
        block for block in receipt_blocks if block.terminator.name == "REFUSE"
    )
    refusal_status = SemanticValue.from_node(
        _node(container, refusal.terminator_value_id)
    )
    assert (
        refusal_status.type_id == 7
        and refusal_status.id == refusal.parameter_value_ids[0]
    )


def test_tagged_operations_use_specialized_union_fields():
    from pymergetic.rxf.model.stdlib_corpus import (
        OPTION_REF_TYPE,
        OPTION_U64_TYPE,
        TYPED_RESULT_U64_U32_TYPE,
    )
    from pymergetic.rxf.ty.builtins import FIELD_TYPE, U32_TYPE
    from pymergetic.rxf.ty.objects import FieldObject, TypeForm, TypeObject

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    tagged_ops = [
        SemanticOperation.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
        and SemanticOperation.from_node(node).opcode
        in {
            SemanticOpcode.READ_TAG,
            SemanticOpcode.PROJECT_PAYLOAD,
            SemanticOpcode.CONSTRUCT_OPTION,
            SemanticOpcode.CONSTRUCT_RESULT,
        }
    ]
    assert sum(op.opcode == SemanticOpcode.READ_TAG for op in tagged_ops) == 12
    assert all(
        op.target_field_id and by_id[op.target_field_id].type_id == FIELD_TYPE
        for op in tagged_ops
    )
    for op in tagged_ops:
        tagged_type = (
            op.input_types[0]
            if op.opcode in {SemanticOpcode.READ_TAG, SemanticOpcode.PROJECT_PAYLOAD}
            else op.result_types[0]
        )
        assert tagged_type in {
            OPTION_U64_TYPE,
            OPTION_REF_TYPE,
            TYPED_RESULT_U64_U32_TYPE,
        }
        descriptor = TypeObject.from_payload(
            id=tagged_type,
            name=by_id[tagged_type].name,
            payload=by_id[tagged_type].data,
        )
        field = FieldObject.from_payload(
            id=op.target_field_id,
            name=by_id[op.target_field_id].name,
            payload=by_id[op.target_field_id].data,
        )
        assert descriptor.form == TypeForm.UNION and field.owner_type == tagged_type
        if op.opcode == SemanticOpcode.READ_TAG:
            assert (
                field.offset == 0
                and field.value_type == U32_TYPE
                and op.result_types == (U32_TYPE,)
            )


def test_canonical_tagged_layouts_are_exact_and_roundtrip():
    from pymergetic.rxf.model.stdlib import TaggedVariant
    from pymergetic.rxf.model.stdlib_corpus import (
        OPTION_REF_TYPE,
        OPTION_U32_TYPE,
        OPTION_U64_TYPE,
        STDLIB_TAGGED_VARIANT_TYPE,
        TYPED_RESULT_U64_U32_TYPE,
    )
    from pymergetic.rxf.ty.objects import TypeObject

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    expected = {
        OPTION_U32_TYPE: (8, 4),
        OPTION_U64_TYPE: (16, 8),
        OPTION_REF_TYPE: (32, 8),
        TYPED_RESULT_U64_U32_TYPE: (16, 8),
    }
    for type_id, geometry in expected.items():
        descriptor = TypeObject.from_payload(
            id=type_id, name=by_id[type_id].name, payload=by_id[type_id].data
        )
        assert (descriptor.size, descriptor.align) == geometry
        variants = [
            TaggedVariant.from_node(node)
            for node in container.nodes
            if node.type_id == STDLIB_TAGGED_VARIANT_TYPE and node.parent == type_id
        ]
        assert {variant.tag_value for variant in variants} == {0, 1}
        assert all(
            TaggedVariant.from_node(variant.to_node(STDLIB_TAGGED_VARIANT_TYPE))
            == variant
            for variant in variants
        )


def test_refusal_transforms_have_typed_authority_and_actual_inputs():
    from pymergetic.rxf.model.stdlib_corpus import (
        STDLIB_REFUSAL_ENRICHMENT_TYPE,
        STDLIB_REFUSAL_MAPPING_TYPE,
        TYPED_RESULT_U64_U32_TYPE,
    )
    from pymergetic.rxf.model.stdlib_semantics import SemanticValueKind

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    transforms = [
        SemanticOperation.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
        and SemanticOperation.from_node(node).opcode
        in {SemanticOpcode.MAP_REFUSAL, SemanticOpcode.ENRICH_REFUSAL}
    ]
    assert len(transforms) == 3
    assert all(
        op.input_types == (TYPED_RESULT_U64_U32_TYPE,)
        and op.result_types == (TYPED_RESULT_U64_U32_TYPE,)
        for op in transforms
    )
    assert all(op.target_field_id and op.transform_authority_id for op in transforms)
    assert all(
        SemanticValue.from_node(by_id[op.input_value_ids[0]]).kind
        == SemanticValueKind.PARAMETER
        for op in transforms
    )
    assert {by_id[op.transform_authority_id].type_id for op in transforms} == {
        STDLIB_REFUSAL_MAPPING_TYPE,
        STDLIB_REFUSAL_ENRICHMENT_TYPE,
    }


def test_count_fold_sort_have_no_ambiguous_algorithm_ops():
    from pymergetic.rxf.ty.builtins import U64_TYPE

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    graphs = _graphs(container)

    def operations(name):
        return [
            SemanticOperation.from_node(by_id[operation_id])
            for block_id in graphs[name].block_ids
            for operation_id in SemanticBlock.from_node(by_id[block_id]).operation_ids
        ]

    for name in ("count", "fold", "stable_sort"):
        assert not any(
            op.opcode in {SemanticOpcode.ACCUMULATE, SemanticOpcode.SORT_INSERT}
            for op in operations(name)
        )
    for name in ("count", "fold"):
        callback = next(
            op for op in operations(name) if op.opcode == SemanticOpcode.CALL_CALLBACK
        )
        assert callback.callee_id == 10852 and callback.input_types == (
            U64_TYPE,
            U64_TYPE,
        )
        assert callback.status_value_id
    sort_operations = operations("stable_sort")
    assert {
        SemanticOpcode.READ,
        SemanticOpcode.WRITE,
        SemanticOpcode.MOVE_SLOT,
        SemanticOpcode.CALL_CALLBACK,
        SemanticOpcode.PUBLISH,
        SemanticOpcode.ROLLBACK,
    } <= {op.opcode for op in sort_operations}
    comparator = next(
        op for op in sort_operations if op.opcode == SemanticOpcode.CALL_CALLBACK
    )
    assert comparator.callee_id == 10812 and comparator.status_value_id


def test_branches_never_consume_payload_truthiness():
    from pymergetic.rxf.model.stdlib_semantics import TerminatorKind
    from pymergetic.rxf.ty.builtins import BOOL_TYPE, U32_TYPE

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    values = {
        node.id: SemanticValue.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_VALUE_TYPE
    }
    branches = [
        SemanticBlock.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_BLOCK_TYPE
        and SemanticBlock.from_node(node).terminator == TerminatorKind.BRANCH
    ]
    bool_branches = [
        block
        for block in branches
        if values[block.condition_value_id].type_id == BOOL_TYPE
    ]
    assert bool_branches
    for block in branches:
        condition = values[block.condition_value_id]
        assert condition.type_id in {BOOL_TYPE, U32_TYPE}
        if condition.type_id == U32_TYPE:
            operation = SemanticOperation.from_node(by_id[condition.owner_operation_id])
            assert block.operation_ids[-1] == operation.id
            assert operation.status_value_id == condition.id


def test_canonical_order_has_explicit_typed_traversal_authority():
    from copy import deepcopy
    from dataclasses import replace

    from pymergetic.rxf.model.stdlib import (
        CanonicalOrderAuthority,
        CanonicalOrderPolicy,
    )
    from pymergetic.rxf.model.stdlib_corpus import STDLIB_CANONICAL_ORDER_AUTHORITY_TYPE

    container = starter_template().build()
    operations = [
        SemanticOperation.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
        and SemanticOperation.from_node(node).opcode == SemanticOpcode.CANONICAL_ORDER
    ]
    assert len(operations) == 1
    operation = operations[0]
    authority_node = _node(container, operation.transform_authority_id)
    assert (
        authority_node is not None
        and authority_node.type_id == STDLIB_CANONICAL_ORDER_AUTHORITY_TYPE
    )
    authority = CanonicalOrderAuthority.from_node(authority_node)
    assert authority.policy == CanonicalOrderPolicy.ASCENDING_SLOT_INDEX
    assert authority.representation_preserving
    assert operation.input_types == (authority.input_type,)
    assert operation.result_types == (authority.output_type,)

    broken = deepcopy(container)
    broken_operation_node = _node(broken, operation.id)
    assert broken_operation_node is not None
    broken_operation = SemanticOperation.from_node(broken_operation_node)
    replacement = replace(broken_operation, transform_authority_id=0).to_node(
        STDLIB_SEMANTIC_OPERATION_TYPE
    )
    broken.nodes[broken.nodes.index(broken_operation_node)] = replacement
    assert any(
        "CANONICAL_ORDER authority is missing" in error
        for error in verify_semantic_graphs(
            broken,
            STDLIB_SEMANTIC_GRAPH_TYPE,
            STDLIB_SEMANTIC_BLOCK_TYPE,
            STDLIB_SEMANTIC_OPERATION_TYPE,
        )
    )


def test_hash_get_contains_use_authoritative_hash_then_lookup_pipeline():
    from pymergetic.rxf.model.stdlib import HashAuthority, HashKeyRepresentation
    from pymergetic.rxf.model.stdlib_corpus import STDLIB_HASH_AUTHORITY_TYPE

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    authority_node = next(
        node for node in container.nodes if node.type_id == STDLIB_HASH_AUTHORITY_TYPE
    )
    authority = HashAuthority.from_node(authority_node)
    assert authority.representation == HashKeyRepresentation.U64_LITTLE_ENDIAN_8
    assert authority.seed == 14695981039346656037 and authority.version == 1
    functions = {
        node.id: node.name for node in container.nodes if node.type_id == FUNCTION_TYPE
    }
    checked = []
    for graph in _graphs(container).values():
        owner = by_id[graph.function_id].name
        if owner not in {"hash_map_get", "hash_set_contains"}:
            continue
        operations = [
            SemanticOperation.from_node(by_id[operation_id])
            for block_id in graph.block_ids
            for operation_id in SemanticBlock.from_node(by_id[block_id]).operation_ids
        ]
        hash_op = next(
            operation
            for operation in operations
            if operation.opcode == SemanticOpcode.HASH
        )
        lookup = next(
            operation
            for operation in operations
            if operation.opcode == SemanticOpcode.LOOKUP
        )
        parameters = [
            SemanticValue.from_node(by_id[value]) for value in graph.parameter_value_ids
        ]
        assert hash_op.input_value_ids[0] == parameters[1].id
        seed = SemanticValue.from_node(by_id[hash_op.input_value_ids[1]])
        assert int.from_bytes(seed.literal, "little") == authority.seed
        assert functions[hash_op.callee_id] == "hash_u64"
        assert lookup.input_value_ids[0] == parameters[0].id
        assert lookup.input_value_ids[2] == hash_op.result_value_ids[0]
        assert lookup.input_value_ids[3] == parameters[1].id
        assert functions[lookup.callee_id] == (
            "runtime_hash_map_lookup"
            if owner == "hash_map_get"
            else "runtime_hash_set_lookup"
        )
        assert (
            hash_op.transform_authority_id
            == lookup.transform_authority_id
            == authority.id
        )
        tagged = [
            operation
            for operation in operations
            if operation.opcode == SemanticOpcode.READ_TAG
        ]
        assert tagged and tagged[0].input_value_ids == (lookup.result_value_ids[0],)
        checked.append(owner)
    assert set(checked) == {"hash_map_get", "hash_set_contains"}

    broken = deepcopy(container)
    node = next(
        node
        for node in broken.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
        and SemanticOperation.from_node(node).opcode == SemanticOpcode.LOOKUP
    )
    operation = replace(
        SemanticOperation.from_node(node), callee_id=authority.hash_function
    )
    broken.nodes[broken.nodes.index(node)] = operation.to_node(
        STDLIB_SEMANTIC_OPERATION_TYPE
    )
    assert any(
        "hash authority callee mismatch" in error
        for error in verify_semantic_graphs(
            broken,
            STDLIB_SEMANTIC_GRAPH_TYPE,
            STDLIB_SEMANTIC_BLOCK_TYPE,
            STDLIB_SEMANTIC_OPERATION_TYPE,
            STDLIB_SEMANTIC_VALUE_TYPE,
        )
    )


def test_transaction_pointer_inputs_never_use_persisted_literals():
    from pymergetic.rxf.model.stdlib_corpus import NATIVE_TRANSACTION_TYPE
    from pymergetic.rxf.model.stdlib_semantics import SemanticValueKind

    container = starter_template().build()
    by_id = {node.id: node for node in container.nodes}
    operations = [
        SemanticOperation.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
    ]
    transaction_inputs = [
        (operation, value)
        for operation in operations
        for value_id in operation.input_value_ids
        if value_id in by_id and by_id[value_id].type_id == STDLIB_SEMANTIC_VALUE_TYPE
        for value in (SemanticValue.from_node(by_id[value_id]),)
        if value.type_id == NATIVE_TRANSACTION_TYPE
    ]
    assert transaction_inputs
    assert all(
        value.kind != SemanticValueKind.LITERAL for _, value in transaction_inputs
    )

    broken = deepcopy(container)
    _operation, value = transaction_inputs[0]
    value_node = _node(broken, value.id)
    assert value_node is not None
    replacement = replace(
        value,
        kind=SemanticValueKind.LITERAL,
        literal=(0xDEADBEEF).to_bytes(8, "little") + bytes(40),
    ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
    broken.nodes[broken.nodes.index(value_node)] = replacement
    assert any(
        "transaction pointer input" in error and "persisted literal" in error
        for error in verify_semantic_graphs(
            broken,
            STDLIB_SEMANTIC_GRAPH_TYPE,
            STDLIB_SEMANTIC_BLOCK_TYPE,
            STDLIB_SEMANTIC_OPERATION_TYPE,
            STDLIB_SEMANTIC_VALUE_TYPE,
        )
    )
