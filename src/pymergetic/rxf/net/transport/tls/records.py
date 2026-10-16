"""The TLS 1.3 record layer (RFC 8446 §5): plaintext records, protected records,
and the inner-plaintext rule.

A record is `type(1) || legacy_record_version(2) || length(2) || fragment`
(§5.1). Everything a TLS 1.3 peer sends after the ServerHello is protected: the
fragment is AEAD output over `TLSInnerPlaintext = content || content_type ||
zeros`, and the AAD is the five-byte TLSCiphertext header (§5.2). The nonce is
the per-direction IV XOR the padded sequence number (§5.3), the sequence number
starting at 0 and advancing once per record.

`legacy_record_version` is 0x0303 for every record a TLS 1.3 implementation
generates except the initial ClientHello's, which may be 0x0301 (§5.1) — the
word "legacy" is honest: the version field of a TLS 1.3 record says nothing
about the payload.

Every primitive rides the frozen crypto card (`net/crypto.py`, ChaCha20-Poly1305
for the one suite this card speaks) — no Python crypto anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...crypto import Crypto
from ...schema import MessageTooLong
from .alerts import TlsAlert
from .keysched import TrafficKeys

# RFC 8446 §5.1: ContentType.
CONTENT_CHANGE_CIPHER_SPEC: Final = 20
CONTENT_ALERT: Final = 21
CONTENT_HANDSHAKE: Final = 22
CONTENT_APPLICATION_DATA: Final = 23

# RFC 8446 §5.1: legacy_record_version words.
LEGACY_VERSION: Final = 0x0303  # what every record but the first ClientHello carries
INITIAL_VERSION: Final = 0x0301  # the initial ClientHello record, for old middleboxes

MAX_PLAINTEXT: Final = 1 << 14  # 2^14 (§5.1)
MAX_CIPHERTEXT: Final = MAX_PLAINTEXT + 256  # the expansion limit of §5.1
TAG_LEN: Final = 16

HEAD_LEN: Final = 5


@dataclass(frozen=True)
class Record:
    """One wire record as it arrives (or leaves): header words plus the fragment."""

    content_type: int
    legacy_version: int
    fragment: bytes


def encode(content_type: int, fragment: bytes, legacy_version: int = LEGACY_VERSION) -> bytes:
    """One plaintext record on the wire (§5.1)."""
    if not 0 <= content_type <= 255:
        raise ValueError(f"not a content type: {content_type}")
    if len(fragment) > MAX_PLAINTEXT:
        raise MessageTooLong(f"a record fragment of {len(fragment)} bytes exceeds 2^14")
    return (
        bytes([content_type])
        + legacy_version.to_bytes(2, "big")
        + len(fragment).to_bytes(2, "big")
        + fragment
    )


def header(content_type: int, fragment_len: int, legacy_version: int = LEGACY_VERSION) -> bytes:
    """The five-byte TLSCiphertext header — the AEAD's AAD (§5.2)."""
    return (
        bytes([content_type])
        + legacy_version.to_bytes(2, "big")
        + fragment_len.to_bytes(2, "big")
    )


class RecordBuffer:
    """The incremental parser: feed bytes, take whole records, never over-read.

    A record the length field overstates is refused `MessageTooLong` before its
    bytes are read (§5.1's 2^14 + 256 ceiling).
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    def take(self) -> Record | None:
        """The next whole record, or None when one has not fully arrived."""
        if len(self._buffer) < HEAD_LEN:
            return None
        content_type = self._buffer[0]
        legacy_version = int.from_bytes(self._buffer[1:3], "big")
        length = int.from_bytes(self._buffer[3:5], "big")
        if length > MAX_CIPHERTEXT:
            raise TlsAlert(
                f"a record of {length} bytes exceeds 2^14 + 256 (RFC 8446 §5.1)",
                "record_overflow",
            )
        if len(self._buffer) < HEAD_LEN + length:
            return None
        fragment = bytes(self._buffer[HEAD_LEN : HEAD_LEN + length])
        del self._buffer[: HEAD_LEN + length]
        return Record(content_type, legacy_version, fragment)


class Protection:
    """One direction's record protection (§5.2): the AEAD and its sequence number.

    `seal` turns (content type, content) into a whole wire record; `open` takes
    a record back apart, stripping the zero padding and reading the real content
    type out of the inner plaintext. The keys are key material: there is no
    export face and no rendering that shows them.
    """

    __slots__ = ("_card", "_cipher", "_keys", "_seq")

    def __init__(self, card: Crypto, cipher: int, keys: TrafficKeys) -> None:
        self._card = card
        self._cipher = cipher
        self._keys = keys
        self._seq = 0

    @property
    def seq(self) -> int:
        """The sequence number the next record takes (§5.3)."""
        return self._seq

    def __repr__(self) -> str:
        return f"Protection(seq={self._seq})"

    def _nonce(self) -> bytes:
        padded = self._seq.to_bytes(12, "big")
        return bytes(a ^ b for a, b in zip(self._keys.iv, padded, strict=True))

    def seal(self, content_type: int, content: bytes, padding: int = 0) -> bytes:
        """One protected record: `content || content_type` (and padding) under the AEAD."""
        if padding < 0:
            raise ValueError("padding is a count of zero bytes")
        inner = content + bytes([content_type]) + bytes(padding)
        if len(inner) > MAX_CIPHERTEXT - TAG_LEN:
            raise MessageTooLong(f"an inner plaintext of {len(inner)} bytes cannot be sealed")
        body_len = len(inner) + TAG_LEN
        head = header(CONTENT_APPLICATION_DATA, body_len)
        sealed = self._card.aead_seal(
            self._cipher, self._keys.key, self._nonce(), head, inner
        )
        self._seq += 1
        return head + sealed

    def open(self, record: Record) -> tuple[int, bytes]:
        """One protected record back to (content type, content); padding dropped.

        The content type lives in the inner plaintext (§5.2), so a record whose
        sealed bytes do not verify tells nothing — it is `bad_record_mac`, and
        one whose inner plaintext carries no type is `decode_error`.
        """
        if record.content_type != CONTENT_APPLICATION_DATA:
            raise TlsAlert(
                "a protected record must be typed application_data (RFC 8446 §5.2)",
                "unexpected_message",
            )
        if len(record.fragment) < TAG_LEN:
            raise TlsAlert("a protected record shorter than one tag", "decode_error")
        head = header(
            CONTENT_APPLICATION_DATA, len(record.fragment), record.legacy_version
        )
        try:
            inner = self._card.aead_open(
                self._cipher,
                self._keys.key,
                self._nonce(),
                head,
                record.fragment,
            )
        except Exception as error:  # the card names the refusal; the wire says less
            raise TlsAlert(
                f"a record failed to verify: {error}", "bad_record_mac"
            ) from error
        self._seq += 1
        end = len(inner)
        while end > 0 and inner[end - 1] == 0:
            end -= 1
        if end == 0:
            raise TlsAlert(
                "an inner plaintext with no content type (RFC 8446 §5.2)",
                "decode_error",
            )
        return inner[end - 1], inner[: end - 1]
