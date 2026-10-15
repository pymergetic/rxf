"""Pins for the interval sets (docs/25 §10): IP prefixes and port ranges as
closed intervals, membership including both boundary endpoints, and the honest
corners — an empty set never matches and a literal range is refused by name.

The member word is `Interval` exactly as the doc spells it (`Interval(23, 23)` is
port 23, `Interval(3128, 3130)` is that closed range) and a prefix compiles to
one interval over the address space (`prefix_interval`). The lookup is a sorted,
merged interval list — one test is a binary search, the O(log n) shape §10 asks
for. A named set's (packets, bytes) counter is a cell like a rule's, and it
moves the same way: `evaluate` hands back deltas, the caller authors the writes.
"""

from __future__ import annotations

import pytest

from pymergetic.rxf.net.fw import (
    CounterDelta,
    FilterTable,
    IntervalList,
    NoSuchSet,
    Packet,
    compile_set,
    evaluate,
    prefix_interval,
    set_counter_path,
)
from pymergetic.rxf.net.schema import (
    ConnState,
    Family,
    Hook,
    Interval,
    Match,
    NotSupported,
    Set,
    Verdict,
)

LAN = "net/filter/tables/lan"
INPUT = f"{LAN}/chains/input"
BLOCKED = f"{LAN}/sets/blocked_ports"
ALLOWED = f"{LAN}/sets/allowed_src"


def packet(
    dport: int | None = 22,
    src: bytes = b"\xc0\x00\x02\x01",
    *,
    iif: str | None = None,
    ct: ConnState | None = None,
) -> Packet:
    """One tcp packet at input, with the port or source a set test is about."""
    return Packet(
        hook=Hook.INPUT,
        proto="tcp",
        src=src,
        dst=b"\xc0\x00\x02\x02",
        sport=50000,
        dport=dport,
        length=60,
        iif=iif,
        ct=ct,
    )


def test_port_range_membership_includes_the_endpoints() -> None:
    """A closed port range covers both ends: 23 and 3128 and 3130 are in, and one
    step outside is out."""
    table = FilterTable(LAN)
    table.add_set(BLOCKED, "inet_service", (Interval(23, 23), Interval(3128, 3130)))
    lookup = table.set_index()[BLOCKED]
    assert lookup.contains(23) and lookup.contains(3128) and lookup.contains(3130)
    assert not lookup.contains(22) and not lookup.contains(24)
    assert not lookup.contains(3127) and not lookup.contains(3131)


def test_prefix_membership_includes_the_endpoints() -> None:
    """A prefix compiles to its address-space interval: the network address and
    the last address are both members, one step out is not."""
    span = prefix_interval("192.0.2.0/24")
    assert span == Interval(0xC0000200, 0xC00002FF)
    assert prefix_interval("192.0.2.7/24") == span  # the prefix, not the spelling
    table = FilterTable(LAN)
    table.add_set(ALLOWED, "ipv4_addr", (span,))
    lookup = table.set_index()[ALLOWED]
    assert lookup.contains(int.from_bytes(b"\xc0\x00\x02\x00", "big"))
    assert lookup.contains(int.from_bytes(b"\xc0\x00\x02\xff", "big"))
    assert not lookup.contains(int.from_bytes(b"\xc0\x00\x03\x00", "big"))
    assert not lookup.contains(int.from_bytes(b"\xc0\x00\x01\xff", "big"))


def test_a_v6_prefix_is_one_interval_too() -> None:
    """Both families, one model (§9): a v6 prefix is an interval over the same
    word, just wider."""
    span = prefix_interval("2001:db8::/32")
    assert span.valid_from == 0x20010DB8 << 96
    assert span.valid_to is not None
    assert span.valid_to - span.valid_from == (1 << 96) - 1


def test_an_empty_set_never_matches() -> None:
    """An empty set holds nothing, so a rule against it never matches — the
    packet falls through to whatever decides next (here, the policy)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_set(BLOCKED, "inet_service", ())
    table.add_rule(INPUT, (Match("tcp", "in", BLOCKED),), Verdict.ACCEPT)
    decision = evaluate(table, packet(dport=3128))
    assert decision.verdict is Verdict.DROP
    assert decision.counters == ()
    assert not compile_set(Set(BLOCKED, Family.INET, "inet_service", ())).contains(0)
    assert len(IntervalList(())) == 0


def test_set_counters_count_membership_hits() -> None:
    """A named set's counter counts the lookups that found membership — and like
    every counter it moves as a delta the caller authors (§10)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_set(BLOCKED, "inet_service", (Interval(23, 23), Interval(3128, 3130)))
    table.add_rule(INPUT, (Match("tcp", "in", BLOCKED),), Verdict.DROP)
    hit = evaluate(table, packet(dport=3129))
    assert CounterDelta(set_counter_path(BLOCKED), 1, 60) in hit.counters
    miss = evaluate(table, packet(dport=80))
    assert set_counter_path(BLOCKED) not in [delta.path for delta in miss.counters]
    for delta in hit.counters:
        table.write_counter(delta)
    assert table.get_counter(set_counter_path(BLOCKED)).packets == 1
    assert table.get_counter(set_counter_path(BLOCKED)).bytes == 60


def test_set_membership_drives_the_verdict() -> None:
    """`in` over a named set is an ordinary match: a member decides the rule, a
    non-member falls through."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.ACCEPT)
    table.add_set(ALLOWED, "ipv4_addr", (prefix_interval("192.0.2.0/24"),))
    table.add_rule(INPUT, (Match("ip.saddr", "in", ALLOWED),), Verdict.DROP)
    assert evaluate(table, packet(src=b"\xc0\x00\x02\x63")).verdict is Verdict.DROP
    assert evaluate(table, packet(src=b"\xc0\x00\x03\x01")).verdict is Verdict.ACCEPT


def test_a_literal_range_is_refused() -> None:
    """A range is an interval set, never a literal (§10) — refused by name."""

    def build(table: FilterTable) -> None:
        table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
        table.add_rule(INPUT, (Match("tcp", "range", 22),), Verdict.DROP)

    with pytest.raises(NotSupported):
        build(FilterTable(LAN))


def test_a_missing_set_is_refused_at_add_time() -> None:
    """A match names a set this table holds — typed, at authoring, never mid-packet."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    with pytest.raises(NoSuchSet):
        table.add_rule(INPUT, (Match("tcp", "in", f"{LAN}/sets/nowhere"),), Verdict.DROP)


def test_an_unknown_set_kind_is_refused() -> None:
    """This slice's interval kinds are addresses and services; anything else is
    refused by name rather than half-supported."""
    table = FilterTable(LAN)
    with pytest.raises(NotSupported):
        table.add_set(f"{LAN}/sets/names", "ifname", ())


def test_the_lookup_is_sorted_and_merged() -> None:
    """The compiled set is a sorted, disjoint interval list — overlapping and
    contiguous members merge, so one membership test is one binary search."""
    lookup = IntervalList([(4, 6), (1, 3), (10, 10), (11, 12), (20, None)])
    assert len(lookup) == 3  # 1..6, 10..12, 20..unbounded
    assert lookup.contains(1) and lookup.contains(6) and lookup.contains(12)
    assert lookup.contains(20) and lookup.contains(10**9)  # the open end reaches
    assert not lookup.contains(0) and not lookup.contains(7) and not lookup.contains(19)
    wide = IntervalList((n, n + 1) for n in range(0, 2000, 2))
    assert len(wide) == 1 and wide.contains(1999) and not wide.contains(2001)


def test_set_members_supersede_by_appending() -> None:
    """Members are added by authoring a wider cell — the old cell stands still
    (docs/24), and the lookup follows the current one."""
    table = FilterTable(LAN)
    table.add_set(BLOCKED, "inet_service", (Interval(23, 23),))
    before = table.get_set(BLOCKED)
    table.add_members(BLOCKED, (Interval(3128, 3130),))
    after = table.get_set(BLOCKED)
    assert before.members == (Interval(23, 23),)
    assert after.members == (Interval(23, 23), Interval(3128, 3130))
    assert table.set_index()[BLOCKED].contains(3130)


def test_a_wrong_width_address_member_is_refused() -> None:
    """A member that cannot mean anything in the set's value space is refused —
    a v6 address is never a member of a v4 set."""
    table = FilterTable(LAN)
    with pytest.raises(NotSupported):
        table.add_set(ALLOWED, "ipv4_addr", (b"\x20\x01" + b"\x00" * 14,))
    with pytest.raises(NotSupported):
        table.add_set(f"{LAN}/sets/svc", "inet_service", (b"\x00\x17",))
