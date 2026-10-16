"""The WireGuard device card (docs/25 §11): one `WireGuardDevice` is server AND client
at once — the roles are configuration, not modes.

The protocol source of record is the WireGuard whitepaper (Jason A. Donenfeld,
"WireGuard: Next Generation Kernel Network Tunnel"), cited section by section below,
and the message layout of the protocol (handshake initiation type 1, handshake response
type 2, cookie reply type 3, transport data type 4). The device rides `cap.net.socket`
datagrams (docs/25 §11) through `net.transport.datagram`, and every cryptographic
primitive comes from the one frozen `cap.crypto` card (`net.crypto`): X25519, the
ChaCha20-Poly1305 AEAD (cipher id 0), unkeyed BLAKE2s, and the HKDF-BLAKE2s pair that
is the Noise KDF (hash id 1) — nothing is reimplemented here (one gap is named at the
bottom and rides the platform's stdlib for exactly one width).

What the whitepaper lays out, and where it lands here:

- **§5.4.1–5.4.3 — the handshake (Noise IK, `Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s`).**
  The key schedule is the whitepaper's, exactly: `Ci := Hash(Construction)`,
  `Hi := Hash(Ci‖Identifier)`, `Hi := Hash(Hi‖Spub_r)`, `Ci := Kdf1(Ci, Epub_i)` (the
  ephemeral public mixes into the chaining key), `msg.static := Aead(κ, 0, Spub_i, Hi)`
  over `Kdf2(Ci, DH(Epriv_i, Spub_r))`, the timestamp likewise over
  `Kdf2(Ci, DH(Spriv_i, Spub_r))`; the response mixes `Kdf1(Cr, Epub_r)`,
  `Kdf1(Cr, DH(Epriv_r, Epub_i))`, `Kdf1(Cr, DH(Epriv_r, Spubi))`, then
  `(Cr, τ, κ) := Kdf3(Cr, Q)` and `msg.empty := Aead(κ, 0, ϵ, Hr)`.
  Kdf1/Kdf2/Kdf3 are one HKDF-BLAKE2s extraction and one chained expansion
  (`T1 = HMAC(temp, 0x01)`, `T2 = HMAC(temp, T1‖0x02)`, `T3 = HMAC(temp, T2‖0x03)`),
  and the transport keys are `(Tsend_i = Trecv_r, Trecv_i = Tsend_r) := Kdf2(C, ϵ)`
  (§5.4.5). The 148-byte initiation and 92-byte response byte-exact pins live in
  `tests/test_net_wireguard.py` over the wireguard test-vectors corpus.
- **§5.1 — timestamps.** The initiation carries a 12-byte big-endian TAI64N [7]
  timestamp (sealed, authenticated); the responder keeps the greatest one seen per peer
  and "discards packets containing timestamps less than or equal to it" — a per-peer
  monotonically increasing 96-bit number, compared here as the memcmp-ordered bytes
  the big-endian encoding exists for.
- **§5.4.4 — the cookie MACs.** `msg.mac1 := Mac(Hash(Label-Mac1‖Spub_m'), msgα)` —
  required and valid on every message, under load or not (§5.3) — and
  `msg.mac2 := 0^16` with no cookie, else `Mac(L_m, msgβ)` with the latest cookie,
  which expires 120 seconds after receipt. `msgα`/`msgβ` are the bytes prior to mac1
  and mac2 respectively. This build always sends `mac2 = 0^16` and accepts any mac2
  on receive — the cookie machinery around it is the named gap below — but the mac2
  construction itself is implemented and pinned (`_add_macs` with an explicit cookie).
- **§5.4.6 — transport data.** `type 4 ‖ receiver ‖ counter ‖ packet`, where
  `packet := Aead(Tsend, Nsend, P′, ϵ)` over the zero-padded `P′ = P ‖ 0^(16·⌈|P|/16⌉−|P|)`
  and the 64-bit counter is the AEAD nonce (4 zero bytes ‖ little-endian counter).
  There is no length in the header — "the inner IP packet already has a length field in
  its header" — so padding is stripped by that field (`_parse_ip`), and a v6 jumbogram
  (RFC 2675, payload_length 0 with a jumbo option) is refused by name rather than
  guessed at. Replay protection is the whitepaper's sliding window, "checked only
  after having verified the authentication tag, using the algorithm detailed by
  appendix C of RFC2401" — `ReplayWindow` is that algorithm (the window of 64;
  RFC 6479's larger bitshift-free bitmap is the whitepaper's named alternative, a
  receiver-local choice that does not change the wire). A replay refuses
  `UnexpectedReply` and moves no counter.
- **§2.1 — roaming.** "The reason it is optional is that if it is not specified and
  WireGuard receives a correctly authenticated packet from a peer, it will use the
  outer external source IP address for determining the endpoint." Every authenticated
  message moves the peer's endpoint to where it came from — pinned in
  `tests/test_net_wireguard.py` over the fill's `deliver` wire face.
- **§9 / docs/25 §11 — `allowed_ips` is the routing decision.** On send, longest
  prefix match over the peers' `allowed_ips` chooses the peer (no match refuses
  `NoRoute`); on receive, the inner source must sit inside the sending peer's
  `allowed_ips` or the packet is refused `PermissionDenied` — a source outside
  `allowed_ips` is not that peer's to send. The same words export as `Route` cells
  with `origin=WG` through the pure `routes()` seam below (§9: "WireGuard
  allowed-ips" is a `RouteOrigin` like any other).
- **§5.2 — the optional pre-shared key.** A peer with no `preshared` cell uses the
  zero key — the psk2 pattern's default, what wireguard-go's zero
  `NoisePresharedKey` does — so the same code path serves both modes.

The lifecycle follows the line and stream fills (`link.slip`, `transport.stream`):
`open`/`close` one-way (a closed device refuses `StaleHandle`, §14), `initiate` is the
client side's handshake start, `send`/`receive` are the inner L3 packets, `pump` drains
the datagram socket (named refusals propagate — the caller decides), and `wait` is the
level-triggered readiness face (§4).

**Deterministic seams — named features, not test hooks.** The wireguard test-vectors
corpus is designed for deterministic ephemeral injection, and the whitepaper itself
notes the timestamp "does not even have to be an accurate timestamp; it simply must be
a per-peer monotonically increasing 96-bit number" (§5.1). So the constructor takes
`ephemeral_provider` (32-byte X25519 ephemeral private keys), `index_provider` (u32
sender indices), `timestamp_provider` (raw 12-byte TAI64N bytes), and `clock` (unix
seconds — the `Interval` model's `Time`, docs/24), defaulting to real randomness and
the wall clock; `key_observer` sees each derived transport key at its label
(`transport_i2r`, `transport_r2i`) so a vector test can compare derived keys to a
corpus. With the defaults installed nothing deterministic leaks: the observer is None
and the providers are `os.urandom`.

**Named gaps (docs/25 honesty rules) — not stubbed silently:**

1. **Cookie reply acceptance (§5.4.7) — refused by name, fail-closed.** The wire is
   `msg.cookie := Xaead(Hash(Label-Cookie‖Spub_m), msg.nonce, τ, M)` — an AEAD "with
   an extended randomized nonce" (XChaCha20Poly1305 over a 24-byte nonce; kernel
   `drivers/net/wireguard/cookie.c` `xchacha20poly1305_encrypt`, wireguard-go
   `chacha20poly1305.NewX`). The frozen `cap.crypto` C ABI carries 12-byte-nonce
   ChaCha20Poly1305 and AES-GCM only, and this build may not reimplement a primitive,
   so a received cookie reply is structurally validated and then refused
   `NotSupported` — an uncheckable cookie must never become a MAC key (that is exactly
   §5.3's third flaw). Recommendation for the card: an XChaCha20Poly1305 face (or
   HChaCha20, to compose one); then §5.4.7 acceptance lands over it.
2. **Cookie generation policy (§5.3 / §5.4.7) — not built.** The under-load
   rate-limiting decision that makes a responder emit cookie replies (and thus demand
   mac2) is policy machinery this card does not carry; cookie replies are never sent
   and mac2 is never demanded.
3. **The §6 timer state machine — not built.** Retransmission (6.4), rekey-after-time
   and rekey-after-messages (6.2/6.3), and passive keepalives (6.5) are timer policy;
   the faces they need exist (the `clock` seam, `Session.send_counter`, the peer's
   `keepalive` config cell) and `initiate` is the one-shot handshake start. A
   `send` with no session refuses `NotConnected` rather than queueing behind a timer.
4. **The MAC1/MAC2 digest width (§5.4.4) — one width off-card.** `Mac()` is keyed
   BLAKE2s with a **16-byte** digest: kernel `cookie.c` `compute_mac1` calls
   `blake2s(key, …, out=mac1, outlen=COOKIE_LEN)` with `COOKIE_LEN = 16`,
   wireguard-go uses `blake2s.New128`, and the official cross-implementation driver
   (`wireguard-tools/contrib/external-tests/python/main.py`) computes
   `blake2s(initiation_packet, digest_size=16, key=mac_key)`. The frozen card's
   `blake2s_keyed` is 256-bit-only and truncating it is **not** this value — the
   corpus's `mac16_vectors` disagree with the truncation and agree with
   `hashlib.blake2s(digest_size=16, key=…)`, so this one width rides the platform's
   BLAKE2s (pinned in `tests/test_net_wireguard.py`) while every other primitive rides
   the card. Recommendation for the card: an `out_len` on `pm_crypto_blake2s_keyed`
   (its Rust core's `Blake2s::new(outlen, key)` already takes one) and this call
   moves onto the card too.

Refused by name (§15), by face: a wrong shape `NotSupported`; an unauthenticated or
unverifiable message `MalformedMessage` (mac1, AEAD tags — §5.4.6 makes the tag the
parse check); a replayed timestamp or transport counter `UnexpectedReply`; a cookie
reply `NotSupported` (gap 1); an inner source outside `allowed_ips`
`PermissionDenied`; no peer for an inner destination `NoRoute`; no session
`NotConnected`; no endpoint `InvalidEndpoint`; an unknown session index `StaleHandle`
(§14 — a session that is gone); a device that is closed `StaleHandle`; before `open`,
`NotConnected`.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .. import crypto, schema
from ..schema import (
    Endpoint,
    Interest,
    Interval,
    InvalidEndpoint,
    IPPrefix,
    MalformedMessage,
    NoRoute,
    NotConnected,
    NotSupported,
    PermissionDenied,
    Route,
    RouteOrigin,
    Secret,
    StaleHandle,
    UnexpectedReply,
    WouldBlock,
)
from ..transport.datagram import Datagrams, DatagramSocket

# ---- the wire words (whitepaper 5.4; the message layout of the protocol) ----

MESSAGE_HANDSHAKE_INITIATION = 1
MESSAGE_HANDSHAKE_RESPONSE = 2
MESSAGE_COOKIE_REPLY = 3
MESSAGE_TRANSPORT_DATA = 4

INITIATION_SIZE = 148  # type 1 ‖ sender 4 ‖ ephemeral 32 ‖ static 48 ‖ ts 28 ‖ mac1 16 ‖ mac2 16
RESPONSE_SIZE = 92  # type 2 ‖ sender 4 ‖ receiver 4 ‖ ephemeral 32 ‖ empty 16 ‖ mac1 16 ‖ mac2 16
COOKIE_REPLY_SIZE = 64  # type 3 ‖ receiver 4 ‖ nonce 24 ‖ cookie 32
TRANSPORT_HEADER = 16  # type 4 ‖ receiver 4 ‖ counter 8
TRANSPORT_MINIMUM = TRANSPORT_HEADER + crypto.TAG_LEN  # empty payload (a keepalive)
MAC_SIZE = 16  # §5.4.4: mac1 and mac2 are 16 bytes each
_KEY_SIZE = 32

# The Noise prologue and labels (§5.4.2/5.4.4/5.4.7; wireguard-go's WG* constants).
CONSTRUCTION = b"Noise_IKpsk2_25519_ChaChaPoly_BLAKE2s"
IDENTIFIER = b"WireGuard v1 zx2c4 Jason@zx2c4.com"
LABEL_MAC1 = b"mac1----"
LABEL_COOKIE = b"cookie--"

_ZERO_NONCE = b"\x00" * 12  # every handshake AEAD runs at nonce 0 (§5.4.2)
_ZERO_KEY = b"\x00" * _KEY_SIZE  # the absent preshared key (§5.2; wireguard-go's zero psk)

_card: crypto.Crypto | None = None


def _crypto() -> crypto.Crypto:
    """The one `cap.crypto` card (docs/25 §11), loaded once per process."""
    global _card
    if _card is None:
        _card = crypto.load()
    return _card


def _bytes(value: object, what: str) -> bytes:
    """Bytes or a refusal by name — a wrong shape is never a silent wrong parse."""
    if not isinstance(value, bytes):
        raise NotSupported(f"{what} is bytes: this value is not one")
    return value


def _mac128(key: bytes, message: bytes) -> bytes:
    """The wire's `Mac()` — keyed BLAKE2s with a 16-byte digest (§5.4.4).

    Pinned at the test to the wireguard test-vectors corpus's `mac16_vectors` and to
    the official external-tests construction (`blake2s(..., digest_size=16, key=...)`).
    This is the one width the frozen card cannot express (its keyed face is 256-bit
    and truncation is a different value — named gap 4 in the module docstring);
    everything else in this card comes from `cap.crypto`.
    """
    return hashlib.blake2s(message, digest_size=16, key=key).digest()


def tai64n(unix_seconds: int, nanos: int = 0) -> bytes:
    """The §5.2 timestamp: 12 bytes, big-endian TAI64N — `2^62 + 10 + unix_seconds`
    and the nanoseconds, byte-for-byte the formula the official cross-implementation
    driver packs (`struct.pack('!qi', 4611686018427387914 + int(now.timestamp()),
    int(now.microsecond * 1e3))` — wireguard-tools contrib/external-tests/python;
    the same 4611686018427387914 offset appears in every reviewed implementation).
    Big-endian is the point: two timestamps compare as the memcmp of their bytes
    (§5.1), which is how the replay check below reads."""
    return struct.pack(">QI", (1 << 62) + 10 + unix_seconds, nanos)


def transport_nonce(counter: int) -> bytes:
    """The §5.4.6 AEAD nonce for a transport message: four zero bytes followed by the
    64-bit little-endian counter — pinned to the corpus's transport nonce vectors."""
    return b"\x00" * 4 + struct.pack("<Q", counter)


def transport_seal(key: bytes, counter: int, payload: bytes) -> bytes:
    """The §5.4.6 `msg.packet` field: `Aead(Tsend, Nsend, P, ϵ)` — ChaCha20-Poly1305
    (cipher id 0) with no associated data. Padding is the message layer's step (the
    device pads `P` to `P′`, §5.4.6), so this face carries the payload as given —
    which is exactly what the corpus's transport vectors pin."""
    card = _crypto()
    data = _bytes(payload, "a transport payload")
    try:
        return card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, transport_nonce(counter), b"", data)
    except crypto.CryptoRefusal as error:
        raise NotSupported(f"the card refused to seal this transport payload: {error}") from error


def transport_open(key: bytes, counter: int, sealed: bytes) -> bytes:
    """Read one §5.4.6 `msg.packet` field back. The tag is the message's parse check
    (§5.4.6: "the authentication tag serves to determine whether the message is
    legitimate") — a tag that does not verify refuses `MalformedMessage`, never a
    half-applied plaintext."""
    card = _crypto()
    data = _bytes(sealed, "a transport packet")
    try:
        return card.aead_open(crypto.CIPHER_CHACHA20_POLY1305, key, transport_nonce(counter), b"", data)
    except crypto.CryptoRefusal as error:
        raise MalformedMessage(f"the transport packet does not verify: {error}") from error


def _kdf(chain_key: bytes, input_key: bytes) -> tuple[bytes, bytes, bytes]:
    """The whitepaper's Kdf1/Kdf2/Kdf3 — one HKDF-BLAKE2s extraction and one chained
    expansion: `T1 = HMAC(temp, 0x01)`, `T2 = HMAC(temp, T1‖0x02)`,
    `T3 = HMAC(temp, T2‖0x03)`.

    `Kdf1(C, x)` is `T1` (the new chaining key), `Kdf2` `(T1, T2)`, `Kdf3`
    `(T1, T2, T3)` — with the card's RFC 5869 pair (hash id 1): extract(salt=chain
    key, ikm=input), then one expand at 96 bytes over empty info."""
    card = _crypto()
    temp = card.hkdf_extract(crypto.HASH_BLAKE2S, chain_key, input_key)
    blocks = card.hkdf_expand(crypto.HASH_BLAKE2S, temp, b"", 96)
    return blocks[:32], blocks[32:64], blocks[64:96]


def _mix_hash(hash_: bytes, data: bytes) -> bytes:
    """`Hi := Hash(Hi‖data)` (§5.4.2) — unkeyed BLAKE2s from the card."""
    return _crypto().blake2s(hash_ + data)


def _mac_key(responder_public: bytes) -> bytes:
    """`Hash(Label-Mac1‖Spub_m')` (§5.4.4) — the responder's (recipient's) static
    public key; precomputable per peer, as the whitepaper notes."""
    return _crypto().blake2s(LABEL_MAC1 + responder_public)


def _cookie_key(responder_public: bytes) -> bytes:
    """`Hash(Label-Cookie‖Spub_m)` (§5.4.7) — the key the cookie reply's `Xaead`
    runs under. Named and computed here for the receipt path; the AEAD itself is
    gap 1 (XChaCha20Poly1305 is not on the frozen card)."""
    return _crypto().blake2s(LABEL_COOKIE + responder_public)


def _add_macs(message: bytes, mac_key: bytes, cookie: bytes | None = None) -> bytes:
    """The §5.4.4 cookie MACs appended to a handshake message.

    `msgα` is `message` itself (the bytes prior to mac1) and `msgβ` the bytes prior to
    mac2. With no cookie (the only state this build reaches — gap 2) `msg.mac2` is
    `0^16` ("if Lm = ϵ or the cookie is 120s old"); with a cookie it is
    `Mac(Lm, msgβ)`, and that construction is pinned in the tests with an explicit
    cookie value."""
    mac1 = _mac128(mac_key, message)
    if cookie is None:
        mac2 = b"\x00" * MAC_SIZE
    else:
        mac2 = _mac128(cookie, message + mac1)
    return message + mac1 + mac2


class ReplayWindow:
    """§5.4.6's sliding replay window — "the algorithm detailed by appendix C of
    RFC2401" over the last 64 counters.

    The whitepaper offers RFC 2401 appendix C or RFC 6479's larger bitshift-free
    bitmap (the kernel's 8128-bit window is the latter); the window is receiver-local
    policy and does not change the wire, and this build pins the classic window of 64.
    Acceptance is only ever asked after the authentication tag has verified (§5.4.6:
    "checked only after having verified the authentication tag") — `Session.open`
    keeps that order, and a refusal here moves no counter.
    """

    def __init__(self, size: int = 64) -> None:
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            raise NotSupported("a window size is a positive word: this value is not one")
        self.size = size
        self._highest: int | None = None
        self._bitmap = 0  # bit i set = counter `highest - i` already seen

    @property
    def highest(self) -> int | None:
        """The greatest counter accepted so far, or None on a window that is new."""
        return self._highest

    def accept(self, counter: int) -> bool:
        """Record `counter`; True when it is fresh, False when it is a replay or has
        fallen out of the window (RFC 2401 appendix C)."""
        if not isinstance(counter, int) or isinstance(counter, bool) or counter < 0:
            raise NotSupported("a counter is a non-negative word: this value is not one")
        if self._highest is None:  # the first counter on a fresh window is the highest
            self._highest = counter
            self._bitmap = 1
            return True
        if counter > self._highest:
            shift = counter - self._highest
            self._bitmap = ((self._bitmap << shift) | 1) & ((1 << self.size) - 1)
            self._highest = counter
            return True
        offset = self._highest - counter
        if offset >= self.size:  # out of the window: refused, never guessed at
            return False
        bit = 1 << offset
        if self._bitmap & bit:  # already seen
            return False
        self._bitmap |= bit
        return True


class SecretValue:
    """Key material behind one `Secret` cell (docs/25 §11); export refused (§17).

    The bytes exist for the crypto card and for nothing else: `repr` and `str` are the
    cell's path — key material never appears in a rendering (§17) — and `export()`
    refuses `PermissionDenied` by name, §17's "private-key export from `Secret` cells
    by default" being an honest refusal, never a quiet success. The device reads the
    material internally to hand it to the card; nothing else can.
    """

    __slots__ = ("_material", "_path")

    def __init__(self, path: str, material: bytes) -> None:
        blob = _bytes(material, "key material")
        if len(blob) != _KEY_SIZE:
            raise NotSupported(f"key material is {_KEY_SIZE} bytes: this value is not one")
        self._path = path
        self._material = blob

    @property
    def path(self) -> str:
        """The `Secret` cell's path — the only word of this value that travels."""
        return self._path

    @property
    def cell(self) -> Secret:
        """The `Secret` cell this material sits behind (schema: a cell path, nothing more)."""
        return Secret(self._path)

    def export(self) -> bytes:
        """Export is refused by name (§17) — private-key material leaves this cell
        through the card's X25519 and nowhere else."""
        raise PermissionDenied("export of a Secret cell's key material is refused by name (§17)")

    def __repr__(self) -> str:
        return f"SecretValue(path={self._path!r})"

    def __str__(self) -> str:
        return self.__repr__()


@dataclass
class Session:
    """One transport keypair with its counters and its replay window (§5.4.5, §5.4.6).

    `seal` writes a whole transport message (`type 4 ‖ receiver ‖ counter ‖ packet`),
    taking the send counter from its state and advancing it exactly once per message;
    `open` reads one back and applies the replay window **after** the tag verifies
    (§5.4.6). The keys are key material (§11/§17): this repr shows the indexes and
    counters and never key bytes, and there is no export face at all."""

    send_key: bytes
    recv_key: bytes
    local_index: int
    remote_index: int
    replay: ReplayWindow = field(default_factory=ReplayWindow)
    _send_counter: int = 0

    @property
    def send_counter(self) -> int:
        """The counter the next `seal` will use (§5.4.6: `Nsend` is the message's nonce)."""
        return self._send_counter

    def seal(self, payload: bytes) -> bytes:
        """One §5.4.6 transport message carrying `payload` as given (the device's
        `send` applies §5.4.6's zero padding around the inner packet)."""
        data = _bytes(payload, "a transport payload")
        counter = self._send_counter
        self._send_counter = counter + 1
        header = struct.pack("<IIQ", MESSAGE_TRANSPORT_DATA, self.remote_index, counter)
        return header + transport_seal(self.send_key, counter, data)

    def open(self, message: bytes) -> bytes:
        """Read one §5.4.6 transport message: the tag verifies first (a failure is
        `MalformedMessage`), then the replay window (a replay is `UnexpectedReply`,
        no counter moved). The payload comes back padded — stripping §5.4.6's zeros
        is the inner IP length field's job, at the L3 face."""
        data = _bytes(message, "a transport message")
        if len(data) < TRANSPORT_MINIMUM:
            raise MalformedMessage(f"a transport message is {TRANSPORT_MINIMUM} bytes or more (§5.4.6): this one is not")
        kind, receiver, counter = struct.unpack("<IIQ", data[:TRANSPORT_HEADER])
        if kind != MESSAGE_TRANSPORT_DATA:
            raise MalformedMessage(f"the message is not transport data (§5.4.6): type {kind}")
        if receiver != self.local_index:
            raise StaleHandle(f"the message names receiver index {receiver}, not this session's {self.local_index} (§14)")
        payload = transport_open(self.recv_key, counter, data[TRANSPORT_HEADER:])
        if not self.replay.accept(counter):
            raise UnexpectedReply(f"transport counter {counter} is a replay (§5.4.6): refused, no counter moved")
        return payload

    def __repr__(self) -> str:
        return (
            f"Session(local_index={self.local_index}, remote_index={self.remote_index}, "
            f"send_counter={self._send_counter}, highest_received={self.replay.highest})"
        )


@dataclass
class _Handshake:
    """An in-flight initiation on the initiator's side (§5.4.2 state, kept until the
    response completes it or the next initiation supersedes it)."""

    hash: bytes
    chain_key: bytes
    local_ephemeral: bytes
    local_index: int

    def __repr__(self) -> str:
        # Key material never appears in a rendering (§17) — the index is the word.
        return f"_Handshake(local_index={self.local_index})"


@dataclass
class _Peer:
    """One peer's runtime state: the schema cell plus what talking to it keeps.

    The cell is `WireGuardPeer` from `net.schema` — never redefined here. `endpoint`
    is the roaming endpoint (§2.1), `handshakes` the §11 `Interval` history (each
    closed by the next handshake), `last_timestamp` the §5.1 replay word."""

    cell: schema.WireGuardPeer
    public: bytes
    preshared: SecretValue | None
    endpoint: Endpoint | None
    session: Session | None = None
    handshake: _Handshake | None = None
    handshakes: list[Interval] = field(default_factory=list)
    last_timestamp: bytes = b""

    def __repr__(self) -> str:
        return f"_Peer(public={self.public.hex()}, endpoint={self.endpoint!r}, session={self.session!r})"


def _prefix_match(ip: bytes, prefix: IPPrefix) -> bool:
    """One prefix test over both families (§9): the family is the length, and the
    match is over the prefix's bits — v4-mapped v6 is refused by name elsewhere (§17),
    so one length is one family, always."""
    if len(ip) != len(prefix.network) or not 0 <= prefix.length <= 8 * len(ip):
        return False
    full, rest = divmod(prefix.length, 8)
    if ip[:full] != prefix.network[:full]:
        return False
    return rest == 0 or (ip[full] ^ prefix.network[full]) & (0xFF << (8 - rest)) & 0xFF == 0


def _parse_ip(packet: bytes) -> tuple[int, bytes, bytes] | None:
    """The inner packet as the L3 face reads it: (length of the IP packet, src, dst).

    The length is what strips §5.4.6's zero padding ("the inner IP packet already has
    a length field in its header") — a packet that does not parse is refused here as
    `None` and by name at the faces. A v6 payload_length of 0 reads as the 40-byte
    empty packet; RFC 2675 jumbograms are refused by name (the §5.4.6 pad never grows
    a datagram past the MTU, and an inner jumbogram has no place here)."""
    if len(packet) >= 20 and packet[0] >> 4 == 4:
        total = int.from_bytes(packet[2:4], "big")
        if total < 20 or total > len(packet):
            return None
        return total, packet[12:16], packet[16:20]
    if len(packet) >= 40 and packet[0] >> 4 == 6:
        total = 40 + int.from_bytes(packet[4:6], "big")
        if total > len(packet):
            return None
        return total, packet[8:24], packet[24:40]
    return None


def _pad(payload: bytes) -> bytes:
    """§5.4.6's zero padding: `P′ := P ‖ 0^(16·⌈|P|/16⌉−|P|)`."""
    return payload + b"\x00" * (-len(payload) % 16)


def routes(config: schema.WireGuardDevice) -> tuple[Route, ...]:
    """The §9 seam: this device's `allowed_ips` as `Route` cells with `origin=WG`.

    Pure descriptors — docs/25 §9 puts "WireGuard allowed-ips" among `Route.origin`'s
    values "so the tunnel routes are deltas like every other route"; writing them into
    the graph is the control plane's job (§8), not this seam's. One cell per
    `(peer, prefix)`, `via=None` (the tunnel is the next hop), and `iface` the
    device's `Device` cell path."""
    out: list[Route] = []
    index = 0
    for peer in config.peers:
        for prefix in peer.allowed_ips:
            out.append(
                Route(
                    path=f"{config.path}/route/{index}",
                    prefix=prefix,
                    via=None,
                    iface=config.device,
                    metric=0,
                    origin=RouteOrigin.WG,
                )
            )
            index += 1
    return tuple(out)


class WireGuardDevice:
    """One WireGuard device — server AND client at once (docs/25 §11): the roles are
    configuration, not modes.

    A peer with `endpoint` set is the device's remote (a client's remote — and it
    roams, §2.1), a peer with only `allowed_ips` is a server's client entry, and
    `listen_port` is the server side's UDP port (§2.1: "the listen port of peers and
    the source port of packets sent are always the same"). One device does both at
    once — `tests/test_net_wireguard.py` pins exactly that.

    The faces, one line each (the `link.slip` / `transport.stream` shape):

    - `open`/`close` — the device's lifecycle over the datagram seam. `open` binds the
      listen endpoint (`0.0.0.0:listen_port`, the POSIX any-address semantic; a
      `listen_port` of None assigns one and `local` reads it back). After `close` every
      face refuses `StaleHandle` (§14: the device is gone) and the lifecycle runs one
      way, like the stream and line fills.
    - `initiate` — the client side's handshake start (§5.4.2): one initiation message
      to the peer's current endpoint. Re-initiating supersedes the handshake in
      flight (retransmission is §6's timer machinery — named gap 3).
    - `send`/`receive` — the inner L3 packets (DeviceKind.WIREGUARD is a tunnel over
      UDP): `send` routes by `allowed_ips` (longest prefix match, §9) and refuses
      `NoRoute` for an inner destination no peer covers; `receive` hands back one
      inner packet at a time, `WouldBlock` when none waits. An inner source outside
      the sending peer's `allowed_ips` is refused `PermissionDenied`.
    - `pump` — drain the datagram socket: handshake and transport messages are
      processed here, a consumed initiation answers with its response (§5.4.3), and
      named refusals propagate to the caller — never a silent drop.
    - `handshakes`/`last_handshake` — the §11 `Interval` model: each handshake opens
      an interval and the next closes it (docs/24: nothing is deleted, intervals
      supersede).
    - `wait` — level-triggered readiness (§4): READ while an inner packet waits,
      WRITE while the device is open.
    """

    def __init__(
        self,
        config: schema.WireGuardDevice,
        sockets: Datagrams,
        *,
        secrets: Iterable[SecretValue] = (),
        ephemeral_provider: Callable[[], bytes] | None = None,
        index_provider: Callable[[], int] | None = None,
        timestamp_provider: Callable[[], bytes] | None = None,
        clock: Callable[[], int] | None = None,
        key_observer: Callable[[str, bytes], None] | None = None,
    ) -> None:
        self.config = config
        self._sockets = sockets
        self._ephemeral = ephemeral_provider if ephemeral_provider is not None else (lambda: os.urandom(_KEY_SIZE))
        self._index = index_provider if index_provider is not None else (lambda: int.from_bytes(os.urandom(4), "little"))
        self._clock = clock if clock is not None else (lambda: int(time.time()))
        self._timestamp = timestamp_provider if timestamp_provider is not None else (lambda: tai64n(int(self._clock())))
        self._observe = key_observer
        self._socket: DatagramSocket | None = None
        self._opened = False
        self._closed = False
        self._packets: list[bytes] = []
        material = self._vault(secrets)
        self._private = self._require(material, config.private_key, "the device's private_key")
        self._peers: dict[str, _Peer] = {}
        self._sessions: dict[int, _Peer] = {}
        for cell in config.peers:
            self._peers[cell.public_key] = self._peer(cell, material)

    # -- the §11 device faces ----------------------------------------------

    @property
    def path(self) -> str:
        """This device's cell path (`net/dev/wg0`)."""
        return self.config.path

    @property
    def local(self) -> Endpoint | None:
        """The bound listen endpoint — `0.0.0.0:listen_port`, or the assigned one
        when the config carries no port (§2.1: the listen port is the send port)."""
        self._guard_device()
        return None if self._socket is None else self._socket.local

    @property
    def peers(self) -> tuple[schema.WireGuardPeer, ...]:
        """The peer table — `WireGuardPeer` cells from `net.schema`, never redefined."""
        self._guard_device()
        return self.config.peers

    def open(self) -> None:
        """Open the device (§11): bind the datagram socket that carries it. Open on
        an open device is refused `NotSupported`; a closed device is gone for good."""
        if self._closed:
            raise StaleHandle(f"the device is gone: {self.config.path} was closed (§14)")
        if self._opened:
            raise NotSupported("open on an already-open device is refused by name")
        port = self.config.listen_port
        if port is None:
            port = 0  # assigned at bind; `local` reads it back (never guess a fixed port)
        self._socket = self._sockets.bind(Endpoint(b"\x00\x00\x00\x00", port, None))
        self._opened = True

    def close(self) -> None:
        """Close the device (§11): every face refuses from here (§14). Idempotent,
        like the stream and line fills' one-way close."""
        if self._socket is not None:
            self._socket.close()
        self._closed = True

    def initiate(self, peer: str) -> None:
        """Start the handshake with `peer` (its public key, hex — the peer cell's
        word) and send the §5.4.2 initiation to its current endpoint. The deterministic
        seams (ephemeral, index, timestamp) feed the message; the wire bytes are the
        corpus's."""
        self._guard_open()
        state = self._peer_by_name(peer)
        if state.endpoint is None:
            raise InvalidEndpoint(f"the peer {peer} has no endpoint to send to (§2.1): nothing configured, nothing learned")
        message, handshake = self._create_initiation(state)
        state.handshake = handshake
        self._send_wire(message, state.endpoint)

    def send(self, packet: bytes) -> None:
        """One inner IP packet out through the tunnel (§5.4.6): routed by the
        destination against every peer's `allowed_ips` (§9, longest prefix match),
        padded (§5.4.6) and sealed under the peer's session. With no session this
        refuses `NotConnected` — timer-driven initiation and queueing are §6's
        machinery (named gap 3), not a silent buffer here."""
        self._guard_open()
        data = _bytes(packet, "an inner packet")
        parsed = _parse_ip(data)
        if parsed is None:
            raise NotSupported("the tunnel carries IP packets (DeviceKind.WIREGUARD is L3 over UDP): this value is not one")
        _, _, destination = parsed
        state = self._route(destination)
        if state.session is None:
            raise NotConnected(f"the tunnel has no session with {state.cell.public_key}: call initiate first")
        if state.endpoint is None:
            raise InvalidEndpoint(f"the peer {state.cell.public_key} has no endpoint to send to (§2.1)")
        self._send_wire(state.session.seal(_pad(data)), state.endpoint)

    def receive(self) -> bytes:
        """One inner IP packet from the tunnel — `WouldBlock` when none waits (§4)."""
        self._guard_open()
        if self._packets:
            return self._packets.pop(0)
        raise WouldBlock("no inner packet is waiting")

    def pump(self) -> int:
        """Drain the datagram socket (§5): every waiting datagram is processed here —
        a consumed initiation is answered with its response (§5.4.3), a transport
        message is decrypted and queued, the endpoint of an authenticated sender
        updates (§2.1) — and named refusals propagate. Returns the count processed."""
        self._guard_open()
        assert self._socket is not None  # _guard_open guarantees it
        processed = 0
        while True:
            try:
                datagram = self._socket.recvfrom()
            except WouldBlock:
                return processed
            self._process(datagram.payload, datagram.src)
            processed += 1

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        """Level-triggered readiness (§4): READ while an inner packet waits (or the
        socket holds datagrams `pump` would process), WRITE while the device is open.
        Nothing ready is an empty wake; a timeout is never an error."""
        self._guard_open()
        ready = Interest(0)
        if self._packets or (self._socket is not None and self._socket.wait(Interest.READ) != Interest(0)):
            ready |= Interest.READ
        ready |= Interest.WRITE
        return ready & interest

    def handshakes(self, peer: str) -> tuple[Interval, ...]:
        """The §11 handshake history for `peer` — each `Interval` closed by the next
        handshake, nothing deleted (docs/24)."""
        self._guard_device()
        return tuple(self._peer_by_name(peer).handshakes)

    def last_handshake(self, peer: str) -> Interval | None:
        """The current handshake `Interval` for `peer`, or None — §11: "`last_handshake`
        is an `Interval` closed by the next handshake"."""
        self._guard_device()
        history = self._peer_by_name(peer).handshakes
        return history[-1] if history else None

    def __repr__(self) -> str:
        return (
            f"WireGuardDevice(path={self.config.path!r}, listen_port={self.config.listen_port!r}, "
            f"peers={len(self.config.peers)})"
        )

    # -- the handshake (§5.4.2, §5.4.3) -------------------------------------

    def _create_initiation(self, state: _Peer) -> tuple[bytes, _Handshake]:
        card = _crypto()
        try:
            own_public = card.x25519_public(self._private._material)
            mac_key = _mac_key(state.public)
            chain_key = card.blake2s(CONSTRUCTION)
            hash_ = _mix_hash(card.blake2s(chain_key + IDENTIFIER), state.public)  # Hi := Hash(Hi‖Spub_r)
            local_ephemeral = _bytes(self._ephemeral(), "an ephemeral private key")
            if len(local_ephemeral) != _KEY_SIZE:
                raise NotSupported(f"an ephemeral private key is {_KEY_SIZE} bytes: this value is not one")
            ephemeral_public = card.x25519_public(local_ephemeral)
            chain_key = _kdf(chain_key, ephemeral_public)[0]  # Ci := Kdf1(Ci, Epub_i)
            hash_ = _mix_hash(hash_, ephemeral_public)
            chain_key, key = _kdf(chain_key, card.x25519(local_ephemeral, state.public))[:2]
            sealed_static = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, own_public)
            hash_ = _mix_hash(hash_, sealed_static)
            chain_key, key = _kdf(chain_key, card.x25519(self._private._material, state.public))[:2]
            timestamp = _bytes(self._timestamp(), "a TAI64N timestamp")
            if len(timestamp) != 12:
                raise NotSupported("a TAI64N timestamp is 12 bytes (§5.1): this value is not one")
            sealed_timestamp = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, timestamp)
            hash_ = _mix_hash(hash_, sealed_timestamp)
        except crypto.CryptoRefusal as error:
            raise NotSupported(f"the card refused this device's keys: {error}") from error
        sender = self._sender_index()
        message = (
            struct.pack("<II", MESSAGE_HANDSHAKE_INITIATION, sender)
            + ephemeral_public
            + sealed_static
            + sealed_timestamp
        )
        return _add_macs(message, mac_key), _Handshake(hash_, chain_key, local_ephemeral, sender)

    def _consume_initiation(self, message: bytes, source: Endpoint) -> None:
        """§5.4.2 on the responder: verify mac1 (§5.3: always), decrypt the identity,
        check the §5.1 timestamp, then answer with the §5.4.3 response and begin the
        session — one call, no state for anything unauthenticated (§5.1)."""
        if len(message) != INITIATION_SIZE:
            raise MalformedMessage(f"a handshake initiation is {INITIATION_SIZE} bytes (§5.4.2): this one is not")
        card = _crypto()
        own_public = card.x25519_public(self._private._material)
        mac1 = message[INITIATION_SIZE - 2 * MAC_SIZE : INITIATION_SIZE - MAC_SIZE]
        if not hmac.compare_digest(_mac128(_mac_key(own_public), message[: INITIATION_SIZE - 2 * MAC_SIZE]), mac1):
            raise MalformedMessage("the initiation's mac1 is not valid (§5.3): it is not a message for this device")
        remote_ephemeral = message[8:40]
        sealed_static = message[40:88]
        sealed_timestamp = message[88:116]
        try:
            hash_ = _mix_hash(_mix_hash(card.blake2s(card.blake2s(CONSTRUCTION) + IDENTIFIER), own_public), remote_ephemeral)
            chain_key = _kdf(card.blake2s(CONSTRUCTION), remote_ephemeral)[0]
            chain_key, key = _kdf(chain_key, card.x25519(self._private._material, remote_ephemeral))[:2]
            remote_static = card.aead_open(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, sealed_static)
            hash_ = _mix_hash(hash_, sealed_static)
            chain_key, key = _kdf(chain_key, card.x25519(self._private._material, remote_static))[:2]
            timestamp = card.aead_open(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, sealed_timestamp)
            hash_ = _mix_hash(hash_, sealed_timestamp)
        except crypto.CryptoRefusal as error:
            raise MalformedMessage(f"the initiation does not verify: {error}") from error
        state = self._peers.get(remote_static.hex())
        if state is None:
            raise MalformedMessage("the initiation's static key names no peer of this device (§5.4.2)")
        if state.last_timestamp and timestamp <= state.last_timestamp:
            raise UnexpectedReply("the initiation's timestamp is not newer than the last one (§5.1): refused")
        self._roam(state, source)  # §2.1: a correctly authenticated packet moves the endpoint
        # §5.4.3's response from the same state variables — the psk step lands HERE,
        # after the response's own ephemeral mixes (§5.4.3's order, exactly).
        sender = self._sender_index()
        try:
            ephemeral_private = _bytes(self._ephemeral(), "an ephemeral private key")
            if len(ephemeral_private) != _KEY_SIZE:
                raise NotSupported(f"an ephemeral private key is {_KEY_SIZE} bytes: this value is not one")
            ephemeral_public = card.x25519_public(ephemeral_private)
            hash_ = _mix_hash(hash_, ephemeral_public)
            chain_key = _kdf(chain_key, ephemeral_public)[0]
            chain_key = _kdf(chain_key, card.x25519(ephemeral_private, remote_ephemeral))[0]
            chain_key = _kdf(chain_key, card.x25519(ephemeral_private, remote_static))[0]
        except crypto.CryptoRefusal as error:
            raise NotSupported(f"the card refused this device's keys: {error}") from error
        chain_key, tau, key = _kdf(chain_key, state.preshared._material if state.preshared is not None else _ZERO_KEY)
        hash_ = _mix_hash(hash_, tau)
        try:
            sealed_empty = card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, b"")
        except crypto.CryptoRefusal as error:
            raise NotSupported(f"the card refused this device's keys: {error}") from error
        hash_ = _mix_hash(hash_, sealed_empty)
        response = struct.pack("<III", MESSAGE_HANDSHAKE_RESPONSE, sender, struct.unpack("<I", message[4:8])[0])
        response = response + ephemeral_public + sealed_empty
        # mac1 names the RECIPIENT's static key (§5.4.4) — this response is for the
        # initiator, whose static key is the one we just decrypted out of the message.
        response = _add_macs(response, _mac_key(state.public))
        # The session begins here on the responder (§5.4.5): keys, indexes, counters.
        transport_i2r, transport_r2i = _kdf(chain_key, b"")[:2]
        self._begin_session(state, sender, struct.unpack("<I", message[4:8])[0], transport_i2r, transport_r2i)
        state.last_timestamp = timestamp
        self._send_wire(response, source)

    def _consume_response(self, message: bytes, source: Endpoint) -> None:
        """§5.4.3 on the initiator: finish the handshake, begin the session (§5.4.5),
        and close the old `last_handshake` interval (§11)."""
        if len(message) != RESPONSE_SIZE:
            raise MalformedMessage(f"a handshake response is {RESPONSE_SIZE} bytes (§5.4.3): this one is not")
        receiver = struct.unpack("<I", message[8:12])[0]
        state = next((s for s in self._peers.values() if s.handshake is not None and s.handshake.local_index == receiver), None)
        if state is None or state.handshake is None:
            raise StaleHandle(f"no handshake in flight names receiver index {receiver} (§14): the response is stale")
        # mac1 names the RECIPIENT's static key (§5.4.4) — the response is addressed
        # to this device, so the mac runs under OUR public key.
        card = _crypto()
        mac1 = message[RESPONSE_SIZE - 2 * MAC_SIZE : RESPONSE_SIZE - MAC_SIZE]
        if not hmac.compare_digest(_mac128(_mac_key(card.x25519_public(self._private._material)), message[: RESPONSE_SIZE - 2 * MAC_SIZE]), mac1):
            raise MalformedMessage("the response's mac1 is not valid (§5.3): it is not a message for this device")
        handshake = state.handshake
        remote_ephemeral = message[12:44]
        sealed_empty = message[44:60]
        try:
            hash_ = _mix_hash(handshake.hash, remote_ephemeral)
            chain_key = _kdf(handshake.chain_key, remote_ephemeral)[0]
            chain_key = _kdf(chain_key, card.x25519(handshake.local_ephemeral, remote_ephemeral))[0]
            chain_key = _kdf(chain_key, card.x25519(self._private._material, remote_ephemeral))[0]
        except crypto.CryptoRefusal as error:
            raise MalformedMessage(f"the response's ephemeral is not a usable key: {error}") from error
        chain_key, tau, key = _kdf(chain_key, state.preshared._material if state.preshared is not None else _ZERO_KEY)
        hash_ = _mix_hash(hash_, tau)
        try:
            opened = card.aead_open(crypto.CIPHER_CHACHA20_POLY1305, key, _ZERO_NONCE, hash_, sealed_empty)
        except crypto.CryptoRefusal as error:
            raise MalformedMessage(f"the response does not verify: {error}") from error
        if opened != b"":
            raise MalformedMessage("the response's authenticated empty is not empty (§5.4.3)")
        hash_ = _mix_hash(hash_, sealed_empty)
        transport_i2r, transport_r2i = _kdf(chain_key, b"")[:2]
        self._begin_session(state, handshake.local_index, struct.unpack("<I", message[4:8])[0], transport_i2r, transport_r2i)
        state.handshake = None
        self._roam(state, source)

    def _consume_transport(self, message: bytes, source: Endpoint) -> None:
        """§5.4.6 on the receiving side: route by receiver index (a gone session is
        `StaleHandle`, §14), verify the tag, apply the replay window (§5.4.6), strip
        the padding by the inner IP length field, and enforce `allowed_ips` (§9)."""
        if len(message) < TRANSPORT_MINIMUM:
            raise MalformedMessage(f"a transport message is {TRANSPORT_MINIMUM} bytes or more (§5.4.6): this one is not")
        receiver = struct.unpack("<I", message[4:8])[0]
        state = self._sessions.get(receiver)
        if state is None or state.session is None:
            raise StaleHandle(f"no session of this device names receiver index {receiver} (§14)")
        payload = state.session.open(message)  # the tag first, then the replay window (§5.4.6)
        self._roam(state, source)  # §2.1: a correctly authenticated packet moves the endpoint
        if not payload:
            return  # a keepalive (§6.5): real traffic, no inner packet
        parsed = _parse_ip(payload)
        if parsed is None:
            raise MalformedMessage("the inner packet does not parse as IP (§5.4.6 carries L3 packets)")
        total, source_ip, _ = parsed
        if not any(_prefix_match(source_ip, prefix) for prefix in state.cell.allowed_ips):
            raise PermissionDenied(
                f"the inner source {source_ip.hex()} is outside {state.cell.public_key}'s allowed_ips (§9): refused"
            )
        self._packets.append(payload[:total])

    def _consume_cookie_reply(self, message: bytes) -> None:
        """§5.4.7's cookie reply — structurally validated, then refused (named gap 1).

        The wire is `Xaead(Hash(Label-Cookie‖Spub_m), nonce, τ, M)` over a 24-byte
        nonce (XChaCha20Poly1305); the frozen card cannot express it, and §5.3's third
        flaw is exactly what an uncheckable cookie would bring. Fail closed: the
        cookie is never accepted, never stored, never a MAC key. `_cookie_key` is the
        derivation that acceptance will use the moment the card grows the face."""
        if len(message) != COOKIE_REPLY_SIZE:
            raise MalformedMessage(f"a cookie reply is {COOKIE_REPLY_SIZE} bytes (§5.4.7): this one is not")
        receiver = struct.unpack("<I", message[4:8])[0]
        if all(state.handshake is None or state.handshake.local_index != receiver for state in self._peers.values()):
            raise StaleHandle(f"no handshake in flight names receiver index {receiver} (§14)")
        raise NotSupported(
            "a cookie reply's Xaead (XChaCha20Poly1305, 24-byte nonce) is not on the frozen cap.crypto card: "
            "accepted cookies are refused by name (§5.4.7; module docstring gap 1)"
        )

    # -- wire plumbing -------------------------------------------------------

    def _process(self, payload: bytes, source: Endpoint) -> None:
        message = _bytes(payload, "a wire message")
        if len(message) < 4:
            raise MalformedMessage("a WireGuard message starts with its 4-byte type (§5.4): this one is not")
        kind = struct.unpack("<I", message[:4])[0]
        if kind == MESSAGE_HANDSHAKE_INITIATION:
            self._consume_initiation(message, source)
        elif kind == MESSAGE_HANDSHAKE_RESPONSE:
            self._consume_response(message, source)
        elif kind == MESSAGE_COOKIE_REPLY:
            self._consume_cookie_reply(message)
        elif kind == MESSAGE_TRANSPORT_DATA:
            self._consume_transport(message, source)
        else:
            raise MalformedMessage(f"the message type {kind} is not one of the protocol's four (§5.4)")

    def _send_wire(self, message: bytes, endpoint: Endpoint) -> None:
        self._guard_open()
        assert self._socket is not None
        self._socket.sendto(message, endpoint)

    def _begin_session(
        self,
        state: _Peer,
        local_index: int,
        remote_index: int,
        transport_i2r: bytes,
        transport_r2i: bytes,
    ) -> None:
        """§5.4.5's key assignment (`Tsend_i = Trecv_r`, `Trecv_i = Tsend_r`) and the
        §11 `Interval` move — the previous handshake closes, the new one opens."""
        if self._observe is not None:
            self._observe("transport_i2r", transport_i2r)
            self._observe("transport_r2i", transport_r2i)
        ours_send, ours_recv = (
            (transport_i2r, transport_r2i)  # the initiator sends with Tsend_i = Trecv_r's T1
            if self._is_initiator(state, local_index)
            else (transport_r2i, transport_i2r)
        )
        session = Session(ours_send, ours_recv, local_index=local_index, remote_index=remote_index)
        state.session = session
        self._sessions[local_index] = state
        now = int(self._clock())
        if state.handshakes:
            state.handshakes[-1] = Interval(state.handshakes[-1].valid_from, now)  # closed by this handshake
        state.handshakes.append(Interval(now, None))

    @staticmethod
    def _is_initiator(state: _Peer, local_index: int) -> bool:
        return state.handshake is not None and state.handshake.local_index == local_index

    def _roam(self, state: _Peer, source: Endpoint) -> None:
        """§2.1: "the outer external source IP of an encrypted WireGuard packet is
        used to identify the remote endpoint of a peer" — every authenticated message
        moves the endpoint to where it came from."""
        state.endpoint = source

    def _route(self, destination: bytes) -> _Peer:
        """§9's routing decision for what to send: longest prefix match over every
        peer's `allowed_ips`, both families one lookup. No match refuses `NoRoute`."""
        best: tuple[int, _Peer] | None = None
        for state in self._peers.values():
            for prefix in state.cell.allowed_ips:
                if _prefix_match(destination, prefix) and (best is None or prefix.length > best[0]):
                    best = (prefix.length, state)
        if best is None:
            raise NoRoute(f"no peer's allowed_ips covers {destination.hex()} (§9): the packet has no tunnel")
        return best[1]

    def _sender_index(self) -> int:
        """One u32 sender index (§5.4.2's `Ii`/`Ir`, ρ4 random by default)."""
        value = self._index()
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFFFF:
            raise NotSupported("a sender index is a word in 0..2^32-1: this value is not one")
        return value

    def _peer_by_name(self, public_key: str) -> _Peer:
        try:
            return self._peers[public_key]
        except KeyError:
            raise InvalidEndpoint(f"no peer of this device is named {public_key!r} (§3)") from None

    def _peer(self, cell: schema.WireGuardPeer, material: dict[str, SecretValue]) -> _Peer:
        try:
            public = bytes.fromhex(cell.public_key)
        except ValueError:
            raise InvalidEndpoint(f"the peer's public_key is not hex: {cell.public_key!r} (§3)") from None
        if len(public) != _KEY_SIZE:
            raise InvalidEndpoint(f"a peer's public_key is {_KEY_SIZE} bytes of hex (§3): this one is not")
        preshared = None
        if cell.preshared is not None:
            preshared = self._require(material, cell.preshared, f"the peer {cell.public_key}'s preshared")
        return _Peer(cell, public, preshared, cell.endpoint)

    @staticmethod
    def _vault(secrets: Iterable[SecretValue]) -> dict[str, SecretValue]:
        """The key material behind this device's `Secret` cells, by cell path. One
        material per cell path — two materials naming one cell is a refused config,
        not a race."""
        vault: dict[str, SecretValue] = {}
        for value in secrets:
            if value.path in vault:
                raise NotSupported(f"two key materials name one Secret cell: {value.path} (§17)")
            vault[value.path] = value
        return vault

    @staticmethod
    def _require(material: dict[str, SecretValue], cell: Secret, what: str) -> SecretValue:
        try:
            return material[cell.path]
        except KeyError:
            raise NotSupported(f"{what} names the Secret cell {cell.path}, and no material for it was given") from None

    def _guard_device(self) -> None:
        """A closed device is gone (§14) — its state reads refuse with the handle."""
        if self._closed:
            raise StaleHandle(f"the device is gone: {self.config.path} was closed (§14)")

    def _guard_open(self) -> None:
        self._guard_device()
        if not self._opened:
            raise NotConnected("the device is not open")
