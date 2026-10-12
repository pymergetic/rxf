"""The client of a live image: reads and changes it through frames, whatever carries them.

A `Link` moves bytes (a harness that pumps the image, a pty, a socket, shared memory). The client
does not know which. Requests are answered one at a time; the answer carries the sequence number
of the request.
"""

from __future__ import annotations

import hashlib
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import frames
from .frames import Frame

REASONS = {
    0: "none",
    1: "the bytes are not a delta",
    2: "the base digest is not the digest of the image",
    3: "a path does not resolve",
    4: "an object is already there",
    5: "a name cannot be used",
    6: "the arena has no room",
    7: "the undo log is full",
    8: "the work memory is full",
    9: "something points at an object that is gone",
    10: "root, Type and Field cannot be removed",
    11: "the image does not link after the delta",
    12: "a type is more than the live layer reads",
    13: "an object is pinned",
    14: "a size outside what is committed and what exists",
    15: "the image has no context to link",
}


class Link(Protocol):
    """Moves bytes to and from the image. `read` never blocks."""

    def write(self, data: bytes) -> None: ...

    def read(self) -> bytes: ...


class LiveError(Exception):
    """The image answered with a status other than ok."""

    def __init__(self, op: int, status: int, payload: bytes = b"") -> None:
        self.op = op
        self.status = status
        self.payload = payload
        super().__init__(f"op 0x{op:02x}: {frames.STATUS_NAMES.get(status, status)}")


class Refused(LiveError):
    """A staged delta was not applied; the image is as it was."""

    def __init__(self, op: int, payload: bytes) -> None:
        at, reason = struct.unpack("<II", payload[:8])
        self.at = at
        self.reason = reason
        super().__init__(op, frames.APPLY, payload)
        self.args = (f"delta refused at operation {at}: {REASONS.get(reason, reason)}",)


class Declined(LiveError):
    """A request that was well formed and was not done (a size out of range, a pinned object)."""

    def __init__(self, op: int, payload: bytes) -> None:
        _, reason = struct.unpack("<II", payload[:8])
        self.reason = reason
        super().__init__(op, frames.REFUSED, payload)
        self.args = (f"op 0x{op:02x} refused: {REASONS.get(reason, reason)}",)


class Timeout(LiveError):
    def __init__(self, op: int) -> None:
        super().__init__(op, -1)
        self.args = (f"no answer to op 0x{op:02x}",)


@dataclass(frozen=True)
class Hello:
    version: int
    handles: int
    body_size: int
    arena_capacity: int
    stage_capacity: int
    emit_capacity: int
    work_capacity: int
    commits: int
    rejects: int
    committed: int


@dataclass(frozen=True)
class Compacted:
    handles: int
    body_size: int
    digest: str
    reclaimed: int


@dataclass(frozen=True)
class Cell:
    handle: int
    type: int
    parent: int
    payload_size: int
    generation: int
    flags: int
    ref_count: int
    index: int
    name: str

    @property
    def kind(self) -> int:
        return self.flags & 0xFF


NONE = 0xFFFFFFFFFFFFFFFF


class Client:
    """`pump`, when given, is called while waiting (a harness that runs the image in the same thread)."""

    def __init__(
        self,
        link: Link,
        pump: Callable[[], object] | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._link = link
        self._pump = pump
        self._timeout = timeout
        self._decoder = frames.Decoder()
        self._sequence = 0
        self._late: dict[int, Frame] = {}

    def call(
        self,
        op: int,
        payload: bytes = b"",
        argument: int = 0,
        *,
        accept: tuple[int, ...] = (frames.OK,),
    ) -> Frame:
        self._sequence = (self._sequence + 1) & 0xFFFFFFFF
        wanted = self._sequence
        self._link.write(frames.encode(op, wanted, argument, payload))
        deadline = time.monotonic() + self._timeout
        while True:
            if wanted in self._late:
                answer = self._late.pop(wanted)
                break
            if self._pump is not None:
                self._pump()
            for frame in self._decoder.feed(self._link.read()):
                self._late[frame.sequence] = frame
            if wanted in self._late:
                answer = self._late.pop(wanted)
                break
            if time.monotonic() > deadline:
                raise Timeout(op)
            if self._pump is None:
                time.sleep(0.0005)
        if answer.op != op | frames.ANSWER:
            raise LiveError(op, frames.BAD_FRAME, answer.payload)
        if answer.status not in accept:
            if answer.status == frames.APPLY:
                raise Refused(op, answer.payload)
            raise LiveError(op, answer.status, answer.payload)
        return answer

    # ---- reading ----------------------------------------------------------------------------

    def ping(self, payload: bytes = b"ping") -> bytes:
        return self.call(frames.PING, payload).payload

    def hello(self) -> Hello:
        p = self.call(frames.HELLO).payload
        (
            version,
            _flags,
            handles,
            body,
            arena,
            stage,
            emit,
            work,
            commits,
            rejects,
            committed,
        ) = struct.unpack("<IIQQQQQQQQQ", p[:80])
        return Hello(
            version,
            handles,
            body,
            arena,
            stage,
            emit,
            work,
            commits,
            rejects,
            committed,
        )

    def header(self) -> bytes:
        return self.call(frames.HEADER_OP).payload

    def resolve(self, path: str) -> int | None:
        answer = self.call(
            frames.RESOLVE, path.encode(), accept=(frames.OK, frames.NOT_FOUND)
        )
        if answer.status != frames.OK:
            return None
        return struct.unpack("<Q", answer.payload)[0]

    def path(self, handle: int) -> str:
        return self.call(frames.PATH, struct.pack("<Q", handle)).payload.decode()

    def get(self, handle: int) -> Cell:
        p = self.call(frames.GET, struct.pack("<Q", handle)).payload
        type_, parent, size, generation, flags, refs, index, name_length = (
            struct.unpack("<QQQQIIII", p[:48])
        )
        return Cell(
            handle,
            type_,
            parent,
            size,
            generation,
            flags,
            refs,
            index,
            p[48 : 48 + name_length].decode(),
        )

    def read(self, handle: int, offset: int, count: int) -> bytes:
        return self.call(
            frames.READ, struct.pack("<QII", handle, offset, count)
        ).payload

    def payload(self, handle: int) -> bytes:
        size = self.get(handle).payload_size
        out = bytearray()
        while len(out) < size:
            out += self.read(handle, len(out), min(frames.MAX_PAYLOAD, size - len(out)))
        return bytes(out)

    def children(self, handle: int) -> list[int]:
        out: list[int] = []
        at = 0
        while at != 0xFFFFFFFF:
            p = self.call(frames.CHILDREN, struct.pack("<QI", handle, at)).payload
            count, at = struct.unpack("<II", p[:8])
            out += struct.unpack(f"<{count}Q", p[8 : 8 + 8 * count]) if count else ()
        return out

    def refs(self, handle: int) -> list[tuple[int, int, int, int, int]]:
        out: list[tuple[int, int, int, int, int]] = []
        while True:
            p = self.call(frames.REFS, struct.pack("<QI", handle, len(out))).payload
            count, total = struct.unpack("<II", p[:8])
            for k in range(count):
                target, role, kind, binding, index = struct.unpack_from(
                    "<QQHHI", p, 8 + 24 * k
                )
                out.append((target, role, kind, binding, index))
            if len(out) >= total or count == 0:
                return out

    # ---- digest and dump ---------------------------------------------------------------------------

    def digest(self) -> str:
        return self.call(frames.DIGEST).payload.hex()

    def emit(self) -> tuple[bytes, str]:
        """The canonical bytes of the image and their digest (checked against the bytes)."""
        p = self.call(frames.EMIT).payload
        (length,) = struct.unpack("<Q", p[:8])
        digest = p[8:40].hex()
        out = bytearray()
        while len(out) < length:
            want = min(frames.MAX_PAYLOAD, length - len(out))
            out += self.call(
                frames.EMIT_READ, struct.pack("<I", want), len(out)
            ).payload
        if hashlib.sha256(out).hexdigest() != digest:
            raise LiveError(frames.EMIT, frames.DIGEST_MISMATCH)
        return bytes(out), digest

    # ---- changing -------------------------------------------------------------------------------------

    def stage(self, data: bytes) -> None:
        self.call(frames.STAGE_RESET)
        for at in range(0, len(data), frames.MAX_PAYLOAD):
            self.call(frames.STAGE_WRITE, data[at : at + frames.MAX_PAYLOAD], at)

    def apply(self, delta: bytes) -> str:
        """Stages `delta` and applies it. Returns the digest of the new image; `Refused` leaves the image as it was."""
        self.stage(delta)
        answer = self.call(
            frames.STAGE_COMMIT,
            struct.pack("<I", len(delta)) + hashlib.sha256(delta).digest(),
        )
        return answer.payload[:32].hex()

    def grow(self, committed: int) -> int:
        """Tells the image that its arena region is committed up to `committed` bytes."""
        answer = self.call(
            frames.GROW,
            struct.pack("<Q", committed),
            accept=(frames.OK, frames.REFUSED),
        )
        if answer.status == frames.REFUSED:
            raise Declined(frames.GROW, answer.payload)
        return struct.unpack("<Q", answer.payload)[0]

    def relink(self) -> tuple[int, int]:
        """Has the image write the entries and function slots of its context from its arena.

        Returns how many object entries it wrote (spare ones included) and how many function
        slots it set. After a change the image does this itself.
        """
        answer = self.call(frames.RELINK, accept=(frames.OK, frames.REFUSED))
        if answer.status == frames.REFUSED:
            raise Declined(frames.RELINK, answer.payload)
        return struct.unpack("<II", answer.payload[:8])

    def compact(self) -> Compacted:
        """Compacts the arena in place; `Refused` when an object is pinned."""
        p = self.call(frames.COMPACT, accept=(frames.OK, frames.REFUSED))
        if p.status == frames.REFUSED:
            raise Declined(frames.COMPACT, p.payload)
        handles, body, reclaimed = struct.unpack("<QQ", p.payload[:16]) + struct.unpack(
            "<Q", p.payload[48:56]
        )
        return Compacted(handles, body, p.payload[16:48].hex(), reclaimed)

    def break_(self, on: bool = True) -> bool:
        """Asks the running image to stop at its next safe point (or to go on); whether it has stopped."""
        answer = self.call(frames.BREAK, argument=1 if on else 0)
        return struct.unpack("<I", answer.payload[:4])[0] != 0
