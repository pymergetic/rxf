"""Pins for the WireGuard device card (docs/25 §11): one `WireGuardDevice` is server
AND client at once (the roles are configuration, not modes) and the whitepaper's
Noise IK lands byte-exact.

Vector sources of record, cited at each test:

- **The official Noise test vectors** — the cacophony corpus linked from the
  noise_wiki "Test-vectors" page
  (https://raw.githubusercontent.com/centromere/cacophony/master/vectors/cacophony.txt),
  vector `Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s`: the handshake pattern the WireGuard
  kernel names exactly (`noise.c`: "This implements Noise_IKpsk2: <- s; -> e, es, s,
  ss, {t}; <- e, ee, se, psk, {}"). It fixes the statics, the ephemerals, the psk and
  the message bytes at every stage, plus the transport message sequence — designed for
  exactly the deterministic ephemeral injection this device's seams provide.
- **The official cross-implementation driver** — wireguard-tools
  `contrib/external-tests/python/main.py`: the WireGuard wire wrapper around that
  pattern — the framing (`b'\x01' + b'\x00' * 3 + struct.pack('<i', sender)`), the mac1
  construction (`blake2s(msg, digest_size=16, key=blake2s(b'mac1----' +
  their_public).digest())`), mac2 zeros without a cookie, the response's
  `their_index, our_index = struct.unpack('<ii', response_packet[4:12])`, the transport
  header `struct.pack('<iq', their_index, counter)` with the ping at counter 0 and the
  keepalive at counter 1, and the TAI64N packing
  (`struct.pack('!qi', 4611686018427387914 + int(now.timestamp()),
  int(now.microsecond * 1e3))`).
- **The kernel's key schedule** (wireguard `noise.c`): `mix_psk` is
  `(chaining_key, τ, κ) := Kdf3(ck, psk)` with `h := Hash(h‖τ)` and the AEAD under κ —
  the whitepaper §5.4.3's order, pinned here through the corpus's psk-bearing vector.

The corpus's trace carries its own 8-byte prologue ("John Galt") and a 16-byte first
payload; WireGuard's prologue is its fixed identifier and its first payload is the
12-byte TAI64N timestamp, so the two wrappers are not the same bytes. The key
schedule and the transport faces are common ground and are pinned against the corpus
byte-exact (walked below through the module's own `_kdf`/`_mix_hash`/`transport_seal`
faces with the corpus's fixed keys and psk); the WireGuard wrapper is pinned against
the driver's constructions with the device's own deterministic seams, and the
device's e fields — which the wrapper does not touch — are compared byte-exact to the
corpus's message bodies with the corpus's ephemerals injected. Nothing here is
invented, and no vector is generated from this implementation.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable

import pytest

from pymergetic.rxftk.net import crypto, schema
from pymergetic.rxftk.net.link import wireguard
from pymergetic.rxftk.net.link.wireguard import (
    COOKIE_REPLY_SIZE,
    INITIATION_SIZE,
    RESPONSE_SIZE,
    ReplayWindow,
    SecretValue,
    WireGuardDevice,
    routes,
    tai64n,
    transport_open,
    transport_seal,
)
from pymergetic.rxftk.net.schema import (
    Endpoint,
    Interval,
    InvalidEndpoint,
    IPPrefix,
    MalformedMessage,
    NoRoute,
    NotConnected,
    NotSupported,
    PermissionDenied,
    Secret,
    StaleHandle,
    UnexpectedReply,
    WouldBlock,
)
from pymergetic.rxftk.net.transport.datagram import MemoryDatagrams

# ---- the corpus (centromere/cacophony vectors/cacophony.txt, noise_wiki Test-vectors):
# the Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s vector, hex verbatim ----

CORPUS_PROLOGUE = bytes.fromhex("4a6f686e2047616c74")  # "John Galt" — the corpus's prologue
CORPUS_PSK = bytes.fromhex("54686973206973206d7920417573747269616e20706572737065637469766521")
CORPUS_INIT_STATIC = bytes.fromhex("e61ef9919cde45dd5f82166404bd08e38bceb5dfdfded0a34c8df7ed542214d1")
CORPUS_INIT_EPHEMERAL = bytes.fromhex("893e28b9dc6ca8d611ab664754b8ceb7bac5117349a4439a6b0569da977c464a")
CORPUS_INIT_REMOTE_STATIC = bytes.fromhex("31e0303fd6418d2f8c0e78b91f22e8caed0fbe48656dcf4767e4834f701b8f62")
CORPUS_RESP_STATIC = bytes.fromhex("4a3acbfdb163dec651dfa3194dece676d437029c62a408b4c5ea9114246e4893")
CORPUS_RESP_EPHEMERAL = bytes.fromhex("bbdb4cdbd309f1a1f2e1456967fe288cadd6f712d65dc7b7793d5e63da6b375b")
CORPUS_HANDSHAKE_HASH = bytes.fromhex("f5191b875290abcd41347ac3622d9679688a7e980229cb937ef748336cfde0e5")
# (payload, ciphertext) per message: 1 and 2 are the handshake bodies,
# 3-6 are the transport message sequence (initiator, responder, initiator, responder).
CORPUS_MESSAGES: tuple[tuple[bytes, bytes], ...] = (
    (
        bytes.fromhex("4c756477696720766f6e204d69736573"),
        bytes.fromhex(
            "ca35def5ae56cec33dc2036731ab14896bc4c75dbb07a61f879f8e3afa4c7944"
            "001e21de9f98ddd8e2ad57527207feb56253c9c94a9e496782ecfcb2a75fbcaf"
            "1b52948cc48daefe660c62119ab5000980c84831215f2441eba616548e8329854"
            "64cf17e51ee93109008399a21f7e13f"
        ),
    ),
    (
        bytes.fromhex("4d757272617920526f746862617264"),
        bytes.fromhex(
            "95ebc60d2b1fa672c1f46a8aa265ef51bfe38e7ccb39ec5be34069f144808843"
            "cb765f2caef0751b8f007572dab0322217755c0632f365717edbf34d33e87a"
        ),
    ),
    (
        bytes.fromhex("462e20412e20486179656b"),
        bytes.fromhex("8153ca9833bc3c1b91a7e66e5f4d4f5b59bf9e64c2f20d15f0bba7"),
    ),
    (
        bytes.fromhex("4361726c204d656e676572"),
        bytes.fromhex("07af0c9c86e1b4e80f36b04ff7688d51141af3debd0332f0a705ef"),
    ),
    (
        bytes.fromhex("4a65616e2d426170746973746520536179"),
        bytes.fromhex("6ab1467c0448cc78394494abaaf23afce0e234315d6e2624dcbfa8a21c1c4d073d"),
    ),
    (
        bytes.fromhex("457567656e2042f6686d20766f6e2042617765726b"),
        bytes.fromhex("dfc346c0d2296ae6cf1acf6f12b8456a1dba228cf8d8b774aacf1c47fc53aa80ebc7a4c292"),
    ),
)

# ---- the two-device fill: fixed identities on one in-memory datagram wire ----

LEFT_PRIVATE = b"\x11" * 32
RIGHT_PRIVATE = b"\x22" * 32
PSK_MATERIAL = b"\x33" * 32
LEFT_TEN = b"\x0a\x00\x00\x01"  # 10.0.0.1 — left's inner address
RIGHT_TEN = b"\x0a\x00\x00\x02"  # 10.0.0.2 — right's inner address
LEFT_LOCAL = Endpoint(b"\x00\x00\x00\x00", 51820, None)
RIGHT_LOCAL = Endpoint(b"\x00\x00\x00\x00", 51821, None)
RIGHT_HOME = Endpoint(b"\x0a\x00\x00\x02", 51821, None)  # left's configured peer endpoint
LEFT_HOME = Endpoint(b"\x0a\x00\x00\x01", 51820, None)  # right's configured peer endpoint
FIXED_TIMESTAMP = tai64n(1_700_000_000, 7)  # one fixed TAI64N through the seam
FIXED_NOW = 1_700_000_000


def _inner(source: bytes, destination: bytes, body: bytes = b"payload") -> bytes:
    """One inner IPv4 packet — the tunnel carries L3 (DeviceKind.WIREGUARD)."""
    total = 20 + len(body)
    return struct.pack("!BBHHHBBH", 0x45, 0, total, 0, 0, 64, 1, 0) + source + destination + body


def _device(
    config: schema.WireGuardDevice,
    wire: MemoryDatagrams,
    secrets: list[tuple[str, bytes]],
    *,
    ephemerals: Iterable[bytes],
    indexes: Iterable[int],
    timestamp: bytes = FIXED_TIMESTAMP,
    now: int = FIXED_NOW,
) -> WireGuardDevice:
    """One device over the fill, with the constructor's deterministic seams (named
    features — ephemeral_provider / index_provider / timestamp_provider / clock)."""
    ephemeral = iter(ephemerals)
    index = iter(indexes)
    return WireGuardDevice(
        config,
        wire,
        secrets=[SecretValue(path, material) for path, material in secrets],
        ephemeral_provider=lambda: next(ephemeral),
        index_provider=lambda: next(index),
        timestamp_provider=lambda: timestamp,
        clock=lambda: now,
    )


def _two_devices(
    *,
    psk: bool = False,
    left_ephemerals: Iterable[bytes] = (b"\x51" * 32,),
    right_ephemerals: Iterable[bytes] = (b"\x61" * 32,),
    left_indexes: Iterable[int] = (0x11110001, 0x11110002),
    right_indexes: Iterable[int] = (0x22220001, 0x22220002),
    timestamp: bytes = FIXED_TIMESTAMP,
    now: int = FIXED_NOW,
) -> tuple[MemoryDatagrams, WireGuardDevice, WireGuardDevice]:
    """Two devices on one wire: left serves 51820 and lives at 10.0.0.1, right serves
    51821 and lives at 10.0.0.2; each is the other's peer with one allowed_ips prefix.
    Both carry `listen_port` and a peer `endpoint` — the roles are configuration."""
    card = crypto.load()
    left_public = card.x25519_public(LEFT_PRIVATE).hex()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    psk_cells = (Secret("net/secret/wg/psk"), Secret("net/secret/wg/psk")) if psk else (None, None)
    left_config = schema.WireGuardDevice(
        path="net/dev/wg0",
        device="net/dev/wg0",
        private_key=Secret("net/secret/wg0"),
        listen_port=51820,
        peers=(
            schema.WireGuardPeer(
                path=f"net/wg/wg0/peers/{right_public}",
                public_key=right_public,
                preshared=psk_cells[0],
                endpoint=RIGHT_HOME,
                allowed_ips=(IPPrefix(RIGHT_TEN, 32),),
                keepalive=None,
                last_handshake=None,
            ),
        ),
    )
    right_config = schema.WireGuardDevice(
        path="net/dev/wg1",
        device="net/dev/wg1",
        private_key=Secret("net/secret/wg1"),
        listen_port=51821,
        peers=(
            schema.WireGuardPeer(
                path=f"net/wg/wg1/peers/{left_public}",
                public_key=left_public,
                preshared=psk_cells[1],
                endpoint=LEFT_HOME,
                allowed_ips=(IPPrefix(LEFT_TEN, 32),),
                keepalive=None,
                last_handshake=None,
            ),
        ),
    )
    wire = MemoryDatagrams()
    left_secrets = [("net/secret/wg0", LEFT_PRIVATE)]
    right_secrets = [("net/secret/wg1", RIGHT_PRIVATE)]
    if psk:
        left_secrets.append(("net/secret/wg/psk", PSK_MATERIAL))
        right_secrets.append(("net/secret/wg/psk", PSK_MATERIAL))
    left = _device(left_config, wire, left_secrets, ephemerals=left_ephemerals, indexes=left_indexes, timestamp=timestamp, now=now)
    right = _device(right_config, wire, right_secrets, ephemerals=right_ephemerals, indexes=right_indexes, timestamp=timestamp, now=now)
    return wire, left, right


def _shake_hands(wire: MemoryDatagrams, left: WireGuardDevice, right: WireGuardDevice, right_public: str) -> None:
    """One full handshake over the fill: initiation, response, both sides complete."""
    left.initiate(right_public)
    right.pump()  # consumes the initiation, answers with the response
    left.pump()  # consumes the response — both sides hold a session


# ---- the corpus pins -------------------------------------------------------


def test_the_corpus_handshake_is_byte_exact_at_every_stage() -> None:
    """(a) The official handshake vectors byte-exact: the `Noise_IKpsk2_25519_
    ChaChaPoly_BLAKE2s` vector of the cacophony corpus (noise_wiki Test-vectors), at
    every stage — `-> e, es, s, ss, {t}` and `<- e, ee, se, psk, {}` (kernel noise.c's
    comment) — walked through the device module's own key-schedule faces with the
    corpus's fixed statics, fixed ephemerals and psk. The derived handshake hash is
    the corpus's `handshake_hash`; the derived transport keys are pinned by the
    corpus's transport message sequence below."""
    card = crypto.load()
    assert card.x25519_public(CORPUS_RESP_STATIC) == CORPUS_INIT_REMOTE_STATIC  # the corpus's remote static
    message_1, message_2, handshake_hash, _, _ = _corpus_walk(card)
    assert message_1 == CORPUS_MESSAGES[0][1]
    assert message_2 == CORPUS_MESSAGES[1][1]
    assert handshake_hash == CORPUS_HANDSHAKE_HASH


def test_the_corpus_transport_message_sequence_round_trips_with_matching_keys_and_counters() -> None:
    """(b) The corpus's transport message sequence byte-exact: messages 3-6 seal under
    the derived `(Tsend_i, Tsend_r)` at counters 0 and 1 (each side's own counter) and
    read back through the device's `transport_open` — the keys and counters match the
    corpus or the ciphertexts could not land."""
    card = crypto.load()
    _, _, _, send_i, send_r = _corpus_walk(card)
    for (payload, ciphertext), key, counter in (
        (CORPUS_MESSAGES[2], send_i, 0),
        (CORPUS_MESSAGES[3], send_r, 0),
        (CORPUS_MESSAGES[4], send_i, 1),
        (CORPUS_MESSAGES[5], send_r, 1),
    ):
        assert transport_seal(key, counter, payload) == ciphertext
        assert transport_open(key, counter, ciphertext) == payload


def _corpus_walk(card: crypto.Crypto) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """The corpus's IKpsk2 trace walked with the device module's own faces —
    `wireguard._kdf`, `wireguard._mix_hash`, `wireguard.transport_seal`, and the card's
    X25519/AEAD (the primitives are never reimplemented). The psk step is the kernel's
    `mix_psk`: `(ck, τ, κ) := Kdf3(ck, psk)`, `h := Hash(h‖τ)`, AEAD under κ.
    Returns (message 1, message 2, handshake hash, Tsend_i, Tsend_r)."""
    responder_public = card.x25519_public(CORPUS_RESP_STATIC)
    initiator_public = card.x25519_public(CORPUS_INIT_STATIC)
    first_payload, second_payload = CORPUS_MESSAGES[0][0], CORPUS_MESSAGES[1][0]
    # -> e, es, s, ss, {t}
    chain_key = card.blake2s(wireguard.CONSTRUCTION)
    hash_ = wireguard._mix_hash(card.blake2s(chain_key + CORPUS_PROLOGUE), responder_public)
    ephemeral_i = card.x25519_public(CORPUS_INIT_EPHEMERAL)
    hash_ = wireguard._mix_hash(hash_, ephemeral_i)
    chain_key = wireguard._kdf(chain_key, ephemeral_i)[0]
    chain_key, key, _ = wireguard._kdf(chain_key, card.x25519(CORPUS_INIT_EPHEMERAL, responder_public))
    enc_static = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, wireguard._ZERO_NONCE, hash_, initiator_public)
    hash_ = wireguard._mix_hash(hash_, enc_static)
    chain_key, key, _ = wireguard._kdf(chain_key, card.x25519(CORPUS_INIT_STATIC, responder_public))
    enc_first = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, wireguard._ZERO_NONCE, hash_, first_payload)
    hash_ = wireguard._mix_hash(hash_, enc_first)
    message_1 = ephemeral_i + enc_static + enc_first
    # <- e, ee, se, psk, {}
    ephemeral_r = card.x25519_public(CORPUS_RESP_EPHEMERAL)
    hash_ = wireguard._mix_hash(hash_, ephemeral_r)
    chain_key = wireguard._kdf(chain_key, ephemeral_r)[0]
    chain_key = wireguard._kdf(chain_key, card.x25519(CORPUS_RESP_EPHEMERAL, ephemeral_i))[0]  # ee
    chain_key = wireguard._kdf(chain_key, card.x25519(CORPUS_RESP_EPHEMERAL, initiator_public))[0]  # se
    chain_key, tau, key = wireguard._kdf(chain_key, CORPUS_PSK)  # psk — the kernel's mix_psk
    hash_ = wireguard._mix_hash(hash_, tau)
    enc_second = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, wireguard._ZERO_NONCE, hash_, second_payload)
    hash_ = wireguard._mix_hash(hash_, enc_second)
    message_2 = ephemeral_r + enc_second
    send_i, send_r, _ = wireguard._kdf(chain_key, b"")  # §5.4.5's split
    return message_1, message_2, hash_, send_i, send_r


def test_the_device_emits_the_corpus_ephemerals_verbatim() -> None:
    """(a) The device's wire bodies at the stages the corpus fixes: with the corpus's
    fixed ephemeral keys injected through the `ephemeral_provider` seam, the
    initiation's unencrypted ephemeral and the response's are byte-exact to the
    corpus's message-1 and message-2 bodies (those fields are outside the wrapper's
    prologue/payload width)."""
    card = crypto.load()
    wire, left, right = _two_devices(
        left_ephemerals=(CORPUS_INIT_EPHEMERAL,),
        right_ephemerals=(CORPUS_RESP_EPHEMERAL,),
    )
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    initiation, response = wire.delivered[0].payload, wire.delivered[1].payload
    assert initiation[8:40] == CORPUS_MESSAGES[0][1][:32]  # e — byte-exact to the corpus
    assert response[12:44] == CORPUS_MESSAGES[1][1][:32]  # e of the response


def test_the_initiation_and_response_are_the_official_drivers_wire_format() -> None:
    """(a) The full wire messages through the device's seams, compared field by field
    to the official cross-implementation driver (wireguard-tools
    contrib/external-tests/python/main.py): `b'\x01' + b'\x00' * 3 +
    struct.pack('<i', 28)` over `noise.write_message(payload=tai)`, then
    `blake2s(initiation_packet, digest_size=16, key=mac_key)` and `b'\x00' * 16`;
    the response asserts `our_index == 28` at `response_packet[4:12]`."""
    card = crypto.load()
    wire, left, right = _two_devices(left_indexes=(28,), right_indexes=(77,))
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    right_bytes = card.x25519_public(RIGHT_PRIVATE)
    left.initiate(right_public)
    initiation = wire.delivered[0].payload
    assert len(initiation) == INITIATION_SIZE == 148
    assert initiation[:4] == b"\x01\x00\x00\x00"  # driver: b'\x01' + three reserved zero bytes
    assert initiation[4:8] == struct.pack("<i", 28)  # driver: Sender index: 28
    assert initiation[8:40] == card.x25519_public(b"\x51" * 32)  # the injected ephemeral
    assert len(initiation[88:116]) == 28  # driver: noise.write_message(payload=tai) — a 12-byte tai sealed
    mac_key = hashlib.blake2s(b"mac1----" + right_bytes).digest()  # driver: mac_key
    assert initiation[116:132] == hashlib.blake2s(initiation[:116], digest_size=16, key=mac_key).digest()
    assert initiation[132:148] == b"\x00" * 16  # driver: mac2 without a cookie
    right.pump()
    response = wire.delivered[1].payload
    assert len(response) == RESPONSE_SIZE == 92
    assert response[:4] == b"\x02\x00\x00\x00"
    assert response[4:8] == struct.pack("<i", 77)  # their_index
    assert response[8:12] == struct.pack("<i", 28)  # our_index — the driver asserts 28
    assert response[12:44] == card.x25519_public(b"\x61" * 32)
    assert response[76:92] == b"\x00" * 16
    left.pump()
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    transport = wire.delivered[-1].payload
    assert transport[4:16] == struct.pack("<iq", 77, 0)  # driver: struct.pack('<iq', their_index, 0)


def test_the_timestamp_is_the_drivers_tai64n_packing() -> None:
    """The §5.2 timestamp, byte-exact to the official driver's packing:
    `struct.pack('!qi', 4611686018427387914 + int(now.timestamp()),
    int(now.microsecond * 1e3))` — 12 bytes, big-endian, 2^62 + 10 + unix seconds."""
    assert tai64n(1_480_000_000, 123) == struct.pack("!qi", 4611686018427387914 + 1_480_000_000, 123)
    assert len(tai64n(0, 0)) == 12


def test_the_mac_construction_is_the_drivers_blake2s_16() -> None:
    """The §5.4.4 `Mac()`, byte-exact to the official driver's construction —
    `mac_key = blake2s(b'mac1----' + their_public).digest()` then
    `blake2s(msg, digest_size=16, key=mac_key)` — and mac2 over `msgβ` under a cookie."""
    message = b"\x01" + b"\x00" * 115
    public = b"\x07" * 32
    mac_key = hashlib.blake2s(b"mac1----" + public).digest()
    assert wireguard._mac_key(public) == mac_key
    assert wireguard._mac128(mac_key, message) == hashlib.blake2s(message, digest_size=16, key=mac_key).digest()
    cookie = b"\x08" * 16
    framed = wireguard._add_macs(message, mac_key, cookie)
    assert framed[116:132] == wireguard._mac128(mac_key, message)
    assert framed[132:148] == hashlib.blake2s(framed[:132], digest_size=16, key=cookie).digest()


# ---- the device gates (c)-(g) ----------------------------------------------


def test_two_devices_talk_both_ways_and_enforce_allowed_ips() -> None:
    """(c) Full self-talk over the in-memory datagram fill: one handshake, bidirectional
    data, and the §9 rule — an inner source outside the sending peer's `allowed_ips` is
    refused `PermissionDenied`, an inner destination no peer covers is `NoRoute`, and
    the same words export as `Route` cells with `origin=WG` through `routes()`."""
    card = crypto.load()
    wire, left, right = _two_devices()
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    right.pump()
    assert right.receive() == _inner(LEFT_TEN, RIGHT_TEN)
    right.send(_inner(RIGHT_TEN, LEFT_TEN))
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)
    with pytest.raises(WouldBlock):
        left.receive()
    # §9: a source outside allowed_ips is not that peer's to send
    left.send(_inner(b"\x0a\x09\x09\x09", RIGHT_TEN))
    with pytest.raises(PermissionDenied):
        right.pump()
    # §9: an inner destination no peer covers has no tunnel
    with pytest.raises(NoRoute):
        left.send(_inner(LEFT_TEN, b"\x0b\x00\x00\x01"))
    # §9's export seam: the allowed_ips become Route cells with origin=WG
    exported = routes(left.config)
    assert exported == (
        schema.Route(
            path="net/dev/wg0/route/0",
            prefix=IPPrefix(RIGHT_TEN, 32),
            via=None,
            iface="net/dev/wg0",
            metric=0,
            origin=schema.RouteOrigin.WG,
        ),
    )


def test_an_authenticated_packet_moves_the_peer_endpoint_and_replies_route_there() -> None:
    """(d) Roaming (§2.1): a valid transport packet delivered with a new apparent
    source endpoint moves the peer's endpoint — pinned by the replies routing there
    (the fill's `deliver` wire face changes the apparent source without touching the
    device)."""
    card = crypto.load()
    wire, left, right = _two_devices()
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    right.send(_inner(RIGHT_TEN, LEFT_TEN))
    sent = wire.delivered[-1]
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)
    # a fresh valid transport packet, its apparent source changed by the wire
    roamed = Endpoint(b"\x0a\x09\x09\x09", 4444, None)
    listener = wire.bind(roamed)  # where replies must land
    right.send(_inner(RIGHT_TEN, LEFT_TEN))  # the next counter — fresh, never seen
    sent = wire.delivered[-1]
    wire.take(LEFT_LOCAL)  # withdraw the send's own copy: the wire re-addresses it
    wire.deliver(roamed, LEFT_LOCAL, sent.payload)
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)  # the packet lands, verified
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    reply = wire.delivered[-1]
    assert reply.dst == roamed  # the learned endpoint is where the reply goes
    assert listener.recvfrom().payload == reply.payload  # and it lands there


def test_a_replayed_transport_packet_is_refused_and_moves_no_counter() -> None:
    """(e) Replay (§5.5): a re-delivered transport packet is refused `UnexpectedReply`
    and moves nothing — the next fresh packet still lands, and the window itself is
    pinned below."""
    card = crypto.load()
    wire, left, right = _two_devices()
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    right.send(_inner(RIGHT_TEN, LEFT_TEN))
    sent = wire.delivered[-1]
    wire.take(LEFT_LOCAL)  # withdraw the send's own copy: this gate drives deliveries
    wire.deliver(sent.src, LEFT_LOCAL, sent.payload)
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)
    wire.deliver(sent.src, LEFT_LOCAL, sent.payload)  # the same counter again
    with pytest.raises(UnexpectedReply):
        left.pump()
    with pytest.raises(WouldBlock):
        left.receive()  # the replay queued nothing
    right.send(_inner(RIGHT_TEN, LEFT_TEN, b"fresh"))  # the next counter is untouched
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN, b"fresh")


def test_the_replay_window_is_the_whitepapers_sliding_window() -> None:
    """(e) The §5.5 window itself (RFC 2401 appendix C over 64 counters): a replay
    refuses and moves no counter, a late in-window counter is accepted once, and one
    out of the window is refused — never guessed at."""
    window = ReplayWindow()
    assert window.highest is None
    assert window.accept(5) and window.highest == 5
    assert not window.accept(5) and window.highest == 5  # a replay moves no counter
    assert window.accept(7) and window.highest == 7
    assert not window.accept(7)
    assert window.accept(6)  # late, but in the window
    assert not window.accept(6)
    assert window.accept(0) and not window.accept(0)  # still in the window of 64
    assert window.accept(72) and window.highest == 72
    assert not window.accept(7)  # 72 − 7 = 65: now out of the window


def test_a_secret_cell_never_renders_or_exports_its_material() -> None:
    """(f) The `Secret` cell contract (§17): key material never appears in `repr` or
    `str`, and export refuses `PermissionDenied` by name — private-key export is
    refused by default, and the device's repr carries no key bytes either."""
    value = SecretValue("net/secret/wg0", LEFT_PRIVATE)
    assert repr(value) == "SecretValue(path='net/secret/wg0')"
    assert str(value) == repr(value)
    assert LEFT_PRIVATE.hex() not in repr(value)
    assert value.path == "net/secret/wg0"
    assert value.cell == Secret("net/secret/wg0")
    with pytest.raises(PermissionDenied):
        value.export()
    card = crypto.load()
    _wire, left, _right = _two_devices()
    assert LEFT_PRIVATE.hex() not in repr(left)
    assert RIGHT_PRIVATE.hex() not in repr(left)
    assert card.x25519_public(LEFT_PRIVATE).hex() not in repr(left)


def test_one_device_is_server_and_client_at_once() -> None:
    """(g) The role pin (§11): one device with `listen_port` AND a peer `endpoint` —
    left initiates as a client and later serves right's initiation on its listen port,
    both directions of data in both sessions, and the §11 `last_handshake` `Interval`
    closes when the next handshake opens."""
    card = crypto.load()
    wire, left, right = _two_devices(
        left_ephemerals=(b"\x51" * 32, b"\x52" * 32),
        right_ephemerals=(b"\x61" * 32, b"\x62" * 32),
        left_indexes=(0x11110001, 0x11110002),
        right_indexes=(0x22220001, 0x22220002),
    )
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    left_public = card.x25519_public(LEFT_PRIVATE).hex()
    assert left.local == LEFT_LOCAL and left.config.listen_port == 51820  # the server face
    assert left.peers[0].endpoint == RIGHT_HOME  # the client face — roles are config
    _shake_hands(wire, left, right, right_public)  # left -> right: left is the client
    assert left.last_handshake(right_public) == Interval(FIXED_NOW, None)
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    right.pump()
    assert right.receive() == _inner(LEFT_TEN, RIGHT_TEN)
    right.send(_inner(RIGHT_TEN, LEFT_TEN))
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)
    # now right initiates to left's listen port — left serves it (the server face)
    right.initiate(left_public)
    left.pump()  # consumes the initiation on 51820 and answers
    right.pump()
    assert left.handshakes(right_public) == (
        Interval(FIXED_NOW, FIXED_NOW),  # closed by the next handshake (§11)
        Interval(FIXED_NOW, None),
    )
    assert left.last_handshake(right_public) == Interval(FIXED_NOW, None)
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    right.pump()
    assert right.receive() == _inner(LEFT_TEN, RIGHT_TEN)
    right.send(_inner(RIGHT_TEN, LEFT_TEN))
    left.pump()
    assert left.receive() == _inner(RIGHT_TEN, LEFT_TEN)


def test_the_same_device_carries_a_preshared_key_peer() -> None:
    """§5.2's optional psk (psk2): a peer with a `preshared` cell handshakes through the
    same code path (the zero key is the absent psk) and the data lands."""
    card = crypto.load()
    wire, left, right = _two_devices(psk=True)
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    left.send(_inner(LEFT_TEN, RIGHT_TEN))
    right.pump()
    assert right.receive() == _inner(LEFT_TEN, RIGHT_TEN)


# ---- the named refusals and the lifecycle ----------------------------------


def test_the_initiation_timestamp_is_monotonic_per_peer() -> None:
    """§5.1: the responder keeps the greatest TAI64N seen per peer and discards an
    initiation "containing timestamps less than or equal to it" — the second
    initiation with the same fixed timestamp refuses `UnexpectedReply`."""
    card = crypto.load()
    wire, left, right = _two_devices(
        left_ephemerals=(b"\x51" * 32, b"\x52" * 32),
        left_indexes=(1, 2),
        right_indexes=(3,),
    )
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    _shake_hands(wire, left, right, right_public)
    left.initiate(right_public)  # the same fixed timestamp again
    with pytest.raises(UnexpectedReply):
        right.pump()


def test_a_cookie_reply_is_validated_then_refused_by_name() -> None:
    """§5.4.7 cookie reply reception — the named gap 1 of `wireguard.py`: the message is
    structurally validated (shape, then the receiver index) and then refused
    `NotSupported` — the Xaead (XChaCha20Poly1305) is not on the frozen `cap.crypto`
    card, and an uncheckable cookie must never become a MAC key."""
    card = crypto.load()
    wire, left, right = _two_devices(left_indexes=(0xABCD,), right_indexes=(0xDCBA,))
    left.open()
    right.open()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    left.initiate(right_public)  # a handshake in flight names index 0xABCD
    reply = struct.pack("<II", 3, 0xABCD) + bytes(24) + bytes(32)
    assert len(reply) == COOKIE_REPLY_SIZE == 64
    wire.deliver(RIGHT_HOME, LEFT_LOCAL, reply)
    with pytest.raises(NotSupported):
        left.pump()
    stranger = struct.pack("<II", 3, 0xDEAD) + bytes(24) + bytes(32)
    wire.deliver(RIGHT_HOME, LEFT_LOCAL, stranger)
    with pytest.raises(StaleHandle):
        left.pump()


def test_a_forged_handshake_is_refused_at_mac1() -> None:
    """§5.3/§5.4.4: mac1 is required and valid on every message — a forged initiation
    (its mac1 covers the message bytes, so it cannot be guessed) refuses
    `MalformedMessage`, as do a transport message below the §5.4.6 minimum and a type
    that is not one of the protocol's four. Never a half-applied message."""
    wire, left, right = _two_devices()
    left.open()
    right.open()
    wire.deliver(LEFT_HOME, RIGHT_LOCAL, struct.pack("<II", 1, 1) + bytes(140))  # zero mac1
    with pytest.raises(MalformedMessage):
        right.pump()
    wire.deliver(LEFT_HOME, RIGHT_LOCAL, b"\x04\x00\x00\x00short")
    with pytest.raises(MalformedMessage):
        right.pump()
    wire.deliver(LEFT_HOME, RIGHT_LOCAL, b"\x09\x00\x00\x00nonsense-message")
    with pytest.raises(MalformedMessage):
        right.pump()


def test_the_lifecycle_gates_every_face_by_name() -> None:
    """The §11/§14 lifecycle in the `link.slip` shape: the config faces answer before
    `open`, the data faces refuse `NotConnected` until `open`, `open` on an open device
    refuses `NotSupported`, and after `close` every face refuses `StaleHandle` — a
    closed device is gone for good, one-way like the stream and line fills."""
    card = crypto.load()
    _wire, left, _right = _two_devices()
    right_public = card.x25519_public(RIGHT_PRIVATE).hex()
    assert left.local is None and left.path == "net/dev/wg0"  # config before open
    assert len(left.peers) == 1
    for operation in (
        lambda: left.initiate(right_public),
        lambda: left.send(_inner(LEFT_TEN, RIGHT_TEN)),
        lambda: left.receive(),
        lambda: left.pump(),
        lambda: left.wait(schema.Interest.READ),
    ):
        with pytest.raises(NotConnected):
            operation()
    left.open()
    with pytest.raises(NotSupported):
        left.open()
    with pytest.raises(InvalidEndpoint):
        left.initiate("ff" * 32)  # no peer of this device is named so (§3)
    left.close()
    left.close()  # idempotent, like the fills'
    for operation in (
        lambda: left.initiate(right_public),
        lambda: left.send(_inner(LEFT_TEN, RIGHT_TEN)),
        lambda: left.receive(),
        lambda: left.pump(),
        lambda: left.wait(schema.Interest.READ),
        lambda: left.handshakes(right_public),
        lambda: left.last_handshake(right_public),
        lambda: left.peers,
        lambda: left.local,
    ):
        with pytest.raises(StaleHandle):
            operation()
    with pytest.raises(StaleHandle):
        left.open()  # a closed device is gone for good
