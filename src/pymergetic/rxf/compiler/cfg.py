"""Typed native control-flow IR and structural safety verifier.

This is compiler authority: semantic marker names are never interpreted as programs.
Only explicit blocks, values, operations, and terminators can reach native lowering.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from pymergetic.rxf.compiler.ir import IREffect, TypeRef, VirtualValue
from pymergetic.rxf.compiler.normalize import CompileError


class AggregateClass(IntEnum):
    SCALAR = 1
    BY_REFERENCE = 2
    TAGGED = 3


@dataclass(frozen=True)
class ABIValue:
    type: TypeRef
    classification: AggregateClass = AggregateClass.SCALAR


@dataclass(frozen=True)
class BlockParameter:
    value: VirtualValue
    incoming: tuple[tuple[int, VirtualValue], ...]


@dataclass(frozen=True)
class ConstOp:
    result: VirtualValue
    bits: int


@dataclass(frozen=True)
class ProjectTagOp:
    source: VirtualValue
    result: VirtualValue
    valid_tags: tuple[int, ...]


@dataclass(frozen=True)
class ProjectPayloadOp:
    source: VirtualValue
    tag: int
    result: VirtualValue
    offset: int


@dataclass(frozen=True)
class NativeCallOp:
    function_id: int
    arguments: tuple[VirtualValue, ...]
    argument_abi: tuple[ABIValue, ...]
    result: VirtualValue
    status: VirtualValue
    refusal_codes: tuple[int, ...]
    effects: IREffect = IREffect.NONE


@dataclass(frozen=True)
class PrepareOp:
    transaction_id: int
    private_value: VirtualValue


@dataclass(frozen=True)
class ValidateOp:
    transaction_id: int
    condition: VirtualValue


@dataclass(frozen=True)
class PublishOp:
    transaction_id: int
    value: VirtualValue


@dataclass(frozen=True)
class CleanupOp:
    transaction_id: int


@dataclass(frozen=True)
class RemapStatusOp:
    status: VirtualValue
    result: VirtualValue
    mapping: tuple[tuple[int, int], ...]


Operation = (
    ConstOp
    | ProjectTagOp
    | ProjectPayloadOp
    | NativeCallOp
    | PrepareOp
    | ValidateOp
    | PublishOp
    | CleanupOp
    | RemapStatusOp
)


@dataclass(frozen=True)
class Jump:
    target: int
    arguments: tuple[VirtualValue, ...] = ()


@dataclass(frozen=True)
class BranchBool:
    condition: VirtualValue
    if_true: int
    if_false: int


@dataclass(frozen=True)
class BranchStatusCode:
    status: VirtualValue
    success: int
    refusal: int


@dataclass(frozen=True)
class SwitchTag:
    tag: VirtualValue
    cases: tuple[tuple[int, int], ...]
    default: int | None
    exhaustive_tags: tuple[int, ...]


@dataclass(frozen=True)
class ReturnControl:
    status: VirtualValue
    value: VirtualValue | None = None


@dataclass(frozen=True)
class BreakLoop:
    loop_header: int
    target: int
    arguments: tuple[VirtualValue, ...] = ()


@dataclass(frozen=True)
class ContinueLoop:
    loop_header: int
    arguments: tuple[VirtualValue, ...] = ()


ControlTerminator = (
    Jump
    | BranchBool
    | BranchStatusCode
    | SwitchTag
    | ReturnControl
    | BreakLoop
    | ContinueLoop
)


@dataclass(frozen=True)
class ControlBlock:
    id: int
    parameters: tuple[BlockParameter, ...]
    operations: tuple[Operation, ...]
    terminator: ControlTerminator
    loop_header: bool = False
    enclosing_loop: int | None = None


@dataclass(frozen=True)
class ControlFunction:
    function_id: int
    parameters: tuple[VirtualValue, ...]
    blocks: tuple[ControlBlock, ...]
    entry: int
    result_type: TypeRef


def successors(term: ControlTerminator) -> tuple[int, ...]:
    if isinstance(term, Jump):
        return (term.target,)
    if isinstance(term, BranchBool):
        return (term.if_true, term.if_false)
    if isinstance(term, BranchStatusCode):
        return (term.success, term.refusal)
    if isinstance(term, SwitchTag):
        return tuple(target for _, target in term.cases) + (
            () if term.default is None else (term.default,)
        )
    if isinstance(term, BreakLoop):
        return (term.target,)
    if isinstance(term, ContinueLoop):
        return (term.loop_header,)
    return ()


def verify_control(function: ControlFunction) -> None:
    blocks = {b.id: b for b in function.blocks}
    if function.entry not in blocks:
        raise CompileError("control entry block is missing")
    if len(blocks) != len(function.blocks):
        raise CompileError("control block IDs are not unique")
    predecessors = {bid: set() for bid in blocks}
    for block in function.blocks:
        for target in successors(block.terminator):
            if target not in blocks:
                raise CompileError(f"block {block.id} targets missing block {target}")
            predecessors[target].add(block.id)
        if isinstance(block.terminator, (BreakLoop, ContinueLoop)):
            header = blocks.get(block.terminator.loop_header)
            if (
                header is None
                or not header.loop_header
                or block.enclosing_loop != header.id
            ):
                raise CompileError(
                    f"block {block.id} break/continue escapes its enclosing loop"
                )
        if isinstance(block.terminator, SwitchTag):
            keys = tuple(key for key, _ in block.terminator.cases)
            if len(keys) != len(set(keys)):
                raise CompileError("switch cases are not unique")
            missing = set(block.terminator.exhaustive_tags) - set(keys)
            if missing and block.terminator.default is None:
                raise CompileError(f"switch is not exhaustive: {sorted(missing)}")
    reachable = {function.entry}
    pending = [function.entry]
    while pending:
        current = pending.pop()
        for target in successors(blocks[current].terminator):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    if reachable != set(blocks):
        raise CompileError(
            f"unreachable control blocks: {sorted(set(blocks) - reachable)}"
        )
    # Iterative dominators.
    dom = {bid: ({bid} if bid == function.entry else set(blocks)) for bid in blocks}
    changed = True
    while changed:
        changed = False
        for bid in blocks:
            if bid == function.entry:
                continue
            incoming = predecessors[bid]
            value = {bid} | (
                set.intersection(*(dom[p] for p in incoming)) if incoming else set()
            )
            if value != dom[bid]:
                dom[bid] = value
                changed = True
    definitions = {v.id: function.entry for v in function.parameters}
    types = {v.id: v.type for v in function.parameters}
    transactions: dict[int, dict[str, set[int]]] = {}
    for block in function.blocks:
        if len(block.parameters):
            for parameter in block.parameters:
                if {source for source, _ in parameter.incoming} != predecessors[
                    block.id
                ]:
                    raise CompileError(
                        f"block parameter {parameter.value.id} lacks exact predecessor inputs"
                    )
                if any(
                    value.type != parameter.value.type
                    for _, value in parameter.incoming
                ):
                    raise CompileError("phi/block parameter type mismatch")
                definitions[parameter.value.id] = block.id
                types[parameter.value.id] = parameter.value.type
        for op in block.operations:
            result = getattr(op, "result", None)
            if isinstance(result, VirtualValue):
                if result.id in definitions:
                    raise CompileError(f"value {result.id} has multiple definitions")
                definitions[result.id] = block.id
                types[result.id] = result.type
            if isinstance(op, NativeCallOp):
                if len(op.arguments) != len(op.argument_abi):
                    raise CompileError("call ABI classification count mismatch")
                for argument, abi in zip(op.arguments, op.argument_abi, strict=True):
                    if argument.type != abi.type:
                        raise CompileError("call ABI type mismatch")
                    if (
                        argument.type.width not in (1, 2, 4, 8)
                        and abi.classification == AggregateClass.SCALAR
                    ):
                        raise CompileError(
                            "aggregate argument requires explicit by-reference ABI classification"
                        )
                definitions[op.status.id] = block.id
                types[op.status.id] = op.status.type
            if isinstance(op, ProjectPayloadOp) and op.offset < 4:
                raise CompileError("tagged payload overlaps tag")
            if isinstance(op, (PrepareOp, ValidateOp, PublishOp, CleanupOp)):
                stage = type(op).__name__
                transactions.setdefault(op.transaction_id, {}).setdefault(
                    stage, set()
                ).add(block.id)

    def use(value: VirtualValue, block_id: int) -> None:
        definition = definitions.get(value.id)
        if definition is None:
            raise CompileError(f"value {value.id} used without definition")
        if definition not in dom[block_id]:
            raise CompileError(
                f"value {value.id} definition does not dominate block {block_id}"
            )
        if types[value.id] != value.type:
            raise CompileError(f"value {value.id} type disagreement")

    for block in function.blocks:
        for op in block.operations:
            for name in ("source", "condition", "status", "value"):
                value = getattr(op, name, None)
                if isinstance(value, VirtualValue):
                    use(value, block.id)
            if isinstance(op, NativeCallOp):
                for value in op.arguments:
                    use(value, block.id)
        term = block.terminator
        for name in ("condition", "status", "tag", "value"):
            value = getattr(term, name, None)
            if isinstance(value, VirtualValue):
                use(value, block.id)
    # Transaction proof: prepare dominates validate/publish; validate dominates publish;
    # every exit reachable from prepare before publish must contain cleanup.
    exits = [b.id for b in function.blocks if isinstance(b.terminator, ReturnControl)]
    for tid, stages in transactions.items():
        prepare = stages.get("PrepareOp", set())
        validate = stages.get("ValidateOp", set())
        publish = stages.get("PublishOp", set())
        cleanup = stages.get("CleanupOp", set())
        if len(prepare) != 1 or len(validate) != 1 or len(publish) != 1:
            raise CompileError(
                f"transaction {tid} requires one prepare/validate/publish"
            )
        prep = next(iter(prepare))
        val = next(iter(validate))
        pub = next(iter(publish))
        if prep not in dom[val] or val not in dom[pub]:
            raise CompileError(
                f"transaction {tid} publish is not prepare/validate dominated"
            )
        for exit_id in exits:
            if pub not in dom[exit_id] and not any(c in dom[exit_id] for c in cleanup):
                raise CompileError(
                    f"transaction {tid} exit {exit_id} lacks rollback cleanup"
                )
