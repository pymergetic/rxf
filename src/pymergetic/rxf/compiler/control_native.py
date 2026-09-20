"""Exact native lowering for typed scalar CFG control."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from pymergetic.rxf.compiler.cfg import (
    BranchBool,
    BranchStatusCode,
    ConstOp,
    ControlFunction,
    Jump,
    ReturnControl,
    SwitchTag,
    verify_control,
)
from pymergetic.rxf.compiler.native import (
    Architecture,
    Fixup,
    FixupKind,
    FixupNamespace,
    FrameSlot,
    NativeImage,
    _validate_fixups,
)
from pymergetic.rxf.compiler.normalize import CompileError


@dataclass
class _X86:
    code: bytearray
    labels: dict[int, int]
    branches: list[tuple[int, int]]
    frame: dict[int, FrameSlot]


def emit_control(function: ControlFunction, architecture: Architecture) -> NativeImage:
    verify_control(function)
    return (
        _emit_x86(function)
        if architecture == Architecture.X86_64
        else _emit_arm(function)
    )


def _slots(function):
    values = []
    for block in function.blocks:
        values += [p.value for p in block.parameters]
        values += [op.result for op in block.operations if hasattr(op, "result")]
    result = []
    offset = 40
    for value in values:
        if any(x.value_id == value.id for x in result):
            continue
        alignment = min(max(value.type.width, 1), 8)
        offset = (offset + alignment - 1) & -alignment
        result.append(FrameSlot(value.id, offset, value.type.width, alignment))
        offset += value.type.width
    return tuple(result)


def _emit_x86(function):
    frame = _slots(function)
    by = {x.value_id: x for x in frame}
    size = ((max((x.offset + x.width for x in frame), default=40) + 15) // 16) * 16
    state = _X86(
        bytearray(
            b"\x55\x48\x89\xe5\x53\x41\x54\x41\x55\x41\x56"
            + b"\x48\x81\xec"
            + struct.pack("<I", size)
            + b"\x49\x89\xfc\x49\x89\xf5"
        ),
        {},
        [],
        by,
    )

    def load(value, reg=b"\x8b\x85"):
        if value.id not in by:
            raise CompileError(f"control value {value.id} has no frame slot")
        state.code.extend(reg + struct.pack("<i", -by[value.id].offset))

    def store(value):
        state.code.extend(b"\x89\x85" + struct.pack("<i", -by[value.id].offset))

    for block in function.blocks:
        state.labels[block.id] = len(state.code)
        for op in block.operations:
            if isinstance(op, ConstOp):
                state.code.extend(b"\xb8" + struct.pack("<I", op.bits & 0xFFFFFFFF))
                store(op.result)
        term = block.terminator
        if isinstance(term, Jump):
            state.code.extend(b"\xe9")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.target))
        elif isinstance(term, BranchBool):
            load(term.condition)
            state.code.extend(b"\x85\xc0\x0f\x85")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.if_true))
            state.code.extend(b"\xe9")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.if_false))
        elif isinstance(term, BranchStatusCode):
            load(term.status)
            state.code.extend(b"\x85\xc0\x0f\x84")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.success))
            state.code.extend(b"\xe9")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.refusal))
        elif isinstance(term, SwitchTag):
            for key, target in term.cases:
                load(term.tag)
                state.code.extend(b"\x3d" + struct.pack("<I", key) + b"\x0f\x84")
                at = len(state.code)
                state.code.extend(bytes(4))
                state.branches.append((at, target))
            if term.default is None:
                raise CompileError(
                    "native switch requires default after exhaustive validation"
                )
            state.code.extend(b"\xe9")
            at = len(state.code)
            state.code.extend(bytes(4))
            state.branches.append((at, term.default))
        elif isinstance(term, ReturnControl):
            if term.value is not None:
                state.code.extend(b"\x4c\x89\xef")
                load(term.value)
                state.code.extend(b"\x89\x07")
            load(term.status)
            state.code.extend(
                b"\x48\x81\xc4"
                + struct.pack("<I", size)
                + b"\x41\x5e\x41\x5d\x41\x5c\x5b\x5d\xc3"
            )
        else:
            raise CompileError(
                f"control terminator {type(term).__name__} lowering is unsupported"
            )
    fixups = []
    for at, target in state.branches:
        if target not in state.labels:
            raise CompileError(f"branch target {target} lacks label")
        displacement = state.labels[target] - (at + 4)
        struct.pack_into("<i", state.code, at, displacement)
        fixups.append(
            Fixup(
                at,
                FixupKind.LOCAL_BRANCH,
                FixupNamespace.TEXT,
                state.labels[target],
                4,
                True,
            )
        )
    _validate_fixups(state.code, fixups)
    digest = hashlib.sha256(bytes(state.code)).digest()
    return NativeImage(
        Architecture.X86_64,
        function.function_id,
        bytes(state.code),
        b"",
        (),
        (),
        tuple(fixups),
        frame,
        digest,
    )


def _emit_arm(function):
    frame = _slots(function)
    size = ((max((x.offset + x.width for x in frame), default=40) + 15) // 16) * 16
    by = {x.value_id: x for x in frame}
    words = [
        0xD10003FF | ((size & 0xFFF) << 10),
        0xA9007BFD,
        0xA90153F3,
        0x910003FD,
        0xAA0103F3,
    ]
    labels = {}
    branches = []

    def load(value, reg=8):
        words.append(0xB94003E0 | ((by[value.id].offset // 4) << 10) | reg)

    def store(value, reg=8):
        words.append(0xB90003E0 | ((by[value.id].offset // 4) << 10) | reg)

    for block in function.blocks:
        labels[block.id] = len(words)
        for op in block.operations:
            if isinstance(op, ConstOp):
                words.append(0x52800000 | ((op.bits & 0xFFFF) << 5) | 8)
                store(op.result)
        term = block.terminator
        if isinstance(term, Jump):
            branches.append((len(words), term.target, "b"))
            words.append(0x14000000)
        elif isinstance(term, BranchBool):
            load(term.condition)
            branches.append((len(words), term.if_true, "nz"))
            words.append(0x35000008)
            branches.append((len(words), term.if_false, "b"))
            words.append(0x14000000)
        elif isinstance(term, BranchStatusCode):
            load(term.status)
            branches.append((len(words), term.success, "z"))
            words.append(0x34000008)
            branches.append((len(words), term.refusal, "b"))
            words.append(0x14000000)
        elif isinstance(term, SwitchTag):
            for key, target in term.cases:
                if key > 4095:
                    raise CompileError("AArch64 switch key exceeds immediate compare")
                load(term.tag)
                words.append(0x7100011F | (key << 10))
                branches.append((len(words), target, "eq"))
                words.append(0x54000000)
            if term.default is None:
                raise CompileError("native switch requires default")
                branches.append((len(words), term.default, "b"))
                words.append(0x14000000)
        elif isinstance(term, ReturnControl):
            if term.value is not None:
                load(term.value, 8)
                words.append(0xB9000268)
            load(term.status, 0)
            words += [
                0xA94153F3,
                0xA9407BFD,
                0x910003FF | ((size & 0xFFF) << 10),
                0xD65F03C0,
            ]
        else:
            raise CompileError(
                f"control terminator {type(term).__name__} lowering is unsupported"
            )
    fixups = []
    for index, target, kind in branches:
        delta = labels[target] - index
        if kind == "b":
            words[index] = 0x14000000 | (delta & 0x3FFFFFF)
        elif kind == "nz":
            words[index] = 0x35000000 | ((delta & 0x7FFFF) << 5) | 8
        elif kind == "z":
            words[index] = 0x34000000 | ((delta & 0x7FFFF) << 5) | 8
        else:
            words[index] = 0x54000000 | ((delta & 0x7FFFF) << 5)
        fixups.append(
            Fixup(
                index * 4,
                FixupKind.LOCAL_BRANCH,
                FixupNamespace.TEXT,
                labels[target] * 4,
                4,
                True,
                4,
            )
        )
    text = b"".join(struct.pack("<I", x) for x in words)
    _validate_fixups(bytearray(text), fixups)
    return NativeImage(
        Architecture.AARCH64,
        function.function_id,
        text,
        b"",
        (),
        (),
        tuple(fixups),
        frame,
        hashlib.sha256(text).digest(),
    )
