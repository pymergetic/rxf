"""Typed generic/template objects and deterministic specialization identities."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    GENERIC_ARGUMENT_TYPE,
    GENERIC_PARAMETER_TYPE,
    SPECIALIZATION_TYPE,
    TEMPLATE_TYPE,
    TEMPLATES_MODULE_ID,
)

SPECIALIZATION_NAMESPACE = 0xF000_0000_0000_0000
SPECIALIZATION_MASK = 0x0FFF_FFFF_FFFF_FFFF


class GenericParameterKind(IntEnum):
    TYPE = 1


class GenericParameterFlags(IntFlag):
    NONE = 0


class TemplateFlags(IntFlag):
    NONE = 0


class SpecializationFlags(IntFlag):
    NONE = 0


class GenericRole(IntEnum):
    PARAMETER = 220
    ARGUMENT = 221
    BOUND_TYPE = 222
    TEMPLATE = 223
    SPECIALIZED_FUNCTION = 224


def _ref(
    source: int, target: int, role: GenericRole, kind: RefKind = RefKind.DATA
) -> RefDef:
    return RefDef(source=source, target=target, kind=kind, to_off=int(role))


@dataclass(frozen=True)
class GenericParameter:
    id: int
    name: str
    parent: int
    index: int
    kind: GenericParameterKind = GenericParameterKind.TYPE
    flags: GenericParameterFlags = GenericParameterFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack("<4I", self.index, int(self.kind), int(self.flags), 0)

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=GENERIC_PARAMETER_TYPE,
            data=self.to_payload(),
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> GenericParameter:
        if len(node.data) != 16:
            raise ValueError(
                f"GenericParameter object {node.id} payload must be 16 bytes"
            )
        index, kind, flags, reserved = struct.unpack("<4I", node.data)
        if reserved:
            raise ValueError(
                f"GenericParameter object {node.id} reserved word is nonzero"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            index,
            GenericParameterKind(kind),
            GenericParameterFlags(flags),
        )


@dataclass(frozen=True)
class GenericArgument:
    id: int
    name: str
    parent: int
    parameter_id: int
    bound_type: int

    def to_payload(self) -> bytes:
        return struct.pack(
            "<2Q2I",
            self.parameter_id,
            self.bound_type,
            int(GenericParameterKind.TYPE),
            0,
        )

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=GENERIC_ARGUMENT_TYPE,
            data=self.to_payload(),
            refs=[
                _ref(self.id, self.parameter_id, GenericRole.PARAMETER),
                _ref(self.id, self.bound_type, GenericRole.BOUND_TYPE, RefKind.TYPE),
            ],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> GenericArgument:
        if len(node.data) != 24:
            raise ValueError(
                f"GenericArgument object {node.id} payload must be 24 bytes"
            )
        parameter, bound_type, kind, reserved = struct.unpack("<2Q2I", node.data)
        if kind != int(GenericParameterKind.TYPE) or reserved:
            raise ValueError(
                f"GenericArgument object {node.id} has invalid TYPE binding"
            )
        return cls(node.id, node.name, node.parent, parameter, bound_type)


@dataclass(frozen=True)
class Template:
    id: int
    name: str
    parent: int
    function_id: int
    parameter_ids: tuple[int, ...]
    semantic_version: int = 1
    flags: TemplateFlags = TemplateFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack(
            "<Q4I",
            self.function_id,
            self.semantic_version,
            len(self.parameter_ids),
            int(self.flags),
            0,
        )

    def to_node(self) -> NodeDef:
        refs = [_ref(self.id, self.function_id, GenericRole.SPECIALIZED_FUNCTION)]
        refs.extend(
            _ref(self.id, value, GenericRole.PARAMETER) for value in self.parameter_ids
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=TEMPLATE_TYPE,
            data=self.to_payload(),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> Template:
        if len(node.data) != 24:
            raise ValueError(f"Template object {node.id} payload must be 24 bytes")
        function, version, count, flags, reserved = struct.unpack("<Q4I", node.data)
        if reserved:
            raise ValueError(f"Template object {node.id} reserved word is nonzero")
        parameters = tuple(
            ref.target for ref in node.refs if ref.to_off == int(GenericRole.PARAMETER)
        )
        if len(parameters) != count:
            raise ValueError(
                f"Template object {node.id} parameter count disagrees with refs"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            function,
            parameters,
            version,
            TemplateFlags(flags),
        )


def specialization_digest(
    template_id: int,
    template_version: int,
    bindings: Iterable[tuple[int, int]],
    parameter_ids: Iterable[int] | None = None,
) -> bytes:
    supplied = tuple(bindings)
    if parameter_ids is None:
        ordered = tuple(sorted(supplied))
    else:
        order = tuple(parameter_ids)
        mapped = dict(supplied)
        if len(mapped) != len(supplied) or set(mapped) != set(order):
            raise ValueError(
                "bindings must exactly cover canonical template parameters"
            )
        ordered = tuple((parameter, mapped[parameter]) for parameter in order)
    payload = struct.pack("<QII", template_id, template_version, len(ordered))
    payload += b"".join(
        struct.pack("<2Q", parameter, bound_type) for parameter, bound_type in ordered
    )
    return hashlib.sha256(payload).digest()


def specialization_id(digest: bytes) -> int:
    if len(digest) != 32:
        raise ValueError("specialization digest must be SHA-256")
    return SPECIALIZATION_NAMESPACE | (
        int.from_bytes(digest[:8], "big") & SPECIALIZATION_MASK
    )


@dataclass(frozen=True)
class Specialization:
    id: int
    name: str
    parent: int
    template_id: int
    template_version: int
    function_id: int
    argument_ids: tuple[int, ...]
    digest: bytes
    flags: SpecializationFlags = SpecializationFlags.NONE

    def to_payload(self) -> bytes:
        if len(self.digest) != 32:
            raise ValueError("specialization digest must be SHA-256")
        return struct.pack(
            "<QIIQII32s",
            self.template_id,
            self.template_version,
            len(self.argument_ids),
            self.function_id,
            int(self.flags),
            0,
            self.digest,
        )

    def to_node(self) -> NodeDef:
        refs = [
            _ref(self.id, self.template_id, GenericRole.TEMPLATE),
            _ref(self.id, self.function_id, GenericRole.SPECIALIZED_FUNCTION),
        ]
        refs.extend(
            _ref(self.id, value, GenericRole.ARGUMENT) for value in self.argument_ids
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=SPECIALIZATION_TYPE,
            data=self.to_payload(),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> Specialization:
        if len(node.data) != 64:
            raise ValueError(
                f"Specialization object {node.id} payload must be 64 bytes"
            )
        template, version, count, function, flags, reserved, digest = struct.unpack(
            "<QIIQII32s", node.data
        )
        if reserved:
            raise ValueError(
                f"Specialization object {node.id} reserved word is nonzero"
            )
        arguments = tuple(
            ref.target for ref in node.refs if ref.to_off == int(GenericRole.ARGUMENT)
        )
        if len(arguments) != count:
            raise ValueError(
                f"Specialization object {node.id} argument count disagrees with refs"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            template,
            version,
            function,
            arguments,
            digest,
            SpecializationFlags(flags),
        )


def specialize_function(
    container,
    template_id: int,
    bindings: dict[int, int],
    *,
    specialization_node_id: int | None = None,
    function_id: int,
    signature_id: int,
    name: str,
):
    """Create concrete specialization metadata, refusing incomplete or colliding IDs."""
    from pymergetic.rxf.execution.decode import decode_function
    from pymergetic.rxf.model.execution import FunctionObject
    from pymergetic.rxf.ty.builtins import FUNCTION_TYPE, SIGNATURE_TYPE

    template_node = container.node_by_id(template_id)
    if template_node is None or template_node.type_id != TEMPLATE_TYPE:
        raise ValueError(f"template {template_id} is missing")
    template = Template.from_node(template_node)
    if set(bindings) != set(template.parameter_ids):
        raise ValueError("generic arguments must exactly cover template parameters")
    ordered = tuple(
        (parameter, bindings[parameter]) for parameter in template.parameter_ids
    )
    digest = specialization_digest(
        template.id, template.semantic_version, bindings.items(), template.parameter_ids
    )
    derived_id = specialization_id(digest)
    if specialization_node_id is not None and specialization_node_id != derived_id:
        raise ValueError("specialization ID does not match canonical digest")
    existing = container.node_by_id(derived_id)
    if existing is not None:
        if (
            existing.type_id != SPECIALIZATION_TYPE
            or Specialization.from_node(existing).digest != digest
        ):
            raise ValueError(f"specialization ID collision at {derived_id}")
        raise ValueError(f"duplicate specialization {derived_id}")
    source_node = container.node_by_id(template.function_id)
    signature_node = container.node_by_id(signature_id)
    if source_node is None or source_node.type_id != FUNCTION_TYPE:
        raise ValueError("template Function is missing")
    if signature_node is None or signature_node.type_id != SIGNATURE_TYPE:
        raise ValueError("concrete Signature is missing")
    source = decode_function(source_node)
    function = FunctionObject(
        function_id,
        name,
        template.parent,
        signature_id,
        source.implementation,
        source.layer,
        source.effects,
        source.semantic_version,
        source.intrinsic,
        source.body_id,
    ).to_node()
    argument_nodes = []
    argument_ids = []
    for index, (parameter, bound_type) in enumerate(ordered):
        argument_id = derived_id ^ (index + 1)
        if container.node_by_id(argument_id) is not None:
            raise ValueError(f"generic argument ID collision at {argument_id}")
        argument_ids.append(argument_id)
        argument_nodes.append(
            GenericArgument(
                argument_id, f"argument_{index}", derived_id, parameter, bound_type
            ).to_node()
        )
    specialization = Specialization(
        derived_id,
        name,
        TEMPLATES_MODULE_ID,
        template.id,
        template.semantic_version,
        function_id,
        tuple(argument_ids),
        digest,
    ).to_node()
    return specialization, function, tuple(argument_nodes)
