"""Gates for the TLS 1.3 card (RFC 8446): pinned vectors, self-talk, opacity, refusals.

Five gates, exactly as the card's build asked for:

(a) the key schedule pinned **byte-exact to RFC 8448 §3** (Simple 1-RTT Handshake):
    the early / handshake / master secrets, the derived traffic secrets, their
    traffic keys and IVs, and both Finished verify_data values are compared to
    the trace's own hex at every step — and the trace's fixed X25519 keys drive
    the frozen crypto card's key exchange. If RFC 8448 disagrees with this
    code, this code is wrong and the test says so.
(b) end-to-end self-talk: both roles of this card over the in-memory fills
    (`test_net_fill.MemoryPair`) complete the full 1-RTT and move data both
    ways — plus the HelloRetryRequest paths against scripted foreign peers
    (built from this card's own modules, never a second crypto).
(c) the opacity pin: the http codec runs over a wrapped stream and a plain one
    and cannot tell (identical protocol bytes; opaque wire bytes) — and the
    wrap seam (`stream.install_wrapper`) hands protocols an established stream.
(d) refusals by name: TLS 1.2 and older, renegotiation, 0-RTT, PSK/resumption,
    cookies, foreign suites and foreign signature schemes — each refused with
    the named alert (RFC 8446 §6) and the name in the message.
(e) secret cells never leak: `export()` refuses, `repr`/`str` show paths and
    cell words only, and no rendering of a live connection carries key bytes.

No sockets: every byte travels over in-memory fills. Threads only in the seam
test, bounded by joins; the fill itself refuses to spin past 1000 empty wakes.
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Iterator

import pytest

from pymergetic.rxftk.certify import ed25519
from pymergetic.rxftk.net.crypto import (
    CIPHER_CHACHA20_POLY1305,
    HASH_SHA256,
    Crypto,
    CryptoArgument,
)
from pymergetic.rxftk.net.schema import (
    NotSupported,
    PermissionDenied,
    Role,
    TlsConfig,
)
from pymergetic.rxftk.net.transport import tls as tls_card
from pymergetic.rxftk.net.transport.http import (
    Request,
    Response,
    read_request,
    read_response,
    write_request,
    write_response,
)
from pymergetic.rxftk.net.transport.stream import install_wrapper
from pymergetic.rxftk.net.transport.stream import wrap as seam_wrap
from pymergetic.rxftk.net.transport.tls import certs, handshake, keysched, msgs, records
from pymergetic.rxftk.net.transport.tls.alerts import TlsAlert

from .test_net_fill import MemoryPair, MemoryStream

# ---- RFC 8448 §3, "Simple 1-RTT Handshake": the trace, transcribed ----
# Every hex below is copied from RFC 8448 section 3; the assertion that uses it
# names the exact trace line. The trace's suite is TLS_AES_128_GCM_SHA256, so
# its traffic keys are 16 bytes; the key schedule itself is suite-agnostic and
# this card's suite (TLS_CHACHA20_POLY1305_SHA256) expands the same labels to
# 32-byte keys. The pins below drive this card's schedule with the trace's own
# inputs and key lengths where the trace names them.


def h(words: str) -> bytes:
    """Hex as the RFC prints it — whitespace allowed and ignored."""
    return bytes.fromhex("".join(words.split()))


# §3, "create an ephemeral x25519 key pair" (client and server).
CLIENT_PRIVATE = h(
    """49 af 42 ba 7f 79 94 85 2d 71 3e f2 78 4b cb ca a7 91 1d e2 6a
       dc 56 42 cb 63 45 40 e7 ea 50 05"""
)
CLIENT_PUBLIC = h(
    """99 38 1d e5 60 e4 bd 43 d2 3d 8e 43 5a 7d ba fe b3 c0 6e 51 c1 3c
       ae 4d 54 13 69 1e 52 9a af 2c"""
)
SERVER_PRIVATE = h(
    """b1 58 0e ea df 6d d5 89 b8 ef 4f 2d 56 52 57 8c c8 10 e9 98 01 91
       ec 8d 05 83 08 ce a2 16 a2 1e"""
)
SERVER_PUBLIC = h(
    """c9 82 88 76 11 20 95 fe 66 76 2b db f7 c6 72 e1 56 d6 cc 25 3b 83
       3d f1 dd 69 b1 b0 4e 75 1f 0f"""
)
# §3, "extract secret 'handshake'", IKM (32 octets): the shared secret.
SHARED_SECRET = h(
    """8b d4 05 4f b5 5b 9d 63 fd fb ac f9 f0 4b 9f 0d 35 e6 d6 3f 53 75
       63 ef d4 62 72 90 0f 89 49 2d"""
)

# §3, "construct a ClientHello" / "construct a ServerHello" (the messages).
CH_MESSAGE = h(
    """01 00 00 c0 03 03 cb 34 ec b1 e7 81 63 ba 1c 38 c6 da cb 19 6a 6d
       ff a2 1a 8d 99 12 ec 18 a2 ef 62 83 02 4d ec e7 00 00 06 13 01 13
       03 13 02 01 00 00 91 00 00 00 0b 00 09 00 00 06 73 65 72 76 65 72
       ff 01 00 01 00 00 0a 00 14 00 12 00 1d 00 17 00 18 00 19 01 00 01
       01 01 02 01 03 01 04 00 23 00 00 00 33 00 26 00 24 00 1d 00 20 99
       38 1d e5 60 e4 bd 43 d2 3d 8e 43 5a 7d ba fe b3 c0 6e 51 c1 3c ae
       4d 54 13 69 1e 52 9a af 2c 00 2b 00 03 02 03 04 00 0d 00 20 00 1e
       04 03 05 03 06 03 02 03 08 04 08 05 08 06 04 01 05 01 06 01 02 01
       04 02 05 02 06 02 02 02 00 2d 00 02 01 01 00 1c 00 02 40 01"""
)
SH_MESSAGE = h(
    """02 00 00 56 03 03 a6 af 06 a4 12 18 60 dc 5e 6e 60 24 9c d3 4c 95
       93 0c 8a c5 cb 14 34 da c1 55 77 2e d3 e2 69 28 00 13 01 00 00 2e
       00 33 00 24 00 1d 00 20 c9 82 88 76 11 20 95 fe 66 76 2b db f7 c6
       72 e1 56 d6 cc 25 3b 83 3d f1 dd 69 b1 b0 4e 75 1f 0f 00 2b 00 02
       03 04"""
)
EE_MESSAGE = h(
    """08 00 00 24 00 22 00 0a 00 14 00 12 00 1d 00 17 00 18 00 19 01 00
       01 01 01 02 01 03 01 04 00 1c 00 02 40 01 00 00 00 00"""
)
CERT_MESSAGE = h(
    """0b 00 01 b9 00 00 01 b5 00 01 b0 30 82 01 ac 30 82 01 15 a0 03 02
       01 02 02 01 02 30 0d 06 09 2a 86 48 86 f7 0d 01 01 0b 05 00 30 0e
       31 0c 30 0a 06 03 55 04 03 13 03 72 73 61 30 1e 17 0d 31 36 30 37
       33 30 30 31 32 33 35 39 5a 17 0d 32 36 30 37 33 30 30 31 32 33 35
       39 5a 30 0e 31 0c 30 0a 06 03 55 04 03 13 03 72 73 61 30 81 9f 30
       0d 06 09 2a 86 48 86 f7 0d 01 01 01 05 00 03 81 8d 00 30 81 89 02
       81 81 00 b4 bb 49 8f 82 79 30 3d 98 08 36 39 9b 36 c6 98 8c 0c 68
       de 55 e1 bd b8 26 d3 90 1a 24 61 ea fd 2d e4 9a 91 d0 15 ab bc 9a
       95 13 7a ce 6c 1a f1 9e aa 6a f9 8c 7c ed 43 12 09 98 e1 87 a8 0e
       e0 cc b0 52 4b 1b 01 8c 3e 0b 63 26 4d 44 9a 6d 38 e2 2a 5f da 43
       08 46 74 80 30 53 0e f0 46 1c 8c a9 d9 ef bf ae 8e a6 d1 d0 3e 2b
       d1 93 ef f0 ab 9a 80 02 c4 74 28 a6 d3 5a 8d 88 d7 9f 7f 1e 3f 02
       03 01 00 01 a3 1a 30 18 30 09 06 03 55 1d 13 04 02 30 00 30 0b 06
       03 55 1d 0f 04 04 03 02 05 a0 30 0d 06 09 2a 86 48 86 f7 0d 01 01
       0b 05 00 03 81 81 00 85 aa d2 a0 e5 b9 27 6b 90 8c 65 f7 3a 72 67
       17 06 18 a5 4c 5f 8a 7b 33 7d 2d f7 a5 94 36 54 17 f2 ea e8 f8 a5
       8c 8f 81 72 f9 31 9c f3 6b 7f d6 c5 5b 80 f2 1a 03 01 51 56 72 60
       96 fd 33 5e 5e 67 f2 db f1 02 70 2e 60 8c ca e6 be c1 fc 63 a4 2a
       99 be 5c 3e b7 10 7c 3c 54 e9 b9 eb 2b d5 20 3b 1c 3b 84 e0 a8 b2
       f7 59 40 9b a3 ea c9 d9 1d 40 2d cc 0c c8 f8 96 12 29 ac 91 87 b4
       2b 4d e1 00 00"""
)
CV_MESSAGE = h(
    """0f 00 00 84 08 04 00 80 5a 74 7c 5d 88 fa 9b d2 e5 5a b0 85 a6 10
       15 b7 21 1f 82 4c d4 84 14 5a b3 ff 52 f1 fd a8 47 7b 0b 7a bc 90
       db 78 e2 d3 3a 5c 14 1a 07 86 53 fa 6b ef 78 0c 5e a2 48 ee aa a7
       85 c4 f3 94 ca b6 d3 0b be 8d 48 59 ee 51 1f 60 29 57 b1 54 11 ac
       02 76 71 45 9e 46 44 5c 9e a5 8c 18 1e 81 8e 95 b8 c3 fb 0b f3 27
       84 09 d3 be 15 2a 3d a5 04 3e 06 3d da 65 cd f5 ae a2 0d 53 df ac
       d4 2f 74 f3"""
)
SERVER_FINISHED_MESSAGE = h(
    """14 00 00 20 9b 9b 14 1d 90 63 37 fb d2 cb dc e7 1d f4 de da 4a b4
       2c 30 95 72 cb 7f ff ee 54 54 b7 8f 07 18"""
)

# §3, "send handshake record" — the ClientHello's and ServerHello's records.
CH_RECORD = h(
    """16 03 01 00 c4 01 00 00 c0 03 03 cb 34 ec b1 e7 81 63 ba 1c 38 c6
       da cb 19 6a 6d ff a2 1a 8d 99 12 ec 18 a2 ef 62 83 02 4d ec e7 00
       00 06 13 01 13 03 13 02 01 00 00 91 00 00 00 0b 00 09 00 00 06 73
       65 72 76 65 72 ff 01 00 01 00 00 0a 00 14 00 12 00 1d 00 17 00 18
       00 19 01 00 01 01 01 02 01 03 01 04 00 23 00 00 00 33 00 26 00 24
       00 1d 00 20 99 38 1d e5 60 e4 bd 43 d2 3d 8e 43 5a 7d ba fe b3 c0
       6e 51 c1 3c ae 4d 54 13 69 1e 52 9a af 2c 00 2b 00 03 02 03 04 00
       0d 00 20 00 1e 04 03 05 03 06 03 02 03 08 04 08 05 08 06 04 01 05
       01 06 01 02 01 04 02 05 02 06 02 02 02 00 2d 00 02 01 01 00 1c 00
       02 40 01"""
)
SH_RECORD = h(
    """16 03 03 00 5a 02 00 00 56 03 03 a6 af 06 a4 12 18 60 dc 5e 6e 60
       24 9c d3 4c 95 93 0c 8a c5 cb 14 34 da c1 55 77 2e d3 e2 69 28 00
       13 01 00 00 2e 00 33 00 24 00 1d 00 20 c9 82 88 76 11 20 95 fe 66
       76 2b db f7 c6 72 e1 56 d6 cc 25 3b 83 3d f1 dd 69 b1 b0 4e 75 1f
       0f 00 2b 00 02 03 04"""
)

# §3, "extract secret 'early'" / "derive secret for handshake 'tls13 derived'".
EARLY_SECRET = h(
    """33 ad 0a 1c 60 7e c0 3b 09 e6 cd 98 93 68 0c e2 10 ad f3 00 aa 1f
       26 60 e1 b2 2e 10 f1 70 f9 2a"""
)
EMPTY_HASH = h(
    """e3 b0 c4 42 98 fc 1c 14 9a fb f4 c8 99 6f b9 24 27 ae 41 e4 64 9b
       93 4c a4 95 99 1b 78 52 b8 55"""
)
DERIVED_FOR_HANDSHAKE = h(
    """6f 26 15 a1 08 c7 02 c5 67 8f 54 fc 9d ba b6 97 16 c0 76 18 9c 48
       25 0c eb ea c3 57 6c 36 11 ba"""
)
# §3, "extract secret 'handshake'".
HANDSHAKE_SECRET = h(
    """1d c8 26 e9 36 06 aa 6f dc 0a ad c1 2f 74 1b 01 04 6a a6 b9 9f 69
       1e d2 21 a9 f0 ca 04 3f be ac"""
)
# §3, the hash of "derive secret 'tls13 c hs traffic'": CH..ServerHello.
TH_CH_SH = h(
    """86 0c 06 ed c0 78 58 ee 8e 78 f0 e7 42 8c 58 ed d6 b4 3f 2c a3 e6
       e9 5f 02 ed 06 3c f0 e1 ca d8"""
)
# §3, "derive secret 'tls13 c hs traffic'" / "'tls13 s hs traffic'".
C_HS_TRAFFIC = h(
    """b3 ed db 12 6e 06 7f 35 a7 80 b3 ab f4 5e 2d 8f 3b 1a 95 07 38 f5
       2e 96 00 74 6a 0e 27 a5 5a 21"""
)
S_HS_TRAFFIC = h(
    """b6 7b 7d 69 0c c1 6c 4e 75 e5 42 13 cb 2d 37 b4 e9 c9 12 bc de d9
       10 5d 42 be fd 59 d3 91 ad 38"""
)
# §3, "derive secret for master 'tls13 derived'" / "extract secret 'master'".
DERIVED_FOR_MASTER = h(
    """43 de 77 e0 c7 77 13 85 9a 94 4d b9 db 25 90 b5 31 90 a6 5b 3e e2
       e4 f1 2d d7 a0 bb 7c e2 54 b4"""
)
MASTER_SECRET = h(
    """18 df 06 84 3d 13 a0 8b f2 a4 49 84 4c 5f 8a 47 80 01 bc 4d 4c 62
       79 84 d5 a4 1d a8 d0 40 29 19"""
)
# §3, the hash of "derive secret 'tls13 c ap traffic'": CH..server Finished.
TH_THRU_SERVER_FINISHED = h(
    """96 08 10 2a 0f 1c cc 6d b6 25 0b 7b 7e 41 7b 1a 00 0e aa da 3d aa
       e4 77 7a 76 86 c9 ff 83 df 13"""
)
# §3, "derive secret 'tls13 c ap traffic'" / "'tls13 s ap traffic'".
C_AP_TRAFFIC = h(
    """9e 40 64 6c e7 9a 7f 9d c0 5a f8 88 9b ce 65 52 87 5a fa 0b 06 df
       00 87 f7 92 eb b7 c1 75 04 a5"""
)
S_AP_TRAFFIC = h(
    """a1 1a f9 f0 55 31 f8 56 ad 47 11 6b 45 a9 50 32 82 04 b4 f4 4b fb
       6b 3a 4b 4f 1f 3f cb 63 16 43"""
)

# §3, "derive write/read traffic keys ..." — the trace's suite keys 16 bytes.
S_HS_KEY = h("3f ce 51 60 09 c2 17 27 d0 f2 e4 e8 6e e4 03 bc")
S_HS_IV = h("5d 31 3e b2 67 12 76 ee 13 00 0b 30")
C_HS_KEY = h("db fa a6 93 d1 76 2c 5b 66 6a f5 d9 50 25 8d 01")
C_HS_IV = h("5b d3 c7 1b 83 6e 0b 76 bb 73 26 5f")
S_AP_KEY = h("9f 02 28 3b 6c 9c 07 ef c2 6b b9 f2 ac 92 e3 56")
S_AP_IV = h("cf 78 2b 88 dd 83 54 9a ad f1 e9 84")
C_AP_KEY = h("17 42 2d da 59 6e d5 d9 ac d8 90 e3 c6 3f 50 51")
C_AP_IV = h("5b 78 92 3d ee 08 57 90 33 e5 23 d9")

# §3, "calculate finished 'tls13 finished'" (server, then client).
S_FINISHED_KEY = h(
    """00 8d 3b 66 f8 16 ea 55 9f 96 b5 37 e8 85 c3 1f c0 68 bf 49 2c 65
       2f 01 f2 88 a1 d8 cd c1 9f c8"""
)
S_FINISHED_DATA = h(
    """9b 9b 14 1d 90 63 37 fb d2 cb dc e7 1d f4 de da 4a b4 2c 30 95 72
       cb 7f ff ee 54 54 b7 8f 07 18"""
)
C_FINISHED_KEY = h(
    """b8 0a d0 10 15 fb 2f 0b d6 5f f7 d4 da 5d 6b f8 3f 84 82 1d 1f 87
       fd c7 d3 c7 5b 5a 7b 42 d9 c4"""
)
C_FINISHED_DATA = h(
    """a8 ec 43 6d 67 76 34 ae 52 5a c1 fc eb e1 1a 03 9e c1 76 94 fa c6
       e9 85 27 b6 42 f2 ed d5 ce 61"""
)

ZERO32 = keysched.HASH_LEN * b"\x00"

# ---- the tests' cell material: one Ed25519 voice, one trust store ----

SEED = bytes(range(32))  # the voice's Ed25519 seed — key material, never exported
SUBJECT = "server.example"
KEY_ID = "server-voice"
ISSUED = 1000


@pytest.fixture(autouse=True)
def _vault() -> Iterator[None]:
    """Each test owns the cell vault (certs.py) and empties it again."""
    certs.clear()
    yield
    certs.clear()


def identity_cells() -> tuple[str, str]:
    """One Certificate cell and one roots cell for the tests' voice."""
    certs.clear()  # a test may ask for its cells more than once; one vault per ask
    key = certs.SecretValue("net/secret/test/tls/key", SEED)
    leaf = certs.sign_certificate(SUBJECT, KEY_ID, key, ISSUED)
    certs.put_certificate(
        "net/secret/test/tls/cert", certs.Identity("net/secret/test/tls/cert", leaf, key)
    )
    certs.put_roots(
        "net/secret/test/tls/roots",
        certs.pinned(KEY_ID, ed25519.public_key(SEED), now=ISSUED, max_age=10**6),
    )
    return "net/secret/test/tls/cert", "net/secret/test/tls/roots"


def make_configs(
    cert: str,
    roots: str,
    *,
    client_alpn: tuple[str, ...] = ("ftp",),
    server_alpn: tuple[str, ...] = ("http/1.1", "ftp"),
    server_name: str | None = SUBJECT,
    client_name: str | None = SUBJECT,
) -> tuple[TlsConfig, TlsConfig]:
    """One client and one server TlsConfig (docs/25 §11: one card, both roles)."""
    client = TlsConfig(
        path="net/tls/test/client",
        role=Role.CLIENT,
        certificates=(),
        roots=roots,
        alpn=client_alpn,
        server_name=client_name,
        min_version="1.3",
    )
    server = TlsConfig(
        path="net/tls/test/server",
        role=Role.SERVER,
        certificates=(cert,),
        roots=roots,
        alpn=server_alpn,
        server_name=server_name,
        min_version="1.3",
    )
    return client, server


def test_configs() -> None:
    """The TlsConfig words this card honors (docs/25 §11): role picks the side,
    ALPN and server_name travel in the hellos, and a config asking for TLS 1.2
    is refused by name at construction — the card speaks TLS 1.3 only."""
    cert, roots = identity_cells()
    client_cfg, server_cfg = make_configs(cert, roots)
    assert client_cfg.role is Role.CLIENT and server_cfg.role is Role.SERVER
    assert client_cfg.certificates == () and server_cfg.certificates == (cert,)
    assert client_cfg.alpn == ("ftp",) and "ftp" in server_cfg.alpn
    assert client_cfg.server_name == server_cfg.server_name == SUBJECT
    pair = MemoryPair("configs")
    client = tls_card.TlsStream(pair.client, client_cfg)
    server = tls_card.TlsStream(pair.server, server_cfg)
    pump(client, server)
    assert client.negotiated_alpn == "ftp"  # the overlap the hellos agreed on
    assert server.server_name == SUBJECT  # the SNI the client named (§4.2.1)

    with pytest.raises(NotSupported) as got:
        tls_card.TlsStream(pair.client, dataclasses.replace(client_cfg, min_version="1.2"))
    assert "refused by name" in str(got.value) and "TLS 1.3 only" in str(got.value)


def pump(*streams: handshake.TlsStream, rounds: int = 500) -> None:
    """Drive every side one step at a time until all are established — no threads,
    no hangs: a bounded number of rounds and the fill's own spin cap."""
    for _ in range(rounds):
        for one in streams:
            one.step()
        if all(one.established for one in streams):
            return
    raise AssertionError("the handshake did not complete — refused to hang")


def tls_pair() -> tuple[handshake.TlsStream, handshake.TlsStream, MemoryPair]:
    """Both roles of this card over one in-memory pair, handshake completed."""
    cert, roots = identity_cells()
    client_cfg, server_cfg = make_configs(cert, roots)
    pair = MemoryPair("tls")
    client = tls_card.TlsStream(pair.client, client_cfg)
    server = tls_card.TlsStream(pair.server, server_cfg)
    pump(client, server)
    return client, server, pair


# ---- (a) the key schedule, pinned to RFC 8448 §3 ----


def test_rfc8448_early_secret_and_derived_secret_pinned() -> None:
    """§3, "extract secret 'early'" and "derive secret for handshake 'tls13 derived'":
    HKDF-Extract(0, 0) and Derive-Secret over the empty hash, byte-exact."""
    card = keysched.card()
    early = card.hkdf_extract(HASH_SHA256, ZERO32, ZERO32)
    assert early == EARLY_SECRET
    empty = card.sha256(b"")
    assert empty == EMPTY_HASH
    assert keysched.derive_secret(card, early, "derived", empty) == DERIVED_FOR_HANDSHAKE


def test_rfc8448_x25519_drives_handshake_secret_pinned() -> None:
    """§3, the key pairs and "extract secret 'handshake'": the trace's fixed keys
    drive the frozen card's X25519, and the shared secret chains the schedule."""
    card = keysched.card()
    assert card.x25519_public(CLIENT_PRIVATE) == CLIENT_PUBLIC
    assert card.x25519_public(SERVER_PRIVATE) == SERVER_PUBLIC
    shared = card.x25519(CLIENT_PRIVATE, SERVER_PUBLIC)
    assert shared == SHARED_SECRET
    derived = keysched.derive_secret(card, EARLY_SECRET, "derived", EMPTY_HASH)
    assert card.hkdf_extract(HASH_SHA256, derived, shared) == HANDSHAKE_SECRET


def test_rfc8448_handshake_traffic_secrets_pinned() -> None:
    """§3, "derive secret 'tls13 c hs traffic'" / "'tls13 s hs traffic'": over the
    transcript hash of CH..ServerHello ("hash (32 octets): 86 0c 06 ed ...")."""
    card = keysched.card()
    assert card.sha256(CH_MESSAGE + SH_MESSAGE) == TH_CH_SH
    transcript = handshake.Transcript(card)
    transcript.update(CH_MESSAGE)
    transcript.update(SH_MESSAGE)
    assert transcript.hash() == TH_CH_SH  # the card's Transcript, the RFC's hash
    assert keysched.derive_secret(card, HANDSHAKE_SECRET, "c hs traffic", TH_CH_SH) == (
        C_HS_TRAFFIC
    )
    assert keysched.derive_secret(card, HANDSHAKE_SECRET, "s hs traffic", TH_CH_SH) == (
        S_HS_TRAFFIC
    )


def test_rfc8448_master_and_application_secrets_pinned() -> None:
    """§3, "derive secret for master", "extract secret 'master'", and the
    "tls13 c ap traffic" / "tls13 s ap traffic" pair over CH..server Finished."""
    card = keysched.card()
    assert keysched.derive_secret(card, HANDSHAKE_SECRET, "derived", EMPTY_HASH) == (
        DERIVED_FOR_MASTER
    )
    master = card.hkdf_extract(HASH_SHA256, DERIVED_FOR_MASTER, ZERO32)
    assert master == MASTER_SECRET
    thru = card.sha256(
        CH_MESSAGE
        + SH_MESSAGE
        + EE_MESSAGE
        + CERT_MESSAGE
        + CV_MESSAGE
        + SERVER_FINISHED_MESSAGE
    )
    assert thru == TH_THRU_SERVER_FINISHED
    assert keysched.derive_secret(card, MASTER_SECRET, "c ap traffic", thru) == C_AP_TRAFFIC
    assert keysched.derive_secret(card, MASTER_SECRET, "s ap traffic", thru) == S_AP_TRAFFIC


def test_rfc8448_traffic_keys_and_ivs_pinned() -> None:
    """§3, "derive write/read traffic keys for handshake/application data": the
    "tls13 key" and "tls13 iv" expands at the trace's own key lengths."""
    card = keysched.card()
    for secret, key, iv in (
        (S_HS_TRAFFIC, S_HS_KEY, S_HS_IV),
        (C_HS_TRAFFIC, C_HS_KEY, C_HS_IV),
        (S_AP_TRAFFIC, S_AP_KEY, S_AP_IV),
        (C_AP_TRAFFIC, C_AP_KEY, C_AP_IV),
    ):
        keys = keysched.traffic_keys(card, secret, len(key), len(iv))
        assert keys == keysched.TrafficKeys(key=key, iv=iv)


def test_rfc8448_finished_keys_and_verify_data_pinned() -> None:
    """§3, "calculate finished 'tls13 finished'": the "tls13 finished" expand and
    the verify_data MAC (RFC 8446 §4.4.4: HMAC(finished_key, Transcript-Hash)).

    The trace prints "hash (0 octets): (empty)" beside the expand — that is the
    expand's empty context; the MAC input is the Transcript-Hash the trace names,
    and the values below land byte-exact only under §4.4.4's construction."""
    card = keysched.card()
    server_key = keysched.finished_key(card, S_HS_TRAFFIC)
    assert server_key == S_FINISHED_KEY
    thru_cv = card.sha256(CH_MESSAGE + SH_MESSAGE + EE_MESSAGE + CERT_MESSAGE + CV_MESSAGE)
    assert keysched.verify_data(card, server_key, thru_cv) == S_FINISHED_DATA
    client_key = keysched.finished_key(card, C_HS_TRAFFIC)
    assert client_key == C_FINISHED_KEY
    assert keysched.verify_data(card, client_key, TH_THRU_SERVER_FINISHED) == C_FINISHED_DATA


def test_rfc8448_record_framing_pins() -> None:
    """§3, "send handshake record": the ClientHello and ServerHello records are
    framed and re-framed byte-exact by the record layer (§5.1's two-byte legacy
    version: 0x0301 on the first hello's record, 0x0303 on the rest)."""
    for wire, message, legacy in (
        (CH_RECORD, CH_MESSAGE, records.INITIAL_VERSION),
        (SH_RECORD, SH_MESSAGE, records.LEGACY_VERSION),
    ):
        buffer = records.RecordBuffer()
        buffer.feed(wire)
        record = buffer.take()
        assert record is not None and buffer.take() is None
        assert record.content_type == records.CONTENT_HANDSHAKE
        assert record.legacy_version == legacy
        assert record.fragment == message
        assert records.encode(records.CONTENT_HANDSHAKE, message, legacy) == wire


def test_rfc8448_protected_records_are_not_faked() -> None:
    """The trace's protected records ride TLS_AES_128_GCM_SHA256's 16-byte keys;
    the frozen card's AEAD keys are 32 bytes, so those records are refused by the
    card rather than opened by a fake (named gap in the report)."""
    card = keysched.card()
    with pytest.raises(CryptoArgument):
        card.aead_open(CIPHER_CHACHA20_POLY1305, b"\x00" * 16, b"\x00" * 12, b"", b"\x00" * 16)


# ---- (b) end-to-end self-talk: both roles over the in-memory fills ----


def test_self_talk_full_1rtt_and_data_both_ways() -> None:
    """A full 1-RTT handshake between the card's own client and server roles, then
    application data in both directions — ALPN and SNI negotiated along the way."""
    client, server, pair = tls_pair()
    assert client.established and server.established
    assert client.wrapped and server.wrapped
    assert client.negotiated_alpn == server.negotiated_alpn == "ftp"
    assert client.server_name == SUBJECT
    assert server.server_name == SUBJECT
    assert client.handle == pair.client.handle  # §12: the handle of the socket under it

    assert client.write(b"ping from the client") == len(b"ping from the client")
    assert server.read(64) == b"ping from the client"
    assert server.write(b"pong from the server") == len(b"pong from the server")
    assert client.read(64) == b"pong from the server"
    # Reads are honest about sizing: 2 bytes asked, 2 bytes given.
    assert server.write(b"half") == 4
    assert client.read(2) == b"ha"
    assert client.read(2) == b"lf"


def test_self_talk_records_hide_the_application_bytes() -> None:
    """The application bytes never touch the wire: the records carry ciphertext
    and the inner content type, never the plaintext (§5.2)."""
    client, server, pair = tls_pair()
    client.write(b"the app bytes")
    wire = bytes(pair.client.sent)
    assert b"the app bytes" not in wire
    assert server.read(64) == b"the app bytes"
    # The record that carried it is typed application_data on the wire (§5.2).
    buffer = records.RecordBuffer()
    buffer.feed(wire)
    while (record := buffer.take()) is not None:
        if record.content_type == records.CONTENT_APPLICATION_DATA:
            assert b"the app bytes" not in record.fragment
            break
    else:
        raise AssertionError("no application_data record on the wire")


def test_self_talk_close_is_close_notify_not_a_hang() -> None:
    """One side closing puts close_notify (§6.1) on the wire and the other side
    reads EOF — b'' is EOF, never a confusion with WouldBlock (§12)."""
    client, server, _ = tls_pair()
    client.write(b"last words")
    client.close()
    assert server.read(64) == b"last words"
    assert server.read(64) == b""


# ---- (c) the opacity pin: the protocol cannot tell ----


def test_http_codec_cannot_tell_tls_from_plain() -> None:
    """The http codec over this card's wrapped stream and over a plain stream: the
    protocol sees the same bytes either way, and the wrapped wire is opaque."""
    request = Request(
        method="GET",
        target="/asset",
        headers=(("host", SUBJECT), ("accept", "*/*")),
    )
    response = Response(
        status=200,
        reason="OK",
        body=b"hi",
        headers=(("Content-Length", "2"),),  # its framing header, as the wire carries it
    )

    plain = MemoryPair("plain")
    write_request(plain.client, request)
    write_response(plain.server, response)

    client, server, pair = tls_pair()
    write_request(client, request)
    write_response(server, response)

    assert read_request(plain.server) == read_request(server) == request
    assert read_response(plain.client) == read_response(client) == response
    # The wire is not the same bytes: TLS records, not the codec's lines.
    assert bytes(pair.client.sent) != bytes(plain.client.sent)
    assert b"GET /asset" not in bytes(pair.client.sent)


def test_wrap_seam_establishes_for_protocols() -> None:
    """The card installs through `stream.install_wrapper` (§11): protocol code
    calls `wrap(stream, config)` and gets back a stream already speaking TLS —
    driven here in bounded threads because `wrap` runs the handshake to done."""
    cert, roots = identity_cells()
    client_cfg, server_cfg = make_configs(cert, roots)
    pair = MemoryPair("seam")
    outcome: dict[str, object] = {}

    def run(name: str, end: MemoryStream, config: TlsConfig) -> None:
        try:
            outcome[name] = seam_wrap(end, config)
        except BaseException as error:  # noqa: BLE001 — handed back to the main thread
            outcome[name] = error

    install_wrapper(tls_card.wrap)
    try:
        threads = [
            threading.Thread(
                target=run, args=("client", pair.client, client_cfg), daemon=True
            ),
            threading.Thread(
                target=run, args=("server", pair.server, server_cfg), daemon=True
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
    finally:
        install_wrapper(None)
    assert all(not thread.is_alive() for thread in threads), "the seam handshake hung"
    for name in ("client", "server"):
        assert not isinstance(outcome[name], BaseException), outcome[name]
    secured_client, secured_server = outcome["client"], outcome["server"]
    assert isinstance(secured_client, handshake.TlsStream)
    assert isinstance(secured_server, handshake.TlsStream)
    assert secured_client.established and secured_server.established
    secured_client.write(b"through the seam")
    assert secured_server.read(64) == b"through the seam"


# ---- HelloRetryRequest (§4.1.4), against scripted foreign peers ----


def test_hrr_transcript_rule_pins() -> None:
    """RFC 8446 §4.4.1: the HRR transcript replaces ClientHello1 with
    `message_hash || 00 00 Hash.length || Hash(ClientHello1)` — the card's
    `fold_message_hash` is that construction and nothing else."""
    card = keysched.card()
    ch1 = msgs.encode_handshake(msgs.CLIENT_HELLO, b"\x01\x02\x03")
    assert msgs.message_hash_message(card.sha256(ch1)) == (
        bytes([msgs.MESSAGE_HASH]) + b"\x00\x00\x20" + card.sha256(ch1)
    )
    transcript = handshake.Transcript(card)
    transcript.update(ch1)
    transcript.fold_message_hash()
    folded = handshake.Transcript(card)
    folded.update(msgs.message_hash_message(card.sha256(ch1)))
    assert transcript.hash() == folded.hash()


def test_hrr_client_round_trip_with_scripted_peer() -> None:
    """The client role through a full HelloRetryRequest round trip against a
    scripted foreign server (this card's own modules play the far side — one
    crypto, one schedule, one record layer): CH1, HRR, CH2 with a fresh share,
    then the 1-RTT flight and application data both ways. §4.1.4, §4.4.1."""
    card = keysched.card()
    cert, roots = identity_cells()
    client_cfg, _ = make_configs(cert, roots)
    pair = MemoryPair("hrr")
    client = tls_card.TlsStream(pair.client, client_cfg)
    transcript = handshake.Transcript(card)

    # The scripted server reads CH1 and answers with a HelloRetryRequest.
    ch1 = _handshake_message_of(_records_in(pair.client.sent), msgs.CLIENT_HELLO)
    hello1 = msgs.decode_client_hello(ch1[4:])
    transcript.update(ch1)
    transcript.fold_message_hash()  # §4.4.1
    hrr = msgs.encode_handshake(
        msgs.SERVER_HELLO,
        msgs.encode_server_hello(
            msgs.ServerHello(
                legacy_version=msgs.LEGACY_HELLO_VERSION,
                random=msgs.HELLO_RETRY_REQUEST_RANDOM,
                session_id=hello1.session_id,
                cipher_suite=keysched.TLS_CHACHA20_POLY1305_SHA256,
                extensions=(
                    (
                        msgs.EXT_SUPPORTED_VERSIONS,
                        msgs.encode_supported_versions_server(msgs.TLS_1_3),
                    ),
                    (msgs.EXT_KEY_SHARE, msgs.encode_key_share_hrr(msgs.GROUP_X25519)),
                ),
            )
        ),
    )
    transcript.update(hrr)
    pair.client.feed(
        records.encode(records.CONTENT_HANDSHAKE, hrr, records.LEGACY_VERSION)
        + records.encode(
            records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
        )
    )
    client.step()  # the HelloRetryRequest
    client.step()  # the dummy change_cipher_spec (D.4)

    # The second hello keeps the words and replaces the share (§4.1.4).
    ch2 = _handshake_message_of(_records_in(pair.client.sent), msgs.CLIENT_HELLO, second=True)
    hello2 = msgs.decode_client_hello(ch2[4:])
    assert hello2.random == hello1.random
    assert hello2.session_id == hello1.session_id
    share1 = msgs.decode_key_share_client(
        msgs.extension(hello1.extensions, msgs.EXT_KEY_SHARE) or b""
    )
    share2 = msgs.decode_key_share_client(
        msgs.extension(hello2.extensions, msgs.EXT_KEY_SHARE) or b""
    )
    assert share2 != share1  # a fresh share, never the refused one again

    # The scripted server completes the 1-RTT over the folded transcript.
    transcript.update(ch2)
    server_scalar = b"\x42" * 32
    server_public = card.x25519_public(server_scalar)
    _, client_share = share2[0]
    sh = msgs.encode_handshake(
        msgs.SERVER_HELLO,
        msgs.encode_server_hello(
            msgs.ServerHello(
                legacy_version=msgs.LEGACY_HELLO_VERSION,
                random=b"\x24" * 32,
                session_id=hello1.session_id,
                cipher_suite=keysched.TLS_CHACHA20_POLY1305_SHA256,
                extensions=(
                    (
                        msgs.EXT_SUPPORTED_VERSIONS,
                        msgs.encode_supported_versions_server(msgs.TLS_1_3),
                    ),
                    (
                        msgs.EXT_KEY_SHARE,
                        msgs.encode_key_share_server(msgs.GROUP_X25519, server_public),
                    ),
                ),
            )
        ),
    )
    transcript.update(sh)
    handshake_root, master_root = _schedule_roots(
        card, card.x25519(server_scalar, client_share)
    )
    th_sh = transcript.hash()
    c_hs = keysched.derive_secret(card, handshake_root, "c hs traffic", th_sh)
    s_hs = keysched.derive_secret(card, handshake_root, "s hs traffic", th_sh)

    ee = msgs.encode_handshake(
        msgs.ENCRYPTED_EXTENSIONS,
        msgs.encode_encrypted_extensions(
            ((msgs.EXT_ALPN, msgs.encode_alpn_selected("ftp")),)
        ),
    )
    transcript.update(ee)
    leaf = certs.certificate_of(cert).leaf
    cert_m = msgs.encode_handshake(
        msgs.CERTIFICATE,
        msgs.encode_certificate(b"", (msgs.CertificateEntry(leaf, ()),)),
    )
    transcript.update(cert_m)
    content = (
        b"\x20" * 64 + b"TLS 1.3, server CertificateVerify" + b"\x00" + transcript.hash()
    )
    cv = msgs.encode_handshake(
        msgs.CERTIFICATE_VERIFY,
        msgs.encode_certificate_verify(
            msgs.CertificateVerify(msgs.SCHEME_ED25519, ed25519.sign(SEED, content))
        ),
    )
    transcript.update(cv)
    s_fin = keysched.verify_data(card, keysched.finished_key(card, s_hs), transcript.hash())
    fin = msgs.encode_handshake(msgs.FINISHED, msgs.encode_finished(s_fin))
    transcript.update(fin)
    th_fin = transcript.hash()
    c_fin_expected = keysched.verify_data(card, keysched.finished_key(card, c_hs), th_fin)
    c_ap = keysched.derive_secret(card, master_root, "c ap traffic", th_fin)
    s_ap = keysched.derive_secret(card, master_root, "s ap traffic", th_fin)

    flight = records.Protection(
        card, CIPHER_CHACHA20_POLY1305, keysched.traffic_keys(card, s_hs, 32, 12)
    )
    pair.client.feed(
        records.encode(records.CONTENT_HANDSHAKE, sh, records.LEGACY_VERSION)
        + records.encode(
            records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
        )
        + flight.seal(records.CONTENT_HANDSHAKE, ee + cert_m + cv + fin)
    )
    client.step()  # the ServerHello
    client.step()  # the dummy change_cipher_spec (D.4)
    client.step()  # the encrypted flight — and the client's Finished answers it
    assert client.established
    assert client.negotiated_alpn == "ftp"

    # The scripted server verifies the client's Finished (under the hs keys).
    incoming = _records_in(pair.server.inbox)
    finished_record = next(
        record
        for record in incoming
        if record.content_type == records.CONTENT_APPLICATION_DATA
    )
    c_hs_records = records.Protection(
        card, CIPHER_CHACHA20_POLY1305, keysched.traffic_keys(card, c_hs, 32, 12)
    )
    opened_type, opened = c_hs_records.open(finished_record)
    assert opened_type == records.CONTENT_HANDSHAKE
    assert opened == msgs.encode_handshake(msgs.FINISHED, c_fin_expected)

    # ... and its application data (under the ap keys), both ways.
    client.write(b"over the retry")
    c_ap_records = records.Protection(
        card, CIPHER_CHACHA20_POLY1305, keysched.traffic_keys(card, c_ap, 32, 12)
    )
    app_records = [
        record
        for record in _records_in(pair.server.inbox)
        if record.content_type == records.CONTENT_APPLICATION_DATA
    ]
    assert c_ap_records.open(app_records[-1]) == (
        records.CONTENT_APPLICATION_DATA,
        b"over the retry",
    )

    s_ap_records = records.Protection(
        card, CIPHER_CHACHA20_POLY1305, keysched.traffic_keys(card, s_ap, 32, 12)
    )
    pair.client.feed(s_ap_records.seal(records.CONTENT_APPLICATION_DATA, b"back over it"))
    assert client.read(64) == b"back over it"


def test_hrr_server_selects_group_and_answers_hrr() -> None:
    """The server role's HRR move: a hello whose only share is for another group
    is answered with the §4.1.3 HelloRetryRequest (the magic random names it)."""
    pair, server = _server_under_test()
    pair.server.feed(_foreign_share_hello_wire())
    server.step()
    hrr = _handshake_message_of(_records_in(pair.server.sent), msgs.SERVER_HELLO)
    hello = msgs.decode_server_hello(hrr[4:])
    assert hello.retry_request()  # the magic random of §4.1.3
    assert hello.random == h(
        """cf 21 ad 74 e5 9a 61 11 be 1d 8c 02 1e 65 b8 91 c2 a2 11 16 7a
           bb 8c 5e 07 9e 09 e2 c8 a8 33 9c"""
    )
    assert (
        msgs.decode_key_share_hrr(msgs.extension(hello.extensions, msgs.EXT_KEY_SHARE) or b"")
        == msgs.GROUP_X25519
    )
    assert not server.established


def test_hrr_server_refuses_a_second_hello_without_the_share() -> None:
    """§4.1.4: a second ClientHello that still carries no share for the selected
    group is `illegal_parameter`, named as such."""
    pair, server = _server_under_test()
    pair.server.feed(_foreign_share_hello_wire())
    server.step()
    pair.server.feed(_foreign_share_hello_wire())
    with pytest.raises(TlsAlert) as got:
        server.step()
    assert got.value.alert == "illegal_parameter"
    assert "second ClientHello" in str(got.value)


def test_hrr_server_refuses_a_changed_random() -> None:
    """§4.1.4: the second hello is the first one's echo — a changed random is
    `illegal_parameter`, named as such."""
    pair, server = _server_under_test()
    pair.server.feed(_foreign_share_hello_wire())
    server.step()
    pair.server.feed(_hello_wire(random=b"\x77" * 32))
    with pytest.raises(TlsAlert) as got:
        server.step()
    assert got.value.alert == "illegal_parameter"
    assert "random" in str(got.value)


# ---- (d) refusals by name (docs/25 §17), each with its alert (§6) ----


def client_hello(
    *,
    legacy_version: int = msgs.LEGACY_HELLO_VERSION,
    random: bytes = b"\x22" * 32,
    suites: tuple[int, ...] = (keysched.TLS_CHACHA20_POLY1305_SHA256,),
    schemes: tuple[int, ...] = (msgs.SCHEME_ED25519,),
    versions: tuple[int, ...] = (msgs.TLS_1_3,),
    shares: tuple[tuple[int, bytes], ...] = ((msgs.GROUP_X25519, b"\x11" * 32),),
    extra: tuple[tuple[int, bytes], ...] = (),
    drop: tuple[int, ...] = (),
) -> msgs.ClientHello:
    """A well-formed hello with the words the test chooses — the shape a foreign
    peer would put on the wire."""
    listed: list[tuple[int, bytes]] = []
    if msgs.EXT_SUPPORTED_VERSIONS not in drop and versions:
        listed.append(
            (msgs.EXT_SUPPORTED_VERSIONS, msgs.encode_supported_versions_client(versions))
        )
    if msgs.EXT_SUPPORTED_GROUPS not in drop:
        listed.append((msgs.EXT_SUPPORTED_GROUPS, msgs.encode_groups((msgs.GROUP_X25519,))))
    if msgs.EXT_SIGNATURE_ALGORITHMS not in drop:
        listed.append((msgs.EXT_SIGNATURE_ALGORITHMS, msgs.encode_schemes(schemes)))
    if msgs.EXT_KEY_SHARE not in drop and shares:
        listed.append((msgs.EXT_KEY_SHARE, msgs.encode_key_share_client(shares)))
    listed.extend(extra)
    return msgs.ClientHello(legacy_version, random, b"\x33" * 32, suites, tuple(listed))


def _hello_wire(**words: object) -> bytes:
    """One crafted ClientHello record on the wire."""
    hello = client_hello(**words)  # type: ignore[arg-type]
    body = msgs.encode_client_hello(hello)
    return records.encode(
        records.CONTENT_HANDSHAKE, msgs.encode_handshake(msgs.CLIENT_HELLO, body)
    )


def _foreign_share_hello_wire() -> bytes:
    """A hello whose only key share is for a group this card does not run: the
    §4.2.8 shape that asks the server to select and retry."""
    return _hello_wire(shares=((0x0017, b"\x12" * 32),))  # secp256r1, not ours


def _server_under_test() -> tuple[MemoryPair, handshake.TlsStream]:
    """A server-role card over one in-memory pair, waiting for its ClientHello."""
    cert, roots = identity_cells()
    _, server_cfg = make_configs(cert, roots)
    pair = MemoryPair("refuse")
    return pair, tls_card.TlsStream(pair.server, server_cfg)


def refuse_server(hello: msgs.ClientHello) -> TlsAlert:
    """Feed one crafted hello to a real server card; hand back the refusal it names."""
    pair, server = _server_under_test()
    body = msgs.encode_client_hello(hello)
    pair.server.feed(
        records.encode(
            records.CONTENT_HANDSHAKE, msgs.encode_handshake(msgs.CLIENT_HELLO, body)
        )
    )
    with pytest.raises(TlsAlert) as got:
        server.step()
    return got.value


def test_refusal_tls12_client_hello_by_name() -> None:
    """A TLS 1.2 hello (no supported_versions) — `protocol_version`, naming TLS 1.2."""
    alert = refuse_server(client_hello(drop=(msgs.EXT_SUPPORTED_VERSIONS,)))
    assert alert.alert == "protocol_version"
    assert "TLS 1.2" in str(alert)


def test_refusal_tls12_and_older_legacy_version() -> None:
    """A hello whose legacy_version speaks TLS 1.2 or older — named, never parsed on."""
    alert = refuse_server(client_hello(legacy_version=0x0302))
    assert alert.alert == "protocol_version"
    assert "TLS 1.2" in str(alert) and "0x0302" in str(alert)


def test_refusal_early_data_by_name() -> None:
    """0-RTT: the `early_data` extension is a named refusal (docs/25 §17)."""
    alert = refuse_server(client_hello(extra=((msgs.EXT_EARLY_DATA, b""),)))
    assert alert.alert == "handshake_failure"
    assert "early_data (0-RTT)" in str(alert)


@pytest.mark.parametrize(
    "extension, name",
    [
        (msgs.EXT_PRE_SHARED_KEY, "pre_shared_key (PSK / session resumption)"),
        (msgs.EXT_SESSION_TICKET, "session_ticket (session resumption)"),
        (msgs.EXT_PSK_KEY_EXCHANGE_MODES, "psk_key_exchange_modes (PSK / session resumption)"),
    ],
)
def test_refusal_psk_and_resumption_by_name(extension: int, name: str) -> None:
    """PSK and resumption modes this card does not implement — named refusals."""
    alert = refuse_server(client_hello(extra=((extension, b"\x00"),)))
    assert alert.alert == "handshake_failure"
    assert name in str(alert)


def test_refusal_cookie_by_name() -> None:
    """Stateless HelloRetryRequest cookies are refused by name, not ignored."""
    alert = refuse_server(client_hello(extra=((msgs.EXT_COOKIE, b"\x00\x00"),)))
    assert alert.alert == "handshake_failure"
    assert "cookie (stateless HelloRetryRequest)" in str(alert)


@pytest.mark.parametrize(
    "scheme, name",
    [
        (0x0804, "rsa_pss_rsae_sha256"),
        (0x0403, "ecdsa_secp256r1_sha256"),
    ],
)
def test_refusal_foreign_signature_schemes_by_name(scheme: int, name: str) -> None:
    """RSA-PSS and ECDSA are refused by name: the tree signs with ed25519 and
    nothing else (seam request: those schemes' primitives)."""
    alert = refuse_server(client_hello(schemes=(scheme,)))
    assert alert.alert == "handshake_failure"
    assert name in str(alert) and "ed25519" in str(alert)


@pytest.mark.parametrize(
    "suite, name",
    [
        (keysched.TLS_AES_128_GCM_SHA256, "TLS_AES_128_GCM_SHA256"),
        (keysched.TLS_AES_256_GCM_SHA384, "TLS_AES_256_GCM_SHA384"),
        (0xC02F, "cipher suite 0xc02f"),
    ],
)
def test_refusal_unrunnable_suites_by_name(suite: int, name: str) -> None:
    """A suite whose primitive the frozen card lacks is refused by name, never
    faked — and an unknown suite names its code point."""
    alert = refuse_server(client_hello(suites=(suite,)))
    assert alert.alert == "handshake_failure"
    assert name in str(alert)


def test_refusal_alpn_no_overlap_by_name() -> None:
    """RFC 7301: no overlapping ALPN is `no_application_protocol`, named."""
    cert, roots = identity_cells()
    server_cfg = make_configs(
        cert, roots, client_alpn=("ftp",), server_alpn=("smtp",)
    )[1]
    pair = MemoryPair("alpn")
    server = tls_card.TlsStream(pair.server, server_cfg)
    client_cfg = make_configs(cert, roots, client_alpn=("ftp",), server_alpn=("smtp",))[0]
    tls_card.TlsStream(pair.client, client_cfg)  # its hello is all the server needs
    with pytest.raises(TlsAlert) as got:
        server.step()
    assert got.value.alert == "no_application_protocol"
    assert "ALPN" in str(got.value)


def test_refusal_sni_mismatch_by_name() -> None:
    """§4.2.1: a client that names another host than this server answers for is
    refused `unrecognized_name`, named."""
    cert, roots = identity_cells()
    server_cfg = make_configs(cert, roots)[1]
    pair = MemoryPair("sni")
    server = tls_card.TlsStream(pair.server, server_cfg)
    client_cfg = make_configs(cert, roots, client_name="other.example")[0]
    tls_card.TlsStream(pair.client, client_cfg)
    with pytest.raises(TlsAlert) as got:
        server.step()
    assert got.value.alert == "unrecognized_name"
    assert "other.example" in str(got.value)


def test_refusal_tls12_server_hello_by_name() -> None:
    """A ServerHello that answers with anything but TLS 1.3 — `protocol_version`,
    naming TLS 1.2 and older, on the client side too."""
    cert, roots = identity_cells()
    client_cfg = make_configs(cert, roots)[0]
    pair = MemoryPair("old-sh")
    client = tls_card.TlsStream(pair.client, client_cfg)
    ch1 = _handshake_message_of(_records_in(pair.client.sent), msgs.CLIENT_HELLO)
    hello = msgs.decode_client_hello(ch1[4:])
    share = (msgs.EXT_KEY_SHARE, msgs.encode_key_share_server(msgs.GROUP_X25519, b"\x55" * 32))
    for extensions in (
        (share,),  # no supported_versions at all
        (share, (msgs.EXT_SUPPORTED_VERSIONS, msgs.encode_supported_versions_server(0x0303))),
    ):
        body = msgs.encode_server_hello(
            msgs.ServerHello(
                legacy_version=msgs.LEGACY_HELLO_VERSION,
                random=b"\x24" * 32,
                session_id=hello.session_id,
                cipher_suite=keysched.TLS_CHACHA20_POLY1305_SHA256,
                extensions=extensions,
            )
        )
        pair.client.feed(
            records.encode(
                records.CONTENT_HANDSHAKE, msgs.encode_handshake(msgs.SERVER_HELLO, body)
            )
        )
        with pytest.raises(TlsAlert) as got:
            client.step()
        assert got.value.alert == "protocol_version"
        assert "TLS 1.2" in str(got.value)


def test_refusal_renegotiation_by_name() -> None:
    """Any handshake message after the handshake — renegotiation, refused by name
    with `unexpected_message` (TLS 1.3 has no renegotiation to accept)."""
    client, _, pair = tls_pair()
    pair.client.feed(
        records.encode(
            records.CONTENT_HANDSHAKE,
            msgs.encode_handshake(msgs.FINISHED, b"\x00" * 32),
        )
    )
    with pytest.raises(TlsAlert) as got:
        client.step()
    assert got.value.alert == "unexpected_message"
    assert "renegotiation is refused by name" in str(got.value)


def test_refusal_new_session_ticket_by_name() -> None:
    """NewSessionTicket is the resumption door — refused by name after the
    handshake (PSK/resumption are not implemented in this build)."""
    client, _, pair = tls_pair()
    pair.client.feed(
        records.encode(
            records.CONTENT_HANDSHAKE,
            msgs.encode_handshake(msgs.NEW_SESSION_TICKET, b"\x00\x00\x00\x05hello"),
        )
    )
    with pytest.raises(TlsAlert) as got:
        client.step()
    assert got.value.alert == "unexpected_message"
    assert "NewSessionTicket (session resumption) is refused by name" in str(got.value)


def test_refusal_zero_rtt_early_data_by_name() -> None:
    """Application data before the handshake is done is 0-RTT early data —
    refused by name with `unexpected_message`."""
    pair, server = _server_under_test()
    pair.server.feed(records.encode(records.CONTENT_APPLICATION_DATA, b"\x00" * 8))
    with pytest.raises(TlsAlert) as got:
        server.step()
    assert got.value.alert == "unexpected_message"
    assert "0-RTT early data is refused by name" in str(got.value)


# ---- (e) secret cells never leak ----


def test_secret_cell_export_refused_and_never_rendered() -> None:
    """A `Secret` cell's material: export refuses `PermissionDenied` (§17), and
    no rendering carries a byte of it."""
    key = certs.SecretValue("net/secret/test/tls/key", SEED)
    with pytest.raises(PermissionDenied) as got:
        key.export()
    assert "refused by name" in str(got.value)
    for rendered in (repr(key), str(key), repr(key.cell)):
        assert SEED.hex() not in rendered
        assert "net/secret/test/tls/key" in rendered


def test_identity_and_stream_renderings_leak_no_keys() -> None:
    """Identity and a live connection render paths and words — never key bytes,
    never secret bytes (the wire carries ciphertext only)."""
    cert, _roots = identity_cells()
    identity = certs.certificate_of(cert)
    assert SEED.hex() not in repr(identity) + str(identity)
    client, server, pair = tls_pair()
    assert SEED.hex() not in repr(client) + repr(server)
    wire = bytes(pair.client.sent) + bytes(pair.server.sent)
    assert SEED not in wire


# ---- the scripted foreign peer's shared words (one card's modules) ----


def _records_in(sent: bytearray) -> list[records.Record]:
    """Every whole record on one side's wire."""
    buffer = records.RecordBuffer()
    buffer.feed(bytes(sent))
    out: list[records.Record] = []
    while (record := buffer.take()) is not None:
        out.append(record)
    return out


def _handshake_message_of(
    listed: list[records.Record], msg_type: int, *, second: bool = False
) -> bytes:
    """The full message (type || uint24 length || body) of one type out of the records."""
    found: list[bytes] = []
    for record in listed:
        if record.content_type != records.CONTENT_HANDSHAKE:
            continue
        fragment = record.fragment
        while len(fragment) >= 4:
            length = int.from_bytes(fragment[1:4], "big")
            if fragment[0] == msg_type:
                found.append(fragment[: 4 + length])
            fragment = fragment[4 + length :]
    return found[1] if second else found[0]


def _schedule_roots(card: Crypto, shared: bytes) -> tuple[bytes, bytes]:
    """The scripted peer's schedule roots (RFC 8446 §7.1) over the card's own
    primitives: the Handshake Secret and the Master Secret."""
    derived = keysched.derive_secret(card, EARLY_SECRET, "derived", EMPTY_HASH)
    handshake_root = card.hkdf_extract(HASH_SHA256, derived, shared)
    master_derived = keysched.derive_secret(card, handshake_root, "derived", EMPTY_HASH)
    master_root = card.hkdf_extract(HASH_SHA256, master_derived, ZERO32)
    return handshake_root, master_root
