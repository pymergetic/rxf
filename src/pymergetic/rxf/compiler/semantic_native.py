"""Opcode-driven native emission for lowered SemanticGraph programs."""

from __future__ import annotations

import struct

from pymergetic.rxf.compiler.native import (
    Architecture,
    Fixup,
    FixupKind,
    FixupNamespace,
    FrameSlot,
    LinkError,
    NativeImage,
    _validate_fixups,
)
from pymergetic.rxf.compiler.semantic_lower import SemanticProgram
from pymergetic.rxf.model.contracts import ABIPassingMode, ABIRegisterBank, ABIRole
from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode

_SIMPLE_CALL_OPCODES = {
    SemanticOpcode.READ,
    SemanticOpcode.UTF8_VALIDATE,
    SemanticOpcode.COMPARE,
}
_STATUS_ONLY_CALL_OPCODES = {
    SemanticOpcode.WRITE,
    SemanticOpcode.STORE_FIELD,
    SemanticOpcode.MOVE_SLOT,
    SemanticOpcode.APPEND_BYTES,
    SemanticOpcode.FORMAT_INTEGER,
    SemanticOpcode.PUBLISH,
    SemanticOpcode.ROLLBACK,
    SemanticOpcode.CLEANUP,
    SemanticOpcode.PIN,
    SemanticOpcode.UNPIN,
    SemanticOpcode.BORROW,
    SemanticOpcode.RELEASE_BORROW,
}
_IDENTITY_OPCODES = {SemanticOpcode.UTF8_BOUNDARY, SemanticOpcode.RETURN_VALUE}


def _simple_call_shape(ir: SemanticProgram):
    calls = tuple(op.source for op in ir.operations if op.call_abi is not None)
    operations = tuple(op.source for op in ir.operations)
    supported_calls = _SIMPLE_CALL_OPCODES | _STATUS_ONLY_CALL_OPCODES
    if len(calls) != 1 or calls[0].opcode not in supported_calls:
        return None
    if any(op.opcode not in supported_calls | _IDENTITY_OPCODES for op in operations):
        return None
    if len(ir.graph.blocks) != 3:
        return None
    non_status_results = tuple(
        value for value in calls[0].results if value != calls[0].status
    )
    if calls[0].opcode in _STATUS_ONLY_CALL_OPCODES and non_status_results:
        raise LinkError(
            f"Function {ir.function_id} SemanticOperation {calls[0].id} "
            f"status-only opcode {calls[0].opcode.name} defines payload values "
            f"{tuple(value.id for value in non_status_results)}"
        )
    return calls[0]


def emit_semantic_program(
    ir: SemanticProgram, architecture: Architecture
) -> NativeImage:
    operations = tuple(operation.source for operation in ir.operations)
    from pymergetic.rxf.compiler.semantic_cfg_native import emit_cfg, supports_cfg

    if supports_cfg(ir):
        return emit_cfg(ir, architecture)
    simple = _simple_call_shape(ir)
    if simple is not None:
        from pymergetic.rxf.compiler.semantic_scalar_native import emit_single_call

        return emit_single_call(ir, simple, architecture)
    opcodes = {operation.opcode for operation in operations}
    supported = {SemanticOpcode.ALLOCATE, SemanticOpcode.RETURN_VALUE}
    unsupported = sorted(opcode.name for opcode in opcodes - supported)
    if unsupported:
        first = next(
            operation
            for operation in operations
            if operation.opcode.name in unsupported
        )
        values = tuple(value.id for value in (*first.inputs, *first.results))
        raise LinkError(
            f"Function {ir.function_id} SemanticOperation {first.id} "
            f"unsupported native opcode {first.opcode.name}; values {values}"
        )
    allocations = tuple(
        operation
        for operation in operations
        if operation.opcode == SemanticOpcode.ALLOCATE
    )
    returns = tuple(
        operation
        for operation in operations
        if operation.opcode == SemanticOpcode.RETURN_VALUE
    )
    if len(allocations) != 1 or len(returns) != 1:
        raise LinkError(
            f"Function {ir.function_id} native shape requires one ALLOCATE and RETURN_VALUE"
        )
    allocate = allocations[0]
    call = ir.operation(allocate.id)
    if call.call_abi is None:
        raise LinkError(f"SemanticOperation {allocate.id} has no persisted call ABI")
    abi = (
        call.call_abi.x86_64
        if architecture == Architecture.X86_64
        else call.call_abi.aarch64
    )
    roles = tuple(location.role for location in abi)
    if roles != (
        ABIRole.HIDDEN_CONTEXT,
        *(ABIRole.SEMANTIC_ARGUMENT,) * len(allocate.inputs),
        ABIRole.TRANSIENT_OUTPUT,
        ABIRole.STATUS_RETURN,
    ):
        raise LinkError(f"SemanticOperation {allocate.id} call ABI role mismatch")
    if abi[-2].passing != ABIPassingMode.INDIRECT_BY_REFERENCE:
        raise LinkError(f"SemanticOperation {allocate.id} output is not by-reference")
    return (
        _x86(ir, allocate, returns[0], abi)
        if architecture == Architecture.X86_64
        else _arm(ir, allocate, returns[0], abi)
    )


def _x86(ir: SemanticProgram, allocate, return_op, abi) -> NativeImage:
    expected = ((0,), (1,), (2,), (3,), (4,), (5,))
    if (
        tuple(x.register_indices for x in abi[:6]) != expected
        or abi[-2].register_bank != ABIRegisterBank.STACK
    ):
        raise LinkError(f"SemanticOperation {allocate.id} SysV ABI locations disagree")
    objects = tuple(sorted(ir.binding_object_ids))
    functions = tuple(sorted(ir.binding_function_ids))
    transaction_value = allocate.inputs[-1]
    if transaction_value.kind.name != "OBJECT" or not transaction_value.source_id:
        raise LinkError(
            f"SemanticOperation {allocate.id} transaction value {transaction_value.id} "
            "is not a durable object binding; use generic CFG emission"
        )
    transaction = transaction_value.source_id
    if len(return_op.inputs) != 1:
        raise LinkError(f"SemanticOperation {return_op.id} return arity is invalid")
    payload = return_op.inputs[0].source_id
    code = bytearray(b"\x55\x48\x89\xe5\x53\x41\x54\x41\x55\x41\x56")
    code += b"\x48\x83\xec\x40\x49\x89\xfc\x49\x89\xf6\x49\x89\xd5"
    fixups: list[Fixup] = []
    failures: list[int] = []

    def resolve(
        table_count: int,
        table_ptr: int,
        index: int,
        durable: int,
        stride: int,
        kind: FixupKind,
        ns: FixupNamespace,
    ) -> None:
        code.extend(b"\x49\x83\x7c\x24" + bytes((table_count, index + 1)) + b"\x0f\x82")
        failures.append(len(code))
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5c\x24" + bytes((table_ptr,)))
        if index:
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * stride))
        code.extend(b"\x49\xb9" + struct.pack("<Q", durable))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x0b\x0f\x85")
        failures.append(len(code))
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5b\x10")
        fixups.append(Fixup(immediate, kind, ns, durable, 8, False))

    resolve(
        32,
        40,
        objects.index(transaction),
        transaction,
        72,
        FixupKind.OBJECT_SLOT,
        FixupNamespace.OBJECT,
    )
    code += b"\x4c\x89\xdb"  # preserve transaction pointer in saved rbx
    code += b"\x4c\x89\xe7\x4c\x89\xf6"  # context, semantic length
    literals = allocate.inputs[1:4]
    for value, mov in zip(
        literals, (b"\x48\xba", b"\x48\xb9", b"\x49\xb8"), strict=True
    ):
        code += mov + value.literal.ljust(8, b"\0")[:8]
    code += b"\x48\x8d\x44\x24\x20\x48\x89\x04\x24"  # stack out pointer
    callee = allocate.callee_id
    resolve(
        16,
        24,
        functions.index(callee),
        callee,
        24,
        FixupKind.FUNCTION_SLOT,
        FixupNamespace.FUNCTION,
    )
    code += b"\x49\x89\xd9\x41\xff\xd3\x85\xc0\x0f\x85"
    refusal_branch = len(code)
    code += bytes(4)
    resolve(
        32,
        40,
        objects.index(payload),
        payload,
        72,
        FixupKind.OBJECT_SLOT,
        FixupNamespace.OBJECT,
    )
    code += b"\x49\x8b\x03\x49\x89\x45\x00\x49\x8b\x43\x08\x49\x89\x45\x08\x49\x8b\x43\x10\x49\x89\x45\x10\x31\xc0\xe9"
    success = len(code)
    code += bytes(4)
    missing = len(code)
    code += b"\xb8\xfe\xff\xff\xff\xe9"
    missing_jump = len(code)
    code += bytes(4)
    refusal = len(code)
    epilogue = b"\x48\x83\xc4\x40\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
    code += epilogue
    struct.pack_into("<i", code, refusal_branch, refusal - refusal_branch - 4)
    struct.pack_into("<i", code, success, refusal - success - 4)
    struct.pack_into("<i", code, missing_jump, refusal - missing_jump - 4)
    for at in failures:
        struct.pack_into("<i", code, at, missing - at - 4)
        fixups.append(
            Fixup(at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, missing, 4, True)
        )
    fixups += [
        Fixup(
            refusal_branch,
            FixupKind.LOCAL_BRANCH,
            FixupNamespace.TEXT,
            refusal,
            4,
            True,
        ),
        Fixup(success, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, refusal, 4, True),
    ]
    _validate_fixups(code, fixups)
    frame = (FrameSlot(allocate.results[0].id, 32, 24, 8),)
    return NativeImage(
        Architecture.X86_64,
        ir.function_id,
        bytes(code),
        b"",
        functions,
        objects,
        tuple(fixups),
        frame,
        ir.semantic_digest,
    )


def _arm(ir: SemanticProgram, allocate, return_op, abi) -> NativeImage:
    if tuple(x.register_indices for x in abi[:7]) != tuple((i,) for i in range(7)):
        raise LinkError(
            f"SemanticOperation {allocate.id} AAPCS64 ABI locations disagree"
        )
    objects = tuple(sorted(ir.binding_object_ids))
    functions = tuple(sorted(ir.binding_function_ids))
    transaction_value = allocate.inputs[-1]
    if transaction_value.kind.name != "OBJECT" or not transaction_value.source_id:
        raise LinkError(
            f"SemanticOperation {allocate.id} transaction value {transaction_value.id} "
            "is not a durable object binding; use generic CFG emission"
        )
    transaction = transaction_value.source_id
    if len(return_op.inputs) != 1:
        raise LinkError(f"SemanticOperation {return_op.id} return arity is invalid")
    payload = return_op.inputs[0].source_id
    words = [
        0xD10143FF,
        0xA9007BFD,
        0xA90153F3,
        0xA9025BF5,
        0x910003FD,
        0xAA0003F4,
        0xAA0103F5,
        0xAA0203F3,
    ]
    fixups: list[Fixup] = []

    def resolve_object(durable: int, reg: int) -> None:
        index = objects.index(durable)
        words.append(0xF9401680)
        words.append(0x91000000 | ((index * 80) << 10) | (0 << 5) | reg)
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

    resolve_object(transaction, 9)
    words += [0xAA1403E0, 0xAA1503E1]
    for reg, value in zip((2, 3, 4), allocate.inputs[1:4], strict=True):
        words.append(
            0xD2800000 | ((int.from_bytes(value.literal, "little") & 0xFFFF) << 5) | reg
        )
    words.append(0xAA0903E5)
    words.append(0x910083E6)
    index = functions.index(allocate.callee_id)
    words.append(0xF9400E90)
    words.append(0x91000210 | ((index * 24) << 10))
    words.append(0xF9400A10)
    fixups.append(
        Fixup(
            (len(words) - 2) * 4,
            FixupKind.FUNCTION_SLOT,
            FixupNamespace.FUNCTION,
            allocate.callee_id,
            4,
            False,
            4,
            index * 24,
        )
    )
    words += [0xD63F0200, 0x35000000]
    refusal_at = len(words) - 1
    resolve_object(payload, 9)
    words += [0xA9402D2A, 0xF940092C, 0xA9002E6A, 0xF9000A6C, 0x52800000, 0x14000000]
    success_at = len(words) - 1
    refusal = len(words)
    words += [0xA9425BF5, 0xA94153F3, 0xA9407BFD, 0x910143FF, 0xD65F03C0]
    for at, base in ((refusal_at, 0x35000000), (success_at, 0x14000000)):
        delta = refusal - at
        words[at] = base | (
            ((delta & 0x7FFFF) << 5) if base == 0x35000000 else (delta & 0x3FFFFFF)
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
    text = b"".join(struct.pack("<I", w) for w in words)
    _validate_fixups(bytearray(text), fixups)
    return NativeImage(
        Architecture.AARCH64,
        ir.function_id,
        text,
        b"",
        functions,
        objects,
        tuple(fixups),
        (FrameSlot(allocate.results[0].id, 32, 24, 8),),
        ir.semantic_digest,
    )
