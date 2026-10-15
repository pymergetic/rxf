"""Line and length-prefixed framing over a byte stream (docs/25 §12), optional.

These are helpers over the stream contract, not a second layer: the codec modules use
them when their grammar is line-shaped (http's head, ftp's control channel) or
length-shaped (bodies, frames). No helper ever over-reads — a line read consumes bytes
up to the terminator and nothing beyond, so the next reader of the same stream sees
exactly what follows.

A line that runs past its cap is `MessageTooLong`; a line or frame truncated by the end
of the stream is `MalformedMessage` — a refusal by name, never a wrong parse
half-applied. A read that finds a clean end of stream at a line boundary answers `None`
(the peer closed); an empty line is `b""` and is never confused with it.
"""

from __future__ import annotations

from ..schema import Interest, MalformedMessage, MessageTooLong, WouldBlock
from .stream import Stream

CRLF = b"\r\n"
MAX_LINE = 8192
MAX_FRAME = 1 << 20


def _read_chunk(stream: Stream, size: int) -> bytes:
    """One readable chunk, waiting (composed blocking) until the stream has one."""
    while True:
        try:
            return stream.read(size)
        except WouldBlock:
            stream.wait(Interest.READ)


def read_line(stream: Stream, max_len: int = MAX_LINE) -> bytes | None:
    """One line without its terminator (a trailing CR is dropped).

    `None` is a clean end of stream at a line boundary — the peer closed. A line
    truncated mid-way is refused `MalformedMessage`; a line over max_len is refused
    `MessageTooLong`.
    """
    line = bytearray()
    while True:
        chunk = _read_chunk(stream, 1)
        if chunk == b"":
            if not line:
                return None
            raise MalformedMessage("a line was truncated by the end of the stream")
        if chunk == b"\n":
            if line.endswith(b"\r"):
                line = line[:-1]
            return bytes(line)
        line += chunk
        if len(line) > max_len:
            raise MessageTooLong(f"a line exceeds {max_len} bytes")


def read_exact(stream: Stream, size: int) -> bytes:
    """Exactly size bytes — or `MalformedMessage` at a truncated end of stream."""
    out = bytearray()
    while len(out) < size:
        chunk = _read_chunk(stream, size - len(out))
        if chunk == b"":
            raise MalformedMessage("the stream ended mid-message")
        out += chunk
    return bytes(out)


def read_to_eof(stream: Stream) -> bytes:
    """Everything until the peer closes — the honest shape of a data connection (§12)."""
    out = bytearray()
    while True:
        chunk = _read_chunk(stream, 4096)
        if chunk == b"":
            return bytes(out)
        out += chunk


def write_all(stream: Stream, data: bytes) -> int:
    """Write every byte, looping over honest partial writes. Returns len(data)."""
    sent = 0
    while sent < len(data):
        try:
            sent += stream.write(data[sent:])
        except WouldBlock:
            stream.wait(Interest.WRITE)
    return sent


def write_line(stream: Stream, line: bytes) -> int:
    """Write one line with its CRLF. The line must not contain a newline itself."""
    if b"\n" in line or b"\r" in line:
        raise MalformedMessage("a framed line carries no newline")
    return write_all(stream, line + CRLF)


def read_prefixed(stream: Stream, width: int = 4, max_len: int = MAX_FRAME) -> bytes:
    """One length-prefixed frame: an unsigned big-endian length, then that many bytes."""
    head = read_exact(stream, width)
    length = int.from_bytes(head, "big")
    if length > max_len:
        raise MessageTooLong(f"a frame of {length} bytes exceeds {max_len}")
    return read_exact(stream, length)


def write_prefixed(stream: Stream, payload: bytes, width: int = 4) -> int:
    """Write one length-prefixed frame; a payload the width cannot count is refused
    `MessageTooLong`."""
    if len(payload).bit_length() > width * 8:
        raise MessageTooLong(f"{len(payload)} bytes do not fit a {width}-byte length")
    return write_all(stream, len(payload).to_bytes(width, "big") + payload)
