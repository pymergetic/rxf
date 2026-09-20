"""Typed native target declarations and canonical corpus targets."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.execution import CallRole, Endianness
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    ABI_TYPE,
    ARCHITECTURE_TYPE,
    ENVIRONMENT_TYPE,
    FEATURE_SET_TYPE,
    FEATURE_TYPE,
    RUNTIME_TARGET_TYPE,
)


class ArchitectureKind(IntEnum):
    X86_64 = 1
    AARCH64 = 2


class ABIKind(IntEnum):
    SYSV = 1
    AAPCS64 = 2


class CallingConvention(IntEnum):
    SYSTEM = 1


class EnvironmentKind(IntEnum):
    LINUX = 1
    UEFI = 2
    BIOS = 3


class FeatureKind(IntEnum):
    ISA = 1


class TargetFlags(IntFlag):
    NONE = 0


class FeatureSetFlags(IntFlag):
    NONE = 0


def _ref(source: int, target: int, role: CallRole) -> RefDef:
    return RefDef(source, target, RefKind.DATA, to_off=int(role))


def _one_ref(node: NodeDef, role: CallRole, expected: int) -> None:
    actual = tuple(ref.target for ref in node.refs if ref.to_off == int(role))
    if actual != (expected,):
        raise ValueError(f"{node.name} payload/ref disagreement for {role.name}")


@dataclass(frozen=True)
class ArchitectureObject:
    id: int
    name: str
    parent: int
    kind: ArchitectureKind
    word_bits: int
    endianness: Endianness
    flags: TargetFlags = TargetFlags.NONE

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ARCHITECTURE_TYPE,
            data=struct.pack(
                "<4I", self.kind, self.word_bits, self.endianness, self.flags
            ),
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> ArchitectureObject:
        if len(node.data) != 16:
            raise ValueError(f"Architecture {node.id} payload must be 16 bytes")
        kind, bits, endian, flags = struct.unpack("<4I", node.data)
        if bits not in (32, 64):
            raise ValueError(f"Architecture {node.id} word_bits is invalid")
        return cls(
            node.id,
            node.name,
            node.parent,
            ArchitectureKind(kind),
            bits,
            Endianness(endian),
            TargetFlags(flags),
        )


@dataclass(frozen=True)
class ABIObject:
    id: int
    name: str
    parent: int
    kind: ABIKind
    architecture_id: int
    version: int = 1
    calling_convention: CallingConvention = CallingConvention.SYSTEM
    flags: TargetFlags = TargetFlags.NONE

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ABI_TYPE,
            data=struct.pack(
                "<4IQ",
                self.kind,
                self.version,
                self.calling_convention,
                self.flags,
                self.architecture_id,
            ),
            refs=[_ref(self.id, self.architecture_id, CallRole.ARCHITECTURE)],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> ABIObject:
        if len(node.data) != 24:
            raise ValueError(f"ABI {node.id} payload must be 24 bytes")
        kind, version, convention, flags, arch = struct.unpack("<4IQ", node.data)
        _one_ref(node, CallRole.ARCHITECTURE, arch)
        return cls(
            node.id,
            node.name,
            node.parent,
            ABIKind(kind),
            arch,
            version,
            CallingConvention(convention),
            TargetFlags(flags),
        )


@dataclass(frozen=True)
class EnvironmentObject:
    id: int
    name: str
    parent: int
    kind: EnvironmentKind
    version: int = 1
    flags: TargetFlags = TargetFlags.NONE

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=ENVIRONMENT_TYPE,
            data=struct.pack("<3I4x", self.kind, self.version, self.flags),
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> EnvironmentObject:
        if len(node.data) != 16:
            raise ValueError(f"Environment {node.id} payload must be 16 bytes")
        kind, version, flags = struct.unpack("<3I4x", node.data)
        return cls(
            node.id,
            node.name,
            node.parent,
            EnvironmentKind(kind),
            version,
            TargetFlags(flags),
        )


@dataclass(frozen=True)
class FeatureObject:
    id: int
    name: str
    parent: int
    kind: FeatureKind
    architecture_id: int
    version: int = 1
    flags: TargetFlags = TargetFlags.NONE

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=FEATURE_TYPE,
            data=struct.pack(
                "<3I4xQ", self.kind, self.version, self.flags, self.architecture_id
            ),
            refs=[_ref(self.id, self.architecture_id, CallRole.ARCHITECTURE)],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> FeatureObject:
        if len(node.data) != 24:
            raise ValueError(f"Feature {node.id} payload must be 24 bytes")
        kind, version, flags, arch = struct.unpack("<3I4xQ", node.data)
        _one_ref(node, CallRole.ARCHITECTURE, arch)
        return cls(
            node.id,
            node.name,
            node.parent,
            FeatureKind(kind),
            arch,
            version,
            TargetFlags(flags),
        )


@dataclass(frozen=True)
class FeatureSetObject:
    id: int
    name: str
    parent: int
    feature_ids: tuple[int, ...] = ()
    flags: FeatureSetFlags = FeatureSetFlags.NONE

    def __post_init__(self) -> None:
        if tuple(sorted(set(self.feature_ids))) != self.feature_ids:
            raise ValueError("FeatureSet features must be sorted and unique")

    def to_node(self) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=FEATURE_SET_TYPE,
            data=struct.pack("<2I", len(self.feature_ids), self.flags),
            refs=[
                _ref(self.id, feature, CallRole.FEATURE) for feature in self.feature_ids
            ],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> FeatureSetObject:
        if len(node.data) != 8:
            raise ValueError(f"FeatureSet {node.id} payload must be 8 bytes")
        count, flags = struct.unpack("<2I", node.data)
        features = tuple(
            ref.target for ref in node.refs if ref.to_off == int(CallRole.FEATURE)
        )
        if len(features) != count:
            raise ValueError(f"FeatureSet {node.id} count/ref disagreement")
        return cls(node.id, node.name, node.parent, features, FeatureSetFlags(flags))


@dataclass(frozen=True)
class RuntimeTargetObject:
    id: int
    name: str
    parent: int
    architecture_id: int
    abi_id: int
    environment_id: int
    feature_set_id: int
    flags: TargetFlags = TargetFlags.NONE

    def to_node(self) -> NodeDef:
        values = (
            self.architecture_id,
            self.abi_id,
            self.environment_id,
            self.feature_set_id,
        )
        roles = (
            CallRole.ARCHITECTURE,
            CallRole.ABI,
            CallRole.ENVIRONMENT,
            CallRole.FEATURE_SET,
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=RUNTIME_TARGET_TYPE,
            data=struct.pack("<4QI4x", *values, self.flags),
            refs=[_ref(self.id, v, r) for v, r in zip(values, roles, strict=True)],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> RuntimeTargetObject:
        if len(node.data) != 40:
            raise ValueError(f"RuntimeTarget {node.id} payload must be 40 bytes")
        arch, abi, env, features, flags = struct.unpack("<4QI4x", node.data)
        for role, value in (
            (CallRole.ARCHITECTURE, arch),
            (CallRole.ABI, abi),
            (CallRole.ENVIRONMENT, env),
            (CallRole.FEATURE_SET, features),
        ):
            _one_ref(node, role, value)
        return cls(
            node.id,
            node.name,
            node.parent,
            arch,
            abi,
            env,
            features,
            TargetFlags(flags),
        )


X86_64_ID = 810
AARCH64_ID = 811
SYSV_ID = 812
AAPCS64_ID = 813
LINUX_ID = 814
UEFI_ID = 815
EMPTY_FEATURES_ID = 816
X86_64_BASELINE_FEATURE_ID = 819
AARCH64_BASELINE_FEATURE_ID = 820
AARCH64_FEATURES_ID = 821
BIOS_ID = 822
AARCH64_LINUX_TARGET_ID = 823
X86_64_UEFI_TARGET_ID = 824
X86_64_BIOS_TARGET_ID = 825
X86_64_LINUX_TARGET_ID = 817
AARCH64_UEFI_TARGET_ID = 818
X86_64_RETURN_U32 = b"\x89\xf8\xc3"
AARCH64_RETURN_U32 = b"\xc0\x03\x5f\xd6"


def native_target_nodes(parent: int) -> list[NodeDef]:
    return [
        ArchitectureObject(
            X86_64_ID, "x86_64", parent, ArchitectureKind.X86_64, 64, Endianness.LITTLE
        ).to_node(),
        ArchitectureObject(
            AARCH64_ID,
            "aarch64",
            parent,
            ArchitectureKind.AARCH64,
            64,
            Endianness.LITTLE,
        ).to_node(),
        ABIObject(SYSV_ID, "sysv", parent, ABIKind.SYSV, X86_64_ID).to_node(),
        ABIObject(AAPCS64_ID, "aapcs64", parent, ABIKind.AAPCS64, AARCH64_ID).to_node(),
        EnvironmentObject(LINUX_ID, "linux", parent, EnvironmentKind.LINUX).to_node(),
        EnvironmentObject(UEFI_ID, "uefi", parent, EnvironmentKind.UEFI).to_node(),
        EnvironmentObject(BIOS_ID, "bios", parent, EnvironmentKind.BIOS).to_node(),
        FeatureObject(
            X86_64_BASELINE_FEATURE_ID,
            "x86_64_baseline",
            parent,
            FeatureKind.ISA,
            X86_64_ID,
        ).to_node(),
        FeatureObject(
            AARCH64_BASELINE_FEATURE_ID,
            "aarch64_baseline",
            parent,
            FeatureKind.ISA,
            AARCH64_ID,
        ).to_node(),
        FeatureSetObject(
            EMPTY_FEATURES_ID, "x86_64_features", parent, (X86_64_BASELINE_FEATURE_ID,)
        ).to_node(),
        FeatureSetObject(
            AARCH64_FEATURES_ID,
            "aarch64_features",
            parent,
            (AARCH64_BASELINE_FEATURE_ID,),
        ).to_node(),
        RuntimeTargetObject(
            X86_64_LINUX_TARGET_ID,
            "x86_64_linux_sysv",
            parent,
            X86_64_ID,
            SYSV_ID,
            LINUX_ID,
            EMPTY_FEATURES_ID,
        ).to_node(),
        RuntimeTargetObject(
            AARCH64_UEFI_TARGET_ID,
            "aarch64_uefi_aapcs64",
            parent,
            AARCH64_ID,
            AAPCS64_ID,
            UEFI_ID,
            AARCH64_FEATURES_ID,
        ).to_node(),
        RuntimeTargetObject(
            AARCH64_LINUX_TARGET_ID,
            "aarch64_linux_aapcs64",
            parent,
            AARCH64_ID,
            AAPCS64_ID,
            LINUX_ID,
            AARCH64_FEATURES_ID,
        ).to_node(),
        RuntimeTargetObject(
            X86_64_UEFI_TARGET_ID,
            "x86_64_uefi_sysv",
            parent,
            X86_64_ID,
            SYSV_ID,
            UEFI_ID,
            EMPTY_FEATURES_ID,
        ).to_node(),
        RuntimeTargetObject(
            X86_64_BIOS_TARGET_ID,
            "x86_64_bios_sysv",
            parent,
            X86_64_ID,
            SYSV_ID,
            BIOS_ID,
            EMPTY_FEATURES_ID,
        ).to_node(),
    ]
