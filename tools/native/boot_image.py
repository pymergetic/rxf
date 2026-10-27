"""The x86_64-bios boot image: the linked v7 image of the starter, in its carrier.

The boot smoke's x86_64-bios seat boots the REAL linked image -- the memory
``tools/native/link.py`` makes of the starter (its CONTEXT/ENTRIES/ARENA rows,
the v7 one-arena image of docs/32 1) -- instead of the tiny stub world
``boot/image_stub/world.c`` builds. This tool is the one build step that packs
those bytes for the seat; it writes bytes and never source.

The image it writes follows ``boot/image_stub/world_v7.h`` (the map is the one
home of the rows, parsed here, never copied):

- the carrier room (the map's rows below LINKED): the loader's head row (the
  chunk count the sector's read loop takes), the argument word the glue passes
  (``"RXFOK"``), the answer room, the 24-byte handle of the run, and zeros for
  the rows the glue owns (the run cell, the entry room);
- from the LINKED row the linked image itself: the runtime context at its byte
  0, the journal, the function slots, the object entries and the arena --
  superblock at its byte 0 (``"RXFA"``, version 1) and the object index as the
  first object at offset 4096 (ground.h pins the record, the slot head and the
  16-byte slot entries).

The run's program is the smoke's own -- one MOVE + RETURN that answers the
argument word, the program ``boot/image_stub/world.c`` carried -- as an object
of the world (``pymergetic.rxf.boot.smoke``), so the arena export places it
like any other object and its handle resolves through the arena's slot table
(the walk ``boot/image_stub/entry.c`` takes under ``RXF_BOOT_V7``). The handle
words at the map's HANDLE_COPY row are that object's (index, gen), packed the
way ground.h packs a handle32.

Usage::

    python tools/native/boot_image.py [--entry ENTRY.BIN] OUT.bin

``--entry`` patches the compiled entry code (``boot/image_stub/entry.c``, built
by the Makefile) into the map's ENTRY row; without it the row stays zero and
only the bytes this tool owns are written.
"""

from __future__ import annotations

import re
import struct
import sys
from pathlib import Path

import arena_export
import graph_header
import link
import programs

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import HANDLE32_GEN_BITS, HANDLE32_INDEX_BITS

ROOT = Path(__file__).resolve().parents[2]
STARTER = ROOT / "starter"
MAP = ROOT / "boot" / "image_stub" / "world_v7.h"

# The seat's line: COM1, the 16550 behind I/O ports (link.LiveSpec's uart: the
# second capability of the live block, the one the entry reports through).
UART_PORT = 0x3F8

# The probe's program: one MOVE + RETURN that answers the argument word -- the
# program boot/image_stub/world.c carried (graph.h: magic, frame bytes,
# parameters, argument words, entry word, then (slot, width) per parameter and
# the instructions). It is data, the smoke's program words: the same words the
# stub world baked in C, now an object of the real world. The magic and the
# opcodes come from the header that defines them (graph_header.numbers).
PROGRAM_PARENT = "pymergetic.rxf.boot"
PROGRAM_NAME = "smoke"


def program() -> bytes:
    """The smoke's program: take one argument word at frame slot 0, move it to
    slot 8 and return it -- every leg of the entry convention on the path of
    the answer."""
    word = graph_header.numbers()
    slot = word["RXF_OPERAND_SLOT"]
    return struct.pack(
        "<18Q",
        word["RXF_GRAPH_MAGIC"],
        64,  # frame bytes
        1,  # parameters
        1,  # argument words
        7,  # entry word (7 = 5 + 2 parameter words)
        0,
        8,  # parameter 0 lives at frame slot 0, one word
        word["RXF_OP_MOVE"],
        8,
        8,
        slot,
        0,
        8,  # frame[8] = frame[0]
        word["RXF_OP_RETURN"],
        slot,
        8,
        8,
        8,  # answer frame[8..16)
    )


# The argument the glue passes: "RXFOK" as one little-endian word (the string's
# bytes R,X,F,O,K low to high: 0x4B4F465852).
ARGUMENT = 0x0000004B4F465852

_ROW = re.compile(r"^#define[ \t]+(RXF_\w+)[ \t]+([^ \t\r\n]+)", re.MULTILINE)


class BootImageError(Exception):
    """The map and the linked image disagree, or the world will not link."""


def rows(path: Path = MAP) -> dict[str, int]:
    """The map's numeric rows by name: a literal or another row's name."""
    spelled = dict(_ROW.findall(path.read_text(encoding="utf-8")))

    def number(name: str) -> int:
        token = spelled[name].rstrip("uUlL")
        if token.startswith(("0x", "0X")):
            return int(token, 16)
        if token.isdigit():
            return int(token)
        return number(token)  # a row that names another row

    return {name: number(name) for name in spelled}


def handle_words(placed: int) -> bytes:
    """The 24-byte handle the glue passes: the packed (index, gen) of the slot
    table, the generation again (the entry's stale check), and the payload
    offset (ground.h: a handle is (index, gen); docs/32 4)."""
    generation = (placed >> HANDLE32_INDEX_BITS) & ((1 << HANDLE32_GEN_BITS) - 1)
    return struct.pack("<3Q", placed, generation, 0)


def build(out: Path, entry: bytes = b"", start: Path = STARTER) -> tuple[int, int]:
    """The seat's image at ``out``: the carrier rows and the linked image.

    Returns the packed size and the probe object's handle32 -- the words at the
    map's HANDLE_COPY row. The arena the linker places must be the arena the
    export made, byte for byte: one export, one image, no second link path.
    """
    map_rows = rows()
    image = tree.load(start)
    parent = image.handle(PROGRAM_PARENT)
    probe = image.add(parent, PROGRAM_NAME, payload=program())

    filled = programs.filled(image)
    made, placed = arena_export.place_world(filled)
    arena = made.to_bytes()
    identity = placed[probe]

    base = map_rows["RXF_BOOT_X86_IMAGE_BASE"] + map_rows["RXF_BOOT_LINKED"]
    linked = link.link(image, "x86_64", base, live=link.LiveSpec(uart=UART_PORT))
    if linked.context != base:
        raise BootImageError("the linked image's context is not the map's LINKED row")
    at = linked.data.find(arena[:64])
    if at < 0 or linked.data[at : at + len(arena)] != arena:
        raise BootImageError("the arena the linker placed is not the one the export made")

    linked_row = map_rows["RXF_BOOT_LINKED"]
    entry_row = map_rows["RXF_BOOT_ENTRY"]
    chunk = map_rows["RXF_BOOT_CHUNK"]
    if entry_row + len(entry) > linked_row:
        raise BootImageError("the entry code does not fit the carrier room")
    total = linked_row + len(linked.data)
    chunks = (total + chunk - 1) // chunk
    blob = bytearray(chunks * chunk)  # padded to whole chunks: the loader reads chunks
    struct.pack_into("<H", blob, map_rows["RXF_BOOT_HEAD_CHUNKS"], chunks)
    struct.pack_into("<I", blob, map_rows["RXF_BOOT_HEAD_BYTES"], len(blob))
    struct.pack_into("<Q", blob, map_rows["RXF_BOOT_ARGS"], ARGUMENT)
    blob[map_rows["RXF_BOOT_HANDLE_COPY"] : map_rows["RXF_BOOT_HANDLE_COPY"] + 24] = (
        handle_words(identity)
    )
    blob[entry_row : entry_row + len(entry)] = entry
    blob[linked_row : linked_row + len(linked.data)] = linked.data
    out.write_bytes(blob)
    return len(blob), identity


def main() -> None:
    arguments = sys.argv[1:]
    entry = b""
    if arguments and arguments[0] == "--entry":
        if len(arguments) < 3:
            raise SystemExit(__doc__)
        entry = Path(arguments[1]).read_bytes()
        arguments = arguments[2:]
    if len(arguments) != 1:
        raise SystemExit(__doc__)
    out = Path(arguments[0])
    size, identity = build(out, entry)
    print(f"boot image: {out} {size} bytes, probe handle {identity:#x}")


if __name__ == "__main__":
    main()
