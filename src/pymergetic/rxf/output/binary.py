"""The complete RXF v5 binary shape."""

from pydantic import Field

from pymergetic.rxf.output.base import BaseRXFModel
from pymergetic.rxf.output.cell import HeapImage
from pymergetic.rxf.output.code_table import CodeLocation
from pymergetic.rxf.output.header import BinaryHeader
from pymergetic.rxf.output.node import NodeEntry
from pymergetic.rxf.output.type_table import TypeLocation


class BinaryLayout(BaseRXFModel):
    header: BinaryHeader = Field(default_factory=BinaryHeader)
    nodes: list[NodeEntry] = Field(default_factory=list)
    type_table: list[TypeLocation] = Field(default_factory=list)
    code_table: list[CodeLocation] = Field(default_factory=list)
    heap: HeapImage = Field(default_factory=HeapImage)

    @property
    def total_size(self) -> int:
        from pymergetic.rxf.output.engine import pack_layout

        return len(pack_layout(self))
