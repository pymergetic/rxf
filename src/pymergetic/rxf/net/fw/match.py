"""The packet's fields and what matches them (docs/25 §6, §10).

A `Packet` is one packet at a hook: the 5-tuple (protocol, source and destination
address and port), the input and output interface, the conntrack state the `ct`
match reads, and the byte length a counter delta counts. A `Match` cell tests one
field; the vocabulary below is the whole language of this slice:

    meta.protocol          the L4 protocol word ("tcp", "udp", …) — `==`, `!=`
    meta.iif / meta.oif    the input / output interface name — `==`, `!=`
    ip.saddr  ip.daddr     one address family, one field (§9) — `==`, `!=`, `prefix`, `in`
    ip6.saddr ip6.daddr    the v6 spellings of the same fields
    tcp.sport tcp.dport    ports — `==`, `!=`, `in`
    udp.sport udp.dport
    ct                     conntrack; op `state` over the state words (§14)

The bare spellings of the doc's rules carry their classic meaning: `ip`/`ip6` is
the **source** address (the accept-from rule), `tcp`/`udp` is the **destination**
port (the accept-service rule). A field the packet does not carry never matches —
a port test is about packets with ports.

Ranges are named sets, never literals: an op `range` is refused by name, because a
port range or an address prefix is an interval set (§10) — a set cell whose members
are closed intervals over one value space. The member word is `Interval` exactly as
the doc's `Interval(23, 23)` spells it: `valid_from..valid_to` closed, endpoints
included, `valid_to=None` reaching without an upper bound. Membership compiles into
a sorted, merged interval list (`IntervalList`) so one lookup is a binary search.
An empty set never matches.

Nothing here mutates: every function is a pure read of the cells it is handed.
"""

from __future__ import annotations

import bisect
import ipaddress
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..schema import (
    IP,
    ConnState,
    ConnTuple,
    Endpoint,
    Hook,
    Interval,
    IPPrefix,
    Match,
    NotSupported,
    Set,
)

_STATE_WORDS = frozenset(state.value for state in ConnState)
_ADDR_WIDTH = {"addr4": 4, "addr6": 16}

# field -> (probe kind, probe side); the bare spellings carry the doc's rule shapes.
_FIELDS: dict[str, tuple[str, str]] = {
    "ip": ("addr4", "src"),
    "ip.daddr": ("addr4", "dst"),
    "ip.saddr": ("addr4", "src"),
    "ip6": ("addr6", "src"),
    "ip6.daddr": ("addr6", "dst"),
    "ip6.saddr": ("addr6", "src"),
    "tcp": ("port", "dport"),
    "tcp.dport": ("port", "dport"),
    "tcp.sport": ("port", "sport"),
    "udp": ("port", "dport"),
    "udp.dport": ("port", "dport"),
    "udp.sport": ("port", "sport"),
    "meta.iif": ("name", "iif"),
    "meta.oif": ("name", "oif"),
    "meta.protocol": ("proto", "proto"),
    "ct": ("ct", "ct"),
}


@dataclass(frozen=True)
class Packet:
    """One packet at a hook (§6) — only the fields the filter model matches on."""

    hook: Hook
    proto: str = ""  # the L4 word: "tcp", "udp", … (ConnTuple.proto)
    src: IP = b""
    dst: IP = b""
    sport: int | None = None
    dport: int | None = None
    iif: str | None = None
    oif: str | None = None
    ct: ConnState | None = None  # what the ct match reads (the transient plane, §14)
    length: int = 0  # the bytes one counter delta counts


def conn_tuple(packet: Packet) -> ConnTuple:
    """The quadruple this packet demuxes by (§6) — ports a packet lacks are 0."""
    return ConnTuple(
        packet.proto,
        Endpoint(packet.src, packet.sport or 0, None),
        Endpoint(packet.dst, packet.dport or 0, None),
    )


def prefix_interval(value: str | IPPrefix) -> Interval:
    """Compile a prefix into its closed address-space interval (§10).

    "192.0.2.0/24" becomes the endpoints 192.0.2.0 and 192.0.2.255 — one interval
    word, the same shape a port range carries. Host bits in the string are masked
    off (the prefix, not the spelling, is the fact).
    """
    low, high, _ = _parse_prefix(value)
    return Interval(low, high)


def _parse_prefix(value: str | IPPrefix) -> tuple[int, int, int]:
    if isinstance(value, IPPrefix):
        network = ipaddress.ip_network(
            (int.from_bytes(value.network, "big"), value.length), strict=False
        )
    else:
        network = ipaddress.ip_network(value, strict=False)
    low = int(network.network_address)
    high = low + (1 << (network.max_prefixlen - network.prefixlen)) - 1
    return low, high, network.max_prefixlen // 8


class IntervalList:
    """A sorted, merged list of closed ranges — one membership test is a binary
    search (the O(log n) shape §10 asks for), endpoints included."""

    def __init__(self, ranges: Iterable[tuple[int, int | None]]) -> None:
        merged: list[tuple[int, int | None]] = []
        for low, high in sorted(ranges, key=_sort_key):
            if merged and _touches(merged[-1], low, high):
                prev_low, prev_high = merged[-1]
                if high is None or (prev_high is not None and high > prev_high):
                    merged[-1] = (prev_low, high)
            else:
                merged.append((low, high))
        self._lows = [low for low, _ in merged]
        self._highs = [high for _, high in merged]

    def contains(self, probe: int) -> bool:
        """Whether the probe falls in any range — inclusive at both ends."""
        at = bisect.bisect_right(self._lows, probe) - 1
        if at < 0:
            return False
        high = self._highs[at]
        return high is None or probe <= high

    def __len__(self) -> int:
        """How many disjoint ranges the members merged into."""
        return len(self._lows)


def _sort_key(item: tuple[int, int | None]) -> tuple[int, float]:
    low, high = item
    return (low, math.inf if high is None else high)


def _touches(previous: tuple[int, int | None], low: int, high: int | None) -> bool:
    prev_high = previous[1]
    if prev_high is None:
        return True  # the previous range already reaches without bound
    return low <= prev_high + 1


def compile_set(cell: Set) -> IntervalList:
    """Compile one set cell's members into the interval lookup (§10).

    A member is an `Interval` (the closed range word), an exact `int`, or an exact
    address `bytes`. Word sets (`frozenset[str]`) are ct state vocabularies, not
    interval members, and are refused by name.
    """
    ranges: list[tuple[int, int | None]] = []
    for member in cell.members:
        if isinstance(member, Interval):
            ranges.append((member.valid_from, member.valid_to))
        elif isinstance(member, int) and not isinstance(member, bool):
            ranges.append((member, member))
        elif isinstance(member, bytes):
            exact = int.from_bytes(member, "big")
            ranges.append((exact, exact))
        else:
            raise NotSupported("a word set is not an interval set; its members are refused")
    return IntervalList(ranges)


def check_member(kind: str, member: object) -> None:
    """Refuse a member that cannot mean anything in this set's value space (§15)."""
    if isinstance(member, (Interval, bytes)) or (
        isinstance(member, int) and not isinstance(member, bool)
    ):
        if isinstance(member, bytes):
            width = _ADDR_WIDTH.get(kind)
            if width is None:
                raise NotSupported(f"a {kind} set holds ranges and ints, not addresses")
            if len(member) != width:
                raise NotSupported(f"an address member of a {kind} set is {width} bytes")
        return
    raise NotSupported("a set member is a closed interval, an exact int, or an exact address")


def check_match(m: Match) -> None:
    """Check one match cell against the vocabulary — refused at authoring, by name.

    The authoring face runs this when a rule is added, so evaluation only ever sees
    cells that say exactly what they mean.
    """
    entry = _FIELDS.get(m.field)
    if entry is None:
        raise NotSupported(f"the match field {m.field!r} is outside this slice's vocabulary")
    kind, _ = entry
    if m.op == "range":
        raise NotSupported("a literal range is refused; a range is an interval set (§10)")
    if kind == "ct":
        if m.op != "state":
            raise NotSupported("a ct match is op 'state' over the conntrack state words")
        for word in _state_words(m.value):
            if word not in _STATE_WORDS:
                raise NotSupported(f"{word!r} is not a conntrack state word")
        return
    if m.op in ("==", "!="):
        want: type = {"port": int, "addr4": bytes, "addr6": bytes, "name": str, "proto": str}[kind]
        if not isinstance(m.value, want) or isinstance(m.value, bool):
            raise NotSupported(f"op {m.op!r} on {m.field!r} takes a {want.__name__} literal")
        return
    if m.op == "in":
        if kind not in ("port", "addr4", "addr6"):
            raise NotSupported(f"op 'in' is refused on {m.field!r}; sets hold ranges and addresses")
        if not isinstance(m.value, str):
            raise NotSupported("op 'in' names a set by its path")
        return
    if m.op == "prefix" and kind in ("addr4", "addr6"):
        if not isinstance(m.value, str):
            raise NotSupported("op 'prefix' takes a prefix string")
        if _parse_prefix(m.value)[2] != _ADDR_WIDTH[kind]:
            raise NotSupported(f"a {m.field!r} prefix is {_ADDR_WIDTH[kind]} bytes wide")
        return
    raise NotSupported(f"op {m.op!r} is refused on {m.field!r}")


def matches(m: Match, packet: Packet, index: Mapping[str, IntervalList]) -> bool:
    """Whether one match cell holds for one packet. Pure: cells in, a bool out.

    `index` is the compiled set lookups keyed by set path (what `compile_set`
    builds); an `in` match against a path absent from the index never matches.
    """
    kind, side = _FIELDS[m.field]
    if kind == "ct":
        return _ct_matches(m, packet)
    probe = getattr(packet, side)
    if probe is None:
        return False  # a field the packet does not carry never matches
    if not _width_ok(kind, probe):
        return False
    if m.op == "in":
        assert isinstance(m.value, str) and isinstance(probe, (bytes, int))
        lookup = index.get(m.value)
        if lookup is None:
            return False
        value = int.from_bytes(probe, "big") if isinstance(probe, bytes) else probe
        return lookup.contains(value)
    if m.op == "prefix":
        assert isinstance(m.value, str) and isinstance(probe, bytes)
        low, high, _ = _parse_prefix(m.value)
        value = int.from_bytes(probe, "big")
        return low <= value <= high
    assert isinstance(m.value, (int, bytes, str))
    equal = probe == m.value
    return equal if m.op == "==" else not equal


def _width_ok(kind: str, probe: object) -> bool:
    width = _ADDR_WIDTH.get(kind)
    return width is None or (isinstance(probe, bytes) and len(probe) == width)


def _state_words(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset({value.lower()})
    if isinstance(value, frozenset):
        return frozenset(word.lower() for word in value if isinstance(word, str))
    raise NotSupported("op 'state' takes the state words")


def _ct_matches(m: Match, packet: Packet) -> bool:
    if packet.ct is None:
        return False  # no entry, no state: a ct rule is about tracked flows
    return packet.ct.value in _state_words(m.value)
