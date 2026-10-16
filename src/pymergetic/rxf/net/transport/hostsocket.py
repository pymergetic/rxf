"""The host POSIX fill of `cap.net.socket` (docs/25 §5): real TCP sockets over the
`net.transport.stream` contracts, so the http/ftp transports run over an actual
network and not just the in-memory fills.

`HostStreams` is the seat's seam: streams and listeners appear **on demand** (§12) —
constructing it opens nothing, `open()` allocates no OS socket at all, and a file
descriptor appears only when a protocol calls `connect`/`listen`. A listener bound to
port 0 gets an ephemeral port and `local()` reads it back; nothing in the library or
its tests ever guesses a fixed port. `TCP_NODELAY` is set on by default on every
socket this fill makes (no knob turns it off in the first build).

The primitive path is non-blocking and `wait` is level-triggered (§4), the same shape
the in-memory fills pin: `wait` answers the ready bits it was asked for, askable again
without consuming anything, and "blocking" is composed — wait for readiness, then run
the operation (exactly what `framing.py` does). One named host difference: the park
inside `wait` is capped at 100 ms per `select` call, because a POSIX `close` from
another thread does not wake a parked `select` — the closed-state re-check after each
cap is what lets a closed listener end a parked accept loop honestly. The wake the
caller sees is unchanged: readiness bits, an empty timeout wake, or a refusal.

A deadline is an absolute tick of the fill's clock (§4, edge timer S9), as the
contract says. This fill's tick is `time.monotonic()` in milliseconds — `ticks()` is
now, `ticks() + 5_000` is five seconds from now. `setdeadline` takes such an absolute
tick and `None` clears it; once it passes, every operation refuses `Timeout` —
including a `wait` whose park runs into it. A `wait` whose *explicit* timeout expires
instead answers an empty wake, never an error (§4). `setdeadline` is a field set, not
a socket operation (the in-memory fill's shape): it works even on a closed stream.

Honest errno -> refusal mapping (docs/25 §15) — protocol code never sees an OSError:

    WouldBlock          EAGAIN (and EWOULDBLOCK where it differs) — nothing can move
                        yet; the composed loops retry through `wait`
    (never surfaced)    EINTR — the runtime retries (PEP 475)
    ConnectionRefused   ECONNREFUSED — no listener answered the connect
    ConnectionReset     ECONNRESET, EPIPE — the peer is gone
    Timeout             ETIMEDOUT, and the fill's own deadline passing
    NotConnected        ENOTCONN, and any read/write/wait on an unconnected stream
    StaleHandle         EBADF — but really every operation after `close`, refused from
                        the fill's own state before the kernel is ever asked
    AddressInUse        EADDRINUSE — the bind target is taken
    InvalidEndpoint     socket.gaierror (EAI_*), EADDRNOTAVAIL, EINVAL, ELOOP,
                        ENAMETOOLONG — and endpoints that name no peer at all (port 0
                        for connect, an out-of-range port for either face)
    NetUnreachable      ENETUNREACH
    HostUnreachable     EHOSTUNREACH
    LinkDown            ENETDOWN
    MessageTooLong      EMSGSIZE
    PermissionDenied    EACCES, EPERM
    NotSupported        EAFNOSUPPORT, EPROTONOSUPPORT, ESOCKTNOSUPPORT, EOPNOTSUPP
    Refusal (base)      any other host errno, naming it — never a raw OSError

Named gaps — pinned in `tests/test_net_socket.py` where they are behavior, listed here
where they are scope; never pretended away:

    - Datagram (UDP) and raw sockets are a later fill (`SocketForm.DATAGRAM`/`RAW`);
      this module is streams and listeners only.
    - `Interest.LINK` is the device face's readiness (§7); a socket's `wait` refuses it
      `NotSupported` by name rather than parking on a wake that can never come.
    - `read(0)` is refused `NotSupported`: `b""` is reserved for EOF and a zero-byte
      read would lie about it.
    - A write after the peer's *orderly* close (FIN) may still be accepted — a TCP
      half-close is real and pretending otherwise would lie. A write once the peer is
      gone (a reset) refuses `ConnectionReset`; the tests drive that path.
    - A refused `connect` leaves the stream unconnected (the half-made socket is
      dropped): read/write/wait then refuse `NotConnected`, while `Streams.connect` —
      which closes what it opened on failure — leaves `StaleHandle` behind.
    - `SO_REUSEADDR` is not set: every probe binds port 0, so the TIME_WAIT dance never
      arises. It is a server knob of a later card.
    - `local()` reads back (host, port) exactly as the contract shapes it; an AF_INET6
      flowinfo or scope id is outside that shape and is dropped (`::1` needs none).
    - A name for `connect` resolves through `getaddrinfo` and the first SOCK_STREAM
      answer is used; a specific family wants an address literal. Both families work;
      v4-mapped v6 is refused by name (`IPV6_V6ONLY` is set, §17).
    - A close racing a park in another thread wakes that park, and the wake is
      refused `StaleHandle` rather than handed out as readiness — so a parked accept
      loop ends when its listener closes. The park's last `select` may still observe a
      reused fd number; the same closed-state re-check means the caller only ever sees
      `StaleHandle`.
"""

from __future__ import annotations

import errno
import ipaddress
import os
import select
import socket
import time
from collections.abc import Callable
from typing import Any, NoReturn

from ..schema import (
    AddressInUse,
    ConnectionRefused,
    ConnectionReset,
    Handle,
    HostUnreachable,
    Interest,
    InvalidEndpoint,
    LinkDown,
    MessageTooLong,
    NetUnreachable,
    NotConnected,
    NotSupported,
    PermissionDenied,
    Refusal,
    StaleHandle,
    Timeout,
    WouldBlock,
)
from .stream import Listener, Stream, Streams

TICKS_PER_SECOND = 1000  # the fill's clock: monotonic milliseconds (S9)

_PARK_CAP_MS = 100  # one select call; see the module docstring on close vs. park

_HANDLES = 0

_ERRNO_REFUSALS: dict[int, type[Refusal]] = {
    errno.EAGAIN: WouldBlock,
    errno.ECONNREFUSED: ConnectionRefused,
    errno.ECONNRESET: ConnectionReset,
    errno.EPIPE: ConnectionReset,
    errno.ETIMEDOUT: Timeout,
    errno.ENOTCONN: NotConnected,
    errno.EBADF: StaleHandle,
    errno.EADDRINUSE: AddressInUse,
    errno.EADDRNOTAVAIL: InvalidEndpoint,
    errno.EINVAL: InvalidEndpoint,
    errno.ELOOP: InvalidEndpoint,
    errno.ENAMETOOLONG: InvalidEndpoint,
    errno.ENETUNREACH: NetUnreachable,
    errno.EHOSTUNREACH: HostUnreachable,
    errno.ENETDOWN: LinkDown,
    errno.EMSGSIZE: MessageTooLong,
    errno.EACCES: PermissionDenied,
    errno.EPERM: PermissionDenied,
    errno.EAFNOSUPPORT: NotSupported,
    errno.EPROTONOSUPPORT: NotSupported,
    errno.ESOCKTNOSUPPORT: NotSupported,
    errno.EOPNOTSUPP: NotSupported,
}
_ERRNO_REFUSALS[errno.EWOULDBLOCK] = WouldBlock  # EAGAIN's twin where it differs


def ticks() -> int:
    """The fill's clock (S9): monotonic milliseconds. A deadline is an absolute count
    of these; a `wait` timeout is a relative one."""
    return time.monotonic_ns() // 1_000_000


def _handle() -> Handle:
    global _HANDLES
    _HANDLES += 1
    return (_HANDLES, 1)


def _raise_host(error: OSError) -> NoReturn:
    """Map one host error to the §15 set (the table in the module docstring)."""
    if isinstance(error, socket.gaierror):
        raise InvalidEndpoint(f"the peer name does not resolve: {error}") from error
    mapped = _ERRNO_REFUSALS.get(error.errno) if error.errno is not None else None
    if mapped is None:
        raise Refusal(f"the host refused: {error}") from error
    raise mapped(f"the host refused: {error.strerror} (errno {error.errno})") from error


def _tune(sock: socket.socket, *, v6only: bool = True) -> None:
    """The fill's socket defaults: non-blocking (the primitive path, §4), TCP_NODELAY
    on, and v6-only on AF_INET6 — v4-mapped v6 is refused by name (§17). `v6only` is
    create-time only (the kernel refuses it later); an accepted socket inherits its
    listener's refusal instead of setting its own."""
    try:
        sock.setblocking(False)
        if v6only and sock.family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError as error:
        _raise_host(error)


def _addr(family: int, host: str, port: int) -> Any:
    if family == socket.AF_INET6:
        return (host, port, 0, 0)
    return (host, port)


def _resolve(peer: tuple[str, int], *, passive: bool) -> tuple[int, Any]:
    """The (family, sockaddr) of an endpoint. A literal picks its own family; a name
    resolves through getaddrinfo and its first SOCK_STREAM answer is used. An endpoint
    that names no peer is refused `InvalidEndpoint`."""
    host, port = peer
    if not isinstance(host, str) or not isinstance(port, int) or isinstance(port, bool):
        raise InvalidEndpoint(f"not a (host, port) endpoint: {peer!r}")
    if not 0 <= port <= 65535:
        raise InvalidEndpoint(f"not a port: {port}")
    if not passive:
        if not host:
            raise InvalidEndpoint("connect names no peer without a host")
        if port == 0:
            raise InvalidEndpoint("connect names no peer at port 0")
    if host == "":
        host = "0.0.0.0"  # the AF_INET wildcard; "::" is the AF_INET6 one
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        return family, _addr(family, host, port)
    flags = socket.AI_PASSIVE if passive else 0
    try:
        answers = socket.getaddrinfo(
            host, port, socket.AF_UNSPEC, socket.SOCK_STREAM, 0, flags
        )
    except socket.gaierror as error:
        _raise_host(error)
    family, _kind, _proto, _canon, address = answers[0]
    return family, address


def _await_connected(sock: socket.socket, deadline: int | None) -> None:
    """Park a non-blocking connect to completion (§4: wait, then run). The park is
    deadline-capped like every other; the socket's own error refuses `ConnectionReset`
    or `ConnectionRefused` through the errno table."""
    while True:
        now = ticks()
        if deadline is not None and now >= deadline:
            raise Timeout("the deadline passed before the connection completed")
        park = _PARK_CAP_MS
        if deadline is not None:
            park = min(park, max(deadline - now, 0))
        try:
            _, writable, _ = select.select([], [sock], [], park / 1000.0)
        except OSError as error:
            _raise_host(error)
        if not writable:
            continue
        fault = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if fault:
            raise OSError(fault, os.strerror(fault))
        return


class _HostSocket:
    """The machinery a host socket face shares: the in-memory fills' guard order
    (closed first, then unconnected, then the deadline), and the level-triggered park
    (§4) that `wait` is made of."""

    _sock: socket.socket | None
    _closed: bool
    _deadline: int | None

    def _guard(self, *, connected: bool = True) -> None:
        if self._closed:
            raise StaleHandle("the socket is gone")
        if connected and self._sock is None:
            raise NotConnected("this stream is not connected")
        if self._deadline is not None and ticks() >= self._deadline:
            raise Timeout("the deadline passed")

    def _live(self) -> socket.socket:
        self._guard()
        sock = self._sock
        if sock is None:
            raise NotConnected("this stream is not connected")
        return sock

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        """Wait for readiness — level-triggered (§4). The ready bits asked for come
        back ready; an explicit timeout that expires is an empty wake, never an error;
        a park that runs into the deadline refuses `Timeout` (the deadline holds on
        every operation). `Interest.LINK` is the device face's wake (§7) and is
        refused by name here."""
        sock = self._live()
        if interest & Interest.LINK:
            raise NotSupported(
                "link-state readiness is the device face's wake (§7), not a socket's"
            )
        watch = interest & (Interest.READ | Interest.WRITE | Interest.ERROR)
        if not watch:
            return Interest(0)
        limit = None if timeout is None else ticks() + timeout
        while True:
            if self._closed:
                raise StaleHandle("the socket is gone")
            now = ticks()
            if self._deadline is not None and now >= self._deadline:
                raise Timeout("the deadline passed")
            park = _PARK_CAP_MS
            if limit is not None:
                park = min(park, max(limit - now, 0))
            if self._deadline is not None:
                park = min(park, max(self._deadline - now, 0))
            readable = [sock] if watch & Interest.READ else []
            writable = [sock] if watch & Interest.WRITE else []
            exceptional = [sock] if watch & Interest.ERROR else []
            try:
                seen_read, seen_write, seen_error = select.select(
                    readable, writable, exceptional, park / 1000.0
                )
            except OSError as error:
                if self._closed:
                    raise StaleHandle("the socket is gone") from error
                _raise_host(error)
            if self._closed:
                # a close from another thread wakes the park with readiness the fill
                # refuses to hand out — the wake is `StaleHandle`, never ready bits
                raise StaleHandle("the socket is gone")
            ready = Interest(0)
            if seen_read:
                ready |= Interest.READ
            if seen_write:
                ready |= Interest.WRITE
            if seen_error:
                ready |= Interest.ERROR
            awake = ready & interest
            if awake:
                return awake
            if limit is not None and ticks() >= limit:
                return Interest(0)  # an expired explicit timeout is an empty wake (§4)
            if self._deadline is not None and ticks() >= self._deadline:
                raise Timeout("the deadline passed")


class HostStream(_HostSocket, Stream):
    """One real TCP stream. Created unconnected with no OS socket at all (`open()`), or
    already connected around an accepted socket — the on-demand shape of §12."""

    def __init__(
        self,
        sock: socket.socket | None = None,
        *,
        on_connect: Callable[[tuple[str, int]], None] | None = None,
    ) -> None:
        self._sock = sock  # None until connect asks for one — nothing opened eagerly
        self._handle = _handle()
        self._closed = False
        self._deadline: int | None = None
        self._on_connect = on_connect

    @property
    def sock(self) -> socket.socket | None:
        """The OS socket behind this stream, or None — the on-demand pin's
        introspection: a stream that was only opened has none."""
        return self._sock

    @property
    def handle(self) -> Handle:
        return self._handle

    @property
    def wrapped(self) -> bool:
        return False  # no TLS card rides this fill; `wrap` is the seam's own business

    @property
    def deadline(self) -> int | None:
        return self._deadline

    def setdeadline(self, deadline: int | None) -> None:
        self._deadline = deadline  # a field set, not a socket operation (see docstring)

    def connect(self, peer: tuple[str, int]) -> None:
        self._guard(connected=False)
        if self._sock is not None:
            raise NotSupported("connect on an already-connected stream is refused by name")
        if self._on_connect is not None:
            self._on_connect(peer)  # the seam's call log: the protocol asked, we record
        family, address = _resolve(peer, passive=False)
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            _raise_host(error)
        try:
            _tune(sock)
            try:
                sock.connect(address)
            except BlockingIOError:
                _await_connected(sock, self._deadline)
        except BaseException as error:
            sock.close()  # a refused connect leaves no half-made socket behind
            if isinstance(error, OSError):
                _raise_host(error)
            raise
        self._sock = sock

    def read(self, size: int) -> bytes:
        """Up to size bytes; `b""` is exactly the peer's close (EOF) and an
        empty-but-open stream refuses `WouldBlock` — the two are never confused."""
        sock = self._live()
        if size <= 0:
            raise NotSupported('a read of no bytes would lie about EOF; b"" is reserved')
        try:
            return sock.recv(size)
        except OSError as error:
            _raise_host(error)

    def write(self, data: bytes) -> int:
        """Accept as much as the kernel takes now and say how much (partial writes are
        honest); refuse `WouldBlock` when nothing at all can be accepted."""
        sock = self._live()
        try:
            return sock.send(data)
        except OSError as error:
            _raise_host(error)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()


class HostListener(_HostSocket, Listener):
    """One real listening TCP socket: bound (port 0 assigns an ephemeral one and
    `local()` reads it back), accepting on readiness, close when done."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._handle = _handle()
        self._closed = False
        self._deadline = None

    @property
    def sock(self) -> socket.socket | None:
        """The OS socket behind this listener, or None once it is closed."""
        return self._sock

    @property
    def handle(self) -> Handle:
        return self._handle

    def local(self) -> tuple[str, int]:
        """The bound name — the assigned ephemeral port reads back here. An AF_INET6
        flowinfo/scope is outside the contract's shape and is dropped."""
        sock = self._live()
        name = sock.getsockname()
        return (name[0], name[1])

    def accept(self) -> Stream:
        """The next waiting connection, already connected and tuned; `WouldBlock` when
        none is waiting — the accept loop's level-triggered retry."""
        sock = self._live()
        try:
            conn, _peer = sock.accept()
        except OSError as error:
            _raise_host(error)
        _tune(conn, v6only=False)  # accepted sockets do not inherit the listener's
        # non-blocking mode, and cannot set v6-only after the fact (see _tune)
        return HostStream(conn)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        sock, self._sock = self._sock, None
        if sock is not None:
            sock.close()


class HostStreams(Streams):
    """The host seam: streams and listeners on demand (§12). Constructing it opens
    nothing; `calls` records every on-demand creation a protocol makes — the same pin
    shape the in-memory fill records."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, int]]] = []

    def open(self) -> HostStream:
        """An unconnected stream — not even an OS socket yet; `connect` creates it."""
        return HostStream(on_connect=self._record)

    def listen(self, peer: tuple[str, int], backlog: int = 16) -> HostListener:
        """A listening socket at peer; port 0 assigns one and `local` reads it back."""
        self.calls.append(("listen", peer))
        family, address = _resolve(peer, passive=True)
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            _raise_host(error)
        try:
            _tune(sock)  # v6-only before bind, or the v4-mapped refusal is a lie
            sock.bind(address)
            sock.listen(backlog)
        except BaseException as error:
            sock.close()
            if isinstance(error, OSError):
                _raise_host(error)
            raise
        return HostListener(sock)

    def _record(self, peer: tuple[str, int]) -> None:
        self.calls.append(("connect", peer))
