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
            if type_object.form.name == "REF" and (
                type_object.size,
                type_object.align,
            ) != (24, 8):
                raise ValueError(
                    f"REF TYPE object {type_object.id} must use canonical 24-byte handle layout"
                )
            fields = self.fields.get(type_object.id, [])
            if type_object.field_count != len(fields):
                raise ValueError(
                    f"TYPE object {type_object.id} declares {type_object.field_count} "
                    f"fields but has {len(fields)} FIELD children"
                )
            names: set[str] = set()
            previous_end = 0
            for index, field_object in enumerate(fields):
                if field_object.name in names:
                    raise ValueError(
                        f"TYPE object {type_object.id} has duplicate FIELD name {field_object.name}"
                    )
                names.add(field_object.name)
                if field_object.count == 0:
                    raise ValueError(f"FIELD object {field_object.id} has zero count")
                if field_object.value_type not in self.types:
                    raise ValueError(
                        f"FIELD object {field_object.id} has unknown value TYPE "
                        f"{field_object.value_type}"
                    )
                value_type = self.types[field_object.value_type]
                if value_type.flags & 1:
                    raise ValueError(
                        f"FIELD object {field_object.id} embeds variable-size TYPE inline"
                    )
                effective_align = min(value_type.align, type_object.align)
                if type_object.id >= 41003 and field_object.offset % effective_align:
                    raise ValueError(f"FIELD object {field_object.id} is misaligned")
                if (
                    value_type.size
                    and field_object.count > ((1 << 64) - 1) // value_type.size
                ):
                    raise ValueError(
                        f"FIELD object {field_object.id} extent overflows uint64"
                    )
                extent = field_object.offset + value_type.size * field_object.count
                if extent > (1 << 64) - 1:
                    raise ValueError(
                        f"FIELD object {field_object.id} extent overflows uint64"
                    )
                if index and field_object.offset < previous_end:
                    previous = fields[index - 1]
                    union_payload_overlap = (
                        type_object.form.name == "UNION"
                        and previous.name != "tag"
                        and field_object.name != "tag"
                        and previous.offset == field_object.offset
                        and bool(previous.flags & 2)
                        and bool(field_object.flags & 2)
                    )
                    if not union_payload_overlap:
                        raise ValueError(
                            f"FIELD object {field_object.id} overlaps previous FIELD"
                        )
                previous_end = max(previous_end, extent)
                if type_object.size and extent > type_object.size:
                    raise ValueError(
                        f"FIELD object {field_object.id} exceeds TYPE {type_object.id}"
                    )
            if (
                type_object.id >= 41003
                and type_object.form.name in {"STRUCT", "UNION"}
                and type_object.size % type_object.align
            ):
                raise ValueError(
                    f"TYPE object {type_object.id} extent is not alignment padded"
                )
            if type_object.id >= 41003 and type_object.form.name == "UNION" and fields:
                discriminants = [field for field in fields if field.name == "tag"]
                if len(discriminants) != 1 or discriminants[0].offset != 0:
                    raise ValueError(
                        f"UNION TYPE object {type_object.id} requires one tag FIELD at offset zero"
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
