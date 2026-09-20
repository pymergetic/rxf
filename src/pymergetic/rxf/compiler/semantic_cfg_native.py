"""Generic multi-block native CFG emission for persisted semantic leaf calls."""

from __future__ import annotations

import struct

from pymergetic.rxf.compiler.native import (
    Architecture,
    Fixup,
    FixupKind,
    FixupNamespace,
    LinkError,
    NativeImage,
    _validate_fixups,
)
from pymergetic.rxf.compiler.semantic import SemanticIRValue, SemanticValueKind
from pymergetic.rxf.compiler.semantic_lower import SemanticProgram
from pymergetic.rxf.compiler.semantic_machine import (
    ParallelMove,
    build_machine_plan,
    schedule_parallel_copies,
)
from pymergetic.rxf.model.contracts import ABIPassingMode, ABIRegisterBank, ABIRole
from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode, TerminatorKind
from pymergetic.rxf.ty.builtins import BOOL_TYPE, U32_TYPE

CALL_OPCODES = {
    SemanticOpcode.BEGIN_PRIVATE,
    SemanticOpcode.ALLOCATE,
    SemanticOpcode.COMPARE,
    SemanticOpcode.COPY,
    SemanticOpcode.HASH,
    SemanticOpcode.LOOKUP,
    SemanticOpcode.READ,
    SemanticOpcode.SEARCH,
    SemanticOpcode.UTF8_VALIDATE,
    SemanticOpcode.CALL_CALLBACK,
    SemanticOpcode.WRITE,
    SemanticOpcode.STORE_FIELD,
    SemanticOpcode.MOVE_SLOT,
    SemanticOpcode.APPEND_BYTES,
    SemanticOpcode.FORMAT_INTEGER,
    SemanticOpcode.VALIDATE,
    SemanticOpcode.PUBLISH,
    SemanticOpcode.ROLLBACK,
    SemanticOpcode.CLEANUP,
    SemanticOpcode.PIN,
    SemanticOpcode.UNPIN,
    SemanticOpcode.BORROW,
    SemanticOpcode.RELEASE_BORROW,
}
IDENTITY_OPCODES = {
    SemanticOpcode.RETURN_VALUE,
    SemanticOpcode.CANONICAL_ORDER,
    SemanticOpcode.UTF8_BOUNDARY,
    SemanticOpcode.ITER_INIT,
}
TRANSFORM_OPCODES = {SemanticOpcode.MAP_REFUSAL, SemanticOpcode.ENRICH_REFUSAL}
TAGGED_OPCODES = {
    SemanticOpcode.READ_TAG,
    SemanticOpcode.PROJECT_PAYLOAD,
    SemanticOpcode.CONSTRUCT_OPTION,
    SemanticOpcode.CONSTRUCT_RESULT,
}
ITERATOR_OPCODES = {
    SemanticOpcode.ITER_CONDITION,
    SemanticOpcode.ITER_PROJECT,
    SemanticOpcode.ITER_ADVANCE,
}


def supports_cfg(program: SemanticProgram) -> bool:
    operations = {operation.source.opcode for operation in program.operations}
    if (
        not (operations & CALL_OPCODES)
        or not operations
        <= CALL_OPCODES
        | IDENTITY_OPCODES
        | ITERATOR_OPCODES
        | TAGGED_OPCODES
        | TRANSFORM_OPCODES
    ):
        return False
    blocks = {block.id: block for block in program.graph.blocks}
    predecessors = {block_id: set() for block_id in blocks}
    for block in blocks.values():
        for edge in block.edges:
            if edge.target not in blocks:
                raise LinkError(
                    f"Function {program.function_id} block {block.id} backedge target {edge.target} is absent"
                )
            predecessors[edge.target].add(block.id)
            parameters = blocks[edge.target].parameters
            if len(edge.arguments) != len(parameters):
                raise LinkError(
                    f"Function {program.function_id} edge {block.id}->{edge.target} phi arity "
                    f"{len(edge.arguments)} != {len(parameters)}"
                )
            for argument, parameter in zip(edge.arguments, parameters, strict=True):
                if argument.type != parameter.type:
                    raise LinkError(
                        f"Function {program.function_id} edge {block.id}->{edge.target} "
                        f"phi value {argument.id} type {argument.type.id} != parameter "
                        f"{parameter.id} type {parameter.type.id}"
                    )
        if block.terminator == TerminatorKind.LOOP and (
            block.condition is None or block.condition.type.id != BOOL_TYPE
        ):
            value_id = 0 if block.condition is None else block.condition.id
            raise LinkError(
                f"Function {program.function_id} loop block {block.id} condition value "
                f"{value_id} is not explicit BOOL"
            )

    entry = program.graph.entry_block_id
    all_ids = set(blocks)
    dominators = {
        block_id: ({block_id} if block_id == entry else set(all_ids))
        for block_id in blocks
    }
    changed = True
    while changed:
        changed = False
        for block_id in blocks:
            if block_id == entry:
                continue
            incoming = predecessors[block_id]
            common = (
                set.intersection(*(dominators[p] for p in incoming))
                if incoming
                else set()
            )
            updated = {block_id} | common
            if updated != dominators[block_id]:
                dominators[block_id] = updated
                changed = True

    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(block_id: int) -> None:
        visiting.add(block_id)
        for edge in blocks[block_id].edges:
            if edge.target in visiting:
                if edge.target not in dominators[block_id]:
                    raise LinkError(
                        f"Function {program.function_id} irreducible backedge "
                        f"{block_id}->{edge.target}"
                    )
                continue
            if edge.target not in visited:
                visit(edge.target)
        visiting.remove(block_id)
        visited.add(block_id)

    visit(entry)
    return True


def emit_cfg(program: SemanticProgram, architecture: Architecture) -> NativeImage:
    verify_transaction_paths(program)
    return (
        _emit_x86(program)
        if architecture == Architecture.X86_64
        else _emit_arm(program)
    )


def _successors(block):
    return tuple(edge.target for edge in block.edges)


def verify_transaction_paths(program: SemanticProgram) -> None:
    """Verify every authored post-mutation exit traverses cleanup."""
    blocks = {block.id: block for block in program.graph.blocks}
    operations = tuple(operation.source for operation in program.operations)
    opcodes = {operation.opcode for operation in operations}
    if SemanticOpcode.PUBLISH not in opcodes:
        return
    required = {
        SemanticOpcode.VALIDATE,
        SemanticOpcode.ROLLBACK,
        SemanticOpcode.CLEANUP,
    }
    missing = sorted(opcode.name for opcode in required - opcodes)
    if missing:
        raise LinkError(
            f"Function {program.function_id} transaction lifecycle lacks authored {missing}"
        )
    mutation_blocks = [
        block.id
        for block in blocks.values()
        if any(
            operation.opcode
            in {
                SemanticOpcode.WRITE,
                SemanticOpcode.APPEND_BYTES,
                SemanticOpcode.MOVE_SLOT,
                SemanticOpcode.STORE_FIELD,
                SemanticOpcode.FORMAT_INTEGER,
                SemanticOpcode.PUBLISH,
            }
            for operation in block.operations
        )
    ]
    for start in mutation_blocks:
        pending = [(start, False)]
        seen = set()
        while pending:
            block_id, finalized = pending.pop()
            if (block_id, finalized) in seen:
                continue
            seen.add((block_id, finalized))
            block = blocks[block_id]
            finalized |= any(
                op.opcode in {SemanticOpcode.CLEANUP, SemanticOpcode.PUBLISH}
                for op in block.operations
            )
            if (
                block.terminator in {TerminatorKind.RETURN, TerminatorKind.REFUSE}
                and not finalized
            ):
                raise LinkError(
                    f"Function {program.function_id} mutation block {start} "
                    f"exit block {block.id} lacks authored cleanup"
                )
            pending.extend((edge.target, finalized) for edge in block.edges)


def _emit_x86(program: SemanticProgram) -> NativeImage:
    plan = build_machine_plan(program, Architecture.X86_64)
    by = {slot.value_id: slot for slot in plan.frame}
    frame_end = max((slot.offset + slot.width for slot in plan.frame), default=64)
    frame_size = ((frame_end + max(32, plan.stack_argument_size) + 15) // 16) * 16
    code = bytearray(b"\x55\x48\x89\xe5\x53\x41\x54\x41\x55\x41\x56")
    code += b"\x48\x81\xec" + struct.pack("<I", frame_size)
    code += b"\x49\x89\xfc\x49\x89\xd5"  # context, output
    fixups: list[Fixup] = []
    branches: list[tuple[int, int]] = []
    missing_branches: list[int] = []
    labels: dict[int, int] = {}
    objects = tuple(sorted(program.binding_object_ids))
    functions = tuple(sorted(program.binding_function_ids))

    # Entry ABI semantic parameters are materialized once into SSA frame slots.
    semantic_entry = [
        x for x in program.x86_entry_abi if x.role == ABIRole.SEMANTIC_ARGUMENT
    ]
    for value, location in zip(program.graph.parameters, semantic_entry, strict=True):
        if (
            location.register_bank != ABIRegisterBank.INTEGER
            or len(location.register_indices) != 1
        ):
            raise LinkError(
                f"Function {program.function_id} entry value {value.id} ABI location {location.id} unsupported"
            )
        loads = {
            0: b"\x48\x89\xf8",
            1: b"\x48\x89\xf0",
            2: b"\x48\x89\xd0",
            3: b"\x48\x89\xc8",
            4: b"\x4c\x89\xc0",
            5: b"\x4c\x89\xc8",
        }
        code += loads[location.register_indices[0]]
        _store_x86(code, by[value.id], value.type.width)

    def missing_jump() -> None:
        missing_branches.append(len(code))
        code.extend(bytes(4))

    def resolve_object(durable: int) -> None:
        if durable not in objects:
            raise LinkError(
                f"Function {program.function_id} durable object {durable} is absent from binding slots"
            )
        index = objects.index(durable)
        code.extend(b"\x49\x83\x7c\x24\x20" + bytes((index + 1,)) + b"\x0f\x82")
        missing_jump()
        code.extend(b"\x4d\x8b\x5c\x24\x28")
        if index:
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * 80))
        code.extend(b"\x49\xb9" + struct.pack("<Q", durable))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x0b\x0f\x85")
        missing_jump()
        code.extend(b"\x4d\x8b\x5b\x18")
        fixups.append(
            Fixup(
                immediate,
                FixupKind.OBJECT_SLOT,
                FixupNamespace.OBJECT,
                durable,
                8,
                False,
            )
        )

    def resolve_function(durable: int) -> None:
        if durable not in functions:
            raise LinkError(
                f"Function {program.function_id} durable function {durable} is absent from binding slots"
            )
        index = functions.index(durable)
        code.extend(b"\x49\x83\x7c\x24\x10" + bytes((index + 1,)) + b"\x0f\x82")
        missing_jump()
        code.extend(b"\x4d\x8b\x5c\x24\x18")
        if index:
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * 24))
        code.extend(b"\x49\xba" + struct.pack("<Q", durable))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x13\x0f\x85")
        missing_jump()
        code.extend(b"\x4d\x8b\x5b\x10")
        fixups.append(
            Fixup(
                immediate,
                FixupKind.FUNCTION_SLOT,
                FixupNamespace.FUNCTION,
                durable,
                8,
                False,
            )
        )

    placements = dict(plan.calls)
    blocks = {block.id: block for block in program.graph.blocks}
    for block in program.graph.blocks:
        labels[block.id] = len(code)
        for operation in block.operations:
            if operation.opcode in IDENTITY_OPCODES:
                if len(operation.inputs) != 1 or len(operation.results) != 1:
                    raise LinkError(
                        f"SemanticOperation {operation.id} identity arity invalid"
                    )
                source = operation.inputs[0]
                result = operation.results[0]
                if (
                    operation.opcode == SemanticOpcode.RETURN_VALUE
                    and source.kind == SemanticValueKind.OBJECT
                ):
                    resolve_object(source.source_id)
                    for offset in range(0, result.type.width, 8):
                        code += b"\x49\x8b\x43" + bytes((offset,))
                        code += b"\x48\x89\x85" + struct.pack(
                            "<i", -(by[result.id].offset - offset)
                        )
                else:
                    _load_value_x86(code, source, by, resolve_object)
                    _store_x86(code, by[result.id], result.type.width)
            elif operation.opcode == SemanticOpcode.ENRICH_REFUSAL:
                enrichment = next(
                    item
                    for item in program.refusal_enrichments
                    if item.operation_id == operation.id
                )
                source = by[operation.inputs[0].id]
                target = by[operation.results[0].id]
                for offset in range(0, operation.results[0].type.width, 8):
                    code += b"\x48\x8b\x85" + struct.pack(
                        "<i", -(source.offset - offset)
                    )
                    code += b"\x48\x89\x85" + struct.pack(
                        "<i", -(target.offset - offset)
                    )
                if enrichment.source_status != enrichment.destination_status:
                    raise LinkError(
                        f"SemanticOperation {operation.id} non-identity enrichment requires constructor"
                    )
            elif operation.opcode == SemanticOpcode.MAP_REFUSAL:
                mapping = next(
                    item
                    for item in program.refusal_mappings
                    if item.operation_id == operation.id
                )
                source = by[operation.inputs[0].id]
                target = by[operation.results[0].id]
                for offset in range(0, operation.results[0].type.width, 8):
                    code += b"\x48\x8b\x85" + struct.pack(
                        "<i", -(source.offset - offset)
                    )
                    code += b"\x48\x89\x85" + struct.pack(
                        "<i", -(target.offset - offset)
                    )
                code += b"\x8b\x85" + struct.pack(
                    "<i", -(source.offset - mapping.payload_offset)
                )
                for source_status, destination_status in mapping.entries:
                    code += b"\x3d" + struct.pack("<I", source_status)
                    code += b"\x75\x07"
                    code += b"\xb8" + struct.pack("<I", destination_status)
                    code += b"\xeb\x00"
                code += b"\x89\x85" + struct.pack(
                    "<i", -(target.offset - mapping.payload_offset)
                )
            elif operation.opcode in {
                SemanticOpcode.READ_TAG,
                SemanticOpcode.PROJECT_PAYLOAD,
            }:
                field = next(
                    field
                    for field in program.fields
                    if field.id == operation.target_field_id
                )
                _load_value_x86(code, operation.inputs[0], by, resolve_object)
                code += b"\x48\x8d\x80" + struct.pack("<i", field.offset)
                code += (
                    b"\x8b\x00"
                    if operation.results[0].type.width <= 4
                    else b"\x48\x8b\x00"
                )
                _store_x86(
                    code, by[operation.results[0].id], operation.results[0].type.width
                )
            elif operation.opcode in {
                SemanticOpcode.CONSTRUCT_OPTION,
                SemanticOpcode.CONSTRUCT_RESULT,
            }:
                field = next(
                    field
                    for field in program.fields
                    if field.id == operation.target_field_id
                )
                variant = next(
                    v for v in program.tagged_variants if v.payload_field_id == field.id
                )
                code += b"\x48\xb8" + struct.pack("<Q", variant.tag_value)
                _store_x86(code, by[operation.results[0].id], 4)
                _load_value_x86(code, operation.inputs[0], by, resolve_object)
                target = by[operation.results[0].id]
                code += b"\x48\x89\x85" + struct.pack(
                    "<i", -(target.offset - field.offset)
                )
            elif operation.opcode == SemanticOpcode.ITER_CONDITION:
                if len(operation.inputs) == 1 and len(operation.results) == 1:
                    if operation.inputs[0].type != operation.results[0].type:
                        raise LinkError(
                            f"SemanticOperation {operation.id} unary iterator condition type mismatch"
                        )
                    _load_value_x86(code, operation.inputs[0], by, resolve_object)
                    _store_x86(
                        code,
                        by[operation.results[0].id],
                        operation.results[0].type.width,
                    )
                elif len(operation.inputs) == 2 and len(operation.results) == 1:
                    _load_value_x86(code, operation.inputs[0], by, resolve_object)
                    code += b"\x48\x3b\x85" + struct.pack(
                        "<i", -by[operation.inputs[1].id].offset
                    )
                    code += b"\x0f\x92\xc0"
                    _store_x86(code, by[operation.results[0].id], 1)
                else:
                    raise LinkError(
                        f"SemanticOperation {operation.id} iterator condition arity "
                        f"{len(operation.inputs)}->{len(operation.results)} is unsupported"
                    )
            elif operation.opcode == SemanticOpcode.ITER_PROJECT:
                _load_value_x86(code, operation.inputs[0], by, resolve_object)
                _store_x86(code, by[operation.results[0].id], 8)
            elif operation.opcode == SemanticOpcode.ITER_ADVANCE:
                _load_value_x86(code, operation.inputs[0], by, resolve_object)
                code += b"\x48\x83\xc0\x01"
                _store_x86(code, by[operation.results[0].id], 8)
            elif operation.opcode in CALL_OPCODES:
                if operation.id not in placements:
                    raise LinkError(
                        f"Function {program.function_id} SemanticOperation {operation.id} "
                        f"{operation.opcode.name} has no persisted callee ABI"
                    )
                _emit_call_x86(
                    code,
                    program,
                    operation,
                    placements[operation.id],
                    by,
                    resolve_object,
                    resolve_function,
                )
            else:
                values = tuple(v.id for v in (*operation.inputs, *operation.results))
                raise LinkError(
                    f"Function {program.function_id} SemanticOperation {operation.id} unsupported native opcode {operation.opcode.name}; values {values}"
                )
        term = block.terminator
        if term == TerminatorKind.GOTO:
            _edge_copies_x86(
                code,
                block.edges[0],
                blocks[block.edges[0].target].parameters,
                by,
                plan.scratch_offset,
            )
            code += b"\xe9"
            at = len(code)
            code += bytes(4)
            branches.append((at, block.edges[0].target))
        elif term == TerminatorKind.SWITCH_TAG:
            condition = block.condition
            if condition is None or len(block.edges) != len(block.case_tags) + 1:
                raise LinkError(
                    f"Function {program.function_id} switch block {block.id} shape is invalid"
                )
            _load_value_x86(code, condition, by, resolve_object)
            case_jumps = []
            for tag, edge in zip(block.case_tags, block.edges[:-1], strict=True):
                code += b"\x48\x83\xf8" + bytes((tag,)) + b"\x0f\x84"
                at = len(code)
                code += bytes(4)
                case_jumps.append((at, edge))
            default_edge = block.edges[-1]
            _edge_copies_x86(
                code,
                default_edge,
                blocks[default_edge.target].parameters,
                by,
                plan.scratch_offset,
            )
            code += b"\xe9"
            at = len(code)
            code += bytes(4)
            branches.append((at, default_edge.target))
            for at, edge in case_jumps:
                trampoline = len(code)
                struct.pack_into("<i", code, at, trampoline - at - 4)
                fixups.append(
                    Fixup(
                        at,
                        FixupKind.LOCAL_BRANCH,
                        FixupNamespace.TEXT,
                        trampoline,
                        4,
                        True,
                    )
                )
                _edge_copies_x86(
                    code, edge, blocks[edge.target].parameters, by, plan.scratch_offset
                )
                code += b"\xe9"
                jump = len(code)
                code += bytes(4)
                branches.append((jump, edge.target))
        elif term in {TerminatorKind.BRANCH, TerminatorKind.LOOP}:
            condition = block.condition
            if condition is None:
                raise LinkError(
                    f"Function {program.function_id} branch {block.id} has no condition"
                )
            if term == TerminatorKind.LOOP and condition.type.id != BOOL_TYPE:
                raise LinkError(
                    f"Function {program.function_id} loop block {block.id} condition is not explicit BOOL"
                )
            _load_value_x86(code, condition, by, resolve_object)
            code += b"\x85\xc0" + (
                b"\x0f\x84" if condition.type.id == U32_TYPE else b"\x0f\x85"
            )
            true_at = len(code)
            code += bytes(4)
            # False-edge copies execute before its target jump. The conditional
            # branch lands on a separate true-edge copy trampoline.
            _edge_copies_x86(
                code,
                block.edges[1],
                blocks[block.edges[1].target].parameters,
                by,
                plan.scratch_offset,
            )
            code += b"\xe9"
            false_target_at = len(code)
            code += bytes(4)
            branches.append((false_target_at, block.edges[1].target))
            true_copies = len(code)
            struct.pack_into("<i", code, true_at, true_copies - true_at - 4)
            fixups.append(
                Fixup(
                    true_at,
                    FixupKind.LOCAL_BRANCH,
                    FixupNamespace.TEXT,
                    true_copies,
                    4,
                    True,
                )
            )
            _edge_copies_x86(
                code,
                block.edges[0],
                blocks[block.edges[0].target].parameters,
                by,
                plan.scratch_offset,
            )
            code += b"\xe9"
            true_target_at = len(code)
            code += bytes(4)
            branches.append((true_target_at, block.edges[0].target))
        elif term in {TerminatorKind.RETURN, TerminatorKind.REFUSE}:
            value = block.terminator_value
            if value is None:
                raise LinkError(
                    f"Function {program.function_id} exit {block.id} has no value"
                )
            _load_value_x86(code, value, by, resolve_object)
            if term == TerminatorKind.RETURN:
                if value.type.width > 8:
                    for offset in range(0, value.type.width, 8):
                        code += b"\x48\x8b\x85" + struct.pack(
                            "<i", -(by[value.id].offset - offset)
                        )
                        code += b"\x49\x89\x45" + bytes((offset,))
                else:
                    code += b"\x49\x89\x45\x00"
                code += b"\x31\xc0"
            code += (
                b"\x48\x81\xc4"
                + struct.pack("<I", frame_size)
                + b"\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
            )
        else:
            raise LinkError(
                f"Function {program.function_id} terminator {term.name} unsupported"
            )
    missing = len(code)
    code += (
        b"\xb8\xfe\xff\xff\xff\x48\x81\xc4"
        + struct.pack("<I", frame_size)
        + b"\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
    )
    for at, target in branches:
        struct.pack_into("<i", code, at, labels[target] - at - 4)
        fixups.append(
            Fixup(
                at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, labels[target], 4, True
            )
        )
    for at in missing_branches:
        struct.pack_into("<i", code, at, missing - at - 4)
        fixups.append(
            Fixup(at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, missing, 4, True)
        )
    _validate_fixups(code, fixups)
    return NativeImage(
        Architecture.X86_64,
        program.function_id,
        bytes(code),
        b"",
        functions,
        objects,
        tuple(fixups),
        plan.frame,
        program.semantic_digest,
    )


def _load_value_x86(code, value: SemanticIRValue, by, resolve_object) -> None:
    if value.kind == SemanticValueKind.LITERAL:
        code.extend(b"\x48\xb8" + value.literal.ljust(8, b"\0")[:8])
    elif value.kind == SemanticValueKind.OBJECT:
        resolve_object(value.source_id)
        code.extend(b"\x4c\x89\xd8")
    else:
        slot = by.get(value.id)
        if slot is None:
            raise LinkError(f"SemanticValue {value.id} has no frame slot")
        code.extend(
            (
                b"\x8a\x85"
                if value.type.width == 1
                else b"\x8b\x85"
                if value.type.width <= 4
                else b"\x48\x8b\x85"
            )
            + struct.pack("<i", -slot.offset)
        )


def _store_x86(code, slot, width: int) -> None:
    code.extend(
        (b"\x88\x85" if width == 1 else b"\x89\x85" if width <= 4 else b"\x48\x89\x85")
        + struct.pack("<i", -slot.offset)
    )


def _edge_copies_x86(code, edge, parameters, by, scratch) -> None:
    direct = []
    copies = []
    for source, destination in zip(edge.arguments, parameters, strict=True):
        if source.kind in {SemanticValueKind.LITERAL, SemanticValueKind.OBJECT}:
            direct.append((source, destination))
        else:
            copies.append(ParallelMove(source.id, destination.id))
    scratch_id = -(scratch + 1)
    for move in schedule_parallel_copies(tuple(copies), scratch_id):
        if move.source == scratch_id:
            code.extend(b"\x48\x8b\x85" + struct.pack("<i", -scratch))
        elif move.source not in by:
            raise LinkError(f"phi source SemanticValue {move.source} has no frame slot")
        else:
            source = by[move.source]
            code.extend(b"\x48\x8b\x85" + struct.pack("<i", -source.offset))
        if move.destination == scratch_id:
            code.extend(b"\x48\x89\x85" + struct.pack("<i", -scratch))
        else:
            destination = by[move.destination]
            code.extend(b"\x48\x89\x85" + struct.pack("<i", -destination.offset))
    for source, destination in direct:
        if source.kind == SemanticValueKind.OBJECT:
            raise LinkError(
                f"phi object SemanticValue {source.id} requires durable resolution"
            )
        code.extend(b"\x48\xb8" + source.literal.ljust(8, b"\0")[:8])
        _store_x86(code, by[destination.id], destination.type.width)


def _emit_call_x86(
    code, program, operation, placements, by, resolve_object, resolve_function
) -> None:
    regs = {
        0: b"\x48\x89\xc7",
        1: b"\x48\x89\xc6",
        2: b"\x48\x89\xc2",
        3: b"\x48\x89\xc1",
        4: b"\x49\x89\xc0",
        5: b"\x49\x89\xc1",
    }
    for placement in placements:
        loc, value = placement.location, placement.value
        if loc.role == ABIRole.STATUS_RETURN:
            continue
        if loc.role == ABIRole.HIDDEN_CONTEXT:
            code.extend(b"\x4c\x89\xe0")
        elif loc.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}:
            if value is None:
                raise LinkError(
                    f"SemanticOperation {operation.id} ABI output {loc.id} has no value"
                )
            code.extend(b"\x48\x8d\x85" + struct.pack("<i", -by[value.id].offset))
        elif value is None:
            raise LinkError(
                f"SemanticOperation {operation.id} ABI location {loc.id} has no value"
            )
        else:
            _load_value_x86(code, value, by, resolve_object)
        if loc.register_bank == ABIRegisterBank.STACK:
            if value is None:
                raise LinkError(
                    f"SemanticOperation {operation.id} stack ABI location {loc.id} has no value"
                )
            off = loc.stack_offset
            if loc.passing in {
                ABIPassingMode.INDIRECT_BY_REFERENCE,
                ABIPassingMode.TRANSACTION_POINTER,
            }:
                if value.id in by:
                    code.extend(
                        b"\x48\x8d\x85" + struct.pack("<i", -by[value.id].offset)
                    )
                elif value.kind == SemanticValueKind.LITERAL:
                    code.extend(b"\x48\xb8" + value.literal.ljust(8, b"\0")[:8])
                else:
                    raise LinkError(
                        f"SemanticOperation {operation.id} indirect stack value {value.id} has no frame slot"
                    )
                code.extend(b"\x48\x89\x84\x24" + struct.pack("<I", off))
                continue
            if (
                loc.passing != ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
                or value.type.width != 24
            ):
                raise LinkError(
                    f"SemanticOperation {operation.id} stack ABI location {loc.id} unsupported"
                )
            code.extend(
                b"\x49\x8b\x03\x48\x89\x84\x24"
                + struct.pack("<I", off)
                + b"\x49\x8b\x43\x08\x48\x89\x84\x24"
                + struct.pack("<I", off + 8)
                + b"\x49\x8b\x43\x10\x48\x89\x84\x24"
                + struct.pack("<I", off + 16)
            )
            continue
        if (
            loc.register_bank != ABIRegisterBank.INTEGER
            or len(loc.register_indices) != 1
        ):
            raise LinkError(
                f"SemanticOperation {operation.id} ABI location {loc.id} unsupported"
            )
        code.extend(regs[loc.register_indices[0]])
    resolve_function(operation.callee_id)
    code.extend(b"\x41\xff\xd3")
    if operation.status is None:
        raise LinkError(f"SemanticOperation {operation.id} status absent")
    _store_x86(code, by[operation.status.id], 4)


def _emit_arm(program: SemanticProgram) -> NativeImage:
    plan = build_machine_plan(program, Architecture.AARCH64)
    by = {slot.value_id: slot for slot in plan.frame}
    frame_end = max((slot.offset + slot.width for slot in plan.frame), default=64)
    size = ((frame_end + max(32, plan.stack_argument_size) + 31) // 16) * 16
    if size > 4095:
        raise LinkError(
            f"Function {program.function_id} AArch64 frame {size} exceeds immediate"
        )
    words = [
        0xD10003FF | (size << 10),
        0xA9007BFD,
        0xA90153F3,
        0x910003FD,
        0xAA0003F4,
        0xAA0203F3,
    ]
    fixups: list[Fixup] = []
    labels: dict[int, int] = {}
    branches: list[tuple[int, int, int]] = []
    objects = tuple(sorted(program.binding_object_ids))
    functions = tuple(sorted(program.binding_function_ids))
    blocks = {block.id: block for block in program.graph.blocks}
    values = {value.id: value for value in program.graph.parameters}
    for block in program.graph.blocks:
        values.update((value.id, value) for value in block.parameters)
        for operation in block.operations:
            values.update((value.id, value) for value in operation.results)

    def object_ptr(durable: int, reg: int = 8) -> None:
        if durable not in objects:
            raise LinkError(
                f"Function {program.function_id} durable object {durable} is absent from binding slots"
            )
        index = objects.index(durable)
        words.extend(
            (
                0xF9401680,
                0x91000000 | ((index * 80) << 10) | reg,
                0xF9400000 | (3 << 10) | (reg << 5) | reg,
            )
        )
        fixups.append(
            Fixup(
                (len(words) - 2) * 4,
                FixupKind.OBJECT_SLOT,
                FixupNamespace.OBJECT,
                durable,
                4,
                False,
                4,
                index * 80,
            )
        )

    def load(value: SemanticIRValue, reg: int = 8) -> None:
        if value.kind == SemanticValueKind.LITERAL:
            number = int.from_bytes(value.literal, "little")
            if number >= 1 << 64:
                raise LinkError(
                    f"SemanticValue {value.id} AArch64 literal exceeds uint64"
                )
            words.append(0xD2800000 | ((number & 0xFFFF) << 5) | reg)
            for shift in range(1, 4):
                part = (number >> (shift * 16)) & 0xFFFF
                if part:
                    words.append(0xF2800000 | (shift << 21) | (part << 5) | reg)
        elif value.kind == SemanticValueKind.OBJECT:
            object_ptr(value.source_id, reg)
        else:
            slot = by.get(value.id)
            if slot is None:
                raise LinkError(f"SemanticValue {value.id} has no frame slot")
            base = (
                0x394003E0
                if value.type.width == 1
                else 0xB94003E0
                if value.type.width <= 4
                else 0xF94003E0
            )
            scale = 1 if value.type.width == 1 else 4 if value.type.width <= 4 else 8
            words.append(base | ((slot.offset // scale) << 10) | reg)

    def store(value_id: int, width: int, reg: int = 8) -> None:
        slot = by[value_id]
        base = 0x390003E0 if width == 1 else 0xB90003E0 if width <= 4 else 0xF90003E0
        scale = 1 if width == 1 else 4 if width <= 4 else 8
        words.append(base | ((slot.offset // scale) << 10) | reg)

    def edge_copies(edge, parameters) -> None:
        copies = tuple(
            ParallelMove(a.id, p.id)
            for a, p in zip(edge.arguments, parameters, strict=True)
            if a.kind not in {SemanticValueKind.LITERAL, SemanticValueKind.OBJECT}
        )
        scratch_id = -(plan.scratch_offset + 1)
        for move in schedule_parallel_copies(copies, scratch_id):
            if move.source == scratch_id:
                words.append(0xF94003E8 | ((plan.scratch_offset // 8) << 10))
            else:
                source = values.get(move.source)
                if source is None:
                    raise LinkError(
                        f"Function {program.function_id} phi source SemanticValue "
                        f"{move.source} is unresolved on AArch64"
                    )
                load(source)
            if move.destination == scratch_id:
                words.append(0xF90003E8 | ((plan.scratch_offset // 8) << 10))
            else:
                destination = next(p for p in parameters if p.id == move.destination)
                store(destination.id, destination.type.width)
        for argument, parameter in zip(edge.arguments, parameters, strict=True):
            if argument.kind in {SemanticValueKind.LITERAL, SemanticValueKind.OBJECT}:
                load(argument)
                store(parameter.id, parameter.type.width)

    semantic_entry = [
        x for x in program.arm_entry_abi if x.role == ABIRole.SEMANTIC_ARGUMENT
    ]
    for value, location in zip(program.graph.parameters, semantic_entry, strict=True):
        if (
            location.register_bank != ABIRegisterBank.INTEGER
            or len(location.register_indices) != 1
        ):
            raise LinkError(
                f"Function {program.function_id} entry ABI location {location.id} unsupported"
            )
        store(value.id, value.type.width, location.register_indices[0])

    placements = dict(plan.calls)
    for block in program.graph.blocks:
        labels[block.id] = len(words)
        for operation in block.operations:
            if operation.opcode in IDENTITY_OPCODES:
                source, result = operation.inputs[0], operation.results[0]
                if (
                    source.kind == SemanticValueKind.OBJECT
                    and operation.opcode == SemanticOpcode.RETURN_VALUE
                ):
                    object_ptr(source.source_id)
                    target = by[result.id]
                    for offset in range(0, result.type.width, 8):
                        words.append(0xF9400109 | ((offset // 8) << 10))
                        words.append(
                            0xF90003E9 | (((target.offset + offset) // 8) << 10)
                        )
                else:
                    load(source)
                    store(result.id, result.type.width)
            elif operation.opcode == SemanticOpcode.ENRICH_REFUSAL:
                enrichment = next(
                    item
                    for item in program.refusal_enrichments
                    if item.operation_id == operation.id
                )
                source = by[operation.inputs[0].id]
                target = by[operation.results[0].id]
                for offset in range(0, operation.results[0].type.width, 8):
                    words.append(0xF94003E8 | (((source.offset + offset) // 8) << 10))
                    words.append(0xF90003E8 | (((target.offset + offset) // 8) << 10))
                if enrichment.source_status != enrichment.destination_status:
                    raise LinkError(
                        f"SemanticOperation {operation.id} non-identity enrichment requires constructor"
                    )
            elif operation.opcode == SemanticOpcode.MAP_REFUSAL:
                mapping = next(
                    item
                    for item in program.refusal_mappings
                    if item.operation_id == operation.id
                )
                source = by[operation.inputs[0].id]
                target = by[operation.results[0].id]
                for offset in range(0, operation.results[0].type.width, 8):
                    words.append(0xF94003E8 | (((source.offset + offset) // 8) << 10))
                    words.append(0xF90003E8 | (((target.offset + offset) // 8) << 10))
                words.append(
                    0xB94003E8 | (((source.offset + mapping.payload_offset) // 4) << 10)
                )
                for source_status, destination_status in mapping.entries:
                    if source_status > 0xFFF or destination_status > 0xFFFF:
                        raise LinkError(
                            f"SemanticOperation {operation.id} refusal status exceeds immediate"
                        )
                    words.extend(
                        (
                            0x7100011F | (source_status << 10),
                            0x54000041,
                            0x52800008 | (destination_status << 5),
                        )
                    )
                words.append(
                    0xB90003E8 | (((target.offset + mapping.payload_offset) // 4) << 10)
                )
            elif operation.opcode in {
                SemanticOpcode.READ_TAG,
                SemanticOpcode.PROJECT_PAYLOAD,
            }:
                field = next(
                    field
                    for field in program.fields
                    if field.id == operation.target_field_id
                )
                load(operation.inputs[0], 8)
                base = (
                    0xB9400109 if operation.results[0].type.width <= 4 else 0xF9400109
                )
                scale = 4 if operation.results[0].type.width <= 4 else 8
                words.append(base | ((field.offset // scale) << 10))
                store(operation.results[0].id, operation.results[0].type.width, 9)
            elif operation.opcode in {
                SemanticOpcode.CONSTRUCT_OPTION,
                SemanticOpcode.CONSTRUCT_RESULT,
            }:
                field = next(
                    field
                    for field in program.fields
                    if field.id == operation.target_field_id
                )
                variant = next(
                    v for v in program.tagged_variants if v.payload_field_id == field.id
                )
                words.append(0xD2800008 | (variant.tag_value << 5))
                store(operation.results[0].id, 4, 8)
                load(operation.inputs[0], 9)
                target = by[operation.results[0].id]
                words.append(0xF90003E9 | (((target.offset + field.offset) // 8) << 10))
            elif operation.opcode == SemanticOpcode.ITER_CONDITION:
                if len(operation.inputs) == 1 and len(operation.results) == 1:
                    if operation.inputs[0].type != operation.results[0].type:
                        raise LinkError(
                            f"SemanticOperation {operation.id} unary iterator condition type mismatch"
                        )
                    load(operation.inputs[0], 8)
                    store(operation.results[0].id, operation.results[0].type.width, 8)
                elif len(operation.inputs) == 2 and len(operation.results) == 1:
                    load(operation.inputs[0], 8)
                    load(operation.inputs[1], 9)
                    words.extend((0xEB09011F, 0x9A9F27E8))
                    store(operation.results[0].id, 1, 8)
                else:
                    raise LinkError(
                        f"SemanticOperation {operation.id} iterator condition arity "
                        f"{len(operation.inputs)}->{len(operation.results)} is unsupported"
                    )
            elif operation.opcode == SemanticOpcode.ITER_PROJECT:
                load(operation.inputs[0], 8)
                store(operation.results[0].id, 8, 8)
            elif operation.opcode == SemanticOpcode.ITER_ADVANCE:
                load(operation.inputs[0], 8)
                words.append(0x91000508)
                store(operation.results[0].id, 8, 8)
            elif operation.opcode in CALL_OPCODES:
                if operation.id not in placements:
                    raise LinkError(
                        f"Function {program.function_id} SemanticOperation {operation.id} "
                        f"{operation.opcode.name} has no persisted callee ABI"
                    )
                for placement in placements[operation.id]:
                    location, value = placement.location, placement.value
                    if location.role == ABIRole.STATUS_RETURN:
                        continue
                    reg = (
                        location.register_indices[0] if location.register_indices else 8
                    )
                    if location.role == ABIRole.HIDDEN_CONTEXT:
                        words.append(0xAA1403E0 | reg)
                    elif location.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}:
                        assert value is not None
                        words.append(0x910003E0 | (by[value.id].offset << 10) | reg)
                    elif value is None:
                        raise LinkError(
                            f"SemanticOperation {operation.id} ABI location {location.id} has no value"
                        )
                    else:
                        load(value, reg)
                if operation.callee_id not in functions:
                    raise LinkError(
                        f"Function {program.function_id} durable function {operation.callee_id} "
                        "is absent from binding slots"
                    )
                index = functions.index(operation.callee_id)
                words.extend(
                    (
                        0xF9400E90,
                        0x91000210 | ((index * 24) << 10),
                        0xF9400A10,
                        0xD63F0200,
                    )
                )
                fixups.append(
                    Fixup(
                        (len(words) - 3) * 4,
                        FixupKind.FUNCTION_SLOT,
                        FixupNamespace.FUNCTION,
                        operation.callee_id,
                        4,
                        False,
                        4,
                        index * 24,
                    )
                )
                assert operation.status is not None
                store(operation.status.id, 4, 0)
        if block.terminator == TerminatorKind.GOTO:
            edge_copies(block.edges[0], blocks[block.edges[0].target].parameters)
            branches.append((len(words), block.edges[0].target, 0x14000000))
            words.append(0)
        elif block.terminator == TerminatorKind.SWITCH_TAG:
            condition = block.condition
            if condition is None or len(block.edges) != len(block.case_tags) + 1:
                raise LinkError(
                    f"Function {program.function_id} switch block {block.id} shape is invalid"
                )
            load(condition, 8)
            case_jumps = []
            for tag, edge in zip(block.case_tags, block.edges[:-1], strict=True):
                words.append(0xF100011F | (tag << 10))
                case_jumps.append((len(words), edge))
                words.append(0)
            default_edge = block.edges[-1]
            edge_copies(default_edge, blocks[default_edge.target].parameters)
            branches.append((len(words), default_edge.target, 0x14000000))
            words.append(0)
            for at, edge in case_jumps:
                trampoline = len(words)
                words[at] = 0x54000000 | (((trampoline - at) & 0x7FFFF) << 5)
                fixups.append(
                    Fixup(
                        at * 4,
                        FixupKind.LOCAL_BRANCH,
                        FixupNamespace.TEXT,
                        trampoline * 4,
                        4,
                        True,
                        4,
                    )
                )
                edge_copies(edge, blocks[edge.target].parameters)
                branches.append((len(words), edge.target, 0x14000000))
                words.append(0)
        elif block.terminator in {TerminatorKind.BRANCH, TerminatorKind.LOOP}:
            assert block.condition is not None
            load(block.condition, 8)
            true_at = len(words)
            words.append(0)
            edge_copies(block.edges[1], blocks[block.edges[1].target].parameters)
            branches.append((len(words), block.edges[1].target, 0x14000000))
            words.append(0)
            true_copy = len(words)
            base = 0x34000008 if block.condition.type.id == U32_TYPE else 0x35000008
            words[true_at] = base | (((true_copy - true_at) & 0x7FFFF) << 5)
            fixups.append(
                Fixup(
                    true_at * 4,
                    FixupKind.LOCAL_BRANCH,
                    FixupNamespace.TEXT,
                    true_copy * 4,
                    4,
                    True,
                    4,
                )
            )
            edge_copies(block.edges[0], blocks[block.edges[0].target].parameters)
            branches.append((len(words), block.edges[0].target, 0x14000000))
            words.append(0)
        elif block.terminator in {TerminatorKind.RETURN, TerminatorKind.REFUSE}:
            assert block.terminator_value is not None
            value = block.terminator_value
            load(value, 8)
            if block.terminator == TerminatorKind.RETURN:
                if value.type.width > 8:
                    slot = by.get(value.id)
                    if slot is None:
                        raise LinkError(
                            f"Function {program.function_id} return SemanticValue {value.id} has no frame slot"
                        )
                    for offset in range(0, value.type.width, 8):
                        words.append(0xF94003E8 | (((slot.offset + offset) // 8) << 10))
                        words.append(0xF9000268 | ((offset // 8) << 10))
                else:
                    words.append(0xF9000268)
                words.append(0x52800000)
            words.extend(
                (0xA94153F3, 0xA9407BFD, 0x910003FF | (size << 10), 0xD65F03C0)
            )
        else:
            raise LinkError(
                f"Function {program.function_id} terminator {block.terminator.name} unsupported"
            )
    for at, target, base in branches:
        words[at] = base | ((labels[target] - at) & 0x3FFFFFF)
        fixups.append(
            Fixup(
                at * 4,
                FixupKind.LOCAL_BRANCH,
                FixupNamespace.TEXT,
                labels[target] * 4,
                4,
                True,
                4,
            )
        )
    text = b"".join(struct.pack("<I", word) for word in words)
    _validate_fixups(bytearray(text), fixups)
    return NativeImage(
        Architecture.AARCH64,
        program.function_id,
        text,
        b"",
        functions,
        objects,
        tuple(fixups),
        plan.frame,
        program.semantic_digest,
    )
