"""Typed Module objects and derived semantic names."""

from __future__ import annotations

import struct
from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum, IntFlag
from typing import Protocol

from pymergetic.rxf.schema import NODE_INVALID, NodeKind


class ModuleCategory(IntEnum):
    ORGANIZATIONAL = 0
    MODEL = 1
    EXECUTION = 2
    APPLICATION = 3


class ModuleFlags(IntFlag):
    NONE = 0
    PUBLIC = 1 << 0


@dataclass(frozen=True)
class ModuleObject:
    """Minimal fixed payload for an ordinary typed Module object."""

    id: int
    name: str
    parent: int
    category: ModuleCategory = ModuleCategory.ORGANIZATIONAL
    flags: ModuleFlags = ModuleFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack("<2I", int(self.category), int(self.flags))

    @classmethod
    def from_payload(
        cls, *, id: int, name: str, parent: int, payload: bytes
    ) -> ModuleObject:
        if len(payload) != 8:
            raise ValueError(f"Module object {id} payload must be 8 bytes")
        category, flags = struct.unpack("<2I", payload)
        return cls(id, name, parent, ModuleCategory(category), ModuleFlags(flags))


class NamedNode(Protocol):
    @property
    def id(self) -> int: ...

    @property
    def name(self) -> str: ...

    @property
    def kind(self) -> int: ...

    @property
    def parent(self) -> int: ...


def derived_fqns(nodes: Iterable[NamedNode]) -> dict[int, str]:
    """Derive dotted FQNs, refusing missing parents and parent cycles."""
    by_id = {node.id: node for node in nodes}
    cache: dict[int, str] = {}
    visiting: set[int] = set()

    def derive(node_id: int) -> str:
        if node_id in cache:
            return cache[node_id]
        if node_id in visiting:
            raise ValueError(f"node {node_id}: parent cycle while deriving FQN")
        node = by_id.get(node_id)
        if node is None:
            raise ValueError(f"node {node_id}: missing while deriving FQN")
        visiting.add(node_id)
        if node.kind == NodeKind.ROOT:
            value = ""
        else:
            if node.parent == NODE_INVALID or node.parent not in by_id:
                raise ValueError(f"node {node.id}: missing parent while deriving FQN")
            parent = derive(node.parent)
            value = f"{parent}.{node.name}" if parent else node.name
        visiting.remove(node_id)
        cache[node_id] = value
        return value

    for key in by_id:
        derive(key)
    return cache


def derived_fqn(nodes: Iterable[NamedNode], node_id: int) -> str:
    """Derive one object's dotted FQN."""
    try:
        return derived_fqns(nodes)[node_id]
    except KeyError as error:
        raise ValueError(f"node {node_id}: missing while deriving FQN") from error
