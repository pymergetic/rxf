"""Derived TYPE ID to global heap offset index."""

from typing import Annotated

from pymergetic.rxf.output.base import Struct
from pymergetic.rxf.output.types import uint64_t


class TypeLocation(Struct):
    type_id: Annotated[int, uint64_t]
    cell_offset: Annotated[int, uint64_t]


TYPE_TABLE_ENTRY_SIZE = TypeLocation.wire_size()
