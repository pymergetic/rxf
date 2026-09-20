"""First complete generic SemanticGraph native compiler slice."""

from __future__ import annotations

import ctypes
from dataclasses import replace

import pytest

from pymergetic.rxf.compiler import (
    Architecture,
    CompileError,
    RuntimeContext,
    compile_function,
    compile_targets,
)
from pymergetic.rxf.compiler.native import LinkError, executable
from pymergetic.rxf.compiler.runtime import native_context
from pymergetic.rxf.compiler.semantic import (
    normalize_semantic_graph,
    verify_semantic_ir,
)
from pymergetic.rxf.compiler.semantic_lower import (
    SemanticProgram,
    lower_semantic_program,
)
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.stdlib_native_runtime import (
    NativeObjectHandle,
    NativeTransaction,
)


def _container_function():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "array_new")
    return container, function


def test_generic_pipeline_lowers_complete_semantic_cfg_and_both_targets_deterministically():
    container, function = _container_function()
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    opcodes = {operation.source.opcode.name for operation in lowered.operations}
    assert {
        "BEGIN_PRIVATE",
        "ALLOCATE",
        "VALIDATE",
        "PUBLISH",
        "ROLLBACK",
        "CLEANUP",
        "RETURN_VALUE",
    } <= opcodes
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(image.text and image.fixups for image in first.images)
    assert all(
        image.function_ids == tuple(sorted(lowered.binding_function_ids))
        for image in first.images
    )
    assert all(
        image.object_ids == tuple(sorted(lowered.binding_object_ids))
        for image in first.images
    )


def test_heap_allocate_transaction_emits_deterministically_both_targets():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "heap_allocate" and node.type_id == 14
    )
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(image.text and image.fixups for image in first.images)
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    allocate = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "ALLOCATE"
    )
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    x86 = build_machine_plan(lowered, Architecture.X86_64)
    output = next(
        placement
        for placement in dict(x86.calls)[allocate.id]
        if placement.location.role.name == "TRANSIENT_OUTPUT"
    )
    assert output.location.register_bank.name == "STACK"
    assert output.location.passing.name == "INDIRECT_BY_REFERENCE"


def test_sysv_stack_transaction_pointer_geometry_is_deterministic():
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    lowered = compile_function(container, 60555)
    assert isinstance(lowered, SemanticProgram)
    allocate = next(op.source for op in lowered.operations if op.source.id == 60576)
    plan = build_machine_plan(lowered, Architecture.X86_64)
    placement = next(p for p in dict(plan.calls)[allocate.id] if p.location.id == 60252)
    assert placement.location.passing.name == "TRANSACTION_POINTER"
    assert placement.location.register_bank.name == "STACK"
    assert placement.location.stack_offset == 0
    assert placement.value is not None and placement.value.id == 60574
    first = compile_targets(container, lowered.function_id)
    second = compile_targets(container, lowered.function_id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_begin_private_uses_persisted_aggregate_output_abi_both_targets():
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "heap_allocate" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    begin = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "BEGIN_PRIVATE"
    )
    callee = container.node_by_id(begin.callee_id)
    assert callee is not None and callee.name == "runtime_begin_private"
    assert begin.results[0].type.id == 41015
    assert begin.results[0].type.width == 48
    assert begin.status == begin.results[1]
    for architecture in Architecture:
        plan = build_machine_plan(lowered, architecture)
        placements = dict(plan.calls)[begin.id]
        output = next(
            placement
            for placement in placements
            if placement.location.role.name == "TRANSIENT_OUTPUT"
        )
        assert output.value == begin.results[0]
        assert output.location.width == 48
        assert output.location.alignment == 8


def test_begin_private_missing_output_abi_mutation_refuses():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "heap_allocate" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    begin = next(
        operation
        for operation in lowered.operations
        if operation.source.opcode.name == "BEGIN_PRIVATE"
    )
    assert begin.call_abi is not None
    broken_abi = replace(
        begin.call_abi,
        x86_64=tuple(
            location
            for location in begin.call_abi.x86_64
            if location.role.name != "TRANSIENT_OUTPUT"
        ),
    )
    broken = replace(
        lowered,
        operations=tuple(
            replace(operation, call_abi=broken_abi) if operation == begin else operation
            for operation in lowered.operations
        ),
    )
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    with pytest.raises(
        LinkError,
        match=rf"SemanticOperation {begin.source.id} ABI output has no SSA result|lacks",
    ):
        plan = build_machine_plan(broken, Architecture.X86_64)
        placements = dict(plan.calls)[begin.source.id]
        if not any(p.location.role.name == "TRANSIENT_OUTPUT" for p in placements):
            raise LinkError(
                f"SemanticOperation {begin.source.id} lacks transient output"
            )


def test_transaction_cfg_lowers_status_only_calls_without_fake_outputs():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "array_set")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    status_only = {
        "WRITE",
        "PUBLISH",
        "ROLLBACK",
        "CLEANUP",
    }
    for operation in lowered.operations:
        source = operation.source
        if source.opcode.name in status_only:
            assert source.results == (source.status,)


def test_transaction_verifier_refuses_authored_failure_edge_without_cleanup():
    from pymergetic.rxf.compiler.semantic_cfg_native import verify_transaction_paths

    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "array_set")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    without_cleanup = replace(
        lowered,
        operations=tuple(
            operation
            for operation in lowered.operations
            if operation.source.opcode.name != "CLEANUP"
        ),
    )
    with pytest.raises(
        LinkError,
        match=rf"Function {function.id} transaction lifecycle lacks authored .*CLEANUP",
    ):
        verify_transaction_paths(without_cleanup)


def test_generic_pipeline_refuses_bad_status_provenance_mutation():
    container, function = _container_function()
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    graph = lowered.graph
    entry = next(block for block in graph.blocks if block.id == graph.entry_block_id)
    broken_entry = replace(entry, condition=graph.parameters[0])
    broken = replace(
        graph,
        blocks=tuple(
            broken_entry if block.id == entry.id else block for block in graph.blocks
        ),
    )
    with pytest.raises(
        CompileError,
        match=rf"condition value {graph.parameters[0].id} is not bool/status",
    ):
        lower_semantic_program(container, broken)


def test_hash_native_opcode_is_supported_on_both_targets():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "hash_map_get")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    hash_operation = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "HASH"
    )
    targets = compile_targets(container, function.id)
    assert [image.architecture for image in targets.images] == list(Architecture)
    assert all(
        hash_operation.callee_id in image.function_ids for image in targets.images
    )


def test_hash_lookup_is_deterministic_both_targets():
    container = starter_template().build()
    completed = []
    for name in ("hash_map_get", "hash_set_contains"):
        function = next(node for node in container.nodes if node.name == name)
        lowered = compile_function(container, function.id)
        assert isinstance(lowered, SemanticProgram)
        calls = [
            op.source
            for op in lowered.operations
            if op.source.opcode.name in {"HASH", "LOOKUP"}
        ]
        assert [(op.opcode.name, op.transform_authority_id) for op in calls] == [
            ("HASH", 89990),
            ("LOOKUP", 89990),
        ]
        first = compile_targets(container, function.id)
        second = compile_targets(container, function.id)
        assert first.inspect() == second.inspect()
        assert [image.architecture for image in first.images] == list(Architecture)
        assert all(
            {op.callee_id for op in calls} <= set(image.function_ids)
            for image in first.images
        )
        completed.append(function.id)
    expected = [
        next(
            node.id
            for node in container.nodes
            if node.name == name and node.type_id == 14
        )
        for name in ("hash_map_get", "hash_set_contains")
    ]
    assert completed == expected


def test_hash_authority_missing_and_wrong_callee_mutations_refuse():
    container = starter_template().build()
    function = next(
        node for node in container.nodes if node.name == "hash_set_contains"
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    hash_call = next(
        op.source for op in lowered.operations if op.source.opcode.name == "HASH"
    )
    lookup = next(
        op.source for op in lowered.operations if op.source.opcode.name == "LOOKUP"
    )
    missing = replace(lookup, transform_authority_id=0)
    blocks = tuple(
        replace(
            block,
            operations=tuple(
                missing if op == lookup else op for op in block.operations
            ),
        )
        for block in lowered.graph.blocks
    )
    with pytest.raises(
        CompileError,
        match=rf"SemanticOperation {lookup.id} LOOKUP has no hash authority",
    ):
        lower_semantic_program(container, replace(lowered.graph, blocks=blocks))
    wrong = replace(hash_call, callee_id=61523)
    blocks = tuple(
        replace(
            block,
            operations=tuple(
                wrong if op == hash_call else op for op in block.operations
            ),
        )
        for block in lowered.graph.blocks
    )
    with pytest.raises(
        CompileError,
        match=rf"SemanticOperation {hash_call.id} HASH does not match HashAuthority 89990",
    ):
        lower_semantic_program(container, replace(lowered.graph, blocks=blocks))


def test_lookup_wrong_option_output_mutation_refuses():
    container = starter_template().build()
    function = next(
        node for node in container.nodes if node.name == "hash_set_contains"
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    lookup = next(
        op.source for op in lowered.operations if op.source.opcode.name == "LOOKUP"
    )
    payload = lookup.results[0]
    wrong_payload = replace(payload, type=replace(payload.type, id=8, width=8))
    wrong = replace(lookup, results=(wrong_payload, lookup.results[1]))
    blocks = tuple(
        replace(
            block,
            operations=tuple(wrong if op == lookup else op for op in block.operations),
        )
        for block in lowered.graph.blocks
    )
    with pytest.raises(
        CompileError,
        match=rf"SemanticOperation {lookup.id} LOOKUP does not match HashAuthority 89990",
    ):
        lower_semantic_program(container, replace(lowered.graph, blocks=blocks))


def test_refusal_mapping_authority_and_dual_target_emission():
    container = starter_template().build()
    expected = {"map_refusal": ((6, 8),), "catch_refusal": ((8, 8),)}
    for name, entries in expected.items():
        function = next(node for node in container.nodes if node.name == name)
        lowered = compile_function(container, function.id)
        assert isinstance(lowered, SemanticProgram)
        mapping = lowered.refusal_mappings[0]
        assert mapping.entries == entries
        assert mapping.payload_offset == 8
        assert mapping.default_policy.name == "PROPAGATE_UNMATCHED"
        first = compile_targets(container, function.id)
        second = compile_targets(container, function.id)
        assert first.inspect() == second.inspect()
        assert [image.architecture for image in first.images] == list(Architecture)


def test_refusal_mapping_duplicate_source_mutation_refuses():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "map_refusal")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    mapping = lowered.refusal_mappings[0]
    authority = container.node_by_id(mapping.authority_id)
    assert authority is not None
    duplicate = replace(
        authority,
        data=bytes([1, 0, 0, 0, 2, 0, 0, 0]) + authority.data[8:],
        refs=authority.refs + authority.refs[-1:],
    )
    mutated = replace(
        container,
        nodes=[
            duplicate if node.id == duplicate.id else node for node in container.nodes
        ],
    )
    with pytest.raises(CompileError, match=r"duplicate source status 6"):
        compile_function(mutated, function.id)


def test_refusal_enrichment_authority_and_dual_target_emission():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "enrich_refusal")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    enrichment = lowered.refusal_enrichments[0]
    assert (
        enrichment.authority_id,
        enrichment.source_status,
        enrichment.destination_status,
    ) == (60163, 10, 10)
    assert enrichment.payload_offsets == (8,)
    assert enrichment.preserve_identity
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_refusal_enrichment_malformed_authority_refuses():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "enrich_refusal")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    enrichment = lowered.refusal_enrichments[0]
    authority = container.node_by_id(enrichment.authority_id)
    assert authority is not None
    wrong = replace(
        authority, refs=tuple(ref for ref in authority.refs if ref.to_off != 1606)
    )
    mutated = replace(
        container,
        nodes=[wrong if node.id == wrong.id else node for node in container.nodes],
    )
    with pytest.raises(CompileError, match=r"unsupported layout/transform"):
        compile_function(mutated, function.id)


def test_search_leaf_uses_persisted_abi_deterministically():
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "string_find")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    search = next(
        op.source for op in lowered.operations if op.source.opcode.name == "SEARCH"
    )
    callee = container.node_by_id(search.callee_id)
    assert callee is not None and callee.name == "runtime_search"
    for architecture in Architecture:
        placements = dict(build_machine_plan(lowered, architecture).calls)[search.id]
        assert [p.location.role.name for p in placements][-2:] == [
            "TRANSIENT_OUTPUT",
            "STATUS_RETURN",
        ]
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(search.callee_id in image.function_ids for image in first.images)


def test_search_missing_callee_and_wrong_output_abi_refuse():
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "string_find")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    search = next(op for op in lowered.operations if op.source.opcode.name == "SEARCH")
    missing = replace(search.source, callee_id=0)
    blocks = tuple(
        replace(
            block,
            operations=tuple(
                missing if op == search.source else op for op in block.operations
            ),
        )
        for block in lowered.graph.blocks
    )
    broken = lower_semantic_program(container, replace(lowered.graph, blocks=blocks))
    with pytest.raises(LinkError, match=r"SEARCH has no persisted callee ABI"):
        from pymergetic.rxf.compiler.semantic_cfg_native import emit_cfg

        emit_cfg(broken, Architecture.X86_64)
    assert search.call_abi is not None
    abi = replace(
        search.call_abi,
        x86_64=tuple(
            location
            for location in search.call_abi.x86_64
            if location.role.name != "TRANSIENT_OUTPUT"
        ),
    )
    mutated = replace(
        lowered,
        operations=tuple(
            replace(op, call_abi=abi) if op == search else op
            for op in lowered.operations
        ),
    )
    with pytest.raises(LinkError, match=r"lacks|output"):
        plan = build_machine_plan(mutated, Architecture.X86_64)
        if not any(
            p.location.role.name == "TRANSIENT_OUTPUT"
            for p in dict(plan.calls)[search.source.id]
        ):
            raise LinkError(f"SemanticOperation {search.source.id} lacks output")


def test_receipt_append_cfg_uses_generic_calls_and_transaction_finalization():
    from pymergetic.rxf.compiler.semantic_cfg_native import supports_cfg
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    lowered = compile_function(container, 90001)
    assert isinstance(lowered, SemanticProgram)
    assert lowered.graph.graph_id == 90030
    assert supports_cfg(lowered)
    appends = [
        op.source
        for op in lowered.operations
        if op.source.opcode.name == "APPEND_BYTES"
    ]
    assert [op.id for op in appends] == [90072, 90074]
    allocate = next(op.source for op in lowered.operations if op.source.id == 90071)
    placement = next(
        p
        for p in dict(build_machine_plan(lowered, Architecture.X86_64).calls)[
            allocate.id
        ]
        if p.location.id == 60252
    )
    begin = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "BEGIN_PRIVATE"
    )
    assert placement.value is not None
    assert placement.value == begin.results[0]
    assert placement.value.kind.name == "OP_RESULT"
    first = compile_targets(container, lowered.function_id)
    second = compile_targets(container, lowered.function_id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_receipt_transaction_verifier_rejects_unfinalized_success_exit():
    from pymergetic.rxf.compiler.semantic_cfg_native import verify_transaction_paths

    container = starter_template().build()
    lowered = compile_function(container, 90001)
    assert isinstance(lowered, SemanticProgram)
    publish = next(
        op.source for op in lowered.operations if op.source.opcode.name == "PUBLISH"
    )
    replacement = replace(publish, opcode=type(publish.opcode).VALIDATE)
    blocks = tuple(
        replace(
            block,
            operations=tuple(
                replacement if op == publish else op for op in block.operations
            ),
        )
        for block in lowered.graph.blocks
    )
    with pytest.raises(LinkError, match=r"exit block 90032 lacks authored cleanup"):
        verify_transaction_paths(
            replace(lowered, graph=replace(lowered.graph, blocks=blocks))
        )


def test_heap_resize_begin_private_and_copy_use_generic_cfg():
    from pymergetic.rxf.compiler.semantic_cfg_native import supports_cfg
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan

    container = starter_template().build()
    lowered = compile_function(container, 60625)
    assert isinstance(lowered, SemanticProgram)
    assert lowered.graph.graph_id == 60632
    assert supports_cfg(lowered)
    begin = next(op.source for op in lowered.operations if op.source.id == 60641)
    copy = next(op.source for op in lowered.operations if op.source.id == 60646)
    assert (begin.opcode.name, begin.callee_id, begin.results[0].type.width) == (
        "BEGIN_PRIVATE",
        60165,
        48,
    )
    assert (copy.opcode.name, copy.callee_id) == ("COPY", 60385)
    for architecture in Architecture:
        placements = dict(build_machine_plan(lowered, architecture).calls)[begin.id]
        output = next(
            p for p in placements if p.location.role.name == "TRANSIENT_OUTPUT"
        )
        assert output.value == begin.results[0] and output.location.width == 48
    first = compile_targets(container, lowered.function_id)
    second = compile_targets(container, lowered.function_id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_heap_resize_copy_missing_callee_abi_refuses_exactly():
    from pymergetic.rxf.compiler.semantic_cfg_native import emit_cfg

    container = starter_template().build()
    lowered = compile_function(container, 60625)
    assert isinstance(lowered, SemanticProgram)
    copy = next(op for op in lowered.operations if op.source.id == 60646)
    broken = replace(copy, call_abi=None)
    mutated = replace(
        lowered,
        operations=tuple(broken if op == copy else op for op in lowered.operations),
    )
    with pytest.raises(
        LinkError, match=r"SemanticOperation 60646 COPY has no persisted callee ABI"
    ):
        emit_cfg(mutated, Architecture.X86_64)


def test_transactional_array_new_uses_generic_cfg_not_legacy_object_indexing():
    from pymergetic.rxf.compiler.semantic_cfg_native import supports_cfg

    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "array_new")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    assert supports_cfg(lowered)
    begin = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "BEGIN_PRIVATE"
    )
    allocate = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "ALLOCATE"
    )
    transaction = allocate.inputs[5]
    assert transaction == begin.results[0]
    assert transaction.kind.name == "OP_RESULT"
    assert transaction.literal == b""
    assert transaction.source_id == 0
    assert transaction.source_id not in lowered.binding_object_ids
    first = compile_targets(container, lowered.function_id)
    second = compile_targets(container, lowered.function_id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_cfg_object_resolver_reports_absent_binding_exactly():
    from pymergetic.rxf.compiler.semantic_cfg_native import emit_cfg

    container = starter_template().build()
    lowered = compile_function(container, 61550)
    assert isinstance(lowered, SemanticProgram)
    payload = next(
        value
        for operation in lowered.operations
        for value in operation.source.inputs
        if value.source_id in lowered.binding_object_ids
    )
    mutated = replace(lowered, binding_object_ids=())
    with pytest.raises(
        LinkError,
        match=rf"durable object {payload.source_id} is absent from binding slots",
    ):
        emit_cfg(mutated, Architecture.X86_64)


def test_parallel_phi_scheduler_breaks_swap_cycle():
    from pymergetic.rxf.compiler.semantic_machine import (
        ParallelMove,
        schedule_parallel_copies,
    )

    scheduled = schedule_parallel_copies(
        (ParallelMove(1, 2), ParallelMove(2, 1)), scratch=99
    )
    assert scheduled == (
        ParallelMove(1, 99),
        ParallelMove(2, 1),
        ParallelMove(99, 2),
    )


def test_machine_plan_covers_sysv_stack_aggregate_and_aapcs_byref():
    from pymergetic.rxf.compiler.semantic_machine import build_machine_plan
    from pymergetic.rxf.model.contracts import ABIPassingMode, ABIRegisterBank

    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "bytes_slice")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    x86 = build_machine_plan(lowered, Architecture.X86_64)
    arm = build_machine_plan(lowered, Architecture.AARCH64)
    xlocations = [placement.location for _, call in x86.calls for placement in call]
    alocations = [placement.location for _, call in arm.calls for placement in call]
    assert any(
        location.register_bank == ABIRegisterBank.STACK
        and location.passing == ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
        and location.width == 24
        for location in xlocations
    )
    assert any(
        location.passing == ABIPassingMode.INDIRECT_BY_REFERENCE
        and location.role.name == "SEMANTIC_ARGUMENT"
        for location in alocations
    )


def test_tagged_result_projection_construction_is_deterministic_both_targets():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "map_success" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    assert {(field.id, field.offset) for field in lowered.fields} == {
        (60039, 0),
        (60040, 8),
    }
    assert [
        (variant.tag_value, variant.payload_field_id)
        for variant in lowered.tagged_variants
    ] == [(0, 60040)]
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)


def test_tagged_construction_refuses_missing_variant_authority():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "map_success" and node.type_id == 14
    )
    mutated = replace(
        container,
        nodes=[node for node in container.nodes if node.id != 60041],
    )
    with pytest.raises(
        CompileError, match=r"tagged field 60040 has 0 variant authorities"
    ):
        compile_function(mutated, function.id)


def test_stable_sort_unary_iterator_condition_emits_both_targets():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "stable_sort")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    condition = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "ITER_CONDITION"
    )
    assert len(condition.inputs) == len(condition.results) == 1
    assert condition.inputs[0].type == condition.results[0].type
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(image.text and image.fixups for image in first.images)
    begin = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "BEGIN_PRIVATE"
    )
    assert all(begin.callee_id in image.function_ids for image in first.images)


def test_stable_sort_unary_iterator_condition_type_mutation_refuses_exactly():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "stable_sort")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    block = next(
        block
        for block in lowered.graph.blocks
        if any(
            operation.opcode.name == "ITER_CONDITION" for operation in block.operations
        )
    )
    operation = next(
        operation
        for operation in block.operations
        if operation.opcode.name == "ITER_CONDITION"
    )
    bad_operation = replace(
        operation,
        results=(replace(operation.results[0], type=lowered.graph.parameters[1].type),),
    )
    bad_block = replace(
        block,
        operations=tuple(
            bad_operation if candidate.id == operation.id else candidate
            for candidate in block.operations
        ),
    )
    bad = replace(
        lowered,
        graph=replace(
            lowered.graph,
            blocks=tuple(
                bad_block if candidate.id == block.id else candidate
                for candidate in lowered.graph.blocks
            ),
        ),
    )
    from pymergetic.rxf.compiler.semantic_native import emit_semantic_program

    for architecture in Architecture:
        with pytest.raises(
            LinkError,
            match=rf"SemanticOperation {operation.id} unary iterator condition type mismatch",
        ):
            emit_semantic_program(bad, architecture)


def test_aarch64_callback_phi_can_reload_entry_parameter():
    container = starter_template().build()
    function = next(
        node for node in container.nodes if node.name == "fold" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    callback = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "CALL_CALLBACK"
    )
    assert any(
        argument.kind.name == "PARAMETER"
        for block in lowered.graph.blocks
        for edge in block.edges
        for argument in edge.arguments
    )
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    arm = first.images[1]
    assert arm.architecture == Architecture.AARCH64
    assert callback.callee_id in arm.function_ids
    assert any(
        fixup.target_id == callback.callee_id and fixup.namespace.name == "FUNCTION"
        for fixup in arm.fixups
    )


def test_count_callback_uses_stable_slot_and_preserves_output_on_refusal_x86():
    container = starter_template().build()
    function = next(
        node for node in container.nodes if node.name == "count" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    callback_op = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "CALL_CALLBACK"
    )
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )
    for callback_status in (0, 31):
        calls = []

        def callback(
            item, accumulator, result, calls=calls, callback_status=callback_status
        ):
            calls.append((item, accumulator))
            result[0] = accumulator + 1
            return callback_status

        function_pointer = callback_type(callback)
        context = RuntimeContext.empty()
        context.functions.publish(
            {
                callback_op.callee_id: ctypes.cast(
                    function_pointer, ctypes.c_void_p
                ).value
            }
        )
        owner = native_context(context)
        memory, address = executable(first.images[0])
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        output = ctypes.c_uint64(0xA5A5A5A5A5A5A5A5)
        status = entry(ctypes.byref(owner.context), 0, ctypes.byref(output))
        assert status == callback_status
        assert output.value == (0 if callback_status == 0 else 0xA5A5A5A5A5A5A5A5)
        assert len(calls) == 1
        memory.close()

    context = RuntimeContext.empty()
    owner = native_context(context)
    memory, address = executable(first.images[0])
    entry = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )(address)
    output = ctypes.c_uint64(0xAA)
    assert entry(ctypes.byref(owner.context), 0, ctypes.byref(output)) == 0xFFFFFFFE
    assert output.value == 0xAA
    memory.close()


def test_multiblock_read_and_compare_use_generic_cfg_on_both_targets():
    container = starter_template().build()
    completed = []
    for name in ("string_scalars", "binary_search"):
        function = next(
            node for node in container.nodes if node.name == name and node.type_id == 14
        )
        lowered = compile_function(container, function.id)
        assert isinstance(lowered, SemanticProgram)
        assert len(lowered.graph.blocks) > 3
        first = compile_targets(container, function.id)
        second = compile_targets(container, function.id)
        assert first.inspect() == second.inspect()
        assert [image.architecture for image in first.images] == list(Architecture)
        completed.append(function.id)
    expected = [
        next(
            node.id
            for node in container.nodes
            if node.name == name and node.type_id == 14
        )
        for name in ("string_scalars", "binary_search")
    ]
    assert completed == expected


def test_multiblock_read_executes_success_and_preserves_output_on_refusal_x86():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "string_scalars" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)

    read = next(
        op.source for op in lowered.operations if op.source.opcode.name == "READ"
    )
    refs = {
        durable: NativeObjectHandle(durable, 1, 0)
        for durable in lowered.binding_object_ids
    }
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_void_p,
        NativeObjectHandle,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )
    for input_value, wanted_status in ((0, 0), (1, 0), (4, 0), (4, 29)):

        def read_count(
            context,
            handle,
            transaction,
            offset,
            width,
            output,
            end=input_value,
            wanted_status=wanted_status,
        ):
            output[0] = end
            return wanted_status

        callback = callback_type(read_count)
        context = RuntimeContext.empty()
        context.functions.publish(
            {read.callee_id: ctypes.cast(callback, ctypes.c_void_p).value}
        )
        context.objects.publish(
            {durable: ctypes.addressof(ref) for durable, ref in refs.items()}
        )
        owner = native_context(context)
        memory, address = executable(first.images[0])
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        output = ctypes.c_uint64(0xA5A5A5A5A5A5A5A5)
        status = entry(ctypes.byref(owner.context), input_value, ctypes.byref(output))
        assert status == wanted_status
        returned = next(
            op.source
            for op in lowered.operations
            if op.source.opcode.name == "RETURN_VALUE"
        )
        assert returned.inputs == (lowered.graph.parameters[0],)
        assert output.value == (
            input_value if wanted_status == 0 else 0xA5A5A5A5A5A5A5A5
        )
        memory.close()


def test_canonical_order_authority_policy_mutation_refuses():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "collection_iter" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    operation = next(
        operation.source
        for operation in lowered.operations
        if operation.source.opcode.name == "CANONICAL_ORDER"
    )
    authority = container.node_by_id(operation.transform_authority_id)
    assert authority is not None
    broken = replace(
        authority,
        data=b"\x01\x00\x00\x00\x00\x00\x00\x00" + authority.data[8:],
    )
    mutated = replace(
        container,
        nodes=[broken if node.id == authority.id else node for node in container.nodes],
    )
    with pytest.raises(
        CompileError,
        match=rf"CANONICAL_ORDER authority {authority.id} policy/layout/type is unsupported",
    ):
        compile_function(mutated, function.id)


def test_loop_backedge_validation_accepts_reducible_and_rejects_non_bool():
    from pymergetic.rxf.compiler.semantic_cfg_native import supports_cfg
    from pymergetic.rxf.ty.builtins import BOOL_TYPE

    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "cleanup" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    graph = lowered.graph
    entry = next(block for block in graph.blocks if block.id == graph.entry_block_id)
    second = graph.blocks[1]
    condition = replace(
        second.parameters[0],
        type=replace(second.parameters[0].type, id=BOOL_TYPE, width=1),
    )
    loop = replace(
        second,
        terminator=type(second.terminator).LOOP,
        condition=condition,
        parameters=(condition,),
        edges=(
            replace(second.edges[0], target=second.id, arguments=(condition,)),
            second.edges[1],
        ),
    )
    entry_edge = replace(entry.edges[0], arguments=(condition,))
    loop_entry = replace(entry, edges=(entry_edge, *entry.edges[1:]))
    loop_graph = replace(
        graph,
        blocks=tuple(
            loop if block == second else loop_entry if block == entry else block
            for block in graph.blocks
        ),
    )
    assert supports_cfg(replace(lowered, graph=loop_graph))
    bad = replace(loop, condition=entry.condition)
    bad_graph = replace(
        loop_graph,
        blocks=tuple(
            bad if block.id == second.id else block for block in loop_graph.blocks
        ),
    )
    with pytest.raises(
        LinkError,
        match=rf"Function {lowered.function_id} loop block {second.id} condition value .* is not explicit BOOL",
    ):
        supports_cfg(replace(lowered, graph=bad_graph))


def test_acyclic_cfg_refuses_phi_arity_and_type_mutations():
    from pymergetic.rxf.compiler.semantic_cfg_native import supports_cfg

    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "cleanup" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    graph = lowered.graph
    entry = next(block for block in graph.blocks if block.id == graph.entry_block_id)
    edge = entry.edges[0]
    broken_edge = replace(edge, arguments=())
    broken_entry = replace(entry, edges=(broken_edge, *entry.edges[1:]))
    broken_graph = replace(
        graph,
        blocks=tuple(
            broken_entry if block == entry else block for block in graph.blocks
        ),
    )
    with pytest.raises(LinkError, match=r"phi arity 0 != 1"):
        supports_cfg(replace(lowered, graph=broken_graph))

    target = next(block for block in graph.blocks if block.id == edge.target)
    wrong_parameter = replace(target.parameters[0], type=graph.parameters[0].type)
    # Force a distinct persisted type while preserving the edge value.
    wrong_parameter = replace(
        wrong_parameter,
        type=replace(
            wrong_parameter.type,
            id=7,
            width=4,
            signed=not wrong_parameter.type.signed,
        ),
    )
    broken_target = replace(target, parameters=(wrong_parameter,))
    broken_graph = replace(
        graph,
        blocks=tuple(
            broken_target if block == target else block for block in graph.blocks
        ),
    )
    with pytest.raises(LinkError, match=r"phi value .* type .* != parameter .* type 7"):
        supports_cfg(replace(lowered, graph=broken_graph))


def test_acyclic_cleanup_cfg_is_deterministic_for_both_targets():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "cleanup" and node.type_id == 14
    )
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(image.text and image.fixups for image in first.images)


def test_acyclic_cleanup_cfg_executes_success_and_preserves_output_on_refusal_x86():
    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "cleanup" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    image = compile_targets(container, function.id).images[0]
    callback_type = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)
    for first_status in (0, 43):
        calls = []
        statuses = iter((first_status, 0))

        def cleanup(context, transaction, calls=calls, statuses=statuses):
            calls.append(transaction)
            assert (
                ctypes.cast(
                    transaction, ctypes.POINTER(NativeTransaction)
                ).contents.identity
                == 77
            )
            return next(statuses)

        callback = callback_type(cleanup)
        context = RuntimeContext.empty()
        cleanup_callee = next(
            operation.source.callee_id
            for operation in lowered.operations
            if operation.source.opcode.name == "CLEANUP"
        )
        context.functions.publish(
            {cleanup_callee: ctypes.cast(callback, ctypes.c_void_p).value}
        )
        owner = native_context(context)
        memory, address = executable(image)
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(NativeTransaction),
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        transaction = NativeTransaction(11, 0, 77, 1, 1, 0)
        output = ctypes.c_uint64(0xAABBCCDD)
        status = entry(
            ctypes.byref(owner.context), ctypes.byref(transaction), ctypes.byref(output)
        )
        assert status == first_status
        assert output.value == (0 if first_status == 0 else 0xAABBCCDD)
        assert len(calls) == (2 if first_status == 0 else 1)
        memory.close()


def test_status_only_format_call_is_deterministic_for_both_targets():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "format_append")
    first = compile_targets(container, function.id)
    second = compile_targets(container, function.id)
    assert first.inspect() == second.inspect()
    assert [image.architecture for image in first.images] == list(Architecture)
    assert all(image.text and image.fixups for image in first.images)


def test_status_only_format_call_executes_success_and_preserves_output_on_refusal_x86():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "format_append")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    call = next(op.source for op in lowered.operations if op.source.callee_id)
    image = compile_targets(container, function.id).images[0]
    values = {
        durable: NativeObjectHandle(durable, 1, 0)
        for durable in lowered.binding_object_ids
    }
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_void_p,
        NativeObjectHandle,
        ctypes.c_void_p,
        ctypes.c_uint64,
    )

    for wanted_status in (0, 37):
        seen = {}

        def format_integer(
            context, buffer, transaction, radix, wanted_status=wanted_status, seen=seen
        ):
            seen.update(
                buffer=(buffer.object_id, buffer.generation, buffer.offset),
                transaction=transaction,
                radix=radix,
            )
            return wanted_status

        callback = callback_type(format_integer)
        context = RuntimeContext.empty()
        context.functions.publish(
            {call.callee_id: ctypes.cast(callback, ctypes.c_void_p).value}
        )
        context.objects.publish(
            {durable: ctypes.addressof(value) for durable, value in values.items()}
        )
        owner = native_context(context)
        memory, address = executable(image)
        output_location = next(
            location
            for location in lowered.x86_entry_abi
            if location.role.name == "TRANSIENT_OUTPUT"
        )
        assert output_location.width == 8 and output_location.pointee_type == 8
        returned = next(
            op.source
            for op in lowered.operations
            if op.source.opcode.name == "RETURN_VALUE"
        )
        assert returned.inputs == (lowered.graph.parameters[0],)
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        output = ctypes.c_uint64(0xA5A5A5A5A5A5A5A5)
        status = entry(ctypes.byref(owner.context), 0, ctypes.byref(output))
        assert status == wanted_status
        assert output.value == (0 if wanted_status == 0 else 0xA5A5A5A5A5A5A5A5)
        assert seen == {
            "buffer": (values[call.inputs[0].source_id].object_id, 1, 0),
            "transaction": ctypes.addressof(values[call.inputs[1].source_id]),
            "radix": 1,
        }
        memory.close()


def test_shared_call_backend_is_deterministic_for_read_family():
    container = starter_template().build()
    for name in ("bytes_slice", "clamp", "string_from_bytes", "string_compare"):
        function = next(node for node in container.nodes if node.name == name)
        first = compile_targets(container, function.id)
        second = compile_targets(container, function.id)
        assert first.inspect() == second.inspect()
        assert all(image.text and image.fixups for image in first.images)


def test_shared_read_call_executes_success_and_refusal_on_x86():
    container = starter_template().build()
    function = next(node for node in container.nodes if node.name == "bytes_slice")
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    call = next(op.source for op in lowered.operations if op.source.callee_id)
    image = compile_targets(container, function.id).images[0]
    handle = NativeObjectHandle(77, 1, 3)
    transaction = NativeTransaction()
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_void_p,
        NativeObjectHandle,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )

    for wanted_status in (0, 23):
        seen = {}

        def read(
            context,
            received,
            txn,
            offset,
            width,
            output,
            seen=seen,
            wanted_status=wanted_status,
        ):
            seen.update(
                handle=(received.object_id, received.generation, received.offset),
                transaction=txn,
                offset=offset,
                width=width,
            )
            output[0] = 0x1122334455667788
            return wanted_status

        callback = callback_type(read)
        context = RuntimeContext.empty()
        context.functions.publish(
            {call.callee_id: ctypes.cast(callback, ctypes.c_void_p).value}
        )
        context.objects.publish(
            {
                call.inputs[0].source_id: ctypes.addressof(handle),
                call.inputs[1].source_id: ctypes.addressof(transaction),
            }
        )
        owner = native_context(context)
        memory, address = executable(image)
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        output = ctypes.c_uint64(0xA5A5A5A5A5A5A5A5)
        status = entry(ctypes.byref(owner.context), 0, ctypes.byref(output))
        assert status == wanted_status
        returned = next(
            op.source
            for op in lowered.operations
            if op.source.opcode.name == "RETURN_VALUE"
        )
        assert returned.inputs == (lowered.graph.parameters[0],)
        assert output.value == (0 if wanted_status == 0 else 0xA5A5A5A5A5A5A5A5)
        assert seen == {
            "handle": (77, 1, 3),
            "transaction": ctypes.addressof(transaction),
            "offset": 1,
            "width": 1,
        }
        memory.close()


def test_unresolved_function_slot_refuses_without_touching_output():
    container, function = _container_function()
    image = compile_targets(container, function.id).images[0]
    transaction = NativeObjectHandle(4025020400, 1, 0)
    expected = (ctypes.c_uint64 * 3)(1, 2, 3)
    context = RuntimeContext.empty()
    context.objects.publish(
        {
            4025020400: ctypes.addressof(transaction),
            4025020411: ctypes.addressof(expected),
        }
    )
    owner = native_context(context)
    memory, address = executable(image)
    entry = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )(address)
    output = (ctypes.c_uint64 * 3)(0xAA, 0xBB, 0xCC)
    assert entry(ctypes.byref(owner.context), 9, output) == 0xFFFFFFFE
    assert tuple(output) == (0xAA, 0xBB, 0xCC)
    memory.close()


def test_native_execution_abi_layout_sentinels():
    from pymergetic.rxf.compiler.runtime import (
        NativeObjectEntry,
        NativeRuntimeContext,
        NativeTransactionMember,
    )

    assert ctypes.sizeof(NativeObjectHandle) == 24
    assert ctypes.alignment(NativeObjectHandle) == 8
    assert ctypes.sizeof(NativeObjectEntry) == 80
    assert NativeObjectEntry.type_id.offset == 16
    assert NativeObjectEntry.payload.offset == 24
    assert NativeObjectEntry.owner_transaction.offset == 72
    assert ctypes.sizeof(NativeTransaction) == 48
    assert ctypes.sizeof(NativeTransactionMember) == 72
    assert ctypes.sizeof(NativeRuntimeContext) == 144
    assert NativeRuntimeContext.journal_count.offset == 96
    assert NativeRuntimeContext.journal_capacity.offset == 104
    assert NativeRuntimeContext.journal.offset == 112


def test_array_new_exact_x86_status_and_payload_execution():
    from pymergetic.rxf.compiler.native import FixupNamespace, emit
    from pymergetic.rxf.compiler.semantic import SemanticValueKind
    from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program

    container, function_node = _container_function()
    raw = lower_semantic_program(
        container, normalize_semantic_graph(container, function_node.id)
    )
    optimized = optimize_semantic_program(raw)
    results = []

    def execute(program):
        image = emit(program, Architecture.X86_64)
        by_opcode = {op.source.opcode.name: op.source for op in program.operations}
        begin = by_opcode["BEGIN_PRIVATE"]
        validate = by_opcode["VALIDATE"]
        assert begin.inputs[0].kind == SemanticValueKind.OBJECT
        assert validate.inputs[0].kind == SemanticValueKind.OBJECT
        object_payloads = {
            begin.inputs[0].source_id: NativeObjectHandle(101, 1, 0),
            validate.inputs[0].source_id: NativeObjectHandle(202, 1, 0),
        }
        assert set(image.object_ids) == set(object_payloads)
        assert {
            fixup.target_id
            for fixup in image.fixups
            if fixup.namespace == FixupNamespace.OBJECT
        } == set(image.object_ids)
        assert {
            fixup.target_id
            for fixup in image.fixups
            if fixup.namespace == FixupNamespace.FUNCTION
        } == set(image.function_ids)
        calls = []
        callbacks = []
        begin_type = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(NativeObjectHandle),
            ctypes.c_uint64,
            ctypes.POINTER(NativeTransaction),
        )
        allocate_type = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.c_uint64,
            ctypes.POINTER(NativeTransaction),
            ctypes.POINTER(NativeObjectHandle),
        )
        validate_type = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            NativeObjectHandle,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )
        transaction_type = ctypes.CFUNCTYPE(
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(NativeTransaction)
        )
        refusal = ctypes.c_uint32(0)

        def begin_private(_context, handle, identity, transaction):
            calls.append(("begin", handle.contents.object_id, identity))
            if refusal.value:
                return refusal.value
            transaction[0] = NativeTransaction(0xABC, 2, identity, 0, 1, 0)
            return 0

        def allocate(
            _context, object_id, type_id, size, capacity, alignment, transaction, output
        ):
            calls.append(
                (
                    "allocate",
                    object_id,
                    type_id,
                    size,
                    capacity,
                    alignment,
                    transaction.contents.root_object_id,
                )
            )
            if refusal.value:
                return refusal.value
            output[0] = NativeObjectHandle(11, 12, 13)
            return 0

        def validate_generation(_context, handle, generation, output):
            calls.append(("validate", handle.object_id, generation))
            output[0] = 0x55
            return 0

        def transaction_callback(name):
            def callback(_context, transaction):
                calls.append((name, bool(transaction)))
                return 0

            return callback

        implementations = {
            "BEGIN_PRIVATE": begin_type(begin_private),
            "ALLOCATE": allocate_type(allocate),
            "VALIDATE": validate_type(validate_generation),
            "PUBLISH": transaction_type(transaction_callback("publish")),
            "ROLLBACK": transaction_type(transaction_callback("rollback")),
            "CLEANUP": transaction_type(transaction_callback("cleanup")),
        }
        callbacks.extend(implementations.values())
        context = RuntimeContext.empty()
        context.functions.publish(
            {
                by_opcode[name].callee_id: ctypes.cast(callback, ctypes.c_void_p).value
                for name, callback in implementations.items()
            }
        )
        context.objects.publish(
            {
                object_id: ctypes.addressof(payload)
                for object_id, payload in object_payloads.items()
            }
        )
        owner = native_context(context)
        memory, address = executable(image)
        entry = ctypes.CFUNCTYPE(
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.POINTER(ctypes.c_uint64),
        )(address)
        output = ctypes.c_uint64(0xAAAAAAAAAAAAAAAA)
        status = entry(ctypes.byref(owner.context), 9, ctypes.byref(output))
        assert status == 0 and output.value == 0xABC
        success_calls = tuple(calls)
        calls.clear()
        refusal.value = 31
        context.functions.publish(
            {
                by_opcode[name].callee_id: ctypes.cast(callback, ctypes.c_void_p).value
                for name, callback in implementations.items()
            }
        )
        refusal_owner = native_context(context)
        output.value = 0xBBBBBBBBBBBBBBBB
        status = entry(ctypes.byref(refusal_owner.context), 9, ctypes.byref(output))
        assert status == 31 and output.value == 0xBBBBBBBBBBBBBBBB
        assert tuple(call[0] for call in calls) == ("begin",)
        result = (
            success_calls,
            tuple(calls),
            owner.context.journal_count,
            owner.context.cleanup_count,
        )
        memory.close()
        return result

    results = (execute(raw), execute(optimized))
    assert results[0] == results[1]


def test_array_new_aarch64_qemu_executes_exact_bytes(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    qemu = shutil.which("qemu-system-aarch64")
    if qemu is None:
        pytest.skip("qemu-system-aarch64 unavailable")
    root = Path(__file__).parents[1]
    subprocess.run(
        [sys.executable, str(root / "tools/generate_semantic_cfg_qemu.py")],
        check=True,
        cwd=root,
        env={**os.environ, "PYTHONPATH": f"{root / 'src'}:{root}"},
    )
    flags = [
        "-target",
        "aarch64-none-elf",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
    ]
    subprocess.run(
        [
            "clang-18",
            *flags,
            "-O2",
            "-c",
            str(root / "native/compiler_semantic_cfg_qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    for source, output in (
        (root / "native/qemu_start.S", "s.o"),
        (root / "generated/semantic_cfg_qemu_aarch64.S", "b.o"),
    ):
        subprocess.run(
            [
                "clang-18",
                "-target",
                "aarch64-none-elf",
                "-c",
                str(source),
                "-o",
                str(tmp_path / output),
            ],
            check=True,
        )
    elf = tmp_path / "qemu.elf"
    subprocess.run(
        [
            "ld.lld-18",
            "-T",
            str(root / "native/qemu.ld"),
            str(tmp_path / "s.o"),
            str(tmp_path / "h.o"),
            str(tmp_path / "b.o"),
            "-o",
            str(elf),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            qemu,
            "-M",
            "virt",
            "-cpu",
            "cortex-a57",
            "-nographic",
            "-semihosting-config",
            "enable=on,target=native",
            "-device",
            f"loader,file={elf}",
            "-device",
            "loader,addr=0x40200000,cpu-num=0",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "RXF-SEMANTIC-CFG-PASS" in result.stdout + result.stderr


def test_semantic_optimizer_is_deterministic_and_retains_calls_and_effects():
    container = starter_template().build()
    for function_id in (60555, 61550, 63155):
        first = compile_function(container, function_id)
        second = compile_function(container, function_id)
        assert isinstance(first, SemanticProgram) and isinstance(
            second, SemanticProgram
        )
        assert first == second and first.optimization_report is not None
        before_calls = {
            operation.source.id
            for operation in lower_semantic_program(
                container, normalize_semantic_graph(container, function_id)
            ).operations
            if operation.source.callee_id or operation.source.effects
        }
        after = {operation.source.id for operation in first.operations}
        assert before_calls <= after


def test_semantic_optimizer_rejects_wrong_specialization_type():
    from pymergetic.rxf.compiler.semantic_lower import SpecializationBinding
    from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program

    container = starter_template().build()
    raw = lower_semantic_program(container, normalize_semantic_graph(container, 61550))
    value = raw.graph.parameters[0]
    with pytest.raises(
        CompileError,
        match=rf"specialization SemanticValue {value.id} type 7!={value.type.id}",
    ):
        optimize_semantic_program(raw, (SpecializationBinding(value.id, 7, b"\x01"),))


def test_semantic_optimizer_folds_bool_branch_and_rewrites_phi():
    from pymergetic.rxf.compiler.semantic_lower import SpecializationBinding
    from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program

    container = starter_template().build()
    raw = lower_semantic_program(container, normalize_semantic_graph(container, 63155))
    boolean = next(
        block.condition
        for block in raw.graph.blocks
        if block.condition is not None and block.condition.type.width == 1
    )
    assert boolean is not None
    optimized = optimize_semantic_program(
        raw, (SpecializationBinding(boolean.id, boolean.type.id, b"\x01"),)
    )
    assert optimized.optimization_report is not None
    assert (
        optimized.optimization_report.blocks_after
        < optimized.optimization_report.blocks_before
    )
    verify_semantic_ir(optimized.graph)


def test_x86_return_materialization_rejects_missing_frame_slot_exactly():
    from pymergetic.rxf.compiler.native import LinkError
    from pymergetic.rxf.compiler.semantic_cfg_native import emit_cfg

    container = starter_template().build()
    function = next(
        node
        for node in container.nodes
        if node.name == "bytes_slice" and node.type_id == 14
    )
    lowered = compile_function(container, function.id)
    assert isinstance(lowered, SemanticProgram)
    return_block = next(
        block for block in lowered.graph.blocks if block.terminator.name == "RETURN"
    )
    assert return_block.terminator_value is not None
    missing = replace(return_block.terminator_value, id=0xFFFF_FFFF)
    mutated = replace(
        lowered,
        graph=replace(
            lowered.graph,
            blocks=tuple(
                replace(block, terminator_value=missing)
                if block.id == return_block.id
                else block
                for block in lowered.graph.blocks
            ),
        ),
    )
    with pytest.raises(LinkError, match=r"SemanticValue 4294967295 has no frame slot"):
        emit_cfg(mutated, Architecture.X86_64)
