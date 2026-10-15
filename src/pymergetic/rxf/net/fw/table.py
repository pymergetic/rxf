"""The cell planes of the firewall (docs/25 §10): the durable filter table and the
transient conntrack plane.

`FilterTable` is one filter table's assembled current image over the frozen cells
(`Table`, `Chain`, `Rule`, `Set`, `Counter`) plus the hook registry and the jump
edges the authoring face keeps loop-free. Cells never mutate: authoring a change
authors a new cell, and the index holds the current one. The history of a firewall
change — `AUTHORED_BY` / `SUPERSEDES`, docs/24's delta chain — is the layer above
this plane; this slice is the current image only.

Counters are image words (§10): every rule and every named set carries a `Counter`
cell of (packets, bytes). Evaluation never touches them — it hands back
`CounterDelta` values and the caller authors the writes through `write_counter`.
A set's counter lives at `<set path>/counter` (the landed `Set` cell names no
counter path — a named schema seam); a rule's counter is its `Rule.counter` path.

`ConntrackPlane` is TRANSIENT (§14): its `Conntrack` cells live under `net/ct/`
and never enter a successor — the durable plane holds no conntrack path, ever.
One flow is one entry whatever direction the packets arrive in, a known entry
answers from its cell (the established fast path: no re-derivation), and state
moves only along the word set of §14 — `NEW` confirms to `ESTABLISHED`, may be
`RELATED`, and goes `INVALID`; `ESTABLISHED` never goes back to `NEW`, and
`INVALID` stays invalid until the entry ages out.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..schema import (
    Chain,
    ChainType,
    ConnState,
    Conntrack,
    ConnTuple,
    Counter,
    Family,
    Hook,
    Interval,
    Match,
    NotSupported,
    Refusal,
    Rule,
    Set,
    Table,
    Value,
    Verdict,
)
from . import match as matchmod
from . import rule as rulemod
from .match import IntervalList, compile_set

Member = Interval | Value  # a set member word (the frozen `Set.members` shape)
Cell = Table | Chain | Rule | Set | Counter  # the durable cells of one table (§14)

SET_KINDS = frozenset({"ipv4_addr", "ipv6_addr", "inet_service"})

_ALLOWED_TRANSITIONS: dict[ConnState, frozenset[ConnState]] = {
    ConnState.NEW: frozenset(
        {ConnState.NEW, ConnState.ESTABLISHED, ConnState.RELATED, ConnState.INVALID}
    ),
    ConnState.ESTABLISHED: frozenset({ConnState.ESTABLISHED, ConnState.INVALID}),
    ConnState.RELATED: frozenset(
        {ConnState.RELATED, ConnState.ESTABLISHED, ConnState.INVALID}
    ),
    ConnState.INVALID: frozenset({ConnState.INVALID}),
}


class DuplicateCell(Refusal):
    """A path is authored twice. Superseding is the delta chain's job (docs/24);
    this index refuses a rewrite of one cell by name."""


class NoSuchCell(Refusal):
    """A lookup or a counter write names a cell this table does not hold."""


class InvalidPolicy(Refusal):
    """A base chain policy is accept or drop (§10); anything else is refused."""


class InvalidTransition(Refusal):
    """A conntrack state step outside the word set (§14) — refused by name."""


@dataclass(frozen=True)
class CounterDelta:
    """What one evaluation wants written to a counter cell — a value, not a write.

    The caller authors the write (`FilterTable.write_counter`); `evaluate` only
    ever produces these (§10: counters move as cell writes, authored deltas).
    """

    path: str
    packets: int
    bytes: int


def rule_counter_path(rule_path: str) -> str:
    """Where a rule's counter cell lives unless its `Rule.counter` says otherwise."""
    return f"{rule_path}/counter"


def set_counter_path(set_path: str) -> str:
    """Where a named set's counter cell lives — the derived convention until the
    landed `Set` cell carries its own counter path (the named seam)."""
    return f"{set_path}/counter"


class FilterTable:
    """One filter table (§10): its chains, rules, sets, and counters as cells.

    The constructor is the table cell's authoring; `add_base_chain` attaches a
    chain at a hook with the policy its rules never reach, `add_regular_chain`
    authors a jump target whose run-out returns to its caller, and `add_rule`
    appends one ordered entry. Every add is validated and typed-refused (§15):
    duplicate paths, unknown chains and sets, verdicts that do not say what they
    mean, and jump cycles all fail here — never on the packet path.
    """

    def __init__(self, path: str, family: Family = Family.INET) -> None:
        self.path = path
        self.family = family
        self._table = Table(path, family, ())
        self._chains: dict[str, Chain] = {}
        self._rules: dict[str, Rule] = {}
        self._sets: dict[str, Set] = {}
        self._counters: dict[str, Counter] = {}
        self._bases: dict[Hook, list[str]] = {}
        self._jumps: dict[str, set[str]] = {}
        self._index: dict[str, IntervalList] = {}

    # ---- the cells, read back ----

    @property
    def cell(self) -> Table:
        """The table cell as it stands (frozen; authoring supersedes, never edits)."""
        return self._table

    def get_chain(self, path: str) -> Chain:
        chain = self._chains.get(path)
        if chain is None:
            raise rulemod.NoSuchChain(f"{path!r} is not a chain of this table")
        return chain

    def get_rule(self, path: str) -> Rule:
        rule = self._rules.get(path)
        if rule is None:
            raise NoSuchCell(f"{path!r} is not a rule of this table")
        return rule

    def get_set(self, path: str) -> Set:
        cell = self._sets.get(path)
        if cell is None:
            raise rulemod.NoSuchSet(f"{path!r} is not a set of this table")
        return cell

    def get_counter(self, path: str) -> Counter:
        counter = self._counters.get(path)
        if counter is None:
            raise NoSuchCell(f"{path!r} is not a counter of this table")
        return counter

    def base_chains(self, hook: Hook) -> tuple[str, ...]:
        """The base chains attached at a hook, in (priority, authoring) order."""
        return tuple(
            sorted(self._bases.get(hook, ()), key=lambda path: self._chains[path].priority)
        )

    def set_index(self) -> Mapping[str, IntervalList]:
        """The compiled set lookups — read-only, and the map evaluation walks."""
        return MappingProxyType(self._index)

    def cells(self) -> tuple[Cell, ...]:
        """Every durable cell of this table — the image a successor would carry."""
        chains = [self._chains[path] for path in sorted(self._chains)]
        rules = [self._rules[path] for path in sorted(self._rules)]
        sets = [self._sets[path] for path in sorted(self._sets)]
        counters = [self._counters[path] for path in sorted(self._counters)]
        return (self._table, *chains, *rules, *sets, *counters)

    def paths(self) -> frozenset[str]:
        """Every durable cell path of this table."""
        return frozenset([self._table.path, *self._chains, *self._rules, *self._sets, *self._counters])

    # ---- authoring ----

    def add_base_chain(
        self,
        path: str,
        *,
        hook: Hook,
        priority: int = 0,
        policy: Verdict = Verdict.DROP,
        type: ChainType = ChainType.FILTER,
    ) -> Chain:
        """A chain attached at a hook (§6's packet path): its `policy` is the
        verdict its rules never reach — accept or drop, nothing else (§10)."""
        if policy not in (Verdict.ACCEPT, Verdict.DROP):
            raise InvalidPolicy(f"{policy.value!r} is not a base chain policy; accept or drop")
        chain = self._author_chain(path, hook=hook, priority=priority, type=type, policy=policy)
        self._bases.setdefault(hook, []).append(path)
        return chain

    def add_regular_chain(self, path: str, *, hook: Hook) -> Chain:
        """A regular chain: reached by jump only, its run-out returns to the caller.

        The landed `Chain.hook` cannot say "no hook" — a regular chain is anchored
        at the hook its callers traverse and is never attached there (a named
        schema seam request: `Hook | None`).
        """
        return self._author_chain(
            path, hook=hook, priority=0, type=ChainType.FILTER, policy=Verdict.RETURN
        )

    def _author_chain(
        self, path: str, *, hook: Hook, priority: int, type: ChainType, policy: Verdict
    ) -> Chain:
        self._refuse_taken(path)
        chain = Chain(path, self.family, hook, priority, type, policy, ())
        self._chains[path] = chain
        self._table = Table(self.path, self.family, (*self._table.chains, path))
        self._jumps.setdefault(path, set())
        return chain

    def add_rule(
        self,
        chain: str,
        matches: Iterable[Match] = (),
        verdict: Verdict = Verdict.DROP,
        *,
        jump: str | None = None,
        counter: str | None = None,
        log: bool = False,
    ) -> Rule:
        """One ordered rule cell, appended at the chain's end.

        The rule's path is `<chain>/rules/<n>` and its counter cell is authored at
        `Rule.counter` (default `<rule>/counter`, shareable by name). Jump targets
        must exist and must not close a cycle — refused here, typed, never during
        evaluation. A matched rule's verdict decides: `accept`, `drop`, `queue`,
        `reject` are terminal; `jump` runs its target and **returns to the next
        rule** of this chain; `return` pops to the caller, and at base chain depth
        (no caller) it is the continue of §10 — the next rule.
        """
        chain_cell = self.get_chain(chain)
        rule_matches = tuple(matches)
        rulemod.check_matches(rule_matches, has_set=self._sets.__contains__)
        rulemod.check_verdict(
            verdict,
            jump,
            source=chain,
            edges=self._jumps,
            has_chain=self._chains.__contains__,
        )
        rule_path = f"{chain}/rules/{len(chain_cell.rules)}"
        counter_path = counter or rule_counter_path(rule_path)
        rule = Rule(rule_path, rule_matches, verdict, jump, counter_path, log)
        self._rules[rule_path] = rule
        if counter_path not in self._counters:
            self._counters[counter_path] = Counter(counter_path, 0, 0)
        self._chains[chain] = Chain(
            chain_cell.path,
            chain_cell.family,
            chain_cell.hook,
            chain_cell.priority,
            chain_cell.type,
            chain_cell.policy,
            (*chain_cell.rules, rule_path),
        )
        if jump is not None:
            self._jumps[chain].add(jump)
        return rule

    def add_set(
        self, path: str, kind: str, members: Iterable[Member] = ()
    ) -> Set:
        """One named interval set (§10): its members are closed ranges (a port
        range, a prefix compiled by `prefix_interval`) or exact values, and its
        counter cell is authored at `<set path>/counter`."""
        if kind not in SET_KINDS:
            raise NotSupported(f"{kind!r} is outside this slice's interval kinds: {sorted(SET_KINDS)}")
        self._refuse_taken(path)
        cell_members = tuple(members)
        for member in cell_members:
            matchmod.check_member(kind, member)
        cell = Set(path, self.family, kind, cell_members)
        self._sets[path] = cell
        self._index[path] = compile_set(cell)
        self._counters.setdefault(set_counter_path(path), Counter(set_counter_path(path), 0, 0))
        return cell

    def add_members(self, path: str, members: Iterable[Member]) -> Set:
        """Append members to a set — the set cell is superseded with the wider
        member tuple; nothing is deleted and nothing is rewritten in place."""
        cell = self.get_set(path)
        grown = tuple(members)
        for member in grown:
            matchmod.check_member(cell.kind, member)
        superseded = Set(cell.path, cell.family, cell.kind, (*cell.members, *grown))
        self._sets[path] = superseded
        self._index[path] = compile_set(superseded)
        return superseded

    def write_counter(self, delta: CounterDelta) -> Counter:
        """The caller's authored counter write: a delta becomes a new counter cell."""
        counter = self.get_counter(delta.path)
        written = Counter(
            counter.path, counter.packets + delta.packets, counter.bytes + delta.bytes
        )
        self._counters[written.path] = written
        return written

    def _refuse_taken(self, path: str) -> None:
        taken = path in self._chains or path in self._rules or path in self._sets
        if taken or path in self._counters or path == self._table.path:
            raise DuplicateCell(f"{path!r} is already a cell of this table")


class ConntrackPlane:
    """The transient conntrack plane (§14): `Conntrack` cells that never land in
    the durable plane and never appear in a successor."""

    def __init__(self, *, ttl: int = 30_000) -> None:
        self._ttl = ttl
        self._entries: dict[str, Conntrack] = {}

    def observe(self, packet: matchmod.Packet, *, now: int = 0) -> Conntrack:
        """The entry for this packet's flow — its tuple or its reverse is one flow.

        A known entry answers from its cell: the established fast path, no
        re-derivation and no second entry. An unknown tuple authors a `NEW` entry;
        an entry the plane holds answers whatever its state is (an `INVALID` entry
        stays invalid until it ages out).
        """
        flow = matchmod.conn_tuple(packet)
        found = self._find(flow)
        if found is not None:
            return found
        entry = Conntrack(_entry_path(flow), flow, ConnState.NEW, now + self._ttl)
        self._entries[entry.path] = entry
        return entry

    def transition(self, flow: ConnTuple | matchmod.Packet, state: ConnState, *, now: int = 0) -> Conntrack:
        """Author one state step for a flow's entry — only the word set moves (§14):
        `ESTABLISHED` never goes back to `NEW`, `INVALID` never leaves itself."""
        entry = self._entry_for(flow)
        if state not in _ALLOWED_TRANSITIONS[entry.state]:
            raise InvalidTransition(
                f"conntrack {entry.state.value!r} never steps to {state.value!r}"
            )
        moved = Conntrack(entry.path, entry.tuple, state, now + self._ttl)
        self._entries[moved.path] = moved
        return moved

    def expire(self, now: int) -> tuple[Conntrack, ...]:
        """Age out what has expired — transient state is dropped, never superseded
        into history. Returns the entries that went."""
        gone = tuple(
            entry for entry in sorted(self._entries.values(), key=lambda e: e.path)
            if entry.expires <= now
        )
        for entry in gone:
            del self._entries[entry.path]
        return gone

    def entries(self) -> tuple[Conntrack, ...]:
        """Every live entry — transient cells, never durable ones."""
        return tuple(sorted(self._entries.values(), key=lambda e: e.path))

    def _find(self, flow: ConnTuple) -> Conntrack | None:
        entry = self._entries.get(_entry_path(flow))
        if entry is None:
            entry = self._entries.get(_entry_path(_reverse(flow)))
        return entry

    def _entry_for(self, flow: ConnTuple | matchmod.Packet) -> Conntrack:
        key = flow if isinstance(flow, ConnTuple) else matchmod.conn_tuple(flow)
        entry = self._find(key)
        if entry is None:
            raise NoSuchCell(f"{_entry_path(key)!r} is not an entry of this plane")
        return entry


def _reverse(flow: ConnTuple) -> ConnTuple:
    return ConnTuple(flow.proto, flow.dst, flow.src)


def _entry_path(flow: ConnTuple) -> str:
    src = f"{flow.src.ip.hex()}:{flow.src.port}"
    dst = f"{flow.dst.ip.hex()}:{flow.dst.port}"
    return f"net/ct/{flow.proto}-{src}-{dst}"
