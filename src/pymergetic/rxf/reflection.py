"""Typed reflection and deterministic tooling object-graph boundary."""

from __future__ import annotations

import json
from dataclasses import dataclass

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.ty.builtins import FUNCTION_TYPE
from pymergetic.rxf.ty.table import TypeTable


@dataclass(frozen=True)
class ReflectedField:
    id: int
    name: str
    owner_type: int
    value_type: int
    offset: int
    count: int


@dataclass(frozen=True)
class ReflectedType:
    id: int
    name: str
    form: str
    size: int
    align: int
    fields: tuple[ReflectedField, ...]


@dataclass(frozen=True)
class ReflectedFunction:
    id: int
    name: str
    signature_id: int
    effects: int
    semantic_version: int


@dataclass(frozen=True)
class MigrationStep:
    source_field: int
    destination_field: int


@dataclass(frozen=True)
class MigrationPlan:
    source_type: int
    destination_type: int
    steps: tuple[MigrationStep, ...]
    refusals: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.refusals


def reflect_types(container: Container) -> tuple[ReflectedType, ...]:
    table = TypeTable.from_container(container)
    result = []
    for type_id, value in sorted(table.types.items()):
        fields = tuple(
            ReflectedField(
                field.id,
                field.name,
                field.owner_type,
                field.value_type,
                field.offset,
                field.count,
            )
            for field in table.fields.get(type_id, ())
        )
        result.append(
            ReflectedType(
                type_id, value.name, value.form.name, value.size, value.align, fields
            )
        )
    return tuple(result)


def reflect_functions(container: Container) -> tuple[ReflectedFunction, ...]:
    result = []
    for node in sorted(container.nodes, key=lambda item: item.id):
        if node.type_id == FUNCTION_TYPE:
            value = decode_function(node)
            result.append(
                ReflectedFunction(
                    node.id,
                    node.name,
                    value.signature_id,
                    int(value.effects),
                    value.semantic_version,
                )
            )
    return tuple(result)


def migration_plan(
    container: Container, source_type: int, destination_type: int
) -> MigrationPlan:
    table = TypeTable.from_container(container)
    source = {field.name: field for field in table.fields.get(source_type, ())}
    destination = table.fields.get(destination_type, ())
    steps = []
    refusals = []
    for field in destination:
        prior = source.get(field.name)
        if prior is None:
            refusals.append(f"destination field {field.name} has no source")
        elif prior.value_type != field.value_type or prior.count != field.count:
            refusals.append(f"field {field.name} type/count changed")
        else:
            steps.append(MigrationStep(prior.id, field.id))
    return MigrationPlan(source_type, destination_type, tuple(steps), tuple(refusals))


def tooling_graph_json(container: Container) -> str:
    """Deterministic inspector boundary, deliberately not a runtime serializer."""
    return json.dumps(container.to_dict(), sort_keys=True, separators=(",", ":"))
