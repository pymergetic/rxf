"""Pins for the network type set (docs/25 §3): frozen values, the enumerations'
spellings, and the refusal names the body is allowed to see (§15, §12).

The doc is the spec; these tests pin that the code carries it exactly. Where the doc
borrows a word from docs/24 (`Interval`), the shape pinned here is that word's shape —
one validity model, not a second one.
"""

from __future__ import annotations

import dataclasses

import pytest

from pymergetic.rxftk.net import schema
from pymergetic.rxftk.net.schema import (
    Address,
    BondMode,
    Certificate,
    Chain,
    ChainType,
    ConnState,
    Conntrack,
    ConnTuple,
    Counter,
    Device,
    DeviceKind,
    Drift,
    DriftEntry,
    Endpoint,
    Family,
    Hardware,
    Hook,
    Interest,
    Interval,
    IPPrefix,
    LinkState,
    Listener,
    Match,
    Neighbor,
    Plan,
    Port,
    Readiness,
    Refusal,
    Role,
    Route,
    RouteOrigin,
    Rule,
    Secret,
    Segment,
    Set,
    Socket,
    SocketForm,
    SocketState,
    StaleHandle,
    Stream,
    Table,
    TlsConfig,
    UnexpectedReply,
    Verdict,
    Vlan,
    WireGuardDevice,
    WireGuardPeer,
    WouldBlock,
)

REFUSALS = (
    "WouldBlock",
    "Timeout",
    "StaleHandle",
    "NotConnected",
    "ConnectionRefused",
    "ConnectionReset",
    "AddressInUse",
    "NoRoute",
    "LinkDown",
    "MessageTooLong",
    "NetUnreachable",
    "HostUnreachable",
    "PermissionDenied",
    "NotSupported",
    "InvalidEndpoint",
)


def test_values_are_frozen() -> None:
    """Every cell type is a value: mutation is refused, not silently ignored."""
    device = Device("net/dev/eth0", DeviceKind.NIC, b"\x00" * 6, 1500, LinkState.UP)
    with pytest.raises(dataclasses.FrozenInstanceError):
        device.mtu = 9000  # type: ignore[misc]


def test_enumeration_spellings() -> None:
    """The enumerations carry the doc's spellings exactly."""
    assert [f.value for f in Family] == ["inet", "ip", "ip6", "netdev", "arp"]
    assert [k.value for k in DeviceKind] == [
        "nic",
        "tap",
        "slip",
        "bridge",
        "loopback",
        "wireguard",
        "veth",
        "bond",
        "dummy",
        "vlan",
    ]
    assert [m.value for m in BondMode] == ["active-backup", "802.3ad"]
    assert [s.value for s in LinkState] == ["down", "up"]
    assert [f.value for f in SocketForm] == ["stream", "datagram", "raw"]
    assert [s.value for s in SocketState] == [
        "closed",
        "bound",
        "listening",
        "connecting",
        "established",
        "closing",
    ]
    assert [h.value for h in Hook] == [
        "ingress",
        "prerouting",
        "input",
        "forward",
        "output",
        "postrouting",
    ]
    assert [t.value for t in ChainType] == ["filter", "nat", "route"]
    assert [v.value for v in Verdict] == [
        "accept",
        "drop",
        "reject",
        "queue",
        "jump",
        "return",
        "continue",
    ]
    assert [r.value for r in Role] == ["client", "server"]
    assert [c.value for c in ConnState] == ["new", "established", "related", "invalid"]


def test_handle_is_a_generation_pair() -> None:
    """A handle is (object_id, generation) — a stale handle is refused, never reused."""
    handle: schema.Handle = (17, 5)
    readiness = Readiness(handle, Interest.READ | Interest.LINK)
    assert readiness.handle == (17, 5)
    assert Interest.LINK in readiness.ready


def test_interest_is_level_readiness() -> None:
    """READ/WRITE/ERROR/LINK are the wake bits — one machinery for sockets and links."""
    assert set(Interest) == {Interest.READ, Interest.WRITE, Interest.ERROR, Interest.LINK}
    assert (Interest.READ | Interest.WRITE) & Interest.WRITE


def test_refusal_set_is_the_documented_one() -> None:
    """The body sees the §15 set by name and nothing else — never a host error code."""
    for name in REFUSALS:
        refusal = getattr(schema, name)
        assert issubclass(refusal, Refusal), name
    for name in ("MalformedMessage", "MalformedRequest", "MalformedResponse", "UnexpectedReply"):
        assert issubclass(getattr(schema, name), Refusal), name


def test_protocol_errors_sit_on_malformed_message() -> None:
    """MalformedMessage and friends are one family on top of the refusal set."""
    from pymergetic.rxftk.net.schema import (
        MalformedMessage,
        MalformedRequest,
        MalformedResponse,
    )

    assert issubclass(MalformedRequest, MalformedMessage)
    assert issubclass(MalformedResponse, MalformedMessage)
    assert issubclass(UnexpectedReply, MalformedMessage)
    assert issubclass(MalformedMessage, Refusal)
    assert not issubclass(StaleHandle, MalformedMessage)


def test_interval_is_the_one_validity_model() -> None:
    """docs/24's Interval: a closed interval is history, None holds until now."""
    open_now = Interval(10, None)
    closed = Interval(10, 20)
    assert open_now.valid_to is None
    assert closed.valid_to == 20


def test_ipprefix_carries_its_family() -> None:
    """One routing lookup over both families (§9) — the prefix says which."""
    assert IPPrefix(b"\x7f\x00\x00\x01", 8).family is Family.IP
    assert IPPrefix(b"\x20\x01" + b"\x00" * 14, 32).family is Family.IP6


def test_l2_cells() -> None:
    """The L2 cell shapes: device, port, segment, vlan, hardware."""
    device = Device("net/dev/eth0", DeviceKind.NIC, b"\x02\x00\x00\x00\x00\x01", 1500, LinkState.UP)
    port = Port("net/port/eth0", device.path, "net/seg/lan")
    segment = Segment("net/seg/lan", (port.path,))
    vlan = Vlan("net/seg/lan/vlan/10", 10, device.path)
    hardware = Hardware(
        "net/dev/eth0/hw",
        1500,
        device.mac,
        LinkState.UP,
        1000,
        True,
        frozenset({"checksum", "gro"}),
        (256, 256),
        False,
    )
    assert segment.ports == ("net/port/eth0",)
    assert vlan.vid == 10
    assert hardware.offloads == frozenset({"checksum", "gro"})
    assert hardware.speed == 1000


def test_l3_cells() -> None:
    """The L3 cell shapes: address, route, neighbor — both families, one model."""
    valid = Interval(0, None)
    address = Address("net/if/lan0/addr/0", "net/if/lan0", b"\xc0\x00\x02\x0a", 24, valid)
    route = Route(
        "net/route/0",
        IPPrefix(b"\x00" * 4, 0),
        b"\xc0\x00\x02\x01",
        "net/if/lan0",
        100,
        RouteOrigin.STATIC,
    )
    neighbor = Neighbor("net/if/lan0/neigh/192.0.2.1", b"\xc0\x00\x02\x01", b"\x02" * 6, valid)
    assert address.prefix == 24
    assert route.origin is RouteOrigin.WG or route.origin is RouteOrigin.STATIC
    assert neighbor.valid is valid


def test_socket_cells() -> None:
    """The socket cell: a handle with a generation, endpoints optional, a deadline."""
    endpoint = Endpoint(b"\xc0\x00\x02\x14", 80, None)
    socket = Socket("net/sock/1", (1, 2), SocketForm.STREAM, SocketState.ESTABLISHED, endpoint, endpoint, 1000)
    assert socket.handle == (1, 2)
    assert socket.state is SocketState.ESTABLISHED
    assert socket.deadline == 1000


def test_stream_and_listener_state_cells() -> None:
    """§3's Stream and Listener state cells (the live contract is transport.stream)."""
    stream = Stream((1, 2), True, None)
    listener = Listener("net/transport/http/0", Endpoint(b"\x00" * 16, 8443, None), None, 16)
    assert stream.wrapped is True
    assert listener.backlog == 16


def test_filter_model() -> None:
    """Tables, chains, rules, sets, counters — interval sets are first class (§10)."""
    rule = Rule(
        "net/filter/tables/lan/chains/input/rules/3",
        (Match("tcp", "==", 22), Match("ct", "state", frozenset({"new"}))),
        Verdict.ACCEPT,
        None,
        "net/filter/counters/3",
        True,
    )
    chain = Chain(
        "net/filter/tables/lan/chains/input",
        Family.INET,
        Hook.INPUT,
        0,
        ChainType.FILTER,
        Verdict.DROP,
        (rule.path,),
    )
    table = Table("net/filter/tables/lan", Family.INET, (chain.path,))
    blocked = Set(
        "net/filter/tables/lan/sets/blocked_ports",
        Family.INET,
        "inet_service",
        (Interval(23, 23), Interval(3128, 3130)),
    )
    counter = Counter("net/filter/counters/3", 7, 512)
    conntrack = Conntrack(
        "net/ct/tcp-1",
        ConnTuple("tcp", Endpoint(b"\xc0\x00\x02\x01", 1, None), Endpoint(b"\xc0\x00\x02\x02", 2, None)),
        ConnState.ESTABLISHED,
        100,
    )
    assert chain.policy is Verdict.DROP
    assert table.chains == (chain.path,)
    assert blocked.members[1] == Interval(3128, 3130)
    assert counter.bytes == 512
    assert conntrack.state is ConnState.ESTABLISHED
    assert rule.matches[0].value == 22
    assert isinstance(rule.matches[1].value, frozenset)


def test_match_values_are_typed_words() -> None:
    """A match value is a literal word, a typed payload, a state set, or a set path."""
    assert Match("tcp", "==", 22).value == 22
    assert Match("meta", "==", b"\x00\x01").value == b"\x00\x01"
    state = frozenset({"established", "related"})
    assert Match("ct", "state", state).value == state
    assert Match("tcp", "in", "net/filter/tables/lan/sets/blocked_ports").value


def test_security_cells() -> None:
    """Secrets, certificates, TLS config, WireGuard: one card each, both roles (§11)."""
    secret = Secret("net/secret/wg0-key")
    certificate = Certificate("net/secret/leaf")
    tls = TlsConfig(
        "net/tls/0",
        Role.SERVER,
        (certificate.path,),
        "net/secret/roots",
        ("http/1.1",),
        None,
        "1.3",
    )
    peer = WireGuardPeer(
        "net/wg/wg0/peers/ab",
        "ab" * 32,
        None,
        None,
        (IPPrefix(b"\x00" * 4, 0),),
        25,
        Interval(0, 10),
    )
    device = WireGuardDevice("net/dev/wg0", "net/dev/wg0", secret, 51820, (peer,))
    assert tls.min_version == "1.3"
    assert tls.role is Role.SERVER
    assert device.peers[0].keepalive == 25
    assert device.private_key is secret


def test_desired_state_cells() -> None:
    """Plan is what an authored document stages; drift is a view that changes nothing."""
    plan = Plan("net/plan/office.yaml", "sha256:base", ("net/route/1",), ("net/route/0",), ())
    drift = Drift(plan.path, ("net/route/1",), ("net/route/0",), ("net/route/9",))
    assert drift.plan == plan.path
    assert drift.missing == ("net/route/1",)


def test_the_added_words_carry_defaults() -> None:
    """The words the lanes asked for land additively (§3): every new field carries a
    default, so every construction site of the frozen set keeps working — a regular
    chain says `hook=None`, a set names its counter cell, a device carries its bond
    mode, hardware carries its enabled offload mask beside the declared set, and a
    drift view carries its field-wise triples."""
    scan = Chain("net/filter/tables/lan/chains/scan", Family.INET, None, 0, ChainType.FILTER, Verdict.RETURN, ())
    blocked = Set("net/filter/tables/lan/sets/blocked", Family.INET, "inet_service", (Interval(23, 23),))
    bond = Device("net/dev/bond0", DeviceKind.BOND, None, 1500, LinkState.DOWN)
    hardware = Hardware("net/dev/eth0/hw", 1500, None, LinkState.DOWN, None, None, frozenset({"gso"}), None, None)
    drift = Drift("net/plan/office.yaml", (), (), ())
    assert scan.hook is None and Verdict.CONTINUE.value == "continue"
    assert blocked.counter is None  # the <path>/counter convention, named on authoring
    assert bond.mode is None and BondMode.ACTIVE_BACKUP.value == "active-backup"
    assert hardware.offloads_enabled == frozenset()
    assert drift.entries == ()
    triple = DriftEntry("net/dev/lan0.mtu", 9000, 1500)
    assert (triple.path, triple.expected, triple.actual) == ("net/dev/lan0.mtu", 9000, 1500)
    named = Set("net/filter/tables/lan/sets/shared", Family.INET, "inet_service", (), "net/filter/counters/9")
    assert named.counter == "net/filter/counters/9"
    posed = Device("net/dev/bond1", DeviceKind.BOND, None, 1500, LinkState.DOWN, BondMode.IEEE_802_3AD)
    assert posed.mode is BondMode.IEEE_802_3AD


def test_refusals_are_exceptions() -> None:
    """The refusal set raises — a refusal is a name the body catches, not a code."""
    with pytest.raises(WouldBlock):
        raise WouldBlock("nothing to read yet")
    with pytest.raises(Refusal):
        raise StaleHandle("the socket is gone")
