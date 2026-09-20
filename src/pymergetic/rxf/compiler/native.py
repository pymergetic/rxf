"""Deterministic native image models and scalar ABI emitters."""

from __future__ import annotations

import ctypes
import hashlib
import mmap
import struct
from dataclasses import dataclass, replace
from enum import IntEnum
from typing import TYPE_CHECKING

from pymergetic.rxf.compiler.ir import CompiledFunction, ReturnValue, ValueClass

if TYPE_CHECKING:
    from pymergetic.rxf.compiler.semantic_lower import SemanticProgram


class Architecture(IntEnum):
    X86_64 = 1
    AARCH64 = 2


class FixupKind(IntEnum):
    LOCAL_BRANCH = 1
    FUNCTION_SLOT = 2
    OBJECT_SLOT = 3
    CODE_SLOT = 4
    CAPABILITY_SLOT = 5


class FixupNamespace(IntEnum):
    TEXT = 1
    RODATA = 2
    FUNCTION = 3
    OBJECT = 4
    CODE = 5
    CAPABILITY = 6


@dataclass(frozen=True)
class Fixup:
    offset: int
    kind: FixupKind
    namespace: FixupNamespace
    target_id: int
    width: int
    signed: bool
    scale: int = 1
    addend: int = 0


@dataclass(frozen=True)
class FrameSlot:
    value_id: int
    offset: int
    width: int
    alignment: int


@dataclass(frozen=True)
class NativeImage:
    architecture: Architecture
    function_id: int
    text: bytes
    rodata: bytes
    function_ids: tuple[int, ...]
    object_ids: tuple[int, ...]
    fixups: tuple[Fixup, ...]
    frame: tuple[FrameSlot, ...]
    semantic_digest: bytes

    @property
    def digest(self) -> bytes:
        return hashlib.sha256(self.text + self.rodata + self.semantic_digest).digest()


class LinkError(ValueError):
    pass


def _frame(ir: CompiledFunction) -> tuple[FrameSlot, ...]:
    values = [op.result for b in ir.blocks for op in b.operations]
    result = []
    offset = 40
    for value in values:
        alignment = min(max(value.type.width, 1), 8)
        offset = (offset + alignment - 1) & -alignment
        result.append(FrameSlot(value.id, offset, max(value.type.width, 1), alignment))
        offset += max(value.type.width, 1)
    return tuple(result)


def emit(
    ir: CompiledFunction | SemanticProgram, architecture: Architecture
) -> NativeImage:
    from pymergetic.rxf.compiler.semantic_lower import SemanticProgram

    if isinstance(ir, SemanticProgram):
        from pymergetic.rxf.compiler.semantic_native import emit_semantic_program

        return emit_semantic_program(ir, architecture)
    return emit_x86_64(ir) if architecture == Architecture.X86_64 else emit_aarch64(ir)


def emit_x86_64(ir: CompiledFunction) -> NativeImage:
    """Emit complete SysV scalar glue with binary-searched durable-ID slots."""
    frame = _frame(ir)
    frame_size = (
        (max((s.offset + s.width for s in frame), default=24) + 15) // 16
    ) * 16
    # Entry rsp is 8 mod 16. Five pushes (including rbp) align it; keep frame a multiple of 16.
    code = bytearray(b"\x55\x48\x89\xe5\x53\x41\x54\x41\x55\x41\x56")
    code += b"\x48\x81\xec" + struct.pack("<I", frame_size)
    # r12=context, r15=final output; r13/r14 preserve argument and call addresses.
    code += b"\x49\x89\xfc\x49\x89\xf5"
    # Validate the shared runtime ABI before any output can be touched.
    code += b"\x49\x83\x3c\x24\x02\x0f\x85"
    version_exit = len(code)
    code += bytes(4)
    code += b"\x49\x81\x7c\x24\x08\x90\x00\x00\x00\x0f\x83"
    size_ok = len(code)
    code += bytes(4)
    code += b"\xb8\xff\xff\xff\xff\xe9"
    generation_exit = len(code)
    code += bytes(4)
    generation_continue = len(code)
    struct.pack_into("<i", code, version_exit, generation_continue - (version_exit + 4))
    struct.pack_into("<i", code, size_ok, generation_continue - (size_ok + 4))
    fixups: list[Fixup] = []
    functions = tuple(
        sorted({op.function_id for block in ir.blocks for op in block.operations})
    )
    binding_function_ids = tuple(sorted(ir.binding_function_ids or functions))
    objects = tuple(
        sorted(
            {
                -arg.id - 1
                for block in ir.blocks
                for op in block.operations
                for arg in op.arguments
                if arg.id < 0
            }
        )
    )
    binding_object_ids = tuple(sorted(ir.binding_object_ids or objects))
    frame_by_value = {slot.value_id: slot for slot in frame}
    refusal_branches: list[int] = []

    def resolve(
        table_count_off: int,
        table_off: int,
        durable_id: int,
        namespace: FixupNamespace,
        kind: FixupKind,
    ) -> None:
        # Deterministic pre-resolved dense index into the context-global sorted table.
        table = (
            binding_function_ids
            if namespace == FixupNamespace.FUNCTION
            else binding_object_ids
        )
        index = table.index(durable_id)
        # cmp qword ptr [r12 + table_count_off], index + 1.  The former
        # imm8 encoding silently limited complete object tables to 255 rows.
        code.extend(
            b"\x49\x81\x7c\x24"
            + bytes((table_count_off,))
            + struct.pack("<I", index + 1)
            + b"\x0f\x82"
        )
        short_count = len(code)
        code.extend(bytes(4))
        code.extend(b"\x4d\x8b\x5c\x24" + bytes((table_off,)))
        if index:
            stride = 24 if namespace == FixupNamespace.FUNCTION else 80
            code.extend(b"\x49\x81\xc3" + struct.pack("<I", index * stride))
        code.extend(b"\x49\xb9" + struct.pack("<Q", durable_id))
        immediate = len(code) - 8
        code.extend(b"\x4d\x39\x0b\x0f\x85")
        mismatch = len(code)
        code.extend(bytes(4))
        code.extend(
            b"\x4d\x8b\x5b\x10\xe9"
            if namespace == FixupNamespace.FUNCTION
            else b"\x4d\x8b\x5b\x18\xe9"
        )
        done_jump = len(code)
        code.extend(bytes(4))
        missing_target = len(code)
        code.extend(b"\xb8\xfe\xff\xff\xff\xe9")
        missing_jump = len(code)
        code.extend(bytes(4))
        done = len(code)
        for at, target in (
            (short_count, missing_target),
            (mismatch, missing_target),
            (done_jump, done),
        ):
            struct.pack_into("<i", code, at, target - (at + 4))
        refusal_branches.append(missing_jump)
        fixups.append(Fixup(immediate, kind, namespace, durable_id, 8, False))

    for block in ir.blocks:
        for op in block.operations:
            integer_position = 0
            float_position = 0
            # Resolve arguments afresh at each call; object payload addresses are never cached across calls.
            for arg in op.arguments:
                source_slot = None
                if arg.id < 0:
                    resolve(
                        32,
                        40,
                        -arg.id - 1,
                        FixupNamespace.OBJECT,
                        FixupKind.OBJECT_SLOT,
                    )
                    source_mem = True
                else:
                    source_mem = False
                    source_slot = frame_by_value[arg.id]
                if arg.type.value_class == ValueClass.FLOAT:
                    if float_position >= 8 or arg.type.width not in (4, 8):
                        raise LinkError("unsupported SysV floating argument")
                    prefix = b"\xf3" if arg.type.width == 4 else b"\xf2"
                    reg_bits = float_position << 3
                    if source_mem:
                        code.extend(
                            prefix + b"\x41\x0f\x10" + bytes((0x03 | reg_bits,))
                        )
                    else:
                        assert source_slot is not None
                        code.extend(
                            prefix
                            + b"\x0f\x10"
                            + bytes((0x85 | reg_bits,))
                            + struct.pack("<i", -source_slot.offset)
                        )
                    float_position += 1
                else:
                    if integer_position >= 6 or arg.type.width not in (1, 2, 4, 8):
                        raise LinkError("unsupported SysV integer argument")
                    regs_mem = (
                        b"\x41\x8b\x3b",
                        b"\x41\x8b\x33",
                        b"\x41\x8b\x13",
                        b"\x41\x8b\x0b",
                        b"\x45\x8b\x03",
                        b"\x45\x8b\x0b",
                    )
                    regs_frame = (
                        b"\x8b\xbd",
                        b"\x8b\xb5",
                        b"\x8b\x95",
                        b"\x8b\x8d",
                        b"\x44\x8b\x85",
                        b"\x44\x8b\x8d",
                    )
                    if arg.type.width == 8:
                        if not source_mem:
                            assert source_slot is not None
                        regs_mem64 = (
                            b"\x49\x8b\x3b",
                            b"\x49\x8b\x33",
                            b"\x49\x8b\x13",
                            b"\x49\x8b\x0b",
                            b"\x4d\x8b\x03",
                            b"\x4d\x8b\x0b",
                        )
                        regs_frame64 = (
                            b"\x48\x8b\xbd",
                            b"\x48\x8b\xb5",
                            b"\x48\x8b\x95",
                            b"\x48\x8b\x8d",
                            b"\x4c\x8b\x85",
                            b"\x4c\x8b\x8d",
                        )
                        if source_mem:
                            code.extend(regs_mem64[integer_position])
                        else:
                            assert source_slot is not None
                            code.extend(
                                regs_frame64[integer_position]
                                + struct.pack("<i", -source_slot.offset)
                            )
                    elif source_mem:
                        code.extend(regs_mem[integer_position])
                    else:
                        assert source_slot is not None
                        code.extend(
                            regs_frame[integer_position]
                            + struct.pack("<i", -source_slot.offset)
                        )
                    integer_position += 1
            result_slot = frame_by_value[op.result.id]
            resolve(
                16, 24, op.function_id, FixupNamespace.FUNCTION, FixupKind.FUNCTION_SLOT
            )
            code.extend(b"\x4d\x89\xde")
            # Hidden context shifts integer arguments only for composed callees.
            if op.composed:
                if integer_position > 4:
                    raise LinkError(
                        "composed callee has no register for hidden context/output"
                    )
                # Shift SysV integer semantic registers right one: rdi,rsi,rdx,rcx -> rsi,rdx,rcx,r8.
                shifts = (
                    (b"\x89\xfe",),
                    (b"\x89\xf2\x89\xfe",),
                    (b"\x89\xd1\x89\xf2\x89\xfe",),
                    (b"\x41\x89\xc8\x89\xd1\x89\xf2\x89\xfe",),
                )
                if integer_position:
                    code.extend(shifts[integer_position - 1][0])
                code.extend(b"\x4c\x89\xe7")
                out_regs_composed = (
                    b"\x48\x8d\xb5",
                    b"\x48\x8d\x95",
                    b"\x48\x8d\x8d",
                    b"\x4c\x8d\x85",
                    b"\x4c\x8d\x8d",
                )
                code.extend(
                    out_regs_composed[integer_position]
                    + struct.pack("<i", -result_slot.offset)
                )
            else:
                out_regs = (
                    b"\x48\x8d\xbd",
                    b"\x48\x8d\xb5",
                    b"\x48\x8d\x95",
                    b"\x48\x8d\x8d",
                    b"\x4c\x8d\x85",
                    b"\x4c\x8d\x8d",
                )
                if integer_position >= len(out_regs):
                    raise LinkError("no SysV register for result pointer")
                code.extend(
                    out_regs[integer_position] + struct.pack("<i", -result_slot.offset)
                )
            code.extend(b"\x41\xff\xd6\x85\xc0\x0f\x85")
            branch = len(code)
            code.extend(bytes(4))
            refusal_branches.append(branch)
    terminal = next(
        block.terminator.value
        for block in ir.blocks
        if isinstance(block.terminator, ReturnValue)
    )
    slot = frame_by_value[terminal.id]
    output_base = b"\x4c\x89\xef"
    code.extend(output_base)
    if terminal.type.value_class == ValueClass.FLOAT:
        prefix = b"\xf3" if terminal.type.width == 4 else b"\xf2"
        code.extend(prefix + b"\x0f\x10\x85" + struct.pack("<i", -slot.offset))
        code.extend(prefix + b"\x0f\x11\x07")
    else:
        code.extend(b"\x8b\x85" + struct.pack("<i", -slot.offset) + b"\x89\x07")
    code.extend(b"\x31\xc0\xe9")
    success_jump = len(code)
    code.extend(bytes(4))
    refusal = len(code)
    epilogue = refusal
    code.extend(
        b"\x48\x81\xc4"
        + struct.pack("<I", frame_size)
        + b"\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
    )
    struct.pack_into("<i", code, success_jump, epilogue - (success_jump + 4))
    struct.pack_into("<i", code, generation_exit, epilogue - (generation_exit + 4))
    for at in refusal_branches:
        struct.pack_into("<i", code, at, refusal - (at + 4))
        fixups.append(
            Fixup(at, FixupKind.LOCAL_BRANCH, FixupNamespace.TEXT, refusal, 4, True)
        )
    _validate_fixups(code, fixups)
    digest = hashlib.sha256(ir.semantic_digest + bytes((Architecture.X86_64,))).digest()
    return NativeImage(
        Architecture.X86_64,
        ir.function_id,
        bytes(code),
        b"",
        functions,
        objects,
        tuple(fixups),
        frame,
        digest,
    )


def emit_aarch64(ir: CompiledFunction) -> NativeImage:
    """Emit complete AAPCS64 scalar glue using context-global dense slots."""
    frame = tuple(replace(slot, offset=slot.offset + 40) for slot in _frame(ir))
    functions = tuple(
        sorted({op.function_id for block in ir.blocks for op in block.operations})
    )
    objects = tuple(
        sorted(
            {
                -a.id - 1
                for block in ir.blocks
                for op in block.operations
                for a in op.arguments
                if a.id < 0
            }
        )
    )
    binding_functions = tuple(sorted(ir.binding_function_ids or functions))
    binding_objects = tuple(sorted(ir.binding_object_ids or objects))
    frame_size = (
        (max((x.offset + x.width for x in frame), default=40) + 15) // 16
    ) * 16
    words = [
        0xD10003FF | ((frame_size & 0xFFF) << 10),
        0xA9007BFD,
        0xA90153F3,
        0xA9025BF5,
        0xA90363F7,
        0xA9046BF9,
        0x910003FD,
        0xAA0003F4,
        0xAA0103F3,
    ]
    fixups = []
    refusal_branches = []
    frame_by = {x.value_id: x for x in frame}

    def ldr_x(rt, rn, off):
        words.append(0xF9400000 | ((off // 8) << 10) | (rn << 5) | rt)

    def ldr_w(rt, rn, off=0):
        words.append(0xB9400000 | ((off // 4) << 10) | (rn << 5) | rt)

    def str_w(rt, rn, off=0):
        words.append(0xB9000000 | ((off // 4) << 10) | (rn << 5) | rt)

    def add_imm(rd, rn, imm):
        words.append(0x91000000 | (imm << 10) | (rn << 5) | rd)

    def sub_imm(rd, rn, imm):
        words.append(0xD1000000 | (imm << 10) | (rn << 5) | rd)

    def add_offset(rd, rn, offset):
        if offset < 0 or offset >= 1 << 64:
            raise LinkError("AArch64 table offset is outside uint64")
        if offset <= 0xFFF:
            add_imm(rd, rn, offset)
            return
        # Materialize full table offsets; an ADD immediate has only 12 bits.
        words.append(0xD2800000 | ((offset & 0xFFFF) << 5) | 18)
        for halfword in range(1, 4):
            value = (offset >> (halfword * 16)) & 0xFFFF
            if value:
                words.append(0xF2800000 | (halfword << 21) | (value << 5) | 18)
        words.append(0x8B000000 | (18 << 16) | (rn << 5) | rd)

    def resolve(function_id):
        index = binding_functions.index(function_id)
        ldr_x(16, 20, 24)
        add_offset(16, 16, index * 24)
        ldr_x(16, 16, 16)
        fixups.append(
            Fixup(
                (len(words) - 2) * 4,
                FixupKind.FUNCTION_SLOT,
                FixupNamespace.FUNCTION,
                function_id,
                4,
                False,
                4,
                index * 24,
            )
        )

    for block in ir.blocks:
        for op in block.operations:
            resolve(op.function_id)
            words.append(0xAA1003F6)  # mov x22, x16; x19-x21 remain caller state
            intpos = 0
            floatpos = 0
            for arg in op.arguments:
                if arg.id < 0:
                    index = binding_objects.index(-arg.id - 1)
                    ldr_x(17, 20, 40)
                    add_offset(17, 17, index * 80)
                    ldr_x(17, 17, 24)
                    if arg.type.value_class == ValueClass.FLOAT:
                        words.append(
                            (0xBD400000 if arg.type.width == 4 else 0xFD400000)
                            | (17 << 5)
                            | floatpos
                        )
                        floatpos += 1
                    else:
                        ldr_w(intpos, 17)
                        intpos += 1
                else:
                    slot = frame_by[arg.id]
                    if arg.type.value_class == ValueClass.FLOAT:
                        opcode = 0xBD4003E0 if arg.type.width == 4 else 0xFD4003E0
                        words.append(
                            opcode | ((slot.offset // arg.type.width) << 10) | floatpos
                        )
                        floatpos += 1
                    else:
                        ldr_w(intpos, 31, slot.offset)
                        intpos += 1
            result = frame_by[op.result.id]
            outreg = intpos
            stack_offset = result.offset
            if op.composed:
                words.append(0xAA1403E0)
                add_imm(1, 31, stack_offset)
            else:
                add_imm(outreg, 31, stack_offset)
            words += [0xD63F02C0, 0x35000000]
            refusal_branches.append(len(words) - 1)
    terminal = next(
        block.terminator.value
        for block in ir.blocks
        if isinstance(block.terminator, ReturnValue)
    )
    slot = frame_by[terminal.id]
    if terminal.type.value_class == ValueClass.FLOAT:
        words.append(
            (0xBD4003E0 if terminal.type.width == 4 else 0xFD4003E0)
            | (((slot.offset) // terminal.type.width) << 10)
        )
        words.append(0xBD000260 if terminal.type.width == 4 else 0xFD000260)
    else:
        ldr_w(8, 31, slot.offset)
        str_w(8, 19)
    words.append(0x52800000)
    refusal = len(words)
    words += [
        0xA9446BF9,
        0xA94363F7,
        0xA9425BF5,
        0xA94153F3,
        0xA9407BFD,
        0x910003FF | ((frame_size & 0xFFF) << 10),
        0xD65F03C0,
    ]
    for index in refusal_branches:
        delta = refusal - index
        if not -(1 << 18) <= delta < (1 << 18):
            raise LinkError("AArch64 refusal branch out of range")
        words[index] = 0x35000000 | ((delta & 0x7FFFF) << 5)
        fixups.append(
            Fixup(
                index * 4,
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
    digest = hashlib.sha256(
        ir.semantic_digest + bytes((Architecture.AARCH64,))
    ).digest()
    return NativeImage(
        Architecture.AARCH64,
        ir.function_id,
        text,
        b"",
        functions,
        objects,
        tuple(fixups),
        frame,
        digest,
    )


def _validate_fixups(text: bytearray, fixups: list[Fixup]) -> None:
    for f in fixups:
        if (
            f.width not in (1, 2, 4, 8)
            or f.scale < 1
            or f.offset < 0
            or f.offset + f.width > len(text)
        ):
            raise LinkError(f"invalid fixup for target {f.target_id}")
        if f.scale > 1 and f.offset % f.scale:
            raise LinkError(f"misaligned scaled fixup for target {f.target_id}")


def executable(image: NativeImage):
    if image.architecture != Architecture.X86_64:
        raise LinkError("host execution requires x86-64 image")
    memory = mmap.mmap(-1, len(image.text), prot=mmap.PROT_READ | mmap.PROT_WRITE)
    memory.write(image.text)
    address = ctypes.addressof(ctypes.c_char.from_buffer(memory))
    libc = ctypes.CDLL(None)
    page = address & ~(mmap.PAGESIZE - 1)
    if libc.mprotect(
        ctypes.c_void_p(page),
        ctypes.c_size_t(
            ((address + len(image.text) - page + mmap.PAGESIZE - 1) // mmap.PAGESIZE)
            * mmap.PAGESIZE
        ),
        mmap.PROT_READ | mmap.PROT_EXEC,
    ):
        raise OSError(ctypes.get_errno(), "mprotect RX failed")
    return memory, address
