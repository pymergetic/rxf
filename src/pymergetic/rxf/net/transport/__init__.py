"""The transport layer (docs/25 §12): one stream contract, protocol modules on top.

`stream.py` is the byte-stream contract every protocol sees — wrapped or not, the
protocol cannot tell. `framing.py` is optional line/length framing over it. Each
protocol is one submodule with one codec and two faces (`http`, `ftp`, …): client and
server share one parser, so there is never a second stack to keep in agreement. TLS is
a wrapper on the same contract (§11) — adding it to any protocol is one constructor
argument.
"""

from .framing import (
    read_exact,
    read_line,
    read_prefixed,
    read_to_eof,
    write_all,
    write_line,
    write_prefixed,
)
from .stream import (
    Listener,
    Stream,
    Streams,
    Wrapper,
    accept_loop,
    install_wrapper,
    wrap,
)

__all__ = [
    "Listener",
    "Stream",
    "Streams",
    "Wrapper",
    "accept_loop",
    "install_wrapper",
    "read_exact",
    "read_line",
    "read_prefixed",
    "read_to_eof",
    "wrap",
    "write_all",
    "write_line",
    "write_prefixed",
]
