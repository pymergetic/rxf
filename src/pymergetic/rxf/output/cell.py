"""Canonical 48-byte cells in the single global RXF heap image."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from pymergetic.rxf.model.state import ObjectState
from pymergetic.rxf.output.base import BaseRXFModel, Struct
from pymergetic.rxf.output.types import uint32_t, uint64_t
from pymergetic.rxf.schema import NODE_INVALID
from pymergetic.rxf.ty.align import align_up


class CellHeader(Struct):
    id: Annotated[int, uint64_t]
    type_id: Annotated[int, uint64_t]
    generation: Annotated[int, uint64_t] = Field(default=0)
    parent: Annotated[int, uint64_t] = Field(default=NODE_INVALID)
    payload_size: Annotated[int, uint64_t]
    flags: Annotated[int, uint32_t] = Field(default=0)
    reserved: Annotated[int, uint32_t] = Field(default=0)

    @property
    def state(self) -> ObjectState:
        return ObjectState.unpack(self.flags)

    def to_wire(self) -> bytes:
        ObjectState.unpack(self.flags)
        if self.reserved:
            raise ValueError("cell reserved field must be zero")
        return super().to_wire()

    @classmethod
    def from_wire(cls, data: bytes, offset: int = 0) -> CellHeader:
        try:
            header = super().from_wire(data, offset)
        except ValueError as error:
            raise ValueError("cell header is outside committed heap bytes") from error
        ObjectState.unpack(header.flags)
        if header.reserved:
            raise ValueError("cell reserved field must be zero")
        return header


CELL_HEADER_SIZE = CellHeader.wire_size()


class Cell(BaseRXFModel):
    offset: Annotated[int, uint64_t]
    align: int = Field(default=8, ge=1)
    header: CellHeader
    payload: bytes = b""

    def model_post_init(self, context: object) -> None:
        if len(self.payload) != self.header.payload_size:
            raise ValueError("cell payload length must equal payload_size")

    @property
    def end(self) -> int:
        return self.offset + CELL_HEADER_SIZE + len(self.payload)


class HeapImage(BaseRXFModel):
    """One heap blob; cell offsets and frontier are relative to its heap base."""

    image_size: Annotated[int, uint64_t] = Field(default=0)
    committed_size: Annotated[int, uint64_t] = Field(default=0)
    limit: int | None = Field(default=None, ge=0)
    frontier: Annotated[int, uint64_t] = Field(default=0)
    align: int = Field(default=8, ge=1)
    page_size: int = Field(default=4096, ge=1)
    cells: list[Cell] = Field(default_factory=list)

    def model_post_init(self, context: object) -> None:
        if self.align & (self.align - 1) or self.page_size & (self.page_size - 1):
            raise ValueError("heap alignment and page size must be powers of two")
        if self.image_size > self.committed_size:
            raise ValueError("image_size exceeds committed_size")
        if self.frontier > self.committed_size:
            raise ValueError("frontier exceeds committed_size")
        if self.limit is not None and self.committed_size > self.limit:
            raise ValueError("committed_size exceeds known heap limit")

    def allocate(
        self,
        *,
        node_id: int,
        type_id: int,
        payload: bytes,
        parent: int = NODE_INVALID,
        generation: int = 0,
        state: ObjectState | None = None,
    ) -> Cell:
        if state is None:
            state = ObjectState()
        if any(cell.header.id == node_id for cell in self.cells):
            raise ValueError(f"node {node_id} is already allocated")
        offset = align_up(self.frontier, self.align)
        end = offset + CELL_HEADER_SIZE + len(payload)
        if end > self.committed_size:
            raise MemoryError(
                f"global heap committed boundary exceeded: {end}>{self.committed_size}"
            )
        if self.limit is not None and end > self.limit:
            raise MemoryError(f"global heap known limit exceeded: {end}>{self.limit}")
        cell = Cell(
            offset=offset,
            align=self.align,
            header=CellHeader(
                id=node_id,
                type_id=type_id,
                generation=generation,
                parent=parent,
                payload_size=len(payload),
                flags=state.pack(),
            ),
            payload=bytes(payload),
        )
        self.cells.append(cell)
        self.cells.sort(key=lambda item: item.offset)
        self.frontier = end
        return cell

    def to_wire(self) -> bytes:
        if self.frontier > self.committed_size:
            raise ValueError("frontier exceeds committed_size")
        data = bytearray(self.frontier)
        previous_end = 0
        seen_ids: set[int] = set()
        for cell in sorted(self.cells, key=lambda item: item.offset):
            if cell.header.id in seen_ids:
                raise ValueError(f"duplicate cell id {cell.header.id}")
            seen_ids.add(cell.header.id)
            if cell.offset < previous_end or cell.end > self.frontier:
                raise ValueError("overlapping or out-of-bounds cells")
            data[cell.offset : cell.offset + CELL_HEADER_SIZE] = cell.header.to_wire()
            data[cell.offset + CELL_HEADER_SIZE : cell.end] = cell.payload
            previous_end = cell.end
        return bytes(data)

    @classmethod
    def from_wire(
        cls,
        data: bytes,
        *,
        image_size: int,
        committed_size: int,
        frontier: int,
        limit: int | None = None,
        align: int = 8,
        page_size: int = 4096,
    ) -> HeapImage:
        if frontier > len(data) or frontier > committed_size:
            raise ValueError("heap frontier exceeds stored or committed bytes")
        cells: list[Cell] = []
        seen_ids: set[int] = set()
        offset = 0
        while offset < frontier:
            offset = align_up(offset, align)
            if offset == frontier:
                break
            header = CellHeader.from_wire(data, offset)
            if header.id in seen_ids:
                raise ValueError(f"duplicate cell id {header.id}")
            seen_ids.add(header.id)
            end = offset + CELL_HEADER_SIZE + header.payload_size
            if end > frontier:
                raise ValueError(f"cell {header.id} exceeds global heap frontier")
            cells.append(
                Cell(
                    offset=offset,
                    align=align,
                    header=header,
                    payload=data[offset + CELL_HEADER_SIZE : end],
                )
            )
            offset = end
        return cls(
            image_size=image_size,
            committed_size=committed_size,
            limit=limit,
            frontier=frontier,
            align=align,
            page_size=page_size,
            cells=cells,
        )
