"""Whole-graph static template specialization.

The transform clones the template Function ownership graph, substitutes every
generic TYPE occurrence, and records canonical specialization provenance.
"""

from __future__ import annotations

import copy
import hashlib
import struct
from dataclasses import dataclass

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import semantic_digest
from pymergetic.rxf.model.generics import (
    GenericArgument,
    Specialization,
    Template,
    specialization_digest,
    specialization_id,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.traits import resolve_conformance
from pymergetic.rxf.ty.builtins import (
    ARGUMENT_TYPE,
    CALL_TYPE,
    CODE_TYPE,
    CONFORMANCE_TYPE,
    FUNCTION_TYPE,
    GENERIC_ARGUMENT_TYPE,
    IMPLEMENTATION_BINDING_TYPE,
    PARAMETER_TYPE,
    RESULT_TYPE,
    SIGNATURE_TYPE,
    SPECIALIZATION_TYPE,
    TEMPLATE_TYPE,
    VALUE_TYPE,
)

_DERIVED_NAMESPACE = 0xD000_0000_0000_0000
_DERIVED_MASK = 0x0FFF_FFFF_FFFF_FFFF


@dataclass(frozen=True)
class SpecializationResult:
    specialization_id: int
    function_id: int
    created_ids: tuple[int, ...]
    reused: bool


def _derived_id(digest: bytes, source_id: int, role: str) -> int:
    value = hashlib.sha256(
        digest + struct.pack("<Q", source_id) + role.encode()
    ).digest()
    return _DERIVED_NAMESPACE | (int.from_bytes(value[:8], "big") & _DERIVED_MASK)


def _owned_graph(container: Container, function_id: int) -> set[int]:
    owned = {function_id}
    changed = True
    while changed:
        changed = False
        for node in container.nodes:
            if node.id not in owned and node.parent in owned:
                owned.add(node.id)
                changed = True
    function = container.node_by_id(function_id)
    if function is None:
        return owned
    record = decode_function(function)
    seeds = {record.signature_id, record.body_id} - {0}
    pending = list(seeds)
    while pending:
        node_id = pending.pop()
        if node_id in owned:
            continue
        node = container.node_by_id(node_id)
        if node is None:
            continue
        owned.add(node_id)
        for ref in node.refs:
            target = container.node_by_id(ref.target)
            if target is not None and (
                target.parent in owned or target.parent == function_id
            ):
                pending.append(target.id)
    return owned


def _substitute_payload(
    node: NodeDef, remap: dict[int, int], bindings: dict[int, int]
) -> bytes:
    sub = lambda value: bindings.get(value, remap.get(value, value))
    data = node.data
    if node.type_id == SIGNATURE_TYPE:
        return struct.pack("<4Q", *(sub(value) for value in struct.unpack("<4Q", data)))
    if node.type_id == PARAMETER_TYPE:
        value_type, index, lazy, reserved = struct.unpack("<2Q2I", data)
        return struct.pack("<2Q2I", sub(value_type), index, lazy, reserved)
    if node.type_id == RESULT_TYPE:
        value_type, index, reserved = struct.unpack("<QII", data)
        return struct.pack("<QII", sub(value_type), index, reserved)
    if node.type_id == CALL_TYPE:
        callee, count, result = struct.unpack("<3Q", data)
        return struct.pack("<3Q", sub(callee), count, sub(result))
    if node.type_id == ARGUMENT_TYPE:
        parameter, value = struct.unpack("<2Q", data)
        return struct.pack("<2Q", sub(parameter), sub(value))
    if node.type_id == VALUE_TYPE:
        value_type, kind, reserved, size = struct.unpack("<QIIQ", data[:24])
        raw = data[24:]
        if size == 8:
            object_id = int.from_bytes(raw, "little")
            if object_id in remap:
                raw = remap[object_id].to_bytes(8, "little")
        return struct.pack("<QIIQ", sub(value_type), kind, reserved, size) + raw
    if node.type_id == FUNCTION_TYPE:
        impl, layer, effects, version, signature, body, intrinsic, _digest = (
            struct.unpack("<4I2QI4x32s", data)
        )
        signature = sub(signature)
        body = sub(body)
        assert signature is not None
        digest = semantic_digest(node.name, signature, effects, version)
        return struct.pack(
            "<4I2QI4x32s",
            impl,
            layer,
            effects,
            version,
            signature,
            body,
            intrinsic,
            digest,
        )
    if node.type_id == TEMPLATE_TYPE:
        function, version, count, flags, reserved = struct.unpack("<Q4I", data)
        return struct.pack("<Q4I", sub(function), version, count, flags, reserved)
    if node.type_id in (IMPLEMENTATION_BINDING_TYPE, GENERIC_ARGUMENT_TYPE):
        first, second, kind, reserved = struct.unpack("<2Q2I", data)
        return struct.pack("<2Q2I", sub(first), sub(second), kind, reserved)
    if node.type_id == CONFORMANCE_TYPE:
        trait, conforming, count, flags, r1, r2 = struct.unpack("<2Q4I", data)
        return struct.pack("<2Q4I", sub(trait), sub(conforming), count, flags, r1, r2)
    if node.type_id == CODE_TYPE:
        header = struct.calcsize("<3Q4I2Q32s")
        owner, target, signature, fmt, effects, version, flags, entry, size, digest = (
            struct.unpack("<3Q4I2Q32s", data[:header])
        )
        return (
            struct.pack(
                "<3Q4I2Q32s",
                sub(owner),
                sub(target),
                sub(signature),
                fmt,
                effects,
                version,
                flags,
                entry,
                size,
                digest,
            )
            + data[header:]
        )
    return data


def specialize_static(
    container: Container,
    template_id: int,
    bindings: dict[int, int],
    *,
    bounds: dict[int, tuple[int, ...]] | None = None,
) -> SpecializationResult:
    """Mutate ``container`` atomically with one canonical closed specialization."""
    template_node = container.node_by_id(template_id)
    if template_node is None or template_node.type_id != TEMPLATE_TYPE:
        raise ValueError(f"template {template_id} is missing")
    template = Template.from_node(template_node)
    if set(bindings) != set(template.parameter_ids):
        raise ValueError("generic arguments must exactly cover template parameters")
    for parameter, bound_type in bindings.items():
        type_node = container.node_by_id(bound_type)
        if type_node is None or type_node.kind.name != "TYPE":
            raise ValueError(
                f"generic parameter {parameter} bound TYPE {bound_type} is missing"
            )
        for trait_id in (bounds or {}).get(parameter, ()):
            try:
                resolve_conformance(container, trait_id, bound_type)
            except ValueError as error:
                raise ValueError(
                    f"unsatisfied bound {trait_id} for generic parameter {parameter}"
                ) from error
    digest = specialization_digest(
        template.id, template.semantic_version, bindings.items(), template.parameter_ids
    )
    provenance_id = specialization_id(digest)
    existing = container.node_by_id(provenance_id)
    if existing is not None:
        if (
            existing.type_id != SPECIALIZATION_TYPE
            or Specialization.from_node(existing).digest != digest
        ):
            raise ValueError(f"specialization ID collision at {provenance_id}")
        record = Specialization.from_node(existing)
        return SpecializationResult(provenance_id, record.function_id, (), True)
    source_ids = _owned_graph(container, template.function_id)
    remap = {
        source_id: _derived_id(digest, source_id, "object") for source_id in source_ids
    }
    if len(set(remap.values())) != len(remap):
        raise ValueError("derived specialization ID collision")
    for source_id, target_id in remap.items():
        collision = container.node_by_id(target_id)
        if collision is not None:
            raise ValueError(
                f"specialization object collision {source_id}->{target_id}"
            )
    argument_ids = tuple(
        _derived_id(digest, parameter, "argument")
        for parameter in template.parameter_ids
    )
    if len(set(argument_ids)) != len(argument_ids) or any(
        container.node_by_id(value) is not None for value in argument_ids
    ):
        raise ValueError("specialization argument ID collision")
    clones = []
    for source_id in sorted(source_ids):
        source = container.node_by_id(source_id)
        assert source is not None
        clone = copy.deepcopy(source)
        clone.id = remap[source_id]
        clone.parent = remap.get(source.parent, source.parent)
        clone.type_id = bindings.get(
            source.type_id, remap.get(source.type_id, source.type_id)
        )
        clone.data = _substitute_payload(source, remap, bindings)
        for ref in clone.refs:
            ref.source = clone.id
            ref.target = bindings.get(ref.target, remap.get(ref.target, ref.target))
        clone.attrs = {
            **clone.attrs,
            "specialization_provenance": str(provenance_id),
            "specialized_from": str(source_id),
        }
        clones.append(clone)
    open_parameters = set(template.parameter_ids)
    for clone in clones:
        if clone.type_id in open_parameters or any(
            ref.target in open_parameters for ref in clone.refs
        ):
            raise ValueError(
                f"open generic object remains after specialization: {clone.id}"
            )
    arguments = [
        GenericArgument(
            argument_id,
            f"argument_{index}",
            provenance_id,
            parameter,
            bindings[parameter],
        ).to_node()
        for index, (argument_id, parameter) in enumerate(
            zip(argument_ids, template.parameter_ids, strict=True)
        )
    ]
    provenance = Specialization(
        provenance_id,
        f"{template.name}_specialization",
        template.parent,
        template.id,
        template.semantic_version,
        remap[template.function_id],
        argument_ids,
        digest,
    ).to_node()
    additions = [*clones, *arguments, provenance]
    if len({node.id for node in additions}) != len(additions):
        raise ValueError("specialization additions contain duplicate IDs")
    container.nodes.extend(additions)
    return SpecializationResult(
        provenance_id,
        remap[template.function_id],
        tuple(node.id for node in additions),
        False,
    )
