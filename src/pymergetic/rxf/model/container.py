"""Top-level RXF v5 graph plus one global heap policy."""

from dataclasses import dataclass, field

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.storage import HeapDef
from pymergetic.rxf.schema import FORMAT_VERSION, MAGIC


@dataclass
class Header:
    magic: bytes = MAGIC
    version: int = FORMAT_VERSION
    node_count: int = 0
    node_table_off: int = 0
    string_table_off: int = 0
    string_table_size: int = 0
    entry_node: int = 0
    flags: int = 0

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "node_count": self.node_count,
            "node_table_off": self.node_table_off,
            "string_table_off": self.string_table_off,
            "string_table_size": self.string_table_size,
            "entry_node": self.entry_node,
            "flags": self.flags,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Header":
        return cls(
            version=value.get("version", FORMAT_VERSION),
            node_count=value.get("node_count", 0),
            node_table_off=value.get("node_table_off", 0),
            string_table_off=value.get("string_table_off", 0),
            string_table_size=value.get("string_table_size", 0),
            entry_node=value.get("entry_node", 0),
            flags=value.get("flags", 0),
        )


@dataclass
class Container:
    header: Header = field(default_factory=Header)
    heap: HeapDef = field(default_factory=HeapDef)
    nodes: list[NodeDef] = field(default_factory=list)
    string_table: bytes = b""

    def node_by_id(self, node_id: int) -> NodeDef | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def to_dict(self) -> dict:
        result: dict = {"header": self.header.to_dict(), "heap": self.heap.to_dict()}
        if self.nodes:
            result["nodes"] = [n.to_dict() for n in self.nodes]
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "Container":
        forbidden = {"arenas", "domains", "regions"}.intersection(value)
        if forbidden:
            raise ValueError(
                f"obsolete top-level storage field(s): {', '.join(sorted(forbidden))}"
            )
        return cls(
            header=Header.from_dict(value.get("header", {})),
            heap=HeapDef.from_dict(value.get("heap", {})),
            nodes=[NodeDef.from_dict(n) for n in value.get("nodes", [])],
        )
