"""The http transport (docs/25 §12): one codec, two faces.

`codec` is the one parser both faces share; `client` is get/post/… over a stream; and
`server` is the listener + route table → handler run. TLS is one constructor argument
on either face (§11) and the codec never learns the difference.
"""

from .client import Client, Url, get, parse_url, post
from .codec import (
    MAX_BODY,
    REASONS,
    VERSION,
    Request,
    Response,
    read_request,
    read_response,
    write_request,
    write_response,
)
from .server import Handler, Server

__all__ = [
    "MAX_BODY",
    "REASONS",
    "VERSION",
    "Client",
    "Handler",
    "Request",
    "Response",
    "Server",
    "Url",
    "get",
    "parse_url",
    "post",
    "read_request",
    "read_response",
    "write_request",
    "write_response",
]
