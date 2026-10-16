"""The datagram contract (docs/25 §11, §12): whole messages, addressed, non-blocking.

A `DatagramSocket` is what a protocol sees: a socket handle (§5) that sends and
receives whole datagrams to and from named endpoints — the face the WireGuard tunnel
rides ("over cap.net.socket datagrams"). The contract is deliberately socket-free: a
seat's fill (the host UDP fill, the in-image stack) implements these faces and
protocol code never reaches past them. The in-memory fill pair lives in this same
module so runtime and tests share ONE face — there is no second, test-only contract.

The primitive path is non-blocking, the transport family's rule (§4): `recvfrom`
refuses `WouldBlock` when nothing is waiting (an empty queue is never EOF — datagrams
have no EOF), and a wait is a level-triggered readiness wake. A bind target that is
already taken refuses `AddressInUse` (§15), a closed socket refuses `StaleHandle`
(§14) — "the socket is gone", never a silent new object.

Endpoints are `net.schema.Endpoint` values — `ip` bytes of one family (4 or 16; the
length is the family, §17) and a port. A port of 0 assigned at bind reads back on
`local` (never guess a fixed port); a wildcard bind (`ip` all zero, the device's
`0.0.0.0:listen_port`) takes any host at its port.

A datagram carries its `src`, `dst` and `payload` as one value: what `recvfrom`
returns is what crossed the wire, source included — §2.1's roaming needs exactly
that source (an authenticated packet's apparent origin updates the peer's endpoint),
so the fill never hides it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass

from ..schema import (
    AddressInUse,
    Endpoint,
    Handle,
    Interest,
    StaleHandle,
    WouldBlock,
)


@dataclass(frozen=True)
class Datagram:
    """One whole datagram as it crossed the wire: who sent it, to whom, what it
    carried. `recvfrom` answers one of these; a fill's wire history is made of them."""

    src: Endpoint
    dst: Endpoint
    payload: bytes


class DatagramSocket(ABC):
    """One datagram socket: a handle (§5), bound or not, addressed sends and receives."""

    @property
    @abstractmethod
    def handle(self) -> Handle:
        """The socket behind this datagram — (object_id, generation), stale refused."""

    @property
    @abstractmethod
    def local(self) -> Endpoint | None:
        """The bound name (host, port) — a port of 0 assigned at bind reads back here;
        None before bind."""

    @abstractmethod
    def sendto(self, data: bytes, endpoint: Endpoint) -> int:
        """Send one whole datagram to endpoint; the count sent (a whole datagram or
        nothing — a partial message is a lie). Unbound sends bind an assigned port
        first, like a host socket does."""

    @abstractmethod
    def recvfrom(self) -> Datagram:
        """One whole datagram, source endpoint included. `WouldBlock` when nothing is
        waiting — datagrams have no EOF and never answer `b""`."""

    @abstractmethod
    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        """Wait for readiness — level-triggered (§4). Returns the ready bits asked
        for; an explicit timeout that expires is an empty wake, not an error."""

    @abstractmethod
    def close(self) -> None:
        """Close the socket. Idempotent; afterwards operations refuse `StaleHandle`
        (§14) — "the socket is gone", never a silent new object."""


class Datagrams(ABC):
    """The datagram seam (§11) — what a seat fills (cap.net.socket)."""

    @abstractmethod
    def open(self) -> DatagramSocket:
        """An unbound datagram socket; its first send assigns a port."""

    @abstractmethod
    def bind(self, endpoint: Endpoint) -> DatagramSocket:
        """A datagram socket bound to endpoint; a port of 0 assigns one and `local`
        reads it back. A target already taken refuses `AddressInUse`."""


# ---- the in-memory fill pair: one face, runtime and test (the honest fill) ----

_HANDLES = 0


def _handle() -> Handle:
    global _HANDLES
    _HANDLES += 1
    return (_HANDLES, 1)


def _wildcard(ip: bytes) -> bool:
    """An all-zero address binds every host of its family (the device's
    `0.0.0.0:listen_port`)."""
    return not any(ip)


class MemoryDatagramSocket(DatagramSocket):
    """One socket on the in-memory fabric: `inbox` is what the wire delivered here —
    whole `Datagram`s, source included (the fill is the test's wire; a test may look
    at what waits, and `MemoryDatagrams.take` may withdraw it)."""

    def __init__(self, fabric: MemoryDatagrams, local: Endpoint | None) -> None:
        self._fabric = fabric
        self._local: Endpoint | None = local
        self.inbox: deque[Datagram] = deque()
        self._handle = _handle()
        self._closed = False

    @property
    def handle(self) -> Handle:
        return self._handle

    @property
    def local(self) -> Endpoint | None:
        self._guard()
        return self._local

    @property
    def closed(self) -> bool:
        return self._closed

    def _guard(self) -> None:
        if self._closed:
            raise StaleHandle("the socket is gone")

    def sendto(self, data: bytes, endpoint: Endpoint) -> int:
        self._guard()
        if self._local is None:
            self._local = self._fabric._assign()
            self._fabric._register(self)
        message = Datagram(self._local, endpoint, bytes(data))
        self._fabric._cross(message)  # a send IS a delivery (the wire is not a queue)
        return len(data)

    def recvfrom(self) -> Datagram:
        self._guard()
        if not self.inbox:
            raise WouldBlock("nothing to receive yet")
        return self.inbox.popleft()

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        self._guard()
        ready = Interest(0)
        if Interest.READ in interest and self.inbox:
            ready |= Interest.READ
        if Interest.WRITE in interest:
            ready |= Interest.WRITE  # one whole datagram always goes now
        return ready & interest

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fabric._unregister(self)


class MemoryDatagrams(Datagrams):
    """The in-memory datagram fabric: sockets on one wire, one delivery rule.

    Every datagram that crosses is recorded in `delivered` (the wire history, whole
    and in order) and delivered to the socket bound at its `dst` — a wildcard bind
    takes any host at its port, a send to nobody is a recorded drop, never a lie
    about success. `deliver` injects one datagram with an apparent source and `take`
    withdraws one waiting datagram — the test's hands on the wire. Together they are
    the roaming seam (§2.1: "this simple thing lets WireGuard roam") a gate drives
    directly: change the apparent source of a valid packet and watch the endpoint
    move.
    """

    def __init__(self) -> None:
        self.delivered: list[Datagram] = []
        self._sockets: dict[tuple[bytes, int], MemoryDatagramSocket] = {}
        self._next_port = 49152  # the first assigned port; never a fixed port number

    def open(self) -> MemoryDatagramSocket:
        return MemoryDatagramSocket(self, None)

    def bind(self, endpoint: Endpoint) -> MemoryDatagramSocket:
        host, port = endpoint.ip, endpoint.port
        if port == 0:
            endpoint = Endpoint(host, self._assign().port, endpoint.scope)
        key = (endpoint.ip, endpoint.port)
        if key in self._sockets:
            raise AddressInUse("the bind target is already taken")
        socket = MemoryDatagramSocket(self, endpoint)
        self._sockets[key] = socket
        return socket

    def deliver(self, src: Endpoint, dst: Endpoint, payload: bytes) -> None:
        """Inject one datagram with an apparent source — the roaming seam (§2.1)."""
        self._cross(Datagram(src, dst, bytes(payload)))

    def take(self, dst: Endpoint) -> Datagram | None:
        """Withdraw the datagram waiting at `dst` — the test's hand on the wire, so a
        gate can re-address a packet (its source endpoint) instead of re-sending."""
        socket = self._lookup(dst)
        if socket is None or not socket.inbox:
            return None
        return socket.inbox.popleft()

    def _cross(self, message: Datagram) -> None:
        self.delivered.append(message)
        socket = self._lookup(message.dst)
        if socket is not None:
            socket.inbox.append(message)

    def _assign(self) -> Endpoint:
        port = self._next_port
        self._next_port += 1
        return Endpoint(b"\x00\x00\x00\x00", port, None)

    def _lookup(self, endpoint: Endpoint) -> MemoryDatagramSocket | None:
        socket = self._sockets.get((endpoint.ip, endpoint.port))
        if socket is not None and not socket.closed:
            return socket
        for (ip, port), candidate in self._sockets.items():  # the wildcard bind (§11)
            if port == endpoint.port and _wildcard(ip) and not candidate.closed:
                return candidate
        return None

    def _register(self, socket: MemoryDatagramSocket) -> None:
        local = socket._local
        if local is not None:
            self._sockets[(local.ip, local.port)] = socket

    def _unregister(self, socket: MemoryDatagramSocket) -> None:
        local = socket._local
        key = (local.ip, local.port) if local is not None else None
        if key in self._sockets and self._sockets[key] is socket:
            del self._sockets[key]
