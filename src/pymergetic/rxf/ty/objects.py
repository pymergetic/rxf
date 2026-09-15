"""Self-describing TYPE and FIELD object payloads.

The bootstrap layouts use little-endian fixed-width fields. Semantic IDs and
offsets are uint64; tags, enums, flags, and counts remain uint32. Names and semantic definitions remain ordinary objects.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum, IntFlag

TYPE_DESCRIPTOR_TAG = 0x54595045  # TYPE
FIELD_DESCRIPTOR_TAG = 0x464C4420  # FLD
TYPE_DESCRIPTOR_SIZE = 48
FIELD_DESCRIPTOR_SIZE = 48


class TypeForm(IntEnum):
    META = 0
    VOID = 1
    BOOL = 2
    UINT = 3
    SINT = 4
    FLOAT = 5
    BYTES = 6
    STRUCT = 7
    UNION = 8
    ENUM = 9
    ARRAY = 10
    REF = 11
    FUNCTION = 12
    CONTROL = 13
    EXPRESSION = 14
    EXECUTION = 15
    RESERVED_16 = 16
    OPAQUE = 17


class TypeFlags(IntFlag):
    NONE = 0
    VARIABLE_SIZE = 1 << 0
    SIGNED = 1 << 1
    SELF_DESCRIBING = 1 << 2


class FieldFlags(IntFlag):
    NONE = 0
    MUTABLE = 1 << 0
    OPTIONAL = 1 << 1
    REPEATED = 1 << 2


@dataclass(frozen=True)
class TypeObject:
    id: int
    name: str
    form: TypeForm
    size: int
    align: int
    inner_type: int = 0
    return_type: int = 0
    flags: TypeFlags = TypeFlags.NONE
    field_count: int = 0

    def to_payload(self) -> bytes:
        return struct.pack(
            "<IIQI4xQQII",
            TYPE_DESCRIPTOR_TAG,
            int(self.form),
            self.size,
            self.align,
            self.inner_type,
            self.return_type,
            int(self.flags),
            self.field_count,
        )

    @classmethod
    def from_payload(cls, *, id: int, name: str, payload: bytes) -> TypeObject:
        if len(payload) != TYPE_DESCRIPTOR_SIZE:
            raise ValueError(f"TYPE object {id} descriptor must be 48 bytes")
        words = struct.unpack("<IIQI4xQQII", payload)
        if words[0] != TYPE_DESCRIPTOR_TAG:
            raise ValueError(f"TYPE object {id} has invalid descriptor tag")
        return cls(
            id=id,
            name=name,
            form=TypeForm(words[1]),
            size=words[2],
            align=words[3],
            inner_type=words[4],
            return_type=words[5],
            flags=TypeFlags(words[6]),
            field_count=words[7],
        )


@dataclass(frozen=True)
class FieldObject:
    id: int
    name: str
    owner_type: int
    value_type: int
    offset: int
    count: int = 1
    flags: FieldFlags = FieldFlags.NONE

    def to_payload(self) -> bytes:
        return struct.pack(
            "<I4xQQQQII",
            FIELD_DESCRIPTOR_TAG,
            self.owner_type,
            self.value_type,
            self.offset,
            self.count,
            int(self.flags),
            0,
        )

    @classmethod
    def from_payload(cls, *, id: int, name: str, payload: bytes) -> FieldObject:
        if len(payload) != FIELD_DESCRIPTOR_SIZE:
            raise ValueError(f"FIELD object {id} descriptor must be 48 bytes")
        words = struct.unpack("<I4xQQQQII", payload)
        if words[0] != FIELD_DESCRIPTOR_TAG:
            raise ValueError(f"FIELD object {id} has invalid descriptor tag")
        return cls(
            id=id,
            name=name,
            owner_type=words[1],
            value_type=words[2],
            offset=words[3],
            count=words[4],
            flags=FieldFlags(words[5]),
        )
