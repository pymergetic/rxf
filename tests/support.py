"""Helpers the tests share."""

from __future__ import annotations

import hashlib
import struct

import link

from pymergetic.rxftk.core.arena import ObjectArena
from pymergetic.rxftk.core.object import Image


def model_copy(model: Image) -> Image:
    """A copy of the model: the world's own form round-trips (the arena bytes
    are the v7 image and not what the model reads)."""
    return Image.from_bytes(model.to_bytes())


def digest_of(model: Image) -> str:
    """The digest of a model as the live layer answers it (docs/32 5.4): the
    hash of the canonical bytes."""
    return Bridge(model).digest()


def canonical_of(model: Image) -> bytes:
    """The canonical bytes of a model (docs/32 5.4)."""
    return Bridge(model).canonical()


def resolved(image: Image, path: str) -> int:
    """The handle of a path that must exist in the image."""
    found = image.resolve(path)
    assert found is not None, f"{path} does not resolve"
    return found


class Bridge:
    """The model as the oracle of the tests, and the map to the arena's handles.

    The model (core/object.py's Image) hands out the world's handles - plain
    ids in the order the world created them. The live channel answers the
    arena's handles - (gen << 20) | index, docs/32 2. The map between the two
    is tools/native/link.py's `arena_map`: "the path is the identity across
    images", derived on every build, never stored."""

    def __init__(self, model: Image):
        self.model = model
        self.arena = link.arena_of(model)
        self.handles = link.arena_map(model)
        # the world's root (handle 0) is not placed as an object: the arena's
        # root carries it (tools/native/arena_export.py's `placed`).
        self.handles.setdefault(0, self.arena_handle(2))

    def digest(self) -> str:
        """The canonical bytes' hash - what the live layer calls the digest
        (docs/32 5.4: the digest is over the canonical bytes)."""
        return hashlib.sha256(self.arena).hexdigest()

    def handle(self, world_handle: int) -> int:
        """The arena handle of a world handle: the name the channel speaks."""
        return self.handles[world_handle]

    def arena_handle(self, index: int) -> int:
        """The arena handle of a slot index (the generation comes off the slot
        entry the bytes carry)."""
        _kind, gen, _reserved, _slot = struct.unpack_from(
            "<HHIQ", self.arena, 4096 + 24 + 8 + 16 * index
        )
        return (gen << 20) | index

    def canonical(self) -> bytes:
        """The canonical bytes of the model (docs/32 5.4): the arena export."""
        return self.arena

    def value(self, world_handle: int) -> bytes:
        """The object's value as the arena carries it: the world's payload with
        the declared reference slots remapped to the arena's handles (tools/
        native/arena_export.py's _relink_payloads)."""
        info = self.model.get(world_handle)
        data = bytearray(info["payload"])
        if info["type"] == (1 << 64) - 1:
            return bytes(data)  # the world's root carries no type
        for leaf in self.model.layout(info["type"]).get("leaves") or []:
            if not leaf.get("reference"):
                continue
            at = int(leaf["offset"])
            if at + 8 > len(data):
                continue
            (was,) = struct.unpack_from("<Q", data, at)
            if was == (1 << 64) - 1:
                continue
            struct.pack_into("<Q", data, at, self.handles[was])
        return bytes(data)

    @staticmethod
    def entry_path(dump: bytes) -> str | None:
        """The entry of canonical bytes (docs/32 2: the superblock's entry): the
        path of the run, or None when the image has none. The field is a handle
        like every other (docs/32 2)."""
        entry = struct.unpack_from("<I", dump, 28)[0]
        if entry == 0xFFFFFFFF:
            return None
        return ObjectArena.attach(dump).path_of(entry)

    @property
    def count(self) -> int:
        """How many slots the canonical form has (the hello's `handles`)."""
        return struct.unpack_from("<I", self.arena, 4096 + 24)[0]

    @property
    def body_size(self) -> int:
        """The canonical form's claim (the hello's `body_size`)."""
        return struct.unpack_from("<Q", self.arena, 8)[0]
