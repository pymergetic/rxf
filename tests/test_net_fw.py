"""Pins for the firewall plane's engine (docs/25 §6, §10): first-match semantics,
the jump/return stack, base chain policies, and counters as authored deltas.

The frozen cells come from `net.schema`; what is pinned here is the behavior the
doc promises over them: a matched rule's verdict decides and the policy is only
the fallback, a jump comes back to the **next rule** of the calling chain and
never to its end, a jump cycle is refused when the rule is authored (typed) and
never on the packet path, a continue falls through to the next rule, and
`evaluate` is pure — it hands back counter deltas and the caller authors the
writes. The continue note: `Verdict.CONTINUE` is one rule cell at any depth —
inside a regular chain included; `Verdict.RETURN` at base chain depth (no caller
to return to) falls through to the next rule as it always has, and that landed
behavior is pinned below beside the new word.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.net.fw import (
    CounterDelta,
    FilterTable,
    InvalidPolicy,
    InvalidRule,
    JumpCycle,
    NoSuchChain,
    Packet,
    evaluate,
    rule_counter_path,
)
from pymergetic.rxftk.net.schema import (
    IP,
    ChainType,
    ConnState,
    Hook,
    Match,
    NotSupported,
    Verdict,
)

LAN = "net/filter/tables/lan"
INPUT = f"{LAN}/chains/input"
SCAN = f"{LAN}/chains/scan"
GUARD = f"{LAN}/chains/guard"


def tcp_packet(
    *,
    hook: Hook = Hook.INPUT,
    proto: str = "tcp",
    src: IP = b"\xc0\x00\x02\x01",
    dst: IP = b"\xc0\x00\x02\x02",
    sport: int | None = 50000,
    dport: int | None = 22,
    iif: str | None = None,
    oif: str | None = None,
    ct: ConnState | None = None,
    length: int = 60,
) -> Packet:
    """One honest tcp packet; keyword fields override what a test is about."""
    return Packet(
        hook=hook,
        proto=proto,
        src=src,
        dst=dst,
        sport=sport,
        dport=dport,
        iif=iif,
        oif=oif,
        ct=ct,
        length=length,
    )


def sample_table() -> FilterTable:
    """input: jump to scan for tcp, accept ssh, then drop — the jump test shape."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_regular_chain(SCAN)
    table.add_rule(INPUT, (Match("meta.protocol", "==", "tcp"),), Verdict.JUMP, jump=SCAN)
    table.add_rule(INPUT, (Match("tcp", "==", 22),), Verdict.ACCEPT)
    table.add_rule(INPUT, (), Verdict.DROP)
    table.add_rule(SCAN, (Match("ip", "==", b"\xc0\x00\x02\x63"),), Verdict.DROP)
    return table


def test_first_match_decides() -> None:
    """The first matching rule's verdict decides; later rules are never consulted."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("tcp", "==", 22),), Verdict.ACCEPT)
    table.add_rule(INPUT, (), Verdict.DROP)
    decision = evaluate(table, tcp_packet())
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0",)


def test_jump_returns_to_the_next_rule_of_the_calling_chain() -> None:
    """A jumped chain that gives up comes back to the caller's NEXT rule — the
    drop behind the jump never runs, so the packet is accepted, not dropped."""
    table = sample_table()
    decision = evaluate(table, tcp_packet(src=b"\xc0\x00\x02\x01"))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0", f"{SCAN}/rules/0", f"{INPUT}/rules/1")


def test_return_pops_to_the_calling_chain() -> None:
    """An explicit `return` in a jumped chain pops to the caller's next rule."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_regular_chain(GUARD)
    table.add_rule(INPUT, (), Verdict.JUMP, jump=GUARD)
    table.add_rule(INPUT, (), Verdict.ACCEPT)
    table.add_rule(GUARD, (Match("meta.iif", "==", "lan0"),), Verdict.RETURN)
    table.add_rule(GUARD, (), Verdict.DROP)
    decision = evaluate(table, tcp_packet(iif="lan0"))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0", f"{GUARD}/rules/0", f"{INPUT}/rules/1")


def test_a_jump_cycle_is_refused_at_add_time() -> None:
    """A cycle is a typed refusal while the rule is authored — evaluation can
    never see one, because the jump graph stays a loop-free graph."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_regular_chain(SCAN)
    table.add_rule(INPUT, (), Verdict.JUMP, jump=SCAN)
    with pytest.raises(JumpCycle):
        table.add_rule(SCAN, (), Verdict.JUMP, jump=INPUT)  # back to the caller
    with pytest.raises(JumpCycle):
        table.add_rule(SCAN, (), Verdict.JUMP, jump=SCAN)  # into itself
    with pytest.raises(JumpCycle):
        table.add_rule(INPUT, (), Verdict.JUMP, jump=INPUT)  # self at the base chain
    # the refused rules were never authored and the engine still answers
    decision = evaluate(table, tcp_packet())
    assert decision.verdict is Verdict.DROP  # the jump returned, the chain ran out
    assert decision.path == (f"{INPUT}/rules/0",)


def test_policy_applies_only_when_no_rule_matches() -> None:
    """The policy is the fallback, never a second match: a matching rule's verdict
    beats it, and a packet no rule matches takes it."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.ACCEPT)
    table.add_rule(INPUT, (Match("meta.protocol", "==", "tcp"),), Verdict.DROP)
    dropped = evaluate(table, tcp_packet())
    assert dropped.verdict is Verdict.DROP
    assert dropped.path == (f"{INPUT}/rules/0",)
    accepted = evaluate(table, tcp_packet(proto="udp", dport=53))
    assert accepted.verdict is Verdict.ACCEPT  # the policy, no rule matched
    assert accepted.path == (f"{INPUT}/rules/0",)  # examined, not matched


def test_continue_falls_through_to_the_next_rule() -> None:
    """A continue decides nothing and the walk goes on: the next rule still runs,
    even one behind it that would drop. `Verdict.CONTINUE` says that in one rule
    cell, at any depth."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("meta.iif", "==", "lan0"),), Verdict.CONTINUE)  # continue
    table.add_rule(INPUT, (Match("tcp", "==", 22),), Verdict.ACCEPT)
    table.add_rule(INPUT, (), Verdict.DROP)
    decision = evaluate(table, tcp_packet(iif="lan0"))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0", f"{INPUT}/rules/1")


def test_run_out_after_a_continue_applies_the_policy() -> None:
    """The fallback corner: a continue is not a decision, so a chain that runs out
    after one applies the policy (the reading of "no rule matches" as "no rule
    decides"; pinned so the corner is never a surprise)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("meta.iif", "==", "lan0"),), Verdict.CONTINUE)  # continue
    decision = evaluate(table, tcp_packet(iif="lan0"))
    assert decision.verdict is Verdict.DROP
    assert decision.path == (f"{INPUT}/rules/0",)


def test_a_continue_inside_a_regular_chain_is_one_rule_cell() -> None:
    """The expressiveness the seam closed: a continue inside a regular chain is one
    `Rule` cell — it falls to the next rule of its **own** chain (never straight
    back to the caller), and the run-out keeps its meaning: back to the caller's
    next rule."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_regular_chain(SCAN)
    table.add_rule(INPUT, (), Verdict.JUMP, jump=SCAN)
    table.add_rule(INPUT, (), Verdict.ACCEPT)  # the caller's next rule after the run-out
    table.add_rule(SCAN, (Match("meta.iif", "==", "lan0"),), Verdict.CONTINUE)  # one cell
    table.add_rule(SCAN, (Match("meta.iif", "==", "wan0"),), Verdict.DROP)  # examined, no match
    decision = evaluate(table, tcp_packet(iif="lan0"))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0", f"{SCAN}/rules/0", f"{SCAN}/rules/1", f"{INPUT}/rules/1")


def test_return_at_base_chain_depth_falls_through() -> None:
    """`return` at base chain depth has no caller to pop to — it falls through to
    the next rule, the landed behavior kept whole now that `continue` is a word of
    its own (expressiveness, not a redesign)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("meta.iif", "==", "lan0"),), Verdict.RETURN)
    table.add_rule(INPUT, (), Verdict.ACCEPT)
    decision = evaluate(table, tcp_packet(iif="lan0"))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == (f"{INPUT}/rules/0", f"{INPUT}/rules/1")


def test_evaluate_is_pure_and_counters_are_authored_writes() -> None:
    """evaluate() answers with counter deltas and touches nothing: the counter
    cells stand still until the caller authors the writes (§10)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    rule = table.add_rule(INPUT, (), Verdict.ACCEPT)
    packet = tcp_packet(length=100)
    first = evaluate(table, packet)
    second = evaluate(table, packet)
    assert first == second  # deterministic and pure
    assert first.counters == (CounterDelta(rule.counter, 1, 100),)
    assert table.get_counter(rule.counter).packets == 0  # nothing applied inside
    written = [table.write_counter(delta) for delta in first.counters]  # the caller authors
    assert written == [table.get_counter(rule.counter)]
    assert written[0].packets == 1 and written[0].bytes == 100


def test_counter_deltas_count_only_what_matched() -> None:
    """A rule's counter counts packets that matched it — a rule never reached or
    never matched wants no delta."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    first = table.add_rule(INPUT, (Match("meta.protocol", "==", "udp"),), Verdict.ACCEPT)
    second = table.add_rule(INPUT, (), Verdict.DROP)
    decision = evaluate(table, tcp_packet())
    assert decision.verdict is Verdict.DROP
    assert decision.counters == (CounterDelta(second.counter, 1, 60),)
    assert rule_counter_path(first.path) == first.counter  # derived convention


def test_every_rule_carries_a_counter_cell() -> None:
    """Counters are cells, one per rule and one per named set — authored with the
    entry, readable back through the honest metrics path (§10)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    rule = table.add_rule(INPUT, (), Verdict.ACCEPT)
    assert table.get_counter(rule.counter).packets == 0
    assert table.get_counter(rule.counter).bytes == 0
    assert rule.counter in table.paths()


def test_accept_passes_to_the_next_base_chain_by_priority() -> None:
    """Several filters sit along one packet path (§6): an accept from one base
    chain passes the packet on to the next, in priority order — only drop (or
    queue, or reject) decides for the hook."""
    table = FilterTable(LAN)
    table.add_base_chain(f"{LAN}/chains/wide", hook=Hook.INPUT, priority=0, policy=Verdict.ACCEPT)
    table.add_base_chain(INPUT, hook=Hook.INPUT, priority=10, policy=Verdict.ACCEPT)
    table.add_rule(f"{LAN}/chains/wide", (Match("tcp", "==", 22),), Verdict.ACCEPT)
    table.add_rule(INPUT, (Match("tcp", "==", 22),), Verdict.DROP)
    decision = evaluate(table, tcp_packet())
    assert decision.verdict is Verdict.DROP
    assert decision.path == (f"{LAN}/chains/wide/rules/0", f"{INPUT}/rules/0")


def test_queue_and_reject_are_terminal_verdicts() -> None:
    """A queue hands the packet over and a reject ends the walk — the engine
    returns the verdict and stops (the error a reject owes is the packet path's)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("meta.iif", "==", "lan0"),), Verdict.QUEUE)
    table.add_rule(INPUT, (Match("meta.iif", "==", "wan0"),), Verdict.REJECT)
    assert evaluate(table, tcp_packet(iif="lan0")).verdict is Verdict.QUEUE
    assert evaluate(table, tcp_packet(iif="wan0")).verdict is Verdict.REJECT


def test_a_hook_without_a_base_chain_accepts() -> None:
    """A hook no filter is attached to is an unfiltered path: nothing dropped the
    packet, so it is accepted, with an empty trace."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    decision = evaluate(table, tcp_packet(hook=Hook.FORWARD))
    assert decision.verdict is Verdict.ACCEPT
    assert decision.path == () and decision.counters == ()


def test_a_base_chain_policy_is_accept_or_drop() -> None:
    """A policy is what the rules never reach — accept or drop, nothing else (§10)."""
    table = FilterTable(LAN)
    with pytest.raises(InvalidPolicy):
        table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.QUEUE)
    with pytest.raises(InvalidPolicy):
        table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.RETURN)


def test_a_jump_target_must_exist() -> None:
    """A jump names a chain this table holds — refused at add time, typed."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    with pytest.raises(NoSuchChain):
        table.add_rule(INPUT, (), Verdict.JUMP, jump=f"{LAN}/chains/nowhere")


def test_a_verdict_and_its_target_must_agree() -> None:
    """A jump names its target and no other verdict carries one (§15 voice:
    the cell has to say what it means)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_regular_chain(SCAN)
    with pytest.raises(InvalidRule):
        table.add_rule(INPUT, (), Verdict.JUMP)
    with pytest.raises(InvalidRule):
        table.add_rule(INPUT, (), Verdict.ACCEPT, jump=SCAN)


def test_a_literal_range_is_refused_for_a_set() -> None:
    """Ranges are interval sets (§10) — a literal range is refused by name."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    with pytest.raises(NotSupported):
        table.add_rule(INPUT, (Match("tcp", "range", 22),), Verdict.DROP)


def test_rules_are_ordered_entries_of_their_chain() -> None:
    """Rules are cells in order: their paths count up from the chain and the chain
    cell lists them in the order they were authored."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (), Verdict.ACCEPT)
    table.add_rule(INPUT, (), Verdict.DROP)
    chain = table.get_chain(INPUT)
    assert chain.rules == (f"{INPUT}/rules/0", f"{INPUT}/rules/1")
    assert chain.type is ChainType.FILTER
    assert table.get_rule(f"{INPUT}/rules/1").verdict is Verdict.DROP


def test_a_frozen_cell_never_moves() -> None:
    """Authoring a change authors a new cell; the old one stands still (docs/24)."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    before = table.get_chain(INPUT)
    table.add_rule(INPUT, (), Verdict.ACCEPT)
    after = table.get_chain(INPUT)
    assert before.rules == () and after.rules == (f"{INPUT}/rules/0",)


def test_the_trace_is_the_traversed_rule_path() -> None:
    """The observable trace is every rule examined, in order — matched or not —
    so a walk can be replayed from the decision alone."""
    table = FilterTable(LAN)
    table.add_base_chain(INPUT, hook=Hook.INPUT, policy=Verdict.DROP)
    table.add_rule(INPUT, (Match("meta.protocol", "==", "udp"),), Verdict.ACCEPT)
    table.add_rule(INPUT, (Match("meta.iif", "==", "wan0"),), Verdict.ACCEPT)
    table.add_rule(INPUT, (), Verdict.DROP)
    decision = evaluate(table, tcp_packet(iif="lan0", ct=ConnState.NEW))
    assert decision.verdict is Verdict.DROP
    assert decision.path == (f"{INPUT}/rules/0", f"{INPUT}/rules/1", f"{INPUT}/rules/2")
