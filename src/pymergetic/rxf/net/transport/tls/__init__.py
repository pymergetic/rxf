"""The TLS 1.3 card of the RXF net stack (docs/25 §11, §12): one card, both roles.

`TlsConfig.role` selects the role; the card installs through the stack's `wrap`
seam (`net.transport.stream.install_wrapper`), so a protocol that speaks the
`Stream` contract cannot tell a secured stream from a plain one. Target: RFC
8446 (TLS 1.3) only — TLS 1.2 and older, renegotiation, 0-RTT, PSK/resumption
and cookies are refused **by name** (docs/25 §17), each with a test in
`tests/test_net_tls.py`.

Suites (one implemented, two refused by name — a suite whose primitive the
frozen `net/crypto.py` card lacks is never faked):

- `TLS_CHACHA20_POLY1305_SHA256` (0x1303) — implemented: ChaCha20-Poly1305
  (card cipher id 0) + SHA-256 key schedule (card hash id 0).
- `TLS_AES_128_GCM_SHA256` (0x1301) — refused: the card's AEAD keys are fixed
  32 bytes and cannot hold AES-128-GCM's 16-byte key (seam request: AES-128
  key length on cap.crypto).
- `TLS_AES_256_GCM_SHA384` (0x1302) — refused: the card has no SHA-384, so the
  suite's transcript hash and key schedule cannot run (seam request: SHA-384 /
  HKDF-SHA384 on cap.crypto).

Signatures: Ed25519 only, the tree's one implementation in
`pymergetic.rxf.certify` (RFC 8032, vectors pinned). RSA-PSS / ECDSA are
refused by name (seam request: those schemes' primitives). Certificates are the
card's self-signed identity statements behind `Certificate` cells (`certs.py`);
X.509 and chain building are a named gap, never a guessed parse.

Every primitive — X25519, HKDF-SHA256, AEAD, SHA-256 — is a call into the
frozen `net/crypto.py` card; the signature is one call into `certify`. Nothing
cryptographic is reimplemented in Python, and key material has no export face.

Named gaps (honest, not silent): no post-handshake KeyUpdate; no client
certificates (mutual TLS); no X.509/chain building (the roots cell pins the
leaf's voice); no resumption or exporter secrets (never derived here); no
HelloRetryRequest cookie (stateless HRR is refused by name).
"""

from __future__ import annotations

from ...schema import TlsConfig
from ..stream import Stream, install_wrapper
from . import msgs, records
from .alerts import ALERTS, FATAL, WARNING, TlsAlert, alert_bytes, name_of
from .certs import (
    Identity,
    SecretValue,
    certificate_of,
    clear,
    parse_certificate,
    pinned,
    put_certificate,
    put_roots,
    roots_of,
    sign_certificate,
    verify_certificate,
)
from .handshake import TlsStream, Transcript
from .keysched import (
    CHACHA20_POLY1305_SHA256,
    SUPPORTED,
    TLS_AES_128_GCM_SHA256,
    TLS_AES_256_GCM_SHA384,
    TLS_CHACHA20_POLY1305_SHA256,
    Suite,
    suite_of,
)

__all__ = [
    "ALERTS",
    "CHACHA20_POLY1305_SHA256",
    "FATAL",
    "SUPPORTED",
    "TLS_AES_128_GCM_SHA256",
    "TLS_AES_256_GCM_SHA384",
    "TLS_CHACHA20_POLY1305_SHA256",
    "WARNING",
    "Identity",
    "SecretValue",
    "Suite",
    "TlsAlert",
    "TlsStream",
    "Transcript",
    "alert_bytes",
    "certificate_of",
    "clear",
    "install",
    "install_wrapper",
    "msgs",
    "name_of",
    "parse_certificate",
    "pinned",
    "put_certificate",
    "put_roots",
    "records",
    "roots_of",
    "sign_certificate",
    "suite_of",
    "verify_certificate",
    "wrap",
]


def wrap(stream: Stream, config: TlsConfig) -> Stream:
    """This card's fill of the §11 seam: a `TlsStream`, handshake run to completion.

    Protocol code calls `net.transport.stream.wrap` at construction and gets
    back a stream that already speaks TLS 1.3; nothing above ever learns which
    card is in between. The handshake is the composed blocking form (`step()`
    is the non-blocking face on `TlsStream` for callers that drive it).
    """
    secured = TlsStream(stream, config)
    secured.establish()
    return secured


def install() -> None:
    """Install this card on the stack's wrap seam (§11)."""
    install_wrapper(wrap)
