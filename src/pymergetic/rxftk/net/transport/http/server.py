"""The http server face (docs/25 §12): the listener, the route table, the handler run.

A thin face over the one codec — the parser is `codec.py`'s, shared with the client so
the two faces can never disagree. A handler is an ordinary Python function: the same
strand the guest stdlib runs (docs/23 §4.3). The accept loop is a plain poll loop over
stream readiness (`stream.accept_loop`) — no scheduler, no queue, no thread pool, no
event loop; the run-cell machine binds that later (docs/23 §4.5). Each accepted
connection is one `handle` run and is closed when it returns.

A request that does not parse gets a refusal response and the connection closes —
never a wrong parse half-applied. A handler's own failures are its run's failures and
propagate: this face owns the dialogue, not the handler's policy (the run-cell binding
owns that seam).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ...schema import (
    MalformedMessage,
    MessageTooLong,
    NotSupported,
    TlsConfig,
)
from ..stream import Listener, Stream, accept_loop, wrap
from .codec import Request, Response, read_request, write_response

Handler = Callable[[Request], Response]


class Server:
    """The listener + route table → handler run. `routes` maps a request target to
    its handler; `tls` (one argument) wraps every accepted stream (§11)."""

    def __init__(
        self,
        routes: Mapping[str, Handler],
        tls: TlsConfig | None = None,
    ) -> None:
        self._routes = dict(routes)
        self._tls = tls

    def route(self, request: Request) -> Handler | None:
        """The route table lookup: the request target, matched exactly as it arrives."""
        return self._routes.get(request.target)

    def handle(self, stream: Stream) -> None:
        """One connection: read a request, run its handler, write the response.

        The stream is left open — the accept loop (or the run cell, later) owns its
        lifetime. A peer that closes before sending a request is simply done.
        """
        try:
            request = read_request(stream)
        except MalformedMessage:
            write_response(stream, Response(400))
            return
        except MessageTooLong:
            write_response(stream, Response(413))
            return
        except NotSupported:
            write_response(stream, Response(501))
            return
        if request is None:
            return
        handler = self.route(request)
        if handler is None:
            write_response(stream, Response(404))
            return
        write_response(stream, handler(request))

    def serve(self, listener: Listener) -> None:
        """The plain accept loop (§12) until the listener closes.

        The seam the run-cell binding takes over (docs/23 §4.5): each `handle` run is
        one accepted connection, and the binding is what later wraps it in a run cell.
        """
        accept_loop(listener, self._run)

    def _run(self, stream: Stream) -> None:
        if self._tls is not None:
            stream = wrap(stream, self._tls)
        self.handle(stream)
