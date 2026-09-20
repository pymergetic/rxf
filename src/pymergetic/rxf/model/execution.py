"""Uniform callable objects and terminal target Code implementations."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    ARGUMENT_TYPE,
    CALL_TYPE,
    CODE_TYPE,
    FUNCTION_TYPE,
    IMPORT_TYPE,
    PARAMETER_TYPE,
    RELOCATION_TYPE,
    SIGNATURE_TYPE,
    TARGET_TYPE,
    VALUE_TYPE,
)


class FunctionImplementation(IntEnum):
    ABSTRACT = 0
    COMPOSED = 1
    CODE_BACKED = 2
    IMPORTED = 3
    DECLARED = 4
    INTRINSIC = 5


class FunctionLayer(IntEnum):
    FOUNDATION = 1
    BASIC = 2
    LIBRARY = 3
    APPLICATION = 4


class FunctionIntrinsic(IntEnum):
    NONE = 0
    IF = 1
    WHILE = 2
    SWITCH = 3
    SEQUENCE = 4
    RETURN = 5
    BREAK = 6
    CONTINUE = 7
    REFUSE = 8


class CodeFormat(IntEnum):
    NATIVE = 1


class Endianness(IntEnum):
    LITTLE = 1
    BIG = 2


class ValueKind(IntEnum):
    PARAMETER = 1
    LITERAL = 2
    RESULT = 3
    OBJECT = 4


class RelocationKind(IntEnum):
    ABSOLUTE = 1
    PC_RELATIVE = 2
    FUNCTION_IMPORT = 3


class Effect(IntFlag):
    NONE = 0
    READ_MEMORY = 1 << 0
    WRITE_MEMORY = 1 << 1
    ALLOCATE = 1 << 2
    IO = 1 << 3
    CLOCK = 1 << 4
    TERMINATE = 1 << 5


class CallRole(IntEnum):
    SIGNATURE = 200
    BODY = 201
    CALLEE = 202
    ARGUMENT = 203
    PARAMETER = 204
    VALUE = 205
    OWNER_FUNCTION = 206
    IMPLEMENTATION = 207
    TARGET = 208
    IMPORT = 209
    RELOCATION = 210
    SOURCE = 211
    SIGNATURE_CONTRACT = 212
    RESULT = 218
    REFUSAL_SET = 219
    NUMERIC_CONTRACT = 220
    ABI_SIGNATURE = 221
    PROVENANCE = 222
    CAPABILITY_REQUIREMENT = 223
    ARCHITECTURE = 213
    ABI = 214
    ENVIRONMENT = 215
    FEATURE_SET = 216
    FEATURE = 217


def semantic_digest(
    name: str, signature_id: int, effects: Effect, version: int
) -> bytes:
    source = f"{name}\0{signature_id}\0{int(effects)}\0{version}".encode()
    return hashlib.sha256(source).digest()


def _ref(
    source: int, target: int, role: CallRole, kind: RefKind = RefKind.DATA
) -> RefDef:
    return RefDef(source=source, target=target, kind=kind, to_off=int(role))


@dataclass(frozen=True)
class SignatureObject:
    id: int
    name: str
    parent: int
    return_type: int
    parameter_ids: tuple[int, ...] = ()
    result_ids: tuple[int, ...] = ()
    refusal_set_id: int = 0

    def to_node(self) -> NodeDef:
        payload = struct.pack(
            "<4Q",
            self.return_type,
            len(self.parameter_ids),
            len(self.result_ids),
            self.refusal_set_id,
        )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=SIGNATURE_TYPE,
            data=payload,
            refs=[
                *(
                    _ref(self.id, value, CallRole.PARAMETER)
                    for value in self.parameter_ids
                ),
                *(_ref(self.id, value, CallRole.RESULT) for value in self.result_ids),
                *(
                    [_ref(self.id, self.refusal_set_id, CallRole.REFUSAL_SET)]
                    if self.refusal_set_id
                    else []
                ),
            ],
        )


@dataclass(frozen=True)
class ParameterObject:
    id: int
    name: str
    parent: int
    value_type: int
    index: int
    lazy: bool = False

    def to_node(self) -> NodeDef:
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=PARAMETER_TYPE,
            data=struct.pack("<2Q2I", self.value_type, self.index, int(self.lazy), 0),
        )


@dataclass(frozen=True)
class FunctionObject:
    id: int
    name: str
    parent: int
    signature_id: int
    implementation: FunctionImplementation
    layer: FunctionLayer
    effects: Effect = Effect.NONE
    semantic_version: int = 1
    intrinsic: FunctionIntrinsic = FunctionIntrinsic.NONE
    body_id: int = 0
    implementation_ids: tuple[int, ...] = ()
    numeric_contract_id: int = 0
    semantic_digest_override: bytes | None = None

    def to_node(self) -> NodeDef:
        digest = self.semantic_digest_override or semantic_digest(
            self.name, self.signature_id, self.effects, self.semantic_version
        )
        payload = struct.pack(
            "<4I2QI4x32s",
            int(self.implementation),
            int(self.layer),
            int(self.effects),
            self.semantic_version,
            self.signature_id,
            self.body_id,
            int(self.intrinsic),
            digest,
        )
        refs = [_ref(self.id, self.signature_id, CallRole.SIGNATURE, RefKind.TYPE)]
        if self.body_id:
            refs.append(_ref(self.id, self.body_id, CallRole.BODY))
        refs.extend(
            _ref(self.id, value, CallRole.IMPLEMENTATION)
            for value in self.implementation_ids
        )
        if self.numeric_contract_id:
            refs.append(
                _ref(self.id, self.numeric_contract_id, CallRole.NUMERIC_CONTRACT)
            )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.FN,
            parent=self.parent,
            type_id=FUNCTION_TYPE,
            data=payload,
            refs=refs,
        )


@dataclass(frozen=True)
class CallObject:
    id: int
    name: str
    parent: int
    callee_id: int
    argument_ids: tuple[int, ...] = ()
    result_type: int = 0

    def to_node(self) -> NodeDef:
        refs = [_ref(self.id, self.callee_id, CallRole.CALLEE, RefKind.CALL)]
        refs.extend(
            _ref(self.id, value, CallRole.ARGUMENT) for value in self.argument_ids
        )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=CALL_TYPE,
            data=struct.pack(
                "<3Q", self.callee_id, len(self.argument_ids), self.result_type
            ),
            refs=refs,
        )


@dataclass(frozen=True)
class ArgumentObject:
    id: int
    name: str
    parent: int
    parameter_id: int
    value_id: int

    def to_node(self) -> NodeDef:
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=ARGUMENT_TYPE,
            data=struct.pack("<2Q", self.parameter_id, self.value_id),
            refs=[
                _ref(self.id, self.parameter_id, CallRole.PARAMETER),
                _ref(self.id, self.value_id, CallRole.VALUE),
            ],
        )


@dataclass(frozen=True)
class ValueObject:
    id: int
    name: str
    parent: int
    value_type: int
    kind: ValueKind
    value: bytes = b""

    def to_node(self) -> NodeDef:
        payload = (
            struct.pack("<QIIQ", self.value_type, int(self.kind), 0, len(self.value))
            + self.value
        )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=VALUE_TYPE,
            data=payload,
        )


@dataclass(frozen=True)
class TargetObject:
    id: int
    name: str
    parent: int
    architecture: int
    environment: int
    abi: int
    word_bits: int
    endianness: Endianness = Endianness.LITTLE
    features: int = 0

    def to_node(self) -> NodeDef:
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=TARGET_TYPE,
            data=struct.pack(
                "<6I",
                self.architecture,
                self.environment,
                self.abi,
                self.word_bits,
                int(self.endianness),
                self.features,
            ),
        )


@dataclass(frozen=True)
class CodeObject:
    id: int
    name: str
    parent: int
    owner_function: int
    target_id: int
    format: CodeFormat
    raw_bytes: bytes
    signature_id: int
    effects: Effect
    semantic_version: int
    semantic_digest: bytes
    entry_offset: int = 0
    flags: int = 0
    import_ids: tuple[int, ...] = ()
    relocation_ids: tuple[int, ...] = ()
    abi_signature_id: int = 0
    provenance_id: int = 0

    def to_node(self) -> NodeDef:
        payload = (
            struct.pack(
                "<3Q4I2Q32s",
                self.owner_function,
                self.target_id,
                self.signature_id,
                int(self.format),
                int(self.effects),
                self.semantic_version,
                self.flags,
                self.entry_offset,
                len(self.raw_bytes),
                self.semantic_digest,
            )
            + self.raw_bytes
        )
        refs = [
            _ref(self.id, self.owner_function, CallRole.OWNER_FUNCTION),
            _ref(self.id, self.target_id, CallRole.TARGET),
            _ref(self.id, self.signature_id, CallRole.SIGNATURE_CONTRACT, RefKind.TYPE),
        ]
        refs.extend(
            _ref(self.id, value, CallRole.IMPORT, RefKind.IMPORT)
            for value in self.import_ids
        )
        refs.extend(
            _ref(self.id, value, CallRole.RELOCATION) for value in self.relocation_ids
        )
        if self.abi_signature_id:
            refs.append(_ref(self.id, self.abi_signature_id, CallRole.ABI_SIGNATURE))
        if self.provenance_id:
            refs.append(_ref(self.id, self.provenance_id, CallRole.PROVENANCE))
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.CODE,
            parent=self.parent,
            type_id=CODE_TYPE,
            data=payload,
            refs=refs,
        )


@dataclass(frozen=True)
class ImportObject:
    id: int
    name: str
    parent: int
    function_id: int
    optional: bool = False
    requirement_id: int = 0

    def to_node(self) -> NodeDef:
        refs = [_ref(self.id, self.function_id, CallRole.CALLEE, RefKind.IMPORT)]
        if self.requirement_id:
            refs.append(
                _ref(
                    self.id,
                    self.requirement_id,
                    CallRole.CAPABILITY_REQUIREMENT,
                    RefKind.IMPORT,
                )
            )
        data = (
            struct.pack(
                "<QI4xQ", self.function_id, int(self.optional), self.requirement_id
            )
            if self.requirement_id
            else struct.pack("<QI4x", self.function_id, int(self.optional))
        )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.IMPORT,
            parent=self.parent,
            type_id=IMPORT_TYPE,
            data=data,
            refs=refs,
        )


@dataclass(frozen=True)
class RelocationObject:
    id: int
    name: str
    parent: int
    offset: int
    kind: RelocationKind
    target_id: int
    patch_width: int
    addend: int = 0

    def to_node(self) -> NodeDef:
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=RELOCATION_TYPE,
            data=struct.pack(
                "<QIIQq",
                self.offset,
                int(self.kind),
                self.patch_width,
                self.target_id,
                self.addend,
            ),
            refs=[_ref(self.id, self.target_id, CallRole.TARGET)],
        )
