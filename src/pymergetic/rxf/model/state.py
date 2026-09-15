"""Orthogonal RXF object state and ordinary ownership classification."""

from dataclasses import dataclass
from enum import IntEnum


class Provenance(IntEnum):
    RUNTIME = 0
    IMAGE = 1


class Disposition(IntEnum):
    RETAIN = 0
    TRANSIENT = 1
    TOMBSTONE = 2


class Cleanliness(IntEnum):
    CLEAN = 0
    DIRTY = 1


class Mobility(IntEnum):
    MOVABLE = 0
    PINNED = 1
    RELOCATABLE = 2


class OwnerKind(IntEnum):
    SYSTEM = 0
    USER = 1
    APPLICATION = 2
    BUILD = 3
    DEVICE = 4
    EXTERNAL = 5
    SHARED = 6


_PROVENANCE_SHIFT = 0
_DISPOSITION_SHIFT = 1
_CLEANLINESS_SHIFT = 3
_MOBILITY_SHIFT = 4
_STATE_MASK = 0x3F


@dataclass(frozen=True)
class ObjectState:
    provenance: Provenance = Provenance.RUNTIME
    disposition: Disposition = Disposition.RETAIN
    cleanliness: Cleanliness = Cleanliness.DIRTY
    mobility: Mobility = Mobility.MOVABLE

    def pack(self) -> int:
        return (
            int(self.provenance) << _PROVENANCE_SHIFT
            | int(self.disposition) << _DISPOSITION_SHIFT
            | int(self.cleanliness) << _CLEANLINESS_SHIFT
            | int(self.mobility) << _MOBILITY_SHIFT
        )

    @classmethod
    def unpack(cls, flags: int) -> "ObjectState":
        if flags & ~_STATE_MASK:
            raise ValueError(f"object state has unknown flags 0x{flags:x}")
        try:
            state = cls(
                provenance=Provenance((flags >> _PROVENANCE_SHIFT) & 0x1),
                disposition=Disposition((flags >> _DISPOSITION_SHIFT) & 0x3),
                cleanliness=Cleanliness((flags >> _CLEANLINESS_SHIFT) & 0x1),
                mobility=Mobility((flags >> _MOBILITY_SHIFT) & 0x3),
            )
        except ValueError as error:
            raise ValueError(
                f"object state has contradictory/invalid flags 0x{flags:x}"
            ) from error
        return state

    def emitted(self) -> "ObjectState":
        return ObjectState(
            provenance=Provenance.IMAGE,
            disposition=Disposition.RETAIN,
            cleanliness=Cleanliness.CLEAN,
            mobility=self.mobility,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "provenance": self.provenance.name,
            "disposition": self.disposition.name,
            "cleanliness": self.cleanliness.name,
            "mobility": self.mobility.name,
        }

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "ObjectState":
        return cls(
            provenance=Provenance[value.get("provenance", "RUNTIME")],
            disposition=Disposition[value.get("disposition", "RETAIN")],
            cleanliness=Cleanliness[value.get("cleanliness", "DIRTY")],
            mobility=Mobility[value.get("mobility", "MOVABLE")],
        )
