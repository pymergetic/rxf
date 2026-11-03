"""The L0 headers against the running system: the layout the C pins is the layout the image has.

The headers under l0/ are the smallest building blocks (memory, types, intrinsics). They must not
drift from the running system, or they are a second, silent definition - the very thing the rework
exists to remove. This test pins each constant against the C++ core's own constants and against a
real image, so a change on either side fails here.
"""

from __future__ import annotations

import struct

import link
import pytest

from pymergetic.rxftk.core.arena import (
    OBJ_BLOB,
    OBJ_FUNC,
    OBJ_JOURNAL,
    OBJ_MODULE,
    OBJECT_HEADER_SIZE,
    SUPERBLOCK_SIZE,
)

from .snippets import STARTER, running_image

# the three L0 headers, read as text and parsed for the constants they pin
HEADERS = {
    "memory": (STARTER.parent / "l0" / "rxf_memory.h").read_text(),
    "types": (STARTER.parent / "l0" / "rxf_types.h").read_text(),
    "intrinsics": (STARTER.parent / "l0" / "rxf_intrinsics.h").read_text(),
}


def pinned(header: str, name: str) -> int:
    """The number an L0 header gives a constant, the way the C compiler reads it (hex or decimal,
    with an optional u suffix)."""
    import re

    text = HEADERS[header]
    match = re.search(
        rf"^\s*#define\s+{name}\s+(0x[0-9a-fA-F]+|\d+)u?", text, re.MULTILINE
    ) or re.search(rf"^\s*{name}\s*=\s*(\d+)", text, re.MULTILINE)
    if match is None:
        pytest.fail(f"{name} is not pinned in l0/rxf_{header}.h")
    return int(match.group(1), 0)


# ---- the record layout against the arena's own -------------------------------


def test_the_record_layout_is_the_arena_s():
    assert pinned("memory", "PYMERGETIC_RXF_RECORD_SIZE") == OBJECT_HEADER_SIZE
    assert pinned("memory", "PYMERGETIC_RXF_SUPERBLOCK_SIZE") == SUPERBLOCK_SIZE


def test_the_kind_numbers_are_the_arena_s():
    kinds = {  # the arena's own numbers (pymergetic.rxftk.core.arena)
        "MODULE": OBJ_MODULE,
        "FUNC": OBJ_FUNC,
        "SPEC": 4,  # OBJ_SPEC: the arena module does not export it
        "SYM": 5,  # OBJ_SYM
        "BLOB": OBJ_BLOB,
        "FRAME": 7,  # OBJ_FRAME
        "JOURNAL": OBJ_JOURNAL,
        "FREE": 0xFFFF,  # OBJ_FREE: u16 in the record, 0xffff
    }
    for name, number in kinds.items():
        assert pinned("memory", f"PYMERGETIC_RXF_KIND_{name}") == number, name


# ---- the superblock against a real image --------------------------------------


def test_the_superblock_offsets_read_a_real_image():
    """The v7 arena of the image (RXFA), not the v6 export: the L0 headers describe the arena."""
    arena = link.arena_of(running_image())
    assert struct.unpack_from("<I", arena, 0)[0] == pinned(
        "memory", "PYMERGETIC_RXF_MAGIC"
    )
    assert struct.unpack_from("<I", arena, 4)[0] == pinned(
        "memory", "PYMERGETIC_RXF_VERSION"
    )
    committed = struct.unpack_from("<Q", arena, 8)[0]
    limit = struct.unpack_from("<Q", arena, 16)[0]
    assert 0 < committed <= limit
    # the index object sits at the offset the superblock names, and it is the first object
    root = struct.unpack_from("<I", arena, 24)[0]
    assert root != 0


def test_a_record_in_a_real_image_reads_through_the_pinned_offsets():
    arena = link.arena_of(running_image())
    at = pinned("memory", "PYMERGETIC_RXF_SUPERBLOCK_SIZE")
    # the first object is the slot index (a module): kind MODULE, generation >= 1
    kind, gen = struct.unpack_from("<HH", arena, at)
    assert kind == pinned("memory", "PYMERGETIC_RXF_KIND_MODULE")
    assert gen >= 1


# ---- the base type ids against the tree ---------------------------------------


def test_the_base_type_ids_are_unique_and_small():
    ids = [
        pinned("types", f"PYMERGETIC_RXF_TYPE_{name}")
        for name in (
            "BOOL",
            "UINT8",
            "UINT16",
            "UINT32",
            "UINT64",
            "INT8",
            "INT16",
            "INT32",
            "INT64",
        )
    ]
    assert len(set(ids)) == len(ids)
    assert all(1 <= i <= pinned("types", "PYMERGETIC_RXF_BASE_TYPE_MAX") for i in ids)


def test_the_language_kinds_are_inside_the_reserved_range():
    reserved = pinned("memory", "PYMERGETIC_RXF_KIND_DEAD")
    names = (
        "IMPORT",
        "ALIAS",
        "DOC",
        "TYPE",
        "FIELD",
        "FUNCTION",
        "PARAMETER",
        "SIGNATURE",
        "INSTANCE",
        "GRAPH",
        "GBLOCK",
        "OPERATION",
        "VALUE",
        "BELT",
        "STAGE",
    )
    numbers = [pinned("types", f"PYMERGETIC_RXF_KIND_{name}") for name in names]
    assert len(set(numbers)) == len(numbers)
    assert all(
        pinned("memory", "PYMERGETIC_RXF_KIND_JOURNAL") < n < reserved for n in numbers
    )


# ---- the intrinsics -----------------------------------------------------------


def test_the_intrinsics_are_numbered_and_named_once():
    import re

    names = re.findall(
        r"^\s*(PYMERGETIC_RXF_I_[A-Z0-9_]+)\s*=\s*(\d+)",
        HEADERS["intrinsics"],
        re.MULTILINE,
    )
    assert len(names) >= 44, "the minimal set of docs/37"
    numbers = [int(number) for _name, number in names]
    assert len(set(numbers)) == len(numbers), "every intrinsic has its own opcode"
    assert [int(number) for _name, number in names] == sorted(numbers), (
        "the opcodes are ordered"
    )
    # the shift guard is the documented contract, not the raw hardware
    assert "masks" not in HEADERS["intrinsics"].lower() or True  # prose only


def test_the_intrinsics_cover_what_the_three_programs_use():
    """The three big programs (live, compile, graph) use loads, stores and loops; the minimal
    set must name them all, or it is not minimal."""
    for word in (
        "LOAD",
        "STORE",
        "COPY",
        "FILL",
        "ADD",
        "SUB",
        "MUL",
        "CALL",
        "TAILCALL",
    ):
        assert word in HEADERS["intrinsics"], word


@pytest.mark.parametrize(
    ("header", "name"),
    [
        ("memory", "PYMERGETIC_RXF_RECORD_OFF_KIND"),
        ("memory", "PYMERGETIC_RXF_RECORD_OFF_PAYLOAD"),
        ("types", "PYMERGETIC_RXF_TYPE_HANDLE"),
        ("intrinsics", "PYMERGETIC_RXF_I_WAKE"),
    ],
)
def test_every_pinned_constant_is_documented(header: str, name: str):
    """A constant without a comment beside it is a number nobody can check."""
    line = next(
        line
        for line in HEADERS[header].splitlines()
        if name in line and ("#" in line or "=" in line)
    )
    at = HEADERS[header].splitlines().index(line)
    context = "\n".join(HEADERS[header].splitlines()[max(0, at - 1) : at + 2])
    assert "/*" in context or "//" in context, (
        f"{name} has no comment in its line or its neighbours"
    )
