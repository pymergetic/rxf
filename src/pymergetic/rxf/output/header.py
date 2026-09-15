"""RXF v5 binary header with a v4 inspection reader path."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from pymergetic.rxf.output.base import Struct
from pymergetic.rxf.output.types import uint32_t, uint64_t

MAGIC = b"RXFB"
FORMAT_VERSION = 5
HEADER_CAPACITY = 256
UNKNOWN_LIMIT = uint64_t.max_value()


class BinaryHeader(Struct):
    __align__ = 8

    magic: bytes = Field(default=MAGIC, min_length=4, max_length=4)
    version: Annotated[int, uint32_t] = Field(default=FORMAT_VERSION)
    header_size: Annotated[int, uint32_t] = Field(default=0)
    header_capacity: Annotated[int, uint32_t] = Field(default=HEADER_CAPACITY)
    heap_off: Annotated[int, uint64_t] = Field(default=0)
    image_size: Annotated[int, uint64_t] = Field(default=0)
    committed_size: Annotated[int, uint64_t] = Field(default=0)
    heap_limit: Annotated[int, uint64_t] = Field(default=UNKNOWN_LIMIT)
    frontier: Annotated[int, uint64_t] = Field(default=0)
    heap_align: Annotated[int, uint32_t] = Field(default=8)
    page_size: Annotated[int, uint32_t] = Field(default=4096)
    node_count: Annotated[int, uint64_t] = Field(default=0)
    node_table_off: Annotated[int, uint64_t] = Field(default=0)
    string_table_off: Annotated[int, uint64_t] = Field(default=0)
    string_table_size: Annotated[int, uint64_t] = Field(default=0)
    entry_node: Annotated[int, uint64_t] = Field(default=0)
    flags: Annotated[int, uint32_t] = Field(default=0)
    reserved: Annotated[int, uint32_t] = Field(default=0)
    ref_table_off: Annotated[int, uint64_t] = Field(default=0)
    ref_table_count: Annotated[int, uint64_t] = Field(default=0)
    section_table_off: Annotated[int, uint64_t] = Field(default=0)
    section_table_count: Annotated[int, uint64_t] = Field(default=0)
    attr_table_off: Annotated[int, uint64_t] = Field(default=0)
    attr_table_size: Annotated[int, uint64_t] = Field(default=0)
    type_table_off: Annotated[int, uint64_t] = Field(default=0)
    type_table_count: Annotated[int, uint64_t] = Field(default=0)
    code_table_off: Annotated[int, uint64_t] = Field(default=0)
    code_table_count: Annotated[int, uint64_t] = Field(default=0)

    def _validate_header_geometry(self) -> None:
        if self.version != FORMAT_VERSION:
            return
        expected_size = type(self).wire_size()
        if self.header_size != expected_size:
            raise ValueError(
                f"header_size must equal the v5 schema size {expected_size}"
            )
        if self.header_capacity < self.header_size:
            raise ValueError("header_capacity is smaller than header_size")
        if self.header_capacity % type(self).__align__:
            raise ValueError("header_capacity is not header-aligned")

    @model_validator(mode="after")
    def _default_and_validate_header_size(self) -> Self:
        if self.header_size == 0:
            self.header_size = type(self).wire_size()
        self._validate_header_geometry()
        return self

    def to_wire(self) -> bytes:
        self._validate_header_geometry()
        return super().to_wire()

    @property
    def is_rxfb(self) -> bool:
        return self.magic == MAGIC


HEADER_SIZE = BinaryHeader.wire_size()

if HEADER_CAPACITY < HEADER_SIZE or HEADER_CAPACITY % BinaryHeader.__align__:
    raise AssertionError("HEADER_CAPACITY must fit and align BinaryHeader")
