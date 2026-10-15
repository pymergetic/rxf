"""Pins for the link and hardware plane (docs/25 §7), over the in-memory fills.

What is pinned here: every operation is a cell write over the graph — durable state
only (§14: a connection or a neighbor never lands in this plane) — the device kinds of
§7 (veth pair, bridge with members, bond, VLAN, dummy), removes as interval closures
whose history stays (docs/24: nothing is deleted), and the device seam as the only
place a kernel call would be: with no provider installed it refuses `NotSupported` by
name (the `wrap()` pattern of §11), and the in-memory fill declares what a device can
do and refuses a knob it cannot — never a silent no-op that reports success.
"""

from __future__ import annotations

import pytest

from pymergetic.rxf.net.link.device import (
    Knob,
    MemoryDriver,
    hardware,
    install_driver,
    set_knob,
)
from pymergetic.rxf.net.link.link import (
    DURABLE_KINDS,
    LinkKind,
    LinkPlane,
    MemoryGraph,
    Write,
    schema_kind,
)
from pymergetic.rxf.net.schema import (
    Address,
    AddressInUse,
    ConnState,
    Conntrack,
    ConnTuple,
    Device,
    DeviceKind,
    Endpoint,
    Hardware,
    Interval,
    IPPrefix,
    LinkState,
    Neighbor,
    NotSupported,
    Port,
    Socket,
    SocketForm,
    SocketState,
    StaleHandle,
)


def test_create_veth_writes_two_devices_and_their_ports() -> None:
    """A veth pair is two devices, authored whole (§7): both ends in one delta."""
    graph = MemoryGraph()
    plane = LinkPlane(graph, by="operator")
    near, far = plane.create_veth("ve0", "ve1")
    assert isinstance(graph.get("net/dev/ve0"), Device)
    assert isinstance(graph.get("net/dev/ve1"), Device)
    assert isinstance(graph.get("net/port/ve0"), Port)
    assert graph.get("net/port/ve1").device == far.path  # type: ignore[union-attr]
    assert near.link is LinkState.DOWN  # creation is config; bring-up is its own write


def test_create_bridge_members_join_the_segment() -> None:
    """A bridge and its members (§7): the members' ports carry the segment word."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.create_nic("lan1")
    plane.create_bridge("br0", members=["lan0", "lan1"])
    assert isinstance(graph.get("net/dev/br0"), Device)
    assert graph.get("net/seg/br0") is not None
    assert graph.get("net/port/lan0").segment == "net/seg/br0"  # type: ignore[union-attr]
    assert graph.get("net/port/br0").segment is None  # type: ignore[union-attr]


def test_create_bond_shares_the_master_shape() -> None:
    """A bond is a master like a bridge (§7) — its mode has no cell to land in yet
    (schema seam request) and the plane never pretends to store one."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.create_bond("bond0", members=["lan0"])
    assert graph.get("net/port/lan0").segment == "net/seg/bond0"  # type: ignore[union-attr]


def test_the_kind_projection_is_pinned_not_silent() -> None:
    """The authored §7 kinds project honestly onto the landed `DeviceKind` set —
    pinned here so the schema gap (no veth/bond/dummy members) is never silent."""
    assert schema_kind(LinkKind.BRIDGE) is DeviceKind.BRIDGE
    for kind in (LinkKind.NIC, LinkKind.VETH, LinkKind.BOND, LinkKind.VLAN, LinkKind.DUMMY):
        assert schema_kind(kind) is DeviceKind.NIC  # "a real or emulated link" (§3)


def test_create_vlan_writes_the_tag_cell() -> None:
    """802.1Q tagging is a `Vlan` over a device (§7) — and a vid outside 1..4094 or a
    carrier that is gone is refused by name, never created anyway."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.create_vlan("lan0.10", 10, "lan0")
    vlan = graph.get("net/vlan/lan0.10")
    assert vlan.vid == 10  # type: ignore[union-attr]
    assert vlan.on == "net/dev/lan0"  # type: ignore[union-attr]
    with pytest.raises(NotSupported):
        plane.create_vlan("lan0.4095", 4095, "lan0")
    with pytest.raises(StaleHandle):
        plane.create_vlan("eth9.10", 10, "eth9")  # the carrier is gone


def test_create_dummy_and_nic_land_the_same_two_cells() -> None:
    """A dummy is a device that exists as durable state and nothing more (§7)."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_dummy("dum0")
    plane.create_nic("lan0")
    assert isinstance(graph.get("net/dev/dum0"), Device)
    assert isinstance(graph.get("net/port/lan0"), Port)


def test_create_over_an_existing_name_refuses_addressinuse() -> None:
    """A create claims free paths: an existing name refuses — never a silent rewrite
    of someone's cell (docs/24: nothing rewrites history)."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    with pytest.raises(AddressInUse):
        plane.create_nic("lan0")
    with pytest.raises(AddressInUse):
        plane.create_veth("lan0", "ve1")


def test_set_state_mac_mtu_rewrite_only_the_device_cell() -> None:
    """`ip link set` is one cell write (§7): the device's words, and nothing else."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    before = len(graph.writes)
    plane.set_state("lan0", LinkState.UP)
    plane.set_mac("lan0", b"\x02\x00\x00\x00\x00\x01")
    plane.set_mtu("lan0", 9000)
    device = graph.get("net/dev/lan0")
    assert device.link is LinkState.UP  # type: ignore[union-attr]
    assert device.mac == b"\x02\x00\x00\x00\x00\x01"  # type: ignore[union-attr]
    assert device.mtu == 9000  # type: ignore[union-attr]
    assert [write.path for write in graph.writes[before:]] == ["net/dev/lan0"] * 3


def test_set_state_on_a_gone_device_refuses_stalehandle() -> None:
    """§7: a device that vanished is why `StaleHandle` exists — never a new cell."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    with pytest.raises(StaleHandle):
        plane.set_state("eth9", LinkState.UP)


def test_add_address_allocates_the_lowest_free_index() -> None:
    """`ip addr add` writes one `Address` cell (§5) at the lowest free face index —
    deterministic, and both families are one model (§9)."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    first = plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    second = plane.add_address("lan0", b"\x20\x01" + b"\x00" * 13 + b"\x0a", 64)
    assert first.path == "net/if/lan0/addr/0"
    assert second.path == "net/if/lan0/addr/1"
    assert second.iface == "net/if/lan0"
    plane.remove_address(first.path)
    third = plane.add_address("lan0", b"\xc0\x00\x02\x0b", 24)
    assert third.path == "net/if/lan0/addr/0"  # the freed index is the lowest free one


def test_add_route_is_one_model_over_both_families() -> None:
    """`ip route add` writes one `Route` cell (§9); a gateway of another family than
    its prefix is refused by name — one routing lookup, no mixed pairs."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    v4 = plane.add_route("lan0", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01")
    v6 = plane.add_route("lan0", IPPrefix(b"\x00" * 16, 0), via=b"\x20\x01" + b"\x00" * 13 + b"\x01")
    assert v4.path == "net/route/0"
    assert v6.path == "net/route/1"
    with pytest.raises(NotSupported):
        plane.add_route("lan0", IPPrefix(b"\x00" * 4, 0), via=b"\x20\x01" + b"\x00" * 13 + b"\x01")


def test_a_remove_is_a_closure_and_history_stays() -> None:
    """`ip addr del`/`ip route del` close intervals (§14): the path leaves the current
    view, the cells stay — nothing is deleted, ever (docs/24)."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    address = plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    route = plane.add_route("lan0", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01")
    graph.advance(7)
    plane.remove_address(address.path)
    plane.remove_route(route.path)
    assert graph.get(address.path) is None
    assert graph.get(route.path) is None
    assert len(graph.history(address.path)) == 1
    closed = graph.history(address.path)[0]
    assert isinstance(closed, Address)
    assert closed.valid == Interval(address.valid.valid_from, 7)  # closed, never rewritten


def test_delete_device_closes_its_cells_and_its_face() -> None:
    """`ip link del` is closures (§7): the link, its port, its vlans and segment, and
    the addresses and routes hanging on its face — one commit, history stays."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.create_nic("lan1")
    plane.create_bridge("br0", members=["lan0", "lan1"])
    plane.create_vlan("lan0.10", 10, "lan0")
    plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    plane.add_route("lan0", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01")
    plane.delete_device("lan0")
    for path in ("net/dev/lan0", "net/port/lan0", "net/vlan/lan0.10", "net/if/lan0/addr/0", "net/route/0"):
        assert graph.get(path) is None, path
        assert graph.history(path), path
    assert graph.get("net/port/lan1").segment == "net/seg/br0"  # type: ignore[union-attr]
    assert graph.get("net/dev/br0") is not None


def test_transient_cells_are_refused_by_name() -> None:
    """Nothing transient appears in this plane (§14): a connection, a socket, or a
    neighbor is refused `NotSupported` — not silently dropped, not half-written."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    transient = (
        Neighbor("net/if/lan0/neigh/192.0.2.1", b"\xc0\x00\x02\x01", b"\x02" * 6, Interval(0, None)),
        Conntrack(
            "net/ct/tcp-1",
            ConnTuple("tcp", Endpoint(b"\xc0\x00\x02\x01", 1, None), Endpoint(b"\xc0\x00\x02\x02", 2, None)),
            ConnState.ESTABLISHED,
            100,
        ),
        Socket("net/sock/1", (1, 2), SocketForm.STREAM, SocketState.ESTABLISHED, None, None, None),
    )
    for cell in transient:
        with pytest.raises(NotSupported):
            plane.put(cell)  # type: ignore[arg-type]  # handed to the seam exactly so it refuses
        with pytest.raises(NotSupported):
            graph.commit((Write(cell.path, cell),))  # type: ignore[arg-type]
    assert graph.writes == []


def test_writes_are_durable_state_only() -> None:
    """Address, route, and device writes are durable state (§14): the write log of a
    session of operations carries durable kinds and nothing else."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_veth("ve0", "ve1")
    plane.create_bridge("br0", members=["ve0"])
    plane.create_vlan("ve0.10", 10, "ve0")
    plane.set_state("ve1", LinkState.UP)
    plane.add_address("ve1", b"\xc0\x00\x02\x0a", 24)
    plane.add_route("ve1", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01")
    plane.delete_device("ve0")
    assert graph.writes, "the session wrote something"
    assert all(isinstance(write.cell, DURABLE_KINDS) for write in graph.writes)


def test_the_device_seam_refuses_without_a_provider() -> None:
    """The device fill is the only place a kernel call would be (§7): with no
    provider installed the seam refuses `NotSupported` by name — the `wrap()` pattern
    of §11, never a silent empty page."""
    install_driver(None)
    for operation in (
        lambda: hardware("net/dev/eth0"),
        lambda: set_knob("net/dev/eth0", Knob.MTU, 9000),
    ):
        with pytest.raises(NotSupported):
            operation()


def test_the_memory_driver_declares_capabilities_honestly() -> None:
    """The fill says what a device can do (§7): a single-queue seat refuses rings, a
    line with no address refuses a mac, and a supported knob reads back — never a
    silent no-op that reports success."""
    fill = MemoryDriver(
        {
            "net/dev/eth0": Hardware(
                "net/dev/eth0/hw",
                1500,
                b"\x02\x00\x00\x00\x00\x01",
                LinkState.DOWN,
                1000,
                True,
                frozenset({"checksum", "gso"}),
                None,  # a single-queue seat says so instead of pretending (§7)
                None,  # no wake-on-lan here
            ),
            "net/dev/slip0": Hardware(
                "net/dev/slip0/hw",
                1000,
                None,
                LinkState.UP,
                None,
                None,
                frozenset(),
                None,
                None,
            ),
        }
    )
    install_driver(fill)
    try:
        assert hardware("net/dev/eth0").rings is None
        set_knob("net/dev/eth0", Knob.MTU, 9000)
        assert hardware("net/dev/eth0").mtu == 9000
        set_knob("net/dev/eth0", Knob.LINK, LinkState.UP)
        assert hardware("net/dev/eth0").link is LinkState.UP
        for operation in (
            lambda: set_knob("net/dev/eth0", Knob.RINGS, (256, 256)),  # no rings here
            lambda: set_knob("net/dev/eth0", Knob.WOL, True),  # no wake-on-lan here
            lambda: set_knob("net/dev/slip0", Knob.MAC, b"\x02" * 6),  # point-to-point, no address
            lambda: set_knob("net/dev/eth0", Knob.OFFLOADS, frozenset({"gso", "lro"})),  # lro not declared
        ):
            with pytest.raises(NotSupported):
                operation()
        assert fill.applied == [
            ("net/dev/eth0", Knob.MTU, 9000),
            ("net/dev/eth0", Knob.LINK, LinkState.UP),
        ]  # the refusals applied nothing
        with pytest.raises(StaleHandle):
            hardware("net/dev/eth9")  # the device is gone (§7)
    finally:
        install_driver(None)


def test_a_commit_is_one_successor_or_nothing() -> None:
    """Partial application is refused (§8): a commit carrying one refused write lands
    none of it."""
    graph = MemoryGraph()
    good = Write("net/dev/lan1", Device("net/dev/lan1", DeviceKind.NIC, None, 1500, LinkState.DOWN))
    bad = Write(
        "net/if/lan0/neigh/192.0.2.1",
        Neighbor("net/if/lan0/neigh/192.0.2.1", b"\xc0\x00\x02\x01", b"\x02" * 6, Interval(0, None)),  # type: ignore[arg-type]
    )
    with pytest.raises(NotSupported):
        graph.commit((good, bad))
    assert graph.get("net/dev/lan1") is None  # the good half landed nothing
    assert graph.writes == []


def test_addresses_carry_their_interval_like_every_cell() -> None:
    """An address write stamps when it was made (docs/24): one validity model, the
    memory lane's `Interval`, not a second one."""
    graph = MemoryGraph(clock=3)
    plane = LinkPlane(graph)
    address = plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    assert isinstance(address, Address)
    assert address.valid == Interval(3, None)
