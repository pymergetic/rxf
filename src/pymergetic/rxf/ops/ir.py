"""Derived CFG/SSA execution objects stored in the ordinary object graph."""

from __future__ import annotations

from dataclasses import dataclass, field

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import CFG_BLOCK_TYPE, CFG_OP_TYPE

SOURCE_ROLE = 104
TARGET_ROLE = 102
ARGUMENT_ROLE = 103


@dataclass
class ExecutionObject:
    id: int
    name: str
    source_id: int
    parent: int
    type_id: int
    role: str
    targets: list[int] = field(default_factory=list)
    arguments: list[int] = field(default_factory=list)

    def to_node(self) -> NodeDef:
        refs = [
            RefDef(
                source=self.id,
                target=self.source_id,
                kind=RefKind.DATA,
                to_off=SOURCE_ROLE,
            )
        ]
        refs.extend(
            RefDef(source=self.id, target=target, kind=RefKind.DATA, to_off=TARGET_ROLE)
            for target in self.targets
        )
        refs.extend(
            RefDef(
                source=self.id, target=value, kind=RefKind.DATA, to_off=ARGUMENT_ROLE
            )
            for value in self.arguments
        )
        return NodeDef(
            id=self.id,
            name=self.name,
            kind=NodeKind.DATA,
            parent=self.parent,
            type_id=self.type_id,
            refs=refs,
            attrs={"derived": True, "role": self.role, "source": self.source_id},
        )


@dataclass
class CFGBlockObject(ExecutionObject):
    type_id: int = field(default=CFG_BLOCK_TYPE, init=False)


@dataclass
class CFGOpObject(ExecutionObject):
    type_id: int = field(default=CFG_OP_TYPE, init=False)
