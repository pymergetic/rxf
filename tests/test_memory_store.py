"""The memory programs (docs/24 §4–§6): remember, link, recall, consolidate, decay.

Recall is an action with a record — every hit writes a Trace cell — and it has honest
temporal reach: a plain recall asks about now, a temporal recall reaches back into
closed intervals. Consolidation is the session's unit (working -> episodic -> semantic),
and decay takes salience from the traces and closes intervals at zero.
"""

from __future__ import annotations

import pytest

from pymergetic.rxf.memory import (
    HALF_WEIGHT,
    SCORE_MAX,
    Fact,
    Identity,
    IdentityKind,
    Interval,
    LinkKind,
    MemoryKind,
    MemoryRefusal,
    MemoryStore,
    Provenance,
    by_of,
    fact_of,
    score_from_bytes,
    score_halve,
)


def fresh() -> MemoryStore:
    return MemoryStore()


def person(store: MemoryStore, name: str) -> Identity:
    return store.identity(IdentityKind.PERSON, (name,), at=0)


def word(facts: tuple[Fact, ...], key: str) -> bytes:
    fact = fact_of(facts, key)
    assert fact is not None
    return fact.value


def test_remember_stamps_the_delta_words_and_seeds_salience():
    store = fresh()
    ada = person(store, "Ada")
    memory = store.remember(MemoryKind.EPISODIC, b"the punch cards ran", at=5, by=ada.id, why=b"first run")
    assert by_of(memory.facts) == ada.id
    assert word(memory.facts, "at") == (5).to_bytes(8, "little")
    assert word(memory.facts, "why") == b"first run"
    assert score_from_bytes(word(memory.facts, "salience")) == SCORE_MAX
    assert memory.valid == Interval(5)


def test_recall_returns_scores_and_writes_a_trace_per_hit():
    store = fresh()
    first = store.remember(MemoryKind.SEMANTIC, b"the answer", at=0)
    second = store.remember(MemoryKind.SEMANTIC, b"the answer", at=0)
    store.advance(3)
    before = store.cell_count
    recall = store.recall(b"the answer")
    assert [hit.memory for hit in recall.hits] == [first.id, second.id]
    assert all(hit.score == SCORE_MAX for hit in recall.hits)
    assert len(recall.traces) == 2  # retrieval history cannot be free of record
    assert store.traces() == recall.traces
    assert store.cell_count == before + 2  # one trace cell per hit, nothing else


def test_associative_recall_scores_below_their_source():
    store = fresh()
    anchor = store.remember(MemoryKind.SEMANTIC, b"root", at=0)
    near = store.remember(MemoryKind.SEMANTIC, b"near", at=0)
    far = store.remember(MemoryKind.SEMANTIC, b"far", at=0)
    store.link(anchor.id, near.id, LinkKind.ABOUT, at=0)
    store.link(near.id, far.id, LinkKind.ABOUT, at=0)
    store.advance(1)
    recall = store.recall(b"root")
    scores = {hit.memory: hit.score for hit in recall.hits}
    assert scores[anchor.id] == SCORE_MAX
    assert scores[near.id] == HALF_WEIGHT  # an edge without a weight word traverses at half
    assert 0 < scores[far.id] < scores[near.id] < scores[anchor.id]
    assert type(scores[far.id]) is int  # never a float, anywhere in a recall


def test_retrieved_with_makes_co_recall_first_class():
    store = fresh()
    left = store.remember(MemoryKind.EPISODIC, b"left", at=0)
    right = store.remember(MemoryKind.EPISODIC, b"right", at=0)
    store.link(left.id, right.id, LinkKind.RETRIEVED_WITH, at=0)
    store.advance(1)
    recall = store.recall(b"left")
    scores = {hit.memory: hit.score for hit in recall.hits}
    assert scores[left.id] == SCORE_MAX
    assert scores[right.id] == HALF_WEIGHT  # the partner of the co-recall surfaces


def test_temporal_reach_reads_closed_intervals_but_plain_recall_does_not():
    store = fresh()
    old = store.remember(MemoryKind.EPISODIC, b"an old belief", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"a new belief", at=10)
    store.supersede(old.id, new.id, at=10)
    store.advance(20)

    now = store.recall(b"an old belief")
    assert [hit.memory for hit in now.hits] == []  # it no longer holds now

    then = store.recall(b"an old belief", around=3)
    assert [hit.memory for hit in then.hits] == [old.id]  # the moment it held

    window = store.recall(b"an old belief", temporal=Interval(0, 5))
    assert [hit.memory for hit in window.hits] == [old.id]  # the interval it overlapped


def test_a_recall_names_one_temporal_shape():
    store = fresh()
    store.remember(MemoryKind.EPISODIC, b"x", at=0)
    with pytest.raises(MemoryRefusal, match="one temporal shape"):
        store.recall(b"x", temporal=Interval(0, 5), around=3)


def test_consolidation_promotes_working_memory_to_episodic_with_derived_from():
    store = fresh()
    session = store.remember(MemoryKind.WORKING, b"run 1", at=0)
    work = store.remember(MemoryKind.WORKING, b"context", at=1, session=session.id)
    created = store.consolidate(session.id, at=10)
    assert len(created) == 1
    episodic = created[0]
    assert episodic.kind is MemoryKind.EPISODIC
    assert episodic.content == b"context"
    lines = store.links(LinkKind.DERIVED_FROM)
    assert [(line.src, line.dst) for line in lines] == [(episodic.id, work.id)]
    source = store.memory(work.id)
    assert source.kind is MemoryKind.WORKING  # the working memory stays as it was
    assert not source.valid.closed
    # the machinery wrote the promotion: its delta words name the system identity
    writer_id = by_of(episodic.facts)
    assert writer_id is not None
    assert store.identity_of(writer_id).kind is IdentityKind.SYSTEM


def test_repeated_episodic_patterns_consolidate_to_semantic():
    store = fresh()
    session = store.remember(MemoryKind.WORKING, b"run", at=0)
    store.remember(
        MemoryKind.WORKING, b"pattern", at=1, session=session.id, provenance=Provenance.CITE
    )
    store.remember(
        MemoryKind.WORKING, b"pattern", at=2, session=session.id, provenance=Provenance.FULL
    )
    created = store.consolidate(session.id, at=10)
    assert [memory.kind for memory in created] == [
        MemoryKind.EPISODIC,
        MemoryKind.EPISODIC,
        MemoryKind.SEMANTIC,
    ]
    semantic = created[-1]
    assert semantic.content == b"pattern"
    assert semantic.provenance is Provenance.CITE  # a derived memory never overclaims
    derived_to = {line.dst for line in store.links(LinkKind.DERIVED_FROM, src=semantic.id)}
    assert derived_to == {created[0].id, created[1].id}
    assert store.consolidate(session.id, at=20) == ()  # the pattern is already semantic


def test_the_session_is_the_unit_of_consolidation():
    store = fresh()
    here = store.remember(MemoryKind.WORKING, b"run here", at=0)
    there = store.remember(MemoryKind.WORKING, b"run there", at=0)
    work = store.remember(MemoryKind.WORKING, b"other context", at=1, session=there.id)
    assert store.consolidate(here.id, at=10) == ()  # nothing of this session to promote
    assert store.memory(work.id).kind is MemoryKind.WORKING


def test_decay_takes_salience_from_the_traces():
    store = fresh()
    kept = store.remember(MemoryKind.EPISODIC, b"keep", at=0)
    store.advance(1)
    store.recall(b"keep")  # one trace at the clock
    store.decay(at=2)
    salience = score_from_bytes(word(store.memory(kept.id).facts, "salience"))
    assert salience == SCORE_MAX  # the trace outweighs the halving (it saturates, never wraps)

    silent = store.remember(MemoryKind.EPISODIC, b"silent", at=0)
    store.decay(at=3)
    salience = score_from_bytes(word(store.memory(silent.id).facts, "salience"))
    assert salience == score_halve(SCORE_MAX)  # no trace since the last decay: it halves
