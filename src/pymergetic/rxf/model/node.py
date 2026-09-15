"""Semantic RXF objects; every durable identity is a uint64 node ID."""

from dataclasses import dataclass, field

from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.state import ObjectState, OwnerKind
from pymergetic.rxf.model.storage import SectionDef
from pymergetic.rxf.schema import NODE_INVALID, NodeKind


@dataclass
class NodeDef:
    """One object in the global RXF graph and heap."""

    id: int
    name: str
    kind: NodeKind
    parent: int = NODE_INVALID
    sections: list[SectionDef] = field(default_factory=list)
    refs: list[RefDef] = field(default_factory=list)
    attrs: dict = field(default_factory=dict)
    type_id: int = 0
    generation: int = 0
    owner: int = NODE_INVALID
    owner_kind: OwnerKind = OwnerKind.SHARED
    state: ObjectState = field(default_factory=ObjectState)
    data: bytes = b""

    def to_dict(self) -> dict:
        result: dict = {"id": self.id, "name": self.name, "kind": self.kind.name}
        if self.parent != NODE_INVALID:
            result["parent"] = self.parent
        if self.type_id:
            result["type"] = self.type_id
        if self.generation:
            result["generation"] = self.generation
        if self.owner != NODE_INVALID:
            result["owner"] = self.owner
        if self.owner_kind != OwnerKind.SHARED:
            result["owner_kind"] = self.owner_kind.name
        default_state = ObjectState()
        if self.state != default_state:
            result["state"] = self.state.to_dict()
        if self.sections:
            result["sections"] = [section.to_dict() for section in self.sections]
        if self.refs:
            result["refs"] = [reference.to_dict() for reference in self.refs]
        if self.attrs:
            result["attrs"] = self.attrs
        if self.data:
            result["data"] = self.data.hex()
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "NodeDef":
        forbidden = {"domain", "storage_domain"}.intersection(value)
        if forbidden:
            raise ValueError(
                f"obsolete per-domain field(s): {', '.join(sorted(forbidden))}"
            )
        data = value.get("data", "")
        if not isinstance(data, str):
            raise TypeError("node data must be a hexadecimal string")
        try:
            payload = bytes.fromhex(data)
        except ValueError as error:
            raise ValueError("node data must be a hexadecimal string") from error
        return cls(
            id=value["id"],
            name=value["name"],
            kind=NodeKind[value["kind"]],
            parent=value.get("parent", NODE_INVALID),
            type_id=value.get("type", 0),
            generation=value.get("generation", 0),
            owner=value.get("owner", NODE_INVALID),
            owner_kind=OwnerKind[value.get("owner_kind", "SHARED")],
            state=ObjectState.from_dict(value.get("state", {})),
            sections=[SectionDef.from_dict(item) for item in value.get("sections", [])],
            refs=[RefDef.from_dict(item) for item in value.get("refs", [])],
            attrs=dict(value.get("attrs", {})),
            data=payload,
        )
