"""The world lands in the v7 one-arena image (docs/32 2): tree, names, payloads."""

import struct

import arena_export
import pytest
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    OBJ_MODULE,
    OBJ_SYM,
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
