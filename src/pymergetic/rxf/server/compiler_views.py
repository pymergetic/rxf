"""Read-only inspector projections for deterministic compiler artifacts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymergetic.rxf.compiler.compile import compile_function
from pymergetic.rxf.compiler.native import Architecture, emit
from pymergetic.rxf.compiler.normalize import CompileError
from pymergetic.rxf.compiler.semantic import (
    classify_semantic_operations,
    discover_semantic_graph,
    normalize_semantic_graph,
)
from pymergetic.rxf.compiler.semantic_lower import (
    OptimizationReport,
    SemanticProgram,
    lower_semantic_program,
)
from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program
from pymergetic.rxf.model.container import Container

_ARCHITECTURES = {
    "x86_64": Architecture.X86_64,
    "aarch64": Architecture.AARCH64,
}


def _id(value: int) -> str:
    return str(value)


def _link(container: Container, value: int) -> dict[str, str]:
    node = container.node_by_id(value)
    return {"id": _id(value), "name": node.name if node else "unresolved"}


def _value(container: Container, value) -> dict[str, Any]:
    return {
        "id": _id(value.id),
        "kind": value.kind.name,
        "type": {
            **_link(container, value.type.id),
            "width": value.type.width,
            "class": value.type.value_class.name,
        },
        "source_id": _id(value.source_id) if value.source_id else None,
        "literal_hex": value.literal.hex() if value.literal else None,
    }


def _location(container: Container, location) -> dict[str, Any]:
    registers = list(location.register_indices)
    return {
        "id": _id(location.id),
        "name": location.name,
        "association": _link(container, location.association_id)
        if location.association_id
        else None,
        "semantic_index": location.semantic_index,
        "role": location.role.name,
        "passing": location.passing.name,
        "value_class": location.value_class.name,
        "width": location.width,
        "alignment": location.alignment,
        "register_bank": location.register_bank.name,
        "registers": registers,
        "stack_offset": location.stack_offset,
        "hidden": location.hidden,
        "mutable": location.mutable,
        "output_only": location.output_only,
    }


def _report(report: OptimizationReport, program: SemanticProgram) -> dict[str, Any]:
    folded = set(report.folded_operations)
    removed = set(report.removed_operations)
    retained = {op.source.id for op in program.operations}
    return {
        "counts": {
            "blocks": {"before": report.blocks_before, "after": report.blocks_after},
            "operations": {
                "before": report.operations_before,
                "after": report.operations_after,
            },
            "removed_values": report.removed_values,
            "retained_effects": report.retained_effects,
        },
        "specialization_bindings": [
            {
                "value_id": _id(b.value_id),
                "type_id": _id(b.type_id),
                "literal_hex": b.literal.hex(),
            }
            for b in program.specialization_bindings
        ],
        "folded_operations": [_id(v) for v in report.folded_operations],
        "removed_operations": [_id(v) for v in report.removed_operations],
        "unreachable_blocks": [_id(v) for v in report.removed_blocks],
        "provenance": [
            {
                "operation_id": _id(v),
                "status": "folded"
                if v in folded
                else "removed"
                if v in removed
                else "retained",
            }
            for v in sorted(retained | folded | removed)
        ],
    }


def _graph(container: Container, program: SemanticProgram) -> dict[str, Any]:
    graph = program.graph
    blocks = []
    for block in graph.blocks:
        operations = []
        for op in block.operations:
            operations.append(
                {
                    "id": _id(op.id),
                    "opcode": op.opcode.name,
                    "inputs": [_value(container, v) for v in op.inputs],
                    "results": [_value(container, v) for v in op.results],
                    "status": _value(container, op.status) if op.status else None,
                    "effects": int(op.effects),
                    "refusal_set": _link(container, op.refusal_set_id)
                    if op.refusal_set_id
                    else None,
                    "callee": _link(container, op.callee_id) if op.callee_id else None,
                    "target_object": _link(container, op.target_object_id)
                    if op.target_object_id
                    else None,
                    "target_field": _link(container, op.target_field_id)
                    if op.target_field_id
                    else None,
                    "transform_authority": _link(container, op.transform_authority_id)
                    if op.transform_authority_id
                    else None,
                    "is_cleanup": op.opcode.name == "CLEANUP",
                }
            )
        blocks.append(
            {
                "id": _id(block.id),
                "index": block.index,
                "is_entry": block.id == graph.entry_block_id,
                "predecessors": [_id(v) for v in block.predecessors],
                "phis": [_value(container, v) for v in block.parameters],
                "operations": operations,
                "terminator": {
                    "kind": block.terminator.name,
                    "condition": _value(container, block.condition)
                    if block.condition
                    else None,
                    "value": _value(container, block.terminator_value)
                    if block.terminator_value
                    else None,
                    "case_tags": list(block.case_tags),
                },
                "edges": [
                    {
                        "target": _id(e.target),
                        "arguments": [_id(v.id) for v in e.arguments],
                    }
                    for e in block.edges
                ],
            }
        )
    return {
        "graph_id": _id(graph.graph_id),
        "entry_block": _id(graph.entry_block_id),
        "output_type": _link(container, graph.output_type.id),
        "effects": int(graph.effects),
        "refusal_set": _link(container, graph.refusal_set_id)
        if graph.refusal_set_id
        else None,
        "parameters": [_value(container, v) for v in graph.parameters],
        "results": [_value(container, v) for v in graph.results],
        "blocks": blocks,
    }


def _image(container: Container, image, architecture: str) -> dict[str, Any]:
    fixups = []
    for fixup in image.fixups:
        fixups.append(
            {
                "offset": fixup.offset,
                "kind": fixup.kind.name,
                "namespace": fixup.namespace.name,
                "target": _link(container, fixup.target_id),
                "width": fixup.width,
                "signed": fixup.signed,
                "scale": fixup.scale,
                "addend": fixup.addend,
            }
        )
    return {
        "architecture": architecture,
        "code_size": len(image.text),
        "code_hex": image.text.hex(),
        "rodata_size": len(image.rodata),
        "rodata_hex": image.rodata.hex(),
        "entry_offset": 0,
        "frame_slots": [
            {**asdict(s), "value_id": _id(s.value_id)} for s in image.frame
        ],
        "function_bindings": [_link(container, v) for v in image.function_ids],
        "object_bindings": [_link(container, v) for v in image.object_ids],
        "capability_bindings": [
            f["target"] for f in fixups if f["namespace"] == "CAPABILITY"
        ],
        "fixups": fixups,
        "unresolved_diagnostics": [],
        "disassembly": None,
        "digest": image.digest.hex(),
    }


def compiler_artifact_view(
    container: Container, function_id: int, architecture: str, optimized: bool
) -> dict[str, Any]:
    """Compile one snapshot in memory and return a JSON-safe inspection view."""
    arch = _ARCHITECTURES.get(architecture)
    if arch is None:
        raise ValueError(f"unsupported architecture {architecture!r}")
    node = container.node_by_id(function_id)
    base: dict[str, Any] = {
        "function_id": _id(function_id),
        "function": node.name if node else "unresolved",
        "architecture": architecture,
        "mode": "optimized" if optimized else "unoptimized",
        "ok": False,
        "diagnostics": [],
    }
    try:
        if discover_semantic_graph(container, function_id) is None:
            ir = compile_function(container, function_id)
            image = emit(ir, arch)
            base.update(
                {
                    "ok": True,
                    "semantic_graph": None,
                    "optimization": None,
                    "native_image": _image(container, image, architecture),
                    "abi": {"entry": [], "calls": []},
                }
            )
            return base
        graph = normalize_semantic_graph(container, function_id)
        classify_semantic_operations(container, graph)
        raw = lower_semantic_program(container, graph)
        optimized_result = optimize_semantic_program(raw)
        program = optimized_result if optimized else raw
        report = optimized_result.optimization_report
        assert report is not None
        image = emit(program, arch)
        entry_abi = (
            program.x86_entry_abi
            if arch == Architecture.X86_64
            else program.arm_entry_abi
        )
        calls = []
        for op in program.operations:
            if op.call_abi is None:
                continue
            locations = (
                op.call_abi.x86_64
                if arch == Architecture.X86_64
                else op.call_abi.aarch64
            )
            calls.append(
                {
                    "operation_id": _id(op.source.id),
                    "function": _link(container, op.source.callee_id),
                    "locations": [_location(container, value) for value in locations],
                }
            )
        base.update(
            {
                "ok": True,
                "semantic_graph": _graph(container, program),
                "optimization": _report(report, program),
                "native_image": _image(container, image, architecture),
                "abi": {
                    "entry": [_location(container, value) for value in entry_abi],
                    "calls": calls,
                },
            }
        )
    except (CompileError, ValueError, AssertionError, KeyError) as error:
        base["diagnostics"].append(
            {
                "kind": type(error).__name__,
                "message": str(error),
                "function_id": _id(function_id),
            }
        )
    return base


def executable_plan_view(
    container: Container, source_blob: bytes, function_id: int, target: int | str
) -> dict[str, Any]:
    """Build a read-only executable plan projection; never package or write."""
    from pymergetic.rxf.executable.build import prepare_executable

    return prepare_executable(
        container, source_blob, function_id, target, package=False
    )
