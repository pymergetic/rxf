"""model/refs.py — cross-node references."""

from dataclasses import dataclass

from pymergetic.rxf.schema import RefBinding, RefKind


@dataclass
class RefDef:
    """A directed reference from one node to another.

    Stored in the source node's refs list.  The target is a node id.
    `to_off` is an optional byte-offset within the target's cell data.
    """

    source: int  # source node id (derived from which node holds this ref)
    target: int  # target node id
    kind: RefKind = RefKind.DATA
    binding: RefBinding = RefBinding.MANDATORY
    to_off: int = 0  # byte offset within target's data section

    # ── JSON round-trip ──

    def to_dict(self) -> dict:
        d: dict = {"target": self.target, "kind": self.kind.name}
        if self.binding != RefBinding.MANDATORY:
            d["binding"] = self.binding.name
        if self.to_off:
            d["to_off"] = self.to_off
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RefDef":
        return cls(
            source=d.get("source", 0),
            target=d["target"],
            kind=RefKind[d.get("kind", "DATA")],
            binding=RefBinding[d.get("binding", "MANDATORY")],
            to_off=d.get("to_off", 0),
        )
