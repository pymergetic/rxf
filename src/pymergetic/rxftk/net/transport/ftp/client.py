"""The ftp client face (docs/25 §12): the control dialogue and the data connection it
opens itself.

A thin face over the one codec — the parser is `codec.py`'s, shared with the server so
the two faces can never disagree. The data connection is why streams exist on demand
(§12): PASV connects to an endpoint the server names, PORT listens and the server
connects to it. `tls` (one argument) wraps the control stream — data streams share the
same wrapper seam and the face never learns the difference (§11).

A reply that is not the one the dialogue promised at its step is `UnexpectedReply`; a
control connection that closes mid-dialogue is `ConnectionReset`. Neither is ever
half-applied.
"""

from __future__ import annotations

from ...schema import ConnectionReset, Interest, TlsConfig, UnexpectedReply, WouldBlock
from ..framing import read_to_eof, write_all
from ..stream import Listener, Stream, Streams, wrap
from .codec import (
    Command,
    Reply,
    format_endpoint,
    parse_endpoint,
    read_reply,
    write_command,
)


class Client:
    """The ftp client over the stream seam. `streams` fills connect/listen (the data
    connections); `tls` wraps the control stream at connect."""

    def __init__(self, streams: Streams, tls: TlsConfig | None = None) -> None:
        self._streams = streams
        self._tls = tls
        self._control: Stream | None = None
        self._host = ""

    def connect(self, host: str, port: int = 21) -> Reply:
        """Open (and wrap) the control connection; returns the server's greeting."""
        stream = self._streams.connect((host, port))
        if self._tls is not None:
            try:
                stream = wrap(stream, self._tls)
            except BaseException:
                stream.close()
                raise
        self._control = stream
        self._host = host
        return self._reply()

    def command(self, verb: str, argument: str = "") -> Reply:
        """One exchange: send the command, return the reply."""
        self._send(Command(verb, argument))
        return self._reply()

    def login(self, user: str, password: str = "") -> Reply:
        """USER, then PASS when the dialogue asks for one."""
        reply = self.command("USER", user)
        if reply.code == 331:
            reply = self.command("PASS", password)
        return reply

    def pasv(self) -> Stream:
        """PASV: the server names an endpoint, the client connects — the data stream
        is created on demand and never leaks into the control dialogue."""
        reply = self.command("PASV")
        if reply.code != 227:
            raise UnexpectedReply(f"PASV answered {reply.code}, not 227")
        return self._open_data(parse_endpoint(reply.text))

    def port(self, host: str | None = None) -> Listener:
        """PORT: the client listens (a port of 0 assigns one), tells the server, and
        accepts when the transfer starts. Returns the listener it opened itself."""
        listener = self._streams.listen((host or self._host, 0))
        try:
            reply = self.command("PORT", format_endpoint(listener.local()))
        except BaseException:
            listener.close()
            raise
        if reply.code != 200:
            listener.close()
            raise UnexpectedReply(f"PORT answered {reply.code}, not 200")
        return listener

    def retr(self, path: str, *, active: bool = False) -> bytes:
        """RETR: one file, over a data connection opened on demand (PASV, or PORT
        when active)."""
        data, listener = self._data(active)
        try:
            reply = self.command("RETR", path)
            self._expect(reply, (125, 150), "RETR")
            stream = data if data is not None else self._accept(listener)
            try:
                payload = read_to_eof(stream)
            finally:
                stream.close()
        finally:
            if listener is not None:
                listener.close()
        self._expect(self._reply(), (226,), "RETR")
        return payload

    def stor(self, path: str, data: bytes, *, active: bool = False) -> Reply:
        """STOR: one file, over a data connection opened on demand. The end of the
        data stream is the end of the file."""
        connection, listener = self._data(active)
        try:
            reply = self.command("STOR", path)
            self._expect(reply, (125, 150), "STOR")
            stream = connection if connection is not None else self._accept(listener)
            try:
                write_all(stream, data)
            finally:
                stream.close()
        finally:
            if listener is not None:
                listener.close()
        return self._expect(self._reply(), (226,), "STOR")

    def quit(self) -> Reply:
        """QUIT and close the control connection."""
        reply = self.command("QUIT")
        self.close()
        return reply

    def close(self) -> None:
        """Close the control connection (the data connections are the transfer's)."""
        if self._control is not None:
            self._control.close()
            self._control = None

    # ---- internals: the dialogue's bookkeeping, no second parser ----

    def _send(self, command: Command) -> None:
        if self._control is None:
            raise ConnectionReset("no control connection is open")
        write_command(self._control, command)

    def _reply(self) -> Reply:
        if self._control is None:
            raise ConnectionReset("no control connection is open")
        reply = read_reply(self._control)
        if reply is None:
            self.close()
            raise ConnectionReset("the control connection closed before the reply arrived")
        return reply

    def _expect(self, reply: Reply, codes: tuple[int, ...], verb: str) -> Reply:
        if reply.code not in codes:
            raise UnexpectedReply(f"{verb} answered {reply.code}, not one of {codes}")
        return reply

    def _data(self, active: bool) -> tuple[Stream | None, Listener | None]:
        """The data connection for the next transfer, opened on demand."""
        if active:
            return None, self.port()
        return self.pasv(), None

    def _open_data(self, peer: tuple[str, int]) -> Stream:
        stream = self._streams.connect(peer)
        if self._tls is None:
            return stream
        try:
            return wrap(stream, self._tls)
        except BaseException:
            stream.close()
            raise

    def _accept(self, listener: Listener | None) -> Stream:
        assert listener is not None
        while True:
            try:
                stream = listener.accept()
            except WouldBlock:
                listener.wait(Interest.READ)  # level-triggered: wait, then try again
                continue
            return self._wrap(stream)

    def _wrap(self, stream: Stream) -> Stream:
        if self._tls is None:
            return stream
        try:
            return wrap(stream, self._tls)
        except BaseException:
            stream.close()
            raise
