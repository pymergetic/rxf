"""The ftp control-channel codec (docs/25 §12): commands and replies — ONE parser,
both faces. `client.py` and `server.py` are thin faces over what is here.

The control channel is line-shaped: a command is a verb and an argument, a reply is a
three-digit code with one or more lines. The data-connection grammar (PASV and PORT
both speak it) lives here too, so the two faces parse and format endpoints the same
way — two faces that must agree is the first step toward two stacks.

A control line that does not parse is `MalformedRequest`/`MalformedResponse`; the
data-connection grammar is v4-only in the first build — the extended forms are a later
card and a v6 host refuses `NotSupported` by name, never a silent wrong address.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...schema import (
    InvalidEndpoint,
    MalformedMessage,
    MalformedRequest,
    MalformedResponse,
    NotSupported,
)
from ..framing import read_line, write_all


@dataclass(frozen=True)
class Command:
    """One control command: a verb and its argument (possibly empty)."""

    verb: str
    argument: str = ""


@dataclass(frozen=True)
class Reply:
    """One control reply: a code and its lines (one per line, terminators dropped)."""

    code: int
    lines: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        """The reply's text — the first line carries the grammar a face parses."""
        return self.lines[0] if self.lines else ""


def _text(line: bytes, refusal: type[MalformedMessage]) -> str:
    try:
        return line.decode("ascii")
    except UnicodeDecodeError as error:
        raise refusal("a control line is not ASCII") from error


def read_command(stream) -> Command | None:
    """One command from the control stream; None when the peer closed cleanly.

    Verbs are upper-case words; the parse normalizes. A line that is not a command is
    refused `MalformedRequest` — never a wrong parse half-applied.
    """
    try:
        line = read_line(stream)
        if line is None:
            return None
        verb, _sep, argument = _text(line, MalformedRequest).partition(" ")
        if not verb:
            raise MalformedRequest(f"not a command: {line!r}")
        return Command(verb.upper(), argument.strip())
    except MalformedMessage as error:
        raise MalformedRequest(str(error)) from error


def write_command(stream, command: Command) -> int:
    """One command onto the control stream."""
    if not command.verb or " " in command.verb or "\r" in command.argument or "\n" in command.argument:
        raise MalformedRequest(f"not a command: {command!r}")
    line = command.verb if not command.argument else f"{command.verb} {command.argument}"
    return write_all(stream, f"{line}\r\n".encode("ascii"))


def read_reply(stream) -> Reply | None:
    """One reply from the control stream; None when the peer closed cleanly.

    A reply is "NNN text" or a multi-line "NNN-text" … "NNN text" block. Anything else
    is refused `MalformedResponse`.
    """
    try:
        first = read_line(stream)
        if first is None:
            return None
        head = _text(first, MalformedResponse)
        code = _code(head, MalformedResponse)
        if len(head) == 3:
            return Reply(code, ())
        if head[3] == " ":
            return Reply(code, (head[4:],))
        if head[3] != "-":
            raise MalformedResponse(f"not a reply line: {first!r}")
        lines = [head[4:]]
        while True:
            line = read_line(stream)
            if line is None:
                raise MalformedMessage("a multi-line reply was truncated")
            text = _text(line, MalformedResponse)
            if text[:3] == head[:3] and (len(text) == 3 or text[3] == " "):
                if len(text) > 3:
                    lines.append(text[4:])
                return Reply(code, tuple(lines))
            lines.append(text)
    except MalformedMessage as error:
        raise MalformedResponse(str(error)) from error


def write_reply(stream, reply: Reply) -> int:
    """One reply onto the control stream: one line, or the multi-line block."""
    if not (100 <= reply.code <= 599):
        raise MalformedResponse(f"not a reply code: {reply.code}")
    if not reply.lines:
        return write_all(stream, f"{reply.code}\r\n".encode("ascii"))
    out = bytearray()
    for line in reply.lines[:-1]:
        out += f"{reply.code}-{line}\r\n".encode("ascii")
    out += f"{reply.code} {reply.lines[-1]}\r\n".encode("ascii")
    return write_all(stream, bytes(out))


def _code(head: str, refusal: type[MalformedMessage]) -> int:
    digits = head[:3]
    if len(head) < 3 or not digits.isdigit() or (len(head) > 3 and head[3] not in " -"):
        raise refusal(f"not a reply line: {head!r}")
    return int(digits)


# ---- the data-connection grammar (PASV and PORT speak one endpoint language) ----


def parse_endpoint(text: str) -> tuple[str, int]:
    """The h1,h2,h3,h4,p1,p2 grammar — from a 227 reply or a PORT argument.

    The digits may sit inside parentheses (the reply's shape). A line that is not six
    decimal bytes is refused `MalformedMessage`.
    """
    fields = text[text.find("(") + 1 : text.rfind(")")] if "(" in text else text
    parts = fields.strip().split(",")
    if len(parts) != 6:
        raise MalformedMessage(f"not a PASV/PORT endpoint: {text!r}")
    try:
        numbers = [int(part.strip()) for part in parts]
    except ValueError as error:
        raise MalformedMessage(f"not a PASV/PORT endpoint: {text!r}") from error
    if any(not (0 <= number <= 255) for number in numbers):
        raise MalformedMessage(f"not a PASV/PORT endpoint: {text!r}")
    host = ".".join(str(number) for number in numbers[:4])
    return host, numbers[4] * 256 + numbers[5]


def format_endpoint(peer: tuple[str, int]) -> str:
    """The grammar reversed: the PORT argument (and the 227 reply's digits).

    The PASV/PORT grammar speaks v4 only; the extended forms (both families) are a
    later card, so a v6 host refuses `NotSupported` by name.
    """
    host, port = peer
    quads = host.split(".")
    try:
        numbers = [int(quad) for quad in quads]
    except ValueError:
        numbers = []
    if len(numbers) != 4 or any(not (0 <= number <= 255) for number in numbers):
        if ":" in host:
            raise NotSupported(
                "the PASV/PORT grammar speaks v4; the extended forms are a later card"
            )
        raise InvalidEndpoint(f"not a v4 host: {host!r}")
    if not (0 <= port <= 65535):
        raise InvalidEndpoint(f"not a port: {port}")
    return ",".join(str(number) for number in numbers + [port >> 8, port & 255])
