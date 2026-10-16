"""The network type set (docs/25 §3): devices, sockets, filters, security, desired state.

Every type here is a value: frozen dataclasses and enumerations, annotated, with no
behavior beyond honest accessors. The cells they describe live under `net/…` paths and
change as deltas (docs/24) — `Address.valid` and `Neighbor.valid` are `Interval`s in the
same validity model memories use, never a second one.

The words the set references (`Time`, `Interval`, `Mac`, `IP`, `IPPrefix`, `Value`,
`RouteOrigin`, `ConnTuple`, `ConnState`) are given here in their simplest honest shape.
`Time` and `Interval` are docs/24's register exactly and move behind that module's
import when the memory lane exports it (the `memory.Interval` seam).

The bottom section carries the typed refusals (§15) and the protocol errors (§12) the
whole layer raises — providers map their errors to these names, and the body never sees
a host error code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, Flag, auto

# ---- words the set references (docs/24's register: one validity model, one word shape) ----

Time = int  # 64-bit value word (docs/24)

Mac = bytes  # 6-byte hardware address (48-bit)
IP = bytes  # 4 bytes (inet) or 16 bytes (inet6); the family is the length —
# v4-mapped v6 is refused by name (§17), so one length is one family, always.

Value = int | bytes | frozenset[str]  # a typed literal word (docs/24's Fact.value shape)


@dataclass(frozen=True)
class Interval:
    """docs/24's validity interval — the one expiry model of the whole image.

    A closed interval with `valid_to` set is history; `None` holds until now.
    Nothing is deleted: neighbors, addresses, handshakes, and filter history all
    close an interval and supersede (docs/24 §6).
    """

    valid_from: Time
    valid_to: Time | None  # None = holds until now


@dataclass(frozen=True)
class IPPrefix:
    """An address prefix; the family is carried by the prefix (§9)."""

    network: IP
    length: int

    @property
    def family(self) -> Family:
        """One routing lookup over both families — derived, so it cannot lie."""
        return Family.IP if len(self.network) == 4 else Family.IP6


# ---- L2: devices, ports, segments ----


Handle = tuple[int, int]  # (object_id, generation) — a stale handle is refused


class Family(Enum):
    INET = "inet"  # both families in one filter model
    IP = "ip"
    IP6 = "ip6"
    NETDEV = "netdev"
    ARP = "arp"


class DeviceKind(Enum):
    NIC = "nic"  # a real or emulated link
    TAP = "tap"  # host frame line
    SLIP = "slip"  # RFC 1055 byte line (docs/23 NETZ)
    BRIDGE = "bridge"
    LOOPBACK = "loopback"
    WIREGUARD = "wireguard"  # L3 tunnel over UDP
    VETH = "veth"  # one end of a veth pair (§7)
    BOND = "bond"  # a link aggregation over members (§7)
    DUMMY = "dummy"  # a link that exists as config and nothing more (§7)
    VLAN = "vlan"  # a tagged link; its `Vlan` cell carries the tag (§7)


class BondMode(Enum):
    """How a bond's members carry traffic (§7): the policy word on the device cell.

    Neutral descriptors, no vendor words: `active-backup` — one member carries
    while the rest stand by; `802.3ad` — the members carry one negotiated
    aggregation group. Every other policy is refused by name where a mode is
    offered (§15) — never accepted and dropped.
    """

    ACTIVE_BACKUP = "active-backup"
    IEEE_802_3AD = "802.3ad"


class LinkState(Enum):
    DOWN = "down"
    UP = "up"


@dataclass(frozen=True)
class Device:
    """L2: one link."""

    path: str  # net/dev/eth0
    kind: DeviceKind
    mac: Mac | None  # none on point-to-point (SLIP) and tunnels
    mtu: int
    link: LinkState
    mode: BondMode | None = None  # the bond policy word (§7); none elsewhere


@dataclass(frozen=True)
class Port:
    """L2: a device attached to a segment."""

    path: str  # net/port/eth0
    device: str
    segment: str | None


@dataclass(frozen=True)
class Segment:
    """L2: one broadcast domain."""

    path: str  # net/seg/lan
    ports: tuple[str, ...]


@dataclass(frozen=True)
class Vlan:
    """L2: 802.1Q tagging, one cell per tag. QinQ and exotic tagging stay refused."""

    path: str  # net/seg/lan/vlan/10
    vid: int
    on: str  # the device or bridge port that carries it


@dataclass(frozen=True)
class Hardware:
    """What the hardware plane configures: honest capabilities, nothing pretended.

    The fill declares what the device can do; setting a knob it cannot is refused
    `NotSupported` by name (§7), never a silent no-op that reports success.
    """

    path: str  # net/dev/eth0/hw
    mtu: int
    mac: Mac | None
    link: LinkState
    speed: int | None  # Mb/s; none when the link carries no speed
    duplex: bool | None
    offloads: frozenset[str]  # checksum, gso, gro — the fill's honest set
    rings: tuple[int, int] | None
    wol: bool | None
    offloads_enabled: frozenset[str] = frozenset()  # the enabled mask: which of `offloads` are on


# ---- L3: addresses, routes, neighbors (one model, two families) ----


@dataclass(frozen=True)
class Address:
    """L3: interface address, one model, two families (§9)."""

    path: str  # net/if/lan0/addr/0
    iface: str
    ip: IP  # v4 or v6
    prefix: int
    valid: Interval  # docs/24's Interval — renumbering closes, never rewrites


class RouteOrigin(Enum):
    STATIC = "static"
    CONNECTED = "connected"
    RA = "ra"
    DHCP = "dhcp"
    WG = "wg"  # WireGuard allowed-ips


@dataclass(frozen=True)
class Route:
    """L3: one route cell; the family is carried by the prefix."""

    path: str  # net/route/0
    prefix: IPPrefix  # family carried by the prefix
    via: IP | None  # gateway, or on-link
    iface: str
    metric: int
    origin: RouteOrigin


@dataclass(frozen=True)
class Neighbor:
    """ARP and ND are one model (§9) — resolution differs per family behind the frame
    contract, the cell does not."""

    path: str  # net/if/lan0/neigh/<ip>
    ip: IP
    mac: Mac
    valid: Interval  # expiry = closed interval + SUPERSEDES


# ---- sockets: forms, states, endpoints, readiness ----


class SocketForm(Enum):
    STREAM = "stream"  # TCP
    DATAGRAM = "datagram"  # UDP
    RAW = "raw"  # capability-gated: cap.net.raw


class SocketState(Enum):
    CLOSED = "closed"
    BOUND = "bound"
    LISTENING = "listening"
    CONNECTING = "connecting"
    ESTABLISHED = "established"
    CLOSING = "closing"


@dataclass(frozen=True)
class Endpoint:
    """Where a socket points: both families carry the same shape."""

    ip: IP
    port: int
    scope: str | None  # v6 scope / iface


@dataclass(frozen=True)
class Socket:
    """One socket cell; the handle carries a generation (§14)."""

    path: str  # net/sock/<n>
    handle: Handle  # generation: gap 5 of docs/17, closed
    form: SocketForm
    state: SocketState
    local: Endpoint | None
    remote: Endpoint | None
    deadline: int | None  # ticks (edge timer S9)


class Interest(Flag):
    """What a run waits for. Level-triggered only — edge-triggered is refused (§4)."""

    READ = auto()
    WRITE = auto()
    ERROR = auto()
    LINK = auto()  # device link-state change (§7) — same wake machinery


@dataclass(frozen=True)
class Readiness:
    """The wake payload — a channel frame (docs/10) targeting one run."""

    handle: Handle
    ready: Interest


# ---- transports (§12): one stream contract, protocol modules on top ----


@dataclass(frozen=True)
class Stream:
    """What a protocol sees: a socket, wrapped or not (the contract lives in
    `net.transport.stream`; this is its state cell)."""

    handle: Handle  # the socket behind it (generation-checked)
    wrapped: bool  # TLS in between or not — the protocol cannot tell
    deadline: int | None


@dataclass(frozen=True)
class Listener:
    """A server's durable config (§14) — listeners are config, not transient state."""

    path: str  # net/transport/http/0
    bind: Endpoint
    tls: TlsConfig | None  # one argument turns TLS on
    backlog: int


# ---- filter model (§10): families, hooks, chains, rules, sets, counters ----
# The shape is the interface; the code is ours — rules are cells, rule changes are
# deltas, and the history of a firewall change is the delta chain (docs/24).


class Hook(Enum):
    INGRESS = "ingress"  # netdev family
    PREROUTING = "prerouting"
    INPUT = "input"
    FORWARD = "forward"
    OUTPUT = "output"
    POSTROUTING = "postrouting"


class ChainType(Enum):
    FILTER = "filter"
    NAT = "nat"
    ROUTE = "route"


class Verdict(Enum):
    ACCEPT = "accept"
    DROP = "drop"
    REJECT = "reject"  # with the ICMP(v6) error to send
    QUEUE = "queue"  # to userspace over cap.net.raw
    JUMP = "jump"
    RETURN = "return"
    CONTINUE = "continue"  # nothing decided: on to the next rule of the chain (§10)


@dataclass(frozen=True)
class Match:
    """One field test; sets are referenced by path."""

    field: str  # meta, ip, ip6, tcp, udp, ct, ...
    op: str  # ==, !=, in, range, prefix
    value: Value | str  # literal or a set path


@dataclass(frozen=True)
class Rule:
    """One rule cell, in order, with its own counter cell."""

    path: str  # net/filter/tables/lan/chains/input/rules/3
    matches: tuple[Match, ...]
    verdict: Verdict
    jump: str | None
    counter: str  # its counter cell path
    log: bool


@dataclass(frozen=True)
class Chain:
    """One filter chain (§10): a base chain attached at a hook, or a regular chain
    a jump reaches — a regular chain attaches at no hook and its `hook` says so."""

    path: str
    family: Family
    hook: Hook | None
    priority: int
    type: ChainType
    policy: Verdict
    rules: tuple[str, ...]  # rule paths, in order


@dataclass(frozen=True)
class Table:
    path: str  # net/filter/tables/lan
    family: Family
    chains: tuple[str, ...]


@dataclass(frozen=True)
class Set:
    """Interval sets are first class (§10).

    `counter` names its counter cell, like `Rule.counter`; a cell that names none
    keeps the `<path>/counter` convention.
    """

    path: str
    family: Family
    kind: str  # ipv4_addr, ipv6_addr, ether_addr, inet_service, ...
    members: tuple[Interval | Value, ...]
    counter: str | None = None  # its counter cell path


@dataclass(frozen=True)
class Counter:
    """An image word, read back through the honest metrics path (§10)."""

    path: str
    packets: int
    bytes: int


class ConnState(Enum):
    NEW = "new"
    ESTABLISHED = "established"
    RELATED = "related"
    INVALID = "invalid"


@dataclass(frozen=True)
class ConnTuple:
    """The quadruple a connection is demuxed by (§6)."""

    proto: str  # tcp, udp, ...
    src: Endpoint
    dst: Endpoint


@dataclass(frozen=True)
class Conntrack:
    """TRANSIENT: never in a successor (§14)."""

    path: str  # net/ct/<tuple>
    tuple: ConnTuple
    state: ConnState  # NEW, ESTABLISHED, RELATED, INVALID
    expires: int  # ticks (edge timer S9)


# ---- security cards (§11): one crypto card, TLS and WireGuard on top ----


class Role(Enum):
    CLIENT = "client"
    SERVER = "server"


@dataclass(frozen=True)
class Secret:
    """Key material is a cell; export is refused by default (§17)."""

    path: str  # net/secret/…


@dataclass(frozen=True)
class Certificate:
    """Chain, leaf, key: cells under net/secret/."""

    path: str


@dataclass(frozen=True)
class TlsConfig:
    """One card, both roles. TLS 1.3 only in the first build; 1.2 and renegotiation
    are refused by name (§17)."""

    path: str  # net/tls/0
    role: Role
    certificates: tuple[str, ...]  # certificate cell paths (server) or
    roots: str  # the trust-store cell path (client)
    alpn: tuple[str, ...]
    server_name: str | None  # SNI (client); selection (server)
    min_version: str  # "1.3"


@dataclass(frozen=True)
class WireGuardPeer:
    """One peer entry; roles are configuration, not modes (§11)."""

    path: str  # net/wg/wg0/peers/<pubkey-hex>
    public_key: str
    preshared: Secret | None
    endpoint: Endpoint | None  # none = server side / roaming target
    allowed_ips: tuple[IPPrefix, ...]  # becomes Route cells with origin=WG
    keepalive: int | None  # seconds
    last_handshake: Interval | None  # closed by the next handshake


@dataclass(frozen=True)
class WireGuardDevice:
    """One device is server AND client at once (§11)."""

    path: str  # net/dev/wg0
    device: str  # its Device cell (kind=WIREGUARD)
    private_key: Secret
    listen_port: int | None  # server side
    peers: tuple[WireGuardPeer, ...]


# ---- desired state (§8): a plan compiled against a base; drift is a view ----


@dataclass(frozen=True)
class Plan:
    """What an authored document stages against the base (docs/24)."""

    path: str  # net/plan/office.yaml is its Doc (docs/22)
    base: str  # the digest it compiles against
    creates: tuple[str, ...]
    sets: tuple[str, ...]
    deletes: tuple[str, ...]  # interval closures, never deletions of history


@dataclass(frozen=True)
class DriftEntry:
    """One field-wise difference of a drift view (§8): a `(path, expected, actual)`
    triple.

    A changed field names itself as `net/dev/lan0.mtu`; a whole missing or extra
    cell names its cell path with the absent side `None`. `expected` is the
    authored word, `actual` the graph's — a config word or a whole cell.
    """

    path: str
    expected: object
    actual: object


@dataclass(frozen=True)
class Drift:
    """A VIEW: desired vs. current, changes nothing (§8).

    The path words and the field-wise truth behind them: `entries` carries the
    `(path, expected, actual)` triples, one per difference.
    """

    plan: str
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    extra: tuple[str, ...]
    entries: tuple[DriftEntry, ...] = ()


# ---- refusals (§15) and protocol errors (§12) ----
# Providers map their errors to this set; the body never sees `errno` or a host error
# code. Protocol errors sit on top: a malformed message is a refusal by name, never a
# wrong parse half-applied.


class Refusal(Exception):
    """The base of every typed refusal the layer raises."""


class WouldBlock(Refusal):
    """Nothing is ready yet — the primitive path is non-blocking (§4)."""


class Timeout(Refusal):
    """A deadline passed — refused only where an operation promises one (§4)."""


class StaleHandle(Refusal):
    """The socket is gone: a generation gap, never a silent new object (§14)."""


class NotConnected(Refusal):
    """The stream has no peer (§5)."""


class ConnectionRefused(Refusal):
    """No listener answered the connect (§15)."""


class ConnectionReset(Refusal):
    """The peer went away mid-connection (§15)."""


class AddressInUse(Refusal):
    """The bind target is already taken (§15)."""


class NoRoute(Refusal):
    """No route cell leads to the destination (§15)."""


class LinkDown(Refusal):
    """The device's link state is down (§7, §15)."""


class MessageTooLong(Refusal):
    """A message exceeds what the path or the codec admits (§15)."""


class NetUnreachable(Refusal):
    """The network is unreachable (§15)."""


class HostUnreachable(Refusal):
    """The host is unreachable (§15)."""


class PermissionDenied(Refusal):
    """The capability is not held (§15)."""


class NotSupported(Refusal):
    """Refused by name — never a silent no-op that reports success (§15)."""


class InvalidEndpoint(Refusal):
    """The endpoint does not parse or does not name a usable peer (§15)."""


class MalformedMessage(Refusal):
    """A protocol message that does not parse — a refusal, never a wrong parse
    half-applied (§12)."""


class MalformedRequest(MalformedMessage):
    """The peer sent a request (or command) the codec refuses by name (§12)."""


class MalformedResponse(MalformedMessage):
    """The peer sent a reply the codec refuses by name (§12)."""


class UnexpectedReply(MalformedMessage):
    """The reply arrived, but not the one the dialogue promised at this step (§12)."""
