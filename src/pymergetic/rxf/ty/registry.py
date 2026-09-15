"""Compatibility-facing type registry built from TYPE/FIELD objects."""

from __future__ import annotations

from pymergetic.rxf.model.container import Container
from pymergetic.rxf.ty.objects import TypeObject
from pymergetic.rxf.ty.table import TypeTable


class TypeReg:
    """Read-only semantic registry discovered from one RXF object graph."""

    def __init__(self, container: Container):
        self.table = TypeTable.from_container(container)

    def get(self, type_id: int) -> TypeObject:
        return self.table.get(type_id)

    def get_id(self, name: str) -> int:
        return self.table.get_by_name(name).id

    def get_by_name(self, name: str) -> TypeObject:
        return self.table.get_by_name(name)

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.table.types

    def __len__(self) -> int:
        return len(self.table.types)

    def items(self):
        return self.table.types.items()

    def size_of(self, type_id: int) -> int:
        return self.get(type_id).size

    def align_of(self, type_id: int) -> int:
        return self.get(type_id).align

    def dump(self) -> list[dict]:
        return [
            {
                "id": item.id,
                "name": item.name,
                "form": item.form.name,
                "size": item.size,
                "align": item.align,
                "fields": [field.id for field in self.table.fields.get(item.id, [])],
            }
            for item in sorted(self.table.types.values(), key=lambda value: value.id)
        ]
