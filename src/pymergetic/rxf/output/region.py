"""Global heap metadata and globally addressed section spans."""

from enum import IntFlag
from typing import Annotated

from pydantic import Field

from pymergetic.rxf.output.base import BaseRXFModel
from pymergetic.rxf.output.types import uint32_t, uint64_t


class SectionPerm(IntFlag):
    R = 1
    W = 2
    X = 4


class SectionMap(IntFlag):
    DIRECT = 0x01
    COPY = 0x02
    RELOCATE = 0x04
    BIND = 0x08
    SELECT = 0x10


class HeapEntry(BaseRXFModel):
    base: Annotated[int, uint64_t] = Field(default=0)
    image_size: Annotated[int, uint64_t] = Field(default=0)
    committed_size: Annotated[int, uint64_t] = Field(default=0)
    frontier: Annotated[int, uint64_t] = Field(default=0)
    limit: int | None = Field(default=None, ge=0)
    align: Annotated[int, uint32_t] = Field(default=8)
    page_size: Annotated[int, uint32_t] = Field(default=4096)
    stored_size: Annotated[int, uint64_t] = Field(default=0)


class SectionSpan(BaseRXFModel):
    node_id: Annotated[int, uint64_t]
    name: str = Field(min_length=1)
    offset: Annotated[int, uint64_t] = Field(default=0)
    size: Annotated[int, uint64_t] = Field(default=0)
    map: SectionMap = SectionMap.DIRECT
    perm: SectionPerm = SectionPerm.R | SectionPerm.W
