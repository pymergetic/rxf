"""Derived Function+Target to Code global heap lookup."""

from typing import Annotated

from pymergetic.rxf.output.base import Struct
from pymergetic.rxf.output.types import uint64_t


class CodeLocation(Struct):
    function_id: Annotated[int, uint64_t]
    target_id: Annotated[int, uint64_t]
    code_id: Annotated[int, uint64_t]
    cell_offset: Annotated[int, uint64_t]


CODE_TABLE_ENTRY_SIZE = CodeLocation.wire_size()
