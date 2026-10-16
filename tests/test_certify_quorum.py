"""The quorum (docs/11): M valid distinct voices of the N that spoke.

docs/11 leaves the shape open ("cross-tool or cross-language checkers can form
a stronger quorum"), so this is the simplest honest one: a threshold over
independent Ed25519 voices over the same two digests. What is pinned here: M
decides, M-1 does not; one key is one voice however often it speaks; and a
refused voice does not stop the others from deciding.
"""

from __future__ import annotations

import dataclasses

import pytest

from pymergetic.rxf import certify
from pymergetic.rxf.certify import (
    NotEnoughVoices,
    Policy,
    RevokedKey,
    Statement,
    TrustKey,
    TrustStore,
)

IMAGE = "ab" * 32
TEXT = "cd" * 32
NOW = 1700000000

VOICES = (
    ("release-1", bytes(range(32))),
    ("release-2", bytes(range(1, 33))),
    ("release-3", bytes(range(2, 34))),
)


def statements() -> list[Statement]:
    return [
        certify.issue(IMAGE, TEXT, key_id, seed, NOW) for key_id, seed in VOICES
    ]


def store(quorum: int = 2, revoked: tuple[str, ...] = ()) -> TrustStore:
    return TrustStore(
        Policy(now=NOW, max_age=3600, skew=60, quorum=quorum),
        tuple(TrustKey(key_id, certify.public_of(seed)) for key_id, seed in VOICES),
        frozenset(revoked),
    )


def test_m_of_n_voices_decide() -> None:
    """Two voices of three, over the same two digests, decide (docs/11)."""
    spoken = statements()
    assert certify.verify_quorum(IMAGE, TEXT, spoken, store()).voices == (
        "release-1",
        "release-2",
        "release-3",
    )
    assert certify.verify_quorum(IMAGE, TEXT, spoken[:2], store()).voices == (
        "release-1",
        "release-2",
    )


def test_one_voice_short_of_the_quorum_is_refused_by_name() -> None:
    """M-1 voices say nothing the policy accepts: `NotEnoughVoices`, with why."""
    with pytest.raises(NotEnoughVoices) as stopped:
        certify.verify_quorum(IMAGE, TEXT, statements()[:1], store())
    refusal = stopped.value
    assert refusal.valid == ("release-1",)
    assert refusal.refused == ()
    assert "needs 2 voices and 1 spoke" in str(refusal)


def test_one_voice_speaks_once() -> None:
    """One key signing twice is one voice: distinct is the word (docs/11)."""
    one = statements()[0]
    with pytest.raises(NotEnoughVoices) as stopped:
        certify.verify_quorum(IMAGE, TEXT, [one, one, one], store())
    assert stopped.value.valid == ("release-1",)


def test_a_refused_voice_does_not_stop_the_others() -> None:
    """M-of-N tolerates N-M voices failing; each refusal is carried by name."""
    spoken = statements()
    broken = dataclasses.replace(
        spoken[0], signature=bytes([spoken[0].signature[0] ^ 0x01])
        + spoken[0].signature[1:]
    )
    decided = certify.verify_quorum(IMAGE, TEXT, [broken, spoken[1], spoken[2]], store())
    assert decided.voices == ("release-2", "release-3")
    with pytest.raises(NotEnoughVoices) as stopped:  # and when they all fail, it says so
        certify.verify_quorum(IMAGE, TEXT, [broken, broken], store())
    refused = stopped.value.refused
    assert [type(each).__name__ for _, each in refused] == ["BadSignature", "BadSignature"]


def test_a_revoked_voice_does_not_count() -> None:
    """A revocation ends one voice; the quorum then needs its remaining voices."""
    spoken = statements()
    with pytest.raises(NotEnoughVoices) as stopped:
        certify.verify_quorum(IMAGE, TEXT, spoken[:2], store(revoked=("release-2",)))
    assert stopped.value.valid == ("release-1",)
    assert [type(each).__name__ for _, each in stopped.value.refused] == ["RevokedKey"]
    assert certify.verify_quorum(  # three voices minus one revoked still decides
        IMAGE, TEXT, spoken, store(revoked=("release-2",))
    ).voices == ("release-1", "release-3")
    with pytest.raises(RevokedKey):  # alone, a revoked voice is refused by name
        certify.verify(IMAGE, TEXT, spoken[1], store(revoked=("release-2",)))
