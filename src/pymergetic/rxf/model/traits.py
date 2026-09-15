"""Typed trait, conformance, and static implementation binding objects."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import FunctionImplementation, FunctionIntrinsic
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    ASSOCIATED_TYPE_TYPE,
    CALL_TYPE,
    CODE_TYPE,
    CONFORMANCE_TYPE,
    FUNCTION_TYPE,
    IMPLEMENTATION_BINDING_TYPE,
    IMPORT_TYPE,
    TRAIT_REQUIREMENT_TYPE,
    TRAIT_TYPE,
)


class TraitFlags(IntFlag):
    NONE = 0


class TraitRequirementKind(IntEnum):
    FUNCTION = 1


class ConformanceFlags(IntFlag):
    NONE = 0


class ImplementationBindingKind(IntEnum):
    REQUIREMENT = 1
    ASSOCIATED_TYPE = 2


class TraitRole(IntEnum):
    REQUIREMENT = 230
    ASSOCIATED_TYPE = 231
    TRAIT = 232
    CONFORMING_TYPE = 233
    BINDING = 234
    DECLARATION = 235
    IMPLEMENTATION = 236
    BOUND_TYPE = 237
    SUPERTRAIT = 238


def _ref(
    source: int, target: int, role: TraitRole, kind: RefKind = RefKind.DATA
) -> RefDef:
    return RefDef(source=source, target=target, kind=kind, to_off=int(role))


@dataclass(frozen=True)
class Trait:
    id: int
    name: str
    parent: int
    requirement_ids: tuple[int, ...] = ()
    associated_type_ids: tuple[int, ...] = ()
    supertrait_ids: tuple[int, ...] = ()
    flags: TraitFlags = TraitFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack(
            "<4I",
            len(self.requirement_ids),
            len(self.associated_type_ids),
            len(self.supertrait_ids),
            int(self.flags),
        )

    def to_node(self) -> NodeDef:
        refs = [
            _ref(self.id, value, TraitRole.REQUIREMENT)
            for value in self.requirement_ids
        ]
        refs.extend(
            _ref(self.id, value, TraitRole.ASSOCIATED_TYPE)
            for value in self.associated_type_ids
        )
        refs.extend(
            _ref(self.id, value, TraitRole.SUPERTRAIT) for value in self.supertrait_ids
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=TRAIT_TYPE,
            data=self.to_payload(),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> Trait:
        if len(node.data) != 16:
            raise ValueError(f"Trait object {node.id} payload must be 16 bytes")
        requirements, associated, supertraits, flags = struct.unpack("<4I", node.data)
        requirement_ids = tuple(
            ref.target for ref in node.refs if ref.to_off == int(TraitRole.REQUIREMENT)
        )
        associated_ids = tuple(
            ref.target
            for ref in node.refs
            if ref.to_off == int(TraitRole.ASSOCIATED_TYPE)
        )
        supertrait_ids = tuple(
            ref.target for ref in node.refs if ref.to_off == int(TraitRole.SUPERTRAIT)
        )
        if (
            len(requirement_ids) != requirements
            or len(associated_ids) != associated
            or len(supertrait_ids) != supertraits
        ):
            raise ValueError(
                f"Trait object {node.id} payload counts disagree with refs"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            requirement_ids,
            associated_ids,
            supertrait_ids,
            TraitFlags(flags),
        )


@dataclass(frozen=True)
class TraitRequirement:
    id: int
    name: str
    parent: int
    function_id: int
    index: int
    kind: TraitRequirementKind = TraitRequirementKind.FUNCTION

    def to_payload(self) -> bytes:
        return struct.pack("<Q4I", self.function_id, self.index, int(self.kind), 0, 0)

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=TRAIT_REQUIREMENT_TYPE,
            data=self.to_payload(),
            refs=[_ref(self.id, self.function_id, TraitRole.DECLARATION)],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> TraitRequirement:
        if len(node.data) != 24:
            raise ValueError(
                f"TraitRequirement object {node.id} payload must be 24 bytes"
            )
        function, index, kind, reserved, reserved2 = struct.unpack("<Q4I", node.data)
        if reserved or reserved2:
            raise ValueError(
                f"TraitRequirement object {node.id} reserved word is nonzero"
            )
        return cls(
            node.id, node.name, node.parent, function, index, TraitRequirementKind(kind)
        )


@dataclass(frozen=True)
class AssociatedType:
    id: int
    name: str
    parent: int
    index: int
    flags: int = 0

    def to_payload(self) -> bytes:
        return struct.pack("<4I", self.index, self.flags, 0, 0)

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ASSOCIATED_TYPE_TYPE,
            data=self.to_payload(),
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> AssociatedType:
        if len(node.data) != 16:
            raise ValueError(
                f"AssociatedType object {node.id} payload must be 16 bytes"
            )
        index, flags, reserved, reserved2 = struct.unpack("<4I", node.data)
        if reserved or reserved2:
            raise ValueError(
                f"AssociatedType object {node.id} reserved word is nonzero"
            )
        return cls(node.id, node.name, node.parent, index, flags)


@dataclass(frozen=True)
class Conformance:
    id: int
    name: str
    parent: int
    trait_id: int
    conforming_type: int
    binding_ids: tuple[int, ...]
    flags: ConformanceFlags = ConformanceFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack(
            "<2Q4I",
            self.trait_id,
            self.conforming_type,
            len(self.binding_ids),
            int(self.flags),
            0,
            0,
        )

    def to_node(self) -> NodeDef:
        refs = [
            _ref(self.id, self.trait_id, TraitRole.TRAIT),
            _ref(
                self.id, self.conforming_type, TraitRole.CONFORMING_TYPE, RefKind.TYPE
            ),
        ]
        refs.extend(
            _ref(self.id, value, TraitRole.BINDING) for value in self.binding_ids
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=CONFORMANCE_TYPE,
            data=self.to_payload(),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> Conformance:
        if len(node.data) != 32:
            raise ValueError(f"Conformance object {node.id} payload must be 32 bytes")
        trait, conforming, count, flags, reserved, reserved2 = struct.unpack(
            "<2Q4I", node.data
        )
        if reserved or reserved2:
            raise ValueError(f"Conformance object {node.id} reserved word is nonzero")
        bindings = tuple(
            ref.target for ref in node.refs if ref.to_off == int(TraitRole.BINDING)
        )
        if len(bindings) != count:
            raise ValueError(
                f"Conformance object {node.id} binding count disagrees with refs"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            trait,
            conforming,
            bindings,
            ConformanceFlags(flags),
        )


@dataclass(frozen=True)
class ImplementationBinding:
    id: int
    name: str
    parent: int
    requirement_id: int
    implementation_id: int
    kind: ImplementationBindingKind = ImplementationBindingKind.REQUIREMENT

    def to_payload(self) -> bytes:
        return struct.pack(
            "<2Q2I", self.requirement_id, self.implementation_id, int(self.kind), 0
        )

    def to_node(self) -> NodeDef:
        target_role = (
            TraitRole.BOUND_TYPE
            if self.kind == ImplementationBindingKind.ASSOCIATED_TYPE
            else TraitRole.IMPLEMENTATION
        )
        target_kind = (
            RefKind.TYPE
            if self.kind == ImplementationBindingKind.ASSOCIATED_TYPE
            else RefKind.DATA
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=IMPLEMENTATION_BINDING_TYPE,
            data=self.to_payload(),
            refs=[
                _ref(self.id, self.requirement_id, TraitRole.REQUIREMENT),
                _ref(self.id, self.implementation_id, target_role, target_kind),
            ],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> ImplementationBinding:
        if len(node.data) != 24:
            raise ValueError(
                f"ImplementationBinding object {node.id} payload must be 24 bytes"
            )
        requirement, implementation, kind, reserved = struct.unpack("<2Q2I", node.data)
        if reserved:
            raise ValueError(
                f"ImplementationBinding object {node.id} reserved word is nonzero"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            requirement,
            implementation,
            ImplementationBindingKind(kind),
        )


def function_is_callable(container: Container, function: NodeDef) -> bool:
    record = decode_function(function)
    if record.implementation == FunctionImplementation.ABSTRACT:
        return False
    if record.implementation == FunctionImplementation.DECLARED:
        return False
    if record.implementation == FunctionImplementation.COMPOSED:
        body = container.node_by_id(record.body_id)
        return record.body_id != 0 and body is not None and body.type_id == CALL_TYPE
    if record.implementation == FunctionImplementation.CODE_BACKED:
        return any(
            node.parent == function.id and node.type_id == CODE_TYPE
            for node in container.nodes
        )
    if record.implementation == FunctionImplementation.IMPORTED:
        return any(
            node.type_id == IMPORT_TYPE
            and any(ref.target == function.id for ref in node.refs)
            for node in container.nodes
        )
    if record.implementation == FunctionImplementation.INTRINSIC:
        return record.intrinsic != FunctionIntrinsic.NONE
    return False


def resolve_conformance(container: Container, trait_id: int, type_id: int) -> NodeDef:
    matches = []
    for node in container.nodes:
        if node.type_id == CONFORMANCE_TYPE:
            record = Conformance.from_node(node)
            if record.trait_id == trait_id and record.conforming_type == type_id:
                matches.append(node)
    if len(matches) != 1:
        raise ValueError(
            f"expected one conformance for trait {trait_id} and type {type_id}, found {len(matches)}"
        )
    return matches[0]


def resolve_requirement(
    container: Container, conformance_id: int, requirement_id: int
) -> NodeDef:
    conformance_node = container.node_by_id(conformance_id)
    if conformance_node is None:
        raise ValueError(f"conformance {conformance_id} is missing")
    conformance = Conformance.from_node(conformance_node)
    matches = []
    for binding_id in conformance.binding_ids:
        node = container.node_by_id(binding_id)
        if node is not None and node.type_id == IMPLEMENTATION_BINDING_TYPE:
            binding = ImplementationBinding.from_node(node)
            if (
                binding.kind == ImplementationBindingKind.REQUIREMENT
                and binding.requirement_id == requirement_id
            ):
                target = container.node_by_id(binding.implementation_id)
                if target is not None and target.type_id == FUNCTION_TYPE:
                    matches.append(target)
    if len(matches) != 1:
        raise ValueError(
            f"expected one implementation for requirement {requirement_id}, found {len(matches)}"
        )
    if not function_is_callable(container, matches[0]):
        raise ValueError(
            f"requirement {requirement_id} resolves to a non-callable Function"
        )
    return matches[0]
