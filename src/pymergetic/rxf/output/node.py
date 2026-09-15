"""RXF v3 node entries and uint64 references."""

from enum import IntEnum
from typing import Annotated

from pydantic import Field

from pymergetic.rxf.model.state import ObjectState, OwnerKind
from pymergetic.rxf.output.base import BaseRXFModel
from pymergetic.rxf.output.region import SectionSpan
from pymergetic.rxf.output.types import uint64_t
from pymergetic.rxf.schema import NODE_INVALID


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


class RefKind(IntEnum):
    CALL = 0
    DATA = 1
    ENTRY = 2
    TYPE = 3
    IMPORT = 4


class RefBinding(IntEnum):
    MANDATORY = 0
    OPTIONAL = 1


class RefEntry(BaseRXFModel):
    target: Annotated[int, uint64_t]
    to_off: Annotated[int, uint64_t] = Field(default=0)
    kind: int = Field(default=RefKind.DATA.value)
    binding: int = Field(default=RefBinding.MANDATORY.value)


class NodeEntry(BaseRXFModel):
    id: Annotated[int, uint64_t]
    name: str = Field(min_length=1)
    kind: NodeKind
    parent: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    type_id: Annotated[int, uint64_t] = Field(default=0)
    generation: Annotated[int, uint64_t] = Field(default=0)
    owner: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    owner_kind: OwnerKind = OwnerKind.SHARED
    state: ObjectState = Field(default_factory=ObjectState)
    ref_first: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    ref_count: Annotated[int, uint64_t] = Field(default=0)
    section_first: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    section_count: Annotated[int, uint64_t] = Field(default=0)
    attr_first: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    attr_count: Annotated[int, uint64_t] = Field(default=0)
    size: Annotated[int, uint64_t] = Field(default=0)
    sections: list[SectionSpan] = Field(default_factory=list)
    refs: list[RefEntry] = Field(default_factory=list)
    attrs: dict[str, object] = Field(default_factory=dict)
