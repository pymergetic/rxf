"""The machine of a linked image behind a link: the same one image, mapped at its own addresses.

`rxf server serve starter --link=shm:NAME` lays its image out in ONE block of host memory in a
shared-memory segment and writes the facts of that block at the end of it (the head
`live.link.Head` reads): the addresses the image runs at, its live area, the regions of its front
end, and the leaf that serves the rings. This module maps that block **at those addresses** in
this process and runs its code — the interpreter on one program (`run`), the image's own live
leaf on a frame (`pump`) — so a second terminal compiles and installs through the front end of
the image behind the link, in the very memory that front end lives in (docs/19, "who runs when").

"One link has one runner" is untouched: the runner is whoever registered the world (the serve).
What maps here is not a second image and not a second registration — it is the same one block,
driven from the other side of the link, one request at a time behind the lock of the link
(`live.link.SegmentGate`): a request that arrives mid-flight of another lands after it, never
inside it. Nothing executes on behalf of the image: the block's own interpreter and its own leaf
run, from the block, at the addresses `tools/native/link.py` linked it at. The block is shared
memory (`MAP_SHARED`): what one mapper does, every mapper sees.

A block that is not a linked form (the `core.live` layout: rings and an arena, no code to run)
gives no machine — `attach` answers None and the frames face speaks to it over the wire alone.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import mmap
import os
import struct
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.front import Front, Region
from pymergetic.rxftk.live.link import (
    HEAD_SIZE,
    Head,
    LinkError,
    RingLink,
    SegmentGate,
    attach_segment,
    segment_of,
)

PROT = mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC  # the image runs its own code there
MAP_SHARED = 0x01  # POSIX: every mapper sees the same memory
MAP_FIXED_NOREPLACE = 0x100000  # Linux 4.17: take these addresses or none (never clobber)

_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.mmap.restype = ctypes.c_void_p
_LIBC.mmap.argtypes = [
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_long,
]
_LIBC.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]


class _Handle(ctypes.Structure):
    """`rxf_object_handle` (native/stdlib_runtime.h): the id a run names a program by."""

    _fields_ = [
        ("object_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("offset", ctypes.c_uint64),
    ]


_RUN = ctypes.CFUNCTYPE(
    ctypes.c_uint32,  # rxf_graph_run(...)
    ctypes.c_void_p,  # rxf_runtime_context *
    _Handle,  # rxf_object_handle, by value
    ctypes.c_uint64,  # args
    ctypes.c_uint64,  # argc
    ctypes.c_void_p,  # out
)
_LEAF = ctypes.CFUNCTYPE(
    ctypes.c_uint32,  # a leaf (context, argument)
    ctypes.c_void_p,
    ctypes.c_void_p,
)


@functools.cache
def _run_at(address: int) -> Any:
    """The interpreter at `address`, as a callable of this process — the entry `link.py`
    computes from the Code of the image (a compaction moves it with everything else that does
    not run)."""
    return _RUN(address)


@functools.cache
def _leaf_at(address: int) -> Any:
    """The leaf at `address`, as a callable of this process."""
    return _LEAF(address)


def _map_at(fd: int, size: int, at: int) -> memoryview:
    """The segment mapped at `at`: the addresses the image was linked at, in this process.

    Mapping at fixed addresses is what makes the image's own code runnable from another mapping
    (`run`, `pump`) — a mapping anywhere else would move every address the image names. Fixed
    addresses are taken, never clobbered (`MAP_FIXED_NOREPLACE`): a process that already has
    that memory is refused plainly instead of having it overwritten.
    """
    if sys.platform != "linux":
        raise LinkError(
            f"cannot map a linked image at its own addresses on {sys.platform}: this needs "
            "MAP_FIXED_NOREPLACE (Linux 4.17 or newer)"
        )
    address = _LIBC.mmap(at, size, PROT, MAP_SHARED | MAP_FIXED_NOREPLACE, fd, 0)
    if not address or address == ctypes.c_void_p(-1).value:
        error = ctypes.get_errno()
        raise LinkError(
            f"cannot map the image at 0x{at:x} ({os.strerror(error)}): this process already has "
            "that memory, and a linked image runs at its own addresses only"
        )
    return memoryview((ctypes.c_char * size).from_address(address)).cast("B")


def _unmap(at: int, size: int) -> None:
    _LIBC.munmap(at, size)


class Machine:
    """One linked image at its own addresses, and the channel of its link on top.

    The face is the machine of the front end (`live.front.Machine`) plus the channel it runs the
    image's traffic on: `read`/`write` are memory by address (what a request is written through),
    `run` is the interpreter on one program, `pump` is the image's own live leaf serving its
    rings. `transaction` is the gate of the link — one request at a time across every process
    that maps the block — and it is what a world carries (`console/world.py`).
    """

    def __init__(
        self,
        head: Head,
        memory: memoryview | mmap.mmap,
        drop: Callable[[], None],
        gate: SegmentGate | None = None,
    ) -> None:
        self.head = head
        self._memory = memory
        self._drop = drop
        self._gate: Any = gate if gate is not None else contextlib.nullcontext()
        self._linked = {head.pump: head.pump_slot}
        self._closed = False
        self.client = Client(RingLink(self, head.area, gate=gate), pump=self.pump)
        self.front = Front(
            self.client,
            self,
            Region(head.arena_at, head.arena_cap),
            Region(head.work_at, head.work_cap),
        )

    # -- the machine of the front end (live.front.Machine) -----------------------------------

    def read(self, address: int, size: int) -> bytes:
        self._check_open()
        at = address - self.head.block
        return bytes(self._memory[at : at + size])

    def write(self, address: int, data: bytes) -> None:
        self._check_open()
        at = address - self.head.block
        self._memory[at : at + len(data)] = data

    def slot(self, identity: int) -> int:
        """Where a leaf is now: the address in the function table of the context in memory (the
        image moves its own code when it compacts itself)."""
        count, table = struct.unpack("<2Q", self.read(self.head.context + 16, 16))
        for k in range(count):
            slot = self.read(table + 24 * k, 24)
            if struct.unpack_from("<Q", slot)[0] == identity:
                return struct.unpack_from("<Q", slot, 16)[0] or self._linked[identity]
        raise KeyError(identity)

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]:
        """The interpreter of the image on one program (`graph.run`): its status and its value.
        A run is not a frame: this call IS this process running the image (docs/19)."""
        self._check_open()  # closed long ago: the gate's descriptor is gone with the machine
        with self._gate:
            self._check_open()  # inside the gate: a close in another thread has finished or waits
            self.write(self.head.args, arguments or bytes(8))
            status = _run_at(self.head.run)(
                self.head.context,
                _Handle(identity, 1, 0),
                self.head.args,
                len(arguments) // 8,
                self.head.out,
            )
            return status, struct.unpack("<Q", self.read(self.head.out, 8))[0]

    def pump(self) -> int:
        """The image's own live leaf (leaf 29, `native/live.cpp`) serves its rings."""
        self._check_open()  # closed long ago: the gate's descriptor is gone with the machine
        with self._gate:
            self._check_open()  # inside the gate: the block cannot be unmapped under the leaf
            return _leaf_at(self.slot(self.head.pump))(self.head.context, self.head.area)

    def close(self) -> None:
        """The machine is done here: the mapping goes, the segment stays with its runner.

        The mapping goes only through the gate: an operation of another thread that is running
        the image's code in this block finishes first, and one that arrives afterwards finds the
        machine closed (`_check_open`) and refuses. Unmapping under a running leaf is a
        segmentation fault, not an error.
        """
        if self._closed:
            return
        with self._gate:
            if self._closed:
                return
            self._closed = True
            self._drop()
        close = getattr(self._gate, "close", None)
        if close is not None:
            close()

    # -- the gate of the link ------------------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One operation on the image at a time — in this process and in every other that maps
        the block. The gate travels with the world (`console/world.py`), so no caller forgets
        it: a request of another lands after this operation, never between its two requests."""
        with self._gate:
            yield

    # -- internals ----------------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "the machine is closed: this process no longer runs the image"
            )

    def __enter__(self) -> Machine:
        return self

    def __exit__(self, *problem: object) -> None:
        self.close()


def head(spec: str) -> Head | None:
    """The facts of the linked image behind SPEC, or None when there is no machine to drive:
    the spec names no shared segment, nothing is laid out there, or the block there is not a
    linked form (the `core.live` layout has no code to run)."""
    name = segment_of(spec)
    if name is None:
        return None
    try:
        segment = attach_segment(name)
    except (OSError, LinkError):
        return None
    try:
        buffer = segment.buf
        assert buffer is not None
        return Head.read(buffer)
    finally:
        segment.close()


def attach(spec: str) -> Machine | None:
    """The machine of the linked image behind SPEC, mapped at its own addresses in this process.

    The block a runner laid out is mapped at the addresses the image runs at (the head of the
    block says which), so its interpreter and its live leaf run HERE on the memory the image was
    linked into — the same one image, driven from this side of the link. None when `head` finds
    no machine to drive; the mapping is this machine's to unmap (`close`), the segment is the
    runner's.
    """
    name = segment_of(spec)
    if name is None:
        return None
    try:
        segment = attach_segment(name)
    except (OSError, LinkError):
        return None
    buffer = segment.buf
    assert buffer is not None
    found = Head.read(buffer)
    if found is None:
        segment.close()
        return None
    size = segment.size
    try:
        memory = _map_at(getattr(segment, "_fd"), size, found.block)  # noqa: B009
    except BaseException:
        segment.close()
        raise
    gate = SegmentGate(getattr(segment, "_fd"))  # noqa: B009
    segment.close()  # the gate has its own descriptor; the mapping is from `_map_at`
    return Machine(found, memory, lambda: _unmap(found.block, size), gate)
