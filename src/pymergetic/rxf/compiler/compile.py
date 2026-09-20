"""Lower normalized composed graphs to typed status-explicit IR."""

from __future__ import annotations

import hashlib
import struct

from pymergetic.rxf.compiler.ir import (
    Block,
    BranchStatus,
    CallOp,
    CompiledFunction,
    IREffect,
    ReturnStatus,
    ReturnValue,
    TypeRef,
    ValueClass,
    VirtualValue,
)
from pymergetic.rxf.compiler.normalize import CompileError, NormalizedValue, normalize
from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import Effect, FunctionImplementation, ValueKind
from pymergetic.rxf.ty.objects import TypeForm, TypeObject

STATUS_TYPE = TypeRef(7, 4, ValueClass.INTEGER)


def type_ref(container: Container, type_id: int) -> TypeRef:
    node = container.node_by_id(type_id)
    if node is None:
        raise CompileError(f"type {type_id} is missing")
    descriptor = TypeObject.from_payload(id=node.id, name=node.name, payload=node.data)
    value_class = (
        ValueClass.FLOAT if descriptor.form == TypeForm.FLOAT else ValueClass.INTEGER
    )
    if descriptor.form in (TypeForm.REF, TypeForm.OPAQUE):
        value_class = ValueClass.POINTER
    return TypeRef(
        type_id, descriptor.size, value_class, bool(int(descriptor.flags) & 2)
    )


def _effects(value: Effect) -> IREffect:
    return IREffect(int(value))


def compile_function(container: Container, function_id: int):
    # SemanticGraph is compiler authority for authored stdlib bodies. It is
    # normalized before the ordinary call spine so stale marker checks cannot
    # reject concrete graphs.
    from pymergetic.rxf.compiler.semantic import (
        classify_semantic_operations,
        discover_semantic_graph,
        normalize_semantic_graph,
    )

    if discover_semantic_graph(container, function_id) is not None:
        semantic_ir = normalize_semantic_graph(container, function_id)
        classify_semantic_operations(container, semantic_ir)
        from pymergetic.rxf.compiler.semantic_lower import lower_semantic_program
        from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program

        return optimize_semantic_program(lower_semantic_program(container, semantic_ir))
    _verify_reachable_implementations(container, function_id, set())
    graph = normalize(container, function_id)
    params = tuple(
        VirtualValue(i + 1, type_ref(container, _parameter_type(container, p)), p, 1)
        for i, p in enumerate(graph.parameter_ids)
    )
    values: dict[int, VirtualValue] = {}
    blocks = []
    next_value = len(params) + 1
    refusal_id = len(graph.calls) + 2
    for index, call in enumerate(graph.calls, 1):
        arguments = tuple(
            _materialize(container, v, values, params) for v in call.arguments
        )
        result = VirtualValue(
            next_value, type_ref(container, call.result_type), call.id, index
        )
        next_value += 1
        status = VirtualValue(next_value, STATUS_TYPE, call.id, index)
        next_value += 1
        callee = container.node_by_id(call.function_id)
        assert callee is not None
        callee_info = decode_function(callee)
        if callee_info.implementation == FunctionImplementation.INTRINSIC:
            raise CompileError(
                f"intrinsic Function {call.function_id} ({callee.name}) has no explicit typed compiler lowering"
            )
        operation = CallOp(
            call.id,
            call.function_id,
            arguments,
            result,
            status,
            _effects(callee_info.effects),
            callee_info.implementation == FunctionImplementation.COMPOSED,
        )
        successor = index + 1 if index < len(graph.calls) else len(graph.calls) + 1
        blocks.append(
            Block(
                index,
                (operation,),
                BranchStatus(call.id, status, successor, refusal_id),
            )
        )
        values[call.id] = result
    terminal = values.get(graph.terminal_call_id)
    if terminal is None:
        raise CompileError("terminal Call has no result definition")
    success_id = len(graph.calls) + 1
    blocks.append(Block(success_id, (), ReturnValue(function_id, terminal)))
    refusal_status = VirtualValue(next_value, STATUS_TYPE, function_id, refusal_id)
    blocks.append(Block(refusal_id, (), ReturnStatus(function_id, refusal_status)))
    digest = hashlib.sha256(
        graph.digest + b"".join(struct.pack("<Q", i) for i in graph.source_ids)
    ).digest()
    result = CompiledFunction(
        function_id,
        graph.signature_id,
        params,
        type_ref(container, graph.return_type),
        tuple(blocks),
        1,
        refusal_id,
        graph.source_ids,
        digest,
    )
    verify(result)
    return result


def _verify_reachable_implementations(
    container: Container, function_id: int, visiting: set[int]
) -> None:
    if function_id in visiting:
        return
    visiting.add(function_id)
    graph = normalize(container, function_id)
    for call in graph.calls:
        callee = container.node_by_id(call.function_id)
        assert callee is not None
        record = decode_function(callee)
        if record.implementation == FunctionImplementation.INTRINSIC:
            raise CompileError(
                f"intrinsic Function {call.function_id} ({callee.name}) has no explicit typed compiler lowering"
            )
        if record.implementation == FunctionImplementation.COMPOSED:
            _verify_reachable_implementations(container, call.function_id, visiting)


def _parameter_type(container: Container, parameter_id: int) -> int:
    node = container.node_by_id(parameter_id)
    assert node is not None
    return struct.unpack_from("<Q", node.data)[0]


def _materialize(
    container: Container,
    value: NormalizedValue,
    results: dict[int, VirtualValue],
    params: tuple[VirtualValue, ...],
) -> VirtualValue:
    if value.kind == ValueKind.RESULT:
        if value.source_id not in results:
            raise CompileError(f"Value {value.id} use does not follow definition")
        return results[value.source_id]
    if value.kind == ValueKind.PARAMETER:
        matches = [p for p in params if p.source_id == value.source_id]
        if len(matches) != 1:
            raise CompileError(f"Value {value.id} Parameter use is ambiguous")
        return matches[0]
    durable_id = value.source_id if value.kind == ValueKind.OBJECT else value.id
    return VirtualValue(
        -(durable_id + 1), type_ref(container, value.type_id), value.id, 0
    )


def verify(function: CompiledFunction) -> None:
    if not function.blocks or function.entry_block != function.blocks[0].id:
        raise CompileError("IR has no canonical entry block")
    ids = [b.id for b in function.blocks]
    if len(ids) != len(set(ids)):
        raise CompileError("IR block IDs are not unique")
    by_id = set(ids)
    definitions = {v.id: v.definition_block for v in function.parameters}
    for block in function.blocks:
        if block.terminator is None:
            raise CompileError(f"block {block.id} lacks terminator")
        if isinstance(block.terminator, BranchStatus) and (
            block.terminator.success not in by_id
            or block.terminator.refusal not in by_id
        ):
            raise CompileError("branch target is missing")
        for op in block.operations:
            for arg in op.arguments:
                if arg.id >= 0 and arg.id not in definitions:
                    raise CompileError(f"virtual value {arg.id} used before definition")
                if arg.id >= 0 and definitions[arg.id] > block.id:
                    raise CompileError(f"virtual value {arg.id} does not dominate use")
            if op.result.id in definitions or op.status.id in definitions:
                raise CompileError("virtual value has multiple definitions")
            definitions[op.result.id] = block.id
            definitions[op.status.id] = block.id
            if op.status.type != STATUS_TYPE:
                raise CompileError("call status is not uint32")
