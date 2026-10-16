"""Pins for the framing helpers (docs/25 §12): lines and length-prefixed frames over
a stream.

What is pinned: no helper ever over-reads (the next reader of the stream sees exactly
what follows), a clean end of stream at a line boundary is `None` and is never an empty
line, a truncated message is `MalformedMessage`, and a message past its cap is
`MessageTooLong` — refusals by name, never a wrong parse half-applied.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.net.schema import MalformedMessage, MessageTooLong
from pymergetic.rxftk.net.transport.framing import (
    read_exact,
    read_line,
    read_prefixed,
    read_to_eof,
    write_all,
    write_line,
    write_prefixed,
)

from .test_net_fill import MemoryPair


def pair_with(data: bytes, *, closed: bool = True) -> MemoryPair:
    """A pair whose server end reads `data` from the wire (then EOF, optionally)."""
    pair = MemoryPair("frame")
    pair.server.feed(data)
    if closed:
        pair.client.close()  # the peer's goodbye is the reader's EOF
    return pair


def test_line_roundtrip() -> None:
    """Write one line, read one line: CRLF goes on the wire and comes off."""
    pair = MemoryPair("frame")
    assert write_line(pair.client, b"hello") == 7
    assert bytes(pair.server.inbox) == b"hello\r\n"
    assert read_line(pair.server) == b"hello"


def test_read_line_accepts_bare_lf() -> None:
    """A line is a line: a bare LF terminates, and a trailing CR is dropped."""
    pair = pair_with(b"one\r\ntwo\n")
    assert read_line(pair.server) == b"one"
    assert read_line(pair.server) == b"two"


def test_empty_line_is_not_end_of_stream() -> None:
    """b"" is a line (the blank line that ends a head); None is the peer's goodbye."""
    pair = pair_with(b"\r\nbye\n")
    assert read_line(pair.server) == b""
    assert read_line(pair.server) == b"bye"
    assert read_line(pair.server) is None


def test_truncated_line_is_refused() -> None:
    """A line the stream cuts short is MalformedMessage — never half a line."""
    pair = pair_with(b"half a line")
    with pytest.raises(MalformedMessage):
        read_line(pair.server)


def test_line_over_cap_is_refused() -> None:
    """A line past its cap is MessageTooLong, named as such (§15)."""
    pair = pair_with(b"x" * 40 + b"\n", closed=False)
    with pytest.raises(MessageTooLong):
        read_line(pair.server, max_len=16)


def test_no_overread() -> None:
    """The helpers leave the stream exactly at the message's end — the honest seam."""
    pair = pair_with(b"line\nbody!")
    assert read_line(pair.server) == b"line"
    assert read_exact(pair.server, 5) == b"body!"


def test_read_exact_truncation_is_refused() -> None:
    """A body the stream cuts short is MalformedMessage, not a short answer."""
    pair = pair_with(b"12345")
    with pytest.raises(MalformedMessage):
        read_exact(pair.server, 8)


def test_read_to_eof() -> None:
    """The data connection's shape (§12): everything until the peer closes."""
    pair = pair_with(b"the whole file")
    assert read_to_eof(pair.server) == b"the whole file"


def test_write_all_loops_partial_writes() -> None:
    """Partial writes are honest and the helper loops over them."""
    pair = MemoryPair("frame")
    pair.client._window = 3
    assert write_all(pair.client, b"abcdef") == 6
    assert bytes(pair.server.inbox) == b"abcdef"


def test_write_line_refuses_a_newline_in_the_line() -> None:
    """The terminator belongs to the framing, not to the caller's bytes."""
    pair = MemoryPair("frame")
    with pytest.raises(MalformedMessage):
        write_line(pair.client, b"two\nlines")


def test_prefixed_roundtrip() -> None:
    """Length-prefixed frames round-trip, at any width."""
    pair = MemoryPair("frame")
    assert write_prefixed(pair.client, b"payload") == 11
    assert read_prefixed(pair.server) == b"payload"
    write_prefixed(pair.client, b"", width=2)
    assert read_prefixed(pair.server, width=2) == b""


def test_prefixed_over_cap_is_refused() -> None:
    """A frame count past the cap is MessageTooLong before the bytes are touched."""
    pair = pair_with((1 << 30).to_bytes(4, "big") + b"never read", closed=False)
    with pytest.raises(MessageTooLong):
        read_prefixed(pair.server, max_len=1024)


def test_write_prefixed_refuses_an_unfittable_count() -> None:
    """A length the width cannot count is refused, not truncated."""
    pair = MemoryPair("frame")
    with pytest.raises(MessageTooLong):
        write_prefixed(pair.client, b"x" * 300, width=1)
