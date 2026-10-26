"""The v7 one-arena allocator: the FREE ring, the end marker, and the growth hook."""

import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.core.arena import (
    ArenaError,
    FREE_MIN_SPAN,
    HANDLE32_NONE,
    INDEX_OFFSET,
    INDEX_SELF,
    INDEX_SYMS,
    JOURNAL_GRANULE,
    JOURNAL_HEAD_SIZE,
    MODULE_SIZE,
    OBJ_BLOB,
    OBJ_FREE,
    OBJ_JOURNAL,
    OBJ_MODULE,
    OBJ_SYM,
    OBJECT_HEADER_SIZE as HEADER,
    OFLAG_ROOT,
    SLOT_SIZE,
    SUPERBLOCK_SIZE as SUPER_SIZE,
    SYMS_ANONYMOUS,
    SYMS_HEAD_SIZE,
    UNDO_SIZE,
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


# ---- the object model (docs/32 slice 3) ------------------------------------


def test_the_object_model_layout_pins():
    assert SLOT_SIZE == 16
    assert MODULE_SIZE == 8
    assert SYMS_HEAD_SIZE == 8
    assert INDEX_OFFSET == 4096
    assert INDEX_SELF == 0
    assert INDEX_SYMS == 1
    assert SYMS_ANONYMOUS == 0  # id 0 is anonymous and stored nowhere
    assert HANDLE32_NONE == 0xFFFFFFFF


def test_bootstrap_plants_the_sys_tree():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    # Handle numbers are layout: gen 1, indices in creation order.
    assert a.root_handle == 0x100002
    sys_h = a.find(a.root_handle, "sys")
    assert sys_h == 0x100003
    leaves = {
        "objects": 0x100000,
        "functions": 0x100004,
        "journal": 0x100005,
        "caps": 0x100006,
        "free": 0x100007,
        "syms": 0x100001,
    }
    for name, handle in leaves.items():
        assert a.find(sys_h, name) == handle, name
    root = a.load(a.root_handle)
    assert root[0] == OBJ_MODULE
    assert root[2] & OFLAG_ROOT
    assert a.load(leaves["objects"])[0] == OBJ_MODULE
    assert a.load(leaves["syms"])[0] == OBJ_SYM  # its payload is the string table


def test_lookups_walk_the_tree_and_misses_answer_none():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    assert a.find(sys_h, "caps") != HANDLE32_NONE
    assert a.find(sys_h, "not-there") == HANDLE32_NONE  # a miss answers HANDLE32_NONE
    assert a.find(sys_h, "sys") == HANDLE32_NONE  # children only, no recursion
    assert a.find(a.find(sys_h, "caps"), "sys") == HANDLE32_NONE
    with pytest.raises(ArenaError):
        a.find(HANDLE32_NONE, "caps")  # a dead parent is a caller error, not a miss


def test_interning_dedupes_and_symbols_roundtrip():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    first = a.intern("hello")
    assert a.intern("hello") == first
    second = a.intern("world")
    assert second != first
    assert a.symbol(first) == "hello"
    assert a.symbol(second) == "world"


def test_anonymous_objects_carry_the_anonymous_name():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, None, OBJ_BLOB, 8)
    assert a.load(handle)[3] == SYMS_ANONYMOUS


def test_create_load_release_and_stale_handles():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, "temp", OBJ_BLOB, 16)
    record = a.load(handle)
    assert record[0] == OBJ_BLOB
    assert record[6] == 1  # refs
    assert record[7] == 16  # payload
    a.release(handle)
    with pytest.raises(ArenaError):
        a.load(handle)
    a.release(HANDLE32_NONE)  # a no-op, not an error
    reused = a.new_object(sys_h, "other", OBJ_BLOB, 8)
    assert reused & 0xFFFFF == handle & 0xFFFFF  # the index lives again
    assert reused != handle  # under a new generation
    with pytest.raises(ArenaError):
        a.load(handle)  # the stale handle stays refused
    with pytest.raises(ArenaError):
        a.release(handle)  # and cannot free the new occupant


def test_releasing_unlinks_the_child_from_its_parent():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    keep = a.new_object(sys_h, "keep", OBJ_BLOB, 8)
    drop = a.new_object(sys_h, "drop", OBJ_BLOB, 8)
    a.release(drop)
    assert a.find(sys_h, "drop") == HANDLE32_NONE
    assert a.find(sys_h, "keep") == keep


def test_the_index_object_refuses_children():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    objects_h = a.find(a.find(a.root_handle, "sys"), "objects")
    with pytest.raises(ArenaError):
        a.new_object(objects_h, "child", OBJ_BLOB, 8)
    with pytest.raises(ArenaError):
        a.find(objects_h, "anything")
    with pytest.raises(ArenaError):
        a.release(objects_h)


def test_creation_refuses_oversized_payloads():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    with pytest.raises(ArenaError):
        a.new_object(sys_h, "big", OBJ_BLOB, 2**40)


def test_syms_growth_relocates_and_repoints_the_index():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    for i in range(200):  # several times over the initial syms window
        a.intern(f"symbol-{i:04d}")
    assert a.symbol(a.intern("symbol-0199")) == "symbol-0199"
    assert a.symbol(a.intern("symbol-0000")) == "symbol-0000"
    assert a.verify() is None


def test_creation_grows_committed_by_the_objects_span():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    before = a.committed
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, "sized", OBJ_BLOB, 100)
    assert a.committed >= before + room_span(100)
    # The payload field is the granted size: 8-aligned, and span = header + payload.
    assert a.load(handle)[7] == 104


# ---- the undo journal (docs/32 3) ------------------------------------------


def test_the_journal_layout_pins():
    assert (UNDO_SIZE, JOURNAL_HEAD_SIZE, JOURNAL_GRANULE) == (16, 24, 8)
    assert OBJ_JOURNAL == 8


def test_bootstrap_ends_on_a_committed_state():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    assert a.journal_records() == 0  # boot is one committed state
    assert a.load(a.find(a.find(a.root_handle, "sys"), "journal"))[0] == OBJ_JOURNAL


def test_writes_are_recorded_before_the_store_and_dropped_at_commit():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    a.new_object(a.find(a.root_handle, "sys"), "extra", OBJ_BLOB, 64)
    assert a.journal_records() > 0  # write-ahead: the records precede the stores
    a.journal_commit()
    assert a.journal_records() == 0  # commit drops the ring


def test_rollback_unwinds_a_create_and_gives_the_span_back():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    before = a.committed
    a.journal_commit()
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, "extra", OBJ_BLOB, 64)
    assert a.find(sys_h, "extra") == handle
    a.journal_rollback()
    assert a.journal_records() == 0
    assert a.find(sys_h, "extra") == HANDLE32_NONE
    with pytest.raises(ArenaError):
        a.load(handle)  # the handle is stale the moment the create is unwound
    assert a.committed == before  # the span is back with the marker
    a.verify()


def test_rollback_unwinds_a_release_and_keeps_the_handle():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, "kept", OBJ_BLOB, 8)
    a.journal_commit()
    a.release(handle)
    assert a.find(sys_h, "kept") == HANDLE32_NONE
    a.journal_rollback()
    assert a.find(sys_h, "kept") == handle  # the unlink is unwound too
    assert a.load(handle)[0] == OBJ_BLOB
    a.verify()


def test_rollback_rewinds_the_names_so_the_same_ids_come_back():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    first = a.intern("fresh")
    a.journal_commit()
    second = a.intern("fresh-2")
    assert second > first
    a.journal_rollback()
    assert a.intern("fresh-2") == second  # the table rewound: the id is handed out again
    assert a.symbol(second) == "fresh-2"
    a.verify()


def test_a_commit_survives_the_next_rollback():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    sys_h = a.find(a.root_handle, "sys")
    handle = a.new_object(sys_h, "kept", OBJ_BLOB, 8)
    a.journal_commit()
    a.journal_rollback()  # nothing is held: nothing is unwound
    assert a.find(sys_h, "kept") == handle
    a.verify()


def test_rollback_across_a_journal_relocation_restores_the_state_and_the_log():
    a = ObjectArena.create(1 << 20)
    a.bootstrap("boot")
    before = a.committed
    a.journal_commit()
    sys_h = a.find(a.root_handle, "sys")
    for i in range(12):
        a.new_object(sys_h, f"fill-{i:02d}", OBJ_BLOB, 8)
    # The boot ring holds 256 records; past it the log relocates mid-transaction,
    # and the move itself is journaled (docs/32 3: the journal "can itself be
    # journaled") so the rollback unwinds it too.
    assert a.journal_records() > 256
    a.journal_rollback()
    assert a.journal_records() == 0
    for i in range(12):
        assert a.find(sys_h, f"fill-{i:02d}") == HANDLE32_NONE
    assert a.committed == before  # the log is back on its boot span
    a.verify()
    # The reopened log still works: the next transaction rolls back just as well.
    handle = a.new_object(sys_h, "again", OBJ_BLOB, 8)
    assert a.find(sys_h, "again") == handle
    a.journal_rollback()
    assert a.find(sys_h, "again") == HANDLE32_NONE
    assert a.committed == before
    a.verify()
