"""The http client face (docs/25 §12): get, post, … over a stream.

A thin face over the one codec — the parser is `codec.py`'s, shared with the server so
the two faces can never disagree. A client takes the stream seam (`Streams`) and an
optional `TlsConfig`; one constructor argument turns TLS on and the client never
learns the difference (§11). Each request opens one stream and closes it after —
connection reuse is a later card, and the honest first shape is one exchange each.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schema import ConnectionReset, InvalidEndpoint, NotSupported, TlsConfig
from ..stream import Stream, Streams, wrap
from .codec import Request, Response, read_response, write_request


@dataclass(frozen=True)
class Url:
    """A parsed http URL: scheme, host, port, request target."""

    scheme: str
    host: str
    port: int
    target: str


def _split_target(rest: str) -> tuple[str, str]:
    """The authority and the request target of a URL's tail."""
    cut = len(rest)
    for mark in "/?":
        found = rest.find(mark)
        if 0 <= found < cut:
            cut = found
    authority, tail = rest[:cut], rest[cut:]
    if not tail:
        target = "/"
    elif tail.startswith("?"):
        target = "/" + tail
    else:
        target = tail
    return authority, target


def _split_host(scheme: str, authority: str, url: str) -> tuple[str, int]:
    """The host and port of a URL authority. An IPv6 literal is bracketed (the URL
    grammar's own shape); anything else with a colon in the host is refused."""
    default = 443 if scheme == "https" else 80
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            raise InvalidEndpoint(f"not a URL: {url!r}")
        host = authority[1:end]
        tail = authority[end + 1 :]
        if tail.startswith(":"):
            port = _port(tail[1:], url)
        elif tail:
            raise InvalidEndpoint(f"not a URL: {url!r}")
        else:
            port = default
    else:
        name, colon, number = authority.rpartition(":")
        if colon and number.isdigit():
            host, port = name, _port(number, url)
        else:
            host, port = authority, default
        if ":" in host:
            raise InvalidEndpoint(f"an IPv6 host in a URL is bracketed: {url!r}")
    if not host:
        raise InvalidEndpoint(f"not a URL: {url!r}")
    return host, port


def _port(number: str, url: str) -> int:
    port = int(number)
    if not (0 <= port <= 65535):
        raise InvalidEndpoint(f"not a URL: {url!r}")
    return port


def parse_url(url: str) -> Url:
    """Split a URL into the seam's pieces. A scheme that is not http/https is refused
    `NotSupported`; a URL that does not parse is refused `InvalidEndpoint`."""
    scheme, sep, rest = url.partition("://")
    if not sep or not scheme:
        raise InvalidEndpoint(f"not a URL: {url!r}")
    if scheme not in ("http", "https"):
        raise NotSupported(f"the scheme {scheme!r} is refused in the first build")
    authority, target = _split_target(rest)
    host, port = _split_host(scheme, authority, url)
    return Url(scheme, host, port, target)


class Client:
    """The http client over the stream seam. `streams` fills connect/listen; `tls`
    (one argument) wraps every stream the client opens."""

    def __init__(self, streams: Streams, tls: TlsConfig | None = None) -> None:
        self._streams = streams
        self._tls = tls

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes = b"",
        headers: tuple[tuple[str, str], ...] = (),
    ) -> Response:
        """One exchange: connect, write the request, read the response, close."""
        parsed = parse_url(url)
        if parsed.scheme == "https" and self._tls is None:
            raise NotSupported(
                "a URL that names TLS needs a tls config; plaintext is never implied"
            )
        stream = self._open((parsed.host, parsed.port))
        try:
            write_request(
                stream, Request(method, parsed.target, headers=headers, body=body)
            )
            response = read_response(stream)
        finally:
            stream.close()
        if response is None:
            raise ConnectionReset("the peer closed before the response arrived")
        return response

    def _open(self, peer: tuple[str, int]) -> Stream:
        stream = self._streams.connect(peer)
        if self._tls is None:
            return stream
        try:
            return wrap(stream, self._tls)
        except BaseException:
            stream.close()
            raise

    def get(self, url: str, **keywords) -> Response:
        """One GET exchange."""
        return self.request("GET", url, **keywords)

    def post(self, url: str, body: bytes = b"", **keywords) -> Response:
        """One POST exchange with a body."""
        return self.request("POST", url, body=body, **keywords)

    def put(self, url: str, body: bytes = b"", **keywords) -> Response:
        """One PUT exchange with a body."""
        return self.request("PUT", url, body=body, **keywords)

    def delete(self, url: str, **keywords) -> Response:
        """One DELETE exchange."""
        return self.request("DELETE", url, **keywords)


def get(streams: Streams, url: str, *, tls: TlsConfig | None = None, **keywords) -> Response:
    """One GET exchange over a fresh client — the script-shaped face."""
    return Client(streams, tls).get(url, **keywords)


def post(
    streams: Streams,
    url: str,
    body: bytes = b"",
    *,
    tls: TlsConfig | None = None,
    **keywords,
) -> Response:
    """One POST exchange over a fresh client."""
    return Client(streams, tls).post(url, body, **keywords)
