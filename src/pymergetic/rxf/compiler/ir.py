"""Typed compiler IR for composed RXF Functions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, IntFlag


class ValueClass(IntEnum):
    INTEGER = 1
    FLOAT = 2
    POINTER = 3


class IREffect(IntFlag):
    NONE = 0
    READ_MEMORY = 1 << 0
    WRITE_MEMORY = 1 << 1
    ALLOCATE = 1 << 2
    IO = 1 << 3
    CLOCK = 1 << 4
    TERMINATE = 1 << 5


@dataclass(frozen=True, order=True)
class TypeRef:
    """Durable type identity and call-lowering shape."""

    id: int
    width: int
    value_class: ValueClass
    signed: bool = False


@dataclass(frozen=True, order=True)
class VirtualValue:
    id: int
    type: TypeRef
    source_id: int
    definition_block: int


@dataclass(frozen=True, order=True)
class CallOp:
    source_id: int
    function_id: int
    arguments: tuple[VirtualValue, ...]
    result: VirtualValue
    status: VirtualValue
    effects: IREffect
    composed: bool = False


@dataclass(frozen=True, order=True)
class BranchStatus:
    source_id: int
    status: VirtualValue
    success: int
    refusal: int


@dataclass(frozen=True)
class ReturnValue:
    source_id: int
    value: VirtualValue


@dataclass(frozen=True)
class ReturnStatus:
    source_id: int
    status: VirtualValue


Terminator = BranchStatus | ReturnValue | ReturnStatus


@dataclass(frozen=True)
class Block:
    id: int
    operations: tuple[CallOp, ...]
    terminator: Terminator


@dataclass(frozen=True)
class CompiledFunction:
    function_id: int
    signature_id: int
    parameters: tuple[VirtualValue, ...]
    result_type: TypeRef
    blocks: tuple[Block, ...]
    entry_block: int
    refusal_block: int
    source_ids: tuple[int, ...]
    semantic_digest: bytes
    binding_function_ids: tuple[int, ...] = ()
    binding_object_ids: tuple[int, ...] = ()
