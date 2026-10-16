"""Authorship is personal, always (docs/24 §9).

A collective never authors: at the write it is refused, and as an attribution it is a
derived view over interval-valued MEMBER_OF lines. The two attributions stay apart —
authorship is a claim carried by a line, change attribution is the factual by/at/why of
the delta — and a change is never written to a group name either.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.memory import (
    AgentSpec,
    Fact,
    IdentityKind,
    Interval,
    MemoryKind,
    MemoryRefusal,
    MemoryStore,
    RelationKind,
    at_fact,
    author_time,
    by_of,
    fact_of,
    name_of,
    why_fact,
)


def fresh() -> MemoryStore:
    return MemoryStore()


def word(facts: tuple[Fact, ...], key: str) -> bytes:
    fact = fact_of(facts, key)
    assert fact is not None
    return fact.value


def test_an_authorship_line_targets_a_person_or_an_agent():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    clerk = store.identity(
        IdentityKind.AGENT, ("clerk",), at=0, spec=AgentSpec("model-x", "1", operator=ada.id)
    )
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5)
    for author in (ada, clerk):
        line = store.link(thought.id, author.id, RelationKind.AUTHORED_BY, at=5)
        assert line.dst == author.id
    assert len(store.links(RelationKind.AUTHORED_BY)) == 2  # an agent is an author like a user


def test_a_collective_author_is_refused_at_the_write():
    store = fresh()
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    team = store.identity(IdentityKind.TEAM, ("the lab",), at=0)
    company = store.identity(IdentityKind.COMPANY, ("the firm",), at=0)
    machinery = store.identity(IdentityKind.SYSTEM, ("the machinery",), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5)
    for wrong in (group, team, company, machinery):
        before = store.cell_count
        with pytest.raises(MemoryRefusal, match="authorship is personal"):
            store.link(thought.id, wrong.id, RelationKind.AUTHORED_BY)
        assert store.cell_count == before  # refused before any cell exists
    assert store.links(RelationKind.AUTHORED_BY) == ()  # and no claim was manufactured


def test_the_collective_credit_is_a_derived_view_over_membership_intervals():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    clerk = store.identity(
        IdentityKind.AGENT, ("clerk",), at=0, spec=AgentSpec("model-x", "1", operator=ada.id)
    )
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    store.link(ada.id, group.id, RelationKind.MEMBER_OF, valid=Interval(0, 10), at=0)
    store.link(clerk.id, group.id, RelationKind.MEMBER_OF, valid=Interval(4, 8), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5)
    store.link(thought.id, ada.id, RelationKind.AUTHORED_BY, facts=(at_fact(5),), at=5)
    store.link(thought.id, clerk.id, RelationKind.AUTHORED_BY, facts=(at_fact(6),), at=6)

    before = store.cell_count
    credit = store.collective_authors(thought.id, group.id)
    assert credit == (ada.id, clerk.id)  # the members who authored, members at that time
    assert store.cell_count == before  # the view derives and stores nothing

    # the same author, claimed outside her membership window: no collective credit
    later = store.remember(MemoryKind.SEMANTIC, b"a later idea", at=11)
    store.link(later.id, ada.id, RelationKind.AUTHORED_BY, facts=(at_fact(11),), at=11)
    assert store.collective_authors(later.id, group.id) == ()

    # and never a stored collective authorship claim, not even one laundered line
    assert all(line.dst != group.id for line in store.links(RelationKind.AUTHORED_BY))


def test_anonymous_joint_work_stays_an_absence():
    store = fresh()
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"unsigned", at=5)
    assert store.collective_authors(thought.id, group.id) == ()  # absence, not a group claim


def test_author_time_reads_the_at_word_of_the_claim():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5)
    line = store.link(thought.id, ada.id, RelationKind.AUTHORED_BY, facts=(at_fact(7),), at=5)
    assert author_time(line) == 7  # the claim's word, not the write's word


def test_change_attribution_is_a_fact_not_a_claim():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    old = store.remember(MemoryKind.SEMANTIC, b"first", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"second", at=5)
    store.supersede(old.id, new.id, by=ada.id, at=5, why=b"it was wrong")
    change = store.changes(old.id)[0]
    assert change.by == ada.id
    assert change.at == 5
    assert change.why == b"it was wrong"
    # the two attributions stay apart: this write is not an authorship claim
    assert store.links(RelationKind.AUTHORED_BY) == ()


def test_a_delta_by_is_never_a_collective():
    store = fresh()
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    team = store.identity(IdentityKind.TEAM, ("the lab",), at=0)
    old = store.remember(MemoryKind.SEMANTIC, b"first", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"second", at=5)
    for wrong in (group, team):
        before = store.cell_count
        with pytest.raises(MemoryRefusal, match="wrote nothing"):
            store.supersede(old.id, new.id, by=wrong.id, at=5)
        assert store.cell_count == before
    assert store.memory(old.id).valid == Interval(0)  # nothing closed on a refused write
    with pytest.raises(MemoryRefusal, match="wrote nothing"):
        store.remember(MemoryKind.SEMANTIC, b"third", by=group.id)


def test_the_group_change_view_derives_from_membership_intervals():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    outsider = store.identity(IdentityKind.PERSON, ("Grace",), at=0)
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    store.link(ada.id, group.id, RelationKind.MEMBER_OF, valid=Interval(0, 10), at=0)
    old = store.remember(MemoryKind.SEMANTIC, b"first", at=0)
    middle = store.remember(MemoryKind.SEMANTIC, b"second", at=5)
    new = store.remember(MemoryKind.SEMANTIC, b"third", at=15)
    store.supersede(old.id, middle.id, by=ada.id, at=5)  # ada writes inside her window
    store.supersede(middle.id, new.id, by=outsider.id, at=15)  # grace was never a member
    assert store.collective_writers(old.id, group.id) == (ada.id,)


def test_a_rename_appends_a_name_fact_and_never_overwrites():
    store = fresh()
    who = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    renamed = store.rename(who.id, "Ada Lovelace", at=5)
    assert renamed.names == ("Ada", "Ada Lovelace")
    names = [name_of(fact) for fact in renamed.facts if fact.key == "name"]
    assert names == [(0, "Ada"), (5, "Ada Lovelace")]  # history in facts, both words intact
    assert store.identity_of(who.id).names == ("Ada", "Ada Lovelace")


def test_an_agent_identity_carries_its_spec_and_its_operator():
    store = fresh()
    operator = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    with pytest.raises(MemoryRefusal, match="carries its AgentSpec"):
        store.identity(IdentityKind.AGENT, ("clerk",), at=0)
    with pytest.raises(MemoryRefusal, match="not of a person"):
        store.identity(IdentityKind.PERSON, ("Ada 2",), at=0, spec=AgentSpec("m", "1", 0))
    clerk = store.identity(
        IdentityKind.AGENT, ("clerk",), at=0, spec=AgentSpec("model-x", "1", operator=operator.id)
    )
    assert clerk.spec is not None
    assert clerk.spec.operator == operator.id
    lines = store.links(RelationKind.OPERATED_BY, src=clerk.id)
    assert [line.dst for line in lines] == [operator.id]


def test_membership_runs_from_persons_and_agents_to_groups():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    company = store.identity(IdentityKind.COMPANY, ("the firm",), at=0)
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    store.link(ada.id, company.id, RelationKind.MEMBER_OF, at=0)  # person -> company
    # a member is personal and a landing is a group — both directions of the rule
    with pytest.raises(MemoryRefusal, match="is personal"):
        store.link(company.id, group.id, RelationKind.MEMBER_OF, at=0)
    with pytest.raises(MemoryRefusal, match="lands in a team"):
        store.link(ada.id, ada.id, RelationKind.MEMBER_OF, at=0)


def test_a_reviewer_is_a_person_or_an_agent():
    store = fresh()
    group = store.identity(IdentityKind.COLLECTIVE, ("the workshop",), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5)
    with pytest.raises(MemoryRefusal, match="is personal"):
        store.link(thought.id, group.id, RelationKind.REVIEWED_BY, at=5)


def test_the_fact_words_of_attribution_are_typed():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    thought = store.remember(MemoryKind.SEMANTIC, b"an idea", at=5, by=ada.id, why=b"because")
    assert word(thought.facts, "why") == b"because"
    assert by_of(thought.facts) == ada.id
    claim = store.link(
        thought.id, ada.id, RelationKind.AUTHORED_BY, facts=(at_fact(9), why_fact(b"claimed")), at=9
    )
    assert word(claim.facts, "why") == b"claimed"
    assert author_time(claim) == 9
