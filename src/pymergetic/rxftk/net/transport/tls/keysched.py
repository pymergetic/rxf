"""The TLS 1.3 key schedule (RFC 8446 §7.1) over the frozen crypto card.

One card, one key schedule: every primitive here is a call into `net/crypto.py`
(HKDF-SHA256, hash id 0) — nothing is reimplemented in Python. The schedule is
the RFC's exactly: HKDF-Extract chains (early / handshake / master secrets),
Derive-Secret over Transcript-Hash, and HKDF-Expand-Label for the traffic keys
("tls13 key" / "tls13 iv") and the Finished keys ("tls13 finished").

The Finished MAC is HMAC(finished_key, Transcript-Hash). The card exposes no
standalone HMAC; RFC 5869 defines HKDF-Extract(salt, IKm) as HMAC(salt, IKm), so
the Finished MAC goes through `card.hkdf_extract` — same bytes, one
implementation. `tests/test_net_tls.py` pins that against RFC 8448's Finished
values byte for byte.

Suites (the honest list; each is what the frozen card can actually run):

- `TLS_CHACHA20_POLY1305_SHA256` (0x1303) — the suite this card speaks.
  ChaCha20-Poly1305 (cipher id 0) + SHA-256 key schedule (hash id 0): every
  primitive exists on the card.

Refused by name (a suite whose primitive is missing is never faked):

- `TLS_AES_128_GCM_SHA256` (0x1301) — refused: the card's AEAD keys are fixed
  32 bytes (`pm_crypto` KEY_LEN); AES-128-GCM's 16-byte key is refused by the
  frozen ABI. Seam request: AES-128 (or a key-length word) on cap.crypto.
- `TLS_AES_256_GCM_SHA384` (0x1302) — refused: the card has no SHA-384, so the
  suite's transcript hash and HKDF-SHA384 key schedule cannot run. Seam
  request: SHA-384 / HKDF-SHA384 on cap.crypto.
- anything else — refused by name as an unknown suite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...crypto import (
    CIPHER_CHACHA20_POLY1305,
    HASH_SHA256,
    Crypto,
    CryptoArgument,
    load,
)
from ...schema import NotSupported

HASH_LEN: Final = 32
KEY_LEN: Final = 32
IV_LEN: Final = 12

# The cipher suite words of RFC 8446 §8.4 (IANA registry).
TLS_AES_128_GCM_SHA256: Final = 0x1301
TLS_AES_256_GCM_SHA384: Final = 0x1302
TLS_CHACHA20_POLY1305_SHA256: Final = 0x1303

_REFUSED: Final = {
    TLS_AES_128_GCM_SHA256: (
        "TLS_AES_128_GCM_SHA256 (0x1301) is refused by name: the frozen crypto card's "
        "AEAD keys are 32 bytes and cannot hold AES-128-GCM's 16-byte key "
        "(seam request: AES-128 key length on cap.crypto)"
    ),
    TLS_AES_256_GCM_SHA384: (
        "TLS_AES_256_GCM_SHA384 (0x1302) is refused by name: the frozen crypto card "
        "has no SHA-384, so the suite's transcript hash and key schedule cannot run "
        "(seam request: SHA-384 / HKDF-SHA384 on cap.crypto)"
    ),
}


@dataclass(frozen=True)
class Suite:
    """One cipher suite's honest words: the card ids and the key sizes."""

    code: int
    name: str
    cipher: int  # the card's cipher id
    key_len: int
    iv_len: int
    hash: int  # the card's hash id (the key schedule's Hash)


CHACHA20_POLY1305_SHA256: Final = Suite(
    code=TLS_CHACHA20_POLY1305_SHA256,
    name="TLS_CHACHA20_POLY1305_SHA256",
    cipher=CIPHER_CHACHA20_POLY1305,
    key_len=KEY_LEN,
    iv_len=IV_LEN,
    hash=HASH_SHA256,
)

# The one suite this card completes a handshake with.
SUPPORTED: Final = (CHACHA20_POLY1305_SHA256,)
OFFERED: Final = tuple(suite.code for suite in SUPPORTED)


def suite_of(code: int) -> Suite:
    """The suite behind a code point — or a refusal that names it, never a guess."""
    for suite in SUPPORTED:
        if suite.code == code:
            return suite
    reason = _REFUSED.get(code)
    if reason is not None:
        raise NotSupported(reason)
    raise NotSupported(
        f"cipher suite 0x{code:04x} is refused by name: this card implements "
        f"TLS_CHACHA20_POLY1305_SHA256 (0x1303) only"
    )


_shared: Crypto | None = None


def card() -> Crypto:
    """The shared crypto card (built on first use; one card per process)."""
    global _shared
    if _shared is None:
        _shared = load()
    return _shared


def hmac_sha256(card_: Crypto, key: bytes, message: bytes) -> bytes:
    """HMAC-SHA256(key, message) through the card, never through Python.

    RFC 5869 defines HKDF-Extract(salt, IKM) as HMAC(salt, IKM); the card's
    `hkdf_extract` with hash id 0 is therefore exactly HMAC-SHA256. The Finished
    verify_data (RFC 8446 §4.4.4) rides this.
    """
    return card_.hkdf_extract(HASH_SHA256, key, message)


def expand_label(
    card_: Crypto, secret: bytes, label: str, context: bytes, length: int
) -> bytes:
    """HKDF-Expand-Label (RFC 8446 §7.1): the one label-shaped expand of TLS 1.3.

    HkdfLabel = length(2) || "tls13 " + label || context, each length-prefixed.
    """
    name = ("tls13 " + label).encode("ascii")
    if len(name) > 255 or len(context) > 255:
        raise CryptoArgument("an HkdfLabel label or context is at most 255 bytes")
    info = (
        length.to_bytes(2, "big")
        + bytes([len(name)])
        + name
        + bytes([len(context)])
        + context
    )
    return card_.hkdf_expand(HASH_SHA256, secret, info, length)


def derive_secret(card_: Crypto, secret: bytes, label: str, transcript_hash: bytes) -> bytes:
    """Derive-Secret (RFC 8446 §7.1) — a Hash-length expand over the transcript."""
    return expand_label(card_, secret, label, transcript_hash, HASH_LEN)


@dataclass(frozen=True)
class TrafficKeys:
    """One direction's record keys: the "key" and "iv" expands of §7.1."""

    key: bytes
    iv: bytes


def traffic_keys(card_: Crypto, secret: bytes, key_len: int, iv_len: int) -> TrafficKeys:
    """The write keys of one traffic secret (sizes come from the suite)."""
    return TrafficKeys(
        key=expand_label(card_, secret, "key", b"", key_len),
        iv=expand_label(card_, secret, "iv", b"", iv_len),
    )


def finished_key(card_: Crypto, base: bytes) -> bytes:
    """The Finished key (RFC 8446 §4.4.4): HKDF-Expand-Label(base, "finished")."""
    return expand_label(card_, base, "finished", b"", HASH_LEN)


def verify_data(card_: Crypto, key: bytes, transcript_hash: bytes) -> bytes:
    """The Finished MAC: HMAC(key, Transcript-Hash), via HKDF-Extract (§4.4.4)."""
    return hmac_sha256(card_, key, transcript_hash)
