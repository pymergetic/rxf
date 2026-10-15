"""The byte-stream contract (docs/25 §12): read, write, wait, close — deadlines as
deadlines, streams and listeners on demand, and TLS as an optional wrapper.

A `Stream` is what a protocol sees: a socket handle (§5), wrapped or not. The contract
is deliberately socket-free — a seat's fill (or the in-memory test fill) implements
these faces, protocol code never reaches past them, and `wrap` (§11) may put a secure
stream in between without one line of protocol code noticing. `net.schema.Stream` is
the state cell of such a stream; the ABC here is its live contract.

The primitive path is non-blocking: `read`/`write` refuse `WouldBlock` when nothing can
move, and a wait is a readiness wake (§4) — level-triggered only. "Blocking" is
composed: wait for readiness, then run the operation. A deadline is a tick on the edge
timer (S9), owned by the fill's clock; once it passes, every operation refuses
`Timeout`. A wait with an explicit timeout that expires is an empty wake, never an
error.

Streams on demand (§12) are why `connect` and `listen` are part of the contract: FTP's
data connection is created at runtime, and a passive data socket is a listener the
protocol opens and closes itself. The generic mechanism covers that, so no protocol
bolts anything on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from ..schema import (
    Handle,
    Interest,
    NotConnected,
    NotSupported,
    StaleHandle,
    TlsConfig,
    WouldBlock,
)


class Stream(ABC):
    """One byte stream: a socket handle (§5), connected or not, wrapped or not."""

    @property
    @abstractmethod
    def handle(self) -> Handle:
        """The socket behind this stream — (object_id, generation), stale refused."""

    @property
    @abstractmethod
    def wrapped(self) -> bool:
        """Whether a TLS wrapper sits in between — the protocol cannot tell either way."""

    @property
    @abstractmethod
    def deadline(self) -> int | None:
        """The current deadline in the fill's ticks (S9), or None."""

    @abstractmethod
    def setdeadline(self, deadline: int | None) -> None:
        """Set the deadline; None clears it. Past it, operations refuse `Timeout`."""

    @abstractmethod
    def connect(self, peer: tuple[str, int]) -> None:
        """Connect this stream's socket to peer (host, port) — on-demand streams (§12).

        An unconnected stream refuses `NotConnected` on read/write/wait until this
        succeeds. host is an address literal or a name the fill resolves.
        """

    @abstractmethod
    def read(self, size: int) -> bytes:
        """Up to size bytes. `b""` means the peer closed (EOF); an empty-but-open
        stream refuses `WouldBlock` — the two are never confused."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Accept as much as the stream can now, return the count (partial writes are
        honest); refuse `WouldBlock` when nothing at all can be accepted."""

    @abstractmethod
    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        """Wait for readiness — level-triggered (§4). Returns the ready bits asked
        for; an explicit timeout that expires is an empty wake, not an error."""

    @abstractmethod
    def close(self) -> None:
        """Close the stream. Idempotent; afterwards operations refuse `StaleHandle`
        (§14) — "the socket is gone", never a silent new object."""


class Listener(ABC):
    """A listening socket's contract: accept on readiness, close when done."""

    @property
    @abstractmethod
    def handle(self) -> Handle:
        """The socket behind this listener."""

    @abstractmethod
    def local(self) -> tuple[str, int]:
        """The bound name (host, port) — a port of 0 assigned at bind reads back here."""

    @abstractmethod
    def accept(self) -> Stream:
        """The next waiting connection, already connected. `WouldBlock` when none is
        waiting — the accept loop's level-triggered retry."""

    @abstractmethod
    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        """Readiness of the listening socket — the same wake machinery as streams."""

    @abstractmethod
    def close(self) -> None:
        """Stop listening; afterwards wait/accept refuse as a closed stream does."""


class Streams(ABC):
    """Streams and listeners on demand (§12) — the seam a seat fills (cap.net.socket).

    FTP's data connection is why this exists: protocols create streams at runtime and
    open and close their own passive listeners. No real socket lives in the library;
    a fill (POSIX host, the in-image stack, the test pair) implements `open`/`listen`.
    """

    @abstractmethod
    def open(self) -> Stream:
        """An unconnected stream; `Stream.connect` completes it."""

    @abstractmethod
    def listen(self, peer: tuple[str, int], backlog: int = 16) -> Listener:
        """A listening socket at peer; a port of 0 assigns one and `local` reads it
        back (never guess a fixed port)."""

    def connect(self, peer: tuple[str, int]) -> Stream:
        """An outbound stream to peer: `open` + `connect`, closed if connect refuses."""
        stream = self.open()
        try:
            stream.connect(peer)
        except BaseException:
            stream.close()
            raise
        return stream


def accept_loop(listener: Listener, run: Callable[[Stream], None]) -> None:
    """The plain accept loop over stream readiness (§12): no scheduler, no queue, no
    thread pool, no event loop.

    Each accepted stream is handed to `run` and closed after it returns. The run-cell
    binding (docs/23 §4.5) later wraps each `run` in a run cell; this loop stays plain.
    Closing the listener ends the loop — that is the only stop.
    """
    while True:
        try:
            listener.wait(Interest.READ)
        except (StaleHandle, NotConnected):
            return  # the listener is closed: the plain loop ends, nothing queued
        try:
            stream = listener.accept()
        except WouldBlock:
            continue  # level-triggered: readiness may have moved on since the wake
        try:
            run(stream)
        finally:
            stream.close()


# ---- the TLS seam (§11): one wrapper interface, the card is a later card ----

Wrapper = Callable[[Stream, TlsConfig], Stream]

_wrapper: Wrapper | None = None


def install_wrapper(wrapper: Wrapper | None) -> None:
    """Install the TLS card's fill of the seam (§11), or remove it with None.

    Until a card installs one, `wrap` refuses `NotSupported` by name — a config that
    names TLS must never ride plaintext on a silent passthrough.
    """
    global _wrapper
    _wrapper = wrapper


def wrap(stream: Stream, config: TlsConfig) -> Stream:
    """Wrap a stream for TLS (§11). The TLS card is a later card; this is its seam.

    Protocol code calls this at construction when a `TlsConfig` is given and never
    learns the difference — that is the whole optional-TLS mechanism.
    """
    if _wrapper is None:
        raise NotSupported("the TLS card is a later card; no wrap fill is installed")
    return _wrapper(stream, config)
