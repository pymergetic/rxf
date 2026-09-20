"""Typed normalization of persisted stdlib SemanticGraph SSA records."""

from __future__ import annotations

from dataclasses import dataclass

from pymergetic.rxf.compiler.ir import IREffect, TypeRef
from pymergetic.rxf.compiler.normalize import CompileError
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.stdlib_semantics import (
    SemanticBlock,
    SemanticGraph,
    SemanticOpcode,
    SemanticOperation,
    SemanticValue,
    SemanticValueKind,
    TerminatorKind,
)

GRAPH_TYPE = 41006
BLOCK_TYPE = 41007
OPERATION_TYPE = 41008
VALUE_TYPE = 41009


@dataclass(frozen=True)
class SemanticIRValue:
    id: int
    type: TypeRef
    kind: SemanticValueKind
    source_id: int
    literal: bytes
    owner_block_id: int
    owner_operation_id: int
    result_index: int


@dataclass(frozen=True)
class SemanticIROperation:
    id: int
    opcode: SemanticOpcode
    inputs: tuple[SemanticIRValue, ...]
    results: tuple[SemanticIRValue, ...]
    status: SemanticIRValue | None
    effects: IREffect
    refusal_set_id: int
    callee_id: int
    target_object_id: int
    target_field_id: int
    transform_authority_id: int


@dataclass(frozen=True)
class SemanticIREdge:
    target: int
    arguments: tuple[SemanticIRValue, ...]


@dataclass(frozen=True)
class SemanticIRBlock:
    id: int
    index: int
    parameters: tuple[SemanticIRValue, ...]
    operations: tuple[SemanticIROperation, ...]
    predecessors: tuple[int, ...]
    edges: tuple[SemanticIREdge, ...]
    terminator: TerminatorKind
    condition: SemanticIRValue | None
    terminator_value: SemanticIRValue | None
    case_tags: tuple[int, ...]

    @property
    def successors(self) -> tuple[int, ...]:
        return tuple(e.target for e in self.edges)


@dataclass(frozen=True)
class SemanticIRFunction:
    graph_id: int
    function_id: int
    entry_block_id: int
    parameters: tuple[SemanticIRValue, ...]
    results: tuple[SemanticIRValue, ...]
    output_type: TypeRef
    effects: IREffect
    refusal_set_id: int
    blocks: tuple[SemanticIRBlock, ...]
    semantic_digest: bytes

    @property
    def opcode_coverage(self) -> tuple[SemanticOpcode, ...]:
        return tuple(
            sorted({o.opcode for b in self.blocks for o in b.operations}, key=int)
        )


def _type_ref(container: Container, type_id: int) -> TypeRef:
    from pymergetic.rxf.compiler.compile import type_ref

    return type_ref(container, type_id)


def discover_semantic_graph(
    container: Container, function_id: int
) -> SemanticGraph | None:
    found = []
    for node in container.nodes:
        if node.type_id == GRAPH_TYPE:
            graph = SemanticGraph.from_node(node)
            if graph.function_id == function_id:
                found.append(graph)
    if len(found) > 1:
        raise CompileError(f"Function {function_id} has multiple SemanticGraphs")
    return found[0] if found else None


def normalize_semantic_graph(
    container: Container, function_id: int
) -> SemanticIRFunction:
    graph = discover_semantic_graph(container, function_id)
    if graph is None:
        raise CompileError(f"Function {function_id} has no SemanticGraph")
    by = {n.id: n for n in container.nodes}
    source_values = {}
    for node in container.nodes:
        if node.type_id == VALUE_TYPE:
            source_value = SemanticValue.from_node(node)
            if source_value.owner_function_id == function_id:
                source_values[source_value.id] = source_value

    def value(value_id: int) -> SemanticIRValue:
        source = source_values.get(value_id)
        if source is None:
            raise CompileError(f"SemanticGraph {graph.id} value {value_id} is missing")
        return SemanticIRValue(
            source.id,
            _type_ref(container, source.type_id),
            source.kind,
            source.source_id,
            source.literal,
            source.owner_block_id,
            source.owner_operation_id,
            source.result_index,
        )

    blocks = []
    for bid in graph.block_ids:
        node = by.get(bid)
        if node is None or node.type_id != BLOCK_TYPE:
            raise CompileError(f"SemanticGraph {graph.id} block {bid} is missing")
        source = SemanticBlock.from_node(node)
        operations = []
        for oid in source.operation_ids:
            onode = by.get(oid)
            if onode is None or onode.type_id != OPERATION_TYPE:
                raise CompileError(f"SemanticBlock {bid} operation {oid} is missing")
            op = SemanticOperation.from_node(onode)
            inputs = tuple(value(v) for v in op.input_value_ids)
            results = tuple(value(v) for v in op.result_value_ids)
            if (
                tuple(v.type.id for v in inputs) != op.input_types
                or tuple(v.type.id for v in results) != op.result_types
            ):
                raise CompileError(f"SemanticOperation {op.id} type/value disagreement")
            operations.append(
                SemanticIROperation(
                    op.id,
                    op.opcode,
                    inputs,
                    results,
                    value(op.status_value_id) if op.status_value_id else None,
                    IREffect(int(op.effects)),
                    op.refusal_set_id,
                    op.callee_id,
                    op.target_object_id,
                    op.target_field_id,
                    op.transform_authority_id,
                )
            )
        edges = tuple(
            SemanticIREdge(target, tuple(value(v) for v in args))
            for target, args in zip(
                source.successor_ids, source.successor_arguments, strict=True
            )
        )
        blocks.append(
            SemanticIRBlock(
                source.id,
                source.index,
                tuple(value(v) for v in source.parameter_value_ids),
                tuple(operations),
                source.predecessor_ids,
                edges,
                source.terminator,
                value(source.condition_value_id) if source.condition_value_id else None,
                value(source.terminator_value_id)
                if source.terminator_value_id
                else None,
                source.case_tags,
            )
        )
    result = SemanticIRFunction(
        graph.id,
        function_id,
        graph.entry_block_id,
        tuple(value(v) for v in graph.parameter_value_ids),
        tuple(value(v) for v in graph.result_value_ids),
        _type_ref(container, graph.output_type),
        IREffect(int(graph.effects)),
        graph.refusal_set_id,
        tuple(blocks),
        graph.semantic_digest(container),
    )
    verify_semantic_ir(result)
    return result


def verify_semantic_ir(function: SemanticIRFunction) -> None:
    blocks = {b.id: b for b in function.blocks}
    if function.entry_block_id not in blocks:
        raise CompileError("Semantic IR entry block is missing")
    if len(blocks) != len(function.blocks):
        raise CompileError("Semantic IR block IDs are not unique")
    if sorted(b.index for b in function.blocks) != list(range(len(blocks))):
        raise CompileError("Semantic IR block indices are not contiguous")
    predecessors = {bid: set() for bid in blocks}
    for block in function.blocks:
        if tuple(sorted(block.predecessors)) != block.predecessors:
            raise CompileError(
                f"SemanticBlock {block.id} predecessors are not canonical"
            )
        for edge in block.edges:
            if edge.target not in blocks:
                raise CompileError(f"SemanticBlock {block.id} edge leaves graph")
            predecessors[edge.target].add(block.id)
            target = blocks[edge.target]
            if len(edge.arguments) != len(target.parameters):
                raise CompileError(
                    f"SemanticBlock {block.id} phi argument count mismatch"
                )
            if tuple(v.type for v in edge.arguments) != tuple(
                v.type for v in target.parameters
            ):
                raise CompileError(
                    f"SemanticBlock {block.id} phi argument type mismatch"
                )
    for bid, preds in predecessors.items():
        if preds != set(blocks[bid].predecessors):
            raise CompileError(f"SemanticBlock {bid} predecessor disagreement")
    reachable = {function.entry_block_id}
    pending = [function.entry_block_id]
    while pending:
        for target in blocks[pending.pop()].successors:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    if reachable != set(blocks):
        raise CompileError(
            f"Semantic IR has unreachable blocks {sorted(set(blocks) - reachable)}"
        )
    dom = {
        bid: ({bid} if bid == function.entry_block_id else set(blocks))
        for bid in blocks
    }
    changed = True
    while changed:
        changed = False
        for bid in blocks:
            if bid == function.entry_block_id:
                continue
            incoming = predecessors[bid]
            new = {bid} | (
                set.intersection(*(dom[p] for p in incoming)) if incoming else set()
            )
            if new != dom[bid]:
                dom[bid] = new
                changed = True
    definitions = {v.id: function.entry_block_id for v in function.parameters}
    for block in function.blocks:
        for value in block.parameters:
            if (
                value.kind != SemanticValueKind.BLOCK_PARAMETER
                or value.owner_block_id != block.id
            ):
                raise CompileError(
                    f"SemanticValue {value.id} is not owned block parameter"
                )
            if value.id in definitions:
                raise CompileError(f"SemanticValue {value.id} has multiple definitions")
            definitions[value.id] = block.id
        for op in block.operations:
            for index, value in enumerate(op.results):
                if (
                    value.kind != SemanticValueKind.OP_RESULT
                    or value.owner_operation_id != op.id
                    or value.result_index != index
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} result backref mismatch"
                    )
                if value.id in definitions:
                    raise CompileError(
                        f"SemanticValue {value.id} has multiple definitions"
                    )
                definitions[value.id] = block.id
            if (
                op.opcode == SemanticOpcode.CALL_CALLBACK
                and not op.callee_id
                and not any(v.kind == SemanticValueKind.FUNCTION for v in op.inputs)
            ):
                raise CompileError(
                    f"SemanticOperation {op.id} callback has no Function binding"
                )
    for block in function.blocks:
        local = {v.id for v in block.parameters}
        for op in block.operations:
            for value in op.inputs:
                if value.kind in {
                    SemanticValueKind.PARAMETER,
                    SemanticValueKind.LITERAL,
                    SemanticValueKind.OBJECT,
                    SemanticValueKind.FUNCTION,
                }:
                    continue
                definition = definitions.get(value.id)
                if definition is None:
                    raise CompileError(
                        f"SemanticOperation {op.id} input {value.id} is undefined"
                    )
                if definition == block.id and value.id not in local:
                    raise CompileError(
                        f"SemanticOperation {op.id} input {value.id} precedes definition"
                    )
                if definition != block.id and definition not in dom[block.id]:
                    raise CompileError(
                        f"SemanticValue {value.id} does not dominate block {block.id}"
                    )
            local.update(v.id for v in op.results)
            if op.status:
                local.add(op.status.id)
        for used in (
            tuple(v for edge in block.edges for v in edge.arguments)
            + ((block.condition,) if block.condition else ())
            + ((block.terminator_value,) if block.terminator_value else ())
        ):
            if used.kind in {
                SemanticValueKind.PARAMETER,
                SemanticValueKind.LITERAL,
                SemanticValueKind.OBJECT,
                SemanticValueKind.FUNCTION,
            }:
                continue
            definition = definitions.get(used.id)
            if definition is None or (
                definition != block.id and definition not in dom[block.id]
            ):
                raise CompileError(
                    f"SemanticBlock {block.id} terminator/phi value {used.id} is not dominated"
                )
    if not function.results:
        raise CompileError(f"SemanticGraph {function.graph_id} has no result values")


def classify_semantic_operations(
    container: Container, function: SemanticIRFunction
) -> None:
    """Prove every operational opcode supplies its concrete callee ABI operands."""
    import struct

    from pymergetic.rxf.execution.decode import decode_function
    from pymergetic.rxf.model.contracts import ABIRole, ABIValueLocation, ContractRole
    from pymergetic.rxf.model.execution import CallRole
    from pymergetic.rxf.ty.builtins import ABI_VALUE_LOCATION_TYPE, REF_TYPE

    by = {n.id: n for n in container.nodes}
    null_pointer_operands: list[tuple[int, int]] = []
    compiler_only = {
        SemanticOpcode.READ_TAG,
        SemanticOpcode.PROJECT_PAYLOAD,
        SemanticOpcode.CONSTRUCT_OPTION,
        SemanticOpcode.CONSTRUCT_RESULT,
        SemanticOpcode.ITER_INIT,
        SemanticOpcode.ITER_CONDITION,
        SemanticOpcode.ITER_PROJECT,
        SemanticOpcode.ITER_ADVANCE,
        SemanticOpcode.ACCUMULATE,
        SemanticOpcode.SORT_INSERT,
        SemanticOpcode.EQUAL,
        SemanticOpcode.UTF8_BOUNDARY,
        SemanticOpcode.MAP_REFUSAL,
        SemanticOpcode.ENRICH_REFUSAL,
        SemanticOpcode.RETURN_VALUE,
        SemanticOpcode.RETURN_REFUSAL,
        SemanticOpcode.CHECK_BOUNDS,
        SemanticOpcode.CHECK_GENERATION,
        SemanticOpcode.CANONICAL_ORDER,
    }
    for block in function.blocks:
        for op in block.operations:
            if op.opcode in compiler_only:
                if op.callee_id:
                    raise CompileError(
                        f"SemanticOperation {op.id} compiler-only {op.opcode.name} unexpectedly has callee"
                    )
                continue
            if not op.callee_id:
                raise CompileError(
                    f"SemanticOperation {op.id} operational {op.opcode.name} has no callee Function"
                )
            callee = by.get(op.callee_id)
            if callee is None:
                raise CompileError(
                    f"SemanticOperation {op.id} callee Function {op.callee_id} is missing"
                )
            signature = by.get(decode_function(callee).signature_id)
            if signature is None:
                raise CompileError(
                    f"SemanticOperation {op.id} callee Signature is missing"
                )
            parameter_ids = tuple(
                r.target for r in signature.refs if r.to_off == int(CallRole.PARAMETER)
            )
            parameter_types = tuple(
                struct.unpack_from("<Q", by[v].data)[0] for v in parameter_ids
            )
            abi_nodes = [
                node
                for node in container.nodes
                if node.parent == op.callee_id
                and any(
                    ref.to_off == int(ContractRole.ABI_LOCATION) for ref in node.refs
                )
            ]
            hidden_associations = {
                location.association_id
                for abi_node in abi_nodes
                for ref in abi_node.refs
                if ref.to_off == int(ContractRole.ABI_LOCATION)
                and by[ref.target].type_id == ABI_VALUE_LOCATION_TYPE
                for location in (ABIValueLocation.from_node(by[ref.target]),)
                if location.role == ABIRole.HIDDEN_CONTEXT and location.hidden
            }
            semantic_types = tuple(
                typ
                for parameter_id, typ in zip(
                    parameter_ids, parameter_types, strict=True
                )
                if parameter_id not in hidden_associations
            )
            if tuple(v.type.id for v in op.inputs) != semantic_types:
                raise CompileError(
                    f"SemanticOperation {op.id} {op.opcode.name} callee ABI requires {semantic_types}, has {tuple(v.type.id for v in op.inputs)}"
                )
            for position, value in enumerate(op.inputs):
                if (
                    value.type.id == REF_TYPE
                    and value.kind == SemanticValueKind.LITERAL
                    and value.literal == bytes(24)
                    and value.source_id == 0
                ):
                    null_pointer_operands.append((op.id, position))
    if null_pointer_operands:
        preview = ", ".join(
            f"{operation}:{position}"
            for operation, position in null_pointer_operands[:8]
        )
        raise CompileError(
            f"SemanticGraph {function.graph_id} has unbound null Ref operands at {preview}"
        )


def decode_strict_abi(container: Container, function_id: int, architecture: int):
    """Decode and validate target ABI locations before machine lowering."""
    from pymergetic.rxf.execution.decode import decode_abi_signature
    from pymergetic.rxf.model.contracts import (
        ABIKind,
        ABIRegisterBank,
        ABIValueLocation,
        ContractRole,
        verify_abi_locations,
    )
    from pymergetic.rxf.ty.builtins import ABI_SIGNATURE_TYPE, ABI_VALUE_LOCATION_TYPE

    by = {node.id: node for node in container.nodes}
    wanted = ABIKind.SYSV_X86_64 if architecture == 1 else ABIKind.AAPCS64
    candidates = [
        node
        for node in container.nodes
        if node.type_id == ABI_SIGNATURE_TYPE
        and node.parent == function_id
        and decode_abi_signature(node).kind == wanted
    ]
    canonical_target = 817 if architecture == 1 else 818
    canonical = [
        node
        for node in candidates
        if decode_abi_signature(node).target_id == canonical_target
    ]
    if len(canonical) != 1:
        raise CompileError(
            f"Function {function_id} has {len(candidates)} {wanted.name} ABIs "
            f"without one canonical target {canonical_target} authority"
        )
    abi_node = canonical[0]
    errors = verify_abi_locations(container, abi_node)
    if errors:
        raise CompileError(errors[0])
    locations = tuple(
        ABIValueLocation.from_node(by[ref.target])
        for ref in abi_node.refs
        if ref.to_off == int(ContractRole.ABI_LOCATION)
        and by[ref.target].type_id == ABI_VALUE_LOCATION_TYPE
    )
    limit = 6 if wanted == ABIKind.SYSV_X86_64 else 8
    for location in locations:
        if location.register_bank == ABIRegisterBank.INTEGER and any(
            register >= limit for register in location.register_indices
        ):
            raise CompileError(
                f"ABIValueLocation {location.id} integer register {max(location.register_indices)} exceeds {wanted.name} bank {limit}"
            )
    return decode_abi_signature(abi_node), locations
