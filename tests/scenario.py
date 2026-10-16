"""One scripted run of the stdlib leaves: memory, runtime, collections, text.

The runtime leaves work on a context in memory (objects, journal, heap) that a
host sets up. This builds that memory and a list of calls on it, with the
refusals as well as the successes, so each machine can run the same script and
the memory it leaves behind can be compared byte for byte.
"""

import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import native_build
import sync

from .snippets import STARTER

SYMBOLS = native_build.MEMORY | native_build.RUNTIME

# The C parameters of each leaf, one letter each:
#   p pointer or context  q u64  d u32  b u8  H object handle (24 bytes by value)
SPEC = {
    "memory_copy": "pqpqq",
    "memory_move": "pqpqq",
    "memory_fill": "pqbq",
    "memory_zero": "pqq",
    "memory_compare": "pqpqqp",
    "load_u32": "pqqddp",
    "store_u32": "pqqddd",
    "utf8_validate": "pqp",
    "hash_bytes": "pqqp",
    "hash_u64": "qqp",
    "runtime_hash_map_lookup": "pHpqqp",
    "runtime_hash_set_lookup": "pHpqqp",
    "runtime_begin_private": "pHqp",
    "runtime_resolve": "pHqp",
    "runtime_resolve_typed": "pHpqqp",
    "runtime_allocate_prepare": "pqqqqqpp",
    "runtime_publish": "pp",
    "runtime_rollback": "pp",
    "runtime_release": "pH",
    "runtime_borrow": "pHq",
    "runtime_release_borrow": "pHq",
    "runtime_pin": "pH",
    "runtime_unpin": "pH",
    "runtime_read": "pHpqqp",
    "runtime_write": "pHpqqq",
    "runtime_reserve": "pHpq",
    "runtime_append": "pHpqq",
    "runtime_format_u32": "pHpd",
    "runtime_search": "pHqHqpp",
    "runtime_cleanup": "pp",
    "graph_run": "pHqqp",
}

BASE = 0x10000  # the image starts here, so the wasm stack (below) stays clear
SIZE = 0x5000  # and ends here; what a run leaves in it is what gets compared
COPIES = 0x16000  # by-value handles that a machine passes by address
CONTEXT = BASE
OBJECTS = BASE + 0x100
JOURNAL = BASE + 0x400
TX = BASE + 0x700
TX2 = BASE + 0x740
HEAP = BASE + 0x1000
HEAP_SIZE = 0x2000
SOURCE = BASE + 0x4000
HANDLE_OUT = BASE + 0x4200
WORD_OUT = BASE + 0x4300
OPTION_OUT = BASE + 0x4340
BUFFER_A = BASE + 0x4400
BUFFER_B = BASE + 0x4480
INT_OUT = BASE + 0x4500
UTF8_GOOD = BASE + 0x4600
UTF8_BAD = BASE + 0x4680
SET_HEADER = BASE + 0x4800
SET_BUCKETS = BASE + 0x4900
MAP_HEADER = BASE + 0x4A00
MAP_BUCKETS = BASE + 0x4B00
TEXT = b"hello world, and more text for the leaves to read."
GOOD = "héllo € 𐍈".encode()
BAD = b"\xc3\x28"
LIVE = 1
OBJECT_COUNT = 8
JOURNAL_CAPACITY = 4
HASH_SET, SET_BUCKET_TYPE = 60018, 41019
HASH_MAP, MAP_BUCKET_TYPE = 60017, 41018

Handle = tuple[int, int, int]  # object id, generation, offset
Argument = int | Handle


@dataclass(frozen=True)
class Step:
    symbol: str
    arguments: tuple[Argument, ...]


def entry(
    identity: int,
    generation: int = 0,
    type_id: int = 0,
    payload: int = 0,
    size: int = 0,
    capacity: int = 0,
    state: int = 0,
) -> bytes:
    return struct.pack(
        "<10Q", identity, generation, type_id, payload, size, capacity, state, 0, 0, 0
    )


def image() -> bytes:
    """The memory the run starts from."""
    memory = bytearray(SIZE)

    def put(address: int, data: bytes) -> None:
        memory[address - BASE : address - BASE + len(data)] = data

    put(
        CONTEXT,
        struct.pack(
            "<18Q",
            2, 144,  # version, size
            0, 0,  # functions
            OBJECT_COUNT, OBJECTS,
            0, 0,  # capabilities
            HEAP, HEAP_SIZE, HEAP_SIZE, 0,  # base, committed, limit, frontier
            0, JOURNAL_CAPACITY, JOURNAL,
            0, 0, 0,  # cleanup
        ),
    )  # fmt: skip
    objects = [
        entry(1),
        entry(2),
        entry(3),
        entry(4),
        entry(5, 1, HASH_SET, SET_HEADER, 56, 56, LIVE),
        entry(6, 1, SET_BUCKET_TYPE, SET_BUCKETS, 192, 192, LIVE),
        entry(7, 1, HASH_MAP, MAP_HEADER, 56, 56, LIVE),
        entry(8, 1, MAP_BUCKET_TYPE, MAP_BUCKETS, 384, 384, LIVE),
    ]
    put(OBJECTS, b"".join(objects))
    put(SOURCE, TEXT)
    put(BUFFER_A, bytes(range(1, 65)))
    put(UTF8_GOOD, GOOD)
    put(UTF8_BAD, BAD)
    put(SET_HEADER, struct.pack("<7Q", 6, 1, 0, 8, 2, 0, 0))
    put(MAP_HEADER, struct.pack("<7Q", 8, 1, 0, 8, 2, 0, 0))
    for slot, (stored, key) in {3: (3, 100), 4: (3, 200)}.items():
        put(SET_BUCKETS + slot * 24, struct.pack("<IIQQ", 1, 0, stored, key))
        put(
            MAP_BUCKETS + slot * 48,
            struct.pack("<IIQQ3Q", 1, 0, stored, key, 3, 1, slot * 16),
        )
    return bytes(memory)


def steps() -> list[Step]:
    first: Handle = (1, 1, 0)
    second: Handle = (2, 1, 0)
    word: Handle = (1, 1, 6)
    ctx = CONTEXT

    def step(symbol: str, *arguments: Argument) -> Step:
        assert len(arguments) == len(SPEC[symbol]), symbol
        return Step(symbol, arguments)

    return [
        # a transaction: allocate, fill, read, publish
        step("runtime_begin_private", ctx, (1, 0, 0), 7, TX),
        step("runtime_allocate_prepare", ctx, 1, 100, 0, 64, 8, TX, HANDLE_OUT),
        step("runtime_write", ctx, first, TX, 0, SOURCE, 5),
        step("runtime_append", ctx, first, TX, SOURCE + 5, 6),
        step("runtime_format_u32", ctx, first, TX, 4097),
        step("runtime_reserve", ctx, first, TX, 64),
        step("runtime_reserve", ctx, first, TX, 65),
        step("runtime_read", ctx, first, TX, 0, 5, WORD_OUT),
        step("runtime_read", ctx, first, TX, 0, 500, WORD_OUT),
        step("runtime_resolve", ctx, first, 4, WORD_OUT),
        step("runtime_publish", ctx, TX),
        step("runtime_publish", ctx, TX),
        step("runtime_resolve", ctx, first, 4, WORD_OUT),
        step("runtime_resolve_typed", ctx, first, 0, 100, 4, WORD_OUT),
        step("runtime_resolve_typed", ctx, first, 0, 99, 4, WORD_OUT),
        step("runtime_resolve", ctx, (1, 9, 0), 4, WORD_OUT),
        # borrows and pins
        step("runtime_borrow", ctx, first, 0),
        step("runtime_borrow", ctx, first, 1),
        step("runtime_release_borrow", ctx, first, 0),
        step("runtime_borrow", ctx, first, 1),
        step("runtime_borrow", ctx, first, 0),
        step("runtime_release_borrow", ctx, first, 1),
        step("runtime_release_borrow", ctx, first, 1),
        step("runtime_pin", ctx, first),
        step("runtime_release", ctx, first),
        step("runtime_unpin", ctx, first),
        step("runtime_unpin", ctx, first),
        # a second transaction that is rolled back, then one that is cleaned up
        step("runtime_begin_private", ctx, (2, 0, 0), 9, TX2),
        step("runtime_allocate_prepare", ctx, 2, 200, 3, 16, 16, TX2, HANDLE_OUT),
        step("runtime_write", ctx, second, TX2, 0, SOURCE + 12, 3),
        step("runtime_rollback", ctx, TX2),
        step("runtime_begin_private", ctx, (2, 0, 0), 9, TX2),
        step("runtime_allocate_prepare", ctx, 2, 200, 3, 16, 16, TX2, HANDLE_OUT),
        step("runtime_cleanup", ctx, TX2),
        step("runtime_cleanup", ctx, TX2),
        # search in the published object
        step("runtime_search", ctx, first, 15, word, 5, 0, WORD_OUT),
        step("runtime_search", ctx, first, 15, (1, 1, 0), 0, 0, WORD_OUT),
        step("runtime_search", ctx, first, 15, (1, 1, 0), 9, 0, WORD_OUT),
        step("runtime_release", ctx, first),
        step("runtime_release", ctx, first),
        # collections
        step("hash_u64", 5, 7, WORD_OUT),
        step("runtime_hash_set_lookup", ctx, (5, 1, 0), 0, 3, 200, OPTION_OUT),
        step("runtime_hash_set_lookup", ctx, (5, 1, 0), 0, 3, 300, OPTION_OUT),
        step("runtime_hash_set_lookup", ctx, (7, 1, 0), 0, 3, 200, OPTION_OUT),
        step("runtime_hash_map_lookup", ctx, (7, 1, 0), 0, 3, 100, OPTION_OUT),
        step("runtime_hash_map_lookup", ctx, (7, 1, 0), 0, 5, 100, OPTION_OUT),
        # memory
        step("memory_copy", BUFFER_B, 32, SOURCE, 32, 10),
        step("memory_copy", BUFFER_B, 4, SOURCE, 32, 10),
        step("memory_move", BUFFER_A + 2, 30, BUFFER_A, 30, 20),
        step("memory_move", BUFFER_A, 30, BUFFER_A + 2, 30, 20),
        step("memory_fill", BUFFER_B + 32, 32, 0x5A, 20),
        step("memory_zero", BUFFER_B + 32, 32, 5),
        step("memory_zero", BUFFER_B + 32, 3, 5),
        step("memory_compare", BUFFER_A, 10, BUFFER_B, 10, 10, INT_OUT),
        step("memory_compare", BUFFER_B, 10, BUFFER_B, 10, 10, INT_OUT),
        step("load_u32", SOURCE, 16, 4, 4, 1, INT_OUT),
        step("load_u32", SOURCE, 16, 4, 4, 2, INT_OUT),
        step("load_u32", SOURCE, 16, 3, 4, 1, INT_OUT),
        step("load_u32", SOURCE, 16, 14, 2, 1, INT_OUT),
        step("store_u32", BUFFER_B + 64, 16, 8, 4, 2, 0xDEADBEEF),
        step("store_u32", BUFFER_B + 64, 16, 0, 4, 1, 0x01020304),
        step("utf8_validate", UTF8_GOOD, len(GOOD), WORD_OUT),
        step("utf8_validate", UTF8_BAD, len(BAD), WORD_OUT),
        step("hash_bytes", SOURCE, 11, 3, WORD_OUT),
    ]  # fmt: skip


def stdlib_code() -> dict[str, dict[str, bytes]]:
    """The code of every stdlib leaf in the starter, per machine."""
    found: dict[str, dict[str, bytes]] = {}
    for path in sorted(STARTER.rglob("*.xml")):
        parts = path.relative_to(STARTER).with_suffix("").parts
        for function in ET.parse(path).getroot().iter("Function"):
            codes = {c.get("name"): c for c in function if c.tag == "Code"}
            if "wasm32" not in codes:
                continue
            try:
                key = sync.snippet_key(parts, str(function.get("name")), SYMBOLS)
            except sync.SyncError:
                continue
            found[key] = {
                machine: bytes.fromhex(codes[name].get(sync.DATA) or "")
                for machine, name in (
                    ("x86_64", "x86_64_linux"),
                    ("aarch64", "aarch64_linux"),
                    ("wasm32", "wasm32"),
                )
            }
    return found
