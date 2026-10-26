"""Gates over real loopback sockets for the host POSIX fill (docs/25 §5, §12).

Every probe binds port 0 (ephemeral) and reads the assigned port back through
`local()` — there is no fixed port number anywhere in this file. The gates are the
ones the fill promises: (a) the connect/accept handshake, (b) a large payload that
spans many `wait` wakes — the blocking-composition pin over a real park, (c) deadlines
that refuse `Timeout` and do not hang the suite, (d) the error mapping
(connect-refused, read-after-close, write-after-peer-close honestly named), (e) the
on-demand pin in the same call-log shape `test_net_fill` records, (f) the
`install_wrapper` opacity pin with the in-memory `DummyWrappedStream`, and (g) the
payoff: one http request/response between `http.client` and `http.server` over real
sockets. Threads are conservative — one writer/one server thread, a generous deadline
on every stream that parks across threads, joins with timeouts, and a finally that
closes every socket.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from pymergetic.rxftk.net.schema import (
    AddressInUse,
    ConnectionRefused,
    ConnectionReset,
    Handle,
    Interest,
    InvalidEndpoint,
    NotConnected,
    NotSupported,
    StaleHandle,
    Timeout,
    WouldBlock,
)
from pymergetic.rxftk.net.transport.framing import read_exact, read_to_eof, write_all
from pymergetic.rxftk.net.transport.hostsocket import (
    HostListener,
    HostStream,
    HostStreams,
    ticks,
)
from pymergetic.rxftk.net.transport.http import Client, Request, Response
from pymergetic.rxftk.net.transport.http.server import Server
from pymergetic.rxftk.net.transport.stream import Stream, wrap

from .test_net_fill import DummyWrappedStream, dummy_tls, sample_tls


pytestmark = pytest.mark.live

def _pair() -> tuple[HostStreams, HostListener, HostStream, HostStream]:
    """One connected loopback pair: (streams, listener, client, accepted server end).
    The caller closes what it did not close itself — every gate tears down in finally."""
    streams = HostStreams()
    listener = streams.listen(("127.0.0.1", 0))
    try:
        client = streams.connect(listener.local())
        try:
            assert listener.wait(Interest.READ, 5_000) == Interest.READ
            server = listener.accept()
        except BaseException:
            client.close()
            raise
    except BaseException:
        listener.close()
        raise
    assert isinstance(client, HostStream)
    assert isinstance(server, HostStream)
    return streams, listener, client, server


def _dead_peer() -> tuple[str, int]:
    """An address nothing answers on: an ephemeral port, bound and then released.
    (A tiny race exists if the OS reassigns that port mid-test; loopback probes make
    that vanishingly unlikely and never guess a fixed port to avoid it.)"""
    probe = HostStreams().listen(("127.0.0.1", 0))
    try:
        return probe.local()
    finally:
        probe.close()


class _CountingStream(Stream):
    """A wake counter: the whole Stream contract delegated to a host stream, with
    `wait` counted — so the large-payload gate can prove the composed reads really
    parked and woke instead of sliding through on luck."""

    def __init__(self, inner: Stream) -> None:
        self.inner = inner
        self.waits = 0

    @property
    def handle(self) -> Handle:
        return self.inner.handle

    @property
    def wrapped(self) -> bool:
        return False

    @property
    def deadline(self) -> int | None:
        return self.inner.deadline

    def setdeadline(self, deadline: int | None) -> None:
        self.inner.setdeadline(deadline)

    def connect(self, peer: tuple[str, int]) -> None:
        self.inner.connect(peer)

    def read(self, size: int) -> bytes:
        return self.inner.read(size)

    def write(self, data: bytes) -> int:
        return self.inner.write(data)

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        self.waits += 1
        return self.inner.wait(interest, timeout)

    def close(self) -> None:
        self.inner.close()


class _RecordingStreams(HostStreams):
    """HostStreams that remembers what `open` handed out — the failure-cleanup pin."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: HostStream | None = None

    def open(self) -> HostStream:
        self.opened = super().open()
        return self.opened


class _GuardedStreams(HostStreams):
    """HostStreams whose streams carry a generous deadline, so a broken exchange fails
    with `Timeout` instead of parking the suite."""

    def open(self) -> HostStream:
        stream = super().open()
        stream.setdeadline(ticks() + 10_000)
        return stream


def hello(request: Request) -> Response:
    """One handler: an ordinary Python function (docs/23 §4.3)."""
    return Response(200, body=b"hello")


# (a) the connect/accept handshake


def test_connect_accept_handshake() -> None:
    """(a) The handshake over real loopback: bind(0), connect, readiness, accept — and
    bytes move in both directions through the composed read/write loops."""
    _streams, listener, client, server = _pair()
    try:
        host, port = listener.local()
        assert host == "127.0.0.1"
        assert port != 0
        assert client.handle != server.handle
        write_all(client, b"hello")
        assert read_exact(server, 5) == b"hello"
        write_all(server, b"world")
        assert read_exact(client, 5) == b"world"
        assert client.wait(Interest.WRITE, 0) == Interest.WRITE  # level: still ready
    finally:
        server.close()
        client.close()
        listener.close()


# (b) the blocking composition: wait really parks, across many wakes


def test_large_payload_spans_many_wait_wakes() -> None:
    """(b) 8 MiB from a paced writer: the reader's composed loop must park and wake
    many times (the waits are counted) and every byte must arrive. This is the
    blocking-composition pin over a real park — wait for readiness, then run the
    operation. The writer is paced in chunks so the wakes are the fill's, not a race
    with kernel buffering."""
    _streams, listener, client, server = _pair()
    try:
        client.setdeadline(ticks() + 30_000)  # no gate may hang the suite
        server.setdeadline(ticks() + 30_000)
        sock = client.sock
        assert sock is not None
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)  # force many wakes
        payload = bytes(range(256)) * 32768  # 8 MiB of deterministic bytes
        chunk = 8192
        problems: list[BaseException] = []

        def writer() -> None:
            try:
                for offset in range(0, len(payload), chunk):
                    write_all(server, payload[offset : offset + chunk])
                    time.sleep(0.0005)  # pace: the reader parks and wakes, not races
            except Exception as error:  # noqa: BLE001 - a thread records its failure; it must not die silently
                problems.append(error)

        thread = threading.Thread(target=writer, name="payload-writer", daemon=True)
        counted = _CountingStream(client)
        received = b""
        thread.start()
        try:
            received = read_exact(counted, len(payload))
        finally:
            thread.join(timeout=35)
        assert not thread.is_alive()
        assert problems == []
        assert received == payload
        assert counted.waits >= 100  # ~1024 paced chunks: many real parks and wakes
    finally:
        server.close()
        client.close()
        listener.close()


# (c) deadlines


def test_read_with_a_short_deadline_times_out() -> None:
    """(c) A composed read on a silent peer with a short deadline refuses `Timeout`
    (the contract's deadline error) and does not hang — then the silence turns out to
    be the peer's, not a closed socket."""
    _streams, listener, client, server = _pair()
    try:
        client.setdeadline(ticks() + 100)
        assert client.deadline is not None
        started = time.monotonic()
        with pytest.raises(Timeout):
            read_exact(client, 1)  # wait parks, runs into the deadline, refuses
        assert time.monotonic() - started < 5.0
        with pytest.raises(Timeout):
            client.wait(Interest.READ, 0)  # once passed, the deadline holds on every op
        client.setdeadline(None)
        assert client.deadline is None
        with pytest.raises(WouldBlock):
            client.read(1)  # the peer is open and silent; only the deadline spoke
    finally:
        server.close()
        client.close()
        listener.close()


# (d) the error mapping


def test_connect_refused_maps_to_connectionrefused() -> None:
    """(d) Nothing answers the connect: `ConnectionRefused`, the contract's connection
    error. A refused connect leaves no half-made socket behind — the stream is
    unconnected again, and `Streams.connect` (which closes what it opened) leaves
    `StaleHandle`."""
    peer = _dead_peer()
    streams = HostStreams()
    stream = streams.open()
    try:
        with pytest.raises(ConnectionRefused):
            stream.connect(peer)
        with pytest.raises(NotConnected):
            stream.read(1)  # unconnected, not closed: the honest refusal
    finally:
        stream.close()
    recording = _RecordingStreams()
    with pytest.raises(ConnectionRefused):
        recording.connect(peer)  # the seam's connect closes what it opened
    assert recording.opened is not None
    with pytest.raises(StaleHandle):
        recording.opened.read(1)


def test_close_is_idempotent_and_the_handle_goes_stale() -> None:
    """(d) After close, the socket is gone: read/write/wait refuse `StaleHandle` (§14),
    `close` is idempotent, and the handle is remembered — never a silent new object."""
    _streams, listener, client, server = _pair()
    try:
        handle = client.handle
        client.close()
        client.close()  # idempotent
        assert client.handle == handle
        for operation in (
            lambda: client.read(1),
            lambda: client.write(b"x"),
            lambda: client.wait(Interest.READ, 0),
            lambda: client.connect(("127.0.0.1", 1)),
        ):
            with pytest.raises(StaleHandle):
                operation()
    finally:
        server.close()
        listener.close()
    listener.close()  # idempotent too
    for operation in (
        lambda: listener.accept(),
        lambda: listener.wait(Interest.READ, 0),
        lambda: listener.local(),
    ):
        with pytest.raises(StaleHandle):
            operation()


def test_orderly_peer_close_reads_as_eof() -> None:
    """(d) An orderly close is EOF after the bytes that arrived — `b""` at the end of
    `read_to_eof`, never a silent empty read and never a refusal."""
    _streams, listener, client, server = _pair()
    try:
        write_all(server, b"tail")
        server.close()
        assert read_to_eof(client) == b"tail"
    finally:
        client.close()
        listener.close()


def test_write_after_the_peer_is_gone_is_connectionreset() -> None:
    """(d) The honest name for POSIX write-after-close: a write after the peer's
    *orderly* close (FIN) may still be accepted — a TCP half-close is real, and the
    fill does not pretend otherwise. What is promised and pinned here is the other
    half: once the peer is gone (an abortive close, SO_LINGER 0, the reset), a write
    refuses `ConnectionReset`. Every step is bounded; the refusal arrives with the
    reset, not with the FIN."""
    _streams, listener, client, server = _pair()
    try:
        sock = server.sock
        assert sock is not None
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        server.close()  # abortive: the peer gets a reset, not a FIN
        client.setdeadline(ticks() + 5_000)
        refused = False
        for _ in range(1_000):
            try:
                client.write(b"x")
            except WouldBlock:
                client.wait(Interest.WRITE, 50)
            except ConnectionReset:
                refused = True
                break
        assert refused, "no write refused after the peer's reset"
    finally:
        client.close()
        listener.close()


# (e) the on-demand pin


def test_streams_open_sockets_on_demand_only() -> None:
    """(e) The on-demand pin (§12) in the call-log shape `test_net_fill` records:
    constructing the seam opens nothing — no OS socket exists, the log is empty — until
    a protocol calls connect/listen. A port of 0 assigns one and `local` reads it back."""
    streams = HostStreams()
    assert streams.calls == []
    stream = streams.open()
    try:
        assert streams.calls == []  # not even `open` asks the host for anything
        assert stream.sock is None  # no OS socket exists yet — nothing to leak
    finally:
        stream.close()
    listener = streams.listen(("127.0.0.1", 0))
    try:
        assert streams.calls == [("listen", ("127.0.0.1", 0))]
        host, port = listener.local()
        assert host == "127.0.0.1"
        assert port != 0
        client = streams.connect((host, port))
        try:
            assert streams.calls == [
                ("listen", ("127.0.0.1", 0)),
                ("connect", (host, port)),
            ]
            assert isinstance(client, HostStream)
            assert client.sock is not None  # the protocol asked; the fill created
        finally:
            client.close()
    finally:
        listener.close()


# (f) the install_wrapper opacity pin


def test_dummy_wrapper_is_opaque_over_a_host_socket() -> None:
    """(f) `DummyWrappedStream` (the in-memory fill's TLS stand-in) over a real host
    socket still satisfies the Stream contract: handle/deadline through, read/write/
    wait/close indistinguishable from a plain stream."""
    _streams, listener, client, server = _pair()
    try:
        with dummy_tls():
            wrapped = wrap(client, sample_tls())
        assert isinstance(wrapped, DummyWrappedStream)
        assert wrapped.wrapped is True
        assert client.wrapped is False
        assert wrapped.handle == client.handle
        wrapped.setdeadline(ticks() + 5_000)
        assert wrapped.deadline == client.deadline
        write_all(wrapped, b"opaque")
        assert read_exact(server, 6) == b"opaque"
        write_all(server, b"back!")
        assert read_exact(wrapped, 5) == b"back!"
        assert wrapped.wait(Interest.WRITE, 0) == Interest.WRITE
        wrapped.close()
        with pytest.raises(StaleHandle):
            wrapped.read(1)  # the wrapper is transparent all the way down
        with pytest.raises(StaleHandle):
            client.read(1)
    finally:
        server.close()
        client.close()
        listener.close()


# (g) the payoff: http over real sockets


def test_http_over_real_sockets() -> None:
    """(g) One request/response between `http.client` and `http.server` with
    `HostStreams` between them on loopback: the transport codecs are socket-free and
    this fill carries them unchanged — the whole point of the stream contract."""
    streams = _GuardedStreams()
    listener = streams.listen(("127.0.0.1", 0))
    host, port = listener.local()
    problems: list[BaseException] = []

    def serve() -> None:
        try:
            Server({"/": hello}).serve(listener)
        except Exception as error:  # noqa: BLE001 - a thread records its failure; it must not die silently
            problems.append(error)

    thread = threading.Thread(target=serve, name="http-serve", daemon=True)
    thread.start()
    response: Response | None = None
    try:
        response = Client(streams).get(f"http://{host}:{port}/")
    finally:
        listener.close()  # the only stop of the plain accept loop (§12)
        thread.join(timeout=15)
    assert not thread.is_alive()
    assert problems == []
    assert response is not None
    assert response.status == 200
    assert response.body == b"hello"
    assert response.header("content-length") == "5"


# both families


def test_ipv6_loopback_handshake() -> None:
    """IPv6 is a first-class family here, not a later port: the same handshake over
    `::1`. A host with no IPv6 loopback skips by name — never a silent pass."""
    streams = HostStreams()
    try:
        listener = streams.listen(("::1", 0))
    except (NotSupported, InvalidEndpoint):
        pytest.skip("this host has no IPv6 loopback")
    try:
        host, port = listener.local()
        assert host == "::1"
        assert port != 0
        client = streams.connect((host, port))
        try:
            assert listener.wait(Interest.READ, 5_000) == Interest.READ
            server = listener.accept()
            try:
                write_all(client, b"v6")
                assert read_exact(server, 2) == b"v6"
            finally:
                server.close()
        finally:
            client.close()
    finally:
        listener.close()


# named refusals and typed endpoints


def test_unconnected_and_twice_connected_are_refused_by_name() -> None:
    """An unconnected stream refuses `NotConnected` on read/write/wait (§5); a second
    `connect` on a connected stream is `NotSupported` — never a silent no-op."""
    streams = HostStreams()
    stream = streams.open()
    try:
        for operation in (
            lambda: stream.read(1),
            lambda: stream.write(b"x"),
            lambda: stream.wait(Interest.READ, 0),
        ):
            with pytest.raises(NotConnected):
                operation()
    finally:
        stream.close()
    _streams, listener, client, server = _pair()
    try:
        with pytest.raises(NotSupported):
            client.connect(("127.0.0.1", 1))  # already connected, refused by name
    finally:
        server.close()
        client.close()
        listener.close()


def test_endpoint_and_bind_refusals_are_typed() -> None:
    """An endpoint that names no peer is `InvalidEndpoint` (never a raw host error),
    and a bind target that is taken is `AddressInUse` — mapped from the errno table."""
    streams = HostStreams()
    with pytest.raises(InvalidEndpoint):
        streams.connect(("127.0.0.1", 0))  # port 0 names no peer
    with pytest.raises(InvalidEndpoint):
        streams.connect(("no-such-host.invalid", 80))  # a name that resolves to nothing
    with pytest.raises(InvalidEndpoint):
        streams.listen(("127.0.0.1", 70_000))  # not a port
    listener = streams.listen(("127.0.0.1", 0))
    try:
        with pytest.raises(AddressInUse):
            streams.listen(listener.local())  # the ephemeral port is taken — by us
    finally:
        listener.close()


def test_link_interest_and_zero_reads_are_refused_by_name() -> None:
    """Named refusals over pretense: `Interest.LINK` is the device face's wake (§7), a
    socket's wait refuses it; and `read(0)` would lie about EOF (`b""` is reserved)."""
    _streams, listener, client, server = _pair()
    try:
        with pytest.raises(NotSupported):
            client.wait(Interest.LINK, 0)
        with pytest.raises(NotSupported):
            client.read(0)
    finally:
        server.close()
        client.close()
        listener.close()
