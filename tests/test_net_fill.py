"""The in-memory duplex stream pair — the honest test fill for the transport layer.

The socket layer is the stack's job later (docs/25 §12); these fills implement the
`net.transport.stream` contracts over plain byte buffers so every codec suite runs
without a provider. A scripted `MemoryStreams` records every connect/listen a protocol
makes (the on-demand pin), and `DummyWrappedStream` is the TLS stand-in for the opacity
pin: protocol code cannot tell it apart from a plain stream.

The fill is single-threaded, so a wait that nothing can satisfy answers an empty wake
(arrange preloaded bytes or EOF in the tests) and refuses loudly after too many empty
wakes rather than spinning. The blocking composition — wait that really parks — is
pinned on the loopback fill of `test_net_loopback` instead.
"""

from __future__ import annotations

import contextlib
from collections import deque
from collections.abc import Callable, Iterator

from pymergetic.rxftk.net.schema import (
    ConnectionReset,
    Handle,
    Interest,
    NotConnected,
    NotSupported,
    Role,
    StaleHandle,
    Timeout,
    TlsConfig,
    WouldBlock,
)
from pymergetic.rxftk.net.transport.stream import (
    Listener,
    Stream,
    Streams,
    install_wrapper,
)

_EPHEMERAL = 49152  # the first assigned port; never a fixed port number
_HANDLES = 0


def _handle() -> Handle:
    global _HANDLES
    _HANDLES += 1
    return (_HANDLES, 1)


class FakeClock:
    """A fill's tick source the test drives itself (the edge timer, S9)."""

    def __init__(self) -> None:
        self.ticks = 0

    def __call__(self) -> int:
        return self.ticks


class MemoryStream(Stream):
    """One end of a duplex pair: `inbox` is what the network delivered here, `sent` is
    what this end wrote. `window` bounds one write (None = unlimited, 0 = choked)."""

    def __init__(
        self,
        name: str = "",
        *,
        window: int | None = None,
        now: Callable[[], int] | None = None,
        on_connect: Callable[[tuple[str, int], MemoryStream], MemoryStream] | None = None,
    ) -> None:
        self.name = name
        self.inbox = bytearray()
        self.sent = bytearray()
        self.peer: MemoryStream | None = None
        self._handle = _handle()
        self._connected = False
        self._closed = False
        self._deadline: int | None = None
        self._window = window
        self._now = now or (lambda: 0)
        self._on_connect = on_connect
        self._spins = 0

    def feed(self, data: bytes) -> None:
        """Deliver bytes from the network to this end (the test's wire)."""
        self.inbox.extend(data)

    @property
    def handle(self) -> Handle:
        return self._handle

    @property
    def wrapped(self) -> bool:
        return False

    @property
    def deadline(self) -> int | None:
        return self._deadline

    def setdeadline(self, deadline: int | None) -> None:
        self._deadline = deadline

    def connect(self, peer: tuple[str, int]) -> None:
        self._guard(connected=False)
        if self._connected:
            raise NotSupported("connect on an already-connected stream is refused by name")
        if self._on_connect is None:
            raise NotSupported("this fill has no connect script")
        far = self._on_connect(peer, self)
        self.peer = far
        far.peer = self
        self._connected = True
        far._connected = True

    def read(self, size: int) -> bytes:
        self._guard()
        if self.inbox:
            chunk = bytes(self.inbox[:size])
            del self.inbox[:size]
            self._spins = 0
            return chunk
        if self.peer is None or self.peer._closed:
            return b""
        raise WouldBlock("nothing to read yet")

    def write(self, data: bytes) -> int:
        self._guard()
        if self.peer is None:
            raise NotConnected("this stream has no peer")
        if self.peer._closed:
            raise ConnectionReset("the peer is gone")
        if self._window == 0:
            raise WouldBlock("the window is closed")
        take = len(data) if self._window is None else min(len(data), self._window)
        self.sent.extend(data[:take])
        self.peer.inbox.extend(data[:take])
        self._spins = 0
        return take

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        self._guard()
        ready = Interest(0)
        if self.inbox or self.peer is None or self.peer._closed:
            ready |= Interest.READ
        if self.peer is not None and not self.peer._closed and self._window != 0:
            ready |= Interest.WRITE
        awake = ready & interest
        if awake:
            self._spins = 0
            return awake
        self._spins += 1
        if self._spins > 1000:
            raise RuntimeError(f"the in-memory fill {self.name!r} deadlocked: nothing can arrive")
        return Interest(0)  # an empty wake — a timeout is never an error (§4)

    def close(self) -> None:
        self._closed = True

    def _guard(self, *, connected: bool = True) -> None:
        if self._closed:
            raise StaleHandle("the socket is gone")
        if connected and not self._connected:
            raise NotConnected("this stream is not connected")
        if self._deadline is not None and self._now() >= self._deadline:
            raise Timeout("the deadline passed")


class MemoryPair:
    """One duplex byte pair: `client` and `server` are two connected ends."""

    def __init__(self, name: str = "") -> None:
        self.client = MemoryStream(f"{name}.client")
        self.server = MemoryStream(f"{name}.server")
        self.client.peer = self.server
        self.server.peer = self.client
        self.client._connected = True
        self.server._connected = True


class MemoryListener(Listener):
    """A scripted listening socket. With `closes_when_drained`, the listener closes
    itself once its last queued connection is accepted — the test's way of saying
    "no more connections" to a plain accept loop. A port of 0 assigns one and `local`
    reads it back."""

    def __init__(
        self,
        pending: tuple[MemoryStream, ...] = (),
        *,
        closes_when_drained: bool = True,
    ) -> None:
        self._pending: deque[MemoryStream] = deque()
        self._closed = False
        self._closes_when_drained = closes_when_drained
        self._local: tuple[str, int] = ("0.0.0.0", 0)
        self._handle = _handle()
        for stream in pending:
            self.push(stream)

    def push(self, stream: MemoryStream) -> None:
        """A connection arrives (the test's wire)."""
        stream._connected = True
        self._pending.append(stream)

    def bind(self, peer: tuple[str, int]) -> None:
        global _EPHEMERAL
        host, port = peer
        if port == 0:
            port = _EPHEMERAL
            _EPHEMERAL += 1
        self._local = (host, port)

    @property
    def handle(self) -> Handle:
        return self._handle

    def local(self) -> tuple[str, int]:
        return self._local

    def accept(self) -> Stream:
        if self._closed:
            raise StaleHandle("the socket is gone")
        if not self._pending:
            if self._closes_when_drained:
                self._closed = True
                raise StaleHandle("the listener is closed")
            raise WouldBlock("no connection is waiting")
        stream = self._pending.popleft()
        if not self._pending and self._closes_when_drained:
            self._closed = True
        return stream

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        if self._closed:
            raise StaleHandle("the socket is gone")
        if self._pending and (interest & Interest.READ):
            return Interest.READ
        return Interest(0)

    def close(self) -> None:
        self._closed = True


class MemoryStreams(Streams):
    """The scripted seam: `queue_connect`/`queue_listener` say what connect/listen
    reach, and `calls` records every on-demand creation a protocol makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, int]]] = []
        self._connects: deque[tuple[MemoryStream, bytes, bool]] = deque()
        self._listeners: deque[MemoryListener] = deque()

    def queue_connect(
        self, far: MemoryStream, *, sends: bytes = b"", closes: bool = False
    ) -> None:
        """Script what the next `connect` reaches: the far end of that channel, and
        its opening move (the bytes it sends, and whether it closes after — a data
        connection's EOF is the end of the file, §12)."""
        far._connected = True
        self._connects.append((far, sends, closes))

    def queue_listener(self, listener: MemoryListener) -> None:
        """Script what the next `listen` hands out (a listener with its pending
        connections already queued)."""
        self._listeners.append(listener)

    def open(self) -> Stream:
        return MemoryStream(on_connect=self._take)

    def listen(self, peer: tuple[str, int], backlog: int = 16) -> MemoryListener:
        self.calls.append(("listen", peer))
        listener = self._listeners.popleft() if self._listeners else MemoryListener()
        listener.bind(peer)
        return listener

    def _take(self, peer: tuple[str, int], near: MemoryStream) -> MemoryStream:
        self.calls.append(("connect", peer))
        if not self._connects:
            raise NotSupported("no connect is scripted for this call")
        far, sends, closes = self._connects.popleft()
        near.peer = far
        far.peer = near
        near._connected = True
        far._connected = True
        if sends:
            far.write(sends)
        if closes:
            far.close()
        return far


class DummyWrappedStream(Stream):
    """The TLS stand-in for the opacity pin (§12): delegates everything to its inner
    stream and claims `wrapped=True`. Protocol code must not be able to tell."""

    def __init__(self, inner: Stream) -> None:
        self.inner = inner

    @property
    def handle(self) -> Handle:
        return self.inner.handle

    @property
    def wrapped(self) -> bool:
        return True

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
        return self.inner.wait(interest, timeout)

    def close(self) -> None:
        self.inner.close()


def dummy_wrapper(stream: Stream, config: TlsConfig) -> Stream:
    """The dummy card's fill of the wrap seam."""
    return DummyWrappedStream(stream)


@contextlib.contextmanager
def dummy_tls() -> Iterator[None]:
    """Install the dummy wrapper for one test, and take it away again."""
    install_wrapper(dummy_wrapper)
    try:
        yield
    finally:
        install_wrapper(None)


def sample_tls(role: Role = Role.CLIENT) -> TlsConfig:
    """One honest TlsConfig for the tests (§11: one card, both roles)."""
    return TlsConfig(
        path="net/tls/0",
        role=role,
        certificates=(),
        roots="net/secret/roots",
        alpn=("ftp",),
        server_name=None,
        min_version="1.3",
    )


def maybe_wrap(stream: Stream, wrapped: bool) -> Stream:
    """Run a scenario plain or through the dummy wrapper — the same answers either way."""
    return DummyWrappedStream(stream) if wrapped else stream
