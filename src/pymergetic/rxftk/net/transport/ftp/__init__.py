"""The ftp transport (docs/25 §12): one control-channel codec, two faces.

`codec` is the one parser both faces share (commands, replies, and the PASV/PORT
data-connection grammar); `client` is the dialogue plus the data connection it opens
itself; `server` is the dialogue plus PASV/PORT data connections. TLS is one
constructor argument on either face (§11) and the codec never learns the difference.
"""

from .client import Client
from .codec import (
    Command,
    Reply,
    format_endpoint,
    parse_endpoint,
    read_command,
    read_reply,
    write_command,
    write_reply,
)
from .server import Files, Server

__all__ = [
    "Client",
    "Command",
    "Files",
    "Reply",
    "Server",
    "format_endpoint",
    "parse_endpoint",
    "read_command",
    "read_reply",
    "write_command",
    "write_reply",
]
