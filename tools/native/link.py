"""Links the starter into one memory image that is already allocated.

Nothing is built when it is loaded: the bytes are mapped at ``base`` (or, for
wasm32, written into linear memory there) and the first 144 bytes are the
runtime context. The image is

    context | journal | function slots | object entries | arena | heap | scratch

With a ``LiveSpec`` the arena has headroom behind it and the image ends in the live block:

    ... | scratch | area | capability table | log | stage | work | emit | rx | tx

``area`` is the ``pymergetic_rxf_live_area`` of ``core/live/live.h`` and the capability table
(words 6 and 7 of the context) holds one entry, the Channel, that points at it.

The arena is the RXFB v6 image as ``Image.to_bytes()`` makes it, byte for byte,
and so are the Code cells in it: the machine code of the leaves and the
interpreter is where the arena has it, and a function slot points there. The
object entries are the runtime view of the arena: one per live object, sorted by
id, with the payload address of the cell. They are derived and recomputed on
every link, never edited.

An object is found by an id that no delta holds a handle of: the ``object_id``
of a ``SemanticObject``, else the first 8 bytes of the SHA-256 of the path. The
value of an object is the variable part of its payload when its type is
variable (a CodeBlob is the bytes of its codes, a semantic object its data), else all of it.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

import graph_header
from compose import object_id

from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live.harness import table_slots

HANDLE_NONE = (1 << 64) - 1
CONTEXT_BYTES = 144
CONTEXT_VERSION = 2
SLOT_BYTES = 24
ENTRY_BYTES = 80
MEMBER_BYTES = 72
CELL_BYTES = 64
LIVE = 1
JOURNAL_CAPACITY = 8
SPARE = 8  # free entries, ids 1..SPARE, for what a program allocates
HEAP_BYTES = 0x10000
SCRATCH_BYTES = 0x1000  # arguments, output and the frame of a host that runs a function
ARGS_AT, OUT_AT, FRAME_AT = 0, 0x100, 0x400
ALIGN = 64
RUN = "pymergetic.rxf.foundation.graph.run"
MACHINES = {"x86_64": "x86_64_linux", "aarch64": "aarch64_linux", "wasm32": "wasm32"}
CODE_ENTRY_OFFSET = 40  # Code.entry_offset
CAPABILITY_BYTES = 24
CAPABILITY_CHANNEL = graph_header.numbers()["RXF_CAPABILITY_CHANNEL"]
CAPABILITY_UART = graph_header.numbers()["RXF_CAPABILITY_UART"]
UART_FIFO_BLOCK = graph_header.numbers()["RXF_UART_FIFO_BLOCK"]
AREA_BYTES = 272
PAGE = 4096
LIVE_MAGIC = 0x4556494C
LIVE_VERSION = 1
# offsets in the live area (core/live/live.h, pinned there by static asserts)
AREA_FIELDS = (
    ("arena", 8),
    ("log", 24),
    ("stage", 40),
    ("work", 56),
    ("emit", 72),
    ("rx", 88),
    ("tx", 104),
)
AREA_TABLE_SLOTS = 136
AREA_COMMITTED = 224
AREA_ENTRIES = 248  # u32 entries_capacity, u32 machine
MACHINE_IDS = {"x86_64": 1, "aarch64": 2, "wasm32": 3}


class LinkError(Exception):
    pass


@dataclass(frozen=True)
class LiveSpec:
    """The memory around the arena of a live image, in bytes."""

    headroom: int = 1 << 20  # the arena can grow by this much
    log: int = 1 << 18
    stage: int = 1 << 18
    scratch: int = 2 << 20  # work memory of a request, behind the lookup table
    ring: int = 1 << 14  # each of the two rings; a power of two
    entries: int = 4096  # object entries that an edit may add
    committed: int | None = None  # bytes of the arena region in use; None: all of it
    # The base of a UART (I/O port on x86_64, address on aarch64): a second capability. On wasm32
    # any value asks for the block of two FIFOs in linear memory (region "uart"), which the host
    # serves.
    uart: int | None = None
    # What the device is (the generation word of the capability, native/graph.h): 1 is the one of
    # the machine (the 16550 on ports on x86_64, the PL011 in memory on aarch64).
    uart_device: int = 1


@dataclass(frozen=True)
class LiveBlock:
    """Where the regions of the live block are, as addresses."""

    area: int
    capabilities: int
    regions: dict[str, tuple[int, int]]  # name -> (address, capacity)


def word(text: str | None) -> str:
    return (text or "").rsplit(".", 1)[-1]


@dataclass
class Linked:
    """A linked image: its bytes and where things are, relative to the base."""

    data: bytes
    base: int
    machine: str
    ids: dict[str, int]  # path of an object to its id
    run: int | None  # address of the interpreter, None on wasm32 (a table index)
    slots: dict[int, int] = field(default_factory=dict)  # leaf id to address
    context: int = 0
    scratch: int = 0  # SCRATCH_BYTES of free memory behind the heap
    table: tuple[int, int] = (0, 0)  # the function slots, which are a machine's own
    live: LiveBlock | None = None

    @property
    def args(self) -> int:
        return self.scratch + ARGS_AT

    @property
    def out(self) -> int:
        return self.scratch + OUT_AT

    @property
    def frame(self) -> int:
        return self.scratch + FRAME_AT

    def compared(self) -> list[tuple[int, int]]:
        """What must be equal after a run on any machine: all but the slot table and the
        frame and handle copies of a host, which are the host's own."""
        frame = self.scratch - self.base + FRAME_AT
        return [(0, self.table[0]), (self.table[1], frame)]

    def handle(self, path: str, generation: int = 1, offset: int = 0) -> bytes:
        """The by-value handle of an object, as the interpreter takes it."""
        return struct.pack("<3Q", self.ids[path], generation, offset)


def arena_cells(arena: bytes) -> list[int]:
    """The cell offset of every handle of an arena, or -1 for a free slot."""
    index_off, _ = struct.unpack_from("<QQ", arena, 48)
    chunks, handles = struct.unpack_from("<QQ", arena, 80)
    directory = struct.unpack_from(f"<{chunks}Q", arena, index_off)
    found: list[int] = []
    for handle in range(handles):
        chunk, slot = divmod(handle, graph_chunk())
        (entry,) = struct.unpack_from(
            "<Q", arena, directory[chunk] + CELL_BYTES + 8 * slot
        )
        found.append(-1 if entry & 1 else entry)
    return found


def graph_chunk() -> int:
    return 4096


def link(image: Image, machine: str, base: int, live: LiveSpec | None = None) -> Linked:
    """The image as it sits in memory at ``base`` for one machine."""
    if machine not in MACHINES:
        raise LinkError(f"no machine {machine}")
    arena = image.to_bytes()
    cells = arena_cells(arena)
    prefixes: dict[int, int] = {}

    def prefix(handle: int) -> int:
        """Where the value of an object starts in its payload: after the fixed part of a variable type."""
        type_handle = image.get(handle)["type"]
        if type_handle not in prefixes:
            values = dict(image.fields(type_handle))
            variable = "Variable" in values.get("flags", "")
            prefixes[type_handle] = (
                image.layout(type_handle)["prefix"] if variable else 0
            )
        return prefixes[type_handle]

    # The entries: where the value of each live object is, in the arena
    rows: list[tuple[int, int, int, int, str]] = []  # id, type id, offset, size, path
    for handle, cell in enumerate(cells):
        if cell < 0 or not image.live(handle):
            continue
        info = image.get(handle)
        if info["kind"] in (254, 255) or info["type"] == HANDLE_NONE:
            continue  # a block or dead cell, or an object that has no type and no value
        offset = prefix(handle)
        size = len(info["payload"]) - offset
        fields = (
            dict(image.fields(handle))
            if image.type_name(handle) == "SemanticObject"
            else {}
        )
        type_path = image.path(info["type"])
        type_id = int.from_bytes(
            hashlib.sha256(type_path.encode()).digest()[:8], "little"
        )
        rows.append(
            (
                object_id(image, handle, fields),
                type_id,
                cell + CELL_BYTES + offset,
                size,
                image.path(handle),
            )
        )
    rows.sort()
    ids = [row[0] for row in rows]
    if len(set(ids)) != len(ids) or any(i <= SPARE for i in ids):
        raise LinkError(
            "two objects share an id, or one takes an id of the spare entries"
        )

    # The places
    leaf_ids = graph_header.leaves()
    journal = CONTEXT_BYTES
    slots_at = journal + JOURNAL_CAPACITY * MEMBER_BYTES
    count_slots = len(leaf_ids)
    entries_at = slots_at + count_slots * SLOT_BYTES
    entries_at = (entries_at + ALIGN - 1) // ALIGN * ALIGN
    count_entries = SPARE + len(rows)
    entries_room = count_entries + (live.entries if live else 0)
    arena_at = (entries_at + entries_room * ENTRY_BYTES + ALIGN - 1) // ALIGN * ALIGN
    arena_room = len(arena)
    if live:
        arena_room = (arena_room + live.headroom + PAGE - 1) // PAGE * PAGE
    heap_at = (arena_at + arena_room + ALIGN - 1) // ALIGN * ALIGN
    scratch_at = heap_at + HEAP_BYTES
    total = scratch_at + SCRATCH_BYTES
    block_at: dict[str, tuple[int, int]] = {}
    area_at = capabilities_at = 0
    table_size = 0
    if live:
        table_size = table_slots(arena)
        sizes = {
            "arena": arena_room,
            "log": live.log,
            "stage": live.stage,
            "work": table_size * 4 + live.scratch,
            "emit": arena_room,
            "rx": live.ring,
            "tx": live.ring,
        }
        total = (total + PAGE - 1) // PAGE * PAGE
        area_at = total
        capabilities_at = area_at + AREA_BYTES
        total = capabilities_at + (2 * CAPABILITY_BYTES + ALIGN - 1) // ALIGN * ALIGN
        for name, size in sizes.items():
            if name == "arena":
                block_at[name] = (arena_at, size)
                continue
            total = (total + PAGE - 1) // PAGE * PAGE
            block_at[name] = (total, size)
            total += size
        if machine == "wasm32" and live.uart is not None:
            total = (total + PAGE - 1) // PAGE * PAGE
            block_at["uart"] = (total, UART_FIFO_BLOCK)
            total += UART_FIFO_BLOCK

    paths = {row[4]: row[0] for row in rows}
    code_of = _code(image, machine, cells)

    def address(path: str) -> int:
        handle = image.resolve(path)
        if handle is None or handle not in code_of:
            raise LinkError(f"{path} has no code for {machine}")
        return base + arena_at + code_of[handle]

    # Function slots: wasm32 calls through table 0, whose index is the id
    run = None if machine == "wasm32" else address(RUN)
    slots_by_leaf: dict[int, int] = {}
    slot_bytes = bytearray()
    for key, identifier in sorted(leaf_ids.items(), key=lambda item: item[1]):
        target = _leaf(image, identifier)
        slots_by_leaf[identifier] = (
            identifier if machine == "wasm32" else address(target)
        )
        slot_bytes += struct.pack("<3Q", identifier, 1, slots_by_leaf[identifier])
    entry_bytes = bytearray()
    for spare in range(1, SPARE + 1):
        entry_bytes += struct.pack("<10Q", spare, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    for identifier, type_id, where, size, _ in rows:
        entry_bytes += struct.pack(
            "<10Q",
            identifier,
            1,
            type_id,
            base + arena_at + where,
            size,
            size,
            LIVE,
            0,
            0,
            0,
        )
    memory = bytearray(total)
    capability_count = 1 + (live.uart is not None) if live else 0
    context = struct.pack(
        "<18Q",
        CONTEXT_VERSION, CONTEXT_BYTES,
        count_slots, base + slots_at,
        count_entries, base + entries_at,
        capability_count, base + capabilities_at if live else 0,
        base + heap_at, HEAP_BYTES, HEAP_BYTES, 0,
        0, JOURNAL_CAPACITY, base + journal,
        0, 0, 0,
    )  # fmt: skip
    memory[:CONTEXT_BYTES] = context
    memory[slots_at : slots_at + len(slot_bytes)] = slot_bytes
    memory[entries_at : entries_at + len(entry_bytes)] = entry_bytes
    memory[arena_at : arena_at + len(arena)] = arena
    table = (slots_at, slots_at + len(slot_bytes))
    block = None
    if live:
        area = bytearray(AREA_BYTES)
        struct.pack_into("<II", area, 0, LIVE_MAGIC, LIVE_VERSION)
        for name, offset in AREA_FIELDS:
            where, size = block_at[name]
            struct.pack_into("<QQ", area, offset, base + where, size)
        struct.pack_into("<I", area, AREA_TABLE_SLOTS, table_size)
        if live.committed is not None:
            if not len(arena) <= live.committed <= arena_room:
                raise LinkError("committed is between the arena and its region")
            struct.pack_into("<Q", area, AREA_COMMITTED, live.committed)
        struct.pack_into("<II", area, AREA_ENTRIES, entries_room, MACHINE_IDS[machine])
        memory[area_at : area_at + AREA_BYTES] = area
        table_bytes = struct.pack("<3Q", CAPABILITY_CHANNEL, 1, base + area_at)
        if live.uart is not None:
            device = base + block_at["uart"][0] if "uart" in block_at else live.uart
            table_bytes += struct.pack("<3Q", CAPABILITY_UART, live.uart_device, device)
        memory[capabilities_at : capabilities_at + len(table_bytes)] = table_bytes
        block = LiveBlock(
            base + area_at,
            base + capabilities_at,
            {n: (base + w, size) for n, (w, size) in block_at.items()},
        )
    return Linked(
        bytes(memory),
        base,
        machine,
        paths,
        run,
        slots_by_leaf,
        base,
        base + scratch_at,
        table,
        block,
    )


def _leaf(image: Image, identifier: int) -> str:
    """The path of the Function that is the leaf with this id."""
    run = image.resolve(RUN)
    assert run is not None
    for target, role, index, _, _ in image.get(run)["refs"]:
        if (
            index == identifier
            and role is not None
            and word(image.path(role)) == "Leaf"
        ):
            return image.path(target)
    raise LinkError(f"{RUN} has no leaf {identifier}")


def _code(image: Image, machine: str, cells: list[int]) -> dict[int, int]:
    """For each Function with code for the machine, the arena offset of its first byte."""
    found: dict[int, int] = {}
    wanted = MACHINES[machine]
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Code":
            continue
        if image.path(handle).rsplit(".", 1)[-1] != wanted:
            continue
        info = image.get(handle)
        payload = info["payload"]
        (entry,) = struct.unpack_from("<Q", payload, CODE_ENTRY_OFFSET)
        # The bytes live in the CodeBlob the code's body names: the entry is an
        # offset into that blob's value, not the (now empty) code payload.
        body = image.resolve(dict(image.fields(handle)).get("body", ""))
        if body is None or image.type_name(body) != "CodeBlob":
            raise LinkError(f"{image.path(handle)} says no blob for its bytes")
        blob = image.get(body)
        blob_at = cells[body] + CELL_BYTES + _blob_prefix(image, blob["type"])
        found[info["parent"]] = blob_at + entry
    return found


def _blob_prefix(image: Image, type_handle: int) -> int:
    """Where the bytes of a CodeBlob start in its payload: after the fixed part."""
    return image.layout(type_handle)["prefix"]
