"""The ftp server face (docs/25 §12): the control dialogue and the PASV/PORT data
connections it opens and closes itself.

A thin face over the one codec — the parser is `codec.py`'s, shared with the client so
the two faces can never disagree. What the server serves is a seam (`Files`); the
dialogue is ordinary Python on the same strand the guest stdlib runs (docs/23 §4.3).
The accept loop is the same plain poll loop the http face uses — no scheduler, no
queue, no thread pool, no event loop; the run-cell machine binds that later
(docs/23 §4.5).

The data connection is created on demand (§12): PASV opens a listener the protocol
closes itself, PORT connects to the endpoint the client named. A command line that
does not parse is answered 500 and the dialogue continues — a refusal on the control
channel has an error channel of its own, and nothing is ever half-applied.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ...schema import Interest, MalformedRequest, Refusal, TlsConfig, WouldBlock
from ..framing import read_to_eof, write_all
from ..stream import Listener, Stream, Streams, accept_loop, wrap
from .codec import (
    Command,
    Reply,
    format_endpoint,
    parse_endpoint,
    read_command,
    write_reply,
)


class Files(ABC):
    """The store the server serves — the seam a caller fills.

    `read` answers a missing path with a `LookupError` (the dialogue answers 550);
    `write` that cannot complete raises `OSError` (also 550). Nothing else about a
    store enters this face — a cell tree, a dict, a tree on a disk are all just fills.
    """

    @abstractmethod
    def read(self, path: str) -> bytes:
        """The file's bytes; a missing path raises LookupError."""

    @abstractmethod
    def write(self, path: str, data: bytes) -> None:
        """Store the bytes; a write that cannot complete raises OSError."""


class _Data:
    """One connection's data-channel state — TRANSIENT (§14): never in a successor."""

    def __init__(self) -> None:
        self.listener: Listener | None = None  # PASV: the listener the protocol opened
        self.target: tuple[str, int] | None = None  # PORT: where the server connects

    def clear(self) -> None:
        if self.listener is not None:
            self.listener.close()
            self.listener = None
        self.target = None


class Server:
    """The ftp server: a store seam, the stream seam, and the control dialogue."""

    def __init__(
        self,
        files: Files,
        streams: Streams,
        *,
        tls: TlsConfig | None = None,
        host: str = "127.0.0.1",
    ) -> None:
        self._files = files
        self._streams = streams
        self._tls = tls
        self._host = host  # the address clients reach data connections at

    def handle(self, stream: Stream) -> None:
        """One control connection: greet, run the dialogue until QUIT or a clean close.

        The stream is left open — the accept loop (or the run cell, later) owns its
        lifetime.
        """
        state = _Data()
        write_reply(stream, Reply(220, ("service ready",)))
        try:
            while True:
                try:
                    command = read_command(stream)
                except MalformedRequest:
                    write_reply(stream, Reply(500, ("bad command line",)))
                    continue  # the line was consumed: the dialogue continues, honestly
                except Refusal:
                    write_reply(stream, Reply(500, ("bad command line",)))
                    return  # the stream is out of step: end the dialogue, never guess
                if command is None:
                    return  # the peer closed cleanly
                reply = self._step(stream, command, state)
                write_reply(stream, reply)
                if reply.code == 221:
                    return
        finally:
            state.clear()

    def serve(self, listener: Listener) -> None:
        """The plain accept loop (§12) until the listener closes — the seam the
        run-cell binding takes over (docs/23 §4.5)."""
        accept_loop(listener, self._run)

    # ---- the dialogue: one command in, one reply out, data on demand ----

    def _step(self, control: Stream, command: Command, state: _Data) -> Reply:
        verb = command.verb
        if verb == "USER":
            return Reply(331, ("password, please",))
        if verb == "PASS":
            return Reply(230, ("logged in",))
        if verb in ("NOOP", "TYPE"):
            return Reply(200, ())
        if verb == "QUIT":
            return Reply(221, ("goodbye",))
        if verb == "PASV":
            return self._pasv(state)
        if verb == "PORT":
            return self._port(command, state)
        if verb == "RETR":
            return self._retr(control, command, state)
        if verb == "STOR":
            return self._stor(control, command, state)
        return Reply(502, ("not implemented",))

    def _pasv(self, state: _Data) -> Reply:
        state.clear()
        try:
            listener = self._streams.listen((self._host, 0))
        except Refusal as refusal:
            return Reply(425, (str(refusal),))
        state.listener = listener
        host, port = listener.local()
        return Reply(227, (f"Entering Passive Mode ({format_endpoint((host, port))})",))

    def _port(self, command: Command, state: _Data) -> Reply:
        state.clear()
        try:
            state.target = parse_endpoint(command.argument)
        except Refusal as refusal:
            return Reply(501, (str(refusal),))
        return Reply(200, ())

    def _retr(self, control: Stream, command: Command, state: _Data) -> Reply:
        try:
            payload = self._files.read(command.argument)
        except LookupError:
            return Reply(550, ("no such file",))
        data = self._data(state)
        if isinstance(data, Reply):
            return data
        try:
            write_reply(control, Reply(150, ("sending",)))
            write_all(data, payload)
        except Refusal as refusal:
            return Reply(426, (str(refusal),))  # the transfer aborted; nothing half-sent
        finally:
            data.close()
            state.clear()
        return Reply(226, ("done",))

    def _stor(self, control: Stream, command: Command, state: _Data) -> Reply:
        data = self._data(state)
        if isinstance(data, Reply):
            return data
        try:
            write_reply(control, Reply(150, ("receiving",)))
            payload = read_to_eof(data)
            self._files.write(command.argument, payload)
        except OSError:
            return Reply(550, ("the write did not complete",))
        except Refusal as refusal:
            return Reply(426, (str(refusal),))
        finally:
            data.close()
            state.clear()
        return Reply(226, ("done",))

    def _data(self, state: _Data) -> Stream | Reply:
        """The data connection for one transfer, opened on demand — a Reply is the
        honest answer when the dialogue has no channel to open."""
        try:
            if state.listener is not None:
                return self._wrap(self._accept(state.listener))
            if state.target is not None:
                return self._wrap(self._streams.connect(state.target))
        except Refusal as refusal:
            return Reply(425, (str(refusal),))
        return Reply(503, ("use PASV or PORT first",))

    def _accept(self, listener: Listener) -> Stream:
        while True:
            try:
                return listener.accept()
            except WouldBlock:
                listener.wait(Interest.READ)  # level-triggered: wait, then try again

    def _wrap(self, stream: Stream) -> Stream:
        if self._tls is None:
            return stream
        try:
            return wrap(stream, self._tls)
        except BaseException:
            stream.close()
            raise

    def _run(self, stream: Stream) -> None:
        self.handle(self._wrap(stream))
