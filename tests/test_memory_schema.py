"""The hard words of the memory register (docs/24 §2, §6, §10).

Two invariants live here: one Interval concept with closed semantics (a memory, a link,
and a relation all hold the same thing, and closing rewrites no history), and Score as
fixed point 0..2**32-1 arithmetic that refuses every float. The records are frozen, the
word payloads are typed, and the provenance floor is honest.
"""

from __future__ import annotations

import dataclasses
import inspect
from typing import cast

import pytest

from pymergetic.rxf import memory
from pymergetic.rxf.memory import identity as identity_module
from pymergetic.rxf.memory import schema
from pymergetic.rxf.memory import store as store_module
from pymergetic.rxf.memory.schema import (
    SCORE_MAX,
    AgentSpec,
    Anchor,
    Change,
    Fact,
    Hit,
    Identity,
    IdentityKind,
    Interval,
    Link,
    LinkKind,
    Memory,
    MemoryKind,
    MemoryRefusal,
    Provenance,
    Recall,
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


def test_an_interval_is_closed_at_both_words_and_none_holds_until_now():
    open_ = Interval(10)
    assert not open_.closed
    assert not open_.holds(9)
    assert open_.holds(10)  # the opening word is included
    assert open_.holds(10**6)  # None = holds until now

    closed = Interval(10, 20)
    assert closed.closed
    assert closed.holds(10)
    assert closed.holds(20)  # a closed interval includes its closing word
    assert not closed.holds(21)
    assert not closed.holds(9)


def test_closing_an_interval_fills_the_end_word():
    closed = Interval(10).close(20)
    assert closed == Interval(10, 20)
    assert closed.holds(20)
    assert not closed.holds(21)


def test_closing_refuses_to_rewrite_history():
    with pytest.raises(MemoryRefusal, match="would rewrite history"):
        Interval(10, 20).close(30)
    with pytest.raises(MemoryRefusal, match="never closes at"):
        Interval(10).close(5)


def test_intervals_overlap_when_they_share_at_least_one_word():
    assert Interval(0, 10).overlaps(Interval(10, 20))  # closed endpoints touch
    assert not Interval(0, 9).overlaps(Interval(10, 20))
    assert Interval(5).overlaps(Interval(6, 7))  # the open end reaches past every word
    assert not Interval(5).overlaps(Interval(0, 4))
    assert Interval(0, 10).overlaps(Interval(3, 4))


def test_one_interval_concept_covers_memories_links_and_relations():
    """There is no second interval hiding in a module of the package."""
    found = set()
    for module in (memory, schema, store_module, identity_module):
        for obj in vars(module).values():
            if inspect.isclass(obj) and "interval" in obj.__name__.lower():
                found.add(obj)
    assert found == {Interval}

    node = Memory(1, MemoryKind.WORKING, b"", (), 0, Interval(0), Provenance.HASH)
    line = Link(1, 2, 3, LinkKind.ABOUT, (), Interval(0))
    assert type(node.valid) is Interval
    assert type(line.valid) is Interval


def test_score_words_are_fixed_point_and_refuse_floats():
    assert score_of(0) == 0
    assert score_of(SCORE_MAX) == SCORE_MAX
    assert score_of(SCORE_MAX) == 2**32 - 1  # the word is 0..2**32-1
    for wrong in (1.5, 1.0, float(SCORE_MAX), True, "1"):
        with pytest.raises(TypeError):
            score_of(cast(int, wrong))
    for out in (-1, SCORE_MAX + 1, 2**64):
        with pytest.raises(MemoryRefusal, match="out of range"):
            score_of(out)


def test_score_arithmetic_is_integral_saturating_and_float_free():
    assert type(score_add(1, 2)) is int
    assert score_add(1, 2) == 3
    assert score_add(SCORE_MAX, 7) == SCORE_MAX  # saturates, never wraps
    assert score_mul(SCORE_MAX, 1 << 31) == (SCORE_MAX + 1) // 2  # full times half
    assert score_mul(0, SCORE_MAX) == 0
    product = score_mul(SCORE_MAX, SCORE_MAX)
    assert type(product) is int
    assert 0 <= product <= SCORE_MAX
    assert score_halve(SCORE_MAX) == SCORE_MAX // 2
    assert score_halve(1) == 0  # the decay step reaches zero, or nothing is ever forgotten
    for wrong in (1.0, 0.5, True):
        with pytest.raises(TypeError):
            score_add(cast(int, wrong), 1)
        with pytest.raises(TypeError):
            score_mul(1, cast(int, wrong))
        with pytest.raises(TypeError):
            score_halve(cast(int, wrong))


def test_score_and_word_payloads_round_trip_as_typed_bytes():
    assert score_from_bytes(score_bytes(SCORE_MAX)) == SCORE_MAX
    assert score_from_bytes(score_bytes(0)) == 0
    with pytest.raises(MemoryRefusal, match="is 4 bytes"):
        score_from_bytes(b"\x01\x02")
    assert unpack_word(pack_word(5)) == 5
    assert unpack_word(pack_word(2**64 - 1)) == 2**64 - 1
    with pytest.raises(MemoryRefusal, match="64 bits"):
        pack_word(-1)
    with pytest.raises(MemoryRefusal, match="is 8 bytes"):
        unpack_word(b"\x00")
    with pytest.raises(TypeError):
        pack_word(cast(int, 1.0))


def test_the_type_set_is_frozen_records():
    instances = [
        Interval(0),
        Fact("k", b"v"),
        Anchor(None, "commit:path:1-2", Provenance.CITE),
        Memory(1, MemoryKind.WORKING, b"", (), 0, Interval(0), Provenance.HASH),
        Link(1, 2, 3, LinkKind.ABOUT, (), Interval(0)),
        Trace(1, 0, b"", 0),
        Hit(1, 0),
        Recall(b"", (), ()),
        AgentSpec("model", "1", 0),
        Identity(1, IdentityKind.PERSON, ("Ada",), 0, Provenance.HASH),
        Change(1, 2, 3, 0, None),
    ]
    for instance in instances:
        assert dataclasses.is_dataclass(instance)
        for field in dataclasses.fields(instance):
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(instance, field.name, None)


def test_provenance_words_refuse_the_anonymous_floor():
    assert provenance_of("full") is Provenance.FULL
    assert provenance_of("cite") is Provenance.CITE
    assert provenance_of("hash") is Provenance.HASH
    with pytest.raises(MemoryRefusal, match="the provenance floor is hash"):
        provenance_of("none")
    with pytest.raises(MemoryRefusal, match="the provenance floor is hash"):
        provenance_of("anonymous")
    with pytest.raises(MemoryRefusal, match="not a provenance tier"):
        provenance_of("gold")
