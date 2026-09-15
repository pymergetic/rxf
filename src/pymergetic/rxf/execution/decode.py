"""Decode fixed callable payloads from ordinary RXF objects."""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pymergetic.rxf.model.execution import (
    CodeFormat,
    Effect,
    Endianness,
    FunctionImplementation,
    FunctionIntrinsic,
    FunctionLayer,
    RelocationKind,
)
from pymergetic.rxf.model.node import NodeDef


@dataclass(frozen=True)
class SignatureRecord:
    return_type: int
    parameter_count: int
    result_count: int
    refusal_set_id: int


def decode_signature(node: NodeDef) -> SignatureRecord:
    if len(node.data) == 16:  # explicit v4 inspection translation
        return_type, count = struct.unpack("<2Q", node.data)
        return SignatureRecord(return_type, count, 1 if return_type else 0, 0)
    if len(node.data) != 32:
        raise ValueError(f"Signature object {node.id} payload must be 32 bytes for v5")
    return SignatureRecord(*struct.unpack("<4Q", node.data))


@dataclass(frozen=True)
class FunctionRecord:
    implementation: FunctionImplementation
    layer: FunctionLayer
    effects: Effect
    semantic_version: int
    signature_id: int
    body_id: int
    intrinsic: FunctionIntrinsic
    semantic_digest: bytes


@dataclass(frozen=True)
class TargetRecord:
    architecture: int
    environment: int
    abi: int
    word_bits: int
    endianness: Endianness
    features: int


@dataclass(frozen=True)
class CodeRecord:
    owner_function: int
    target_id: int
    format: CodeFormat
    signature_id: int
    effects: Effect
    semantic_version: int
    flags: int
    entry_offset: int
    byte_count: int
    semantic_digest: bytes
    raw_bytes: bytes


def decode_function(node: NodeDef) -> FunctionRecord:
    if len(node.data) != 72:
        raise ValueError(f"Function object {node.id} payload must be 72 bytes")
    impl, layer, effects, version, signature, body, intrinsic, digest = struct.unpack(
        "<4I2QI4x32s", node.data
    )
    return FunctionRecord(
        FunctionImplementation(impl),
        FunctionLayer(layer),
        Effect(effects),
        version,
        signature,
        body,
        FunctionIntrinsic(intrinsic),
        digest,
    )


def decode_target(node: NodeDef) -> TargetRecord:
    if len(node.data) != 24:
        raise ValueError(f"Target object {node.id} payload must be 24 bytes")
    arch, environment, abi, word_bits, endianness, features = struct.unpack(
        "<6I", node.data
    )
    return TargetRecord(
        arch, environment, abi, word_bits, Endianness(endianness), features
    )


def decode_code(node: NodeDef) -> CodeRecord:
    header_size = struct.calcsize("<3Q4I2Q32s")
    if len(node.data) < header_size:
        raise ValueError(f"Code object {node.id} payload is truncated")
    (
        owner,
        target,
        signature,
        _format_value,
        effects,
        version,
        flags,
        entry,
        byte_count,
        digest,
    ) = struct.unpack("<3Q4I2Q32s", node.data[:header_size])
    raw_bytes = node.data[header_size:]
    if flags or byte_count != len(raw_bytes) or entry >= len(raw_bytes):
        raise ValueError(f"Code object {node.id} byte extent or flags are invalid")
    return CodeRecord(
        owner,
        target,
        CodeFormat(_format_value),
        signature,
        Effect(effects),
        version,
        flags,
        entry,
        byte_count,
        digest,
        raw_bytes,
    )


@dataclass(frozen=True)
class ImportRecord:
    function_id: int
    optional: bool


@dataclass(frozen=True)
class RelocationRecord:
    offset: int
    kind: RelocationKind
    patch_width: int
    target_id: int
    addend: int


def decode_import(node: NodeDef) -> ImportRecord:
    if len(node.data) != 16:
        raise ValueError(f"Import object {node.id} payload must be 16 bytes")
    function_id, optional = struct.unpack("<QI4x", node.data)
    if optional not in (0, 1):
        raise ValueError(f"Import object {node.id} optional flag is invalid")
    return ImportRecord(function_id, bool(optional))


def decode_relocation(node: NodeDef) -> RelocationRecord:
    if len(node.data) != 32:
        raise ValueError(f"Relocation object {node.id} payload must be 32 bytes")
    offset, kind, width, target, addend = struct.unpack("<QIIQq", node.data)
    if width not in (1, 2, 4, 8):
        raise ValueError(f"Relocation object {node.id} patch_width is invalid")
    return RelocationRecord(offset, RelocationKind(kind), width, target, addend)
