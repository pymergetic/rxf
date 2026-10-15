# 25 — Network: devices, sockets, and the orchestration layer

> Status: design. The [contract ladder](17-OPERATING-SYSTEM-PATH.md) names the socket / frame /
> driver contracts this layer fills; [23 §4.4–4.5](23-MICROPYTHON-EXPANSION.md) (NETZ) supplies
> the lwIP card, the SLIP line, and the run-cell binding for readiness. This document specifies
> the orchestration layer itself: L2 devices and segments, the link and hardware plane (`ip
> link`, `ethtool`, udev) and the control plane (`ip`/`ifconfig` as adapters, netplan as
> authored desired state), L3 for IPv4 **and** IPv6, sockets with readiness and deadlines, the
> filter model (tables, chains, sets) and its control face, TLS and WireGuard as cards over one
> crypto card, the transport modules (http, ftp, … client and server over one codec, TLS as an
> optional wrapper), and the SLIP line complete.

## 1. Overview

The orchestration layer owns state and the packet path: devices, ports, segments, addresses,
routes, neighbors, sockets, filter tables, sets, tunnels — all graph cells with paths
(`net/dev/eth0`, `net/route/0`, `net/filter/tables/lan`), all mutation as deltas against a named
base. It is not a second scheduler (docs/23 §4.5) and not a second queue: a readiness wait is a
park of one run cell (S4), and the wake is a channel frame (docs/10) that targets that run.

Configuration is delta, so network configuration is authored and tracked like every other change
(docs/24): who set `net/route/0` when is `AUTHORED_BY`, a superseding rule closes its interval,
never a silent rewrite. The firewall and the tunnels live by the same rule — a filter change is a
reviewable delta, and the counters it moves are image words read back like every other metric.

## 2. The contract ladder

| Contract | Operations | Satisfied by |
|---|---|---|
| `cap.net.socket` (socket level; today's `network_*`) | stream / datagram / raw, deadlines, readiness | a host provider **or** the in-image stack |
| `cap.net.frame` (frame level) | `tx_frame`, `rx_frame`, `link_state`, MTU, frame events | composed over the driver foundation (the lwIP card, docs/23) |
| `cap.net.driver` (driver foundation) | link open/close, DMA buffer pin/publish | one seat fill (tap, SLIP line, metal park, …) |

One ladder, **one contract suite** (docs/17 "same image, two bindings"): the host provider and
the in-image stack fill the *same* contract. That is not a second card (the platform rule) —
it is one contract with two providers, and the suite is what proves the body independent of
whichever filled it.

## 3. The type set

```python
# pymergetic/rxf/net/schema.py  — the orchestration layer's types

Handle = tuple[int, int]                 # (object_id, generation) — a stale handle is refused

class Family(Enum):
    INET = "inet"                        # both families in one filter model
    IP = "ip"
    IP6 = "ip6"
    NETDEV = "netdev"
    ARP = "arp"

class DeviceKind(Enum):
    NIC = "nic"                          # a real or emulated link
    TAP = "tap"                          # host frame line
    SLIP = "slip"                        # RFC 1055 byte line (docs/23 NETZ)
    BRIDGE = "bridge"
    LOOPBACK = "loopback"
    WIREGUARD = "wireguard"              # L3 tunnel over UDP

class LinkState(Enum):
    DOWN = "down"
    UP = "up"

@dataclass(frozen=True)
class Device:                            # L2: one link
    path: str                            # net/dev/eth0
    kind: DeviceKind
    mac: Mac | None                      # none on point-to-point (SLIP) and tunnels
    mtu: int
    link: LinkState

@dataclass(frozen=True)
class Port:                              # L2: a device attached to a segment
    path: str                            # net/port/eth0
    device: str
    segment: str | None

@dataclass(frozen=True)
class Segment:                           # L2: one broadcast domain
    path: str                            # net/seg/lan
    ports: tuple[str, ...]

@dataclass(frozen=True)
class Vlan:                              # L2: 802.1Q tagging, one cell per tag
    path: str                            # net/seg/lan/vlan/10
    vid: int
    on: str                              # the device or bridge port that carries it

@dataclass(frozen=True)
class Hardware:                          # what ip link/ethtool configure: honest capabilities
    path: str                            # net/dev/eth0/hw
    mtu: int
    mac: Mac | None
    link: LinkState
    speed: int | None                    # Mb/s; none when the link carries no speed
    duplex: bool | None
    offloads: frozenset[str]             # checksum, gso, gro — the fill's honest set
    rings: tuple[int, int] | None
    wol: bool | None

@dataclass(frozen=True)
class Address:                           # L3: interface address, one model, two families
    path: str                            # net/if/lan0/addr/0
    iface: str
    ip: IP                               # v4 or v6
    prefix: int
    valid: Interval                      # docs/24's Interval — renumbering closes,
                                         # never rewrites

@dataclass(frozen=True)
class Route:
    path: str                            # net/route/0
    prefix: IPPrefix                     # family carried by the prefix
    via: IP | None                       # gateway, or on-link
    iface: str
    metric: int
    origin: RouteOrigin                  # STATIC, CONNECTED, RA, DHCP, WG (allowed-ips)

@dataclass(frozen=True)
class Neighbor:                          # ARP and ND are one model
    path: str                            # net/if/lan0/neigh/<ip>
    ip: IP
    mac: Mac
    valid: Interval                      # expiry = closed interval + SUPERSEDES

class SocketForm(Enum):
    STREAM = "stream"                    # TCP
    DATAGRAM = "datagram"                # UDP
    RAW = "raw"                          # capability-gated: cap.net.raw

class SocketState(Enum):
    CLOSED = "closed"; BOUND = "bound"; LISTENING = "listening"
    CONNECTING = "connecting"; ESTABLISHED = "established"; CLOSING = "closing"

@dataclass(frozen=True)
class Endpoint:
    ip: IP
    port: int
    scope: str | None                    # v6 scope / iface

@dataclass(frozen=True)
class Socket:
    path: str                            # net/sock/<n>
    handle: Handle                       # generation: gap 5 of docs/17, closed
    form: SocketForm
    state: SocketState
    local: Endpoint | None
    remote: Endpoint | None
    deadline: int | None                 # ticks (edge timer S9)

class Interest(Flag):
    READ = auto(); WRITE = auto(); ERROR = auto()
    LINK = auto()                        # device link-state change (§7) — same wake machinery

@dataclass(frozen=True)
class Readiness:                         # the wake payload — a channel frame (docs/10)
    handle: Handle
    ready: Interest

# ---- transports (§12): one stream contract, protocol modules on top ----

@dataclass(frozen=True)
class Stream:                            # what a protocol sees: a socket, wrapped or not
    handle: Handle                       # the socket behind it (generation-checked)
    wrapped: bool                        # TLS in between or not — the protocol cannot tell
    deadline: int | None

@dataclass(frozen=True)
class Listener:                          # a server's durable config (§14)
    path: str                            # net/transport/http/0
    bind: Endpoint
    tls: TlsConfig | None                # one argument turns TLS on
    backlog: int

# ---- filter model (nftables/PF-shaped: the shape is the interface, the code is ours) ----

class Hook(Enum):
    INGRESS = "ingress"                  # netdev family
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
    REJECT = "reject"                    # with the ICMP(v6) error to send
    QUEUE = "queue"                      # to userspace over cap.net.raw
    JUMP = "jump"
    RETURN = "return"

@dataclass(frozen=True)
class Match:                             # one field test; sets reference by path
    field: str                           # meta, ip, ip6, tcp, udp, ct, ...
    op: str                              # ==, !=, in, range, prefix
    value: Value | str                   # literal or a set path

@dataclass(frozen=True)
class Rule:
    path: str                            # net/filter/tables/lan/chains/input/rules/3
    matches: tuple[Match, ...]
    verdict: Verdict
    jump: str | None
    counter: str                         # its counter cell path
    log: bool

@dataclass(frozen=True)
class Chain:
    path: str
    family: Family
    hook: Hook
    priority: int
    type: ChainType
    policy: Verdict
    rules: tuple[str, ...]               # rule paths, in order

@dataclass(frozen=True)
class Table:
    path: str                            # net/filter/tables/lan
    family: Family
    chains: tuple[str, ...]

@dataclass(frozen=True)
class Set:                               # interval sets are first class
    path: str
    family: Family
    kind: str                            # ipv4_addr, ipv6_addr, ether_addr, inet_service, ...
    members: tuple[Interval | Value, ...]

@dataclass(frozen=True)
class Counter:                           # an image word, read back like every metric
    path: str
    packets: int
    bytes: int

@dataclass(frozen=True)
class Conntrack:                         # TRANSIENT: never in a successor
    path: str                            # net/ct/<tuple>
    tuple: ConnTuple
    state: ConnState                     # NEW, ESTABLISHED, RELATED, INVALID
    expires: int

# ---- security cards ----

class Role(Enum):
    CLIENT = "client"
    SERVER = "server"

@dataclass(frozen=True)
class Secret:                            # key material is a cell; export is refused by default
    path: str                            # net/secret/…

class Certificate:                       # chain, leaf, key: cells under net/secret/
    path: str

@dataclass(frozen=True)
class TlsConfig:                         # one card, both roles
    path: str                            # net/tls/0
    role: Role
    certificates: tuple[str, ...]        # certificate cell paths (server) or
    roots: str                           # the trust-store cell path (client)
    alpn: tuple[str, ...]
    server_name: str | None              # SNI (client); selection (server)
    min_version: str                     # "1.3" — 1.2 refused by name, first build

@dataclass(frozen=True)
class WireGuardPeer:
    path: str                            # net/wg/wg0/peers/<pubkey-hex>
    public_key: str
    preshared: Secret | None
    endpoint: Endpoint | None            # none = server side / roaming target
    allowed_ips: tuple[IPPrefix, ...]    # becomes Route cells with origin=WG
    keepalive: int | None                # seconds
    last_handshake: Interval | None      # closed by the next handshake

@dataclass(frozen=True)
class WireGuardDevice:                   # one device is server AND client at once
    path: str                            # net/dev/wg0
    device: str                          # its Device cell (kind=WIREGUARD)
    private_key: Secret
    listen_port: int | None              # server side
    peers: tuple[WireGuardPeer, ...]

# ---- desired state (netplan's job: a Doc compiled into one delta) ----

@dataclass(frozen=True)
class Plan:                              # what a netplan document stages against the base
    path: str                            # net/plan/office.yaml is its Doc (docs/22)
    base: str                            # the digest it compiles against
    creates: tuple[str, ...]
    sets: tuple[str, ...]
    deletes: tuple[str, ...]             # interval closures, never deletions of history

@dataclass(frozen=True)
class Drift:                             # a VIEW: desired vs. current, changes nothing
    plan: str
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    extra: tuple[str, ...]
```

`Interval` is docs/24's type, not a second one — neighbors, addresses, filter history, and
handshake records run in the same validity model as memories.

## 4. Readiness and deadlines

- **Level-triggered, only.** Edge-triggered is refused by name in the first build: it drops
  events and contradicts the no-second-queue stance.
- A wait is a **park** of the run cell (S4) with an optional deadline on the edge timer (S9).
  The wake is a `Readiness` frame targeting that run — the uasyncio shim's wait/notify
  (docs/23 §4.5) is a face over exactly this, not a second mechanism. The same wake carries
  link-state changes (`Interest.LINK`, §7): a cable pulled is one more readiness event, not a
  second event bus.
- `poll(interests, timeout)` parks **once** for N handles and wakes with a readiness set. The
  primitive path is always non-blocking; "blocking" is composed (park-until-ready, then the op).
- A timeout is a wake with an empty ready set, not an error. `Timeout` is refused only where an
  operation promises one (`recv(timeout=)`).

## 5. Sockets

```
cap.net.socket   stream:   connect bind listen accept recv send shutdown
                 datagram: sendto recvfrom
                 shared:   setdeadline close poll/wait
cap.net.frame    tx_frame rx_frame link_state mtu frame-events
cap.net.driver   link open/close, DMA buffer pin/publish
```

Both families, one face — the family is carried by the endpoint, and sockets are bound per
family (`IP.ANY` / `IP6.ANY` for wildcard binds). V4-mapped v6 is refused by name in the first
build; a dual bind is two sockets, which is what the state model can honestly track.

```python
import net

nic = net.device("net/dev/eth0")
nic.set(link=LinkState.UP, mtu=1500)                  # delta, authored (docs/24)
lan = net.iface("net/if/lan0")
lan.add(Address("192.0.2.10/24"))
lan.add(Address("2001:db8::10/64"))
lan.add(Route("0.0.0.0/0", via="192.0.2.1"))
lan.add(Route("::/0", via="2001:db8::1"))

s = net.socket(net.STREAM)                            # Handle carries a generation
s.connect(("2001:db8::20", 80))
ready = net.poll([s.wait(net.WRITE)], timeout=1_000)  # one park, one wake
if ready:
    s.send(b"GET / HTTP/1.0\r\n\r\n")
```

`usocket` compatibility is a thin adapter over the same contract — one suite, two surfaces.

## 6. The packet path

```
RX:  driver fill → (SLIP deframe | tap read | UDP decap) → cap.net.frame
     → hook PREROUTING (filter) → L3 input: validate, defrag, PMTUD (needfrag honored),
       neighbor, route lookup
       ├─ local:  hook INPUT → socket demux (quadruple, listen queues) → Readiness wake
       └─ forward: hook FORWARD → out-port, neighbor resolve → hook POSTROUTING → TX
TX:  socket send → hook OUTPUT → L3 output (source select, route, fragmentation)
     → L2 (neighbor resolve, encapsulate) → hook POSTROUTING → driver fill
```

Each hook is a filter chain (§10) at a named place in this path; a packet that a rule drops is
gone before the next hop sees it. MAC learning in a segment hangs off `Interval` + `SUPERSEDES`
like everything else that expires.

## 7. The link and hardware plane

What `ip link`, `ethtool`, and a udev rule do today is state and events on the hardware edge —
here it is cells and wakes, under the same honesty rules:

- **Lifecycle is interval, not deletion.** A hotplugged NIC creates its `Device`/`Port` cells as
  a delta; an unplugged one closes their intervals and supersedes (docs/24). Nothing is deleted,
  and a port that vanishes mid-connection is why `StaleHandle` exists.
- **Link state is data and an event.** `LinkState`, carrier, speed, and duplex live on the
  device; a change is a `LINK` wake (§4) to every run waiting on that device — the same
  Readiness mechanism sockets use, never a second event bus (no netlink channel beside it).
- **Hardware knobs are honest capabilities.** MTU, MAC, offloads (checksum/GSO/GRO), ring sizes,
  interrupt coalescing, WOL, and autoneg are `Hardware` options; the fill declares what the
  device can do, and setting a knob it cannot is refused `NotSupported` **by name** — never a
  silent no-op that reports success.
- **VLANs are cells.** 802.1Q tagging is a `Vlan` over a device or bridge port; QinQ and exotic
  tagging stay refused (§17).
- **Hardware queues stay honest to the seat.** RSS queues and IRQ steering map to what the fill
  exposes; a single-queue seat says so instead of pretending.

## 8. The control plane: `ip`, `ifconfig`, and netplan are adapters and programs

There is no privileged config tool and no hidden network store — the tools people know are
faces over the same cells (docs/10 adapters):

- **`ip` / `ifconfig` / `bridge` / `ethtool` shape is adapter shape.** A CLI or HTTP inspector
  translates their language into core operations (`get`, `children`, `stage`, `check`, `emit`)
  and their output is views. Mutations take the same delta path as everything else: `ip addr
  add` is an authored delta and its author is the caller.
- **netplan is desired state, authored.** A declarative document (a `Doc`, docs/22) compiles
  against the base into a staged plan: `stage` → `check` (typed refusals, nothing applied) →
  **one successor for the whole bring-up** (link up, addresses, routes, listeners in order —
  partial application is refused, docs/10 atomic refusal). Rollback is the delta chain: supersede
  with the previous plan, never a rewrite.
- **Drift is a view plus an explicit repair.** Re-rendering the desired plan against the current
  base and diffing yields a drift report — a view, not a mutation. A reconciler program that
  re-applies is an **agent author** (docs/24): its writes are dated, attributed deltas like any
  person's, and the audit trail stays true.
- **Ordering lives in the plan, not in hooks.** Bring-up order (link → address → route →
  listener) is the plan's compile order; nothing reorders behind the image's back.

```python
plan = net.plan("net/plan/office.yaml")   # a Doc (docs/22), compiled against the base
plan.check()                              # typed refusals; nothing is applied
plan.apply()                              # ONE successor: link up, addresses, routes, listeners
diff = plan.drift()                       # a view: desired vs. current, no mutation
```

## 9. IPv4 and IPv6: one model, two families

Both families are first build, not a port later:

- **Address and route** cells carry the family in `IP`/`IPPrefix`; one routing lookup, longest
  prefix match over both families, `Route.origin` distinguishes static, connected, RA, DHCP,
  and WireGuard allowed-ips.
- **ARP and ND are one `Neighbor` model** — resolution differs per family behind
  `cap.net.frame` (ARP requests vs ICMPv6 neighbor solicitation), the cell does not.
- **ND's router side** (RA/RS, redirects, DAD) and **DHCP (v4 and v6)** are *programs on the
  contract*, not layer internals: they write `Address`/`Route`/`Neighbor` deltas like any
  program would, and their writes are authored and tracked like any delta.
- **PMTUD** is L3 input state (`needfrag` honored per destination, `Interval`-aged), and
  ICMP/ICMPv6 error generation lives at the hooks that cause it (`REJECT`, `MessageTooLong`).
- **One refusal, named:** V4-mapped v6 sockets (§5).

## 10. The filter model: tables, chains, sets

The shape is nftables/PF — families, base chains at hooks, rules in order, verdicts, sets,
counters — but the *code is ours*: rules are cells, rule changes are deltas, and the history of
a firewall change is the delta chain (`AUTHORED_BY`, `SUPERSEDES`, docs/24). A firewall you can
audit is the point; a nft binary blob is not.

```python
fw = net.filter                       # the control face; CLI and HTTP inspector are peers
                                      # over the same cells (docs/10 adapters)

lan = fw.table("inet", "lan")
inp = lan.chain("input", hook=Hook.INPUT, priority=0, policy=Verdict.DROP)
inp.rule(matches=[Match("ip6", "prefix", "2001:db8::/32")], verdict=Verdict.ACCEPT)
inp.rule(matches=[Match("tcp", "==", 22), Match("ct", "state", "NEW")],
         verdict=Verdict.ACCEPT, log=True)
inp.rule(matches=[Match("ct", "state", {"ESTABLISHED", "RELATED"})], verdict=Verdict.ACCEPT)

port = fw.set("lan", "blocked_ports", kind="inet_service")
port.add(Interval(23, 23), Interval(3128, 3130))     # interval sets are first class
inp.rule(matches=[Match("tcp", "in", port.path)], verdict=Verdict.DROP)
```

- **Stateful**: `Conntrack` is TRANSIENT state (§14); `Match("ct", …)` sees it, and it never
  appears in a successor.
- **NAT** is `ChainType.NAT` at the SNAT/DNAT hooks (`prerouting`/`postrouting`); masquerade is
  a rule, not a mode.
- **Counters are image words** — `Counter.packets/bytes` read back through the same honest
  metrics path the world HUD uses; the filter's numbers are not a private dashboard.
- **QUEUE** hands a packet to a userspace program over `cap.net.raw`; the verdict returns from
  that program (`accept`/`drop`/`repeat`).

## 11. Security cards: one crypto card, TLS and WireGuard on top

Two protocol cards over **one** crypto card — do not invent a second card so one seat can skip
the first:

```
cap.crypto   curve25519, chacha20poly1305, blake2s, hkdf, (aes-gcm for TLS suites)
             one card, one defining lang (RS), one set of test vectors
net.tls      TLS 1.3, both roles (TlsConfig.role), over cap.net.socket streams
net.wg       WireGuard, device = server AND client (peers), over cap.net.socket datagrams
```

**TLS.** One card, both roles: `TlsConfig.role` selects client or server; certificates and keys
are cells (`net/secret/…`), export refused by default. The card wraps a stream socket into a
secure stream — it is a card *above* the socket contract, not part of the layer, so the layer's
contract suite stays stack-agnostic. TLS 1.3 only in the first build; 1.2 and renegotiation are
refused by name.

**WireGuard.** One `WireGuardDevice` is server and client at once — the roles are configuration,
not modes: a peer with `endpoint` set is a client's remote (and may roam, re-resolving the
endpoint on incoming packets), a peer with only `allowed_ips` is a server's client entry, and
`listen_port` is the server side's UDP port. `allowed_ips` becomes `Route` cells with
`origin=WG` (§9), so the tunnel routes are deltas like every other route. `last_handshake` is an
`Interval` closed by the next handshake; keys are `Secret` cells.

**Source of record and provenance (docs/22).** The crypto card and the WireGuard card are each
one defining lang (RS), with pinned upstream sources cited (candidate: boringtun, BSD-3, as the
protocol source of record and differential reference; the GPLv2 reference implementation is
refused by name unless its license gate clears). TLS rides the vendored TLS source already in
the stack's provenance set (mbedtls, Apache-2.0, the same body `io.fetch` cites), imported once
through `rximport` like the lwIP card — the imported graph is the defining lang, the vendored C
is the cited source of record.

## 12. Transports: protocol modules over one stream, TLS optional

The transport layer is Python-first code over `cap.net.socket` streams: one module per protocol,
client and server as faces of **one codec**, and an optional TLS wrapper no protocol knows
about. Adding a protocol is one submodule; adding TLS to any protocol is one constructor
argument. The goal is that a protocol is cheap enough to add that we never again debate whether
to take one (http, ftp today; smtp, mqtt, and friends are the same three files later).

```text
pymergetic/rxf/net/transport/
    stream.py       the byte-stream contract: read, write, wait, close
    framing.py      line and length-prefixed helpers over a stream (optional)
    http/
        codec.py    request/response parse and write — ONE parser, both faces
        client.py   get, post, … over a stream
        server.py   the listener: route table -> handler run
    ftp/
        codec.py    the control channel
        client.py   incl. the data connection it opens itself
        server.py   incl. PASV/PORT data connections
    ...             smtp, mqtt, …: each the same three files
```

- **One stream contract.** `Stream` wraps a socket handle (§5): `read`/`write`/`wait`/`close`
  with deadlines as deadlines. TLS is a wrapper over the *same* contract
  (`net.tls.wrap(stream, TlsConfig)`, §11) — protocol code cannot tell whether it is wrapped.
  That is the whole optional-TLS mechanism: a protocol takes `tls: TlsConfig | None` at
  construction and never learns the difference.
- **Client and server share the codec.** One parser per protocol (one defining lang, the house
  rule); client and server modules are faces over it. Two parsers that must agree is the first
  step toward two stacks.
- **Servers are run-cell machines.** A listener is durable config (§14); each accepted
  connection is a run cell (docs/23 §4.5), handlers park on `wait(READ)` and wake as readiness
  arrives, backpressure is channel frames (docs/10). A handler is an ordinary Python function —
  the same strand the guest stdlib runs (docs/23 §4.3) — so `import net.transport.http` works in
  a guest program and on the host alike, and the client modules are what scripts and
  `io.fetch`-style callers reuse.
- **Streams on demand.** FTP's data connection (PASV/PORT) is why the mechanism creates streams
  at runtime: the transport contract includes `connect()`, and a passive data socket is a
  listener the protocol opens and closes itself. The generic mechanism has to cover that, or
  ftp is where we bolt things on.
- **Protocol errors are typed.** Malformed messages are refused by name (`MalformedMessage`) on
  top of the §15 set — a bad request is a refusal, never a wrong parse half-applied.

```python
import net.transport.http as http
from net.tls import TlsConfig

app = http.server(routes={...})        # handlers are run-cells (docs/23 §4.5)
app.listen(("::", 8443), tls=TlsConfig(role=Role.SERVER, ...))   # TLS: one argument

body = http.client.get("https://example.net/", tls=TlsConfig(role=Role.CLIENT, ...))
```

**microdot and friends.** A micro web framework is an app layer, not a transport: its seam is
`http/server.py`'s route face. When we want one, we assimilate it as an adapter over that seam —
its own provenance and citations (docs/22), its own file, no fork into our codec and no second
HTTP server beside ours. Until then the route table is the app surface, and the framework
question is an open question (§19).

## 13. The SLIP line, complete

The byte line of docs/23 §4.4, fully specified:

- **Framing**: RFC 1055 END/ESC stuffing only; VJ header compression stays **refused** (a
  refused card, not a silent default).
- **The line contract** (`cap.net.driver` for a SLIP device): `open/close`, byte write (TX),
  byte-source readiness (RX, into the run's wait/notify), MTU (1000 default, settable), and the
  error counters (`framing`, `overrun`) as image words.
- **One fill per seat** (`slip_line_host.c`, `slip_line_tap.c`, `slip_line_js.c`,
  `slip_line_uart.c`), the same contract suite on every seat — the line is a fill like
  `io.fetch` has fills, never a second stack inside a fill.
- The guest side deframes into `netif->input` (the i3 callback the boot wires, docs/23 §4.4);
  `sys_now` stays the edge timer, a mailbox a channel frame, a semaphore a park/wake pair.

## 14. State classification and buffers

- **Durable** (survives successors): device config, addresses, routes, listeners (the service is
  config), filter tables/chains/rules/sets, TLS configs, WireGuard devices and peers, secrets.
- **Transient** (omitted from successors): connections, accept queues, `Conntrack`, neighbor
  cache (derived, re-resolved), reassembly buffers, handshake state. Handles carry generations,
  so a stale handle is refused `StaleHandle` — "the socket is gone", never a silent new object.
- **Buffers**: frames and payloads are heap cells; DMA buffers are pinned and immobile;
  compaction never moves them; pbufs are arena cells (docs/23 risk 4).

## 15. Refusals providers map to

`WouldBlock · Timeout · StaleHandle · NotConnected · ConnectionRefused · ConnectionReset ·
AddressInUse · NoRoute · LinkDown · MessageTooLong · NetUnreachable · HostUnreachable ·
PermissionDenied · NotSupported · InvalidEndpoint`

Providers map their errors to this set; the body never sees `errno` or a host TLS error code.

## 16. Seats

Fills may differ per seat (the POSIX host binds `cap.net.socket` directly; metal/firmware seats
compose over `cap.net.frame` with the lwIP card and the SLIP line; the browser seat rides its
bridge), but the module and the Python/C face do not, and **one contract suite runs on every
seat**. A missing fill is a stub that fails the prove, not a dark port. No seat gets a second
card so it can skip the first.

## 17. Honest refusals (first build)

- edge-triggered readiness (level only) · VJ header compression · V4-mapped v6 sockets ·
  TLS 1.2 and renegotiation · Zero-copy `splice`/`sendfile` · RAW sockets without
  `cap.net.raw` · private-key export from `Secret` cells by default ·
  `QUEUE` without a bound consumer program (the packet is dropped, not queued forever)

## 18. NetBSD rump: a provider, never the in-image stack

Rump (unmodified NetBSD drivers as libraries over `rumpuser`) is genuinely attractive — a full
BSD stack, and PF is the firewall model we just shaped ourselves around. It still does not
enter, for three reasons that are the house rules, not taste:

1. **One in-image stack.** The contract ladder (§2) already grants rump its honest place: a
   *provider fill* of `cap.net.socket` on a hosted seat — the same ladder rung a POSIX host
   fills. The in-image stack is one card (lwIP's imported graph), and a second full kernel
   subsystem beside it would be exactly the "second card so one seat can skip the first" the
   platform rule refuses.
2. **Provenance surface.** rump drags in kern, uvm, sys subsystems behind the net stack — a
   foreign body orders of magnitude past lwIP, each byte needing its docs/22 citation. The
   import gate (i2/i3) is built for lwIP-sized bodies; rump would need its own porting program.
3. **Our firewall is cells.** PF's *shape* lives in §10; PF's *code* cannot — filter changes must
   be deltas with authors and intervals (docs/24). A black-box PF inside rump is opaque exactly
   where this system is required to be legible.

So: no rump discussion needed for the build. It is listed here as the named alternative provider
fill; if a hosted seat ever wants it, it fills `cap.net.socket`, passes the same suite, and the
body never learns which provider answered.

**The interface is the truth.** Whatever a seat sees — `netif` in, socket contract out — is the
same face on every seat, and what runs beneath it is a fill and nothing more: lwIP's imported
graph today, rump's BSD stack on a hosted seat tomorrow, a bare driver on firmware. Rump is
exactly that — a driver provider. It may sit under the interface and nowhere else; the moment it
grew a face of its own it would be the second stack this system refuses.

## 19. Open questions

- WireGuard source of record: boringtun (BSD-3, RS) as pinned import vs. protocol core composed
  over `cap.crypto` with boringtun as differential reference only. The license gate (docs/22)
  decides first; composition is the fallback, and either way one defining lang.
- TLS source of record: mbedtls as imported graph (like lwIP) vs. RS TLS on `cap.crypto`. One
  card either way; the choice is provenance and size, not semantics.
- `Secret` cells at rest: serialized encrypted under a device key, or capability-held and
  non-serializable? Affects successor/backup semantics and must be answered before the first
  firmware build.
- DHCP/RA as programs: which of them ship in the starter (as authored programs) and which stay
  examples.
- `QUEUE` interface width: one consumers' program per chain or per table.
- Filter rule vs. socket-reuse conflict (TPROXY-like redirection): needs its own delta-level
  design note before it is more than a named refusal.
- The micro web framework seam: when the route table in `http/server.py` stops being enough,
  microdot (or similar) is assimilated as one adapter module over that seam with its own
  provenance (docs/22) — the decision to make now is only the route-face shape it would need,
  so the seam is cheap to adapt to when the day comes.
