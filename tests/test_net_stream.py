"""Pins for the byte-stream contract (docs/25 §12) and the TLS seam (§11).

The fill is the in-memory duplex pair (`test_net_fill`) — the socket layer is the
stack's job later. What is pinned here is the contract itself: EOF is never confused
with an empty-but-open stream, a closed stream is `StaleHandle` and an unconnected one
is `NotConnected`, deadlines refuse `Timeout`, a timeout wake is empty and never an
error, streams and listeners appear on demand (FTP's data connection), and `wrap`
without a card refuses `NotSupported` by name — never a silent passthrough.
"""

from __future__ import annotations

from typing import cast

import pytest

from pymergetic.rxf.net.schema import (
    Interest,
    NotConnected,
    NotSupported,
    StaleHandle,
    Timeout,
    WouldBlock,
)
from pymergetic.rxf.net.transport.stream import accept_loop, install_wrapper, wrap

from .test_net_fill import (
    DummyWrappedStream,
    FakeClock,
    MemoryListener,
    MemoryPair,
    MemoryStream,
    MemoryStreams,
    dummy_tls,
    sample_tls,
)


def test_read_write_roundtrip() -> None:
    """What one end writes, the other reads — the duplex pair is the honest wire."""
    pair = MemoryPair("t")
    assert pair.client.write(b"hello") == 5
    assert pair.server.read(5) == b"hello"


def test_read_refuses_wouldblock_when_empty_but_open() -> None:
    """An empty-but-open stream refuses WouldBlock; it is never an EOF."""
    pair = MemoryPair("t")
    with pytest.raises(WouldBlock):
        pair.server.read(1)


def test_read_returns_empty_exactly_at_eof() -> None:
    """b"" means the peer closed — after the bytes it sent, never before."""
    pair = MemoryPair("t")
    pair.client.write(b"tail")
    pair.client.close()
    assert pair.server.read(10) == b"tail"
    assert pair.server.read(10) == b""


def test_partial_write_is_honest() -> None:
    """A write accepts what the window can take and says how much — no pretending."""
    pair = MemoryPair("t")
    pair.client._window = 3
    assert pair.client.write(b"abcdef") == 3
    assert pair.client.write(b"def") == 3
    assert bytes(pair.server.inbox) == b"abcdef"


def test_write_refuses_wouldblock_when_choke() -> None:
    """A closed window refuses WouldBlock rather than dropping bytes silently."""
    pair = MemoryPair("t")
    pair.client._window = 0
    with pytest.raises(WouldBlock):
        pair.client.write(b"x")


def test_write_refuses_when_the_peer_is_gone() -> None:
    """Writing to a closed peer is ConnectionReset — not a silent loss."""
    from pymergetic.rxf.net.schema import ConnectionReset

    pair = MemoryPair("t")
    pair.server.close()
    with pytest.raises(ConnectionReset):
        pair.client.write(b"x")


def test_closed_stream_refuses_stalehandle() -> None:
    """After close, the socket is gone: StaleHandle (§14), never a silent new object."""
    pair = MemoryPair("t")
    pair.client.close()
    pair.client.close()  # idempotent
    for operation in (
        lambda: pair.client.read(1),
        lambda: pair.client.write(b"x"),
        lambda: pair.client.wait(Interest.READ, 0),
    ):
        with pytest.raises(StaleHandle):
            operation()


def test_unconnected_stream_refuses_notconnected() -> None:
    """Before connect, there is no peer: NotConnected (§5) on read/write/wait."""
    streams = MemoryStreams()
    stream = streams.open()
    for operation in (
        lambda: stream.read(1),
        lambda: stream.write(b"x"),
        lambda: stream.wait(Interest.READ, 0),
    ):
        with pytest.raises(NotConnected):
            operation()


def test_connect_on_demand() -> None:
    """A stream connects at runtime (§12) — FTP's data connection rides this."""
    streams = MemoryStreams()
    far = MemoryStream("data.far")
    streams.queue_connect(far)
    stream = streams.connect(("192.0.2.1", 49152))
    assert streams.calls == [("connect", ("192.0.2.1", 49152))]
    far.write(b"payload")
    assert stream.read(7) == b"payload"


def test_connect_failure_closes_the_stream() -> None:
    """A refused connect leaves no open stream behind."""

    class Recording(MemoryStreams):
        def __init__(self) -> None:
            super().__init__()
            self.opened: MemoryStream | None = None

        def open(self) -> MemoryStream:
            stream = MemoryStream(on_connect=self._take)
            self.opened = stream
            return stream

    streams = Recording()
    with pytest.raises(NotSupported):
        streams.connect(("192.0.2.1", 80))
    assert streams.opened is not None
    with pytest.raises(StaleHandle):
        streams.opened.read(1)


def test_deadline_refuses_timeout() -> None:
    """A deadline is a deadline (§12): at or past it, every operation refuses Timeout."""
    clock = FakeClock()
    pair = MemoryPair("t")
    pair.client._now = clock
    pair.server.write(b"x")  # the wire delivers to the client's inbox
    pair.client.setdeadline(5)
    assert pair.client.deadline == 5
    assert pair.client.read(1) == b"x"
    clock.ticks = 5
    for operation in (
        lambda: pair.client.read(1),
        lambda: pair.client.write(b"x"),
        lambda: pair.client.wait(Interest.READ, 0),
    ):
        with pytest.raises(Timeout):
            operation()
    pair.client.setdeadline(None)
    assert pair.client.deadline is None
    with pytest.raises(WouldBlock):
        pair.client.read(1)  # the peer is open and silent; only the deadline spoke


def test_wait_timeout_is_an_empty_wake_not_an_error() -> None:
    """A timeout is a wake with an empty ready set (§4), never a refusal."""
    pair = MemoryPair("t")
    assert pair.server.wait(Interest.READ, 0) == Interest(0)


def test_wait_reports_level_readiness() -> None:
    """Readiness is level-triggered (§4): ready bits name what can move now."""
    pair = MemoryPair("t")
    assert pair.server.wait(Interest.WRITE) == Interest.WRITE
    pair.client.write(b"x")
    assert pair.server.wait(Interest.READ) == Interest.READ
    assert pair.server.read(1) == b"x"
    assert pair.server.wait(Interest.READ, 0) == Interest(0)


def test_listen_reads_back_the_assigned_port() -> None:
    """A port of 0 assigns one and `local` reads it back — never a fixed port."""
    streams = MemoryStreams()
    listener = streams.listen(("127.0.0.1", 0))
    host, port = listener.local()
    assert host == "127.0.0.1"
    assert port != 0
    assert ("listen", ("127.0.0.1", 0)) in streams.calls


def test_accept_wouldblock_then_streams() -> None:
    """Accept is non-blocking; the readiness loop is the caller's composition."""
    listener = MemoryListener(closes_when_drained=False)
    with pytest.raises(WouldBlock):
        listener.accept()
    stream = MemoryStream("incoming")
    listener.push(stream)
    assert listener.wait(Interest.READ) == Interest.READ
    assert listener.accept() is stream
    listener.close()
    with pytest.raises(StaleHandle):
        listener.accept()


def test_accept_loop_is_plain() -> None:
    """The accept loop runs each connection and closes it — no queue, no scheduler."""
    served: list[str] = []
    first = MemoryStream("one")
    second = MemoryStream("two")
    listener = MemoryListener((first, second))
    accept_loop(listener, lambda stream: served.append(cast(MemoryStream, stream).name))
    assert served == ["one", "two"]
    with pytest.raises(StaleHandle):
        first.read(1)  # the loop closed every accepted stream
    with pytest.raises(StaleHandle):
        listener.accept()  # and the drained listener is closed


def test_accept_loop_stops_when_the_listener_closes() -> None:
    """Closing the listener is the only stop of the plain loop."""
    listener = MemoryListener()
    listener.close()
    called: list[str] = []
    accept_loop(listener, lambda stream: called.append(cast(MemoryStream, stream).name))
    assert called == []


def test_wrap_without_a_card_refuses_notsupported() -> None:
    """Without an installed card, wrap refuses by name — plaintext is never implied."""
    install_wrapper(None)
    with pytest.raises(NotSupported):
        wrap(MemoryStream("t"), sample_tls())


def test_wrap_uses_the_installed_wrapper() -> None:
    """The card's fill wraps; the protocol sees one Stream contract either way."""
    pair = MemoryPair("t")
    with dummy_tls():
        wrapped = wrap(pair.client, sample_tls())
        assert isinstance(wrapped, DummyWrappedStream)
        assert wrapped.wrapped is True
        assert pair.client.wrapped is False
        assert wrapped.handle == pair.client.handle
        wrapped.write(b"opaque")
        assert bytes(pair.server.inbox) == b"opaque"
    with pytest.raises(NotSupported):
        wrap(pair.client, sample_tls())  # the context took the card away again
