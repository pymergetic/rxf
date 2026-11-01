"""The links a client talks to a live image over (docs/19: one channel, several providers).

A link is two byte streams, `write` toward the image and `read` from it. Frames do not care what
carries them:

- `RingLink`: the two rings of the live area in a memory the peer can read and write (a
  shared-memory segment, the memory of a machine in a test). The doorbell is the head word.
- `FdLink`: a file descriptor, which is a serial line, a pty, a pipe or a socket. Whatever drains
  the UART into the rings (the leaf `uart.pump` of the image) is on the other side.

`open_link` makes one from a text, which is what the commands take:

    pty:/dev/pts/3      a pty or serial device (``tty:`` is the same), optionally ``:115200``
    fd:5                an open descriptor of this process
    shm:name            a shared-memory segment that a host laid out (`core.live.Area.shared`)
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import select
import struct
import sys
import termios
import threading
import tty
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from multiprocessing import resource_tracker, shared_memory
from typing import Protocol

from .client import Client

# the words of the live area (core/live/live.h)
RX_BASE, RX_CAPACITY, TX_BASE, TX_CAPACITY = 88, 96, 104, 112
RX_HEAD, RX_TAIL, TX_HEAD, TX_TAIL = 120, 124, 128, 132
ARENA_COMMITTED, GROW_REQUEST = 224, 232
AREA_BYTES = 4096  # the area of a block laid out by `core.live.Area`: its first page

# The head of a linked block (the facts a peer maps it by), written at the end of the block's
# segment by whoever laid the image out (`server/live/runner.py`): magic, version, then the
# addresses the image runs at and works in. The block starts at offset 0 of its segment, so the
# mapping of a peer is the image's own memory map with nothing shifted.
_HEAD = struct.Struct("<4sI12Q")
HEAD_MAGIC = b"RXLK"
HEAD_VERSION = 1
HEAD_SIZE = _HEAD.size


class LinkError(ValueError):
    """The text names no link."""


@dataclass(frozen=True)
class Head:
    """The facts of the linked block behind a link: what a peer maps it for (docs/19).

    `rxf server serve --link=shm:NAME` writes this at the end of the block it lays out in the
    segment `NAME`. The addresses are the image's own (computed by `tools/native/link.py` from
    `block`): mapping the segment at `block` in another process gives that process the very
    memory map the image runs in, so its interpreter (`run`) and its live leaf (`pump`) run
    there unchanged. `pump_slot` is the address the linker gave that leaf — the fallback of a
    function-table slot that the image has not filled in yet.
    """

    block: int  # the address the block is linked at (its memory map's base)
    area: int  # the live area of the image (core/live/live.h)
    context: int  # the runtime context its code runs on (native/stdlib_runtime.h)
    run: int  # the interpreter's entry (tools/native/link.py)
    args: int  # where a run reads its arguments
    out: int  # where a run writes its result
    pump: int  # the leaf that serves the rings (native/graph.h)
    pump_slot: int  # the address the linker gave that leaf
    arena_at: int  # the region the front end compiles in (live.front.Front)
    arena_cap: int
    work_at: int  # the memory of one request (the work region of the live block)
    work_cap: int

    def pack(self) -> bytes:
        return _HEAD.pack(
            HEAD_MAGIC,
            HEAD_VERSION,
            self.block,
            self.area,
            self.context,
            self.run,
            self.args,
            self.out,
            self.pump,
            self.pump_slot,
            self.arena_at,
            self.arena_cap,
            self.work_at,
            self.work_cap,
        )

    @staticmethod
    def read(block: bytes | memoryview) -> Head | None:
        """The head at the end of a block's bytes; None when the block carries none — the
        `core.live` layout serves frames and has no code to run, so no peer can drive it."""
        words = bytes(block[-HEAD_SIZE:])
        if len(words) < HEAD_SIZE:
            return None
        magic, version, *facts = _HEAD.unpack(words)
        if magic != HEAD_MAGIC or version != HEAD_VERSION:
            return None
        return Head(*facts)


class Link(Protocol):
    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes: ...

    def close(self) -> None: ...


class Memory(Protocol):
    """Memory by address: the memory of a machine, or a mapping with a base."""

    def read(self, address: int, size: int) -> bytes: ...

    def write(self, address: int, data: bytes) -> None: ...


class Mapped:
    """A buffer whose first byte is at the address `base`."""

    def __init__(self, buffer: memoryview, base: int) -> None:
        self.buffer = buffer
        self.base = base

    def read(self, address: int, size: int) -> bytes:
        at = address - self.base
        return bytes(self.buffer[at : at + size])

    def write(self, address: int, data: bytes) -> None:
        at = address - self.base
        self.buffer[at : at + len(data)] = data


class RingLink:
    """The rings of the live area at `area` in `memory`, as the peer sees them.

    `gate`, when given, is the lock of the memory behind the rings (`SegmentGate` for a
    shared-memory segment): the channel's one-request-at-a-time gate travels with the link, so
    every peer of one image takes the same lock (`console/world.py`, `live.client.Client`).
    """

    def __init__(
        self, memory: Memory, area: int, gate: AbstractContextManager[None] | None = None
    ) -> None:
        self.memory = memory
        self.area = area
        self.gate: AbstractContextManager[None] = (
            gate if gate is not None else contextlib.nullcontext()
        )
        self.rx, self.rx_capacity = self._pair(RX_BASE)
        self.tx, self.tx_capacity = self._pair(TX_BASE)

    def _pair(self, offset: int) -> tuple[int, int]:
        return struct.unpack("<QQ", self.memory.read(self.area + offset, 16))

    def _word(self, offset: int) -> int:
        return struct.unpack("<I", self.memory.read(self.area + offset, 4))[0]

    @staticmethod
    def _wrapped(start: int, count: int, capacity: int) -> list[tuple[int, int, int]]:
        out = []
        done = 0
        while done < count:
            at = (start + done) & (capacity - 1)
            take = min(count - done, capacity - at)
            out.append((at, done, done + take))
            done += take
        return out

    def free(self) -> int:
        return self.rx_capacity - (
            (self._word(RX_HEAD) - self._word(RX_TAIL)) & 0xFFFFFFFF
        )

    def arena_committed(self) -> int:
        """How much of the arena region is usable (live.h `arena_committed`; 0: all of it)."""
        return struct.unpack("<Q", self.memory.read(self.area + ARENA_COMMITTED, 8))[0]

    def grow_request(self) -> int:
        """The end offset the image asks its arena to be committed to after a refusal for lack of
        room (live.h `grow_request`, docs/19); 0 when it asks for nothing."""
        return struct.unpack("<Q", self.memory.read(self.area + GROW_REQUEST, 8))[0]

    def write(self, data: bytes) -> None:
        if len(data) > self.free():
            raise OverflowError("the ring to the image is full")
        head = self._word(RX_HEAD)
        for at, start, stop in self._wrapped(head, len(data), self.rx_capacity):
            self.memory.write(self.rx + at, data[start:stop])
        # the head moves last: it is the doorbell
        self.memory.write(
            self.area + RX_HEAD, struct.pack("<I", (head + len(data)) & 0xFFFFFFFF)
        )

    def read(self) -> bytes:
        head, tail = self._word(TX_HEAD), self._word(TX_TAIL)
        count = (head - tail) & 0xFFFFFFFF
        out = b"".join(
            self.memory.read(self.tx + at, stop - start)
            for at, start, stop in self._wrapped(tail, count, self.tx_capacity)
        )
        if count:
            self.memory.write(self.area + TX_TAIL, struct.pack("<I", head))
        return out

    def close(self) -> None:
        """The memory belongs to whoever gave it."""


def segment_of(spec: str) -> str | None:
    """The segment name a `shm:` spec names; None for the link kinds that stay in one process
    (`pty:`, `tty:`, `fd:` carry frames only — the machine that runs the image is where its
    runner is)."""
    return spec[len("shm:") :] if spec.startswith("shm:") else None


class SegmentGate:
    """The lock of a shared-memory segment: one request at a time, in every process that maps it.

    `flock` on a descriptor of the segment, held open here: the lock is on the file behind the
    name, so two processes that map the same block take the same lock, and a process that dies
    takes its lock with it. It is reentrant in this process (an operation that enters again
    through the seam of another face waits against nobody), and it is what makes "the rings
    serialize one request at a time" true across processes: a request that arrives mid-flight of
    another lands after it, never inside it.
    """

    def __init__(self, fd: int) -> None:
        self._fd = os.dup(fd)  # the gate's own descriptor: it outlives the opener's
        self._held = threading.RLock()
        self._depth = 0

    def __enter__(self) -> None:
        self._held.acquire()
        if self._depth == 0:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        self._depth += 1

    def __exit__(self, *problem: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        self._held.release()

    def close(self) -> None:
        """The gate is over: its descriptor goes and no lock of it stays behind."""
        with contextlib.suppress(OSError):
            os.close(self._fd)


def create_segment(name: str, size: int) -> shared_memory.SharedMemory:
    """A fresh shared-memory segment of `size` bytes: this process's to lay out and to unlink.

    One link has one runner across processes too: a name that is already taken raises
    `FileExistsError` and keeps its runner — the segment is never shared and never stolen.
    """
    if sys.version_info >= (3, 13):
        return shared_memory.SharedMemory(name=name, create=True, size=size, track=False)
    segment = shared_memory.SharedMemory(name=name, create=True, size=size)
    with contextlib.suppress(Exception):
        resource_tracker.unregister(getattr(segment, "_name"), "shared_memory")  # noqa: B009
    return segment


def attach_segment(name: str) -> shared_memory.SharedMemory:
    """A segment that exists, without the resource tracker of this process taking it for its own."""
    if sys.version_info >= (3, 13):
        return shared_memory.SharedMemory(name=name, track=False)
    segment = shared_memory.SharedMemory(name=name)
    with contextlib.suppress(Exception):
        resource_tracker.unregister(getattr(segment, "_name"), "shared_memory")  # noqa: B009
    return segment


class SharedMemory(RingLink):
    """A block that a host process laid out in a shared-memory segment (`core.live.Area.shared`)."""

    def __init__(self, name: str) -> None:
        self.segment = shared_memory.SharedMemory(name=name)
        # the attach does not own it: the tracker of this process must not unlink it at exit
        with contextlib.suppress(Exception):
            resource_tracker.unregister(getattr(self.segment, "_name"), "shared_memory")  # noqa: B009
        buffer = self.segment.buf
        assert buffer is not None
        (arena,) = struct.unpack_from("<Q", buffer, 8)
        super().__init__(Mapped(buffer, arena - AREA_BYTES), arena - AREA_BYTES)

    def close(self) -> None:
        self.memory = None  # type: ignore[assignment]
        self.segment.close()


class FdLink:
    """A descriptor: a serial line, a pty, a pipe or a socket. Never blocks on reading."""

    def __init__(self, fd: int, *, own: bool = False) -> None:
        self.fd = fd
        self.own = own
        os.set_blocking(fd, False)

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.fd, view) :]
            except BlockingIOError:
                select.select([], [self.fd], [], 1.0)

    def read(self) -> bytes:
        try:
            return os.read(self.fd, 1 << 16)
        except BlockingIOError:
            return b""
        except OSError:  # a pty whose other side is closed
            return b""

    def close(self) -> None:
        if self.own:
            os.close(self.fd)


BAUD = {
    9600: termios.B9600,
    19200: termios.B19200,
    38400: termios.B38400,
    57600: termios.B57600,
    115200: termios.B115200,
}


def open_device(path: str, baud: int | None = None) -> FdLink:
    """A serial device or pty in raw mode (no echo, no translation, eight bits)."""
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    if os.isatty(fd):
        tty.setraw(fd)
        if baud is not None:
            attributes = termios.tcgetattr(fd)
            attributes[4] = attributes[5] = BAUD[baud]
            termios.tcsetattr(fd, termios.TCSANOW, attributes)
    return FdLink(fd, own=True)


def open_link(spec: str) -> Link:
    """A link from `pty:PATH[:BAUD]`, `tty:PATH[:BAUD]`, `fd:N` or `shm:NAME`."""
    kind, _, rest = spec.partition(":")
    if kind in ("pty", "tty"):
        path, _, baud = rest.partition(":")
        return open_device(path, int(baud) if baud else None)
    if kind == "fd":
        return FdLink(int(rest))
    if kind == "shm":
        return SharedMemory(rest)
    raise LinkError(f"no link {spec!r}: use pty:PATH, tty:PATH, fd:N or shm:NAME")


def connect(
    spec: str | Link, *, timeout: float = 30.0, pump: Callable[[], object] | None = None
) -> Client:
    """A client over a link or the text of one."""
    link = open_link(spec) if isinstance(spec, str) else spec
    return Client(link, pump=pump, timeout=timeout)
