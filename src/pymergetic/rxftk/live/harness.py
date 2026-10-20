"""Hosts for a live image that run in the test process: the host build of the live layer.

`HostImage` puts the bytes of an image in a live area (core.live.Area) and runs the code of the
native leaf on it each time the client waits. It is the reference the native machines are held to.
"""

from __future__ import annotations

from pymergetic.rxftk.core import live
from pymergetic.rxftk.core.object import Image

from .client import Client

MiB = 1 << 20


def table_slots(image: bytes, headroom: int = 4) -> int:
    """A lookup table for the handles an arena of this directory can hold, twice over."""
    probe = Image.from_bytes(image)
    chunks = max(probe.index_chunks, 1)
    directory = (chunks + 7) // 8 * 8
    want = 2 * directory * 4096 * headroom // 4
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

    def image(self) -> Image:
        """The arena as it is, loaded into a host image."""
        return Image.from_bytes(self.area.arena_bytes())


def replay(image: bytes, delta: bytes) -> Image:
    """The image after the physical delta, applied by the host build of the live layer.

    What `apply` does inside a running image, with the same code: the delta is staged, its digest
    checked, replayed on the journal, and the result linked. A refusal is `Refused`.
    """
    size = (3 * len(image) + 4 * MiB + MiB - 1) // MiB * MiB
    host = HostImage(image, arena=size, emit=size)
    host.client.apply(delta)
    return host.image()
