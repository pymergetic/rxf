"""The firewall plane of the network orchestration layer (docs/25 §6, §10).

Tables contain chains, chains contain ordered rules, rules match the packet's
fields and carry a verdict, named sets hold interval members, and every rule and
set owns a counter — all of it graph cells (`pymergetic.rxftk.net.schema`), all of it
changing as authored writes, with evaluation a pure function over the cells.

The pieces: `match` is the packet's field vocabulary and the interval-set lookup;
`rule` is the verdict vocabulary and the loop-free jump graph (a jump cycle is
refused when the rule is authored, typed); `table` is the durable filter table and
the transient conntrack plane (conntrack never lands in a successor, §14);
`engine` is `evaluate(table, packet) -> Decision` — the verdict, the traversed
rule path, and the counter deltas the caller authors.
"""

from .engine import Decision, evaluate
from .match import (
    IntervalList,
    Packet,
    check_match,
    check_member,
    compile_set,
    conn_tuple,
    matches,
    prefix_interval,
)
from .rule import (
    InvalidRule,
    JumpCycle,
    NoSuchChain,
    NoSuchSet,
    check_jumps,
    check_matches,
    check_verdict,
    reaches,
)
from .table import (
    SET_KINDS,
    Cell,
    ConntrackPlane,
    CounterDelta,
    DuplicateCell,
    FilterTable,
    InvalidPolicy,
    InvalidTransition,
    NoSuchCell,
    rule_counter_path,
    set_counter_path,
)

__all__ = [
    "SET_KINDS",
    "Cell",
    "ConntrackPlane",
    "CounterDelta",
    "Decision",
    "DuplicateCell",
    "FilterTable",
    "IntervalList",
    "InvalidPolicy",
    "InvalidRule",
    "InvalidTransition",
    "JumpCycle",
    "NoSuchCell",
    "NoSuchChain",
    "NoSuchSet",
    "Packet",
    "check_jumps",
    "check_match",
    "check_matches",
    "check_member",
    "check_verdict",
    "compile_set",
    "conn_tuple",
    "evaluate",
    "matches",
    "prefix_interval",
    "reaches",
    "rule_counter_path",
    "set_counter_path",
]
