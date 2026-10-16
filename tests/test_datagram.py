"""Pins for the datagram face (docs/25 §11, §12) and its in-memory fill.

The tunnel rides "over cap.net.socket datagrams" — these gates pin what that face is:
whole datagrams with their source endpoint, `WouldBlock` when nothing waits (an empty
queue is never EOF), endpoint addressing including the wildcard bind and the
assigned port, and named refusals — `AddressInUse` for a bind collision, `StaleHandle`
for a socket that is gone (§14). The fill pair lives in `transport.datagram` itself
("runtime and test share ONE face"), so what runs on a seat is what runs here.

The wire seams — `deliver` (inject with an apparent source) and `take` (withdraw a
waiting datagram) — are the roaming gate's hands (§2.1): they change a valid packet's
apparent source endpoint without touching the device.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.net.schema import (
    AddressInUse,
    Endpoint,
    Interest,
    StaleHandle,
    WouldBlock,
)
from pymergetic.rxftk.net.transport.datagram import (
    Datagram,
    Datagrams,
    DatagramSocket,
    MemoryDatagrams,
    MemoryDatagramSocket,
)

ANY = Endpoint(b"\x00\x00\x00\x00", 0, None)  # bind: any host, assigned port
FIRST = Endpoint(b"\x0a\x00\x00\x01", 51820, None)
SECOND = Endpoint(b"\x0a\x00\x00\x02", 51821, None)


def test_the_fill_pair_is_the_face_runtime_and_test_share() -> None:
    """One face, one fill: the memory pair implements the very `DatagramSocket` /
    `Datagrams` contracts a seat's socket fill implements — there is no second,
    test-only contract hiding in the tests (the one-defining-face rule)."""
    assert issubclass(MemoryDatagramSocket, DatagramSocket)
    assert isinstance(MemoryDatagrams(), Datagrams)


def test_the_fill_roundtrips_whole_datagrams_with_their_source() -> None:
    """§12: send/recv of whole datagrams — `recvfrom` hands back one message with the
    endpoint it came from, never a fragment, and the wire records what crossed."""
    wire = MemoryDatagrams()
    here = wire.bind(Endpoint(b"\x0a\x00\x00\x02", 51821, None))
    there = wire.bind(Endpoint(b"\x0a\x00\x00\x01", 51820, None))
    sent = there.sendto(b"one datagram", SECOND)
    assert sent == len(b"one datagram")  # a whole datagram or nothing
    message = here.recvfrom()
    assert message == Datagram(src=FIRST, dst=SECOND, payload=b"one datagram")
    assert wire.delivered == [message]  # the wire history is the same value
    there.sendto(b"", SECOND)  # an empty datagram is a datagram (never EOF)
    assert here.recvfrom().payload == b""


def test_an_empty_queue_is_never_eof_and_refuses_would_block() -> None:
    """§4: the primitive path is non-blocking — an empty inbox refuses `WouldBlock`,
    and datagrams have no EOF: `recvfrom` never answers an empty message as "closed"."""
    wire = MemoryDatagrams()
    here = wire.bind(Endpoint(b"\x0a\x00\x00\x02", 51821, None))
    there = wire.bind(Endpoint(b"\x0a\x00\x00\x01", 51820, None))
    with pytest.raises(WouldBlock):
        here.recvfrom()
    there.sendto(b"one", SECOND)
    assert here.recvfrom().payload == b"one"
    with pytest.raises(WouldBlock):  # drained: still not EOF
        here.recvfrom()


def test_endpoints_address_the_wire() -> None:
    """Endpoint addressing: a port of 0 assigned at bind reads back on `local` (never
    a fixed port), a wildcard bind takes any host at its port (the device's
    `0.0.0.0:listen_port`), and a send to nobody is a recorded drop — the fill never
    invents a receiver."""
    wire = MemoryDatagrams()
    assigned = wire.bind(ANY)  # a port of 0 ...
    assert assigned.local is not None and assigned.local.port >= 49152  # ... assigned at bind
    device = wire.bind(Endpoint(b"\x00\x00\x00\x00", 51820, None))  # the wildcard bind
    roaming = Endpoint(b"\x0a\x09\x09\x09", 4444, None)
    stranger = wire.bind(roaming)
    stranger.sendto(b"for the device", Endpoint(b"\x0a\x00\x00\x07", 51820, None))
    assert device.recvfrom() == Datagram(
        src=roaming, dst=Endpoint(b"\x0a\x00\x00\x07", 51820, None), payload=b"for the device"
    )  # any host at the bound port lands on the wildcard bind
    stranger.sendto(b"to nobody", Endpoint(b"\x0a\x09\x09\x09", 9, None))
    assert wire.delivered[-1].payload == b"to nobody"  # recorded on the wire ...
    with pytest.raises(WouldBlock):
        device.recvfrom()  # ... and delivered to nobody: never a lie about success


def test_a_bind_collision_is_refused_and_a_closed_socket_frees_its_target() -> None:
    """§15: a bind target already taken refuses `AddressInUse` — and §14's close is
    one-way but the socket is really gone: its target is free again, its faces refuse
    `StaleHandle` ("the socket is gone", never a silent new object)."""
    wire = MemoryDatagrams()
    here = wire.bind(Endpoint(b"\x0a\x00\x00\x02", 51821, None))
    with pytest.raises(AddressInUse):
        wire.bind(Endpoint(b"\x0a\x00\x00\x02", 51821, None))
    assert here.handle == here.handle  # one handle, generation-checked (§5)
    here.close()
    here.close()  # idempotent
    with pytest.raises(StaleHandle):
        here.recvfrom()
    with pytest.raises(StaleHandle):
        _ = here.local
    again = wire.bind(Endpoint(b"\x0a\x00\x00\x02", 51821, None))  # freed by close
    assert again.handle != here.handle  # never a silent new object


def test_the_wire_seams_deliver_with_an_apparent_source_and_take_it_back() -> None:
    """The roaming seam (§2.1) the device gates drive: `deliver` injects a valid
    datagram with an apparent source endpoint, `take` withdraws a waiting one so a
    gate can re-address it instead of re-sending it."""
    wire = MemoryDatagrams()
    device = wire.bind(Endpoint(b"\x00\x00\x00\x00", 51820, None))
    roamed = Endpoint(b"\x0a\x09\x09\x09", 4444, None)
    wire.deliver(roamed, Endpoint(b"\x00\x00\x00\x00", 51820, None), b"roaming")
    assert device.recvfrom() == Datagram(
        src=roamed, dst=Endpoint(b"\x00\x00\x00\x00", 51820, None), payload=b"roaming"
    )
    listener = wire.bind(roamed)
    listener.sendto(b"take me", Endpoint(b"\x00\x00\x00\x00", 51820, None))
    held = wire.take(Endpoint(b"\x00\x00\x00\x00", 51820, None))
    assert held is not None and held.payload == b"take me"
    with pytest.raises(WouldBlock):  # withdrawn: the device never saw it
        device.recvfrom()
    assert wire.take(roamed) is None  # nothing waits there


def test_wait_is_level_triggered_and_an_expired_timeout_is_an_empty_wake() -> None:
    """§4: readiness is level-triggered — idle is an empty wake (`Interest(0)`, never
    an error), a waiting datagram is READ-ready, and a datagram socket can always
    send (WRITE) — a timeout that expires is an empty wake, not a refusal."""
    wire = MemoryDatagrams()
    name = Endpoint(b"\x0a\x00\x00\x02", 51821, None)
    here = wire.bind(name)
    assert here.wait(Interest.READ, timeout=0) == Interest(0)  # the empty wake
    assert here.wait(Interest.READ | Interest.WRITE) == Interest.WRITE
    wire.deliver(Endpoint(b"\x0a\x00\x00\x01", 51820, None), name, b"ready")
    assert here.wait(Interest.READ) == Interest.READ  # still ready until drained
    here.recvfrom()
    assert here.wait(Interest.READ) == Interest(0)
