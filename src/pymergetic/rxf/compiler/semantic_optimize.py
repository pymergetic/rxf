"""Deterministic target-independent optimization of lowered SemanticGraph IR."""

from __future__ import annotations

from dataclasses import replace

from pymergetic.rxf.compiler.normalize import CompileError
from pymergetic.rxf.compiler.semantic import (
    SemanticIRValue,
    SemanticValueKind,
    verify_semantic_ir,
)
from pymergetic.rxf.compiler.semantic_lower import (
    OptimizationReport,
    SemanticProgram,
    SpecializationBinding,
)
from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode, TerminatorKind

# Explicit conservative classification: everything absent is retained.
PURE_OPCODES = frozenset(
    {
        SemanticOpcode.EQUAL,
        SemanticOpcode.READ_TAG,
        SemanticOpcode.PROJECT_PAYLOAD,
        SemanticOpcode.CONSTRUCT_OPTION,
        SemanticOpcode.CONSTRUCT_RESULT,
        SemanticOpcode.MAP_REFUSAL,
        SemanticOpcode.ENRICH_REFUSAL,
    }
)
FOLD_IDENTITY = frozenset({SemanticOpcode.ENRICH_REFUSAL})


def _literal(value: SemanticIRValue, bits: bytes) -> SemanticIRValue:
    if len(bits) > value.type.width:
        raise CompileError(
            f"specialization SemanticValue {value.id} exceeds width {value.type.width}"
        )
    return replace(
        value,
        kind=SemanticValueKind.LITERAL,
        source_id=0,
        literal=bits.ljust(value.type.width, b"\0"),
    )


def _rewrite(value, substitutions):
    if value is None:
        return None
    seen = set()
    while value.id in substitutions and value.id not in seen:
        seen.add(value.id)
        value = substitutions[value.id]
    return value


def _fold(op, inputs, program):
    if (
        op.callee_id
        or op.effects
        or not inputs
        or any(v.kind != SemanticValueKind.LITERAL for v in inputs)
    ):
        return None
    payload = tuple(v for v in op.results if v != op.status)
    if len(payload) != 1:
        return None
    result = payload[0]
    if op.opcode in FOLD_IDENTITY:
        return _literal(result, inputs[0].literal)
    if (
        op.opcode == SemanticOpcode.EQUAL
        and len(inputs) == 2
        and inputs[0].type == inputs[1].type
        and inputs[0].type.width in (1, 2, 4, 8)
    ):
        return _literal(result, bytes((int(inputs[0].literal == inputs[1].literal),)))
    if op.opcode == SemanticOpcode.READ_TAG:
        field = next((f for f in program.fields if f.id == op.target_field_id), None)
        if field and field.offset + result.type.width <= len(inputs[0].literal):
            return _literal(
                result,
                inputs[0].literal[field.offset : field.offset + result.type.width],
            )
    if op.opcode in {SemanticOpcode.CONSTRUCT_OPTION, SemanticOpcode.CONSTRUCT_RESULT}:
        field = next((f for f in program.fields if f.id == op.target_field_id), None)
        variant = next(
            (
                v
                for v in program.tagged_variants
                if field and v.payload_field_id == field.id
            ),
            None,
        )
        if (
            field
            and variant
            and field.offset + len(inputs[0].literal) <= result.type.width
        ):
            bits = bytearray(result.type.width)
            bits[:4] = variant.tag_value.to_bytes(4, "little")
            bits[field.offset : field.offset + len(inputs[0].literal)] = inputs[
                0
            ].literal
            return _literal(result, bytes(bits))
    if op.opcode == SemanticOpcode.MAP_REFUSAL:
        plan = next(
            (p for p in program.refusal_mappings if p.operation_id == op.id), None
        )
        if plan and plan.payload_offset + 4 <= len(inputs[0].literal):
            bits = bytearray(inputs[0].literal)
            status = int.from_bytes(
                bits[plan.payload_offset : plan.payload_offset + 4], "little"
            )
            bits[plan.payload_offset : plan.payload_offset + 4] = (
                dict(plan.entries).get(status, status).to_bytes(4, "little")
            )
            return _literal(result, bytes(bits))
    return None


def optimize_semantic_program(
    program: SemanticProgram, bindings: tuple[SpecializationBinding, ...] = ()
) -> SemanticProgram:
    graph = program.graph
    values = {v.id: v for v in graph.parameters}
    for block in graph.blocks:
        values.update((v.id, v) for v in block.parameters)
        for op in block.operations:
            values.update((v.id, v) for v in (*op.inputs, *op.results))
        for value in (block.condition, block.terminator_value):
            if value is not None:
                values[value.id] = value
        for edge in block.edges:
            values.update((v.id, v) for v in edge.arguments)
    substitutions = {}
    for binding in sorted(
        (*program.specialization_bindings, *bindings), key=lambda b: b.value_id
    ):
        value = values.get(binding.value_id)
        if value is None:
            raise CompileError(
                f"specialization SemanticValue {binding.value_id} is unresolved"
            )
        if value.type.id != binding.type_id:
            raise CompileError(
                f"specialization SemanticValue {value.id} type {binding.type_id}!={value.type.id}"
            )
        substitutions[value.id] = _literal(value, binding.literal)
    folded = set()
    blocks = []
    for block in graph.blocks:
        ops = []
        for op in block.operations:
            inputs = tuple(_rewrite(v, substitutions) for v in op.inputs)
            value = _fold(replace(op, inputs=inputs), inputs, program)
            if value is not None:
                substitutions[next(v for v in op.results if v != op.status).id] = value
                folded.add(op.id)
            else:
                ops.append(replace(op, inputs=inputs))
        edges = tuple(
            replace(e, arguments=tuple(_rewrite(v, substitutions) for v in e.arguments))
            for e in block.edges
        )
        condition_was_specialized = (
            block.condition is not None and block.condition.id in substitutions
        )
        condition = _rewrite(block.condition, substitutions)
        terminator = block.terminator
        if (
            condition_was_specialized
            and block.terminator == TerminatorKind.BRANCH
            and condition
            and condition.kind == SemanticValueKind.LITERAL
            and condition.type.width == 1
        ):
            edges = (edges[0 if condition.literal[0] else 1],)
            terminator = TerminatorKind.GOTO
            condition = None
        blocks.append(
            replace(
                block,
                operations=tuple(ops),
                edges=edges,
                condition=condition,
                terminator=terminator,
                terminator_value=_rewrite(block.terminator_value, substitutions),
            )
        )
    by = {b.id: b for b in blocks}
    reachable = {graph.entry_block_id}
    pending = [graph.entry_block_id]
    while pending:
        for edge in by[pending.pop()].edges:
            if edge.target not in reachable:
                reachable.add(edge.target)
                pending.append(edge.target)
    blocks = [b for b in blocks if b.id in reachable]
    used = set()
    for b in blocks:
        used.update(v.id for e in b.edges for v in e.arguments)
        if b.condition:
            used.add(b.condition.id)
        if b.terminator_value:
            used.add(b.terminator_value.id)
    removed = set()
    for bi in range(len(blocks) - 1, -1, -1):
        kept = []
        for op in reversed(blocks[bi].operations):
            outputs = {v.id for v in op.results}
            if (
                op.opcode in PURE_OPCODES
                and not op.effects
                and outputs.isdisjoint(used)
            ):
                removed.add(op.id)
            else:
                kept.append(op)
                used.update(v.id for v in op.inputs)
        blocks[bi] = replace(blocks[bi], operations=tuple(reversed(kept)))
    # Preserve phi geometry: native placement assigns canonical slots to all block
    # parameters, including values required by parallel-copy scheduling.
    final = []
    for block in blocks:
        preds = tuple(
            sorted(x.id for x in blocks if any(e.target == block.id for e in x.edges))
        )
        final.append(replace(block, index=len(final), predecessors=preds))
    final_graph = replace(graph, blocks=tuple(final))
    verify_semantic_ir(final_graph)
    remaining = {o.id for b in final for o in b.operations}
    operations = tuple(o for o in program.operations if o.source.id in remaining)
    report = OptimizationReport(
        len(graph.blocks),
        len(final),
        sum(len(b.operations) for b in graph.blocks),
        sum(len(b.operations) for b in final),
        tuple(sorted(folded)),
        tuple(sorted(removed)),
        tuple(b.id for b in graph.blocks if b.id not in reachable),
        0,
        sum(
            1
            for o in operations
            if o.source.effects or o.source.opcode not in PURE_OPCODES
        ),
    )
    return replace(
        program,
        graph=final_graph,
        operations=operations,
        optimization_report=report,
    )
