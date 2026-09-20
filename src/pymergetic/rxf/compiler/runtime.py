"""Transient, generation-checked binding slots keyed by durable uint64 IDs."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

UINT64_MAX = (1 << 64) - 1


class ResolverError(LookupError):
    def __init__(self, namespace: str, object_id: int):
        self.namespace = namespace
        self.object_id = object_id
        super().__init__(f"{namespace} slot {object_id} is unbound")


@dataclass(frozen=True)
class SlotValue[T]:
    object_id: int
    payload: T
    generation: int


class SlotTable[T]:
    """Copy-on-write sorted slots; readers observe one complete publication."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._slots: tuple[SlotValue[T], ...] = ()
        self._index: dict[int, int] = {}
        self._version = 0
        self._lock = RLock()

    @property
    def version(self) -> int:
        return self._version

    def publish(self, values: dict[int, T]) -> int:
        with self._lock:
            old = {slot.object_id: slot for slot in self._slots}
            slots = []
            for object_id, payload in sorted(values.items()):
                _uint64(object_id)
                prior = old.get(object_id)
                generation = (
                    1
                    if prior is None
                    else prior.generation
                    + int(payload is not prior.payload and payload != prior.payload)
                )
                slots.append(SlotValue(object_id, payload, generation))
            snapshot = tuple(slots)
            index = {slot.object_id: i for i, slot in enumerate(snapshot)}
            self._slots, self._index = snapshot, index
            self._version += 1
            return self._version

    def update(self, object_id: int, payload: T) -> SlotValue[T]:
        _uint64(object_id)
        with self._lock:
            values = {slot.object_id: slot.payload for slot in self._slots}
            values[object_id] = payload
            self.publish(values)
            return self.resolve(object_id)

    def resolve(self, object_id: int) -> SlotValue[T]:
        _uint64(object_id)
        snapshot, index = self._slots, self._index
        position = index.get(object_id)
        if position is None:
            raise ResolverError(self.namespace, object_id)
        return snapshot[position]

    def inspect(self) -> tuple[SlotValue[T], ...]:
        return self._slots


def _uint64(value: int) -> None:
    if isinstance(value, bool) or not 0 <= value <= UINT64_MAX:
        raise ValueError(f"persistent identity {value!r} is not uint64")


class FunctionSlot(SlotTable[object]):
    def __init__(self):
        super().__init__("function")


class CodeSlot(SlotTable[object]):
    def __init__(self):
        super().__init__("code")


class ObjectSlot(SlotTable[object]):
    def __init__(self):
        super().__init__("object")


class CapabilitySlot(SlotTable[object]):
    def __init__(self):
        super().__init__("capability")


@dataclass
class RuntimeContext:
    """Transient context. No address stored here is serialised into RXF."""

    functions: FunctionSlot
    codes: CodeSlot
    objects: ObjectSlot
    capabilities: CapabilitySlot

    @classmethod
    def empty(cls) -> RuntimeContext:
        return cls(FunctionSlot(), CodeSlot(), ObjectSlot(), CapabilitySlot())


# Stable 144-byte C ABI shared by both native targets. Tables are sorted by durable ID.
import ctypes


class NativeSlot(ctypes.Structure):
    _fields_ = [
        ("object_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("address", ctypes.c_uint64),
    ]


class NativeObjectEntry(ctypes.Structure):
    _fields_ = [
        ("object_id", ctypes.c_uint64),
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


@dataclass
class NativeContextOwner:
    context: NativeRuntimeContext
    function_array: object
    object_array: object
    capability_array: object
    journal_array: object


def native_context(context: RuntimeContext) -> NativeContextOwner:
    def address(payload: object) -> int:
        if isinstance(payload, int):
            return payload
        value = ctypes.cast(payload, ctypes.c_void_p).value  # pyright: ignore[reportArgumentType]
        return 0 if value is None else value

    def slot_array(slots: SlotTable[object]):
        values = tuple(slots.inspect())
        result = (NativeSlot * max(1, len(values)))()
        for index, slot in enumerate(values):
            result[index] = NativeSlot(
                slot.object_id, slot.generation, address(slot.payload)
            )
        return result, len(values)

    def object_array(slots: SlotTable[object]):
        values = tuple(slots.inspect())
        result = (NativeObjectEntry * max(1, len(values)))()
        for index, slot in enumerate(values):
            result[index] = NativeObjectEntry(
                slot.object_id,
                slot.generation,
                0,
                address(slot.payload),
                0,
                0,
                1,
                0,
                0,
                0,
            )
        return result, len(values)

    functions, function_count = slot_array(context.functions)
    objects, object_count = object_array(context.objects)
    capabilities, capability_count = slot_array(context.capabilities)
    journal = (NativeTransactionMember * max(1, object_count))()
    native = NativeRuntimeContext(
        2,
        ctypes.sizeof(NativeRuntimeContext),
        function_count,
        ctypes.addressof(functions),
        object_count,
        ctypes.addressof(objects),
        capability_count,
        ctypes.addressof(capabilities),
        0,
        0,
        0,
        0,
        0,
        object_count,
        ctypes.addressof(journal),
        0,
        0,
        0,
    )
    return NativeContextOwner(native, functions, objects, capabilities, journal)
