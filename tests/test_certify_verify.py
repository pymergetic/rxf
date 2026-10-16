"""The activation check: one pure function, ok or a typed refusal by name.

`verify` is what the boot path calls before an image runs (docs/07: activation
validates, failure changes nothing). Every refusal has its name — wrong digest,
unknown key, bad signature, expired, not-yet, revoked key — and the seam's
signature is pinned here so the packaging lane can call it and nothing else.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from pymergetic.rxf import certify
from pymergetic.rxf.certify import (
    BadSignature,
    Expired,
    NotYetValid,
    Ok,
    Policy,
    RevokedKey,
    Statement,
    TrustKey,
    TrustStore,
    UnknownKey,
    WrongDigest,
)

IMAGE = "ab" * 32
TEXT = "cd" * 32
SEED = bytes(range(32))
NOW = 1700000000
MAX_AGE = 3600
SKEW = 60


def statement(
    issued: int = NOW, key_id: str = "release-1", seed: bytes = SEED
) -> Statement:
    return certify.issue(IMAGE, TEXT, key_id, seed, issued)


def store(
    now: int = NOW,
    quorum: int = 1,
    revoked: tuple[str, ...] = (),
    key_id: str = "release-1",
    seed: bytes = SEED,
) -> TrustStore:
    return TrustStore(
        Policy(now=now, max_age=MAX_AGE, skew=SKEW, quorum=quorum),
        (TrustKey(key_id, certify.public_of(seed)),),
        frozenset(revoked),
    )


def test_the_activation_seam_is_the_named_pure_function() -> None:
    """The packaging lane calls `verify` and `verify_quorum`; their shape is the seam."""
    assert list(inspect.signature(certify.verify).parameters) == [
        "image_digest",
        "text_digest",
        "statement",
        "trust_store",
    ]
    assert list(inspect.signature(certify.verify_quorum).parameters) == [
        "image_digest",
        "text_digest",
        "statements",
        "trust_store",
    ]


def test_issue_then_verify_says_ok() -> None:
    """The round trip: a statement over these digests is one accepted voice."""
    signed = statement()
    assert certify.verify(IMAGE, TEXT, signed, store()) == Ok("release-1", NOW)
    assert signed.signable() == certify.canonical(IMAGE, TEXT, "release-1", NOW)


def test_a_statement_for_other_digests_is_refused_by_name() -> None:
    """The binding is both digests; either one failing is the wrong digest."""
    signed = statement()
    with pytest.raises(WrongDigest):
        certify.verify("00" * 32, TEXT, signed, store())
    with pytest.raises(WrongDigest):
        certify.verify(IMAGE, "11" * 32, signed, store())


def test_a_tampered_signature_is_refused_by_name() -> None:
    """One flipped byte of the signature, and the voice did not speak this."""
    signed = statement()
    broken = bytearray(signed.signature)
    broken[0] ^= 0x01
    tampered = dataclasses.replace(signed, signature=bytes(broken))
    with pytest.raises(BadSignature):
        certify.verify(IMAGE, TEXT, tampered, store())
    with pytest.raises(BadSignature):  # the id is admitted, the words are another's
        certify.verify(
            IMAGE,
            TEXT,
            signed,
            store(key_id="release-1", seed=bytes(32)),
        )


def test_a_tampered_stamp_is_refused_by_name() -> None:
    """Restamping a statement is signing again: the signature covers the stamp."""
    tampered = dataclasses.replace(statement(), issued=NOW - 1)
    with pytest.raises(BadSignature):
        certify.verify(IMAGE, TEXT, tampered, store())


def test_an_unknown_key_is_refused_by_name() -> None:
    """A voice the store does not admit speaks for nothing."""
    signed = statement(key_id="release-2", seed=bytes(range(1, 33)))
    with pytest.raises(UnknownKey):
        certify.verify(IMAGE, TEXT, signed, store())
    with pytest.raises(UnknownKey):  # a store of other voices is still no answer
        certify.verify(IMAGE, TEXT, statement(), store(key_id="release-2"))


def test_a_revoked_key_is_refused_by_name() -> None:
    """An admission closed at any time ends that voice (docs/11: explicit policy)."""
    with pytest.raises(RevokedKey):
        certify.verify(IMAGE, TEXT, statement(), store(revoked=("release-1",)))


def test_expired_and_not_yet_are_refused_by_name() -> None:
    """The stamp is judged against the store's words — the check reads no clock."""
    with pytest.raises(Expired):
        certify.verify(IMAGE, TEXT, statement(issued=NOW - MAX_AGE - 1), store())
    with pytest.raises(NotYetValid):
        certify.verify(IMAGE, TEXT, statement(issued=NOW + SKEW + 1), store())
    # The boundary words hold: at the edges the statement is still the voice's.
    assert certify.verify(IMAGE, TEXT, statement(issued=NOW - MAX_AGE), store())
    assert certify.verify(IMAGE, TEXT, statement(issued=NOW + SKEW), store())
    assert certify.verify(  # and a fresh store's now follows its policy, not a clock
        IMAGE, TEXT, statement(issued=NOW), store(now=NOW + MAX_AGE)
    )
