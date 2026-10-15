"""The evaluation engine (docs/25 §6, §10): `evaluate(table, packet) -> Decision`.

`evaluate` is a pure function over the cells: deterministic, no writes, no hidden
state. It walks the base chains attached at the packet's hook in (priority,
authoring) order and applies first-match semantics down each chain:

- a matched rule's verdict decides — `accept`, `drop`, `queue`, `reject` are
  terminal; `jump` runs its target chain and comes **back to the next rule** of
  the calling chain (never to its end); `return` pops to the caller, and at base
  chain depth it is the continue of §10 — the next rule, never a verdict;
- a chain that runs out of rules returns to its caller, and a base chain that runs
  out applies its policy — the policy is the fallback, never a second match;
- `accept` from one base chain passes the packet to the next base chain at the
  hook (§6 places several filters along one path); `drop`, `reject`, and `queue`
  decide for the hook and stop.

The result is a `Decision`: the verdict, the traversed rule path (every rule
examined, in order — the observable trace), and the counter deltas the evaluation
*wants* written. The deltas are values: the caller authors the writes (§10,
counters are image words) — evaluation never touches a counter cell.

The jump graph is loop-free by construction (a jump cycle is refused when the rule
is authored, `JumpCycle`), so the jump/return stack is bounded by the chain count
and this walk always terminates.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..schema import Match, Verdict
from .match import Packet
from .match import matches as match_holds
from .rule import InvalidRule
from .table import CounterDelta, FilterTable, set_counter_path


@dataclass(frozen=True)
class Decision:
    """What one evaluation decided (§10): the verdict, the traversed rule path
    (the observable trace), and the counter deltas it wants written."""

    verdict: Verdict
    path: tuple[str, ...]
    counters: tuple[CounterDelta, ...]


def evaluate(table: FilterTable, packet: Packet) -> Decision:
    """Evaluate one packet against one filter table — pure, deterministic, done."""
    trace: list[str] = []
    deltas: list[CounterDelta] = []
    verdict = Verdict.ACCEPT
    for chain_path in table.base_chains(packet.hook):
        outcome = _chain(table, chain_path, packet, trace, deltas, ())
        if outcome is not Verdict.ACCEPT:
            verdict = outcome
            break
    return Decision(verdict=verdict, path=tuple(trace), counters=tuple(deltas))


def _chain(
    table: FilterTable,
    chain_path: str,
    packet: Packet,
    trace: list[str],
    deltas: list[CounterDelta],
    stack: tuple[str, ...],
) -> Verdict:
    """Run one chain; `Verdict.RETURN` as an outcome means "back to the caller"."""
    chain = table.get_chain(chain_path)
    stack = (*stack, chain_path)
    for rule_path in chain.rules:
        rule = table.get_rule(rule_path)
        trace.append(rule.path)
        if not _matches(table, rule.matches, packet, deltas):
            continue
        deltas.append(CounterDelta(rule.counter, 1, packet.length))
        verdict = rule.verdict
        if verdict is Verdict.JUMP:
            target = rule.jump
            if target is None:
                raise InvalidRule("a jump rule names its target")  # refused at authoring
            outcome = _chain(table, target, packet, trace, deltas, stack)
            if outcome is Verdict.RETURN:
                continue  # back at the caller: its NEXT rule, never its end
            return outcome
        if verdict is Verdict.RETURN:
            if len(stack) > 1:
                return Verdict.RETURN  # pop to the caller's next rule
            continue  # base chain depth: the continue of §10, the next rule
        return verdict  # accept, drop, reject, queue — terminal
    if len(stack) > 1:
        return Verdict.RETURN  # a jumped-to chain that runs out returns
    return chain.policy  # a base chain that runs out applies its policy


def _matches(
    table: FilterTable,
    matches: tuple[Match, ...],
    packet: Packet,
    deltas: list[CounterDelta],
) -> bool:
    """All atoms are read (no short-circuit) so every named set sees the packet:
    a membership hit is a set counter delta whether or not the rule decides."""
    index = table.set_index()
    matched = True
    for m in matches:
        hit = match_holds(m, packet, index)
        if hit and m.op == "in" and isinstance(m.value, str):
            deltas.append(CounterDelta(set_counter_path(m.value), 1, packet.length))
        matched = matched and hit
    return matched
