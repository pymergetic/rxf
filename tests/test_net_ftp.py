"""Pins for the ftp transport (docs/25 §12): one control codec, two faces, and the
data connection created on demand.

The codec tests run twice — once plain, once through a dummy wrapping Stream — and the
answers are the same either way. What is pinned beyond the dialogue: PASV and PORT
speak one endpoint grammar (parse ∘ format is the identity), the data channel is opened
on demand via the stream seam's connect/listen (the scripted fill records every call),
and the v4-only grammar refuses a v6 host `NotSupported` by name — the extended forms
are a later card, never a silent wrong address.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from pymergetic.rxf.net.schema import (
    ConnectionReset,
    InvalidEndpoint,
    MalformedMessage,
    MalformedRequest,
    MalformedResponse,
    NotSupported,
    TlsConfig,
    UnexpectedReply,
)
from pymergetic.rxf.net.transport.ftp import (
    Client,
    Command,
    Files,
    Reply,
    Server,
    format_endpoint,
    parse_endpoint,
    read_command,
    read_reply,
    write_command,
    write_reply,
)

from .test_net_fill import (
    MemoryListener,
    MemoryPair,
    MemoryStream,
    MemoryStreams,
    dummy_tls,
    maybe_wrap,
    sample_tls,
)

both = pytest.mark.parametrize("wrapped", [False, True], ids=["plain", "wrapped"])


@contextlib.contextmanager
def maybe_tls(wrapped: bool) -> Iterator[TlsConfig | None]:
    """A tls config for the wrapped runs, the dummy card installed while they run."""
    if wrapped:
        with dummy_tls():
            yield sample_tls()
    else:
        yield None


class DictFiles(Files):
    """One honest store fill: a dict, and a LookupError when a path is missing."""

    def __init__(self, entries: dict[str, bytes]) -> None:
        self.entries = dict(entries)

    def read(self, path: str) -> bytes:
        if path not in self.entries:
            raise KeyError(path)
        return self.entries[path]

    def write(self, path: str, data: bytes) -> None:
        self.entries[path] = data


# ---- the codec, both faces ----


@both
def test_command_roundtrip(wrapped: bool) -> None:
    """A command round-trips and the wire form is pinned."""
    pair = MemoryPair("ftp")
    write_command(maybe_wrap(pair.client, wrapped), Command("USER", "root"))
    assert bytes(pair.server.inbox) == b"USER root\r\n"
    assert read_command(maybe_wrap(pair.server, wrapped)) == Command("USER", "root")


@both
def test_command_verbs_normalize(wrapped: bool) -> None:
    """Verbs are upper-case words; the parse normalizes, the argument stays verbatim."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"retr File Name.txt\r\n")
    command = read_command(maybe_wrap(pair.server, wrapped))
    assert command == Command("RETR", "File Name.txt")


@both
def test_reply_roundtrip(wrapped: bool) -> None:
    """One-line and multi-line replies round-trip; the code is the dialogue's word."""
    pair = MemoryPair("ftp")
    write_reply(maybe_wrap(pair.client, wrapped), Reply(220, ("service ready",)))
    write_reply(maybe_wrap(pair.client, wrapped), Reply(227, ("first", "last")))
    assert bytes(pair.server.inbox) == (
        b"220 service ready\r\n227-first\r\n227 last\r\n"
    )
    assert read_reply(maybe_wrap(pair.server, wrapped)) == Reply(220, ("service ready",))
    assert read_reply(maybe_wrap(pair.server, wrapped)) == Reply(227, ("first", "last"))


@both
def test_empty_command_line_is_refused(wrapped: bool) -> None:
    """An empty line is not a command: MalformedRequest, the line consumed whole."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"\r\n")
    with pytest.raises(MalformedRequest):
        read_command(maybe_wrap(pair.server, wrapped))


@both
def test_reply_line_is_refused(wrapped: bool) -> None:
    """A reply line that is not one is MalformedResponse — never a wrong parse."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"garbage\r\n")
    with pytest.raises(MalformedResponse):
        read_reply(maybe_wrap(pair.server, wrapped))


@both
def test_truncated_multiline_reply_is_refused(wrapped: bool) -> None:
    """A multi-line reply the stream cuts short is MalformedResponse."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"227-first\r\n")
    pair.client.close()
    with pytest.raises(MalformedResponse):
        read_reply(maybe_wrap(pair.server, wrapped))


@both
def test_clean_eof_is_none_not_a_refusal(wrapped: bool) -> None:
    """A peer that closes at a line boundary is simply done — None, never a parse."""
    pair = MemoryPair("ftp")
    pair.client.close()
    assert read_command(maybe_wrap(pair.server, wrapped)) is None
    assert read_reply(maybe_wrap(pair.server, wrapped)) is None


# ---- the data-connection grammar (PASV and PORT speak one language) ----


def test_pasv_grammar_roundtrip() -> None:
    """parse ∘ format is the identity over the grammar — both faces agree by pin."""
    for text in (
        "Entering Passive Mode (192,0,2,1,192,0)",
        "(127,0,0,1,0,80)",
        "0,0,0,0,1,1",
    ):
        host, port = parse_endpoint(text)
        again = parse_endpoint(format_endpoint((host, port)))
        assert again == (host, port)
    assert parse_endpoint("Entering Passive Mode (192,0,2,1,192,0)") == ("192.0.2.1", 49152)
    assert format_endpoint(("192.0.2.1", 49152)) == "192,0,2,1,192,0"


def test_grammar_refusals_are_named() -> None:
    """A v6 host refuses NotSupported by name; garbage and bad ports are refusals too."""
    with pytest.raises(NotSupported):
        format_endpoint(("2001:db8::1", 21))
    with pytest.raises(InvalidEndpoint):
        format_endpoint(("example.net", 21))
    with pytest.raises(InvalidEndpoint):
        format_endpoint(("192.0.2.1", 70000))
    with pytest.raises(MalformedMessage):
        parse_endpoint("1,2,3")
    with pytest.raises(MalformedMessage):
        parse_endpoint("1,2,3,4,5,x")


# ---- the client face: the dialogue and its data connection ----


def connect_client(wrapped: bool, replies: bytes) -> tuple[Client, MemoryStreams, MemoryStream]:
    """A client whose control stream already carries the scripted replies."""
    streams = MemoryStreams()
    control = MemoryStream("control")
    streams.queue_connect(control, sends=replies)
    client = Client(streams, tls=sample_tls() if wrapped else None)
    assert client.connect("192.0.2.1").code == 220
    return client, streams, control


@both
def test_client_login_dialogue(wrapped: bool) -> None:
    """USER, PASS, the replies read — the control channel is just lines."""
    with maybe_tls(wrapped):
        client, _, control = connect_client(
            wrapped, b"220 service ready\r\n331 password, please\r\n230 logged in\r\n"
        )
        reply = client.login("root", "hunter2")
    assert reply.code == 230
    assert bytes(control.inbox) == b"USER root\r\nPASS hunter2\r\n"


@both
def test_client_retr_over_pasv(wrapped: bool) -> None:
    """PASV: the reply names the endpoint, the client connects on demand (§12)."""
    with maybe_tls(wrapped):
        client, streams, control = connect_client(
            wrapped, b"220 ready\r\n227 here (127,0,0,1,192,0)\r\n150 sending\r\n226 done\r\n"
        )
        data = MemoryStream("data.far")
        streams.queue_connect(data, sends=b"the file", closes=True)
        payload = client.retr("file")
    assert payload == b"the file"
    assert streams.calls == [
        ("connect", ("192.0.2.1", 21)),
        ("connect", ("127.0.0.1", 49152)),
    ]
    assert bytes(control.inbox) == b"PASV\r\nRETR file\r\n"


@both
def test_client_stor_over_pasv(wrapped: bool) -> None:
    """STOR: the body rides the data connection, its close is the end of the file."""
    with maybe_tls(wrapped):
        client, streams, control = connect_client(
            wrapped, b"220 ready\r\n227 here (127,0,0,1,192,0)\r\n150 receiving\r\n226 done\r\n"
        )
        data = MemoryStream("data.far")
        streams.queue_connect(data)
        reply = client.stor("up", b"the upload")
    assert reply.code == 226
    assert bytes(data.inbox) == b"the upload"
    assert bytes(control.inbox) == b"PASV\r\nSTOR up\r\n"


@both
def test_client_retr_over_port(wrapped: bool) -> None:
    """PORT: the client listens (port 0 assigns), the server connects to it."""
    with maybe_tls(wrapped):
        client, streams, control = connect_client(
            wrapped, b"220 ready\r\n200 port ok\r\n150 sending\r\n226 done\r\n"
        )
        data = MemoryPair("data")
        data.server.write(b"active mode")
        data.server.close()
        listener = MemoryListener((data.client,))
        streams.queue_listener(listener)
        payload = client.retr("file", active=True)
    assert payload == b"active mode"
    assert streams.calls == [("connect", ("192.0.2.1", 21)), ("listen", ("192.0.2.1", 0))]
    assert bytes(control.inbox) == (
        f"PORT {format_endpoint(listener.local())}\r\nRETR file\r\n".encode("ascii")
    )


def test_client_unexpected_reply_is_typed() -> None:
    """A reply that is not the one the dialogue promised is UnexpectedReply."""
    client, _, _ = connect_client(False, b"220 ready\r\n500 no PASV here\r\n")
    with pytest.raises(UnexpectedReply):
        client.pasv()


def test_client_reset_when_control_dies() -> None:
    """A control connection that closes mid-dialogue is ConnectionReset."""
    streams = MemoryStreams()
    control = MemoryStream("control")
    streams.queue_connect(control, sends=b"220 ready\r\n", closes=True)
    client = Client(streams)
    client.connect("192.0.2.1")
    with pytest.raises(ConnectionReset):
        client.command("NOOP")


# ---- the server face: the dialogue and its PASV/PORT data connections ----


@both
def test_server_dialogue(wrapped: bool) -> None:
    """The greeting, USER/PASS, NOOP, QUIT — one command in, one reply out."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"USER root\r\nPASS pw\r\nNOOP\r\nQUIT\r\nNOT A VERB\r\n")
    files = DictFiles({})
    with maybe_tls(wrapped) as tls:
        Server(files, MemoryStreams(), tls=tls).handle(maybe_wrap(pair.server, wrapped))
    assert bytes(pair.client.inbox) == (
        b"220 service ready\r\n"
        b"331 password, please\r\n"
        b"230 logged in\r\n"
        b"200\r\n"
        b"221 goodbye\r\n"
    )


@both
def test_server_retr_over_pasv(wrapped: bool) -> None:
    """PASV opens the listener the protocol closes itself; RETR sends the file."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"USER root\r\nPASS pw\r\nPASV\r\nRETR file\r\nQUIT\r\n")
    data = MemoryPair("data")
    listener = MemoryListener((data.server,))
    streams = MemoryStreams()
    streams.queue_listener(listener)
    with maybe_tls(wrapped) as tls:
        Server(DictFiles({"file": b"the file"}), streams, tls=tls).handle(
            maybe_wrap(pair.server, wrapped)
        )
    assert bytes(data.client.inbox) == b"the file"
    assert streams.calls == [("listen", ("127.0.0.1", 0))]
    host, port = listener.local()
    assert bytes(pair.client.inbox) == (
        b"220 service ready\r\n"
        b"331 password, please\r\n"
        b"230 logged in\r\n"
        + f"227 Entering Passive Mode ({format_endpoint((host, port))})\r\n".encode("ascii")
        + b"150 sending\r\n"
        b"226 done\r\n"
        b"221 goodbye\r\n"
    )


@both
def test_server_stor_over_port(wrapped: bool) -> None:
    """PORT names the client's endpoint; the server connects on demand and stores."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"PORT 127,0,0,1,192,0\r\nSTOR up\r\nQUIT\r\n")
    data = MemoryStream("data.far")
    streams = MemoryStreams()
    streams.queue_connect(data, sends=b"the upload", closes=True)
    files = DictFiles({})
    with maybe_tls(wrapped) as tls:
        Server(files, streams, tls=tls).handle(maybe_wrap(pair.server, wrapped))
    assert files.entries["up"] == b"the upload"
    assert streams.calls == [("connect", ("127.0.0.1", 49152))]
    assert bytes(pair.client.inbox) == (
        b"220 service ready\r\n200\r\n150 receiving\r\n226 done\r\n221 goodbye\r\n"
    )


def test_server_answers_550_for_a_missing_file() -> None:
    """A missing file is the store's LookupError — the dialogue answers 550, cleanly."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"RETR nope\r\nQUIT\r\n")
    with maybe_tls(False) as tls:
        Server(DictFiles({}), MemoryStreams(), tls=tls).handle(pair.server)
    assert bytes(pair.client.inbox) == b"220 service ready\r\n550 no such file\r\n221 goodbye\r\n"


def test_server_answers_500_and_continues() -> None:
    """A bad command line is 500; the dialogue continues — the line was consumed."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"\r\nNOOP\r\nQUIT\r\n")
    with maybe_tls(False) as tls:
        Server(DictFiles({}), MemoryStreams(), tls=tls).handle(pair.server)
    assert bytes(pair.client.inbox) == (
        b"220 service ready\r\n500 bad command line\r\n200\r\n221 goodbye\r\n"
    )


def test_server_answers_503_without_a_data_channel() -> None:
    """RETR before PASV/PORT is a dialogue error, answered 503 — nothing half-done."""
    pair = MemoryPair("ftp")
    pair.server.feed(b"RETR file\r\nQUIT\r\n")
    with maybe_tls(False) as tls:
        Server(DictFiles({"file": b"x"}), MemoryStreams(), tls=tls).handle(pair.server)
    assert bytes(pair.client.inbox) == (
        b"220 service ready\r\n503 use PASV or PORT first\r\n221 goodbye\r\n"
    )


def test_wrapped_and_plain_agree() -> None:
    """The opacity pin (§12): the same ftp exchange plain and wrapped, same answers."""
    outcomes: dict[bool, tuple[bytes, bytes]] = {}
    for wrapped in (False, True):
        with maybe_tls(wrapped):
            client, streams, control = connect_client(
                wrapped, b"220 ready\r\n227 here (127,0,0,1,192,0)\r\n150 sending\r\n226 done\r\n"
            )
            data = MemoryStream("data.far")
            streams.queue_connect(data, sends=b"the file", closes=True)
            payload = client.retr("file")
        outcomes[wrapped] = (bytes(control.inbox), payload)
    assert outcomes[False] == outcomes[True]
    assert outcomes[False][1] == b"the file"
