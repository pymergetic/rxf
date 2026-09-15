"""Derived type-object lookup table and self-description checks."""

from __future__ import annotations

from dataclasses import dataclass, field

from pymergetic.rxf.model.container import Container
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.objects import FieldObject, TypeObject


@dataclass(frozen=True)
class TypeTableEntry:
    type_id: int
    cell_offset: int


@dataclass
class TypeTable:
    types: dict[int, TypeObject] = field(default_factory=dict)
    fields: dict[int, list[FieldObject]] = field(default_factory=dict)
    locations: dict[int, TypeTableEntry] = field(default_factory=dict)

    @classmethod
    def from_container(cls, container: Container) -> TypeTable:
        table = cls()
        offset = 0
        for node in container.nodes:
            if node.kind not in (NodeKind.TYPE, NodeKind.FIELD):
                continue
            table.locations[node.id] = TypeTableEntry(node.id, offset)
            offset += 48 + len(node.data)
            if node.kind == NodeKind.TYPE:
                table.types[node.id] = TypeObject.from_payload(
                    id=node.id, name=node.name, payload=node.data
                )
            else:
                field_object = FieldObject.from_payload(
                    id=node.id, name=node.name, payload=node.data
                )
                table.fields.setdefault(field_object.owner_type, []).append(
                    field_object
                )
        for fields in table.fields.values():
            fields.sort(key=lambda item: (item.offset, item.id))
        table.check()
        return table

    def check(self) -> None:
        for type_object in self.types.values():
            if type_object.align <= 0 or type_object.align & (type_object.align - 1):
                raise ValueError(f"TYPE object {type_object.id} has invalid alignment")
            fields = self.fields.get(type_object.id, [])
            if type_object.field_count != len(fields):
                raise ValueError(
                    f"TYPE object {type_object.id} declares {type_object.field_count} "
                    f"fields but has {len(fields)} FIELD children"
                )
            for field_object in fields:
                if field_object.value_type not in self.types:
                    raise ValueError(
                        f"FIELD object {field_object.id} has unknown value TYPE "
                        f"{field_object.value_type}"
                    )
                value_type = self.types[field_object.value_type]
                extent = field_object.offset + value_type.size * field_object.count
                if type_object.size and extent > type_object.size:
                    raise ValueError(
                        f"FIELD object {field_object.id} exceeds TYPE {type_object.id}"
                    )

    def get(self, type_id: int) -> TypeObject:
        try:
            return self.types[type_id]
        except KeyError as error:
            raise KeyError(f"TYPE object {type_id} was not found") from error

    def get_by_name(self, name: str) -> TypeObject:
        return next(item for item in self.types.values() if item.name == name)

    def entries(self) -> tuple[TypeTableEntry, ...]:
        return tuple(self.locations[key] for key in sorted(self.types))
