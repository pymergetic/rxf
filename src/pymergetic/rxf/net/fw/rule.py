"""Rules and their verdicts (docs/25 §10): ordered entries, one match expression
each, one verdict each — and the jump graph that has to stay loop-free.

A verdict is one of: `accept`, `drop`, `queue`, `reject` (terminal); `jump` to a
named chain, which comes **back** to the next rule of the calling chain when the
target returns or runs out; and `return`. The one honest encoding note: the frozen
`Verdict` set carries no `continue`, so a continue is `Verdict.RETURN` at base
chain depth (where there is no caller to return to, it falls through to the next
rule — the shape the doc's own `continue` describes). `Verdict.CONTINUE` is a named
schema seam request; until it lands, a continue inside a regular chain is not
expressible in one `Rule` cell.

The jump graph is checked when the rule is **authored**, never while a packet is
being evaluated: a jump that would close a cycle refuses `JumpCycle` at add time.
The graph therefore stays acyclic and the evaluation stack is bounded by the chain
count — no depth refusal is needed on the packet path.

Everything here is validation: it reads cells and edges and either returns or
raises a typed refusal (§15's pattern: one name per condition). It never mutates.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet

from ..schema import Match, Refusal, Verdict
from . import match as matchmod


class JumpCycle(Refusal):
    """A jump that would return to itself — refused when the rule is authored (§10),
    never at evaluation."""


class NoSuchChain(Refusal):
    """A jump names a chain this table does not hold."""


class NoSuchSet(Refusal):
    """A match names a set this table does not hold."""


class InvalidRule(Refusal):
    """The rule would not say what it means: a jump verdict without a target, or a
    target on a verdict that is not a jump."""


def reaches(edges: Mapping[str, AbstractSet[str]], start: str, goal: str) -> bool:
    """Whether `goal` is already reachable from `start` along the jump edges."""
    seen: set[str] = set()
    frontier = [start]
    while frontier:
        here = frontier.pop()
        for target in edges.get(here, ()):
            if target == goal:
                return True
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return False


def check_jumps(
    edges: Mapping[str, AbstractSet[str]], source: str, target: str, *, has_chain: Callable[[str], bool]
) -> None:
    """One jump edge is legal only if it exists and closes no cycle."""
    if not has_chain(target):
        raise NoSuchChain(f"{target!r} is not a chain of this table")
    if target == source or reaches(edges, target, source):
        raise JumpCycle(f"jump from {source!r} to {target!r} would return to itself")


def check_verdict(
    verdict: Verdict,
    jump: str | None,
    *,
    source: str,
    edges: Mapping[str, AbstractSet[str]],
    has_chain: Callable[[str], bool],
) -> None:
    """The verdict and its target must agree before the cell is authored."""
    if verdict is Verdict.JUMP:
        if jump is None:
            raise InvalidRule("a jump verdict names its target chain")
        check_jumps(edges, source, jump, has_chain=has_chain)
    elif jump is not None:
        raise InvalidRule(f"a {verdict.value!r} verdict carries no jump target")


def check_matches(matches: tuple[Match, ...], *, has_set: Callable[[str], bool]) -> None:
    """Every match cell is in the vocabulary and every set path exists."""
    for m in matches:
        matchmod.check_match(m)
        if m.op == "in":
            assert isinstance(m.value, str)
            if not has_set(m.value):
                raise NoSuchSet(f"{m.value!r} is not a set of this table")
