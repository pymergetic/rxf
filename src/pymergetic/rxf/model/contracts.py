"""RXF v5 portable semantic and target ABI contract objects."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    ABI_SIGNATURE_TYPE,
    COMPILER_PROVENANCE_TYPE,
    NUMERIC_CONTRACT_TYPE,
    REFUSAL_SET_TYPE,
    REFUSAL_VARIANT_TYPE,
    RESULT_TYPE,
)


class ContractRole(IntEnum):
    RESULT = 260
    REFUSAL_SET = 261
    REFUSAL_VARIANT = 262
    NUMERIC_CONTRACT = 263
    ABI_SIGNATURE = 264
    VALUE_TYPE = 265
    SOURCE_TYPE = 266
    DESTINATION_TYPE = 267
    PROVENANCE = 268


class NumericOperation(IntEnum):
    EQUAL = 1
    LESS = 2
    MINIMUM = 3
    MAXIMUM = 4
    CHECKED_ADD = 5
    WRAPPING_ADD = 6
    SATURATING_ADD = 7
    CHECKED_SUBTRACT = 8
    WRAPPING_SUBTRACT = 9
    SATURATING_SUBTRACT = 10
    CHECKED_MULTIPLY = 11
    WRAPPING_MULTIPLY = 12
    SATURATING_MULTIPLY = 13
    DIVIDE = 14
    REMAINDER = 15
    BIT_NOT = 16
    BIT_AND = 17
    BIT_OR = 18
    BIT_XOR = 19
    SHIFT_LEFT = 20
    SHIFT_RIGHT = 21
    CHECKED_NEGATE = 22
    CHECKED_ABSOLUTE = 23
    CONVERT = 24


class NumericPolicy(IntFlag):
    NONE = 0
    CHECKED = 1
    WRAPPING = 2
    SATURATING = 4
    TRUNCATE_ZERO = 8
    REMAINDER_DIVIDEND_SIGN = 16
    REFUSE_SHIFT_WIDTH = 32
    ARITHMETIC_RIGHT = 64
    FINITE_ONLY = 128
    REFUSE_NAN = 256
    REFUSE_INFINITY = 512
    PRESERVE_SIGNED_ZERO = 1024
    EXACT = 2048
    RANGE_CHECKED = 4096
    ROUND_NEAREST_EVEN = 8192


class ABIClass(IntEnum):
    INTEGER = 1
    FLOAT = 2
    POINTER = 3


class ABIKind(IntEnum):
    SYSV_X86_64 = 1
    AAPCS64 = 2


def _ref(
    source: int, target: int, role: ContractRole, kind: RefKind = RefKind.DATA
) -> RefDef:
    return RefDef(source, target, kind, to_off=int(role))


@dataclass(frozen=True)
class ResultObject:
    id: int
    name: str
    parent: int
    value_type: int
    index: int

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=RESULT_TYPE,
            data=struct.pack("<QII", self.value_type, self.index, 0),
            refs=[
                _ref(self.id, self.value_type, ContractRole.VALUE_TYPE, RefKind.TYPE)
            ],
        )


@dataclass(frozen=True)
class RefusalVariant:
    id: int
    name: str
    parent: int
    code: int

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=REFUSAL_VARIANT_TYPE,
            data=struct.pack("<2I", self.code, 0),
        )


@dataclass(frozen=True)
class RefusalSet:
    id: int
    name: str
    parent: int
    variant_ids: tuple[int, ...]

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=REFUSAL_SET_TYPE,
            data=struct.pack("<2I", len(self.variant_ids), 0),
            refs=[
                _ref(self.id, v, ContractRole.REFUSAL_VARIANT) for v in self.variant_ids
            ],
        )


@dataclass(frozen=True)
class NumericContract:
    id: int
    name: str
    parent: int
    operation: NumericOperation
    source_type: int
    destination_type: int
    policy: NumericPolicy
    width: int
    signed: bool

    def to_node(self) -> NodeDef:
        data = struct.pack(
            "<4I2Q2I",
            int(self.operation),
            int(self.policy),
            self.width,
            int(self.signed),
            self.source_type,
            self.destination_type,
            0,
            0,
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=NUMERIC_CONTRACT_TYPE,
            data=data,
            refs=[
                _ref(self.id, self.source_type, ContractRole.SOURCE_TYPE, RefKind.TYPE),
                _ref(
                    self.id,
                    self.destination_type,
                    ContractRole.DESTINATION_TYPE,
                    RefKind.TYPE,
                ),
            ],
        )


@dataclass(frozen=True)
class ABISignature:
    id: int
    name: str
    parent: int
    target_id: int
    kind: ABIKind
    argument_classes: tuple[ABIClass, ...]
    output_class: ABIClass = ABIClass.POINTER

    def to_node(self) -> NodeDef:
        packed = bytes(int(v) for v in self.argument_classes)
        data = (
            struct.pack(
                "<Q4I",
                self.target_id,
                int(self.kind),
                len(packed),
                int(self.output_class),
                0,
            )
            + packed
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ABI_SIGNATURE_TYPE,
            data=data,
            refs=[_ref(self.id, self.target_id, ContractRole.ABI_SIGNATURE)],
        )


@dataclass(frozen=True)
class CompilerProvenance:
    id: int
    name: str
    parent: int
    compiler_major: int
    recipe_digest: bytes

    def to_node(self) -> NodeDef:
        if len(self.recipe_digest) != 32:
            raise ValueError("recipe digest must be 32 bytes")
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=COMPILER_PROVENANCE_TYPE,
            data=struct.pack("<2I", self.compiler_major, 0) + self.recipe_digest,
        )


def canonical_semantic_digest(
    signature: bytes,
    results: list[bytes],
    refusal: bytes,
    numeric: bytes,
    effects: int,
    version: int,
) -> bytes:
    h = hashlib.sha256()
    h.update(b"RXF5\0")
    h.update(struct.pack("<II", effects, version))
    for part in (signature, *results, refusal, numeric):
        h.update(struct.pack("<Q", len(part)))
        h.update(part)
    return h.digest()
