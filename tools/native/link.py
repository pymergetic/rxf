"""Links the starter into one memory image that is already allocated.

Nothing is built when it is loaded: the bytes are mapped at ``base`` (or, for
wasm32, written into linear memory there) and the first 144 bytes are the
runtime context. The image is

    context | journal | function slots | object entries | arena | heap | scratch

With a ``LiveSpec`` the arena has headroom behind it and the image ends in the live block:

    ... | scratch | area | capability table | log | stage | work | emit | rx | tx

``area`` is the ``pymergetic_rxf_live_area`` of ``core/live/live.h`` and the capability table
(words 6 and 7 of the context) holds one entry, the Channel, that points at it.

The arena is the v7 one-arena image (docs/32 1) that ``tools/native/arena_export.py``
makes from the world, byte for byte, and so are the Code cells in it: the machine
code of the leaves and the interpreter is where the arena has it, and a function
slot points there. The object entries are the runtime view of the arena: one per
live object, sorted by id, with the address of the object's value in the arena -
past the record's payload head (a module owns its chain head first) and the type's
fixed part. They are derived and recomputed on every link, never edited.

An object is found by an id that no delta holds a handle of: the ``object_id``
of a ``SemanticObject``, else the first 8 bytes of the SHA-256 of the path. The
value of an object is the variable part of its payload when its type is
variable (a CodeBlob is the bytes of its codes, a semantic object its data), else all of it.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

import arena_export
import graph_header
import programs
from compose import object_id

from pymergetic.rxftk.core.arena import (
    MODULE_SIZE,
    OBJ_MODULE,
    OBJECT_HEADER_SIZE,
    SUPERBLOCK_SIZE,
    ObjectArena,
)
from pymergetic.rxftk.core.object import Image

HANDLE_NONE = (1 << 64) - 1
CONTEXT_BYTES = 144
CONTEXT_VERSION = 2
SLOT_BYTES = 24
ENTRY_BYTES = 80
MEMBER_BYTES = 72
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
CAPABILITY_ALLOCATOR = graph_header.numbers()["RXF_CAPABILITY_ALLOCATOR"]
ALLOCATOR_CELL_BYTES = graph_header.numbers()["RXF_ALLOCATOR_CELL_BYTES"]
POOL_FIRST = 1 << 20  # the id of the first free row of the allocator pool (docs/23 4.9)
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
    # The rows of the allocator pool (docs/23 4.9): that many free rows of the entry table, ids
    # from POOL_FIRST on, and the Allocator capability that names them (the next capability after
    # the channel and the uart). 0 binds none: the leaf runtime_allocate answers UNBOUND, which is
    # what a seat that runs no dynamic guest wants.
    pool: int = 0


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


def table_slots(arena: bytes, headroom: int = 4) -> int:
    """A lookup table for the handles a v7 arena can hold, twice over.

    The slot table is the first object of the arena at its fixed place (docs/32 1:
    the anchor a cold attach needs) and its head carries the count and capacity.
    """
    count, capacity = struct.unpack_from(
        "<II", arena, SUPERBLOCK_SIZE + OBJECT_HEADER_SIZE
    )
    assert count <= capacity, "the slot table never publishes past its capacity"
    want = 2 * capacity * headroom // 4
    slots = 1024
    while slots < want:
        slots *= 2
    return slots


def arena_of(image: Image) -> bytes:
    """The arena of a linked image: the v7 one-arena image (docs/32 1) that `link`
    places at its region, byte for byte. The host build serves these same bytes, so
    a parity test compares like with like."""
    return arena_export.place_world(programs.filled(image))[0].to_bytes()


def arena_map(image: Image) -> dict[int, int]:
    """Where each world object landed in that arena: world handle -> arena handle32.

    A handle is image-local (docs/32 1.2), so a reader that holds the world's
    handle needs this to name the same object in the arena. It is derived, not
    stored: the path is the identity across images."""
    _, placed = arena_export.place_world(programs.filled(image))
    return dict(placed)


def link(image: Image, machine: str, base: int, live: LiveSpec | None = None) -> Linked:
    """The image as it sits in memory at ``base`` for one machine."""
    if machine not in MACHINES:
        raise LinkError(f"no machine {machine}")
    image = programs.filled(image)  # the built code, from its recipe (Wave 4b step 2)
    made, placed = arena_export.place_world(image)  # the v7 arena (docs/32 1)
    arena = made.to_bytes()
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

    def value(handle: int) -> int:
        """Where an object's value sits in the arena: past the record's payload
        head (a module owns its chain head first) and the type's fixed part."""
        target = placed[handle]
        kind = made.load(target)[0]
        return made.locate(target)[1] + (MODULE_SIZE if kind == OBJ_MODULE else 0)

    # The entries: where the value of each live object is, in the arena
    rows: list[tuple[int, int, int, int, str]] = []  # id, type id, offset, size, path
    for handle in range(image.handle_count):
        if not image.live(handle):
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
                value(handle) + offset,
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
    # the slot table lists the leaves the image's graph.run wires: an image may wire fewer
    # leaves than the header names (docs/23 4.9, step 0), so the header grows ahead of a starter
    wired = _wired(image)
    leaf_ids = {
        key: identifier
        for key, identifier in graph_header.leaves().items()
        if identifier in wired
    }
    journal = CONTEXT_BYTES
    slots_at = journal + JOURNAL_CAPACITY * MEMBER_BYTES
    count_slots = len(leaf_ids)
    entries_at = slots_at + count_slots * SLOT_BYTES
    entries_at = (entries_at + ALIGN - 1) // ALIGN * ALIGN
    pool = live.pool if live else 0
    if any(POOL_FIRST <= i < POOL_FIRST + pool for i in ids):
        raise LinkError("an object takes an id of the allocator pool")
    count_entries = SPARE + len(rows) + pool
    entries_room = count_entries + (live.entries if live else 0)
    arena_at = (entries_at + entries_room * ENTRY_BYTES + ALIGN - 1) // ALIGN * ALIGN
    arena_room = len(arena)
    if live:
        arena_room = (arena_room + live.headroom + PAGE - 1) // PAGE * PAGE
    heap_at = (arena_at + arena_room + ALIGN - 1) // ALIGN * ALIGN
    scratch_at = heap_at + HEAP_BYTES
    total = scratch_at + SCRATCH_BYTES
    block_at: dict[str, tuple[int, int]] = {}
    area_at = capabilities_at = allocator_at = 0
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
        if pool:
            # a third capability and the allocator cell behind the table
            allocator_at = capabilities_at + 3 * CAPABILITY_BYTES
            total = allocator_at + (ALLOCATOR_CELL_BYTES + ALIGN - 1) // ALIGN * ALIGN
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
    code_of = _code(image, machine, made, placed)

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
    # the entry table is sorted by id: the spare rows, the objects of the image, the pool
    entries: dict[int, bytes] = {}
    for spare in range(1, SPARE + 1):
        entries[spare] = struct.pack("<10Q", spare, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    for identifier, type_id, where, size, _ in rows:
        entries[identifier] = struct.pack(
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
    for i in range(pool):
        entries[POOL_FIRST + i] = struct.pack("<10Q", POOL_FIRST + i, *[0] * 9)
    entry_bytes = bytearray(b"".join(entries[i] for i in sorted(entries)))
    memory = bytearray(total)
    capability_count = 1 + (live.uart is not None) + (pool > 0) if live else 0
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
        if pool:
            table_bytes += struct.pack(
                "<3Q", CAPABILITY_ALLOCATOR, 1, base + allocator_at
            )
            memory[allocator_at : allocator_at + ALLOCATOR_CELL_BYTES] = struct.pack(
                "<4Q", POOL_FIRST, pool, POOL_FIRST, 0
            )
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


def _wired(image: Image) -> set[int]:
    """The ids of the leaves the image's ``graph.run`` has a ``Leaf`` reference for."""
    run = image.resolve(RUN)
    assert run is not None
    return {
        index
        for _, role, index, _, _ in image.get(run)["refs"]
        if role is not None and word(image.path(role)) == "Leaf"
    }


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


def _code(
    image: Image, machine: str, made: ObjectArena, placed: dict[int, int]
) -> dict[int, int]:
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
        blob_at = made.locate(placed[body])[1] + _blob_prefix(image, blob["type"])
        found[info["parent"]] = blob_at + entry
    return found


def _blob_prefix(image: Image, type_handle: int) -> int:
    """Where the bytes of a CodeBlob start in its payload: after the fixed part."""
    return image.layout(type_handle)["prefix"]
