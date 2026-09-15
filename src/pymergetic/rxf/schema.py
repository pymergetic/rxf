"""schema.py — the canonical axiom set.

Every primitive's little-endian wire representation.  One table.
This is the hand-written authority; the C header and the JSON schema
are generated views of it.
"""

from dataclasses import dataclass, field
from enum import IntEnum, IntFlag

# ── Canonical persisted primitive types ──


class PrimId(IntEnum):
    """Stable self-described TYPE IDs shared with ``ty.builtins``."""

    VOID = 3
    BOOL = 4
    UINT8_T = 5
    UINT16_T = 6
    UINT32_T = 7
    UINT64_T = 8
    INT32_T = 9
    DOUBLE = 10
    INT8_T = 28
    INT16_T = 29
    INT64_T = 30
    FLOAT = 31


@dataclass(frozen=True)
class PrimDef:
    id: PrimId
    name: str
    size: int
    align: int
    signed: bool = False


PRIMS: dict[PrimId, PrimDef] = {
    item.id: item
    for item in (
        PrimDef(PrimId.VOID, "void", 0, 1),
        PrimDef(PrimId.BOOL, "bool", 1, 1),
        PrimDef(PrimId.UINT8_T, "uint8_t", 1, 1),
        PrimDef(PrimId.UINT16_T, "uint16_t", 2, 2),
        PrimDef(PrimId.UINT32_T, "uint32_t", 4, 4),
        PrimDef(PrimId.UINT64_T, "uint64_t", 8, 8),
        PrimDef(PrimId.INT8_T, "int8_t", 1, 1, True),
        PrimDef(PrimId.INT16_T, "int16_t", 2, 2, True),
        PrimDef(PrimId.INT32_T, "int32_t", 4, 4, True),
        PrimDef(PrimId.INT64_T, "int64_t", 8, 8, True),
        PrimDef(PrimId.FLOAT, "float", 4, 4),
        PrimDef(PrimId.DOUBLE, "double", 8, 8),
    )
}

# RXF persists uint64 offsets and IDs. Native ``size_t``, ``uintptr_t``, and raw
# pointers belong only in target-specific execution metadata, never this table.

# ── Type kind ──


class TypeKind(IntEnum):
    PRIM = 0
    STRUCT = 1
    UNION = 2
    ENUM = 3
    ARRAY = 4
    PTR = 5
    FN_SIG = 6
    OPAQUE = 7
    INTRINSIC = 8


# ── Field ──


class FieldFlag(IntFlag):
    NONE = 0
    MUT = 1 << 0
    CONST_VAL = 1 << 1
    PINNED = 1 << 2
    ALIGN = 1 << 3
    SPAN = 1 << 4


@dataclass
class Field:
    name: str
    type_id: int
    offset: int = 0
    flags: FieldFlag = FieldFlag.NONE
    count: int = 1
    const_val: int = 0

    def is_mutable(self) -> bool:
        return bool(self.flags & FieldFlag.MUT)

    def is_pinned(self) -> bool:
        return bool(self.flags & FieldFlag.PINNED)

    def is_const(self) -> bool:
        return bool(self.flags & FieldFlag.CONST_VAL)

    def field_size(self, get_size) -> int:
        return get_size(self.type_id) * self.count

    def to_dict(self) -> dict:
        d = {"name": self.name, "type": int(self.type_id), "offset": self.offset}
        if self.flags != FieldFlag.NONE:
            d["flags"] = self.flags.value
        if self.count != 1:
            d["count"] = self.count
        if self.is_const():
            d["const_val"] = self.const_val
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Field":
        return cls(
            name=d["name"],
            type_id=d["type"],
            offset=d.get("offset", 0),
            flags=FieldFlag(d.get("flags", 0)),
            count=d.get("count", 1),
            const_val=d.get("const_val", 0),
        )


# ─ Intrinsic built-in types  (ids 16..31) ─


@dataclass
class IntrinsicDef:
    id: int
    name: str
    kind: TypeKind
    instance_size: int = 0
    align: int = 1
    fields: list = field(default_factory=list)


INTRINSIC_ID_REF = 16
INTRINSIC_ID_PATH = 17
INTRINSIC_ID_DIGEST = 18

INTRINSICS: dict[int, IntrinsicDef] = {
    INTRINSIC_ID_REF: IntrinsicDef(
        id=INTRINSIC_ID_REF,
        name="Ref",
        kind=TypeKind.STRUCT,
        instance_size=16,
        align=4,
        fields=[
            Field(
                name="target", type_id=PrimId.UINT32_T, offset=0, flags=FieldFlag.NONE
            ),
            Field(
                name="to_off", type_id=PrimId.UINT64_T, offset=4, flags=FieldFlag.NONE
            ),
            Field(
                name="kind", type_id=PrimId.UINT16_T, offset=12, flags=FieldFlag.NONE
            ),
            Field(
                name="binding", type_id=PrimId.UINT16_T, offset=14, flags=FieldFlag.NONE
            ),
        ],
    ),
    INTRINSIC_ID_PATH: IntrinsicDef(
        id=INTRINSIC_ID_PATH,
        name="Path",
        kind=TypeKind.STRUCT,
        instance_size=12,
        align=4,
        fields=[
            Field(
                name="name_len", type_id=PrimId.UINT32_T, offset=0, flags=FieldFlag.NONE
            ),
            Field(
                name="name_off", type_id=PrimId.UINT32_T, offset=4, flags=FieldFlag.NONE
            ),
            Field(
                name="resolved", type_id=PrimId.UINT32_T, offset=8, flags=FieldFlag.NONE
            ),
        ],
    ),
    INTRINSIC_ID_DIGEST: IntrinsicDef(
        id=INTRINSIC_ID_DIGEST,
        name="Digest",
        kind=TypeKind.STRUCT,
        instance_size=32,
        align=1,
        fields=[
            Field(name="bytes", type_id=PrimId.UINT8_T, flags=FieldFlag.NONE, count=32),
        ],
    ),
}

# ── Node kinds ──


class NodeKind(IntEnum):
    ROOT = 0
    GROUP = 1
    NAMESPACE = 2
    CARD = 3
    TYPE = 4
    FIELD = 5
    FN = 6
    CODE = 7
    DATA = 8
    RESERVED_9 = 9
    SECTION = 10
    LIMIT = 11
    IMPORT = 12
    EXPORT = 13
    VIEW = 14
    TOOL = 15
    BLOB = 16
    CHANNEL = 17
    TRANSACTION = 18
    MODULE = 19


# ─ Container constants ─

MAGIC = b"RXFB"
FORMAT_VERSION = 5


class SectionMap(IntFlag):
    DIRECT = 0x01
    COPY = 0x02
    RELOCATE = 0x04
    BIND = 0x08
    SELECT = 0x10


class SectionPerm(IntFlag):
    R = 0x01
    W = 0x02
    X = 0x04


class RefKind(IntEnum):
    CALL = 0
    DATA = 1
    ENTRY = 2
    TYPE = 3
    IMPORT = 4


class RefBinding(IntEnum):
    MANDATORY = 0
    OPTIONAL = 1


class LocSpace(IntEnum):
    FILE = 0
    MEMORY = 1
    NONE = 2


# ─ Well-known node ids ─

NODE_INVALID = 0xFFFF_FFFF_FFFF_FFFF  # u64 max
NODE_ROOT = 0  # u64
