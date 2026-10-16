"""Identity, authorship, and change attribution (docs/24 §9): authorship is personal.

The atoms of authorship are persons and agents — a group does not think, it is a
*context*. An ``AUTHORED_BY`` line points at a ``PERSON`` or ``AGENT`` identity and
carries ``at``/``role``/``order``/``weight`` words; a collective's credit is a **derived
view** over interval-valued ``MEMBER_OF`` lines (the members who authored, holding
membership at that time) and is never stored — not even for anonymous joint work, where
the honest representation is the absence of authorship lines. What is never produced is
a claim that launders responsibility into a group name.

The two attributions stay apart. Intellectual authorship is a *claim* carried by an
``AUTHORED_BY`` line with its provenance tier; change attribution is the factual
``by``/``at``/``why`` of the write that produced a delta. ``Change`` is a view over that
chain, never a store.

What lives here is the pure vocabulary of the layer: kind predicates, the fact words and
their codecs, and the write gates the store enforces before any relation cell exists.
"""

from __future__ import annotations

from .schema import (
    KEY_AT,
    KEY_BY,
    KEY_NAME,
    KEY_WHY,
    WORD_BYTES,
    Change,
    Fact,
    Identity,
    IdentityId,
    IdentityKind,
    Link,
    MemoryId,
    MemoryRefusal,
    RelationKind,
    Time,
    pack_word,
    unpack_word,
)

# The change attribution words of a delta (docs/24 §9): by, at, and an optional why.
CHANGE_KEYS = (KEY_BY, KEY_AT, KEY_WHY)


def is_personal(kind: IdentityKind) -> bool:
    """The authorship atoms: a person or an agent — the only identities a thought can
    come from."""
    return kind in (IdentityKind.PERSON, IdentityKind.AGENT)


def is_group(kind: IdentityKind) -> bool:
    """A team, company, or collective — a context that derives its credit, never authors
    and never writes a byte."""
    return kind in (IdentityKind.TEAM, IdentityKind.COMPANY, IdentityKind.COLLECTIVE)


def by_fact(by: IdentityId) -> Fact:
    """The word naming who physically wrote the delta."""
    return Fact(KEY_BY, pack_word(by))


def at_fact(at: Time) -> Fact:
    """The word naming when the delta was written."""
    return Fact(KEY_AT, pack_word(at))


def why_fact(why: bytes) -> Fact:
    """The optional word naming why the delta was written."""
    return Fact(KEY_WHY, why)


def name_fact(name: str, at: Time) -> Fact:
    """A display name with the word it became valid — a rename is a new fact appended to
    the identity's history, never an overwrite of the old name."""
    return Fact(KEY_NAME, pack_word(at) + name.encode("utf-8"))


def fact_of(facts: tuple[Fact, ...], key: str) -> Fact | None:
    """The word carried under `key`, or None if the cell does not carry it."""
    for fact in reversed(facts):
        if fact.key == key:
            return fact
    return None


def by_of(facts: tuple[Fact, ...]) -> IdentityId | None:
    """Who the delta words name as its writer."""
    fact = fact_of(facts, KEY_BY)
    return None if fact is None else unpack_word(fact.value)


def at_of(facts: tuple[Fact, ...]) -> Time | None:
    """When the delta words say it was written."""
    fact = fact_of(facts, KEY_AT)
    return None if fact is None else unpack_word(fact.value)


def why_of(facts: tuple[Fact, ...]) -> bytes | None:
    """The why word of the delta, if it carries one."""
    fact = fact_of(facts, KEY_WHY)
    return None if fact is None else fact.value


def name_of(fact: Fact) -> tuple[Time, str]:
    """A name history word back into the name and the word it became valid at."""
    if fact.key != KEY_NAME or len(fact.value) < WORD_BYTES:
        raise MemoryRefusal("a name word carries its time and the name: this word carries neither")
    at = unpack_word(fact.value[:WORD_BYTES])
    return at, fact.value[WORD_BYTES:].decode("utf-8")


def author_time(line: Link) -> Time:
    """When an authorship claim was made: its `at` word if it carries one, else the word
    its interval opens at."""
    at = at_of(line.facts)
    return line.valid.valid_from if at is None else at


def change_of(id: MemoryId, subject: MemoryId, step: Link) -> Change:
    """The Change view of one supersession step, decoded from the step cell's words.

    Attribution is a fact, not a claim: a step that carries no `by` word has no honest
    writer to name, and the view refuses to invent one.
    """
    by = by_of(step.facts)
    if by is None:
        raise MemoryRefusal(f"the step cell {step.id} carries no by word: a change names who wrote it")
    at = at_of(step.facts)
    return Change(id=id, subject=subject, by=by, at=step.valid.valid_from if at is None else at, why=why_of(step.facts))


def gate_writer(who: Identity) -> None:
    """A delta's `by` is a person, an agent, or the machinery itself.

    A collective never wrote a byte: "the group made this change" is derived from
    membership intervals, never stored (docs/24 §9).
    """
    if not (is_personal(who.kind) or who.kind is IdentityKind.SYSTEM):
        raise MemoryRefusal(
            f"a delta's by is a person, an agent, or the machinery: {who.kind.value} wrote nothing"
        )


def gate_relation(kind: RelationKind, src: object, dst: object) -> None:
    """The write gates of the identity layer (docs/24 §9), applied before a cell exists.

    Authorship is personal, always: an ``AUTHORED_BY`` target is a ``PERSON`` or ``AGENT``
    cell and a collective is refused at the write. Membership runs person/agent to
    collective/team/company; an agent is operated by an identity; a voice is personal;
    a reviewer is a person or an agent.
    """
    if kind is RelationKind.AUTHORED_BY:
        author = _as_identity(dst, kind)
        if not is_personal(author.kind):
            raise MemoryRefusal(f"authorship is personal: a {author.kind.value} cannot author a thought")
        return
    if kind is RelationKind.MEMBER_OF:
        _require_personal(_as_identity(src, kind), kind)
        _require_group(_as_identity(dst, kind), kind)
        return
    if kind is RelationKind.OPERATED_BY:
        agent = _as_identity(src, kind)
        if agent.kind is not IdentityKind.AGENT:
            raise MemoryRefusal(f"{kind.value} runs from an AGENT identity to whoever runs it")
        _as_identity(dst, kind)
        return
    if kind is RelationKind.SPEAKS_FOR:
        _require_personal(_as_identity(src, kind), kind)
        _as_identity(dst, kind)
        return
    if kind is RelationKind.REVIEWED_BY:
        _require_personal(_as_identity(dst, kind), kind)
        return


def _as_identity(cell: object, kind: RelationKind) -> Identity:
    if not isinstance(cell, Identity):
        raise MemoryRefusal(f"{kind.value} relates identities: this cell is not one")
    return cell


def _require_personal(who: Identity, kind: RelationKind) -> None:
    if not is_personal(who.kind):
        raise MemoryRefusal(f"{kind.value} is personal: a {who.kind.value} identity is refused")


def _require_group(who: Identity, kind: RelationKind) -> None:
    if not is_group(who.kind):
        raise MemoryRefusal(f"{kind.value} lands in a team, company, or collective: not in a {who.kind.value}")
