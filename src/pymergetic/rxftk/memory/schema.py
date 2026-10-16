"""The memory type register (docs/24 §2 and §9): one memory, one cell, one interval.

The memory system is a vocabulary on the cells that already exist, not a second engine:
a memory is a cell, a link is a reified edge cell that carries its facts, an identity is
a cell, and a recall writes trace cells. What lives here is the register itself -- frozen
records with full annotations -- and the words are exactly the words of docs/24: ``Memory``
(the node), ``Link`` (the line), ``Fact`` (a typed word payload), ``Anchor`` (what a memory
hangs on), ``Interval`` (the one validity concept), ``Trace`` and ``Recall`` (remembering
is an action with a record), and the identity layer of §9: ``Identity``, ``AgentSpec``,
``Change``.

Two fills beyond the sketch of §2, both demanded by §6/§9 and marked here:

- a memory cell carries words too (``Memory.facts``): §6 needs a salience fact on the
  memory and §9 gives every delta cell ``by``/``at``/``why``. The link stays the primary
  fact bearer (§3); the cell words are the same ``Fact`` register.
- one ``Link`` type carries both kind vocabularies (``LinkKind`` §2, ``RelationKind`` §9):
  both are edge cells, so both ride the one reified line.

``Score`` is fixed point 0..2**32-1 and ``Time`` is a 64-bit value word (docs/23 §6 q1).
There are no floats in the core and there is no second interval: a memory, a link, and a
relation all hold the same ``Interval``, closed at both words when it ends.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

MemoryId = int  # cell object id (i2 form: one memory = one cell)
IdentityId = int  # an identity IS a cell (docs/24 §9)
Time = int  # 64-bit value word (the dual-width policy, docs/23 §6 q1)
Score = int  # fixed point 0..2**32-1 — no floats in the core

SCORE_MIN: Final = 0
SCORE_MAX: Final = 2**32 - 1
WORD_BYTES: Final = 8  # the 64-bit value word, little-endian in a fact payload
SCORE_BYTES: Final = 4  # the 32-bit score word, little-endian in a fact payload

# The fact keys of the register (schema evolution for keys is open in docs/24 §10;
# until it lands, these words are the vocabulary and nothing else is invented).
KEY_AT = "at"
KEY_BY = "by"
KEY_WHY = "why"
KEY_NAME = "name"
KEY_ROLE = "role"
KEY_ORDER = "order"
KEY_WEIGHT = "weight"
KEY_SALIENCE = "salience"


class MemoryRefusal(ValueError):
    """An honest refusal: the memory programs never guess and never destroy."""


class Provenance(StrEnum):
    """The provenance tier a memory or identity is claimed at (docs/22 k1 floor)."""

    FULL = "full"
    CITE = "cite"
    HASH = "hash"


class MemoryKind(StrEnum):
    """What a memory is: the register of docs/24 §2."""

    WORKING = "working"  # context of the current run (S3 arena)
    EPISODIC = "episodic"  # what happened
    SEMANTIC = "semantic"  # what holds
    PROCEDURAL = "procedural"  # how it is done
    AFFECTIVE = "affective"  # how it was (an evaluation)


class LinkKind(StrEnum):
    """How two cells relate on a line: the register of docs/24 §2."""

    ABOUT = "about"
    PART_OF = "part_of"
    BEFORE = "before"  # temporal
    CAUSED_BY = "caused_by"  # causal
    SUPPORTS = "supports"  # epistemic
    CONTRADICTS = "contradicts"
    SUPERSEDES = "supersedes"  # replacement, not deletion
    DERIVED_FROM = "derived_from"  # consolidation
    GROUNDED_IN = "grounded_in"  # anchor to the system
    SIMILAR_TO = "similar_to"
    RETRIEVED_WITH = "retrieved_with"  # co-recall (associative)


class IdentityKind(StrEnum):
    """Who is a node of the graph (docs/24 §9): people, groups, agents, machinery."""

    PERSON = "person"
    TEAM = "team"
    COMPANY = "company"
    COLLECTIVE = "collective"  # an ad-hoc coauthorship group: humans and/or agents
    AGENT = "agent"  # an AI author, exactly like a user
    SYSTEM = "system"  # the machinery itself (consolidate, decay write too)


class RelationKind(StrEnum):
    """Who stands in what relation to what (docs/24 §9)."""

    AUTHORED_BY = "authored_by"  # persons/agents only — a thought/change came from
    MEMBER_OF = "member_of"  # person/agent -> collective/team -> company
    OPERATED_BY = "operated_by"  # agent -> whoever runs it
    SPEAKS_FOR = "speaks_for"  # delegation of voice
    REVIEWED_BY = "reviewed_by"


def score_of(value: int) -> Score:
    """A checked score word: fixed point 0..2**32-1, integer arithmetic only.

    A float is refused outright -- even an integral one -- and so is a bool, a string, or
    any value outside the word. This is the only way a score enters the core.
    """
    if type(value) is not int:
        raise TypeError(f"a score word is an integer, not {type(value).__name__}")
    if not SCORE_MIN <= value <= SCORE_MAX:
        raise MemoryRefusal(f"a score word is fixed point 0..{SCORE_MAX}: {value} is out of range")
    return value


def score_add(a: Score, b: Score) -> Score:
    """Saturating fixed-point sum: clamps at the top of the word, never wraps."""
    return min(SCORE_MAX, score_of(a) + score_of(b))


def score_mul(a: Score, b: Score) -> Score:
    """Fixed-point product of two Q0.32 words (2**32-1 is full weight), rounded to nearest."""
    return min(SCORE_MAX, ((score_of(a) * score_of(b)) + (1 << 31)) >> 32)


def score_halve(value: Score) -> Score:
    """The decay step: a floor-halving that reaches zero.

    Round-nearest halving would stick at one forever and nothing would ever be
    forgotten; this floors, so a salience without traces reaches zero and closes.
    """
    return score_of(value) // 2


def score_bytes(value: Score) -> bytes:
    """The typed word payload of a score fact: four little-endian bytes."""
    return score_of(value).to_bytes(SCORE_BYTES, "little")


def score_from_bytes(payload: bytes) -> Score:
    """A score word read back from its payload -- refuses a payload of the wrong width."""
    if len(payload) != SCORE_BYTES:
        raise MemoryRefusal(f"a score word is {SCORE_BYTES} bytes: {len(payload)} is not one")
    return score_of(int.from_bytes(payload, "little"))


def pack_word(value: int) -> bytes:
    """A 64-bit value word as its typed payload: eight little-endian bytes."""
    if type(value) is not int:
        raise TypeError(f"a value word is an integer, not {type(value).__name__}")
    if not 0 <= value < 1 << 64:
        raise MemoryRefusal(f"a value word is 64 bits: {value} is out of range")
    return value.to_bytes(WORD_BYTES, "little")


def unpack_word(payload: bytes) -> int:
    """A 64-bit value word read back from its payload."""
    if len(payload) != WORD_BYTES:
        raise MemoryRefusal(f"a value word is {WORD_BYTES} bytes: {len(payload)} is not one")
    return int.from_bytes(payload, "little")


def provenance_of(word: str) -> Provenance:
    """The tier behind a provenance word (docs/22): `full`, `cite`, or `hash`.

    The floor is hash: an anonymous word (`none`, `off`, ...) is not a smaller tier, it is
    no provenance at all, and it is refused.
    """
    try:
        return Provenance(word)
    except ValueError:
        pass
    if word in ("", "none", "off", "no", "anonymous"):
        raise MemoryRefusal("the provenance floor is hash: the memory system takes no anonymous input")
    raise MemoryRefusal(f"{word!r} is not a provenance tier (full, cite, or hash)")


@dataclass(frozen=True)
class Interval:
    """A validity interval over Time words: closed ``[valid_from, valid_to]``.

    This is the one interval concept of the memory system -- a memory, a link, and a
    relation all hold one. ``valid_to = None`` means the interval holds until now; when
    forgetting or supersession ends it, ``close`` fills the end and the interval is closed
    at both words. Closing rewrites no history: what is closed stays closed.
    """

    valid_from: Time
    valid_to: Time | None = None

    def holds(self, at: Time) -> bool:
        """Whether the interval covers `at` -- both ends of a closed interval included."""
        return self.valid_from <= at and (self.valid_to is None or at <= self.valid_to)

    @property
    def closed(self) -> bool:
        """Whether the interval has ended (its end word is filled)."""
        return self.valid_to is not None

    def close(self, at: Time) -> Interval:
        """The same interval closed at `at` -- refused before it opens or after it closed."""
        if self.valid_to is not None:
            raise MemoryRefusal(
                f"the interval already closed at {self.valid_to}: closing it again would rewrite history"
            )
        if at < self.valid_from:
            raise MemoryRefusal(f"the interval opens at {self.valid_from}: it never closes at {at}")
        return Interval(self.valid_from, at)

    def overlaps(self, other: Interval) -> bool:
        """Whether the two intervals share at least one Time word.

        An open end reaches past every word: it holds until now, and now is never before
        the query that asks about it.
        """
        return _le(self.valid_from, other.valid_to) and _le(other.valid_from, self.valid_to)


def _le(left: Time | None, right: Time | None) -> bool:
    """`left <= right` over value words where `None` is the open end (+inf)."""
    if right is None:
        return True
    if left is None:
        return False
    return left <= right


@dataclass(frozen=True)
class Fact:
    """A typed word payload on a cell (docs/23 §6): a key and its bytes.

    The link is the primary fact bearer; memory cells carry words too (salience of §6,
    delta attribution of §9). Writing a key again replaces its word; the `name` key is
    the one append-only history (a rename is a new fact, never an overwrite).
    """

    key: str
    value: bytes


@dataclass(frozen=True)
class Anchor:
    """What a memory hangs on (docs/22 form): a named cell, a `commit:path:lines` locator,
    and the provenance tier the anchor is claimed at."""

    cell: MemoryId | None
    locator: str | None
    tier: Provenance


@dataclass(frozen=True)
class Memory:
    """THE NODE: one memory is one cell, one identity, one id."""

    id: MemoryId
    kind: MemoryKind
    content: bytes  # the engram payload
    anchors: tuple[Anchor, ...]
    created: Time
    valid: Interval
    provenance: Provenance
    facts: tuple[Fact, ...] = ()  # cell words: salience (§6), by/at/why (§9)


@dataclass(frozen=True)
class Link:
    """THE LINE — itself a cell: a reified edge that carries its own facts.

    One link type, two kind vocabularies: `LinkKind` (§2) for memory structure and
    `RelationKind` (§9) for the identity layer. Both are edge cells, so both ride this
    one line and neither needs a side table.
    """

    id: MemoryId
    src: MemoryId
    dst: MemoryId
    kind: LinkKind | RelationKind
    facts: tuple[Fact, ...]
    valid: Interval


@dataclass(frozen=True)
class Trace:
    """The record of one recall hit — decay and salience read THIS (docs/24 §5).

    A trace is a run-cell trace (recall *is* a run); its cell id is the store's, and the
    record carries only what forgetting needs: which memory, when, what was asked, and
    the score it got.
    """

    memory: MemoryId
    at: Time
    query: bytes
    score: Score


@dataclass(frozen=True)
class Hit:
    """One scored result of a recall: a memory and its fixed-point score."""

    memory: MemoryId
    score: Score


@dataclass(frozen=True)
class Recall:
    """What a recall returns: the scores *and* the traces it wrote (docs/24 §5).

    Retrieval history is the input of forgetting, so a recall is never free of record.
    """

    query: bytes
    hits: tuple[Hit, ...]
    traces: tuple[Trace, ...]


@dataclass(frozen=True)
class AgentSpec:
    """The payload of an AGENT identity (docs/24 §9): what it is and who runs it."""

    model: str
    version: str
    operator: IdentityId  # who runs it (OPERATED_BY link)


@dataclass(frozen=True)
class Identity:
    """An identity is a cell (docs/24 §9): a person, team, company, collective, agent, or
    the machinery itself — one kind space, and an AI agent is an author exactly like a
    user. `names` is display names over time; the history lives in `name` facts."""

    id: IdentityId
    kind: IdentityKind
    names: tuple[str, ...]
    created: Time
    provenance: Provenance
    facts: tuple[Fact, ...] = ()  # cell words: the name history, delta attribution
    spec: AgentSpec | None = None  # the payload of an AGENT identity


@dataclass(frozen=True)
class Change:
    """A VIEW over the delta chain, not a new store (docs/24 §9).

    One supersession step: the successor cell, the cell it changed, and the factual
    `by`/`at`/`why` words of the write that produced it. Derivable things are views.
    """

    id: MemoryId  # the delta/successor cell
    subject: MemoryId  # what changed (a memory or a link)
    by: IdentityId  # who
    at: Time
    why: bytes | None  # a fact on the change


Cell = Memory | Link | Identity | Trace  # one cell world, one id space
