"""Reference semantics for the global movable RXF heap directory.

This module proves allocator/borrow transitions. It does not execute RXF Functions.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum

from pymergetic.rxf.model.stdlib import ObjectHandle, aligned_frontier


class HeapRefusal(IntEnum):
    OUT_OF_MEMORY = 1
    UNKNOWN_OBJECT = 2
    STALE_GENERATION = 3
    BORROW_CONFLICT = 4
    PINNED = 5
    OUT_OF_BOUNDS = 6


class HeapOperationRefused(RuntimeError):
    def __init__(self, refusal: HeapRefusal):
        super().__init__(refusal.name.lower())
        self.refusal = refusal


@dataclass(frozen=True)
class DirectorySlot:
    object_id: int
    generation: int
    offset: int
    size: int
    alignment: int
    pins: int = 0
    immutable_borrows: int = 0
    mutable_borrow: bool = False

    @property
    def immovable(self) -> bool:
        return bool(self.pins or self.immutable_borrows or self.mutable_borrow)


@dataclass(frozen=True)
class BorrowToken:
    handle: ObjectHandle
    mutable: bool


class GlobalHeapDirectory:
    """Atomic publish model over one byte heap and stable uint64 object IDs."""

    def __init__(self, committed: int):
        if committed < 0:
            raise ValueError("committed size must be nonnegative")
        self.committed = committed
        self.frontier = 0
        self._storage = bytearray(committed)
        self._slots: dict[int, DirectorySlot] = {}
        self._next_id = 1
        self._generations: dict[int, int] = {}

    @property
    def slots(self) -> tuple[DirectorySlot, ...]:
        return tuple(self._slots[key] for key in sorted(self._slots))

    def allocate(self, size: int, alignment: int, initial: bytes = b"") -> ObjectHandle:
        old_frontier, old_next = self.frontier, self._next_id
        try:
            offset, frontier = aligned_frontier(
                old_frontier, size, alignment, self.committed
            )
        except MemoryError as error:
            raise HeapOperationRefused(HeapRefusal.OUT_OF_MEMORY) from error
        if len(initial) > size:
            raise ValueError("initial payload exceeds allocation")
        object_id = old_next
        generation = self._generations.get(object_id, 0) + 1
        # Prepare bytes first; publish directory/frontier only after all checks succeed.
        prepared = bytes(initial) + bytes(size - len(initial))
        self._storage[offset : offset + size] = prepared
        self._slots[object_id] = DirectorySlot(
            object_id, generation, offset, size, alignment
        )
        self._generations[object_id] = generation
        self.frontier, self._next_id = frontier, object_id + 1
        return ObjectHandle(object_id, generation, 0)

    def resolve(
        self, handle: ObjectHandle, length: int = 0
    ) -> tuple[DirectorySlot, int]:
        slot = self._slots.get(handle.object_id)
        if slot is None:
            raise HeapOperationRefused(HeapRefusal.UNKNOWN_OBJECT)
        if slot.generation != handle.generation:
            raise HeapOperationRefused(HeapRefusal.STALE_GENERATION)
        if handle.offset > slot.size or length > slot.size - handle.offset:
            raise HeapOperationRefused(HeapRefusal.OUT_OF_BOUNDS)
        return slot, slot.offset + handle.offset

    def read(self, handle: ObjectHandle, length: int) -> bytes:
        _slot, address = self.resolve(handle, length)
        return bytes(self._storage[address : address + length])

    def borrow(self, handle: ObjectHandle, mutable: bool = False) -> BorrowToken:
        slot, _ = self.resolve(handle)
        if mutable and (slot.mutable_borrow or slot.immutable_borrows):
            raise HeapOperationRefused(HeapRefusal.BORROW_CONFLICT)
        if not mutable and slot.mutable_borrow:
            raise HeapOperationRefused(HeapRefusal.BORROW_CONFLICT)
        updated = (
            replace(slot, mutable_borrow=True)
            if mutable
            else replace(slot, immutable_borrows=slot.immutable_borrows + 1)
        )
        self._slots[slot.object_id] = updated
        return BorrowToken(handle, mutable)

    def release_borrow(self, token: BorrowToken) -> None:
        slot, _ = self.resolve(token.handle)
        if token.mutable:
            if not slot.mutable_borrow:
                raise ValueError("mutable borrow is not active")
            slot = replace(slot, mutable_borrow=False)
        else:
            if not slot.immutable_borrows:
                raise ValueError("immutable borrow is not active")
            slot = replace(slot, immutable_borrows=slot.immutable_borrows - 1)
        self._slots[slot.object_id] = slot

    def pin(self, handle: ObjectHandle) -> None:
        slot, _ = self.resolve(handle)
        self._slots[slot.object_id] = replace(slot, pins=slot.pins + 1)

    def unpin(self, handle: ObjectHandle) -> None:
        slot, _ = self.resolve(handle)
        if not slot.pins:
            raise ValueError("object is not pinned")
        self._slots[slot.object_id] = replace(slot, pins=slot.pins - 1)

    def release(self, handle: ObjectHandle) -> None:
        slot, _ = self.resolve(handle)
        if slot.immovable:
            raise HeapOperationRefused(HeapRefusal.BORROW_CONFLICT)
        del self._slots[slot.object_id]

    def compact(self) -> None:
        cursor = 0
        prepared: list[tuple[DirectorySlot, int, bytes]] = []
        for slot in self.slots:
            destination, cursor = aligned_frontier(
                cursor, slot.size, slot.alignment, self.committed
            )
            if slot.immovable and destination != slot.offset:
                raise HeapOperationRefused(HeapRefusal.PINNED)
            prepared.append(
                (
                    slot,
                    destination,
                    bytes(self._storage[slot.offset : slot.offset + slot.size]),
                )
            )
        new_storage = bytearray(self.committed)
        new_slots = dict(self._slots)
        for slot, destination, payload in prepared:
            new_storage[destination : destination + slot.size] = payload
            new_slots[slot.object_id] = replace(slot, offset=destination)
        self._storage, self._slots, self.frontier = new_storage, new_slots, cursor

    def resize(self, handle: ObjectHandle, new_size: int) -> ObjectHandle:
        slot, _ = self.resolve(handle)
        if slot.immovable:
            raise HeapOperationRefused(HeapRefusal.PINNED)
        snapshot = (self.frontier, bytes(self._storage), dict(self._slots))
        try:
            destination, frontier = aligned_frontier(
                self.frontier, new_size, slot.alignment, self.committed
            )
        except MemoryError as error:
            raise HeapOperationRefused(HeapRefusal.OUT_OF_MEMORY) from error
        payload = bytes(
            self._storage[slot.offset : slot.offset + min(slot.size, new_size)]
        )
        try:
            self._storage[destination : destination + new_size] = payload + bytes(
                new_size - len(payload)
            )
            generation = slot.generation + 1
            self._slots[slot.object_id] = replace(
                slot, generation=generation, offset=destination, size=new_size
            )
            self._generations[slot.object_id] = generation
            self.frontier = frontier
            return ObjectHandle(slot.object_id, generation, 0)
        except Exception:
            self.frontier, storage, self._slots = snapshot
            self._storage[:] = storage
            raise
