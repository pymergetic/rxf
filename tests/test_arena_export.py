"""The world lands in the v7 one-arena image (docs/32 2): tree, names, payloads."""

import struct

import arena_export
import pytest
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    OBJ_MODULE,
    OBJ_SYM,
    OFLAG_RO,
    SPEC_SIZE,
)

from .snippets import STARTER

ALGORITHMS = ("pymergetic", "rxf", "algorithms", "algorithms")
BINARY_SEARCH_GRAPH = (
    "pymergetic",
    "rxf",
    "algorithms",
    "binary_search",
    "binary_search_graph",
)
BYTECODE = ("pymergetic", "rxf", "algorithms", "binary_search", "bytecode")


@pytest.fixture(scope="module")
def world():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def arena(world):
    return arena_export.export_world(world)


def down(arena, *path):
    node = arena.root_handle
    for segment in path:
        node = arena.find(node, segment)
        assert node != HANDLE32_NONE, f"no {segment!r} under {'/'.join(path)}"
    return node


def source_of(world, *path):
    node = 0
    for segment in path:
        node = next(
            child
            for child in world.children(node)
            if world.get(child)["name"] == segment
        )
    return node


def test_the_world_lands_under_one_root(arena):
    kind, _, _, name, _, _, _, _ = arena.load(arena.root_handle)
    assert kind == OBJ_MODULE
    assert arena.symbol(name) == "boot"
    for segment in ("sys", "pymergetic", "Alias"):
        assert arena.find(arena.root_handle, segment) != HANDLE32_NONE


def test_the_paths_survive_the_export(arena):
    for length in range(1, len(ALGORITHMS) + 1):
        assert down(arena, *ALGORITHMS[:length]) != HANDLE32_NONE


def test_a_leaf_payload_lands_byte_for_byte(world, arena):
    source = source_of(world, *ALGORITHMS)
    payload = world.get(source)["payload"]
    assert payload, "the pinned leaf has bytes to compare"
    handle = down(arena, *ALGORITHMS)
    room = arena.load(handle)[7]
    assert arena.payload(handle) == payload.ljust(room, b"\0")


def test_a_modules_own_bytes_follow_the_chain_head(world, arena):
    source = source_of(world, "Alias")
    data = world.get(source)["payload"]
    assert data, "the pinned type has a layout to compare"
    stored = arena.payload(down(arena, "Alias"))
    children, count = struct.unpack("<II", stored[:8])
    assert count == len(world.children(source))
    first = world.get(world.children(source)[0])["name"]
    assert arena.symbol(arena.load(children)[3]) == first
    head_size = len(arena_export.EMPTY_MODULE_HEAD)
    assert stored[head_size:][: len(data)] == data


def test_kinds_are_storage_roles(world, arena):
    source = source_of(world, *ALGORITHMS)
    assert world.get(source)["kind"] == 21  # a world ALIAS (ground.h NODE_ALIAS)
    # A type with fields holds the tree's chain: its storage role is the module.
    assert arena.load(down(arena, "Alias"))[0] == OBJ_MODULE
    assert arena.load(down(arena, *ALGORITHMS))[0] == OBJ_SYM
    for length in range(1, len(ALGORITHMS)):
        assert arena.load(down(arena, *ALGORITHMS[:length]))[0] == OBJ_MODULE


def test_the_export_is_deterministic(world, arena):
    again = arena_export.export_world(world)
    assert bytes(again.to_bytes()) == bytes(arena.to_bytes())


def test_the_refs_land_as_relation_tables(world, arena):
    source = source_of(world, *BINARY_SEARCH_GRAPH)
    refs = world.get(source)["refs"]
    assert len(refs) == 3
    path = world.get(source)["path"]
    table_handle = down(arena, "sys", arena_export.RELATIONS, path)
    assert arena.load(table_handle)[7] == len(refs) * arena_export.REF_RECORD.size
    table = arena.payload(table_handle)
    for ref, (target, role, kind, binding, index) in zip(
        refs,
        (
            arena_export.REF_RECORD.unpack_from(table, at)
            for at in range(0, len(table), arena_export.REF_RECORD.size)
        ),
        strict=True,
    ):
        world_target, world_role, world_index, world_kind, world_binding = ref
        assert (kind, binding, index) == (world_kind, world_binding, world_index)
        assert arena.symbol(arena.load(target)[3]) == world.get(world_target)["name"]
        assert arena.symbol(arena.load(role)[3]) == world.get(world_role)["name"]


def test_a_leaf_with_refs_gets_its_table_too(world, arena):
    source = source_of(world, *BYTECODE)
    assert not world.children(source), "the pinned path is a leaf"
    refs = world.get(source)["refs"]
    assert refs
    path = world.get(source)["path"]
    table_handle = down(arena, "sys", arena_export.RELATIONS, path)
    assert arena.load(table_handle)[7] == len(refs) * arena_export.REF_RECORD.size


def test_a_dangling_ref_refuses_the_export():
    class TinyWorld:
        def children(self, handle):
            return [1] if handle == 0 else []

        def get(self, handle):
            if handle == 0:
                return {"name": None, "kind": 0, "payload": b"", "refs": []}
            return {
                "name": "x",
                "path": "x",
                "kind": 0,
                "payload": b"",
                "refs": [(999, 1, 0, 0, 0)],  # 999 is no object this walk places
            }

    with pytest.raises(ValueError, match="dangling ref"):
        arena_export.export_world(TinyWorld())


@pytest.fixture(scope="module")
def placed(world):
    """The arena together with where each world object landed in it."""
    return arena_export.place_world(world)


def every_object(world):
    stack = [0]
    while stack:
        source = stack.pop()
        yield source
        stack.extend(world.children(source))


def test_every_object_keeps_its_semantic_face(world, placed):
    """docs/32 5: the record flattens the world's kind to a storage role; the row
    of sys/specs keeps the Type, the flags word, the index and the generation."""
    arena, handles = placed
    handles = {0: arena.root_handle, **handles}
    count = 0
    for source in every_object(world):
        info = world.get(source)
        kind = info["type"]
        want_type = HANDLE32_NONE if kind == (1 << 64) - 1 else handles[kind]
        assert arena.spec(handles[source]) == (
            want_type,
            arena_export.flags_word(info),
            info["index"],
            info["generation"],
        ), info["path"]
        count += 1
    assert count == world.handle_count  # every world object, none skipped


def test_the_row_keeps_the_kinds_the_record_flattens(world, placed):
    arena, handles = placed
    world_kinds: dict[int, set[int]] = {}
    for source in every_object(world):
        if source == 0:
            continue
        role = arena.load(handles[source])[0]
        world_kinds.setdefault(role, set()).add(world.get(source)["kind"])
    # more than one world kind shares a storage role: the record alone loses them
    assert any(len(kinds) > 1 for kinds in world_kinds.values())
    for source in every_object(world):
        if source == 0:
            continue
        row = arena.spec(handles[source])
        assert row[1] & 0xFF == world.get(source)["kind"]  # the kind byte of the flags


def test_the_paths_are_the_worlds_paths(world, placed):
    """The arena answers the path the world spells: the names below the root, dotted.
    A relation table is named after it, so this is the lookup a reader resolves by."""
    arena, handles = placed
    for source in every_object(world):
        want = world.get(source)["path"]
        got = arena.path_of(arena.root_handle if source == 0 else handles[source])
        assert got == want, f"{want!r} -> {got!r}"
    assert arena.path_of(arena.root_handle) == ""


def test_the_refs_are_the_relation_table_of_the_object(world, placed):
    """docs/32 2: refs are data, in a table under sys/relations, keyed by the path.
    `refs_of` is the reader's side of it - the same rows the export wrote."""
    arena, handles = placed
    seen = 0
    for source in every_object(world):
        want = world.get(source)["refs"]
        got = arena.refs_of(arena.root_handle if source == 0 else handles[source])
        assert len(got) == len(want), world.get(source)["path"]
        for row, (target, role, index, kind, binding) in zip(got, want, strict=True):
            row_target, row_role, row_kind, row_binding, row_index = row
            assert (row_kind, row_binding, row_index) == (kind, binding, index)
            # the exporter remapped the targets to arena handles: the same objects
            assert row_target == (arena.root_handle if target == 0 else handles[target])
            assert row_target != HANDLE32_NONE
            assert row_role == (arena.root_handle if role == 0 else handles[role])
        seen += len(want)
    assert seen > 0, "the starter carries relations"
    # an object with no table has no refs, and a stale handle answers nothing
    assert arena.refs_of(down(arena, "sys", "specs")) == []
    with pytest.raises(Exception):
        arena.refs_of(0x12345678)


def test_the_handle_count_is_the_index(placed):
    arena, _ = placed
    assert arena.handle_count > arena.root_handle >> 20  # every placed object has an index


def test_the_spec_table_is_sized_to_the_index(placed):
    arena, _ = placed
    table = down(arena, "sys", "specs")
    rows = arena.load(table)[7] // SPEC_SIZE
    assert rows > 0 and arena.load(table)[7] == rows * SPEC_SIZE


def test_a_dangling_type_refuses_the_export():
    class TinyWorld:
        def children(self, handle):
            return [1] if handle == 0 else []

        def get(self, handle):
            if handle == 0:
                return {"name": None, "kind": 0, "payload": b"", "refs": []}
            return {
                "name": "x",
                "path": "x",
                "kind": 0,
                "payload": b"",
                "refs": [],
                "type": 999,  # 999 is no object this walk places
            }

    with pytest.raises(ValueError, match="dangling type"):
        arena_export.export_world(TinyWorld())


def test_the_founding_core_lands(arena):
    role = down(arena, "honors", "roles", "inspired")
    assert arena.payload(role).rstrip(b"\0") == b"made it thinkable"
    person = down(arena, "honors", "people", "terry_davis")
    assert b"TempleOS" in arena.payload(person)
    work = down(arena, "honors", "works", "templeos")
    assert b"one person" in arena.payload(work)
    assert down(arena, "honors", "ledger") != HANDLE32_NONE


def test_the_founding_refs_land_as_tables(arena):
    table = down(arena, "sys", arena_export.RELATIONS, "honors.people.terry_davis")
    assert arena.load(table)[7] == 2 * arena_export.REF_RECORD.size
    records = [
        arena_export.REF_RECORD.unpack_from(arena.payload(table), at)
        for at in range(0, 2 * arena_export.REF_RECORD.size, arena_export.REF_RECORD.size)
    ]
    targets = [arena.symbol(arena.load(target)[3]) for target, _, *_ in records]
    roles = [arena.symbol(arena.load(role)[3]) for _, role, *_ in records]
    assert (targets, roles) == (["templeos", "pymergetic"], ["wrote", "inspired"])


def test_the_genesis_is_read_only(arena):
    for path in (
        ("honors", "roles", "wrote"),
        ("honors", "people", "terry_davis"),
        ("honors", "works", "templeos"),
        ("sys", arena_export.RELATIONS, "honors.works.templeos"),
    ):
        handle = down(arena, *path)
        assert arena.load(handle)[2] & OFLAG_RO, f"{'/'.join(path)} is Genesis"
    ledger = down(arena, "honors", "ledger")
    assert not arena.load(ledger)[2] & OFLAG_RO, "the ledger grows"
