"""Typed semantic records for the RXF standard library object graph.

All durable links are uint64 object IDs. Host pointers are deliberately absent: a
resolved address is valid only inside the borrow/no-move epoch that produced it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind


class StdlibRecordKind(IntEnum):
    TEMPLATE_MODEL = 1
    RECORD_MODEL = 2
    HEAP_API = 3
    MEMORY_API = 4
    COLLECTION_MODEL = 5
    TEXT_MODEL = 6
    ALGORITHM = 7
    REFUSAL_COMBINATOR = 8


class StdlibFlags(IntFlag):
    NONE = 0
    MUTABLE = 1 << 0
    VARIABLE_SIZE = 1 << 1
    REFERENCE_VALUE = 1 << 2
    MOVABLE = 1 << 3
    PIN_REQUIRED = 1 << 4
    OUTPUT_UNCHANGED_ON_REFUSAL = 1 << 5
    DECLARED_COMPOSED = 1 << 6
    NATIVE_FOUNDATION = 1 << 7


class TaggedVariantRole(IntEnum):
    PAYLOAD_TYPE = 1500
    PAYLOAD_FIELD = 1501


@dataclass(frozen=True)
class TaggedVariant:
    id: int
    name: str
    parent: int
    tag_value: int
    payload_type: int
    payload_field_id: int

    def to_node(self, record_type: int) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack("<QQ", self.tag_value, 0),
            refs=[
                RefDef(
                    self.id,
                    self.payload_type,
                    RefKind.TYPE,
                    to_off=int(TaggedVariantRole.PAYLOAD_TYPE),
                ),
                RefDef(
                    self.id,
                    self.payload_field_id,
                    RefKind.DATA,
                    to_off=int(TaggedVariantRole.PAYLOAD_FIELD),
                ),
            ],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> TaggedVariant:
        if len(node.data) != 16:
            raise ValueError(f"TaggedVariant {node.id} payload must be 16 bytes")
        tag, reserved = struct.unpack("<QQ", node.data)
        if reserved:
            raise ValueError(f"TaggedVariant {node.id} reserved word is nonzero")
        payload_types = [
            r.target
            for r in node.refs
            if r.to_off == int(TaggedVariantRole.PAYLOAD_TYPE)
        ]
        payload_fields = [
            r.target
            for r in node.refs
            if r.to_off == int(TaggedVariantRole.PAYLOAD_FIELD)
        ]
        if len(payload_types) != 1 or len(payload_fields) != 1:
            raise ValueError(f"TaggedVariant {node.id} requires payload type and field")
        return cls(
            node.id, node.name, node.parent, tag, payload_types[0], payload_fields[0]
        )


class HashBucketState(IntEnum):
    EMPTY = 0
    OCCUPIED = 1
    TOMBSTONE = 2


class HashCapacityRule(IntEnum):
    POWER_OF_TWO = 1


class HashProbePolicy(IntEnum):
    LINEAR = 1


class HashTerminationRule(IntEnum):
    EMPTY_OR_CAPACITY_PROBES = 1


class HashGenerationPolicy(IntEnum):
    INCREMENT_ON_MUTATION = 1


class HashVisibilityPolicy(IntEnum):
    LIVE_OR_MATCHING_OWNER_PENDING = 1


class HashAlgorithm(IntEnum):
    FNV1A_64 = 1


class HashKeyRepresentation(IntEnum):
    U64_LITTLE_ENDIAN_8 = 1


class HashSeedPolicy(IntEnum):
    EXPLICIT = 1


class HashFunctionRole(IntEnum):
    HASH_FUNCTION = 1810
    MAP_LOOKUP_FUNCTION = 1811
    SET_LOOKUP_FUNCTION = 1812
    INPUT_TYPE = 1813
    OUTPUT_TYPE = 1814


@dataclass(frozen=True)
class HashAuthority:
    id: int
    name: str
    parent: int
    hash_function: int
    map_lookup_function: int
    set_lookup_function: int
    input_type: int
    output_type: int
    algorithm: HashAlgorithm = HashAlgorithm.FNV1A_64
    representation: HashKeyRepresentation = HashKeyRepresentation.U64_LITTLE_ENDIAN_8
    seed_policy: HashSeedPolicy = HashSeedPolicy.EXPLICIT
    version: int = 1
    seed: int = 14695981039346656037

    def validate(self) -> None:
        if self.version != 1 or self.seed != 14695981039346656037:
            raise ValueError("HashAuthority FNV-1a version/seed mismatch")
        if self.algorithm != HashAlgorithm.FNV1A_64:
            raise ValueError("HashAuthority algorithm mismatch")
        if self.representation != HashKeyRepresentation.U64_LITTLE_ENDIAN_8:
            raise ValueError("HashAuthority representation mismatch")

    def to_node(self, record_type: int) -> NodeDef:
        self.validate()
        refs = [
            RefDef(
                self.id,
                self.hash_function,
                RefKind.DATA,
                to_off=int(HashFunctionRole.HASH_FUNCTION),
            ),
            RefDef(
                self.id,
                self.map_lookup_function,
                RefKind.DATA,
                to_off=int(HashFunctionRole.MAP_LOOKUP_FUNCTION),
            ),
            RefDef(
                self.id,
                self.set_lookup_function,
                RefKind.DATA,
                to_off=int(HashFunctionRole.SET_LOOKUP_FUNCTION),
            ),
            RefDef(
                self.id,
                self.input_type,
                RefKind.TYPE,
                to_off=int(HashFunctionRole.INPUT_TYPE),
            ),
            RefDef(
                self.id,
                self.output_type,
                RefKind.TYPE,
                to_off=int(HashFunctionRole.OUTPUT_TYPE),
            ),
        ]
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack(
                "<4IQ",
                int(self.algorithm),
                int(self.representation),
                int(self.seed_policy),
                self.version,
                self.seed,
            ),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> HashAuthority:
        if len(node.data) != 24:
            raise ValueError(f"HashAuthority {node.id} payload must be 24 bytes")
        algorithm, representation, seed_policy, version, seed = struct.unpack(
            "<4IQ", node.data
        )

        def one(role: HashFunctionRole) -> int:
            values = [ref.target for ref in node.refs if ref.to_off == int(role)]
            if len(values) != 1:
                raise ValueError(f"HashAuthority {node.id} role {role.name} is invalid")
            return values[0]

        result = cls(
            node.id,
            node.name,
            node.parent,
            one(HashFunctionRole.HASH_FUNCTION),
            one(HashFunctionRole.MAP_LOOKUP_FUNCTION),
            one(HashFunctionRole.SET_LOOKUP_FUNCTION),
            one(HashFunctionRole.INPUT_TYPE),
            one(HashFunctionRole.OUTPUT_TYPE),
            HashAlgorithm(algorithm),
            HashKeyRepresentation(representation),
            HashSeedPolicy(seed_policy),
            version,
            seed,
        )
        result.validate()
        return result


class HashAuthorityRole(IntEnum):
    MAP_TYPE = 1800
    SET_TYPE = 1801
    MAP_BUCKET_TYPE = 1802
    SET_BUCKET_TYPE = 1803
    BUCKET_STATE_TYPE = 1804
    HASH_ALGORITHM = 1805


@dataclass(frozen=True)
class HashStorageAuthority:
    id: int
    name: str
    parent: int
    map_type: int
    set_type: int
    map_bucket_type: int
    set_bucket_type: int
    bucket_state_type: int
    minimum_capacity: int = 8
    load_numerator: int = 3
    load_denominator: int = 4
    tombstone_numerator: int = 1
    tombstone_denominator: int = 4
    capacity_rule: HashCapacityRule = HashCapacityRule.POWER_OF_TWO
    probe_policy: HashProbePolicy = HashProbePolicy.LINEAR
    termination_rule: HashTerminationRule = HashTerminationRule.EMPTY_OR_CAPACITY_PROBES
    generation_policy: HashGenerationPolicy = HashGenerationPolicy.INCREMENT_ON_MUTATION
    visibility_policy: HashVisibilityPolicy = (
        HashVisibilityPolicy.LIVE_OR_MATCHING_OWNER_PENDING
    )
    hash_algorithm_id: int = 0

    def validate(self) -> None:
        if self.minimum_capacity < 2 or self.minimum_capacity & (
            self.minimum_capacity - 1
        ):
            raise ValueError(
                "HashStorageAuthority minimum capacity must be power of two"
            )
        if not (0 < self.load_numerator < self.load_denominator):
            raise ValueError("HashStorageAuthority load threshold is invalid")
        if not (0 <= self.tombstone_numerator < self.tombstone_denominator):
            raise ValueError("HashStorageAuthority tombstone threshold is invalid")

    def to_node(self, record_type: int) -> NodeDef:
        self.validate()
        refs = [
            RefDef(
                self.id,
                self.map_type,
                RefKind.TYPE,
                to_off=int(HashAuthorityRole.MAP_TYPE),
            ),
            RefDef(
                self.id,
                self.set_type,
                RefKind.TYPE,
                to_off=int(HashAuthorityRole.SET_TYPE),
            ),
            RefDef(
                self.id,
                self.map_bucket_type,
                RefKind.TYPE,
                to_off=int(HashAuthorityRole.MAP_BUCKET_TYPE),
            ),
            RefDef(
                self.id,
                self.set_bucket_type,
                RefKind.TYPE,
                to_off=int(HashAuthorityRole.SET_BUCKET_TYPE),
            ),
            RefDef(
                self.id,
                self.bucket_state_type,
                RefKind.TYPE,
                to_off=int(HashAuthorityRole.BUCKET_STATE_TYPE),
            ),
        ]
        if self.hash_algorithm_id:
            refs.append(
                RefDef(
                    self.id,
                    self.hash_algorithm_id,
                    RefKind.DATA,
                    to_off=int(HashAuthorityRole.HASH_ALGORITHM),
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack(
                "<10I",
                self.minimum_capacity,
                self.load_numerator,
                self.load_denominator,
                self.tombstone_numerator,
                self.tombstone_denominator,
                int(self.capacity_rule),
                int(self.probe_policy),
                int(self.termination_rule),
                int(self.generation_policy),
                int(self.visibility_policy),
            ),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> HashStorageAuthority:
        if len(node.data) != 40:
            raise ValueError(f"HashStorageAuthority {node.id} payload must be 40 bytes")
        values = struct.unpack("<10I", node.data)

        def one(role: HashAuthorityRole) -> int:
            matches = [ref.target for ref in node.refs if ref.to_off == int(role)]
            if len(matches) != 1:
                raise ValueError(
                    f"HashStorageAuthority {node.id} role {role.name} is invalid"
                )
            return matches[0]

        algorithms = [
            ref.target
            for ref in node.refs
            if ref.to_off == int(HashAuthorityRole.HASH_ALGORITHM)
        ]
        result = cls(
            node.id,
            node.name,
            node.parent,
            one(HashAuthorityRole.MAP_TYPE),
            one(HashAuthorityRole.SET_TYPE),
            one(HashAuthorityRole.MAP_BUCKET_TYPE),
            one(HashAuthorityRole.SET_BUCKET_TYPE),
            one(HashAuthorityRole.BUCKET_STATE_TYPE),
            values[0],
            values[1],
            values[2],
            values[3],
            values[4],
            HashCapacityRule(values[5]),
            HashProbePolicy(values[6]),
            HashTerminationRule(values[7]),
            HashGenerationPolicy(values[8]),
            HashVisibilityPolicy(values[9]),
            algorithms[0] if algorithms else 0,
        )
        result.validate()
        return result

    def validate_model(self, nodes: dict[int, NodeDef]) -> None:
        from pymergetic.rxf.ty.builtins import FIELD_TYPE, REF_TYPE, U64_TYPE
        from pymergetic.rxf.ty.objects import FieldObject, TypeObject

        expected = {
            self.map_type: (
                56,
                (
                    ("bucket_storage", REF_TYPE, 0),
                    ("capacity", U64_TYPE, 24),
                    ("count", U64_TYPE, 32),
                    ("tombstone_count", U64_TYPE, 40),
                    ("generation", U64_TYPE, 48),
                ),
            ),
            self.set_type: (
                56,
                (
                    ("bucket_storage", REF_TYPE, 0),
                    ("capacity", U64_TYPE, 24),
                    ("count", U64_TYPE, 32),
                    ("tombstone_count", U64_TYPE, 40),
                    ("generation", U64_TYPE, 48),
                ),
            ),
            self.map_bucket_type: (
                48,
                (
                    ("state", self.bucket_state_type, 0),
                    ("stored_hash", U64_TYPE, 8),
                    ("key", U64_TYPE, 16),
                    ("value", REF_TYPE, 24),
                ),
            ),
            self.set_bucket_type: (
                24,
                (
                    ("state", self.bucket_state_type, 0),
                    ("stored_hash", U64_TYPE, 8),
                    ("key", U64_TYPE, 16),
                ),
            ),
        }
        for type_id, (size, fields) in expected.items():
            node = nodes.get(type_id)
            if node is None:
                raise ValueError(f"HashStorageAuthority missing TYPE {type_id}")
            descriptor = TypeObject.from_payload(
                id=node.id, name=node.name, payload=node.data
            )
            actual = tuple(
                (field.name, field.value_type, field.offset)
                for child in nodes.values()
                if child.parent == type_id and child.type_id == FIELD_TYPE
                for field in (
                    FieldObject.from_payload(
                        id=child.id, name=child.name, payload=child.data
                    ),
                )
            )
            if descriptor.size != size or descriptor.align != 8 or actual != fields:
                raise ValueError(f"HashStorageAuthority TYPE {type_id} layout mismatch")
        state = nodes.get(self.bucket_state_type)
        if state is None:
            raise ValueError("HashStorageAuthority bucket state TYPE is missing")
        descriptor = TypeObject.from_payload(
            id=state.id, name=state.name, payload=state.data
        )
        if descriptor.size != 4 or descriptor.align != 4:
            raise ValueError("HashStorageAuthority bucket state layout mismatch")

    def validate_counts(self, capacity: int, count: int, tombstones: int) -> None:
        if capacity < self.minimum_capacity or capacity & (capacity - 1):
            raise ValueError("hash capacity violates authority")
        if count > capacity or tombstones > capacity - count:
            raise ValueError("hash count plus tombstones exceeds capacity")


class CanonicalOrderPolicy(IntEnum):
    ASCENDING_SLOT_INDEX = 1


class CanonicalOrderRole(IntEnum):
    INPUT_TYPE = 1700
    OUTPUT_TYPE = 1701


@dataclass(frozen=True)
class CanonicalOrderAuthority:
    id: int
    name: str
    parent: int
    input_type: int
    output_type: int
    policy: CanonicalOrderPolicy
    representation_preserving: bool = True

    def to_node(self, record_type: int) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack(
                "<4I", int(self.policy), int(self.representation_preserving), 0, 0
            ),
            refs=[
                RefDef(
                    self.id,
                    self.input_type,
                    RefKind.TYPE,
                    to_off=int(CanonicalOrderRole.INPUT_TYPE),
                ),
                RefDef(
                    self.id,
                    self.output_type,
                    RefKind.TYPE,
                    to_off=int(CanonicalOrderRole.OUTPUT_TYPE),
                ),
            ],
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> CanonicalOrderAuthority:
        if len(node.data) != 16:
            raise ValueError(
                f"CanonicalOrderAuthority {node.id} payload must be 16 bytes"
            )
        policy, preserving, reserved0, reserved1 = struct.unpack("<4I", node.data)
        inputs = [
            r.target
            for r in node.refs
            if r.to_off == int(CanonicalOrderRole.INPUT_TYPE)
        ]
        outputs = [
            r.target
            for r in node.refs
            if r.to_off == int(CanonicalOrderRole.OUTPUT_TYPE)
        ]
        if (
            reserved0
            or reserved1
            or preserving not in {0, 1}
            or len(inputs) != 1
            or len(outputs) != 1
        ):
            raise ValueError(f"CanonicalOrderAuthority {node.id} is invalid")
        return cls(
            node.id,
            node.name,
            node.parent,
            inputs[0],
            outputs[0],
            CanonicalOrderPolicy(policy),
            bool(preserving),
        )


class RefusalDefaultPolicy(IntEnum):
    PROPAGATE_UNMATCHED = 1
    REFUSE_UNMATCHED = 2


class RefusalTransformRole(IntEnum):
    SOURCE_SET = 1600
    DESTINATION_SET = 1601
    SOURCE_VARIANT = 1602
    DESTINATION_VARIANT = 1603
    PAYLOAD_TRANSFORM = 1604
    ENTRY = 1605
    PAYLOAD_FIELD = 1606
    CONTEXT_VALUE = 1607
    PAYLOAD_CONSTRUCTOR = 1608


@dataclass(frozen=True)
class RefusalMappingEntry:
    id: int
    name: str
    parent: int
    source_variant_id: int
    source_status: int
    destination_variant_id: int
    destination_status: int
    payload_transform_id: int = 0

    def to_node(self, record_type: int) -> NodeDef:
        refs = [
            RefDef(
                self.id,
                self.source_variant_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.SOURCE_VARIANT),
            ),
            RefDef(
                self.id,
                self.destination_variant_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.DESTINATION_VARIANT),
            ),
        ]
        if self.payload_transform_id:
            refs.append(
                RefDef(
                    self.id,
                    self.payload_transform_id,
                    RefKind.CALL,
                    to_off=int(RefusalTransformRole.PAYLOAD_TRANSFORM),
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack("<IIQ", self.source_status, self.destination_status, 0),
            refs=refs,
        )


@dataclass(frozen=True)
class RefusalMapping:
    id: int
    name: str
    parent: int
    source_set_id: int
    destination_set_id: int
    entry_ids: tuple[int, ...]
    default_policy: RefusalDefaultPolicy

    def to_node(self, record_type: int) -> NodeDef:
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack(
                "<4I", int(self.default_policy), len(self.entry_ids), 0, 0
            ),
            refs=[
                RefDef(
                    self.id,
                    self.source_set_id,
                    RefKind.DATA,
                    to_off=int(RefusalTransformRole.SOURCE_SET),
                ),
                RefDef(
                    self.id,
                    self.destination_set_id,
                    RefKind.DATA,
                    to_off=int(RefusalTransformRole.DESTINATION_SET),
                ),
                *(
                    RefDef(
                        self.id,
                        entry,
                        RefKind.DATA,
                        to_off=int(RefusalTransformRole.ENTRY),
                    )
                    for entry in self.entry_ids
                ),
            ],
        )


@dataclass(frozen=True)
class RefusalEnrichment:
    id: int
    name: str
    parent: int
    source_set_id: int
    destination_set_id: int
    source_variant_id: int
    destination_variant_id: int
    payload_field_ids: tuple[int, ...]
    context_value_ids: tuple[int, ...]
    payload_constructor_id: int = 0
    preserve_identity: bool = True

    def to_node(self, record_type: int) -> NodeDef:
        refs = [
            RefDef(
                self.id,
                self.source_set_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.SOURCE_SET),
            ),
            RefDef(
                self.id,
                self.destination_set_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.DESTINATION_SET),
            ),
            RefDef(
                self.id,
                self.source_variant_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.SOURCE_VARIANT),
            ),
            RefDef(
                self.id,
                self.destination_variant_id,
                RefKind.DATA,
                to_off=int(RefusalTransformRole.DESTINATION_VARIANT),
            ),
            *(
                RefDef(
                    self.id,
                    field,
                    RefKind.DATA,
                    to_off=int(RefusalTransformRole.PAYLOAD_FIELD),
                )
                for field in self.payload_field_ids
            ),
            *(
                RefDef(
                    self.id,
                    value,
                    RefKind.DATA,
                    to_off=int(RefusalTransformRole.CONTEXT_VALUE),
                )
                for value in self.context_value_ids
            ),
        ]
        if self.payload_constructor_id:
            refs.append(
                RefDef(
                    self.id,
                    self.payload_constructor_id,
                    RefKind.CALL,
                    to_off=int(RefusalTransformRole.PAYLOAD_CONSTRUCTOR),
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=struct.pack(
                "<4I",
                int(self.preserve_identity),
                len(self.payload_field_ids),
                len(self.context_value_ids),
                0,
            ),
            refs=refs,
        )


class StdlibRole(IntEnum):
    VALUE_TYPE = 300
    AUXILIARY_TYPE = 301
    FUNCTION = 304


def _checked_id(value: int, label: str) -> int:
    if not 0 <= value <= 0xFFFF_FFFF_FFFF_FFFF:
        raise ValueError(f"{label} must be uint64")
    return value


@dataclass(frozen=True)
class StdlibRecord:
    id: int
    name: str
    parent: int
    kind: StdlibRecordKind
    value_type: int = 0
    auxiliary_type: int = 0
    extent: int = 0
    flags: StdlibFlags = StdlibFlags.NONE
    related_ids: tuple[int, ...] = ()

    def to_payload(self) -> bytes:
        for label, value in (
            ("id", self.id),
            ("parent", self.parent),
            ("value_type", self.value_type),
            ("auxiliary_type", self.auxiliary_type),
            ("extent", self.extent),
        ):
            _checked_id(value, label)
        return struct.pack(
            "<4I3Q",
            int(self.kind),
            int(self.flags),
            len(self.related_ids),
            0,
            self.value_type,
            self.auxiliary_type,
            self.extent,
        )

    def to_node(self, record_type: int) -> NodeDef:
        refs = []
        if self.value_type:
            refs.append(
                RefDef(
                    self.id,
                    self.value_type,
                    RefKind.TYPE,
                    to_off=int(StdlibRole.VALUE_TYPE),
                )
            )
        if self.auxiliary_type:
            refs.append(
                RefDef(
                    self.id,
                    self.auxiliary_type,
                    RefKind.TYPE,
                    to_off=int(StdlibRole.AUXILIARY_TYPE),
                )
            )
        refs.extend(
            RefDef(self.id, target, RefKind.DATA, to_off=int(StdlibRole.FUNCTION))
            for target in self.related_ids
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=self.to_payload(),
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> StdlibRecord:
        if len(node.data) != 40:
            raise ValueError(f"StdlibRecord object {node.id} payload must be 40 bytes")
        kind, flags, count, reserved, value, auxiliary, extent = struct.unpack(
            "<4I3Q", node.data
        )
        if reserved:
            raise ValueError(f"StdlibRecord object {node.id} reserved word is nonzero")
        related = tuple(
            ref.target for ref in node.refs if ref.to_off == int(StdlibRole.FUNCTION)
        )
        if len(related) != count:
            raise ValueError(
                f"StdlibRecord object {node.id} related count disagrees with refs"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            StdlibRecordKind(kind),
            value,
            auxiliary,
            extent,
            StdlibFlags(flags),
            related,
        )


@dataclass(frozen=True)
class HeapSlot:
    """Stable-ID slot metadata; address is transient and intentionally absent."""

    object_id: int
    offset: int
    size: int
    alignment: int
    generation: int = 0
    pin_count: int = 0
    borrow_count: int = 0

    def validate(self, committed: int) -> None:
        _checked_id(self.object_id, "object_id")
        if self.alignment <= 0 or self.alignment & (self.alignment - 1):
            raise ValueError("alignment must be a positive power of two")
        if self.offset % self.alignment:
            raise ValueError("slot offset is misaligned")
        if self.offset > committed or self.size > committed - self.offset:
            raise ValueError("slot exceeds committed heap")
        if self.pin_count < 0 or self.borrow_count < 0:
            raise ValueError("pin and borrow counts must be nonnegative")


@dataclass(frozen=True)
class MovePlanEntry:
    object_id: int
    source_offset: int
    destination_offset: int
    size: int

    def validate(self, committed: int, immovable_ids: set[int]) -> None:
        if (
            self.object_id in immovable_ids
            and self.source_offset != self.destination_offset
        ):
            raise ValueError("pinned or borrowed object cannot move")
        if max(self.source_offset, self.destination_offset) > committed:
            raise ValueError("move starts outside committed heap")
        if (
            self.size > committed - self.source_offset
            or self.size > committed - self.destination_offset
        ):
            raise ValueError("move exceeds committed heap")


def aligned_frontier(
    frontier: int, size: int, alignment: int, committed: int
) -> tuple[int, int]:
    """Return offset/new frontier without mutating allocator state."""
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    if min(frontier, size, committed) < 0 or frontier > committed:
        raise ValueError("invalid heap geometry")
    offset = (frontier + alignment - 1) & -alignment
    if offset < frontier or size > committed - offset:
        raise MemoryError("heap allocation refused: out of memory")
    return offset, offset + size


@dataclass(frozen=True)
class ObjectHandle:
    """Canonical durable handle; resolving it yields only a transient address."""

    object_id: int
    generation: int
    offset: int = 0

    def __post_init__(self) -> None:
        _checked_id(self.object_id, "object_id")
        _checked_id(self.generation, "generation")
        _checked_id(self.offset, "offset")

    def to_bytes(self) -> bytes:
        return struct.pack("<3Q", self.object_id, self.generation, self.offset)

    @classmethod
    def from_bytes(cls, payload: bytes) -> ObjectHandle:
        if len(payload) != 24:
            raise ValueError("ObjectHandle payload must be 24 bytes")
        return cls(*struct.unpack("<3Q", payload))


@dataclass(frozen=True)
class BoundedView:
    handle: ObjectHandle
    start: int
    length: int
    mutable: bool = False

    def __post_init__(self) -> None:
        _checked_id(self.start, "start")
        _checked_id(self.length, "length")
        if self.start > (1 << 64) - 1 - self.length:
            raise ValueError("view extent overflows uint64")

    def checked_extent(self, object_size: int) -> tuple[int, int]:
        absolute = self.handle.offset + self.start
        if absolute > (1 << 64) - 1 or self.length > object_size - absolute:
            raise ValueError("view exceeds object extent")
        return absolute, absolute + self.length


SPECIALIZED_TYPE_NAMESPACE = 0xD000_0000_0000_0000


@dataclass(frozen=True)
class ConcreteType:
    id: int
    template: str
    type_arguments: tuple[int, ...]
    const_arguments: tuple[int, ...]
    size: int
    alignment: int


def specialize_value_type(
    template: str,
    type_arguments: tuple[tuple[int, int, int], ...],
    const_arguments: tuple[int, ...] = (),
) -> ConcreteType:
    """Deterministically specialize core value templates without compiler state.

    Type arguments are ``(type_id, size, alignment)``. Const arguments are uint64.
    """
    import hashlib

    if not type_arguments:
        raise ValueError("specialization requires a type argument")
    for type_id, size, alignment in type_arguments:
        _checked_id(type_id, "type argument")
        if size < 0 or alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("invalid type argument layout")
    for value in const_arguments:
        _checked_id(value, "const argument")
    if template == "FixedArray":
        if len(type_arguments) != 1 or len(const_arguments) != 1:
            raise ValueError("FixedArray requires one type and one const argument")
        _element_id, element_size, alignment = type_arguments[0]
        count = const_arguments[0]
        if element_size and count > ((1 << 64) - 1) // element_size:
            raise OverflowError("FixedArray extent overflows uint64")
        size = element_size * count
    elif template == "Option":
        if len(type_arguments) != 1 or const_arguments:
            raise ValueError("Option requires one type argument")
        __element_id, element_size, alignment = type_arguments[0]
        payload_offset = (4 + alignment - 1) & -alignment
        size = (payload_offset + element_size + alignment - 1) & -alignment
    elif template == "TypedResult":
        if len(type_arguments) != 2 or const_arguments:
            raise ValueError("TypedResult requires success and refusal type arguments")
        alignment = max(value[2] for value in type_arguments)
        payload = max(value[1] for value in type_arguments)
        payload_offset = (4 + alignment - 1) & -alignment
        size = (payload_offset + payload + alignment - 1) & -alignment
    elif template.startswith("Tuple"):
        alignment = max(value[2] for value in type_arguments)
        cursor = 0
        for _type_id, element_size, element_align in type_arguments:
            cursor = (cursor + element_align - 1) & -element_align
            cursor += element_size
        size = (cursor + alignment - 1) & -alignment
    else:
        # Handles and iterator/callable descriptors are always fixed uint64 geometry.
        alignment, size = 8, 24
    payload = template.encode() + b"\0"
    payload += b"".join(struct.pack("<3Q", *value) for value in type_arguments)
    payload += b"".join(struct.pack("<Q", value) for value in const_arguments)
    digest = hashlib.sha256(payload).digest()
    identity = SPECIALIZED_TYPE_NAMESPACE | (
        int.from_bytes(digest[:8], "big") & 0x0FFF_FFFF_FFFF_FFFF
    )
    return ConcreteType(
        identity,
        template,
        tuple(v[0] for v in type_arguments),
        const_arguments,
        size,
        alignment,
    )
