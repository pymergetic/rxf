"""Shared machine planning for persisted SemanticGraph values, CFG edges, and calls."""

from __future__ import annotations

from dataclasses import dataclass

from pymergetic.rxf.compiler.native import Architecture, FrameSlot, LinkError
from pymergetic.rxf.compiler.semantic import SemanticIRValue, SemanticValueKind
from pymergetic.rxf.compiler.semantic_lower import SemanticProgram
from pymergetic.rxf.model.contracts import (
    ABIPassingMode,
    ABIRegisterBank,
    ABIRole,
    ABIValueLocation,
)


@dataclass(frozen=True)
class CallPlacement:
    value: SemanticIRValue | None
    location: ABIValueLocation


@dataclass(frozen=True)
class MachinePlan:
    architecture: Architecture
    frame: tuple[FrameSlot, ...]
    calls: tuple[tuple[int, tuple[CallPlacement, ...]], ...]
    stack_argument_size: int
    scratch_offset: int


@dataclass(frozen=True)
class ParallelMove:
    source: int
    destination: int


def schedule_parallel_copies(
    copies: tuple[ParallelMove, ...], scratch: int
) -> tuple[ParallelMove, ...]:
    """Schedule simultaneous phi transfers, breaking cycles through scratch."""
    pending = {
        move.destination: move.source
        for move in copies
        if move.source != move.destination
    }
    if len(pending) != sum(move.source != move.destination for move in copies):
        raise LinkError("parallel phi destinations are not unique")
    result = []
    while pending:
        ready = sorted(
            destination
            for destination in pending
            if destination not in pending.values()
        )
        if ready:
            for destination in ready:
                result.append(ParallelMove(pending.pop(destination), destination))
            continue
        destination = min(pending)
        source = pending[destination]
        result.append(ParallelMove(destination, scratch))
        for target, candidate in tuple(pending.items()):
            if candidate == destination:
                pending[target] = scratch
        result.append(ParallelMove(source, destination))
        del pending[destination]
    return tuple(result)


def build_machine_plan(
    program: SemanticProgram, architecture: Architecture
) -> MachinePlan:
    """Translate persisted ABI locations into explicit value placements."""
    values = {value.id: value for value in program.graph.parameters}
    for block in program.graph.blocks:
        values.update((value.id, value) for value in block.parameters)
        for operation in block.operations:
            values.update((value.id, value) for value in operation.results)
            if operation.status is not None:
                values[operation.status.id] = operation.status
    offset = 64
    slots = []
    for value in sorted(values.values(), key=lambda item: item.id):
        alignment = min(max(value.type.width, 1), 8)
        offset = (offset + alignment - 1) & -alignment
        slots.append(FrameSlot(value.id, offset, max(value.type.width, 1), alignment))
        offset += max(value.type.width, 1)
    scratch = (offset + 7) & -8
    calls = []
    stack_size = 0
    for lowered in program.operations:
        operation = lowered.source
        if lowered.call_abi is None:
            continue
        locations = (
            lowered.call_abi.x86_64
            if architecture == Architecture.X86_64
            else lowered.call_abi.aarch64
        )
        semantic = tuple(
            location
            for location in locations
            if location.role == ABIRole.SEMANTIC_ARGUMENT
        )
        if len(semantic) != len(operation.inputs):
            raise LinkError(
                f"Function {program.function_id} SemanticOperation {operation.id} ABI has "
                f"{len(semantic)} semantic locations for {len(operation.inputs)} values"
            )
        placements = [
            CallPlacement(value, location)
            for value, location in zip(operation.inputs, semantic, strict=True)
        ]
        outputs = tuple(
            location
            for location in locations
            if location.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}
        )
        if outputs:
            if not operation.results:
                raise LinkError(
                    f"Function {program.function_id} SemanticOperation {operation.id} ABI output has no SSA result"
                )
            placements.append(CallPlacement(operation.results[0], outputs[0]))
        contexts = tuple(
            location
            for location in locations
            if location.role == ABIRole.HIDDEN_CONTEXT
        )
        if len(contexts) > 1:
            raise LinkError(
                f"SemanticOperation {operation.id} has multiple hidden contexts"
            )
        if contexts:
            placements.insert(0, CallPlacement(None, contexts[0]))
        status = tuple(
            location for location in locations if location.role == ABIRole.STATUS_RETURN
        )
        if len(status) != 1 or operation.status is None:
            raise LinkError(
                f"SemanticOperation {operation.id} lacks status return provenance"
            )
        placements.append(CallPlacement(operation.status, status[0]))
        for placement in placements:
            location = placement.location
            if location.register_bank == ABIRegisterBank.STACK:
                stack_size = max(
                    stack_size, location.stack_offset + ((location.width + 7) // 8) * 8
                )
            if (
                placement.value is not None
                and location.role == ABIRole.SEMANTIC_ARGUMENT
                and placement.value.type.width > 8
                and location.passing == ABIPassingMode.DIRECT_SCALAR
            ):
                raise LinkError(
                    f"SemanticOperation {operation.id} value {placement.value.id} aggregate has scalar ABI location {location.id}"
                )
        calls.append((operation.id, tuple(placements)))
    return MachinePlan(architecture, tuple(slots), tuple(calls), stack_size, scratch)


def durable_object(value: SemanticIRValue) -> int:
    if value.kind != SemanticValueKind.OBJECT or not value.source_id:
        raise LinkError(f"SemanticValue {value.id} is not a durable object binding")
    return value.source_id
