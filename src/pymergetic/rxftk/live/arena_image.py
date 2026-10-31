"""The v7 arena as the model's Image, and back (docs/32 5.5).

The model (``core.object.Image``) is the world: handles in the order the world made them, the
typed payloads, the references as handles. The running image is the v7 one-arena image (docs/32):
records, a slot table, the semantic rows in ``sys/specs``, the relation tables in
``sys/relations``. This module is the seam between the two for the host's commands:

- ``load(raw)`` reads an arena through ``pymergetic.rxfb`` - the C core's read-only, zero-copy
  views - and maps what it sees onto the model with ``Image.add``. It parses nothing itself: every
  record, name, spec row and reference table comes from the C reader, and this function only
  decides what the model calls them (the inverse of ``tools/native/arena_export.py``).
- ``arena_of(model)`` is the writer's half - the export ``link`` places - so a caller can hand
  the model to a host build of the live layer, and ``digest_of`` is the hash the live layer
  answers for it (docs/32 5.4).

``load(arena_of(model))`` is the model again, as far as the arena carries it: the objects by path,
their kinds, states, owners, generations, indices, types, payloads (the reference slots as
handles of the model) and references. What the arena adds around the world (``sys/``, the
``honors/`` core) is not the world's and is not loaded; ``programs.filled`` has already put the
built bytes in the arena, so the loaded model carries them as stored data.

The arena keeps the room of a payload, not its length (docs/32 1.2), so the length of a
variable-length value is not in the bytes. A fixed layout gives it back (its prefix); for the rest
the caller that still holds the world the arena came from passes it as ``like``. The arena is the
*filled* world (``programs.filled``: the built bytes of the recipes are in it), so an object that
agrees with the filled ``like`` is ``like``'s own - its length, and its unfilled form.
"""

from __future__ import annotations

import hashlib
import importlib
import struct
from collections.abc import Buffer
from typing import Any

from pymergetic import rxfb
from pymergetic.rxftk.core.arena import (
    FLAG_KIND_MASK,
    FLAG_KIND_SHIFT,
    FLAG_OWNER_MASK,
    FLAG_OWNER_SHIFT,
    FLAG_STATE_MASK,
    FLAG_STATE_SHIFT,
    MODULE_SIZE,
    OBJ_MODULE,
)
from pymergetic.rxftk.core.object import Image

NONE = (1 << 64) - 1  # the model's "no handle"
# The arena's own subtrees below its root: not the world's, not loaded.
SKIPPED = frozenset({"sys", "honors"})


def arena_of(model: Image) -> bytes:
    """The v7 arena of a model: what ``link`` places for it (docs/32 2), byte for byte.

    The writer lives with the linker (``tools/native``, a checkout's scripts), so it is reached
    the way ``binary.package`` reaches the linker."""
    from pymergetic.rxftk.binary import package

    return package.link_module().arena_of(model)


def digest_of(model: Image) -> str:
    """The digest the live layer answers for a model: the hash of its canonical bytes
    (docs/32 5.4)."""
    return hashlib.sha256(arena_of(model)).hexdigest()


def load(raw: Buffer, like: Image | None = None) -> Image:
    """The model of a v7 arena: the world the arena carries, as an ``Image``.

    ``like`` is the world the arena was made from or a predecessor of it, when the caller has
    one: an object whose bytes agree with it keeps its payload as ``like`` holds it (the length
    the arena only knows as room, and the recipes' built bytes left out). A refusal of the C reader (``rxfb.BadImage``: not an arena, damaged) passes through."""
    arena = rxfb.attach(raw)
    made = None if like is None else _filled(like)
    image = Image.empty()  # every model starts with the ground (Type, Field, ...)
    ground = image.handle_count
    placed: dict[int, int] = {arena.root.handle: 0}
    todo: list[
        tuple[Any, int]
    ] = []  # (the arena object, its model handle): wired after the walk
    _walk(arena.root, 0, image, placed, todo, top=True)
    tables = _tables(arena)
    # A payload's reference slots are read through the layout of the object's type, and a layout
    # is read from the Field objects below the Type: those (typed by the ground) are wired first.
    first = [
        (s, h) for s, h in todo if h >= ground and _typed_by_ground(s, placed, ground)
    ]
    rest = [
        (s, h)
        for s, h in todo
        if h >= ground and not _typed_by_ground(s, placed, ground)
    ]
    for source, handle in first + rest:
        _wire(source, handle, image, placed, tables, like, made)
    entry = arena.entry
    if entry is not None and entry.handle in placed:
        image.entry = placed[entry.handle]
    return image


def _walk(
    parent: Any,
    handle: int,
    image: Image,
    placed: dict[int, int],
    todo: list[tuple[Any, int]],
    *,
    top: bool,
) -> None:
    """The children in chain order, depth first: every world object earns its model handle."""
    for child in parent.children():
        if top and child.name in SKIPPED:
            continue
        spec = child.spec()
        state = (spec.flags >> FLAG_STATE_SHIFT) & FLAG_STATE_MASK
        made = image.resolve(child.path())
        if made is None:
            made = image.add(
                handle,
                child.name or "",
                (spec.flags >> FLAG_KIND_SHIFT) & FLAG_KIND_MASK,
                NONE,  # the type is named by a handle the walk has not made yet: wired below
                _data(child),
                [],
                state,
                (spec.flags >> FLAG_OWNER_SHIFT) & FLAG_OWNER_MASK,
                spec.generation,
                spec.index,
            )
        else:  # the ground: in the model already, and its bytes carry the model's own handles
            image.set_state(made, state)
        placed[child.handle] = made
        todo.append((child, made))
        _walk(child, made, image, placed, todo, top=False)


def _tables(arena: Any) -> dict[str, Any]:
    """Every relation table of the arena by the path it is named after, read once: asking the
    reader for one object's table walks the chain, which over every object is quadratic."""
    try:
        relations = arena.lookup("sys.relations")
    except rxfb.NotFound:
        return {}  # an image without refs
    return {table.name: table.table() for table in relations.children()}


def _typed_by_ground(source: Any, placed: dict[int, int], ground: int) -> bool:
    kind = source.spec().type
    return kind is not None and placed[kind] < ground


def _data(source: Any) -> bytes:
    """The world's own bytes of an object: a module's payload opens with the arena's chain
    head."""
    payload = bytes(source.payload)
    return payload[MODULE_SIZE:] if source.kind == OBJ_MODULE else payload


def _wire(
    source: Any,
    handle: int,
    image: Image,
    placed: dict[int, int],
    tables: dict[str, Any],
    like: Image | None,
    made: Image | None,
) -> None:
    """What names other objects (the type, the reference slots of the payload, the references)
    and the payload's length."""
    spec = source.spec()
    if spec.type is not None:
        image.set_type(handle, placed[spec.type])
    _payload(source, handle, image, placed, like, made)
    rows = [
        (
            placed[ref.target],
            None if ref.role is None else placed[ref.role],
            ref.index,
            ref.kind,
            ref.binding,
        )
        for ref in tables.get(source.path(), ())
    ]
    if rows:
        image.set_refs(handle, rows)


def _payload(
    source: Any,
    handle: int,
    image: Image,
    placed: dict[int, int],
    like: Image | None,
    made: Image | None,
) -> None:
    """The payload as the model holds it: its length, then its reference slots as model handles."""
    got = image.get(handle)
    data = bytearray(got["payload"])
    if not data:
        return
    was = bytes(data)
    layout = image.layout(got["type"]) if got["type"] != NONE else None
    if layout is not None and layout["error"]:
        layout = None
    slots: list[int] = []
    if layout is not None and not layout["raw"]:
        slots = [int(leaf["offset"]) for leaf in layout["leaves"] if leaf["reference"]]
    if layout is not None and layout["fixed"]:
        prefix = int(layout["prefix"])
        if prefix < len(data) and not any(data[prefix:]):
            del data[prefix:]
    elif like is not None and made is not None:
        _known(like, made, source.path(), data, slots)
    for at in slots:
        if at + 8 > len(data):
            continue
        (word,) = struct.unpack_from("<Q", data, at)
        if word == NONE:
            continue
        if word not in placed:
            raise rxfb.BadImage(f"{source.path()}: a reference slot names no object")
        struct.pack_into("<Q", data, at, placed[word])
    if bytes(data) != was:
        image.set_payload(handle, bytes(data))


def _filled(model: Image) -> Image:
    """The model with the recipes' built bytes in it: the world as the arena carries it."""
    from pymergetic.rxftk.binary import package

    package.link_module()  # puts the native tools on the path
    return importlib.import_module("programs").filled(model)


def _known(
    like: Image, made: Image, path: str, data: bytearray, slots: list[int]
) -> None:
    """The payload as `like` holds it, when the arena's bytes are what the filled `like` holds:
    the room's tail is only zeros and everything before it agrees (the reference slots aside,
    which are handles of two different models)."""
    known, filled = like.resolve(path), made.resolve(path)
    if known is None or filled is None:
        return
    before, after = (
        bytes(like.get(known)["payload"]),
        bytes(made.get(filled)["payload"]),
    )
    if len(after) > len(data) or any(data[len(after) :]) or len(before) > len(after):
        return
    mine, theirs = bytearray(data[: len(after)]), bytearray(after)
    for at in slots:
        if at + 8 <= len(after):
            mine[at : at + 8] = theirs[at : at + 8] = bytes(8)
    if mine == theirs:
        del data[len(before) :]
