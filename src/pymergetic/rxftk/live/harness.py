"""Hosts for a live image that run in the test process: the host build of the live layer.

`HostImage` puts the bytes of an image in a live area (core.live.Area) and runs the code of the
native leaf on it each time the client waits. It is the reference the native machines are held to.
"""

from __future__ import annotations

import struct

from pymergetic.rxftk.core import live
from pymergetic.rxftk.core.arena import OBJECT_HEADER_SIZE, SUPERBLOCK_SIZE
from pymergetic.rxftk.core.object import Image

from . import arena_image
from .client import Client

MiB = 1 << 20


def table_slots(image: bytes, headroom: int = 4) -> int:
    """A lookup table for the handles a v7 arena can hold, twice over.

    The slot table is the first object of the arena at its fixed place (docs/32 1:
    the anchor a cold attach needs) and its head carries the count and capacity.
    """
    count, capacity = struct.unpack_from(
        "<II", image, SUPERBLOCK_SIZE + OBJECT_HEADER_SIZE
    )
    assert count <= capacity, "the slot table never publishes past its capacity"
    want = 2 * capacity * headroom // 4
    slots = 1024
    while slots < want:
        slots *= 2
    return slots


def fit(image: bytes) -> int:
    """An arena that holds this image with room to grow, sized as the linker gives it
    (link.LiveSpec: the image and a megabyte of headroom, a page at a time)."""
    page = 4096
    return max(8 * MiB, (len(image) + (1 << 20) + page - 1) // page * page)


class HostImage:
    def __init__(
        self,
        image: bytes,
        *,
        arena: int | None = None,
        log: int = 4 * MiB,
        stage: int = 4 * MiB,
        work: int = 4 * MiB,
        emit: int | None = None,
        ring: int = 1 << 16,
        slots: int | None = None,
        committed: int = 0,
        batch: int = 64,
    ) -> None:
        sized = fit(image)
        self.area = live.Area(
            image,
            sized if arena is None else arena,
            log,
            stage,
            work,
            sized if emit is None else emit,
            ring,
            slots or table_slots(image),
            committed,
        )
        self._batch = batch
        self.client = Client(self, pump=self.pump)

    # the link the client talks over
    def write(self, data: bytes) -> None:
        if not self.area.rx_write(data):
            raise OverflowError("the ring to the image is full")

    def read(self) -> bytes:
        return self.area.tx_read(1 << 20)

    def pump(self) -> int:
        return self.area.pump(self._batch)

    def image(self, like: Image | None = None) -> Image:
        """The arena as it is, loaded into a host image (the v7 reader, ``arena_image.load``;
        `like` is the world it came from, for the length of its payloads)."""
        return arena_image.load(self.area.arena_bytes(), like)


def replay(image: bytes, delta: bytes, like: Image | None = None) -> Image:
    """The image after the physical delta, applied by the host build of the live layer.

    `image` is a v7 arena (``arena_image.arena_of`` of a model); the answer is the model of the
    arena the apply left, with the payload lengths of `like` (the model `image` was made from).

    What `apply` does inside a running image, with the same code: the delta is staged, its digest
    checked, replayed on the journal, and the result linked. A refusal is `Refused`.
    """
    size = (3 * len(image) + 4 * MiB + MiB - 1) // MiB * MiB
    host = HostImage(image, arena=size, emit=size)
    host.client.apply(delta)
    return host.image(like)
