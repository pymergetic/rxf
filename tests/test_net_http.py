"""Pins for the http transport (docs/25 §12): one codec, two faces, TLS opaque.

The codec tests run twice — once over a plain stream and once through a dummy wrapping
Stream — and the answers are the same either way (§12's whole optional-TLS mechanism).
The wire form is pinned byte-exact: the two faces share one parser, so the bytes they
trade are the contract. The client's streams come from the scripted fill, never from a
real socket; the accept loop is the plain readiness poll of `stream.py`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest

from pymergetic.rxftk.net.schema import (
    ConnectionReset,
    InvalidEndpoint,
    MalformedRequest,
    MalformedResponse,
    MessageTooLong,
    NotSupported,
    TlsConfig,
)
from pymergetic.rxftk.net.transport.http import (
    Client,
    Request,
    Response,
    get,
    parse_url,
    post,
    read_request,
    read_response,
    write_request,
    write_response,
)
from pymergetic.rxftk.net.transport.http.server import Server

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


def hello(request: Request) -> Response:
    """One handler: an ordinary Python function (docs/23 §4.3)."""
    return Response(200, body=b"hello")


@both
def test_request_roundtrip(wrapped: bool) -> None:
    """A request round-trips through the codec and the wire form is pinned."""
    request = Request("GET", "/a/b?x=1", headers=(("Host", "example.net"),))
    pair = MemoryPair("http")
    write_request(maybe_wrap(pair.client, wrapped), request)
    assert bytes(pair.server.inbox) == b"GET /a/b?x=1 HTTP/1.1\r\nHost: example.net\r\n\r\n"
    assert read_request(maybe_wrap(pair.server, wrapped)) == request


@both
def test_response_roundtrip(wrapped: bool) -> None:
    """A response round-trips: the codec counts the body on the wire and the parse
    carries the count back — the reason phrase is the table's when left empty."""
    response = Response(200, headers=(("Content-Type", "text/plain"),), body=b"ok")
    pair = MemoryPair("http")
    write_response(maybe_wrap(pair.client, wrapped), response)
    assert bytes(pair.server.inbox) == (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\nok"
    )
    parsed = read_response(maybe_wrap(pair.server, wrapped))
    assert parsed == Response(
        200,
        "OK",
        headers=(("Content-Type", "text/plain"), ("Content-Length", "2")),
        body=b"ok",
    )
    assert parsed is not None and parsed.header("content-type") == "text/plain"


@both
def test_response_reason_phrase_kept_verbatim(wrapped: bool) -> None:
    """A reason phrase is the server's words — spaces and all."""
    pair = MemoryPair("http")
    write_response(maybe_wrap(pair.client, wrapped), Response(404, "Nothing To See Here"))
    parsed = read_response(maybe_wrap(pair.server, wrapped))
    assert parsed is not None
    assert parsed.reason == "Nothing To See Here"


@both
def test_request_line_refused_by_name(wrapped: bool) -> None:
    """A request line that is not one is MalformedRequest — never a wrong parse."""
    pair = MemoryPair("http")
    pair.server.feed(b"nonsense without version\r\n\r\n")
    pair.client.close()
    with pytest.raises(MalformedRequest):
        read_request(maybe_wrap(pair.server, wrapped))


@both
def test_status_line_refused_by_name(wrapped: bool) -> None:
    """A status line that is not one is MalformedResponse."""
    pair = MemoryPair("http")
    pair.server.feed(b"HTTP/1.1 twenty OK\r\n\r\n")
    pair.client.close()
    with pytest.raises(MalformedResponse):
        read_response(maybe_wrap(pair.server, wrapped))


@both
def test_clean_eof_is_none_not_a_refusal(wrapped: bool) -> None:
    """A peer that closes before a message is simply done — None, never a parse."""
    pair = MemoryPair("http")
    pair.client.close()
    assert read_request(maybe_wrap(pair.server, wrapped)) is None
    assert read_response(maybe_wrap(pair.server, wrapped)) is None


@both
def test_body_follows_content_length(wrapped: bool) -> None:
    """The body is exactly what Content-Length counts — no more, no less."""
    pair = MemoryPair("http")
    pair.server.feed(b"POST /up HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello and more")
    parsed = read_request(maybe_wrap(pair.server, wrapped))
    assert parsed is not None
    assert parsed.body == b"hello"
    assert parsed.method == "POST"


@both
def test_truncated_body_is_refused(wrapped: bool) -> None:
    """A body the stream cuts short is a refusal, not a short answer."""
    pair = MemoryPair("http")
    pair.server.feed(b"POST /up HTTP/1.1\r\nContent-Length: 10\r\n\r\nshort")
    pair.client.close()
    with pytest.raises(MalformedRequest):
        read_request(maybe_wrap(pair.server, wrapped))


@both
def test_chunked_is_refused_by_name(wrapped: bool) -> None:
    """Transfer codings are a later card: NotSupported, never a silent wrong parse."""
    pair = MemoryPair("http")
    pair.server.feed(b"POST /up HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n")
    with pytest.raises(NotSupported):
        read_request(maybe_wrap(pair.server, wrapped))


@both
def test_body_over_cap_is_refused(wrapped: bool) -> None:
    """A body past the cap is MessageTooLong before the bytes are touched."""
    pair = MemoryPair("http")
    pair.server.feed(b"POST /up HTTP/1.1\r\nContent-Length: 100\r\n\r\n")
    with pytest.raises(MessageTooLong):
        read_request(maybe_wrap(pair.server, wrapped), max_body=16)


@both
def test_write_refuses_a_lying_content_length(wrapped: bool) -> None:
    """A count that disagrees with the body is refused — the wire never lies."""
    pair = MemoryPair("http")
    request = Request("POST", "/up", headers=(("Content-Length", "2"),), body=b"hello")
    with pytest.raises(MalformedRequest):
        write_request(maybe_wrap(pair.client, wrapped), request)


def test_parse_url() -> None:
    """URL parsing is honest: defaults by scheme, bracketed v6, a target with query."""
    assert parse_url("http://example.net").port == 80
    assert parse_url("https://example.net/x").port == 443
    assert parse_url("http://example.net:8080/x").target == "/x"
    assert parse_url("http://example.net?x=1").target == "/?x=1"
    assert parse_url("http://[2001:db8::1]:8080/x").host == "2001:db8::1"
    with pytest.raises(NotSupported):
        parse_url("ftp://example.net/")
    with pytest.raises(InvalidEndpoint):
        parse_url("2001:db8::1/x")
    with pytest.raises(InvalidEndpoint):
        parse_url("http://2001:db8::1/x")  # unbracketed v6: refused, never guessed


@both
def test_client_get_over_scripted_streams(wrapped: bool) -> None:
    """The client face over the seam: the request on the wire, the response parsed."""
    streams = MemoryStreams()
    far = MemoryStream("server")
    streams.queue_connect(far, sends=b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi")
    with maybe_tls(wrapped) as tls:
        response = get(streams, "http://example.net/x", tls=tls)
    assert streams.calls == [("connect", ("example.net", 80))]
    assert bytes(far.inbox) == b"GET /x HTTP/1.1\r\n\r\n"
    assert response.status == 200
    assert response.body == b"hi"


@both
def test_client_post(wrapped: bool) -> None:
    """The body is counted on the wire and returned whole."""
    streams = MemoryStreams()
    far = MemoryStream("server")
    streams.queue_connect(far, sends=b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n")
    with maybe_tls(wrapped) as tls:
        response = post(streams, "http://example.net/y", b"hello", tls=tls)
    assert bytes(far.inbox) == b"POST /y HTTP/1.1\r\nContent-Length: 5\r\n\r\nhello"
    assert response.status == 201


def test_client_https_without_tls_is_refused() -> None:
    """A URL that names TLS needs a config — plaintext is never implied (§11)."""
    with pytest.raises(NotSupported):
        Client(MemoryStreams()).get("https://example.net/")


def test_client_resets_when_the_response_never_arrives() -> None:
    """A peer that closes before the response is ConnectionReset, named as such."""
    streams = MemoryStreams()
    far = MemoryStream("server")
    streams.queue_connect(far, closes=True)
    with pytest.raises(ConnectionReset):
        Client(streams).get("http://example.net/")


@both
def test_server_routes_handler(wrapped: bool) -> None:
    """The route table → handler run: the response is the handler's, bytes exact."""
    pair = MemoryPair("http")
    pair.server.feed(b"GET / HTTP/1.1\r\n\r\n")
    with maybe_tls(wrapped) as tls:
        Server({"/": hello}, tls=tls).handle(maybe_wrap(pair.server, wrapped))
    assert bytes(pair.client.inbox) == b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"


@both
def test_server_unknown_route_is_404(wrapped: bool) -> None:
    """No route, no handler: 404, honestly."""
    pair = MemoryPair("http")
    pair.server.feed(b"GET /missing HTTP/1.1\r\n\r\n")
    with maybe_tls(wrapped) as tls:
        Server({"/": hello}, tls=tls).handle(maybe_wrap(pair.server, wrapped))
    assert bytes(pair.client.inbox).startswith(b"HTTP/1.1 404 Not Found\r\n")


@both
def test_server_malformed_request_is_400(wrapped: bool) -> None:
    """A bad request is a refusal answered 400 — never a wrong parse half-applied."""
    pair = MemoryPair("http")
    pair.server.feed(b"not a request line at all\r\n\r\n")
    with maybe_tls(wrapped) as tls:
        Server({"/": hello}, tls=tls).handle(maybe_wrap(pair.server, wrapped))
    assert bytes(pair.client.inbox).startswith(b"HTTP/1.1 400 Bad Request\r\n")


@both
def test_server_serve_is_the_plain_accept_loop(wrapped: bool) -> None:
    """serve() is the readiness poll of stream.py: each accepted connection is one
    handle run, closed after — no scheduler, no queue, no event loop."""
    first = MemoryPair("one")
    second = MemoryPair("two")
    first.server.feed(b"GET / HTTP/1.1\r\n\r\n")
    second.server.feed(b"GET / HTTP/1.1\r\n\r\n")
    listener = MemoryListener((first.server, second.server))
    with maybe_tls(wrapped) as tls:
        Server({"/": hello}, tls=tls).serve(listener)
    for pair in (first, second):
        assert bytes(pair.client.inbox) == b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"


def test_wrapped_and_plain_agree() -> None:
    """The opacity pin (§12): the same exchange plain and wrapped, the same answers."""
    outcomes: dict[bool, tuple[bytes, Response]] = {}
    for wrapped in (False, True):
        streams = MemoryStreams()
        far = MemoryStream("server")
        streams.queue_connect(far, sends=b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\ndata")
        with maybe_tls(wrapped) as tls:
            response = Client(streams, tls=tls).post("http://example.net/x", b"body")
        outcomes[wrapped] = (bytes(far.inbox), response)
    assert outcomes[False] == outcomes[True]
    assert outcomes[False][1].body == b"data"
