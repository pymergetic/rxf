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
    ABI_VALUE_LOCATION_TYPE,
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
    ABI_LOCATION = 269
    SEMANTIC_ASSOCIATION = 270
    INDIRECT_POINTEE = 271


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


class ABIRole(IntEnum):
    HIDDEN_CONTEXT = 1
    SEMANTIC_ARGUMENT = 2
    TRANSIENT_OUTPUT = 3
    STATUS_RETURN = 4
    SRET = 5


class ABIPassingMode(IntEnum):
    DIRECT_SCALAR = 1
    DIRECT_AGGREGATE_CHUNKS = 2
    INDIRECT_BY_REFERENCE = 3
    RESOLVE_OBJECT_HANDLE = 4
    TRANSACTION_POINTER = 5
    CALL_FRAME_SLOT = 6


class ABIRegisterBank(IntEnum):
    INTEGER = 1
    FLOAT = 2
    STACK = 3
    RETURN = 4


class ABIKind(IntEnum):
    SYSV_X86_64 = 1
    AAPCS64 = 2


def _ref(
    source: int, target: int, role: ContractRole, kind: RefKind = RefKind.DATA
) -> RefDef:
    return RefDef(source, target, kind, to_off=int(role))


@dataclass(frozen=True)
class ResultSlot:
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


# Compatibility spelling for RXF v5 producers. The wire TYPE remains RESULT_TYPE.
ResultObject = ResultSlot


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
class ABIValueLocation:
    id: int
    name: str
    parent: int
    association_id: int
    semantic_index: int
    role: ABIRole
    passing: ABIPassingMode
    value_class: ABIClass
    width: int
    alignment: int
    chunk_count: int
    register_bank: ABIRegisterBank
    register_indices: tuple[int, ...] = ()
    stack_offset: int = 0
    pointee_type: int = 0
    hidden: bool = False
    mutable: bool = False
    output_only: bool = False

    def to_node(self) -> NodeDef:
        flags = (
            int(self.hidden) | (int(self.mutable) << 1) | (int(self.output_only) << 2)
        )
        data = struct.pack(
            "<Q10I3Q",
            self.association_id,
            self.semantic_index,
            int(self.role),
            int(self.passing),
            int(self.value_class),
            self.width,
            self.alignment,
            self.chunk_count,
            int(self.register_bank),
            len(self.register_indices),
            flags,
            self.stack_offset,
            self.pointee_type,
            0,
        ) + b"".join(struct.pack("<I", v) for v in self.register_indices)
        refs = []
        if self.association_id:
            refs.append(
                _ref(self.id, self.association_id, ContractRole.SEMANTIC_ASSOCIATION)
            )
        if self.pointee_type:
            refs.append(
                _ref(
                    self.id,
                    self.pointee_type,
                    ContractRole.INDIRECT_POINTEE,
                    RefKind.TYPE,
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ABI_VALUE_LOCATION_TYPE,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> ABIValueLocation:
        if len(node.data) < 72 or (len(node.data) - 72) % 4:
            raise ValueError(f"ABIValueLocation {node.id} extent invalid")
        (
            association,
            index,
            role,
            passing,
            vclass,
            width,
            align,
            chunks,
            bank,
            count,
            flags,
            stack,
            pointee,
            reserved,
        ) = struct.unpack("<Q10I3Q", node.data[:72])
        registers = struct.unpack(f"<{count}I", node.data[72:]) if count else ()
        if reserved or len(registers) != count:
            raise ValueError(f"ABIValueLocation {node.id} malformed")
        return cls(
            node.id,
            node.name,
            node.parent,
            association,
            index,
            ABIRole(role),
            ABIPassingMode(passing),
            ABIClass(vclass),
            width,
            align,
            chunks,
            ABIRegisterBank(bank),
            tuple(registers),
            stack,
            pointee,
            bool(flags & 1),
            bool(flags & 2),
            bool(flags & 4),
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
    location_ids: tuple[int, ...] = ()

    def to_node(self) -> NodeDef:
        packed = bytes(int(v) for v in self.argument_classes)
        data = (
            struct.pack(
                "<Q4I",
                self.target_id,
                int(self.kind),
                len(packed),
                int(self.output_class),
                len(self.location_ids),
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
            refs=[
                _ref(self.id, self.target_id, ContractRole.ABI_SIGNATURE),
                *(
                    _ref(self.id, v, ContractRole.ABI_LOCATION)
                    for v in self.location_ids
                ),
            ],
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


def verify_abi_locations(container, abi_node: NodeDef) -> list[str]:
    """Strict execution validation for ordered ABIValueLocation children."""
    from pymergetic.rxf.execution.decode import decode_abi_signature
    from pymergetic.rxf.ty.builtins import ABI_VALUE_LOCATION_TYPE

    errors = []
    by = {n.id: n for n in container.nodes}
    abi = decode_abi_signature(abi_node)
    ids = tuple(
        r.target for r in abi_node.refs if r.to_off == int(ContractRole.ABI_LOCATION)
    )
    if len(ids) != abi.location_count or not ids:
        return [f"ABISignature {abi_node.id} has incomplete typed locations"]
    values = []
    for value_id in ids:
        node = by.get(value_id)
        if node is None or node.type_id != ABI_VALUE_LOCATION_TYPE:
            errors.append(f"ABISignature {abi_node.id} location {value_id} is missing")
            continue
        try:
            values.append(ABIValueLocation.from_node(node))
        except ValueError as error:
            errors.append(str(error))
    if values:
        contexts = [v for v in values if v.role == ABIRole.HIDDEN_CONTEXT]
        if len(contexts) > 1 or any(not v.hidden for v in contexts):
            errors.append(f"ABISignature {abi_node.id} has invalid hidden context")
        if values[-1].role != ABIRole.STATUS_RETURN or values[-1].width != 4:
            errors.append(f"ABISignature {abi_node.id} lacks uint32 status return")
        outputs = [
            v for v in values if v.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}
        ]
        if any(not v.output_only for v in outputs):
            errors.append(f"ABISignature {abi_node.id} output policy is incomplete")
        associations = [
            v.association_id for v in values if v.role == ABIRole.SEMANTIC_ARGUMENT
        ]
        if len(associations) != len(set(associations)):
            errors.append(f"ABISignature {abi_node.id} duplicates semantic association")
        for value in values:
            if value.alignment <= 0 or value.alignment & (value.alignment - 1):
                errors.append(f"ABIValueLocation {value.id} alignment invalid")
            if (
                value.passing == ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
                and value.chunk_count * 8 < value.width
            ):
                errors.append(f"ABIValueLocation {value.id} aggregate chunks too small")
            if value.register_bank == ABIRegisterBank.STACK:
                if value.register_indices:
                    errors.append(
                        f"ABIValueLocation {value.id} stack has register indices"
                    )
                if value.stack_offset % max(8, value.alignment):
                    errors.append(
                        f"ABIValueLocation {value.id} stack offset misaligned"
                    )
            elif value.register_bank == ABIRegisterBank.RETURN:
                if value.role != ABIRole.STATUS_RETURN or value.register_indices != (
                    0,
                ):
                    errors.append(f"ABIValueLocation {value.id} return bank invalid")
            elif not value.register_indices:
                errors.append(f"ABIValueLocation {value.id} register bank is empty")
        stack_values = sorted(
            (v for v in values if v.register_bank == ABIRegisterBank.STACK),
            key=lambda v: v.stack_offset,
        )
        stack_end = 0
        for value in stack_values:
            if value.stack_offset < stack_end:
                errors.append(f"ABIValueLocation {value.id} stack extent overlaps")
            stack_end = value.stack_offset + ((value.width + 7) // 8) * 8
    return errors
