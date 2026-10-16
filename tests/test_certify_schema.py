"""The statement format: five words, one canonical byte string, the named refusals.

Two things are pinned here: the canonical serialization a voice signs (byte for
byte, golden — it may not drift between runs or versions) and the wire shapes
the files round-trip through. Everything a statement or a store refuses is
refused by name: a malformed word never becomes a half-read value.
"""

from __future__ import annotations

import dataclasses

import pytest

from pymergetic.rxftk import certify
from pymergetic.rxftk.certify import (
    MalformedKey,
    MalformedStatement,
    MalformedStore,
    Policy,
    Refusal,
    Statement,
    TrustKey,
    TrustStore,
)

IMAGE = "ab" * 32
TEXT = "cd" * 32
NOW = 1700000000

# The pinned canonical bytes of (IMAGE, TEXT, "release-1", NOW): five tagged
# lines, each ending in a newline. A change here is a format change.
GOLDEN = (
    b"rxf-certify-statement-v1\n"
    b"image " + b"ab" * 32 + b"\n"
    b"text " + b"cd" * 32 + b"\n"
    b"key release-1\n"
    b"issued 1700000000\n"
)


def statement(signature: bytes = bytes(64)) -> Statement:
    return Statement(IMAGE, TEXT, "release-1", NOW, signature)


def test_the_canonical_bytes_are_pinned() -> None:
    """The exact bytes a voice signs — golden, so no run may differ from another."""
    assert certify.canonical(IMAGE, TEXT, "release-1", NOW) == GOLDEN
    assert statement().signable() == GOLDEN


def test_the_canonical_bytes_are_stable_across_runs() -> None:
    """Two constructions of one statement produce one byte string, every time."""
    first = certify.canonical(*_four(statement()))
    again = Statement(IMAGE, TEXT, "release-1", NOW, bytes(64))
    assert certify.canonical(*_four(again)) == first == GOLDEN
    assert first == certify.canonical(IMAGE, TEXT, "release-1", NOW)


def _four(value: Statement) -> tuple[str, str, str, int]:
    return value.image_digest, value.text_digest, value.key_id, value.issued


def test_one_statement_is_one_byte_string() -> None:
    """A key id carries no space and no newline: no two statements share bytes."""
    assert certify.canonical(IMAGE, TEXT, "a-b.c_1", 1) != certify.canonical(
        IMAGE, TEXT, "a-b.c_2", 1
    )
    for bad in ("", "with space", "with\nnewline", "tab\tseparated"):
        with pytest.raises(MalformedStatement):
            certify.canonical(IMAGE, TEXT, bad, NOW)


def test_the_values_are_frozen() -> None:
    """A statement, a policy and a store are values: they do not move under a reader."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        statement().issued = 1  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        Policy(NOW, 1).quorum = 2  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        TrustStore(Policy(NOW, 1)).keys = ()  # type: ignore[misc]


def test_a_malformed_statement_is_refused_at_the_door() -> None:
    """Words that are not a statement never become one."""
    with pytest.raises(MalformedStatement):
        Statement("not-a-digest", TEXT, "release-1", NOW, bytes(64))
    with pytest.raises(MalformedStatement):
        Statement(IMAGE, "AB" * 32, "release-1", NOW, bytes(64))  # uppercase is not it
    with pytest.raises(MalformedStatement):
        Statement(IMAGE, TEXT, "release-1", -1, bytes(64))
    with pytest.raises(MalformedStatement):
        Statement(IMAGE, TEXT, "release-1", NOW, bytes(63))


def test_a_malformed_key_or_store_is_refused_by_name() -> None:
    with pytest.raises(MalformedKey):
        TrustKey("release-1", bytes(31))
    with pytest.raises(MalformedKey):
        certify.public_of(bytes(16))
    with pytest.raises(MalformedStore):
        Policy(NOW, 1, 0, 0)  # a quorum of none decides nothing
    store = certify.admit(
        TrustStore(Policy(NOW, 1)), TrustKey("release-1", bytes(32))
    )
    with pytest.raises(MalformedStore):
        certify.admit(store, TrustKey("release-1", b"\x01" * 32))  # one id, two keys


def test_the_wire_shapes_round_trip() -> None:
    """A statement, a key and a store read back as what was written."""
    signed = statement(bytes(range(64)))
    assert certify.statement_of(certify.statement_wire(signed)) == signed
    key_id, seed = "release-1", bytes(range(32))
    assert certify.key_of(certify.key_wire(key_id, seed)) == (key_id, seed)
    store = certify.admit(
        TrustStore(Policy(NOW, 3600, 60, 2), (), frozenset()),
        TrustKey(key_id, certify.public_of(seed)),
    )
    assert certify.store_of(certify.store_wire(store)) == store


def test_wire_shapes_that_are_not_themselves_are_refused() -> None:
    """A JSON shape with the wrong format word is refused, never guessed at."""
    with pytest.raises(MalformedStatement):
        certify.statement_of({"format": "other"})
    with pytest.raises(MalformedStatement):
        certify.statement_of(
            {**certify.statement_wire(statement()), "signature": "not hex"}
        )
    with pytest.raises(MalformedKey):
        certify.key_of({"format": "other"})
    with pytest.raises(MalformedStore):
        certify.store_of({"format": "other"})
    twice = {
        **certify.store_wire(TrustStore(Policy(NOW, 1))),
        "keys": [
            {"key_id": "release-1", "public": "00" * 32},
            {"key_id": "release-1", "public": "00" * 32},
        ],
    }
    with pytest.raises(MalformedStore):
        certify.store_of(twice)


def test_the_refusal_set_is_named_and_raises() -> None:
    """A refusal is a name the body catches (docs/10), not a code."""
    for name in (
        "WrongDigest",
        "UnknownKey",
        "BadSignature",
        "Expired",
        "NotYetValid",
        "RevokedKey",
        "NotEnoughVoices",
    ):
        refusal = getattr(certify, name)
        assert issubclass(refusal, Refusal)
        with pytest.raises(Refusal):
            raise refusal(name)
