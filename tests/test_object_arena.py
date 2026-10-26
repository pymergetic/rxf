"""The v7 one-arena allocator: the FREE ring, the end marker, and the growth hook."""

import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.core.arena import (
    ArenaError,
    FREE_MIN_SPAN,
    OBJ_FREE,
    OBJECT_HEADER_SIZE as HEADER,
    SUPERBLOCK_SIZE as SUPER_SIZE,
    ObjectArena,
)

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "pymergetic" / "rxftk" / "core" / "arena" / "object_arena.cpp"
PROBE = ROOT / "tests" / "arena" / "fixed_region_probe.cpp"


def room_span(room: int) -> int:
    return HEADER + ((room + 7) & ~7)


# ---- the fresh arena --------------------------------------------------------


def test_superblock_and_the_end_marker():
    arena = ObjectArena.create(limit=8192)
    assert (arena.committed, arena.limit) == (SUPER_SIZE, 8192)
    assert arena.need == SUPER_SIZE + FREE_MIN_SPAN  # the ask init had to grow into
    tail = 8192 - SUPER_SIZE
    assert arena.record(SUPER_SIZE) == (OBJ_FREE, 0, 0, 0, 0, 0, 0, tail - HEADER)
    assert arena.links(SUPER_SIZE) == (SUPER_SIZE, SUPER_SIZE)  # the ring anchor, a singleton
    arena.verify()


def test_allocation_fills_from_the_tail_and_bumps_the_generation():
    arena = ObjectArena.create(limit=8192)
    offset = arena.s_allocate(8)
    assert offset == SUPER_SIZE
    assert arena.record(offset) == (0, 1, 0, 0, 0, 0, 0, 8)  # zeroed, gen 1, room 8
    assert arena.committed == SUPER_SIZE + room_span(8)
    assert arena.record(arena.committed)[0] == OBJ_FREE  # the marker moved up
    assert arena.links(arena.committed) == (arena.committed, arena.committed)
    arena.verify()
    arena.s_release(offset)  # adjacent to the tail: the marker grows downward
    assert arena.committed == SUPER_SIZE
    again = arena.s_allocate(8)
    assert again == SUPER_SIZE
    assert arena.record(again)[1] == 2  # the same span, one generation later
    arena.verify()


# ---- the FREE ring ----------------------------------------------------------


def test_best_fit_prefers_the_tighter_span_and_splits_it():
    arena = ObjectArena.create(limit=16384)
    hole = arena.s_allocate(104)  # a 128-byte span at the front
    keep = arena.s_allocate(16)  # keeps the hole off the tail
    assert (hole, keep) == (SUPER_SIZE, SUPER_SIZE + 128)
    arena.s_release(hole)
    arena.verify()
    taken = arena.s_allocate(8)  # 32 bytes: the hole beats the tail and splits
    assert taken == SUPER_SIZE
    assert arena.record(taken)[7] == 8
    rest = SUPER_SIZE + room_span(8)
    assert arena.record(rest)[0] == OBJ_FREE
    assert arena.record(rest)[7] == 128 - room_span(8) - HEADER
    anchor = SUPER_SIZE + 128 + room_span(16)  # the hole's ring slot: next to the marker
    assert arena.links(rest) == (anchor, anchor)
    second = arena.s_allocate(8)  # the remainder beats the tail again
    assert second == rest
    arena.verify()


def test_a_span_whose_rest_cannot_link_is_taken_whole():
    arena = ObjectArena.create(limit=16384)
    hole = arena.s_allocate(24)  # a 48-byte span
    arena.s_allocate(8)  # keeps the hole off the tail
    arena.s_release(hole)
    arena.verify()
    taken = arena.s_allocate(8)  # 32 bytes: the 16-byte rest cannot carry ring links
    assert taken == hole
    assert arena.record(taken)[7] == 24  # the granted room absorbs the slack
    arena.verify()


def test_the_end_marker_keeps_a_ring_span():
    arena = ObjectArena.create(limit=SUPER_SIZE + 88)  # a tail of 88 bytes
    assert FREE_MIN_SPAN == 40
    first = arena.s_allocate(16)  # 40 bytes, the marker keeps 48
    assert first == SUPER_SIZE
    with pytest.raises(ArenaError) as caught:
        arena.s_allocate(8)  # 32 bytes: a 48-byte marker cannot keep a ring span
    assert str(caught.value) == "OutOfMemory"
    assert arena.need == SUPER_SIZE + room_span(16) + room_span(8)
    arena.verify()


def test_releasing_is_stable_under_mixed_loads():
    arena = ObjectArena.create(limit=32768)
    offsets = [arena.s_allocate(room) for room in (8, 16, 24, 40, 88)]
    arena.verify()
    for offset in offsets[1::2]:
        arena.s_release(offset)
    arena.verify()
    reused = arena.s_allocate(24)  # best fit walks the holes, not the tail
    assert reused == offsets[3]  # the 64-byte hole: the 40-byte one cannot serve 48
    arena.verify()


# ---- growth and the fixed region -------------------------------------------


def test_the_growth_hook_commits_on_demand():
    arena = ObjectArena.create(limit=1 << 20)
    offset = arena.s_allocate(0)  # 24 bytes in the superblock page
    assert offset == SUPER_SIZE
    big = arena.s_allocate(1 << 16)  # past the first committed page
    assert big == SUPER_SIZE + room_span(0)
    arena.verify()
    # The ask that needed growth (record + remainder header and links) is `need`.
    assert arena.need == big + room_span(1 << 16) + FREE_MIN_SPAN


def test_the_fixed_region_refuses_past_the_limit():
    arena = ObjectArena.create(limit=8192, fixed=True)
    assert arena.s_allocate(8) == SUPER_SIZE  # a region committed whole, no hook behind it
    arena.verify()
    with pytest.raises(ArenaError) as caught:
        arena.s_ensure(0, 8192 + 1)
    assert str(caught.value) == "OutOfMemory"


def test_the_fixed_region_build_refuses_without_consulting_the_hook(tmp_path):
    compiler = shutil.which("clang++-18")
    if compiler is None:
        pytest.skip("clang++-18 is not installed")
    probe = tmp_path / "fixed_region_probe"
    built = subprocess.run(
        [
            compiler,
            "-std=c++20",
            "-DPYMERGETIC_RXF_FIXED_REGION",
            "-fno-exceptions",
            "-fno-rtti",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{ROOT / 'src'}",
            str(SOURCE),
            str(PROBE),
            "-o",
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(probe)], capture_output=True, text=True, check=False)
    assert ran.returncode == 0, (ran.stdout, ran.stderr)
