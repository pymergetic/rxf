"""The http message codec (docs/25 §12): request/response parse and write — ONE
parser, both faces. `client.py` and `server.py` are thin faces over what is here.

Messages are read from and written to a byte stream (the contract of `stream.py`,
wrapped or not — the codec cannot tell). The head is line-shaped and rides the framing
helpers; the body is `Content-Length`-shaped. Transfer codings are refused by name in
the first build (`NotSupported`) — a refused card, never a silent wrong parse. A
message that does not parse is `MalformedRequest`/`MalformedResponse`, a refusal; a
message that runs past a cap is `MessageTooLong`.

A read that finds a clean end of stream where a message would start answers `None` —
the peer closed before sending one — and is never confused with a truncated message.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...schema import (
    MalformedMessage,
    MalformedRequest,
    MalformedResponse,
    MessageTooLong,
    NotSupported,
)
from ..framing import read_exact, read_line, write_all

VERSION = "HTTP/1.1"
MAX_BODY = 8 << 20

REASONS = {
    200: "OK",
    201: "Created",
    204: "No Content",
    301: "Moved Permanently",
    302: "Found",
    304: "Not Modified",
    400: "Bad Request",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    413: "Content Too Large",
    500: "Internal Server Error",
    501: "Not Implemented",
    503: "Service Unavailable",
}


@dataclass(frozen=True)
class Request:
    """One request: a method, a target, a version, headers in order, a body."""

    method: str
    target: str
    version: str = VERSION
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def header(self, name: str) -> str | None:
        """One header value, looked up case-insensitively (names are case-insensitive)."""
        return _header(self.headers, name)


@dataclass(frozen=True)
class Response:
    """One response: a status, its reason, headers in order, a body."""

    status: int
    reason: str = ""
    version: str = VERSION
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def header(self, name: str) -> str | None:
        """One header value, looked up case-insensitively."""
        return _header(self.headers, name)


@dataclass(frozen=True)
class _Head:
    """The parse of a head: one raw start line plus headers in order."""

    start: str
    headers: tuple[tuple[str, str], ...] = field(default=())


def _header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    want = name.lower()
    for key, value in headers:
        if key.lower() == want:
            return value
    return None


def _text(line: bytes, refusal: type[MalformedMessage]) -> str:
    try:
        return line.decode("ascii")
    except UnicodeDecodeError as error:
        raise refusal("a message line is not ASCII") from error


def _read_head(stream, refusal: type[MalformedMessage]) -> _Head | None:
    """The start line and headers of one message; None at a clean end of stream."""
    line = read_line(stream)
    if line is None:
        return None
    start = _text(line, refusal)
    headers: list[tuple[str, str]] = []
    while True:
        line = read_line(stream)
        if line is None:
            raise refusal("the head was truncated by the end of the stream")
        if line == b"":
            break
        name, sep, value = _text(line, refusal).partition(":")
        if not sep or not name or name.strip() != name:
            raise refusal(f"a header line is not a name and a value: {line!r}")
        headers.append((name, value.strip()))
    return _Head(start, tuple(headers))


def _read_body(stream, headers: tuple[tuple[str, str], ...], max_body: int) -> bytes:
    coding = _header(headers, "transfer-encoding")
    if coding is not None and coding.lower() != "identity":
        raise NotSupported(f"transfer coding {coding!r} is refused in the first build")
    length = _header(headers, "content-length")
    if length is None:
        return b""
    try:
        size = int(length)
    except ValueError as error:
        raise MalformedMessage(f"content-length {length!r} is not a count") from error
    if size < 0:
        raise MalformedMessage(f"content-length {size} is negative")
    if size > max_body:
        raise MessageTooLong(f"a body of {size} bytes exceeds {max_body}")
    return read_exact(stream, size)


def read_request(stream, max_body: int = MAX_BODY) -> Request | None:
    """One request from the stream; None when the peer closed before sending one.

    A request that does not parse is refused `MalformedRequest` — never a wrong parse
    half-applied.
    """
    try:
        head = _read_head(stream, MalformedRequest)
        if head is None:
            return None
        parts = head.start.split(" ")
        if len(parts) != 3 or not all(parts) or not parts[2].startswith("HTTP/"):
            raise MalformedRequest(f"not a request line: {head.start!r}")
        method, target, version = parts
        body = _read_body(stream, head.headers, max_body)
    except MalformedMessage as error:
        raise MalformedRequest(str(error)) from error
    return Request(method, target, version, head.headers, body)


def read_response(stream, max_body: int = MAX_BODY) -> Response | None:
    """One response from the stream; None when the peer closed before sending one."""
    try:
        head = _read_head(stream, MalformedResponse)
        if head is None:
            return None
        version, _, rest = head.start.partition(" ")
        status, _, reason = rest.partition(" ")
        if not version.startswith("HTTP/") or len(status) != 3 or not status.isdigit():
            raise MalformedResponse(f"not a status line: {head.start!r}")
        body = _read_body(stream, head.headers, max_body)
    except MalformedMessage as error:
        raise MalformedResponse(str(error)) from error
    return Response(int(status), reason, version, head.headers, body)


def _render(headers: tuple[tuple[str, str], ...], body: bytes) -> bytes:
    """Headers as bytes, with Content-Length added when the body carries one and the
    caller did not. An explicit count that disagrees with the body is refused."""
    out = bytearray()
    counted = False
    for name, value in headers:
        if name.lower() == "content-length":
            counted = True
            try:
                if int(value) != len(body):
                    raise MalformedMessage(
                        f"content-length {value} disagrees with {len(body)} bytes"
                    )
            except ValueError as error:
                raise MalformedMessage(f"content-length {value!r} is not a count") from error
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            raise MalformedMessage("a header carries no newline")
        out += f"{name}: {value}\r\n".encode("ascii")
    if body and not counted:
        out += f"Content-Length: {len(body)}\r\n".encode("ascii")
    out += b"\r\n"
    return bytes(out)


def write_request(stream, request: Request) -> int:
    """One request onto the stream; returns the bytes written."""
    line = f"{request.method} {request.target} {request.version}"
    if (
        not request.method
        or " " in request.method
        or " " in request.target
        or not request.version.startswith("HTTP/")
        or " " in request.version
        or "\r" in line
        or "\n" in line
    ):
        raise MalformedRequest(f"not a request line: {line!r}")
    try:
        head = _render(request.headers, request.body)
    except MalformedMessage as error:
        raise MalformedRequest(str(error)) from error
    out = f"{line}\r\n".encode("ascii") + head + request.body
    return write_all(stream, out)


def write_response(stream, response: Response) -> int:
    """One response onto the stream; returns the bytes written."""
    if not (100 <= response.status <= 599):
        raise MalformedResponse(f"not a status: {response.status}")
    reason = response.reason or REASONS.get(response.status, "")
    if "\r" in reason or "\n" in reason or "\r" in response.version or "\n" in response.version:
        raise MalformedResponse("a status line carries no newline")
    try:
        head = _render(response.headers, response.body)
    except MalformedMessage as error:
        raise MalformedResponse(str(error)) from error
    out = f"{response.version} {response.status} {reason}\r\n".encode("ascii") + head + response.body
    return write_all(stream, out)
