"""Shared native backend for one-call status-explicit scalar SemanticGraphs."""

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
from pymergetic.rxf.compiler.semantic import SemanticIROperation, SemanticValueKind
from pymergetic.rxf.compiler.semantic_lower import SemanticProgram
from pymergetic.rxf.compiler.semantic_machine import build_machine_plan
from pymergetic.rxf.model.contracts import (
    ABIPassingMode,
    ABIRegisterBank,
    ABIRole,
)
from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode, TerminatorKind


def emit_single_call(
    ir: SemanticProgram, call: SemanticIROperation, architecture: Architecture
) -> NativeImage:
    plan = build_machine_plan(ir, architecture)
    blocks = ir.graph.blocks
    entry = next(block for block in blocks if block.id == ir.graph.entry_block_id)
    success = tuple(
        block for block in blocks if block.terminator == TerminatorKind.RETURN
    )
    refusal = tuple(
        block for block in blocks if block.terminator == TerminatorKind.REFUSE
    )
    if (
        call.status is None
        or entry.condition != call.status
        or not entry.operations
        or entry.operations[-1] != call
        or len(success) != 1
        or len(refusal) != 1
    ):
        raise LinkError(
            f"Function {ir.function_id} SemanticOperation {call.id} is not an immediate status CFG"
        )
    for edge in entry.edges:
        if len(edge.arguments) != 1 or edge.arguments[0] != call.status:
            ids = tuple(value.id for value in edge.arguments)
            raise LinkError(
                f"Function {ir.function_id} block {entry.id} status phi arguments {ids} disagree with value {call.status.id}"
            )
    return (
        _x86(ir, call, plan)
        if architecture == Architecture.X86_64
        else _arm(ir, call, plan)
    )


def _return_value(ir: SemanticProgram):
    returns = tuple(
        op.source
        for op in ir.operations
        if op.source.opcode == SemanticOpcode.RETURN_VALUE
    )
    if len(returns) != 1 or len(returns[0].inputs) != 1:
        raise LinkError(f"Function {ir.function_id} requires one scalar RETURN_VALUE")
    value = returns[0].inputs[0]
    while value.kind == SemanticValueKind.OP_RESULT:
        producer = next(
            (op.source for op in ir.operations if value in op.source.results),
            None,
        )
        if producer is None or producer.opcode != SemanticOpcode.UTF8_BOUNDARY:
            break
        value = producer.inputs[0]
    return value


def _x86(ir: SemanticProgram, call: SemanticIROperation, plan) -> NativeImage:
    by = {slot.value_id: slot for slot in plan.frame}
    stack_bytes = max(32, (plan.stack_argument_size + 15) & -16)
    frame_end = max((slot.offset + slot.width for slot in plan.frame), default=64)
    frame_size = ((frame_end + stack_bytes + 15) // 16) * 16
    code = bytearray(b"\x55\x48\x89\xe5\x53\x41\x54\x41\x55\x41\x56")
    code += b"\x48\x81\xec" + struct.pack("<I", frame_size)
    code += b"\x49\x89\xfc\x49\x89\xd5"  # context r12, output r13
    fixups: list[Fixup] = []
    missing_jumps: list[int] = []
    objects = tuple(sorted(ir.binding_object_ids))
    functions = tuple(sorted(ir.binding_function_ids))

    def resolve_object(durable: int) -> None:
        index = objects.index(durable)
        code.extend(b"\x49\x83\x7c\x24\x20" + bytes((index + 1,)) + b"\x0f\x82")
        missing_jumps.append(len(code))
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5c\x24\x28")
        if index:
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * 80))
        code.extend(b"\x49\xb9" + struct.pack("<Q", durable))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x0b\x0f\x85")
        missing_jumps.append(len(code))
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5b\x10")
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
        index = functions.index(durable)
        code.extend(b"\x49\x83\x7c\x24\x10" + bytes((index + 1,)) + b"\x0f\x82")
        missing_jumps.append(len(code))
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5c\x24\x18")
        if index:
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * 24))
        code.extend(b"\x49\xba" + struct.pack("<Q", durable))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x13\x0f\x85")
        missing_jumps.append(len(code))
        code.extend(bytes(4))
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

    placements = dict(plan.calls)[call.id]
    int_regs = {
        0: b"\x48\x89\xc7",
        1: b"\x48\x89\xc6",
        2: b"\x48\x89\xc2",
        3: b"\x48\x89\xc1",
        4: b"\x49\x89\xc0",
        5: b"\x49\x89\xc1",
    }
    for placement in placements:
        location, value = placement.location, placement.value
        if location.role == ABIRole.STATUS_RETURN:
            continue
        if location.role == ABIRole.HIDDEN_CONTEXT:
            code += b"\x4c\x89\xe0"
        elif location.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}:
            assert value is not None
            code += b"\x48\x8d\x85" + struct.pack("<i", -by[value.id].offset)
        elif value is None:
            raise LinkError(
                f"SemanticOperation {call.id} ABI location {location.id} has no value"
            )
        elif value.kind == SemanticValueKind.OBJECT:
            resolve_object(value.source_id)
            if location.register_bank == ABIRegisterBank.STACK:
                if (
                    location.passing != ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
                    or value.type.width != 24
                ):
                    raise LinkError(
                        f"SemanticOperation {call.id} value {value.id} unsupported SysV stack aggregate location {location.id}"
                    )
                off = location.stack_offset
                code += b"\x49\x8b\x03\x48\x89\x84\x24" + struct.pack("<I", off)
                code += b"\x49\x8b\x43\x08\x48\x89\x84\x24" + struct.pack("<I", off + 8)
                code += b"\x49\x8b\x43\x10\x48\x89\x84\x24" + struct.pack(
                    "<I", off + 16
                )
                continue
            code += b"\x4c\x89\xd8"
        elif value.kind == SemanticValueKind.LITERAL:
            code += b"\x48\xb8" + value.literal.ljust(8, b"\0")[:8]
        elif value.kind in {
            SemanticValueKind.OP_RESULT,
            SemanticValueKind.BLOCK_PARAMETER,
        }:
            code += b"\x48\x8b\x85" + struct.pack("<i", -by[value.id].offset)
        elif value.kind == SemanticValueKind.PARAMETER:
            raise LinkError(
                f"Function {ir.function_id} SemanticValue {value.id} parameter reload is not implemented"
            )
        else:
            raise LinkError(
                f"SemanticOperation {call.id} value {value.id} kind {value.kind.name} is unsupported"
            )
        if (
            location.register_bank != ABIRegisterBank.INTEGER
            or len(location.register_indices) != 1
        ):
            raise LinkError(
                f"SemanticOperation {call.id} value {value.id} ABI location {location.id} is unsupported"
            )
        code += int_regs[location.register_indices[0]]
    resolve_function(call.callee_id)
    code += b"\x41\xff\xd3"
    if call.status is None:
        raise LinkError(f"SemanticOperation {call.id} status is absent")
    code += b"\x89\x85" + struct.pack("<i", -by[call.status.id].offset)
    code += b"\x85\xc0\x0f\x85"
    refusal_at = len(code)
    code += bytes(4)
    result = _return_value(ir)
    if result.kind == SemanticValueKind.OP_RESULT:
        code += b"\x48\x8b\x85" + struct.pack("<i", -by[result.id].offset)
        code += b"\x49\x89\x45\x00"
    elif result.kind == SemanticValueKind.LITERAL:
        code += b"\x48\xb8" + result.literal.ljust(8, b"\0")[:8]
        code += b"\x49\x89\x45\x00"
    elif result.kind == SemanticValueKind.OBJECT:
        resolve_object(result.source_id)
        for offset in range(0, result.type.width, 8):
            code += b"\x49\x8b\x43" + bytes((offset,))
            code += b"\x49\x89\x45" + bytes((offset,))
    else:
        raise LinkError(
            f"Function {ir.function_id} return SemanticValue {result.id} is not materialized"
        )
    code += b"\x31\xc0\xe9"
    success_at = len(code)
    code += bytes(4)
    missing = len(code)
    code += b"\xb8\xfe\xff\xff\xff\xe9"
    missing_at = len(code)
    code += bytes(4)
    refusal = len(code)
    code += (
        b"\x48\x81\xc4"
        + struct.pack("<I", frame_size)
        + b"\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
    )
    for at, target in (
        (refusal_at, refusal),
        (success_at, refusal),
        (missing_at, refusal),
    ):
        struct.pack_into("<i", code, at, target - at - 4)
        fixups.append(
            Fixup(at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, target, 4, True)
        )
    for at in missing_jumps:
        struct.pack_into("<i", code, at, missing - at - 4)
        fixups.append(
            Fixup(at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, missing, 4, True)
        )
    _validate_fixups(code, fixups)
    return NativeImage(
        Architecture.X86_64,
        ir.function_id,
        bytes(code),
        b"",
        functions,
        objects,
        tuple(fixups),
        plan.frame,
        ir.semantic_digest,
    )


def _arm(ir: SemanticProgram, call: SemanticIROperation, plan) -> NativeImage:
    by = {slot.value_id: slot for slot in plan.frame}
    frame_end = max((slot.offset + slot.width for slot in plan.frame), default=64)
    size = ((frame_end + 31) // 16) * 16
    if size > 4095:
        raise LinkError(
            f"Function {ir.function_id} AArch64 frame {size} exceeds immediate"
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
    objects = tuple(sorted(ir.binding_object_ids))
    functions = tuple(sorted(ir.binding_function_ids))

    def object_ptr(durable: int, reg: int) -> None:
        index = objects.index(durable)
        words.append(0xF9401680)
        words.append(0x91000000 | ((index * 80) << 10) | reg)
        words.append(0xF9400000 | (3 << 10) | (reg << 5) | reg)
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

    placements = dict(plan.calls)[call.id]
    for placement in placements:
        location, value = placement.location, placement.value
        if location.role == ABIRole.STATUS_RETURN:
            continue
        if (
            location.register_bank != ABIRegisterBank.INTEGER
            or len(location.register_indices) != 1
        ):
            raise LinkError(
                f"SemanticOperation {call.id} ABI location {location.id} is unsupported on AArch64"
            )
        reg = location.register_indices[0]
        if location.role == ABIRole.HIDDEN_CONTEXT:
            words.append(0xAA1403E0 | reg)
        elif location.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}:
            assert value is not None
            words.append(0x910003E0 | (by[value.id].offset << 10) | reg)
        elif value is None:
            raise LinkError(
                f"SemanticOperation {call.id} ABI location {location.id} has no value"
            )
        elif value.kind == SemanticValueKind.OBJECT:
            object_ptr(value.source_id, reg)
        elif value.kind == SemanticValueKind.LITERAL:
            number = int.from_bytes(value.literal, "little")
            if number > 0xFFFF:
                raise LinkError(
                    f"SemanticValue {value.id} AArch64 literal exceeds movz"
                )
            words.append(0xD2800000 | (number << 5) | reg)
        elif value.kind in {
            SemanticValueKind.OP_RESULT,
            SemanticValueKind.BLOCK_PARAMETER,
        }:
            words.append(0xF94003E0 | ((by[value.id].offset // 8) << 10) | reg)
        else:
            raise LinkError(
                f"SemanticOperation {call.id} value {value.id} kind {value.kind.name} is unsupported"
            )
    index = functions.index(call.callee_id)
    words += [0xF9400E90, 0x91000210 | ((index * 24) << 10), 0xF9400A10]
    fixups.append(
        Fixup(
            (len(words) - 2) * 4,
            FixupKind.FUNCTION_SLOT,
            FixupNamespace.FUNCTION,
            call.callee_id,
            4,
            False,
            4,
            index * 24,
        )
    )
    words += [0xD63F0200, 0x35000000]
    refusal_at = len(words) - 1
    result = _return_value(ir)
    if result.kind == SemanticValueKind.OP_RESULT:
        words.append(0xF94003E8 | ((by[result.id].offset // 8) << 10))
        words.append(0xF9000268)
    elif result.kind == SemanticValueKind.OBJECT:
        object_ptr(result.source_id, 8)
        for offset in range(0, result.type.width, 8):
            words.append(0xF9400109 | ((offset // 8) << 10))
            words.append(0xF9000269 | ((offset // 8) << 10))
    else:
        raise LinkError(
            f"Function {ir.function_id} return SemanticValue {result.id} is unsupported on AArch64"
        )
    words += [0x52800000, 0x14000000]
    success_at = len(words) - 1
    refusal = len(words)
    words += [0xA94153F3, 0xA9407BFD, 0x910003FF | (size << 10), 0xD65F03C0]
    for at, base in ((refusal_at, 0x35000000), (success_at, 0x14000000)):
        delta = refusal - at
        words[at] = base | (
            ((delta & 0x7FFFF) << 5) if base == 0x35000000 else delta & 0x3FFFFFF
        )
        fixups.append(
            Fixup(
                at * 4,
                FixupKind.LOCAL_BRANCH,
                FixupNamespace.TEXT,
                refusal * 4,
                4,
                True,
                4,
            )
        )
    text = b"".join(struct.pack("<I", word) for word in words)
    _validate_fixups(bytearray(text), fixups)
    return NativeImage(
        Architecture.AARCH64,
        ir.function_id,
        text,
        b"",
        functions,
        objects,
        tuple(fixups),
        plan.frame,
        ir.semantic_digest,
    )
