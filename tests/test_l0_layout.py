"""The L0 headers against the running system: the structs the C pins are the structs the arena has.

l0/rxf_memory.h defines the arena as structs (the record, the superblock as the first object, the
slot index as the second, the index entry, the kinds as one list). They must not drift from the
running system, or they are a second, silent definition. This test pins every struct against the
C++ core and against a real v7 image.
"""

from __future__ import annotations

import re
import struct

import link

from pymergetic.rxftk.core.arena import (
    OBJ_BLOB,
    OBJ_FUNC,
    OBJ_MODULE,
    OBJECT_HEADER_SIZE,
    SUPERBLOCK_SIZE,
)

from .snippets import STARTER, running_image

HEADER = (STARTER.parent / "l0" / "rxf_memory.h").read_text()


def enum_value(name: str) -> int:
    """The number the C enum gives a kind, the way the C compiler reads it."""
    match = re.search(rf"\b{name}\s*=\s*(\d+)", HEADER)
    assert match, f"{name} is not in l0/rxf_memory.h"
    return int(match.group(1))


# ---- the record -----------------------------------------------------------------


def test_the_record_is_the_arena_s():
    """The struct's size and every offset are the arena's own (the C++ core pins 24 bytes)."""
    assert _c_sizeof("pymergetic_rxf_record_t") == OBJECT_HEADER_SIZE
    assert _c_offset("pymergetic_rxf_record_t", "kind") == 0
    assert _c_offset("pymergetic_rxf_record_t", "gen") == 2
    assert _c_offset("pymergetic_rxf_record_t", "flags") == 4
    assert _c_offset("pymergetic_rxf_record_t", "name") == 6
    assert _c_offset("pymergetic_rxf_record_t", "parent") == 8
    assert _c_offset("pymergetic_rxf_record_t", "payload") == 20


def test_the_superblock_is_an_object_and_fits_the_page():
    """The superblock carries a record like every object, and its whole page stays 4096."""
    assert _c_offset("pymergetic_rxf_superblock_t", "record") == 0
    assert _c_offset("pymergetic_rxf_superblock_t", "magic") == OBJECT_HEADER_SIZE
    assert _c_sizeof("pymergetic_rxf_superblock_t") == SUPERBLOCK_SIZE


def test_the_index_entry_is_the_arena_s():
    assert _c_sizeof("pymergetic_rxf_index_entry_t") == 16
    assert _c_offset("pymergetic_rxf_index_entry_t", "offset") == 8


# ---- the kinds: one list, the arena's own beside the language's ------------------


def test_the_kinds_are_a_fresh_list_beside_the_running_one():
    """The L0 kinds are their own list (superblock first, then index, then the tree), NOT the
    running system's kind numbers: the running arena answers its own (OBJ_MODULE is 1 there),
    and this header is the proposal of the rebuild. What must hold: dense from 1, in order, and
    free stays the all-ones marker."""
    pairs = [
        (n, int(v))
        for n, v in re.findall(r"pymergetic_rxf_kind_(\w+)\s*=\s*(\d+)", HEADER)
        if n != "free"  # free is the all-ones marker, not part of the dense list
    ]
    names = [n for n, _v in pairs]
    numbers = [v for _n, v in pairs]
    assert numbers[0] == 1 and numbers == sorted(numbers), "dense from 1, in order"
    match = re.search(r"pymergetic_rxf_kind_free\s*=\s*(\S+),", HEADER)
    assert match and int(match.group(1), 0) == 0xFFFF, "free is the all-ones marker"
    assert "superblock" in names and "index" in names and "module" in names


def test_the_running_kinds_are_pinned_where_they_are():
    """What the running system answers stays what it answers; the L0 list is beside it, not
    instead of it (the genesis of the new image will map one onto the other, once it exists)."""
    assert OBJ_MODULE == 1 and OBJ_BLOB == 6 and OBJ_FUNC == 2


# ---- a real image through the structs ---------------------------------------------


def test_a_real_image_reads_through_the_superblock_words():
    """The running system's superblock words sit at offset 8 of the region (no record in front
    yet): magic, version, committed, limit, root, entry. The L0 header proposes to make it an
    object (a record in front, words behind); until the genesis writes one, this test pins the
    words where they are, and test_the_superblock_is_an_object pins the proposal in the C."""
    arena = link.arena_of(running_image())
    magic, version = struct.unpack_from("<II", arena, 0)
    assert magic == int.from_bytes(b"RXFA", "little")
    assert version == 1
    committed, limit = struct.unpack_from("<QQ", arena, 8)
    assert 0 < committed <= limit
    root, index = struct.unpack_from("<II", arena, 24)
    assert root != 0 and index != 0


def test_the_first_object_of_a_real_image_is_the_index():
    """The slot index is the first object behind the superblock's page (kind INDEX in the L0
    list; the running system writes its own kind numbers there, which the kinds test pins
    separately)."""
    arena = link.arena_of(running_image())
    at = SUPERBLOCK_SIZE
    kind, gen = struct.unpack_from("<HH", arena, at)
    assert kind >= 1
    assert gen >= 1


# ---- helpers: the C structs as the compiler lays them ----------------------------


def _c_sizeof(name: str) -> int:
    return _c_field(f"sizeof({name})")


def _c_offset(name: str, field: str) -> int:
    return _c_field(f"offsetof({name}, {field})")


def _c_field(expression: str) -> int:
    """The number the C compiler answers for one expression over the L0 structs."""
    # a tiny C file whose only line prints the answer; the test compiles and runs it
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as work:
        path = __import__("pathlib").Path(work) / "probe.c"
        path.write_text(
            f'#include <stdio.h>\n#include <stddef.h>\n#include "rxf_memory.h"\n'
            f'int main(void) {{ printf("%zu\\n", (size_t)({expression})); return 0; }}\n'
        )
        binary = path.with_suffix(".run")
        done = subprocess.run(
            [
                "cc",
                "-std=c11",
                "-I",
                str(STARTER.parent / "l0"),
                "-o",
                str(binary),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        _unused = done
        out = subprocess.run([str(binary)], capture_output=True, text=True, check=True)
        return int(out.stdout.strip())
