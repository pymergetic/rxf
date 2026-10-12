"""Frames of the channel to a live image (docs/19): 16-byte header, up to 1024 bytes, CRC-32.

The layout is the one of live.h. A stream that is damaged is read by skipping one byte at a time
until a header with the magic, a legal length and a matching CRC turns up.
"""

from __future__ import annotations

import struct
import zlib
from typing import NamedTuple

MAGIC = 0x5852
MAX_PAYLOAD = 1024
HEADER = struct.Struct("<HBBIIHH")
HEADER_SIZE = HEADER.size
TRAILER_SIZE = 4
ANSWER = 0x80

assert HEADER_SIZE == 16

# Ops (live.h PYMERGETIC_RXF_OP_*).
PING = 0x01
HELLO = 0x02
HEADER_OP = 0x03
RESOLVE = 0x04
PATH = 0x05
GET = 0x06
READ = 0x07
CHILDREN = 0x08
REFS = 0x09
STAGE_RESET = 0x10
STAGE_WRITE = 0x11
STAGE_COMMIT = 0x12
DIGEST = 0x20
EMIT = 0x21
EMIT_READ = 0x22
COMPACT = 0x23
GROW = 0x24
RELINK = 0x25
BREAK = 0x30

# Status words (live.h PYMERGETIC_RXF_LIVE_*).
OK = 0
BAD_FRAME = 1
UNKNOWN_OP = 2
NOT_FOUND = 3
BAD_REQUEST = 4
NO_ROOM = 5
DIGEST_MISMATCH = 6
APPLY = 7
BAD_IMAGE = 8
REFUSED = 9

STATUS_NAMES = {
    OK: "ok",
    BAD_FRAME: "bad frame",
    UNKNOWN_OP: "unknown op",
    NOT_FOUND: "not found",
    BAD_REQUEST: "bad request",
    NO_ROOM: "no room",
    DIGEST_MISMATCH: "digest mismatch",
    APPLY: "delta refused",
    BAD_IMAGE: "image not usable",
    REFUSED: "request refused",
}


class Frame(NamedTuple):
    op: int
    sequence: int
    argument: int
    status: int
    payload: bytes
    flags: int = 0


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def encode(
    op: int,
    sequence: int,
    argument: int = 0,
    payload: bytes = b"",
    status: int = 0,
    flags: int = 0,
) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"a frame carries at most {MAX_PAYLOAD} bytes")
    head = HEADER.pack(
        MAGIC,
        op,
        flags,
        sequence & 0xFFFFFFFF,
        argument & 0xFFFFFFFF,
        len(payload),
        status,
    )
    body = head + payload
    return body + struct.pack("<I", crc32(body))


class Decoder:
    """Collects bytes and yields the frames in them, resynchronising after damage."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self.skipped = 0

    def feed(self, data: bytes) -> list[Frame]:
        self._buffer += data
        frames: list[Frame] = []
        while True:
            if len(self._buffer) < HEADER_SIZE + TRAILER_SIZE:
                break
            magic, op, flags, sequence, argument, length, status = HEADER.unpack_from(
                self._buffer
            )
            if magic != MAGIC or length > MAX_PAYLOAD:
                del self._buffer[0]
                self.skipped += 1
                continue
            total = HEADER_SIZE + length + TRAILER_SIZE
            if len(self._buffer) < total:
                break
            body = bytes(self._buffer[: total - 4])
            (check,) = struct.unpack_from("<I", self._buffer, total - 4)
            if check != crc32(body):
                del self._buffer[0]
                self.skipped += 1
                continue
            frames.append(
                Frame(op, sequence, argument, status, body[HEADER_SIZE:], flags)
            )
            del self._buffer[:total]
        return frames
