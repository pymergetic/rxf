"""Linked AI memories as graph cells (docs/24): one memory, one cell, nothing destroyed.

The memory system is a vocabulary on the cells that already exist — no new engine, no
second graph. A memory is a cell, a link is a reified edge cell that carries its facts,
an identity is a cell (a person, a team, a company, or an AI agent is an author exactly
like a user), and remembering is an action with a trace, not a dictionary lookup.

The programs are the faces of docs/24 §4::

    remember(kind, content, anchors=...)  -> Memory    a new node
    link(src, dst, kind, facts=...)       -> Link      a reified edge cell
    recall(query, temporal=, around=)     -> Recall    scores *and* trace cells
    consolidate(session)                  -> memories  working -> episodic -> semantic
    decay()                               -> None      salience from traces; closes intervals
    supersede(old, new)                   -> Link      SUPERSEDES + delta compose

And the honest refusals of §7: no embeddings in the core, no destructive forgetting
(intervals close; the cell stays), no float scores, and no second storage engine — the
graph is the store. Authorship is personal (§9): an ``AUTHORED_BY`` line targets a
person or an agent, and a collective's credit is a derived view over ``MEMBER_OF``
intervals, never a stored claim.

This is the Python face; the C face of docs/24 §4 is ``pm_memory_*`` (a later lane, and
not the ``util.mem`` arena — that card is the arena, a different thing).
"""

from __future__ import annotations

from .identity import (
    at_fact,
    at_of,
    author_time,
    by_fact,
    by_of,
    change_of,
    fact_of,
    is_group,
    is_personal,
    name_fact,
    name_of,
    why_fact,
    why_of,
)
from .schema import (
    SCORE_MAX,
    SCORE_MIN,
    AgentSpec,
    Anchor,
    Cell,
    Change,
    Fact,
    Hit,
    Identity,
    IdentityId,
    IdentityKind,
    Interval,
    Link,
    LinkKind,
    Memory,
    MemoryId,
    MemoryKind,
    MemoryRefusal,
    Provenance,
    Recall,
    RelationKind,
    Score,
    Time,
    Trace,
    pack_word,
    provenance_of,
    score_add,
    score_bytes,
    score_from_bytes,
    score_halve,
    score_mul,
    score_of,
    unpack_word,
)
from .store import HALF_WEIGHT, MemoryStore

__all__ = [
    "HALF_WEIGHT",
    "SCORE_MAX",
    "SCORE_MIN",
    "AgentSpec",
    "Anchor",
    "Cell",
    "Change",
    "Fact",
    "Hit",
    "Identity",
    "IdentityId",
    "IdentityKind",
    "Interval",
    "Link",
    "LinkKind",
    "Memory",
    "MemoryId",
    "MemoryKind",
    "MemoryRefusal",
    "MemoryStore",
    "Provenance",
    "Recall",
    "RelationKind",
    "Score",
    "Time",
    "Trace",
    "at_fact",
    "at_of",
    "author_time",
    "by_fact",
    "by_of",
    "change_of",
    "fact_of",
    "is_group",
    "is_personal",
    "name_fact",
    "name_of",
    "pack_word",
    "provenance_of",
    "score_add",
    "score_bytes",
    "score_from_bytes",
    "score_halve",
    "score_mul",
    "score_of",
    "unpack_word",
    "why_fact",
    "why_of",
]
