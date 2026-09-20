"""Reference layout and invariant model for the transient native runtime ABI."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass


class NativeSlot(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("address", ctypes.c_uint64),
    ]


class NativeObjectEntry(ctypes.Structure):
    _fields_ = [
        ("id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("type_id", ctypes.c_uint64),
        ("payload", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("capacity", ctypes.c_uint64),
        ("state", ctypes.c_uint64),
        ("read_borrows", ctypes.c_uint64),
        ("pin_count", ctypes.c_uint64),
        ("owner_transaction", ctypes.c_uint64),
    ]


class NativeObjectHandle(ctypes.Structure):
    _fields_ = [
        ("object_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("offset", ctypes.c_uint64),
    ]


class NativeTransactionMember(ctypes.Structure):
    _fields_ = [
        ("identity", ctypes.c_uint64),
        ("object_id", ctypes.c_uint64),
        ("old_generation", ctypes.c_uint64),
        ("old_type_id", ctypes.c_uint64),
        ("old_payload", ctypes.c_uint64),
        ("old_size", ctypes.c_uint64),
        ("old_capacity", ctypes.c_uint64),
        ("old_state", ctypes.c_uint64),
        ("old_owner_transaction", ctypes.c_uint64),
    ]


class NativeTransaction(ctypes.Structure):
    _fields_ = [
        ("root_object_id", ctypes.c_uint64),
        ("old_frontier", ctypes.c_uint64),
        ("identity", ctypes.c_uint64),
        ("member_count", ctypes.c_uint64),
        ("active", ctypes.c_uint64),
        ("reserved", ctypes.c_uint64),
    ]


class NativeOptionU64(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_uint32),
        ("padding", ctypes.c_uint32),
        ("value", ctypes.c_uint64),
    ]


class NativeOptionRef(ctypes.Structure):
    _fields_ = [
        ("tag", ctypes.c_uint32),
        ("padding", ctypes.c_uint32),
        ("value", NativeObjectHandle),
    ]


class NativeHashHeader(ctypes.Structure):
    _fields_ = [
        ("bucket_storage", NativeObjectHandle),
        ("capacity", ctypes.c_uint64),
        ("count", ctypes.c_uint64),
        ("tombstone_count", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
    ]


class NativeHashMapBucket(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_uint32),
        ("padding", ctypes.c_uint32),
        ("stored_hash", ctypes.c_uint64),
        ("key", ctypes.c_uint64),
        ("value", NativeObjectHandle),
    ]


class NativeHashSetBucket(ctypes.Structure):
    _fields_ = [
        ("state", ctypes.c_uint32),
        ("padding", ctypes.c_uint32),
        ("stored_hash", ctypes.c_uint64),
        ("key", ctypes.c_uint64),
    ]


class NativeRuntimeContext(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint64),
        ("size", ctypes.c_uint64),
        ("function_count", ctypes.c_uint64),
        ("functions", ctypes.c_uint64),
        ("object_count", ctypes.c_uint64),
        ("objects", ctypes.c_uint64),
        ("capability_count", ctypes.c_uint64),
        ("capabilities", ctypes.c_uint64),
        ("heap_base", ctypes.c_uint64),
        ("heap_committed", ctypes.c_uint64),
        ("heap_limit", ctypes.c_uint64),
        ("heap_frontier", ctypes.c_uint64),
        ("journal_count", ctypes.c_uint64),
        ("journal_capacity", ctypes.c_uint64),
        ("journal", ctypes.c_uint64),
        ("cleanup_count", ctypes.c_uint64),
        ("cleanup_capacity", ctypes.c_uint64),
        ("cleanup", ctypes.c_uint64),
    ]


RUNTIME_CONTEXT_OFFSETS = {
    name: getattr(NativeRuntimeContext, name).offset
    for field in NativeRuntimeContext._fields_
    for name in (field[0],)
}


@dataclass
class FunctionBinding:
    function_id: int
    generation: int
    address: int


class RuntimeDirectory:
    """Atomic transient slot rebinding keyed only by durable Function IDs."""

    def __init__(self):
        self.version = 0
        self._functions: dict[int, FunctionBinding] = {}

    def bind(self, function_id: int, address: int) -> FunctionBinding:
        if not 0 < function_id < 1 << 64 or not address:
            raise ValueError("invalid transient binding")
        old = self._functions.get(function_id)
        value = FunctionBinding(
            function_id, 1 if old is None else old.generation + 1, address
        )
        self._functions = {**self._functions, function_id: value}
        self.version += 1
        return value

    def resolve(self, function_id: int, generation: int) -> int:
        value = self._functions.get(function_id)
        if value is None or value.generation != generation:
            raise LookupError("unbound or stale FunctionSlot")
        return value.address
