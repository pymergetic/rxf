"""Certification (docs/11): signed statements bind an image's two digests to keys.

A statement is one voice: `issue` makes one over `Image.digest()` and
`Image.delta_digest()`. `verify` is the activation check — one pure function
that takes the digests a boot is about to run, one statement, and the trust
store, and answers ok or a typed refusal by name (wrong digest, unknown key,
bad signature, expired / not-yet, revoked key). `verify_quorum` is the decision
docs/11 sketches: M valid distinct voices of the N that spoke, over the same
two digests. No clock, no machine, no network is consulted (docs/11: policy is
typed metadata, never inferred from the file's opener).

The signatures are Ed25519 per RFC 8032 in pure Python (`certify.ed25519`),
pinned against the RFC's test vectors. docs/11 leaves the quorum shape open
(cross-tool checkers "can form a stronger quorum"), so this is the simplest
honest shape: a threshold over independent Ed25519 voices, one statement each.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import ed25519
from .schema import (
    FORMAT_KEY,
    FORMAT_STORE,
    HEADER,
    PUBLIC_BYTES,
    SEED_BYTES,
    SIGNATURE_BYTES,
    BadSignature,
    Digest,
    Expired,
    KeyId,
    MalformedKey,
    MalformedStatement,
    MalformedStore,
    NotEnoughVoices,
    NotYetValid,
    Ok,
    Policy,
    Quorum,
    Refusal,
    RevokedKey,
    Statement,
    Time,
    TrustKey,
    TrustStore,
    UnknownKey,
    WrongDigest,
    canonical,
    key_of,
    key_wire,
    statement_of,
    statement_wire,
    store_of,
    store_wire,
)

__all__ = [
    "FORMAT_KEY",
    "FORMAT_STORE",
    "HEADER",
    "PUBLIC_BYTES",
    "SEED_BYTES",
    "SIGNATURE_BYTES",
    "BadSignature",
    "Digest",
    "Expired",
    "KeyId",
    "MalformedKey",
    "MalformedStatement",
    "MalformedStore",
    "NotEnoughVoices",
    "NotYetValid",
    "Ok",
    "Policy",
    "Quorum",
    "Refusal",
    "RevokedKey",
    "Statement",
    "Time",
    "TrustKey",
    "TrustStore",
    "UnknownKey",
    "WrongDigest",
    "admit",
    "canonical",
    "issue",
    "key_of",
    "key_wire",
    "public_of",
    "revoke",
    "statement_of",
    "statement_wire",
    "store_of",
    "store_wire",
    "verify",
    "verify_quorum",
]


def public_of(seed: bytes) -> bytes:
    """The 32-byte public word of a seed — the voice's word in a trust store."""
    if len(seed) != SEED_BYTES:
        raise MalformedKey(f"a seed is {SEED_BYTES} bytes")
    return ed25519.public_key(seed)


def issue(
    image_digest: Digest,
    text_digest: Digest,
    key_id: KeyId,
    seed: bytes,
    issued: Time,
) -> Statement:
    """A statement signed by the seed's voice: the digests, the key id, the stamp."""
    if len(seed) != SEED_BYTES:
        raise MalformedKey(f"a seed is {SEED_BYTES} bytes")
    signature = ed25519.sign(seed, canonical(image_digest, text_digest, key_id, issued))
    return Statement(image_digest, text_digest, key_id, issued, signature)


def verify(
    image_digest: Digest,
    text_digest: Digest,
    statement: Statement,
    trust_store: TrustStore,
) -> Ok:
    """The activation check: one voice over these digests, ok or a typed refusal.

    The cheap words refuse first — the digests, the key, its revocation, the
    stamp against the store's policy words — and the signature, the only
    expensive step, is checked last. That is what keeps a boot-time decision
    fast; the caller gets `Ok`, or catches the named `Refusal` it wants.
    """
    if statement.image_digest != image_digest or statement.text_digest != text_digest:
        raise WrongDigest("the statement speaks for other digests than these")
    key = trust_store.voice(statement.key_id)
    if key is None:
        raise UnknownKey(f"no voice {statement.key_id} is admitted here")
    if statement.key_id in trust_store.revoked:
        raise RevokedKey(f"the voice {statement.key_id} is revoked")
    policy = trust_store.policy
    if statement.issued > policy.now + policy.skew:
        raise NotYetValid(
            f"the statement is stamped {statement.issued}: after now {policy.now}"
            f" with skew {policy.skew}"
        )
    if policy.now > statement.issued + policy.max_age:
        raise Expired(
            f"the statement is stamped {statement.issued}: older than max_age"
            f" {policy.max_age} at now {policy.now}"
        )
    if not ed25519.verify(key.public, statement.signable(), statement.signature):
        raise BadSignature("the signature is not the one these canonical bytes carry")
    return Ok(statement.key_id, statement.issued)


def verify_quorum(
    image_digest: Digest,
    text_digest: Digest,
    statements: Sequence[Statement],
    trust_store: TrustStore,
) -> Quorum:
    """The decision docs/11 asks for: M valid distinct voices of the N that spoke.

    Every statement is judged alone by `verify`; a voice that refuses does not
    count, and one key counts once however often it speaks. Fewer than the
    policy's quorum is `NotEnoughVoices`, carrying what each voice answered.
    """
    voices: list[KeyId] = []
    refused: list[tuple[KeyId, Refusal]] = []
    for statement in statements:
        try:
            ok = verify(image_digest, text_digest, statement, trust_store)
        except Refusal as refusal:
            refused.append((statement.key_id, refusal))
            continue
        if ok.key_id not in voices:
            voices.append(ok.key_id)
    wanted = trust_store.policy.quorum
    if len(voices) < wanted:
        word = "voice" if wanted == 1 else "voices"
        raise NotEnoughVoices(
            f"the decision needs {wanted} {word} and {len(voices)} spoke",
            valid=tuple(voices),
            refused=tuple(refused),
        )
    return Quorum(tuple(voices))


def admit(trust_store: TrustStore, key: TrustKey) -> TrustStore:
    """The store with the voice admitted: a new id, or the one already under it."""
    held = trust_store.voice(key.key_id)
    if held is not None:
        if held.public != key.public:
            raise MalformedStore(f"the store already names {key.key_id} with another key")
        return trust_store
    return TrustStore(trust_store.policy, trust_store.keys + (key,), trust_store.revoked)


def revoke(trust_store: TrustStore, key_id: KeyId) -> TrustStore:
    """The store with the voice revoked — an admission closed, no history lost."""
    if trust_store.voice(key_id) is None:
        raise UnknownKey(f"no voice {key_id} is admitted here")
    if key_id in trust_store.revoked:
        raise RevokedKey(f"the voice {key_id} is already revoked")
    return TrustStore(trust_store.policy, trust_store.keys, trust_store.revoked | {key_id})
