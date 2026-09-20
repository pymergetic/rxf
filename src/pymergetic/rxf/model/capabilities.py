"""Typed capability requirements and transient provider manifests."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.execution import Effect
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import CAPABILITY_REQUIREMENT_TYPE


class CapabilityKind(IntEnum):
    CONSOLE = 1
    FILE = 2
    CLOCK = 3
    TIMER = 4
    ENTROPY = 5
    NETWORK = 6
    ENVIRONMENT = 7
    PLATFORM = 8


class CapabilityRights(IntFlag):
    NONE = 0
    LOG = 1 << 0
    OPEN = 1 << 1
    READ = 1 << 2
    WRITE = 1 << 3
    STAT = 1 << 4
    LIST = 1 << 5
    CLOSE = 1 << 6
    MONOTONIC = 1 << 7
    WALL = 1 << 8
    SCHEDULE = 1 << 9
    RANDOM = 1 << 10
    RESOLVE = 1 << 11
    CONNECT = 1 << 12
    LISTEN = 1 << 13
    SEND = 1 << 14
    RECEIVE = 1 << 15
    INSPECT = 1 << 16


class CapabilityRole(IntEnum):
    TARGET = 270
    ENVIRONMENT = 271
    REFUSAL_SET = 272
    REQUIREMENT = 273


def capability_digest(
    name: str,
    kind: CapabilityKind,
    version: int,
    rights: CapabilityRights,
    effects: Effect,
    refusal_set_id: int,
) -> bytes:
    source = f"{name}\0{int(kind)}\0{version}\0{int(rights)}\0{int(effects)}\0{refusal_set_id}".encode()
    return hashlib.sha256(source).digest()


@dataclass(frozen=True)
class CapabilityRequirement:
    id: int
    name: str
    parent: int
    kind: CapabilityKind
    version: int
    rights: CapabilityRights
    effects: Effect
    refusal_set_id: int
    target_ids: tuple[int, ...] = ()
    environment_ids: tuple[int, ...] = ()
    policy: bytes = b""
    semantic_digest: bytes = b""

    def __post_init__(self) -> None:
        if not 0 <= self.id < 1 << 64:
            raise ValueError("capability requirement id must be uint64")
        if self.version <= 0:
            raise ValueError("capability version must be positive")
        if tuple(sorted(set(self.target_ids))) != self.target_ids:
            raise ValueError("capability targets must be sorted and unique")
        if tuple(sorted(set(self.environment_ids))) != self.environment_ids:
            raise ValueError("capability environments must be sorted and unique")
        digest = self.semantic_digest or capability_digest(
            self.name,
            self.kind,
            self.version,
            self.rights,
            self.effects,
            self.refusal_set_id,
        )
        if len(digest) != 32:
            raise ValueError("capability semantic digest must be 32 bytes")
        object.__setattr__(self, "semantic_digest", digest)

    def to_node(self) -> NodeDef:
        data = (
            struct.pack(
                "<7I32s4xQ",
                int(self.kind),
                self.version,
                int(self.rights),
                int(self.effects),
                len(self.target_ids),
                len(self.environment_ids),
                len(self.policy),
                self.semantic_digest,
                self.refusal_set_id,
            )
            + self.policy
        )
        refs = [
            *(
                RefDef(self.id, value, RefKind.DATA, to_off=int(CapabilityRole.TARGET))
                for value in self.target_ids
            ),
            *(
                RefDef(
                    self.id, value, RefKind.DATA, to_off=int(CapabilityRole.ENVIRONMENT)
                )
                for value in self.environment_ids
            ),
            RefDef(
                self.id,
                self.refusal_set_id,
                RefKind.DATA,
                to_off=int(CapabilityRole.REFUSAL_SET),
            ),
        ]
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=CAPABILITY_REQUIREMENT_TYPE,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> CapabilityRequirement:
        header_size = struct.calcsize("<7I32s4xQ")
        if len(node.data) < header_size:
            raise ValueError(f"CapabilityRequirement {node.id} payload is truncated")
        (
            kind,
            version,
            rights,
            effects,
            target_count,
            environment_count,
            policy_size,
            digest,
            refusal,
        ) = struct.unpack("<7I32s4xQ", node.data[:header_size])
        policy = node.data[header_size:]
        targets = tuple(
            ref.target for ref in node.refs if ref.to_off == int(CapabilityRole.TARGET)
        )
        environments = tuple(
            ref.target
            for ref in node.refs
            if ref.to_off == int(CapabilityRole.ENVIRONMENT)
        )
        refusals = tuple(
            ref.target
            for ref in node.refs
            if ref.to_off == int(CapabilityRole.REFUSAL_SET)
        )
        if (
            len(policy) != policy_size
            or len(targets) != target_count
            or len(environments) != environment_count
            or refusals != (refusal,)
        ):
            raise ValueError(
                f"CapabilityRequirement {node.id} payload/ref disagreement"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            CapabilityKind(kind),
            version,
            CapabilityRights(rights),
            Effect(effects),
            refusal,
            targets,
            environments,
            policy,
            digest,
        )


@dataclass(frozen=True)
class CapabilityProvider:
    """Transient host provider; address/handle is never serialized."""

    requirement_id: int
    version: int
    rights: CapabilityRights
    target_ids: frozenset[int] = frozenset()
    environment_ids: frozenset[int] = frozenset()
    semantic_digest: bytes | None = None
    address: object | None = None


@dataclass(frozen=True)
class CapabilityManifest:
    providers: tuple[CapabilityProvider, ...] = ()

    def by_requirement(self) -> dict[int, CapabilityProvider]:
        result: dict[int, CapabilityProvider] = {}
        for provider in self.providers:
            if provider.requirement_id in result:
                raise ValueError(
                    f"duplicate capability provider {provider.requirement_id}"
                )
            result[provider.requirement_id] = provider
        return result


def provider_satisfies(
    requirement: CapabilityRequirement,
    provider: CapabilityProvider,
    target_id: int,
    environment_id: int,
) -> str | None:
    if provider.requirement_id != requirement.id:
        return "requirement identity mismatch"
    if provider.version < requirement.version:
        return "provider version is too old"
    if requirement.rights & ~provider.rights:
        return "provider rights are insufficient"
    if requirement.target_ids and target_id not in requirement.target_ids:
        return "active target is not permitted by requirement"
    if provider.target_ids and target_id not in provider.target_ids:
        return "provider does not support active target"
    if (
        requirement.environment_ids
        and environment_id not in requirement.environment_ids
    ):
        return "active environment is not permitted by requirement"
    if provider.environment_ids and environment_id not in provider.environment_ids:
        return "provider does not support active environment"
    if (
        provider.semantic_digest is not None
        and provider.semantic_digest != requirement.semantic_digest
    ):
        return "semantic digest mismatch"
    return None
