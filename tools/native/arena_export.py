"""The world as the v7 one-arena image (docs/32 2).

`link` and `compose` shape the v6 world (the typed image of docs/1); this module
writes that world into the one-arena object model: one root image ("boot"), the
world's path tree as named objects under it, names interned in sys/syms, every
kind normalized to the storage role the arena indexes (docs/32 1.2 OBJ_*).

The paths are preserved as they are today: the world object `pymergetic.rxf.foo`
is the arena path pymergetic/rxf/foo. The kinds follow the storage roles:

- a node with children is a MODULE (the chain lives in its payload head) and its
  own bytes follow that head - a module payload is head + data;
- a leaf takes the role of its world kind: fn and code bodies are FUNC, typed
  layouts (type, field, enumerator) are SPEC, names (import, export, alias) are
  SYM, the rest is BLOB.

Relations are data, not navigation (docs/32 2): each world object's refs become
a table of the pinned 24-byte reference records in `sys/relations/<path>`, with
`target` and `role` remapped to arena handles. A dangling target refuses the
export. The walk is two passes - the tree first (every world handle earns its
arena handle), then the tables.

Then the founding core lands (docs/36): `honors/roles`, `honors/people`,
`honors/works` - the Genesis, OFLAG_RO - and the append-only `honors/ledger`.
Who enters is a human decision; the entries below are the founding commit's,
and an `embodies` ref is only ever written where its code is really in the
image (docs/36 5).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Protocol

from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    MODULE_SIZE,
    OBJ_BLOB,
    OBJ_FUNC,
    OBJ_MODULE,
    OBJ_SPEC,
    OBJ_SYM,
    OFLAG_RO,
    ObjectArena,
)

# The v6 node kinds (ground.h PYMERGETIC_RXF_NODE_*) as the world reports them in
# `kind`, mapped to the arena's storage roles. The semantic node kind itself is
# the Spec's business (docs/32 2), not the record's.
_NODE_FUNC = {6, 7}  # fn, code
_NODE_SPEC = {4, 5, 20}  # type, field, enumerator
_NODE_SYM = {12, 13, 21}  # import, export, alias

LEAF_KIND: dict[int, int] = {
    **{node: OBJ_FUNC for node in _NODE_FUNC},
    **{node: OBJ_SPEC for node in _NODE_SPEC},
    **{node: OBJ_SYM for node in _NODE_SYM},
}

# A module payload opens with the arena-owned chain head (ground.h
# pymergetic_rxf_module): the empty chain spells HANDLE32_NONE, and a zeroed head
# would read as a chain onto the index object. The module's own data follows.
EMPTY_MODULE_HEAD = struct.pack("<II", HANDLE32_NONE, 0)

# Relation tables (docs/32 2): sys/relations holds one child per related object,
# named by the object's dotted path; its payload is the pinned reference record
# (ground.h pymergetic_rxf_ref) per ref, in the world's order.
RELATIONS = "relations"
REF_RECORD = struct.Struct("<QQHHI")  # target, role, kind, binding, index
HANDLE_NONE_64 = (1 << 64) - 1  # ground.h PYMERGETIC_RXF_HANDLE_NONE

# The Genesis (docs/36 3): the founding core of the image, in name order
# (docs/36 6: the tree order is name order). Who enters is a human decision
# (docs/36 5) - these entries are the founding commit's. A ref is (role, target
# path, index): a work names the people who invented and wrote it, a person
# names their work and the image it made thinkable. An `embodies` ref is only
# written where its code is really in the image (docs/36 5): the faces that
# carry Blake2s, curve arithmetic and MicroPython join the image later, and
# their embodies refs land with them, before the founding commit pins the
# digest.
HONORS = "honors"
ROLES = "roles"
PEOPLE = "people"
WORKS = "works"
LEDGER = "ledger"


@dataclass(frozen=True)
class Entry:
    text: str
    refs: tuple[tuple[str, str, int], ...]  # (role, target path, index)


GENESIS_ROLES = {
    "embodies": "lives on in <this part of the image>",
    "inspired": "made it thinkable",
    "invented": "gave the idea",
    "wrote": "wrote the code",
}

GENESIS_PEOPLE = {
    "aumasson": Entry(
        "Co-designed BLAKE2s: speed and care in one construction.",
        (("wrote", f"{HONORS}/{WORKS}/blake2s", 0),),
    ),
    "bernstein": Entry(
        "Gave us curve25519, ed25519 and chacha20-poly1305: modern cryptography "
        "that is safe to implement.",
        (("wrote", f"{HONORS}/{WORKS}/curve25519", 0),),
    ),
    "george": Entry(
        "Wrote MicroPython; the Python that runs where memory is scarce is his.",
        (("wrote", f"{HONORS}/{WORKS}/micropython", 0),),
    ),
    "terry_davis": Entry(
        "1969-2018. Built TempleOS alone - compiler, kernel, userland - and "
        "proved the whole-system dream is human.",
        (
            ("wrote", f"{HONORS}/{WORKS}/templeos", 0),
            ("inspired", "pymergetic", 1),
        ),
    ),
}

GENESIS_WORKS = {
    "blake2s": Entry(
        "A cryptographic hash function, fast on every word size and careful by "
        "construction.",
        (
            ("invented", f"{HONORS}/{PEOPLE}/aumasson", 0),
            ("wrote", f"{HONORS}/{PEOPLE}/aumasson", 1),
        ),
    ),
    "curve25519": Entry(
        "Diffie-Hellman over a curve chosen to be safe by construction.",
        (
            ("invented", f"{HONORS}/{PEOPLE}/bernstein", 0),
            ("wrote", f"{HONORS}/{PEOPLE}/bernstein", 1),
        ),
    ),
    "micropython": Entry(
        "Python for microcontrollers: a full language where only kilobytes were "
        "expected.",
        (
            ("invented", f"{HONORS}/{PEOPLE}/george", 0),
            ("wrote", f"{HONORS}/{PEOPLE}/george", 1),
        ),
    ),
    "templeos": Entry(
        "A whole operating system with its own compiler and userland, built by "
        "one person.",
        (
            ("invented", f"{HONORS}/{PEOPLE}/terry_davis", 0),
            ("wrote", f"{HONORS}/{PEOPLE}/terry_davis", 1),
        ),
    ),
}

# Index room beyond the world's own objects: sys/ leaves, the honors tree, and
# the objects the image needs after the walk.
INDEX_HEADROOM = 256


class World(Protocol):
    """The reader side of a world (tools/native/link.py's image): a path tree of
    named objects with payloads. `get` answers the world's dict for one handle."""

    def children(self, handle: int) -> list[int]: ...

    def get(self, handle: int) -> dict[str, Any]: ...


def export_world(world: World, limit: int = 256 * 1024 * 1024) -> ObjectArena:
    """The world as an arena image: root "boot", the world's tree under it.

    The export is deterministic - the same world answers byte-identical images.
    The arena arrives committed (one journal commit at the end of the walk).
    """
    objects, tables = _survey(world)
    arena = ObjectArena.create(limit)
    arena.bootstrap("boot", objects + tables + INDEX_HEADROOM)
    arena.journal_commit()  # the skeleton is ground, not history
    handles: dict[int, int] = {}
    for child in world.children(0):  # the world's root IS the arena's root
        _place(world, child, arena.root_handle, arena, handles)
    relations = _relations(world, handles, arena)
    _genesis(arena, relations)
    arena.journal_commit()
    arena.verify()
    return arena


def _survey(world: World) -> tuple[int, int]:
    """The world's object count and how many of them carry relation tables."""

    def walk(source: int) -> tuple[int, int]:
        objects, tables = 1, 1 if world.get(source).get("refs") else 0
        for child in world.children(source):
            more, more_tables = walk(child)
            objects += more
            tables += more_tables
        return objects, tables

    return walk(0)


def _place(
    world: World, source: int, target: int, arena: ObjectArena, handles: dict[int, int]
) -> None:
    info = world.get(source)
    name = info.get("name") or None  # the world may spell anonymity as ""
    data = info.get("payload") or b""
    children = world.children(source)
    if children:
        # The chain head is arena-owned; the module's own bytes follow it.
        room = MODULE_SIZE + _round8(len(data))
        handle = arena.new_object(target, name, OBJ_MODULE, room)
        if data:
            arena.set_payload(handle, EMPTY_MODULE_HEAD + data)
    else:
        room = _round8(len(data))
        handle = arena.new_object(
            target, name, LEAF_KIND.get(info.get("kind", 0), OBJ_BLOB), room
        )
        if data:
            arena.set_payload(handle, data)
    handles[source] = handle
    for child in children:
        _place(world, child, handle, arena, handles)


def _relations(world: World, handles: dict[int, int], arena: ObjectArena) -> int:
    """The refs of every world object as tables under sys/relations (pass 2)."""
    sys = arena.find(arena.root_handle, "sys")
    assert sys != HANDLE32_NONE, "bootstrap lays out sys/"
    relations = arena.new_object(sys, RELATIONS, OBJ_MODULE, MODULE_SIZE)

    def walk(source: int) -> None:
        info = world.get(source)
        refs = info.get("refs")
        if refs:
            path = info.get("path")
            if not path:
                raise ValueError(f"object {source} has refs but no path to name them by")
            table = b"".join(_record(handles, ref) for ref in refs)
            handle = arena.new_object(relations, path, OBJ_SPEC, _round8(len(table)))
            arena.set_payload(handle, table)
        for child in world.children(source):
            walk(child)

    walk(0)
    return relations


def _genesis(arena: ObjectArena, relations: int) -> None:
    """The founding core (docs/36 3): roles, people, works - the Genesis, marked
    OFLAG_RO - and the append-only ledger that grows for the life of the image."""
    honors = arena.new_object(arena.root_handle, HONORS, OBJ_MODULE, MODULE_SIZE)
    groups = {
        ROLES: _module(arena, honors, ROLES),
        PEOPLE: _module(arena, honors, PEOPLE),
        WORKS: _module(arena, honors, WORKS),
    }
    arena.new_object(honors, LEDGER, OBJ_MODULE, MODULE_SIZE)

    frozen: list[int] = [handle for handle in groups.values()]
    for name, text in sorted(GENESIS_ROLES.items()):
        frozen.append(_entry(arena, groups[ROLES], name, text.encode(), OBJ_SPEC))
    for group, entries in ((PEOPLE, GENESIS_PEOPLE), (WORKS, GENESIS_WORKS)):
        for name, entry in sorted(entries.items()):
            frozen.append(_entry(arena, groups[group], name, entry.text.encode(), OBJ_BLOB))
    for group, entries in ((PEOPLE, GENESIS_PEOPLE), (WORKS, GENESIS_WORKS)):
        for name, entry in sorted(entries.items()):
            if entry.refs:
                frozen.append(_genesis_table(arena, relations, group, name, entry))
    for handle in frozen:
        arena.set_flags(handle, OFLAG_RO)  # the flag states the intent as data


def _module(arena: ObjectArena, parent: int, name: str) -> int:
    return arena.new_object(parent, name, OBJ_MODULE, MODULE_SIZE)


def _entry(arena: ObjectArena, parent: int, name: str, text: bytes, kind: int) -> int:
    handle = arena.new_object(parent, name, kind, _round8(len(text)))
    arena.set_payload(handle, text)
    return handle


def _genesis_table(
    arena: ObjectArena, relations: int, group: str, name: str, entry: Entry
) -> int:
    """One entry's refs as a relation table (docs/32 2), named by its path."""
    records = []
    for role, target, index in entry.refs:
        role_handle = _resolve(arena, f"{HONORS}/{ROLES}/{role}")
        records.append(REF_RECORD.pack(_resolve(arena, target), role_handle, 0, 0, index))
    path = ".".join((HONORS, group, name))
    table = b"".join(records)
    handle = arena.new_object(relations, path, OBJ_SPEC, _round8(len(table)))
    arena.set_payload(handle, table)
    return handle


def _resolve(arena: ObjectArena, path: str) -> int:
    """The object at `path` (slash-separated from the root), or a refusal."""
    node = arena.root_handle
    for segment in path.split("/"):
        node = arena.find(node, segment)
        if node == HANDLE32_NONE:
            raise ValueError(f"no object at {path!r}")
    return node


def _record(handles: dict[int, int], ref: tuple[int, int, int, int, int]) -> bytes:
    target, role, index, kind, binding = ref
    return REF_RECORD.pack(
        _handle(handles, target), _handle(handles, role), kind, binding, index
    )


def _handle(handles: dict[int, int], source: int) -> int:
    """The world handle as its arena handle (index, gen), zero-extended into the
    record's 64-bit field. A target the walk never placed is a dangling ref, and
    the export refuses it."""
    if source is None or source == HANDLE_NONE_64:
        return HANDLE_NONE_64
    if source not in handles:
        raise ValueError(f"dangling ref: no arena object for world handle {source}")
    return handles[source]


def _round8(size: int) -> int:
    """Every payload is 8-aligned room (ground.h: a record's payload length is)."""
    return (size + 7) & ~7
