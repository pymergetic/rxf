"""The link and hardware plane (docs/25 §7): what `ip link`, `ip addr`, and `ip route`
do today is state on the hardware edge — here it is cells and deltas over the graph.

Every operation of this plane is a cell write over the graph: create and delete devices
(veth pair, bridge with members, bond, VLAN, dummy), set state (up/down), set MAC/MTU,
add and remove addresses, add and remove routes. **Durable state only** (§14): a
connection or a neighbor is transient and refused here by name, and nothing is deleted —
a remove closes an interval and the history stays (docs/24). No real netlink, no ioctl,
no sockets: the only place a kernel call would be is the device fill seam
(`net.link.device`), and the ethtool-shaped read goes through it.

The udev-shaped device plane lives here too (§7): a device appearing, changing, or
disappearing is an event the machine handles — its properties matched against a
rulebook (tags and commands, as **config**: this lane executes nothing), its
`Device`/`Port` cells as one delta, and the event itself recorded as a cell. Device
events serialize against control-plane applies: both land as graph commits and commits
never interleave — a device event during an apply lands after it, never inside.

The graph seam (`Graph`) is the transaction face the World lane fills (docs/24's delta
store): this lane composes on paper — it decides *what* is written, the World decides
how a write becomes a durable delta. `MemoryGraph` is the one honest in-memory fill of
that seam.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import cast

from ..schema import (
    IP,
    Address,
    AddressInUse,
    BondMode,
    Device,
    DeviceKind,
    Interval,
    IPPrefix,
    LinkState,
    Mac,
    NotSupported,
    Port,
    Route,
    RouteOrigin,
    Segment,
    StaleHandle,
    Time,
    Vlan,
)


class LinkKind(Enum):
    """The §7 kind set for authored links: a plain nic, a veth pair end, a bridge
    (+members), a bond, a VLAN, a dummy — one `DeviceKind` of the frozen set each,
    one to one.
    """

    NIC = "nic"
    VETH = "veth"
    BRIDGE = "bridge"
    BOND = "bond"
    VLAN = "vlan"
    DUMMY = "dummy"


def schema_kind(kind: LinkKind) -> DeviceKind:
    """The authored kind's `DeviceKind` word (§3): identity — the frozen set now
    carries every §7 kind one to one, and this is where it is pinned."""
    return DeviceKind(kind.value)


# ---- the cell vocabulary of this plane -------------------------------------


@dataclass(frozen=True)
class LinkEvent:
    """A link event cell (§7): the machine's record that a device arrived, changed, or
    left.

    A durable event record (the trace shape of docs/24), not transient state. The wake
    a waiting run receives is a `Readiness` frame with `Interest.LINK` (§4) — the same
    wake machinery sockets use, owned by the run lane, never a second event bus here.
    """

    path: str  # net/event/link/<n>
    action: EventAction
    device: str  # net/dev/<name>
    at: Time
    tags: frozenset[str]
    commands: tuple[str, ...]  # config words on the event — this lane executes nothing


class EventAction(Enum):
    """The udev action words (§7) an event carries."""

    ADD = "add"
    REMOVE = "remove"
    CHANGE = "change"


NetCell = Device | Port | Segment | Vlan | Address | Route | LinkEvent

DURABLE_KINDS: tuple[type, ...] = (Device, Port, Segment, Vlan, Address, Route, LinkEvent)


def check_durable(cell: object) -> NetCell:
    """The durable-only rule of §14, by name: only durable link-plane cells land here.

    A connection, a socket, or a neighbor is transient (§14 — derived, re-resolved,
    never in a successor) and refused `NotSupported` — nothing transient appears in
    this plane, and the refusal is never a silent drop.
    """
    if isinstance(cell, DURABLE_KINDS):
        return cast(NetCell, cell)
    raise NotSupported(
        f"a {type(cell).__name__} is not durable link-plane state (§14): "
        "this plane writes device, address, route, and event cells only"
    )


@dataclass(frozen=True)
class Write:
    """One delta write against the named base (§8, docs/24): a cell at a path, or the
    interval closure of one.

    `closed=True` is a closure, never a deletion — history stays. `by` is the author
    of the delta (docs/24 §9): `ip addr add` is an authored delta and its author is
    the caller.
    """

    path: str
    cell: NetCell
    closed: bool = False
    by: str | None = None


class Graph(ABC):
    """The cell table the net lanes write against — the transaction seam.

    The World lane fills this from docs/24's delta store; until then `MemoryGraph` is
    the honest fill. Reads see the current view (open cells); a write is one `commit`
    — one atomic successor (§8: partial application is refused), durable cells only.
    """

    @abstractmethod
    def get(self, path: str) -> NetCell | None:
        """The current cell at `path`, or None when the path is open-free (never
        written, or closed — its history still holds)."""

    @abstractmethod
    def cells(self) -> tuple[tuple[str, NetCell], ...]:
        """The whole current view, in path order."""

    @abstractmethod
    def digest(self) -> str:
        """The digest of the current view — the base a plan compiles against."""

    @abstractmethod
    def commit(self, writes: Sequence[Write]) -> None:
        """One atomic successor: all of `writes` land, or a refused write lands none."""

    @property
    @abstractmethod
    def clock(self) -> Time:
        """The word writes stamp with when they carry no word of their own."""


class MemoryGraph(Graph):
    """The one in-memory fill of the graph seam — compose on paper, honestly.

    Commits are atomic (a refused write lands nothing) and **serialized**: a commit
    raised while another commit is in flight is queued and lands after it — never
    interleaved. That is the hotplug race rule of §7 as store semantics: a device event
    during a control-plane apply lands before or after it, never inside.

    `on_write` is the fill's window onto a commit in flight (the place a seat's kernel
    would interrupt); the serialization pin drives device events through it. History
    is kept: a closure retires the path from the current view and its cells stay in
    `history` — nothing is deleted.
    """

    def __init__(
        self,
        *,
        clock: Time = 0,
        on_write: Callable[[Write], None] | None = None,
    ) -> None:
        self._open: dict[str, NetCell] = {}
        self._history: dict[str, list[NetCell]] = {}
        self._clock = clock
        self._on_write = on_write
        self._busy = False
        self._queued: list[Sequence[Write]] = []
        self.writes: list[Write] = []  # the honest log, in landing order

    @property
    def clock(self) -> Time:
        return self._clock

    def advance(self, to: Time) -> None:
        """Moves the clock forward — time does not walk backwards (docs/24)."""
        if to < self._clock:
            raise NotSupported(f"the clock stands at {self._clock}: it never moves back to {to}")
        self._clock = to

    def get(self, path: str) -> NetCell | None:
        return self._open.get(path)

    def cells(self) -> tuple[tuple[str, NetCell], ...]:
        return tuple(sorted(self._open.items()))

    def history(self, path: str) -> tuple[NetCell, ...]:
        """What the path held before — closures keep history (docs/24: nothing is
        deleted); an unknown path has none."""
        return tuple(self._history.get(path, ()))

    def digest(self) -> str:
        view = "\n".join(f"{path}\x00{cell!r}" for path, cell in sorted(self._open.items()))
        return "sha256:" + hashlib.sha256(view.encode("utf-8")).hexdigest()

    def commit(self, writes: Sequence[Write]) -> None:
        checked = tuple((write.path, check_durable(write.cell), write.closed, write.by) for write in writes)
        if self._busy:
            self._queued.append(writes)  # lands after the commit in flight, never inside
            return
        self._busy = True
        try:
            self._apply(writes, checked)
            while self._queued:
                pending, self._queued = self._queued[0], self._queued[1:]
                checked = tuple(
                    (write.path, check_durable(write.cell), write.closed, write.by) for write in pending
                )
                self._apply(pending, checked)
        finally:
            self._busy = False

    def _apply(self, writes: Sequence[Write], checked: tuple) -> None:
        for write, (path, cell, closed, _by) in zip(writes, checked, strict=True):
            if closed:
                self._history.setdefault(path, []).append(cell)
                self._open.pop(path, None)
            else:
                self._open[path] = cell
            self.writes.append(write)
            if self._on_write is not None:
                self._on_write(write)


# ---- path words (the graph's naming: the schema's example shapes) -----------


def device_path(name: str) -> str:
    return f"net/dev/{name}"


def port_path(name: str) -> str:
    return f"net/port/{name}"


def segment_path(name: str) -> str:
    return f"net/seg/{name}"


def vlan_path(name: str) -> str:
    return f"net/vlan/{name}"


def iface_path(name: str) -> str:
    """The L3 face of a link (§5): addresses and routes hang on `net/if/<name>`."""
    return f"net/if/{name}"


def address_path(name: str, index: int) -> str:
    return f"net/if/{name}/addr/{index}"


def route_path(index: int) -> str:
    return f"net/route/{index}"


def event_path(index: int) -> str:
    return f"net/event/link/{index}"


def _check_name(name: str) -> str:
    if not name or "/" in name or any(char.isspace() for char in name):
        raise NotSupported(f"a link name is one path word, no slash, no space: {name!r} is not one")
    return name


class LinkPlane:
    """The `ip link` / `ip addr` / `ip route` face over the graph (§8's adapter shape).

    Every mutation is one cell write — durable state only (§14) — and its author is
    the caller (`by`). Names already taken refuse `AddressInUse`; a device that is
    gone refuses `StaleHandle` (§7). Nothing here reaches hardware: knob honesty lives
    on the device fill seam (`net.link.device`).
    """

    def __init__(self, graph: Graph, *, by: str | None = None) -> None:
        self._graph = graph
        self._by = by

    # -- the cell faces ------------------------------------------------------

    def put(self, cell: NetCell) -> NetCell:
        """One durable cell, one commit — the write every operation here is shaped as."""
        checked = check_durable(cell)
        self._graph.commit((Write(_path_of(checked), checked, by=self._by),))
        return checked

    def close(self, path: str) -> None:
        """Retire a cell: its interval closes and the history stays (docs/24) — a
        delete is a closure here, never a deletion."""
        cell = self._device_or_gone(path)
        self._graph.commit((Write(path, self._closing(path, cell), closed=True, by=self._by),))

    # -- ip link shaped ------------------------------------------------------

    def create_veth(self, name: str, peer: str) -> tuple[Device, Device]:
        """A veth pair is two devices, authored whole (§7): both ends, one commit."""
        _check_name(name)
        _check_name(peer)
        if name == peer:
            raise NotSupported("a veth pair has two distinct ends: this names one twice")
        self._claim(device_path(name), port_path(name), device_path(peer), port_path(peer))
        near = Device(device_path(name), schema_kind(LinkKind.VETH), None, 1500, LinkState.DOWN)
        far = Device(device_path(peer), schema_kind(LinkKind.VETH), None, 1500, LinkState.DOWN)
        self._graph.commit(
            (
                Write(device_path(name), near, by=self._by),
                Write(port_path(name), Port(port_path(name), near.path, None), by=self._by),
                Write(device_path(peer), far, by=self._by),
                Write(port_path(peer), Port(port_path(peer), far.path, None), by=self._by),
            )
        )
        return near, far

    def create_bridge(self, name: str, members: Sequence[str] = ()) -> Device:
        """A bridge and its members (§7): the bridge device, the segment its members'
        ports join, and the members' port words — one commit."""
        return self._create_master(name, LinkKind.BRIDGE, members)

    def create_bond(self, name: str, members: Sequence[str] = (), *, mode: BondMode | None = None) -> Device:
        """A bond and its members (§7), and its policy word: `mode` lands on the
        device cell (`Device.mode`) — `active-backup`, `802.3ad`, or none until one
        is set. A word outside the landed `BondMode` set refuses `NotSupported` by
        name — never accepted and dropped."""
        if mode is not None and not isinstance(mode, BondMode):
            raise NotSupported("the bond mode is active-backup or 802.3ad: this word is refused by name")
        return self._create_master(name, LinkKind.BOND, members, mode=mode)

    def create_vlan(self, name: str, vid: int, on: str) -> Device:
        """802.1Q tagging as a cell (§7): the link, and its `Vlan` cell over the
        carrying device. QinQ and exotic tagging stay refused (§17)."""
        _check_name(name)
        carrier = self._device_or_gone(device_path(on))
        if not isinstance(vid, int) or isinstance(vid, bool) or not 1 <= vid <= 4094:
            raise NotSupported(f"a vlan id is a word from 1 to 4094: {vid!r} is not one")
        self._claim(device_path(name), port_path(name), vlan_path(name))
        link = Device(device_path(name), schema_kind(LinkKind.VLAN), None, 1500, LinkState.DOWN)
        self._graph.commit(
            (
                Write(device_path(name), link, by=self._by),
                Write(port_path(name), Port(port_path(name), link.path, None), by=self._by),
                Write(vlan_path(name), Vlan(vlan_path(name), vid, carrier.path), by=self._by),
            )
        )
        return link

    def create_dummy(self, name: str) -> Device:
        """A dummy link (§7): a device that exists as durable state and nothing more."""
        return self._create_link(name, LinkKind.DUMMY)

    def create_nic(self, name: str) -> Device:
        """A plain link (§7): a real or emulated device, authored as config."""
        return self._create_link(name, LinkKind.NIC)

    def delete_device(self, name: str) -> None:
        """`ip link del` as closures (§7): the link, its port, its segment and vlans,
        and the addresses and routes that hang on its face — one commit, history
        stays. A veth peer is left standing (a pair is authored whole, retired whole
        by whoever authored it)."""
        device = self._device_or_gone(device_path(name))
        face = iface_path(name)
        closing: list[Write] = [
            Write(device_path(name), self._closing(device_path(name), device), closed=True, by=self._by)
        ]
        for path, cell in self._graph.cells():
            if path in (port_path(name), segment_path(name), vlan_path(name)) or isinstance(cell, Vlan) and cell.on == device.path:
                closing.append(Write(path, self._closing(path, cell), closed=True, by=self._by))
            elif isinstance(cell, Port) and cell.segment == segment_path(name):
                closing.append(Write(path, replace(cell, segment=None), by=self._by))
            elif isinstance(cell, (Address, Route)) and getattr(cell, "iface", None) == face:
                closing.append(Write(path, self._closing(path, cell), closed=True, by=self._by))
        self._graph.commit(tuple(closing))

    def set_state(self, name: str, link: LinkState) -> Device:
        """`ip link set up/down` (§7): link state is durable data on the device; the
        change-as-event half is the hotplug plane's."""
        if not isinstance(link, LinkState):
            raise NotSupported("the link state is up or down: this value is not one")
        return self._set_fields(name, link=link)

    def set_mac(self, name: str, mac: Mac) -> Device:
        """`ip link set address` (§7): the authored address of the link."""
        if not isinstance(mac, bytes) or len(mac) != 6:
            raise NotSupported("a mac is a 6-byte address: this value is not one")
        return self._set_fields(name, mac=mac)

    def set_mtu(self, name: str, mtu: int) -> Device:
        """`ip link set mtu` (§7): the authored frame size of the link."""
        if not isinstance(mtu, int) or isinstance(mtu, bool) or mtu <= 0:
            raise NotSupported("an mtu is a positive word: this value is not one")
        return self._set_fields(name, mtu=mtu)

    # -- ip addr / ip route shaped -------------------------------------------

    def add_address(self, name: str, ip: IP, prefix: int) -> Address:
        """`ip addr add` (§5): one `Address` cell on the link's face — authored state,
        one family per address (§9). The index is the lowest free one (the doc's
        compiler names its own)."""
        face = iface_path(_check_name(name))
        self._check_prefix(ip, prefix)
        cell = Address(
            address_path(name, self._free_index(f"{face}/addr/")),
            face,
            ip,
            prefix,
            Interval(self._graph.clock, None),
        )
        self.put(cell)
        return cell

    def remove_address(self, path: str) -> None:
        """`ip addr del` as a closure (§14): the address closes its interval and stays
        — renumbering closes, never rewrites."""
        self.close(path)

    def add_route(self, name: str, to: IPPrefix, via: IP | None = None, metric: int = 100) -> Route:
        """`ip route add` (§5, §9): one `Route` cell, both families one model. A
        gateway's family must match the prefix's — one routing lookup over both
        families, and a mixed pair is refused by name."""
        face = iface_path(_check_name(name))
        if not isinstance(to, IPPrefix):
            raise NotSupported("a route destination is an ip prefix: this value is not one")
        if via is not None and len(via) != len(to.network):
            raise NotSupported("a gateway answers the family of its prefix: this pair mixes two")
        cell = Route(
            route_path(self._free_index("net/route/")),
            to,
            via,
            face,
            metric,
            RouteOrigin.STATIC,
        )
        self.put(cell)
        return cell

    def remove_route(self, path: str) -> None:
        """`ip route del` as a closure (§14): history stays."""
        self.close(path)

    # -- internals ------------------------------------------------------------

    def _claim(self, *paths: str) -> None:
        """A create claims free paths — an existing name refuses `AddressInUse` (the
        target is already taken), never a silent rewrite of someone's cell."""
        for path in paths:
            if self._graph.get(path) is not None:
                raise AddressInUse(f"the path is already taken: {path}")

    def _create_link(self, name: str, kind: LinkKind) -> Device:
        _check_name(name)
        self._claim(device_path(name), port_path(name))
        link = Device(device_path(name), schema_kind(kind), None, 1500, LinkState.DOWN)
        self._graph.commit(
            (
                Write(device_path(name), link, by=self._by),
                Write(port_path(name), Port(port_path(name), link.path, None), by=self._by),
            )
        )
        return link

    def _create_master(
        self, name: str, kind: LinkKind, members: Sequence[str], *, mode: BondMode | None = None
    ) -> Device:
        _check_name(name)
        self._claim(device_path(name), port_path(name), segment_path(name))
        link = Device(device_path(name), schema_kind(kind), None, 1500, LinkState.DOWN, mode)
        ports: list[Write] = []
        member_ports: list[str] = []
        for member in members:
            _check_name(member)
            port = self._device_or_gone(port_path(member))  # a member that is gone refuses
            if not isinstance(port, Port):
                raise NotSupported(f"{port_path(member)} is not a port: a master members ports")
            ports.append(Write(port.path, replace(port, segment=segment_path(name)), by=self._by))
            member_ports.append(port.path)
        self._graph.commit(
            tuple(
                [
                    Write(device_path(name), link, by=self._by),
                    Write(port_path(name), Port(port_path(name), link.path, None), by=self._by),
                    Write(segment_path(name), Segment(segment_path(name), tuple(member_ports)), by=self._by),
                ]
                + ports
            )
        )
        return link

    def _set_fields(self, name: str, **fields: object) -> Device:
        cell = self._device_or_gone(device_path(name))
        if not isinstance(cell, Device):
            raise NotSupported(f"{device_path(name)} is not a device")
        updated = replace(cell, **fields)
        self.put(updated)
        return updated

    def _device_or_gone(self, path: str) -> NetCell:
        cell = self._graph.get(path)
        if cell is None:
            raise StaleHandle(f"the device is gone: nothing at {path}")
        return cell

    def _closing(self, path: str, cell: NetCell) -> NetCell:
        """The cell as history holds it: an interval-bearing cell closes its interval
        (§7, docs/24); the rest is retired as it is."""
        if isinstance(cell, Address):
            return replace(cell, valid=Interval(cell.valid.valid_from, self._graph.clock))
        return cell

    def _free_index(self, prefix: str) -> int:
        used = {
            int(path[len(prefix) :])
            for path, _ in self._graph.cells()
            if path.startswith(prefix) and path[len(prefix) :].isdigit()
        }
        index = 0
        while index in used:
            index += 1
        return index

    @staticmethod
    def _check_prefix(ip: IP, prefix: int) -> None:
        width = 32 if len(ip) == 4 else 128 if len(ip) == 16 else 0
        if width == 0:
            raise NotSupported("an address is 4 or 16 bytes: this one is neither")
        if not isinstance(prefix, int) or isinstance(prefix, bool) or not 0 <= prefix <= width:
            raise NotSupported(f"a prefix is a word from 0 to {width} for this family: {prefix!r} is not one")


def _path_of(cell: NetCell) -> str:
    return cell.path


# ---- the udev-shaped matcher (§7): device properties to tags and commands ----


@dataclass(frozen=True)
class PropMatch:
    """One udev-shaped property match (§7): `key == value` or `key != value`, compared
    exactly. Glob patterns and the other operators stay refused by name in the first
    build — a matcher that half-matches is worse than one that says no."""

    key: str
    op: str
    value: str

    def __post_init__(self) -> None:
        if self.op not in ("==", "!="):
            raise NotSupported(f"this matcher compares with == or != : the {self.op!r} operator is refused by name")

    def holds(self, props: Mapping[str, str]) -> bool:
        """Whether the property set satisfies this match (an absent property differs
        from every value)."""
        actual = props.get(self.key)
        return actual == self.value if self.op == "==" else actual != self.value


@dataclass(frozen=True)
class UdevRule:
    """One udev-shaped rule (§7): match device properties, assign tags and commands —
    **as config**. This lane executes nothing; a command is a named word on the event
    and whatever runs it does so as its own authored program (docs/24)."""

    matches: tuple[PropMatch, ...]
    tags: frozenset[str] = frozenset()
    commands: tuple[str, ...] = ()

    def applies(self, props: Mapping[str, str]) -> bool:
        """Every match must hold (udev's AND across a rule's conditions)."""
        return all(match.holds(props) for match in self.matches)


@dataclass(frozen=True)
class RuleHit:
    """What a rulebook assigns: the union of every matching rule's tags and commands,
    in rule order."""

    tags: frozenset[str]
    commands: tuple[str, ...]


@dataclass(frozen=True)
class Rulebook:
    """The rules a device's properties run through (§7), in authored order."""

    rules: tuple[UdevRule, ...] = ()

    def evaluate(self, props: Mapping[str, str]) -> RuleHit:
        """Every matching rule contributes its tags and commands — nothing else."""
        tags: set[str] = set()
        commands: list[str] = []
        for rule in self.rules:
            if not rule.applies(props):
                continue
            tags |= rule.tags
            commands.extend(command for command in rule.commands if command not in commands)
        return RuleHit(frozenset(tags), tuple(commands))


class Hotplug:
    """The udev-shaped device plane (§7): appearance, change, and disappearance are
    events the machine handles.

    Each event is one delta: the device's `Device`/`Port` cells (or their closures),
    the rulebook's tags and commands matched from the device's properties, and the
    event itself as a cell. A device event never races a control-plane apply: both
    land as graph commits, and commits serialize — a device event during an apply
    lands after it, never inside.
    """

    def __init__(self, graph: Graph, rules: Rulebook | None = None, *, by: str | None = None) -> None:
        self._graph = graph
        self._rules = rules if rules is not None else Rulebook()
        self._by = by
        self._events = 0

    def appear(
        self,
        name: str,
        *,
        mac: Mac | None = None,
        mtu: int = 1500,
        link: LinkState = LinkState.DOWN,
        props: Mapping[str, str] | None = None,
    ) -> LinkEvent:
        """A device arrives (§7): its `Device`/`Port` cells as one delta and the event
        as a cell."""
        _check_name(name)
        if self._graph.get(device_path(name)) is not None or self._graph.get(port_path(name)) is not None:
            raise AddressInUse(f"the path is already taken: {device_path(name)}")
        device = Device(device_path(name), DeviceKind.NIC, mac, mtu, link)
        event = self._event(EventAction.ADD, name, props)
        self._graph.commit(
            (
                Write(device.path, device, by=self._by),
                Write(port_path(name), Port(port_path(name), device.path, None), by=self._by),
                Write(event.path, event, by=self._by),
            )
        )
        return event

    def change(
        self,
        name: str,
        *,
        link: LinkState | None = None,
        mtu: int | None = None,
        mac: Mac | None = None,
        props: Mapping[str, str] | None = None,
    ) -> LinkEvent:
        """The device changed (§7): its new words on the cell, and the event. A change
        to nothing is still an event — the carrier moved and the record says so."""
        fields = {
            key: value
            for key, value in (("link", link), ("mtu", mtu), ("mac", mac))
            if value is not None
        }
        current = self._plane_device(name)
        updated = replace(current, **fields) if fields else current
        event = self._event(EventAction.CHANGE, name, props)
        self._graph.commit(
            (
                Write(updated.path, updated, by=self._by),
                Write(event.path, event, by=self._by),
            )
        )
        return event

    def disappear(self, name: str, *, props: Mapping[str, str] | None = None) -> LinkEvent:
        """The device is gone (§7): its `Device`/`Port` cells and the addresses and
        routes on its face close their intervals and supersede — nothing is deleted —
        and the event records the departure."""
        device = self._plane_device(name)
        face = iface_path(name)
        closing: list[Write] = []
        for path, cell in self._graph.cells():
            if isinstance(cell, Port) and cell.device == device.path:
                closing.append(Write(path, cell, closed=True, by=self._by))
            elif isinstance(cell, (Address, Route)) and getattr(cell, "iface", None) == face:
                closed = (
                    replace(cell, valid=Interval(cell.valid.valid_from, self._graph.clock))
                    if isinstance(cell, Address)
                    else cell
                )
                closing.append(Write(path, closed, closed=True, by=self._by))
        event = self._event(EventAction.REMOVE, name, props)
        closing.append(Write(device.path, device, closed=True, by=self._by))
        closing.append(Write(event.path, event, by=self._by))
        self._graph.commit(tuple(closing))
        return event

    def _plane_device(self, name: str) -> Device:
        cell = self._graph.get(device_path(name))
        if not isinstance(cell, Device):
            raise StaleHandle(f"the device is gone: nothing at {device_path(name)}")
        return cell

    def _event(self, action: EventAction, name: str, props: Mapping[str, str] | None) -> LinkEvent:
        merged = dict(props or {})
        merged["action"] = action.value  # udev's own words, filled by the plane
        merged["name"] = name
        hit = self._rules.evaluate(merged)
        event = LinkEvent(
            event_path(self._events),
            action,
            device_path(name),
            self._graph.clock,
            hit.tags,
            hit.commands,
        )
        self._events += 1
        return event
