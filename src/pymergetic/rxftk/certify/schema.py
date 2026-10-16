"""The certification type set: one statement, one trust store, the named refusals.

A `Statement` is one voice: it binds an image's two digests — `image_digest` (the
canonical bytes, `Image.digest()`) and `text_digest` (the canonical delta text,
`Image.delta_digest()`) — to a `key_id`, stamps `issued`, and carries the
signature over the canonical bytes of those four words (docs/11: signatures and
policy are explicit typed metadata). `TrustKey`, `Policy` and `TrustStore` are
the deployment side: which voices are admitted, which are revoked, and the words
a decision is judged against.

`Policy` carries `now`, the observation word, because the check reads no clock:
policy is typed metadata, never inferred from the machine that happens to open
the file (docs/11). A statement is accepted at most `max_age` after its stamp
and at most `skew` before it, and one decision needs `quorum` voices.

The canonical serialization is defined once, here, and pinned: `canonical` is
the exact byte string a voice signs. The bottom section carries the typed
refusals the whole layer raises (docs/10: `check` returns success or typed
refusals) — a refusal is a name the body catches, not a code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, cast

Time = int  # 64-bit value word (the register of docs/24)
Digest = str  # 64 lowercase hex digits: SHA-256 of canonical bytes or text
KeyId = str  # the name a voice signs under: printable ASCII, no spaces

SEED_BYTES: Final = 32  # an Ed25519 seed (the private word of a voice)
PUBLIC_BYTES: Final = 32  # an Ed25519 public key
SIGNATURE_BYTES: Final = 64  # an Ed25519 signature

HEADER: Final = "rxf-certify-statement-v1"  # the canonical bytes' first line
FORMAT_KEY: Final = "rxf-certify-key-v1"  # the key file a voice keeps
FORMAT_STORE: Final = "rxf-certify-store-v1"  # the trust store a deployment pins


# ---- the typed refusals ----


class Refusal(Exception):
    """The base of every typed refusal the layer raises."""


class MalformedStatement(Refusal):
    """Words or bytes that are not a statement — refused, never half-read."""


class MalformedKey(Refusal):
    """Key material or a key id that is not usable."""


class MalformedStore(Refusal):
    """A trust store that does not parse, or one that names a voice twice."""


class WrongDigest(Refusal):
    """The statement binds other digests than the ones it is asked about."""


class UnknownKey(Refusal):
    """The trust store holds no voice under this key id."""


class RevokedKey(Refusal):
    """The voice is revoked in the trust store."""


class NotYetValid(Refusal):
    """The statement is stamped after the policy's now, beyond its skew."""


class Expired(Refusal):
    """The statement is older than the policy's max_age allows."""


class BadSignature(Refusal):
    """The signature is not the one these canonical bytes carry."""


class NotEnoughVoices(Refusal):
    """Fewer than the quorum's M valid distinct voices spoke (docs/11).

    `valid` names the voices that did speak; `refused` carries each voice's
    typed refusal, so a report can say why a voice did not count.
    """

    def __init__(
        self,
        message: str,
        valid: tuple[KeyId, ...] = (),
        refused: tuple[tuple[KeyId, Refusal], ...] = (),
    ) -> None:
        super().__init__(message)
        self.valid = valid
        self.refused = refused


# ---- the words ----


def _is_digest(word: object) -> bool:
    """A digest word: exactly 64 lowercase hex digits (as `doc_hash` demands)."""
    return (
        isinstance(word, str)
        and len(word) == 64
        and all(c in "0123456789abcdef" for c in word)
    )


def _is_key_id(word: object) -> bool:
    """A key id word: non-empty printable ASCII without spaces (one line in the bytes)."""
    return (
        isinstance(word, str) and bool(word) and all(0x21 <= ord(c) <= 0x7E for c in word)
    )


# ---- the values ----


@dataclass(frozen=True)
class Statement:
    """One voice over an image: the two digests bound to a key and stamped.

    `image_digest` is `Image.digest()` (the canonical bytes) and `text_digest`
    is `Image.delta_digest()` (the canonical delta text): one statement names
    both, so a text that disagrees with the bytes it came with is refused.
    """

    image_digest: Digest
    text_digest: Digest
    key_id: KeyId
    issued: Time  # the moment of signing, a 64-bit word
    signature: bytes  # Ed25519 over `signable()`, 64 bytes

    def __post_init__(self) -> None:
        if not _is_digest(self.image_digest) or not _is_digest(self.text_digest):
            raise MalformedStatement("a digest is 64 lowercase hex digits")
        if not _is_key_id(self.key_id):
            raise MalformedStatement("a key id is printable ASCII without spaces")
        if not 0 <= self.issued < 2**64:
            raise MalformedStatement("issued is a 64-bit word")
        if not isinstance(self.signature, bytes) or len(self.signature) != SIGNATURE_BYTES:
            raise MalformedStatement(f"a signature is {SIGNATURE_BYTES} bytes")

    def signable(self) -> bytes:
        """The canonical bytes this statement signs (its four bound words)."""
        return canonical(self.image_digest, self.text_digest, self.key_id, self.issued)


@dataclass(frozen=True)
class Policy:
    """The words a decision is judged against — explicit deployment policy (docs/11).

    `now` is the observation word: the check reads no clock, so the store says
    what time its decision speaks for. A statement is accepted while it is at
    most `max_age` old and at most `skew` ahead of `now`, and one decision needs
    `quorum` valid distinct voices (M of the N the store admits).
    """

    now: Time
    max_age: Time
    skew: Time = 0
    quorum: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.now < 2**64:
            raise MalformedStore("now is a 64-bit word")
        if not 0 <= self.max_age < 2**64 or not 0 <= self.skew < 2**64:
            raise MalformedStore("max_age and skew are 64-bit words")
        if self.quorum < 1:
            raise MalformedStore("a quorum is at least one voice")


@dataclass(frozen=True)
class TrustKey:
    """One admitted voice: its key id and its 32-byte Ed25519 public word."""

    key_id: KeyId
    public: bytes

    def __post_init__(self) -> None:
        if not _is_key_id(self.key_id):
            raise MalformedKey("a key id is printable ASCII without spaces")
        if not isinstance(self.public, bytes) or len(self.public) != PUBLIC_BYTES:
            raise MalformedKey(f"a public word is {PUBLIC_BYTES} bytes")


@dataclass(frozen=True)
class TrustStore:
    """The deployment side: admitted voices, revocations, and the policy words."""

    policy: Policy
    keys: tuple[TrustKey, ...] = ()
    revoked: frozenset[KeyId] = frozenset()

    def voice(self, key_id: KeyId) -> TrustKey | None:
        """The admitted voice under this key id; none when the store holds none."""
        for key in self.keys:
            if key.key_id == key_id:
                return key
        return None


@dataclass(frozen=True)
class Ok:
    """The verdict of one accepted voice: these digests, this key, at this word."""

    key_id: KeyId
    issued: Time


@dataclass(frozen=True)
class Quorum:
    """The voices that spoke for one decision, in the order they counted (docs/11)."""

    voices: tuple[KeyId, ...]


# ---- the canonical bytes (pinned, defined once) ----


def canonical(image_digest: Digest, text_digest: Digest, key_id: KeyId, issued: Time) -> bytes:
    """The canonical statement bytes — the deterministic bytes a voice signs.

    Pinned here, once: five tagged lines in a fixed order, UTF-8, each ending in
    a newline. A key id carries no space and no newline and the digests are 64
    hex digits, so one statement is one byte string and no two statements sign
    the same bytes.
    """
    if not _is_digest(image_digest) or not _is_digest(text_digest):
        raise MalformedStatement("a digest is 64 lowercase hex digits")
    if not _is_key_id(key_id):
        raise MalformedStatement("a key id is printable ASCII without spaces")
    if not 0 <= issued < 2**64:
        raise MalformedStatement("issued is a 64-bit word")
    return (
        f"{HEADER}\n"
        f"image {image_digest}\n"
        f"text {text_digest}\n"
        f"key {key_id}\n"
        f"issued {issued}\n"
    ).encode()


# ---- the wire shapes (JSON files; the CLI writes these) ----


def statement_wire(statement: Statement) -> dict[str, Any]:
    """The statement as its JSON shape: the file `rxf certify issue` writes."""
    return {
        "format": HEADER,
        "image_digest": statement.image_digest,
        "text_digest": statement.text_digest,
        "key_id": statement.key_id,
        "issued": statement.issued,
        "signature": statement.signature.hex(),
    }


def statement_of(data: object) -> Statement:
    """The statement behind a parsed JSON shape; a refusal when there is none."""
    if not isinstance(data, dict) or data.get("format") != HEADER:
        raise MalformedStatement(f"not a statement: the format is not {HEADER}")
    fields = cast(dict[str, object], data)
    image_digest, text_digest, key_id = (
        fields.get("image_digest"),
        fields.get("text_digest"),
        fields.get("key_id"),
    )
    issued = fields.get("issued")
    signature = fields.get("signature")
    if (
        not isinstance(image_digest, str)
        or not isinstance(text_digest, str)
        or not isinstance(key_id, str)
    ):
        raise MalformedStatement("the digests and the key id are text")
    if not isinstance(issued, int) or not isinstance(signature, str):
        raise MalformedStatement("issued is a word and the signature is hex")
    try:
        body = bytes.fromhex(signature)
    except ValueError as error:
        raise MalformedStatement("the signature is hex") from error
    return Statement(image_digest, text_digest, key_id, issued, body)


def key_wire(key_id: KeyId, seed: bytes) -> dict[str, Any]:
    """The private key file of a voice: its id and its 32-byte seed."""
    return {"format": FORMAT_KEY, "key_id": key_id, "seed": seed.hex()}


def key_of(data: object) -> tuple[KeyId, bytes]:
    """The (key id, seed) behind a parsed key file; a refusal when there is none."""
    if not isinstance(data, dict) or data.get("format") != FORMAT_KEY:
        raise MalformedKey(f"not a key: the format is not {FORMAT_KEY}")
    fields = cast(dict[str, object], data)
    key_id, seed = fields.get("key_id"), fields.get("seed")
    if not isinstance(key_id, str) or not isinstance(seed, str):
        raise MalformedKey("a key file carries its id as text and its seed as hex")
    try:
        body = bytes.fromhex(seed)
    except ValueError as error:
        raise MalformedKey("the seed is hex") from error
    if not _is_key_id(key_id) or len(body) != SEED_BYTES:
        raise MalformedKey(f"a key id is printable text and a seed is {SEED_BYTES} bytes")
    return key_id, body


def store_wire(store: TrustStore) -> dict[str, Any]:
    """The trust store as its JSON shape: the file a deployment pins."""
    return {
        "format": FORMAT_STORE,
        "policy": {
            "now": store.policy.now,
            "max_age": store.policy.max_age,
            "skew": store.policy.skew,
            "quorum": store.policy.quorum,
        },
        "keys": [
            {"key_id": key.key_id, "public": key.public.hex()} for key in store.keys
        ],
        "revoked": sorted(store.revoked),
    }


def store_of(data: object) -> TrustStore:
    """The trust store behind a parsed JSON shape; a refusal when there is none."""
    if not isinstance(data, dict) or data.get("format") != FORMAT_STORE:
        raise MalformedStore(f"not a store: the format is not {FORMAT_STORE}")
    fields = cast(dict[str, object], data)
    words, listed, revoked = fields.get("policy"), fields.get("keys", []), fields.get("revoked", [])
    if not isinstance(words, dict) or not isinstance(listed, list) or not isinstance(revoked, list):
        raise MalformedStore("a store carries a policy, a key list and a revocation list")
    policy = cast(dict[str, object], words)
    if not all(isinstance(policy.get(name), int) for name in ("now", "max_age", "skew", "quorum")):
        raise MalformedStore("the policy words are numbers")
    keys: list[TrustKey] = []
    seen: set[KeyId] = set()
    for entry in listed:
        if not isinstance(entry, dict):
            raise MalformedStore("every admitted voice is its key id and public word")
        item = cast(dict[str, object], entry)
        key_id, public = item.get("key_id"), item.get("public")
        if not isinstance(key_id, str) or not isinstance(public, str):
            raise MalformedStore("every admitted voice is its key id and public word")
        try:
            body = bytes.fromhex(public)
        except ValueError as error:
            raise MalformedStore("a public word is hex") from error
        if key_id in seen:
            raise MalformedStore(f"the store names the voice {key_id} twice")
        seen.add(key_id)
        keys.append(TrustKey(key_id, body))
    if not all(isinstance(name, str) for name in revoked):
        raise MalformedStore("a revocation names a key id")
    return TrustStore(
        Policy(cast(int, policy["now"]), cast(int, policy["max_age"]), cast(int, policy["skew"]), cast(int, policy["quorum"])),
        tuple(keys),
        frozenset(cast(list[str], revoked)),
    )
