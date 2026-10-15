"""Forgetting is a closed interval, never a deletion (docs/24 §6, §10).

Decay closes validity intervals and supersession writes SUPERSEDES lines — and there is
no physical deletion API anywhere in the memory module. What is closed stays closed: a
rewrite of history is refused, and a contradiction that is not resolved stays honest
with both intervals intact.
"""

from __future__ import annotations

import inspect

import pytest

from pymergetic.rxf import memory as memory_pkg
from pymergetic.rxf.memory import (
    IdentityKind,
    Interval,
    LinkKind,
    MemoryKind,
    MemoryRefusal,
    MemoryStore,
    schema,
)
from pymergetic.rxf.memory import identity as identity_module
from pymergetic.rxf.memory import store as store_module

BANNED = (
    "delete",
    "remove",
    "destroy",
    "purge",
    "erase",
    "forget",
    "wipe",
    "drop",
    "free",
    "clear",
    "kill",
)


def fresh() -> MemoryStore:
    return MemoryStore()


def test_decay_closes_the_validity_interval_and_keeps_the_cell():
    store = fresh()
    fades = store.remember(MemoryKind.EPISODIC, b"it fades", at=0)
    before = store.cell_count
    when = 0
    for step in range(1, 65):
        when = step
        store.decay(at=step)
        if store.memory(fades.id).valid.closed:
            break
    kept = store.memory(fades.id)
    assert kept.valid == Interval(0, when)  # closed at both words, at the decay word
    assert kept.content == b"it fades"  # the cell is whole, not freed
    assert store.cell_count == before  # closing wrote words; it removed nothing


def test_there_is_no_physical_deletion_api():
    """A forget that frees bytes is a refusal, not a feature — so the face does not exist."""
    faces = [
        name
        for name, obj in vars(MemoryStore).items()
        if not name.startswith("_") and callable(obj)
    ]
    for module in (memory_pkg, schema, store_module, identity_module):
        faces += [
            name
            for name, obj in vars(module).items()
            if inspect.isfunction(obj) and not name.startswith("_")
        ]
    assert not set(faces) & set(BANNED)
    for name in BANNED:
        assert not hasattr(MemoryStore, name)


def test_supersede_writes_the_supersedes_line_and_closes_the_old_interval():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    old = store.remember(MemoryKind.SEMANTIC, b"the old belief", at=1)
    new = store.remember(MemoryKind.SEMANTIC, b"the new belief", at=2)
    step = store.supersede(old.id, new.id, by=ada.id, at=10, why=b"it was wrong")
    assert step.kind is LinkKind.SUPERSEDES
    assert (step.src, step.dst) == (new.id, old.id)  # new supersedes old
    assert store.memory(old.id).valid == Interval(1, 10)  # the interval closes, the cell stays
    assert store.memory(new.id).valid == Interval(2)
    assert not store.memory(new.id).valid.closed


def test_supersession_closes_at_the_supersede_word_inclusively():
    store = fresh()
    old = store.remember(MemoryKind.SEMANTIC, b"belief", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"better", at=1)
    store.supersede(old.id, new.id, at=7)
    closed = store.memory(old.id).valid
    assert closed.holds(7)  # the closing word is inside the closed interval
    assert not closed.holds(8)


def test_supersession_is_a_readable_history_of_a_thought():
    store = fresh()
    ada = store.identity(IdentityKind.PERSON, ("Ada",), at=0)
    grace = store.identity(IdentityKind.PERSON, ("Grace",), at=0)
    first = store.remember(MemoryKind.SEMANTIC, b"v1", at=0)
    second = store.remember(MemoryKind.SEMANTIC, b"v2", at=5)
    third = store.remember(MemoryKind.SEMANTIC, b"v3", at=9)
    store.supersede(first.id, second.id, by=ada.id, at=5, why=b"was wrong")
    store.supersede(second.id, third.id, by=grace.id, at=9)
    history = store.changes(first.id)
    assert [(change.id, change.subject, change.by, change.at, change.why) for change in history] == [
        (second.id, first.id, ada.id, 5, b"was wrong"),
        (third.id, second.id, grace.id, 9, None),
    ]  # who thought what, who changed it, when, and why — oldest step first
    assert [(change.id, change.subject) for change in store.changes(second.id)] == [
        (third.id, second.id)
    ]


def test_a_closed_memory_is_never_superseded_again():
    store = fresh()
    old = store.remember(MemoryKind.SEMANTIC, b"belief", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"better", at=5)
    store.supersede(old.id, new.id, at=5)
    before = store.cell_count
    with pytest.raises(MemoryRefusal, match="would rewrite when it stopped holding"):
        store.supersede(old.id, new.id, at=9)
    assert store.cell_count == before  # the refused step wrote nothing
    assert store.memory(old.id).valid == Interval(0, 5)  # the history is untouched


def test_a_supersession_line_comes_only_from_the_program():
    store = fresh()
    old = store.remember(MemoryKind.SEMANTIC, b"belief", at=0)
    new = store.remember(MemoryKind.SEMANTIC, b"better", at=5)
    with pytest.raises(MemoryRefusal, match="goes through supersede"):
        store.link(new.id, old.id, LinkKind.SUPERSEDES)
    assert store.links(LinkKind.SUPERSEDES) == ()  # no line without the closing compose


def test_contradiction_that_is_not_resolved_stays_honest():
    store = fresh()
    left = store.remember(MemoryKind.SEMANTIC, b"it holds", at=0)
    right = store.remember(MemoryKind.SEMANTIC, b"it does not", at=0)
    store.link(left.id, right.id, LinkKind.CONTRADICTS, at=0)
    assert not store.memory(left.id).valid.closed
    assert not store.memory(right.id).valid.closed  # both intervals honest, nothing closed
    assert len(store.links(LinkKind.CONTRADICTS)) == 1
    assert store.changes(left.id) == ()
