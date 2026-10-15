"""Pins for the conntrack slice (docs/25 §14): entries over `ConnTuple`/`ConnState`,
the word set's transitions, the established fast path, and the honesty rule that
makes this slice small — conntrack is TRANSIENT and never lands in the durable
plane.

What is pinned: an unseen tuple authors a `NEW` entry; state moves only along the
word set (`NEW` confirms to `ESTABLISHED` and may be `RELATED`; `ESTABLISHED`
never goes back to `NEW`; `INVALID` never leaves itself) and a step outside it is
a typed refusal; one flow is one entry whatever direction packets arrive in and a
known entry answers from its cell — the established fast path, no re-derivation;
`INVALID` never matches an established rule; and the durable table holds no
conntrack path, ever — writes here are invisible to a successor.
"""

from __future__ import annotations

import pytest

from pymergetic.rxf.net.fw import (
    ConntrackPlane,
    FilterTable,
    InvalidTransition,
    NoSuchCell,
    Packet,
    evaluate,
)
from pymergetic.rxf.net.schema import (
    ConnState,
    Hook,
    Match,
    Verdict,
)

LAN = "net/filter/tables/lan"
INPUT = f"{LAN}/chains/input"


def tcp_packet(
    *,
    hook: Hook = Hook.INPUT,
    proto: str = "tcp",
    src: bytes = b"\xc0\x00\x02\x01",
    dst: bytes = b"\xc0\x00\x02\x02",
    sport: int | None = 50000,
    dport: int | None = 22,
    ct: ConnState | None = None,
) -> Packet:
    """One honest tcp packet; keyword fields override what a test is about."""
    return Packet(
        hook=hook,
        proto=proto,
        src=src,
        dst=dst,
        sport=sport,
        dport=dport,
        ct=ct,
        length=60,
    )


def flow() -> tuple[Packet, Packet]:
    """One flow in both directions — a connection is one entry either way."""
    return tcp_packet(), tcp_packet(
        src=b"\xc0\x00\x02\x02",
        dst=b"\xc0\x00\x02\x01",
        sport=22,
        dport=50000,
    )


def test_an_unseen_tuple_is_new() -> None:
    """A tuple the plane has never seen authors a NEW entry under net/ct/."""
    plane = ConntrackPlane()
    forward, _ = flow()
    entry = plane.observe(forward, now=10)
    assert entry.state is ConnState.NEW
    assert entry.path.startswith("net/ct/")
    assert entry.expires == 10 + 30_000
    assert plane.entries() == (entry,)


def test_state_transitions_follow_the_word_set() -> None:
    """State moves only along §14's word set; a step outside it is refused typed."""
    plane = ConntrackPlane()
    forward, _ = flow()
    entry = plane.observe(forward)
    assert plane.transition(entry.tuple, ConnState.ESTABLISHED).state is ConnState.ESTABLISHED
    with pytest.raises(InvalidTransition):  # confirmed never goes back to new
        plane.transition(entry.tuple, ConnState.NEW)
    with pytest.raises(InvalidTransition):  # confirmed is not related
        plane.transition(entry.tuple, ConnState.RELATED)
    assert plane.transition(entry.tuple, ConnState.INVALID).state is ConnState.INVALID
    with pytest.raises(InvalidTransition):  # invalid never leaves itself
        plane.transition(entry.tuple, ConnState.ESTABLISHED)
    assert plane.transition(entry.tuple, ConnState.INVALID).state is ConnState.INVALID


def test_new_may_go_established_or_related() -> None:
    """NEW confirms to ESTABLISHED and may become RELATED — both steps of the
    word set, refused neither."""
    plane = ConntrackPlane()
    forward, _ = flow()
    entry = plane.observe(forward)
    related = plane.transition(entry.tuple, ConnState.RELATED)
    assert related.state is ConnState.RELATED
    assert plane.transition(related.tuple, ConnState.ESTABLISHED).state is ConnState.ESTABLISHED


def test_the_established_fast_path_answers_from_the_entry() -> None:
    """A known flow answers from its cell: forward or reverse is one entry, and no
    second entry is authored for it — the lookup is the state, nothing re-derived."""
    plane = ConntrackPlane()
    forward, backward = flow()
    entry = plane.observe(forward)
    established = plane.transition(entry.tuple, ConnState.ESTABLISHED)
    again = plane.observe(forward)
    turned = plane.observe(backward)
    assert again is established and turned is established  # both directions, one entry
    assert again.state is ConnState.ESTABLISHED
    assert plane.entries() == (established,)


def test_invalid_never_matches_an_established_rule() -> None:
    """The established rule sees established and related flows — an INVALID flow
    falls through it to whatever decides next."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(
        INPUT,
        (Match("ct", "state", frozenset({"established", "related"})),),
        Verdict.ACCEPT,
    )
    accepted = evaluate(table, tcp_packet(ct=ConnState.ESTABLISHED))
    assert accepted.verdict is Verdict.ACCEPT
    related = evaluate(table, tcp_packet(ct=ConnState.RELATED))
    assert related.verdict is Verdict.ACCEPT
    invalid = evaluate(table, tcp_packet(ct=ConnState.INVALID))
    assert invalid.verdict is Verdict.DROP  # never an established match
    new = evaluate(table, tcp_packet(ct=ConnState.NEW))
    assert new.verdict is Verdict.DROP


def test_an_untracked_packet_matches_no_ct_rule() -> None:
    """A packet with no entry carries no state, so no ct rule speaks for it."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(
        INPUT,
        (Match("ct", "state", frozenset({"established", "related", "invalid", "new"})),),
        Verdict.ACCEPT,
    )
    assert evaluate(table, tcp_packet()).verdict is Verdict.DROP


def test_conntrack_writes_never_land_in_the_durable_plane() -> None:
    """§14's honesty rule: a conntrack write is invisible to the durable plane —
    the table's cells and paths hold no net/ct/ entry, before or after traffic."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (), Verdict.ACCEPT)
    before = table.cells()
    plane = ConntrackPlane()
    forward, _ = flow()
    entry = plane.observe(forward)
    established = plane.transition(entry.tuple, ConnState.ESTABLISHED)
    assert table.cells() == before  # not one cell moved
    assert all(not path.startswith("net/ct/") for path in table.paths())
    assert all(cell.path != established.path for cell in before)
    assert established.path.startswith("net/ct/")  # it lives only in the transient plane
    assert plane.entries() == (established,)


def test_expired_entries_age_out() -> None:
    """Transient state ages out on the edge timer's ticks (S9) — dropped, never
    superseded into history, and what went is handed back honestly."""
    plane = ConntrackPlane(ttl=100)
    forward, _ = flow()
    entry = plane.observe(forward, now=0)
    assert plane.expire(50) == ()
    assert plane.expire(100) == (entry,)
    assert plane.entries() == ()


def test_transition_without_an_entry_is_refused() -> None:
    """A state change is authored for a flow the plane tracks — a flow with no
    entry refuses NoSuchCell rather than silently authoring one."""
    plane = ConntrackPlane()
    forward, _ = flow()
    with pytest.raises(NoSuchCell):
        plane.transition(forward, ConnState.ESTABLISHED)


def test_invalid_stays_invalid_until_it_ages_out() -> None:
    """An invalid entry answers INVALID to every later packet of the flow — it is
    the state, and it is never quietly repaired."""
    plane = ConntrackPlane()
    forward, backward = flow()
    entry = plane.observe(forward)
    plane.transition(entry.tuple, ConnState.INVALID)
    assert plane.observe(backward).state is ConnState.INVALID
    assert plane.observe(forward).state is ConnState.INVALID
