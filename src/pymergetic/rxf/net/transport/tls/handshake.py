"""The TLS 1.3 handshake (RFC 8446 §4) and the secure stream it leaves behind.

One card, both roles: `TlsConfig.role` selects client or server (§11). The
client sends ClientHello with an X25519 `key_share` (§4.2.8) and may be answered
with a HelloRetryRequest (§4.1.3/§4.1.4, the `message_hash` transcript rule of
§4.4.1 included); the server answers ServerHello, EncryptedExtensions,
Certificate, CertificateVerify (§4.4.3, Ed25519 — the tree's one signature, in
`pymergetic.rxf.certify`) and Finished (§4.4.4); the client closes with its
Finished. That is the full 1-RTT of §4.1.1; the key schedule (§7.1) is
`keysched`, the record layer (§5) is `records`, and the middlebox compatibility
mode of Appendix D.4 rides along (non-empty legacy_session_id echoed, dummy
change_cipher_spec records, both ignored exactly as §5 demands).

ALPN (RFC 7301) and `server_name` (§4.2.1) come from `TlsConfig`: the client
offers what the config names, the server selects what both name and refuses
`no_application_protocol` when it names protocols and none overlap.

Refused by name (each with a test in `tests/test_net_tls.py`):

- TLS 1.2 and older ClientHello (and a ServerHello that answers with anything
  but TLS 1.3) — `protocol_version`.
- renegotiation (any handshake message once the handshake is done) —
  `unexpected_message`.
- 0-RTT early data (the `early_data` extension, or application data before the
  handshake is done) — `handshake_failure` / `unexpected_message`.
- PSK and resumption (`pre_shared_key`, `psk_key_exchange_modes`,
  `session_ticket`, `NewSessionTicket`) — `handshake_failure` /
  `unexpected_message`.
- `cookie` (stateless HelloRetryRequest) and client authentication
  (CertificateRequest) — named refusals, `handshake_failure`/`unexpected_message`.
- signature schemes the tree does not have (RSA-PSS, ECDSA, …) —
  `illegal_parameter` (seam request: those schemes' primitives).

Named gaps (honest, not silent): no post-handshake KeyUpdate; no client
certificates; no chain validation beyond the roots cell pin (`certs.py`);
resumption secrets are never derived or kept. One seam note: the stream contract
carries a deadline but no clock, so an operation served entirely from this
wrapper's own buffers does not itself evaluate the deadline — the fill under it
does (seam request: a clock word on the Stream contract).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from enum import Enum
from typing import NoReturn

from ....certify import ed25519
from ...crypto import Crypto, CryptoRefusal
from ...schema import (
    ConnectionReset,
    Interest,
    MalformedMessage,
    NotConnected,
    NotSupported,
    PermissionDenied,
    Role,
    StaleHandle,
    TlsConfig,
    WouldBlock,
)
from ..stream import Stream
from . import certs, keysched, msgs, records
from .alerts import FATAL, WARNING, TlsAlert, alert_bytes, name_of
from .keysched import Suite
from .records import Protection, Record, RecordBuffer

_PARK = 0.001  # seconds, only after an empty wake from a poll-style fill wait
_CHUNK = 65536
_MAX_PENDING = 1 << 17  # two-plus records of wire bytes held before refusing more


class _State(Enum):
    CLIENT_WAIT_HELLO = "client: waiting for ServerHello"
    CLIENT_WAIT_FLIGHT = "client: waiting for the server flight"
    SERVER_WAIT_CH = "server: waiting for ClientHello"
    SERVER_WAIT_CH2 = "server: waiting for the second ClientHello"
    SERVER_WAIT_FINISHED = "server: waiting for the client Finished"
    ESTABLISHED = "established"


class Transcript:
    """The running transcript (§4.4.1): handshake messages, in order, hashed once.

    The HelloRetryRequest exception is here too: the first ClientHello is
    replaced by the synthetic `message_hash` message carrying its hash.
    """

    def __init__(self, card: Crypto) -> None:
        self._card = card
        self._bytes = bytearray()

    def update(self, message: bytes) -> None:
        """One whole handshake message (type || uint24 length || body) joins."""
        self._bytes.extend(message)

    def hash(self) -> bytes:
        """Transcript-Hash over everything joined so far."""
        return self._card.sha256(bytes(self._bytes))

    def fold_message_hash(self) -> None:
        """The §4.4.1 exception: `message_hash(Hash(CH1))` replaces ClientHello1."""
        self._bytes = bytearray(msgs.message_hash_message(self.hash()))


class TlsStream(Stream):
    """One TLS 1.3 connection over an inner stream: the wrap seam's answer (§11).

    The Stream contract is untouched — `read`/`write`/`wait`/`close` are the
    same words a plain stream has, and `wrapped` says only what §12 lets the
    protocol see. `step()` is the non-blocking driver of the handshake (one unit
    of progress per call, `WouldBlock`-free); `establish()` is the composed
    blocking version `wrap` runs. Key material never leaves this object: there
    is no export face and the rendering carries no key bytes (§17).
    """

    def __init__(
        self,
        inner: Stream,
        config: TlsConfig,
        *,
        card: Crypto | None = None,
        entropy: Callable[[int], bytes] | None = None,
    ) -> None:
        if config.min_version != "1.3":
            raise NotSupported(
                f"min_version {config.min_version!r} is refused by name: this card "
                "speaks TLS 1.3 only (docs/25 §17)"
            )
        if config.role is Role.SERVER:
            if not config.certificates:
                raise NotSupported(
                    "a server config names no Certificate cell to answer with"
                )
            if config.certificates[1:]:
                raise NotSupported(
                    "chains of several Certificate cells are refused by name: the "
                    "leaf cell carries its own chain (seam request: chain building)"
                )
            certs.certificate_of(config.certificates[0])  # fail fast, before the wire
        else:
            if config.certificates:
                raise NotSupported(
                    "client authentication is refused by name in this build: a client "
                    "config names no certificates (seam request: mutual TLS)"
                )
            certs.roots_of(config.roots)  # fail fast, before the wire
        self._inner = inner
        self._config = config
        self._card = card if card is not None else keysched.card()
        self._entropy = entropy if entropy is not None else secrets.token_bytes
        self._wire = RecordBuffer()
        self._messages = msgs.HandshakeBuffer()
        self._pending = bytearray()
        self._flight = bytearray()
        self._recv_plain = bytearray()
        self._send: Protection | None = None
        self._recv: Protection | None = None
        self._transcript = Transcript(self._card)
        self._share: bytes = b""  # this side's X25519 scalar (key material)
        self._session_id = b""
        self._client_random = b""
        self._suite: Suite | None = None
        self._state = (
            _State.CLIENT_WAIT_HELLO if config.role is Role.CLIENT else _State.SERVER_WAIT_CH
        )
        self._wire_eof = False
        self._eof = False
        self._closed = False
        self._hrr_done = False
        self._peer_public = b""
        self._seen_name: str | None = None
        self._alpn: str | None = None
        self._stage = 0  # where one side's flight stands (0..3)
        self._ch1_random = b""
        self._c_hs = b""
        self._s_hs = b""
        self._hs_root = b""
        self._master = b""
        self._c_ap: keysched.TrafficKeys | None = None
        if config.role is Role.CLIENT:
            self._send_client_hello(first=True)

    # ---- the words the protocol may see (§12: nothing more) ----

    @property
    def handle(self) -> tuple[int, int]:
        return self._inner.handle

    @property
    def wrapped(self) -> bool:
        return True

    @property
    def deadline(self) -> int | None:
        return self._inner.deadline

    def setdeadline(self, deadline: int | None) -> None:
        self._inner.setdeadline(deadline)

    def connect(self, peer: tuple[str, int]) -> None:
        self._inner.connect(peer)

    @property
    def established(self) -> bool:
        """Whether the 1-RTT handshake completed in both directions."""
        return self._state is _State.ESTABLISHED

    @property
    def negotiated_alpn(self) -> str | None:
        """The protocol ALPN settled on, or none — from `TlsConfig.alpn`, both sides."""
        return self._alpn

    @property
    def server_name(self) -> str | None:
        """The SNI name: sent by the client, seen by the server (§4.2.1)."""
        return self._config.server_name if self._config.role is Role.CLIENT else self._seen_name

    def __repr__(self) -> str:
        role = self._config.role.value
        return f"TlsStream(role={role!r}, established={self.established})"

    # ---- the Stream face ----

    def read(self, size: int) -> bytes:
        self._guard()
        if self._state is not _State.ESTABLISHED:
            raise NotConnected("the TLS handshake has not completed")
        if size <= 0:
            return b""
        while not self._recv_plain:
            if self._eof:
                return b""
            record = self._take_record()
            if record is None:
                self._eof = True
                return b""
            self._handle_record(record)
        out = bytes(self._recv_plain[:size])
        del self._recv_plain[:size]
        return out

    def write(self, data: bytes) -> int:
        self._guard()
        if self._state is not _State.ESTABLISHED:
            raise NotConnected("the TLS handshake has not completed")
        if not data:
            return 0
        self._flush()
        if len(self._pending) >= _MAX_PENDING:
            raise WouldBlock("the TLS send buffer is full")
        take = min(len(data), records.MAX_PLAINTEXT)
        assert self._send is not None
        self._pending += self._send.seal(records.CONTENT_APPLICATION_DATA, data[:take])
        self._flush()
        return take

    def wait(self, interest: Interest, timeout: int | None = None) -> Interest:
        self._guard()
        ready = Interest(0)
        if (interest & Interest.READ) and (self._recv_plain or self._eof):
            ready |= Interest.READ
        if (interest & Interest.WRITE) and len(self._pending) < _MAX_PENDING:
            ready |= Interest.WRITE
        want = interest & ~ready
        if want:
            ready |= self._inner.wait(want, timeout)
        return ready & interest

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._state is _State.ESTABLISHED and not self._eof:
                self._alert_raw("close_notify", WARNING)
                self._flush()
        except (TlsAlert, ConnectionReset, StaleHandle, WouldBlock, NotConnected):
            pass  # a close is best effort; the socket goes either way
        self._closed = True
        self._inner.close()

    def _guard(self) -> None:
        if self._closed:
            raise StaleHandle("the socket is gone")

    # ---- the handshake driver ----

    def establish(self) -> None:
        """Run the handshake to completion: the composed blocking wait (§4)."""
        while self._state is not _State.ESTABLISHED:
            if self.step():
                continue
            if not self._inner.wait(Interest.READ):
                time.sleep(_PARK)  # an empty wake: let the far side run before polling

    def step(self) -> bool:
        """One unit of progress — never blocks; True when something moved.

        Refusals arrive as `TlsAlert` (carrying the wire alert name) with the
        alert already sent to the peer where the socket allowed it. After the
        handshake the same step keeps consuming wire records, so alerts and
        post-handshake messages (renegotiation, NewSessionTicket) are refused
        as they arrive rather than parked in the buffer.
        """
        self._guard()
        self._flush()
        try:
            record = self._take_record()
        except WouldBlock:
            return False
        if record is None:
            if self._state is _State.ESTABLISHED:
                self._eof = True
                return False
            raise ConnectionReset("the peer closed during the TLS handshake")
        self._handle_record(record)
        return True

    # ---- wire in and out ----

    def _flush(self) -> None:
        while self._pending:
            taken = self._inner.write(bytes(self._pending))
            if taken == 0:
                return
            del self._pending[:taken]

    def _out_plaintext(self, content_type: int, fragment: bytes, legacy: int) -> None:
        self._pending += records.encode(content_type, fragment, legacy)
        self._flush()

    def _out_protected(self, content_type: int, content: bytes) -> None:
        assert self._send is not None
        self._pending += self._send.seal(content_type, content)
        self._flush()

    def _take_record(self) -> Record | None:
        record = self._wire.take()
        if record is not None:
            return record
        if self._wire_eof:
            if self._wire.buffered:
                raise ConnectionReset("the peer closed mid-record")
            return None
        while True:
            try:
                chunk = self._inner.read(_CHUNK)
            except WouldBlock:
                break
            if chunk == b"":
                self._wire_eof = True
                break
            self._wire.feed(chunk)
        record = self._wire.take()
        if record is not None:
            return record
        if self._wire_eof:
            if self._wire.buffered:
                raise ConnectionReset("the peer closed mid-record")
            return None
        raise WouldBlock("nothing to read yet")

    def _alert_raw(self, name: str, level: int) -> None:
        payload = alert_bytes(name, level)
        if self._send is not None:
            self._pending += self._send.seal(records.CONTENT_ALERT, payload)
        else:
            self._pending += records.encode(records.CONTENT_ALERT, payload)

    def _refuse(self, message: str, alert: str) -> NoReturn:
        """Send the alert the refusal names, then refuse (§17 style, §6 wire)."""
        try:
            self._alert_raw(alert, FATAL)
            self._flush()
        except (ConnectionReset, StaleHandle, WouldBlock, NotConnected):
            pass  # the refusal still stands; the wire just refused to carry it
        raise TlsAlert(message, alert)

    # ---- record dispatch (§5.2: the content type lives inside once protected) ----

    def _handle_record(self, record: Record) -> None:
        try:
            content_type, content = self._unwrap(record)
            self._handle_content(content_type, content)
        except MalformedMessage as error:
            self._refuse(str(error), "decode_error")

    def _unwrap(self, record: Record) -> tuple[int, bytes]:
        if self._recv is not None and record.content_type == records.CONTENT_APPLICATION_DATA:
            return self._recv.open(record)
        return record.content_type, record.fragment

    def _handle_content(self, content_type: int, content: bytes) -> None:
        if content_type == records.CONTENT_CHANGE_CIPHER_SPEC:
            if self._state is _State.ESTABLISHED:
                self._refuse(
                    "a change_cipher_spec record after the handshake",
                    "unexpected_message",
                )
            if content != b"\x01":
                self._refuse("a change_cipher_spec record is one 0x01 byte", "decode_error")
            return  # §5 / D.4: dummy records, ignored by both peers
        if content_type == records.CONTENT_ALERT:
            self._handle_alert(content)
            return
        if content_type == records.CONTENT_HANDSHAKE:
            self._messages.feed(content)
            while True:
                message = self._messages.take()
                if message is None:
                    return
                self._handle_message(*message)
        if content_type == records.CONTENT_APPLICATION_DATA:
            if self._state is not _State.ESTABLISHED:
                self._refuse(
                    "0-RTT early data is refused by name: this card runs the 1-RTT "
                    "handshake only (docs/25 §17)",
                    "unexpected_message",
                )
            self._recv_plain.extend(content)
            return
        self._refuse(f"content type {content_type} is not a TLS record type", "decode_error")

    def _handle_alert(self, content: bytes) -> None:
        if len(content) != 2:
            self._refuse("an alert record carries two bytes (RFC 8446 §6)", "decode_error")
        level, description = content[0], content[1]
        name = name_of(description)
        if name == "close_notify":
            self._eof = True
            return
        tone = "warning" if level == WARNING else "fatal"
        raise TlsAlert(f"the peer sent a {tone} {name} alert", name)

    # ---- handshake messages (§4) ----

    def _handle_message(self, msg_type: int, body: bytes) -> None:
        if self._state is _State.ESTABLISHED:
            if msg_type == msgs.NEW_SESSION_TICKET:
                self._refuse(
                    "NewSessionTicket (session resumption) is refused by name: "
                    "PSK and resumption are not implemented in this build (docs/25 §17)",
                    "unexpected_message",
                )
            self._refuse(
                "renegotiation is refused by name: TLS 1.3 has none and this card "
                "accepts no handshake message after the handshake (docs/25 §17)",
                "unexpected_message",
            )
        if self._config.role is Role.CLIENT:
            self._client_message(msg_type, body)
        else:
            self._server_message(msg_type, body)

    def _client_message(self, msg_type: int, body: bytes) -> None:
        if msg_type == msgs.SERVER_HELLO and self._state is _State.CLIENT_WAIT_HELLO:
            self._client_server_hello(body)
            return
        if self._state is _State.CLIENT_WAIT_FLIGHT:
            self._client_flight(msg_type, body)
            return
        self._refuse(
            f"a {msgs_msg_name(msg_type)} where the client waits for the ServerHello",
            "unexpected_message",
        )

    def _server_message(self, msg_type: int, body: bytes) -> None:
        if msg_type == msgs.CLIENT_HELLO and self._state in (
            _State.SERVER_WAIT_CH,
            _State.SERVER_WAIT_CH2,
        ):
            self._server_client_hello(body)
            return
        if msg_type == msgs.FINISHED and self._state is _State.SERVER_WAIT_FINISHED:
            self._server_finished(body)
            return
        self._refuse(
            f"a {msgs_msg_name(msg_type)} where the server waits for "
            f"{self._state.value}",
            "unexpected_message",
        )

    # ---- the client side ----

    def _client_hello(self, shares: tuple[tuple[int, bytes], ...]) -> msgs.ClientHello:
        config = self._config
        extensions: list[tuple[int, bytes]] = [
            (
                msgs.EXT_SUPPORTED_VERSIONS,
                msgs.encode_supported_versions_client((msgs.TLS_1_3,)),
            ),
            (msgs.EXT_SUPPORTED_GROUPS, msgs.encode_groups((msgs.GROUP_X25519,))),
            (msgs.EXT_SIGNATURE_ALGORITHMS, msgs.encode_schemes((msgs.SCHEME_ED25519,))),
            (msgs.EXT_KEY_SHARE, msgs.encode_key_share_client(shares)),
        ]
        if config.server_name is not None:
            extensions.append(
                (msgs.EXT_SERVER_NAME, msgs.encode_server_name(config.server_name))
            )
        if config.alpn:
            extensions.append((msgs.EXT_ALPN, msgs.encode_alpn(config.alpn)))
        return msgs.ClientHello(
            legacy_version=msgs.LEGACY_HELLO_VERSION,
            # §4.1.4: the second hello is the first one's echo — the random is
            # chosen once and never replaced.
            random=self._client_random,
            session_id=self._session_id,
            cipher_suites=keysched.OFFERED,
            extensions=tuple(extensions),
        )

    def _send_client_hello(self, *, first: bool) -> None:
        if first:
            self._session_id = self._entropy(32)  # D.4: middlebox compatibility mode
            self._client_random = self._entropy(32)  # one random for both hellos
        # §4.2.8: one key_share for the one group — and §4.1.4: a hello after a
        # HelloRetryRequest carries a fresh share, never the refused one again.
        self._share = self._entropy(32)
        shares: tuple[tuple[int, bytes], ...] = (
            (msgs.GROUP_X25519, self._card.x25519_public(self._share)),
        )
        hello = self._client_hello(shares)
        message = msgs.encode_handshake(msgs.CLIENT_HELLO, msgs.encode_client_hello(hello))
        self._transcript.update(message)
        legacy = records.INITIAL_VERSION if first else records.LEGACY_VERSION
        if not first:
            self._out_plaintext(  # D.4: the dummy record before the second flight
                records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
            )
        self._out_plaintext(records.CONTENT_HANDSHAKE, message, legacy)
        if not first:
            self._state = _State.CLIENT_WAIT_HELLO

    def _client_server_hello(self, body: bytes) -> None:
        hello = msgs.decode_server_hello(body)
        message = msgs.encode_handshake(msgs.SERVER_HELLO, body)
        if hello.retry_request():
            self._client_hello_retry(hello, message)
            return
        if hello.session_id != self._session_id:
            self._refuse(
                "the ServerHello echoes a legacy_session_id that is not ours "
                "(RFC 8446 §4.1.3)",
                "illegal_parameter",
            )
        selected = msgs.extension(hello.extensions, msgs.EXT_SUPPORTED_VERSIONS)
        if selected is None or msgs.decode_supported_versions_server(selected) != msgs.TLS_1_3:
            self._refuse(
                "TLS 1.2 and older are refused by name (docs/25 §17): the server "
                "selected no TLS 1.3",
                "protocol_version",
            )
        try:
            self._suite = keysched.suite_of(hello.cipher_suite)
        except NotSupported as error:
            self._refuse(str(error), "handshake_failure")
        share = msgs.extension(hello.extensions, msgs.EXT_KEY_SHARE)
        if share is None:
            self._refuse("the ServerHello carries no key_share (§4.2.8)", "missing_extension")
        group, key = msgs.decode_key_share_server(share)
        if group != msgs.GROUP_X25519 or len(key) != 32:
            self._refuse(
                f"the server's key share is {msgs.group_name(group)}: this card "
                "exchanges x25519 keys only",
                "illegal_parameter",
            )
        self._transcript.update(message)
        self._derive_handshake(key)
        self._state = _State.CLIENT_WAIT_FLIGHT

    def _client_hello_retry(self, hello: msgs.ServerHello, message: bytes) -> None:
        if self._hrr_done:
            self._refuse("a second HelloRetryRequest (RFC 8446 §4.1.4)", "illegal_parameter")
        share = msgs.extension(hello.extensions, msgs.EXT_KEY_SHARE)
        if share is None:
            self._refuse(
                "a HelloRetryRequest carries its selected group (§4.2.8)",
                "missing_extension",
            )
        group = msgs.decode_key_share_hrr(share)
        if group != msgs.GROUP_X25519:
            self._refuse(
                f"the server selected {msgs.group_name(group)}: this card exchanges "
                "x25519 keys only (seam request: more groups on cap.crypto)",
                "illegal_parameter",
            )
        selected = msgs.extension(hello.extensions, msgs.EXT_SUPPORTED_VERSIONS)
        if selected is None or msgs.decode_supported_versions_server(selected) != msgs.TLS_1_3:
            self._refuse(
                "TLS 1.2 and older are refused by name (docs/25 §17)",
                "protocol_version",
            )
        self._hrr_done = True
        self._transcript.fold_message_hash()  # §4.4.1: message_hash replaces CH1
        self._transcript.update(message)
        self._send_client_hello(first=False)

    def _client_flight(self, msg_type: int, body: bytes) -> None:
        expected = {
            0: msgs.ENCRYPTED_EXTENSIONS,
            1: msgs.CERTIFICATE,
            2: msgs.CERTIFICATE_VERIFY,
            3: msgs.FINISHED,
        }[self._flight_stage()]
        if msg_type != expected:
            self._refuse(
                f"a {msgs_msg_name(msg_type)} where the flight carries "
                f"{msgs_msg_name(expected)} (RFC 8446 §4.4)",
                "unexpected_message",
            )
        if msg_type == msgs.ENCRYPTED_EXTENSIONS:
            self._client_encrypted_extensions(body)
        elif msg_type == msgs.CERTIFICATE:
            self._client_certificate(body)
        elif msg_type == msgs.CERTIFICATE_VERIFY:
            self._client_certificate_verify(body)
        else:
            self._client_finished(body)

    def _flight_stage(self) -> int:
        return self._stage

    def _client_encrypted_extensions(self, body: bytes) -> None:
        extensions = msgs.decode_encrypted_extensions(body)
        if msgs.refused_extensions(extensions) is not None:
            self._refuse(
                f"{msgs.refused_extensions(extensions)} is refused by name",
                "handshake_failure",
            )
        selected = msgs.extension(extensions, msgs.EXT_ALPN)
        if selected is not None:
            self._alpn = msgs.decode_alpn_selected(selected)
            if self._config.alpn and self._alpn not in self._config.alpn:
                self._refuse(
                    f"the server selected ALPN {self._alpn!r}, which this client "
                    "never offered",
                    "illegal_parameter",
                )
        self._transcript.update(
            msgs.encode_handshake(msgs.ENCRYPTED_EXTENSIONS, body)
        )
        self._stage = 1

    def _client_certificate(self, body: bytes) -> None:
        context, entries = msgs.decode_certificate(body)
        if context:
            self._refuse(
                "a server Certificate carries an empty context (RFC 8446 §4.4.2)",
                "illegal_parameter",
            )
        if not entries:
            self._refuse("the server sent no certificate", "handshake_failure")
        store = certs.roots_of(self._config.roots)
        try:
            self._peer_public = certs.verify_certificate(
                entries[0].cert_data, store, subject=self._config.server_name
            )
        except (NotSupported, PermissionDenied) as error:
            self._refuse(str(error), "bad_certificate")
        self._transcript.update(msgs.encode_handshake(msgs.CERTIFICATE, body))
        self._stage = 2

    def _client_certificate_verify(self, body: bytes) -> None:
        self._verify_certificate_verify(body, "TLS 1.3, server CertificateVerify")
        self._transcript.update(msgs.encode_handshake(msgs.CERTIFICATE_VERIFY, body))
        self._stage = 3

    def _verify_certificate_verify(self, body: bytes, context: str) -> None:
        verify = msgs.decode_certificate_verify(body)
        if verify.algorithm != msgs.SCHEME_ED25519:
            self._refuse(
                f"signature scheme {msgs.scheme_name(verify.algorithm)} is refused "
                "by name: the tree signs with ed25519 (RFC 8032) and nothing else "
                "(seam request: RSA-PSS / ECDSA primitives)",
                "illegal_parameter",
            )
        content = (
            b"\x20" * 64
            + context.encode("ascii")
            + b"\x00"
            + self._transcript.hash()
        )
        if not ed25519.verify(self._peer_public, content, verify.signature):
            self._refuse(
                "the CertificateVerify signature is not the one these bytes carry",
                "decrypt_error",
            )

    def _client_finished(self, body: bytes) -> None:
        assert self._suite is not None
        key = keysched.finished_key(self._card, self._s_hs)
        expected = keysched.verify_data(self._card, key, self._transcript.hash())
        if body != expected:
            self._refuse("the server's Finished does not verify (§4.4.4)", "decrypt_error")
        self._transcript.update(msgs.encode_handshake(msgs.FINISHED, body))
        th = self._transcript.hash()
        self._derive_master()
        c_ap = keysched.derive_secret(self._card, self._master, "c ap traffic", th)
        s_ap = keysched.derive_secret(self._card, self._master, "s ap traffic", th)
        # The exporter and resumption secrets stop here on purpose (§7.5): a named
        # gap — this build derives no secrets it will not use, and resumption is
        # refused by name.
        mine = keysched.verify_data(
            self._card, keysched.finished_key(self._card, self._c_hs), th
        )
        self._transcript.update(msgs.encode_handshake(msgs.FINISHED, mine))
        self._out_plaintext(  # D.4: the dummy record before the second flight
            records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
        )
        self._out_protected(
            records.CONTENT_HANDSHAKE, msgs.encode_handshake(msgs.FINISHED, mine)
        )
        self._install_application(c_ap, s_ap)
        self._state = _State.ESTABLISHED

    # ---- the server side ----

    def _server_client_hello(self, body: bytes) -> None:
        hello = msgs.decode_client_hello(body)
        if self._state is _State.SERVER_WAIT_CH:
            self._session_id = hello.session_id  # what our ServerHello echoes (D.4)
        if hello.legacy_version != msgs.LEGACY_HELLO_VERSION:
            self._refuse(
                f"a ClientHello with legacy_version 0x{hello.legacy_version:04x} is "
                "refused by name: TLS 1.2 and older are refused (docs/25 §17)",
                "protocol_version",
            )
        refused = msgs.refused_extensions(hello.extensions)
        if refused is not None:
            self._refuse(
                f"{refused} is refused by name: this card runs the TLS 1.3 1-RTT "
                "handshake with X25519 and Ed25519, and no PSK, resumption, 0-RTT "
                "or cookie mode (docs/25 §17)",
                "handshake_failure",
            )
        versions = msgs.extension(hello.extensions, msgs.EXT_SUPPORTED_VERSIONS)
        if versions is None or msgs.TLS_1_3 not in msgs.decode_supported_versions_client(
            versions
        ):
            self._refuse(
                "TLS 1.2 and older are refused by name (docs/25 §17): this ClientHello "
                "offers no TLS 1.3",
                "protocol_version",
            )
        self._suite = self._select_suite(hello.cipher_suites)
        self._require_ed25519(hello.extensions)
        self._server_name_of(hello.extensions)
        alpn = self._select_alpn(hello.extensions)
        share = self._server_share(hello.extensions)
        message = msgs.encode_handshake(msgs.CLIENT_HELLO, body)
        if share is None:  # §4.2.8: an empty or foreign share asks for selection
            self._send_hello_retry(hello, message)
            return
        if self._state is _State.SERVER_WAIT_CH2:
            self._second_hello_checked(hello)
        self._transcript.update(message)
        self._server_hello(alpn, share)

    def _select_suite(self, offered: tuple[int, ...]) -> Suite:
        reasons: list[str] = []
        for code in offered:
            try:
                return keysched.suite_of(code)
            except NotSupported as error:
                reasons.append(str(error))
        self._refuse(
            "the peer offers no cipher suite this card can complete: " + " | ".join(reasons),
            "handshake_failure",
        )

    def _require_ed25519(self, extensions: msgs.Extensions) -> None:
        raw = msgs.extension(extensions, msgs.EXT_SIGNATURE_ALGORITHMS)
        schemes = msgs.decode_schemes(raw) if raw is not None else ()
        if msgs.SCHEME_ED25519 in schemes:
            return
        names = ", ".join(msgs.scheme_name(scheme) for scheme in schemes) or "none"
        self._refuse(
            f"the peer offers no signature scheme this card implements ({names}): "
            "the tree signs with ed25519 (RFC 8032) and every other scheme is "
            "refused by name (seam request: RSA-PSS / ECDSA primitives)",
            "handshake_failure",
        )

    def _server_name_of(self, extensions: msgs.Extensions) -> None:
        raw = msgs.extension(extensions, msgs.EXT_SERVER_NAME)
        self._seen_name = msgs.decode_server_name(raw) if raw is not None else None
        want = self._config.server_name
        if want is not None and self._seen_name is not None and self._seen_name != want:
            self._refuse(
                f"the client names {self._seen_name!r} and this server answers as "
                f"{want!r} (RFC 8446 §4.2.1)",
                "unrecognized_name",
            )

    def _select_alpn(self, extensions: msgs.Extensions) -> str | None:
        raw = msgs.extension(extensions, msgs.EXT_ALPN)
        if raw is None or not self._config.alpn:
            return None
        offered = msgs.decode_alpn(raw)
        for name in self._config.alpn:
            if name in offered:
                self._alpn = name
                return name
        self._refuse(
            f"no ALPN protocol overlaps: the client offers {list(offered)} and this "
            f"server names {list(self._config.alpn)} (RFC 7301)",
            "no_application_protocol",
        )

    def _server_share(self, extensions: msgs.Extensions) -> bytes | None:
        raw = msgs.extension(extensions, msgs.EXT_KEY_SHARE)
        if raw is None:
            self._refuse("the ClientHello carries no key_share (§4.2.8)", "missing_extension")
        shares = msgs.decode_key_share_client(raw)
        for group, key in shares:
            if group == msgs.GROUP_X25519:
                if len(key) != 32:
                    self._refuse("an x25519 key share is 32 bytes", "illegal_parameter")
                return key
        groups_raw = msgs.extension(extensions, msgs.EXT_SUPPORTED_GROUPS)
        groups = msgs.decode_groups(groups_raw) if groups_raw is not None else ()
        if msgs.GROUP_X25519 not in groups:
            names = ", ".join(msgs.group_name(group) for group in groups) or "none"
            self._refuse(
                f"the peer offers no key-exchange group this card implements ({names}): "
                "x25519 is the one group (seam request: more groups on cap.crypto)",
                "handshake_failure",
            )
        return None  # §4.2.8: no share for the selected group — HelloRetryRequest

    def _send_hello_retry(self, hello: msgs.ClientHello, message: bytes) -> None:
        if self._state is _State.SERVER_WAIT_CH2:
            self._refuse(
                "a second ClientHello without the selected group's key share "
                "(RFC 8446 §4.1.4)",
                "illegal_parameter",
            )
        self._ch1_random = hello.random
        self._transcript.fold_message_hash()  # §4.4.1: message_hash replaces CH1
        self._transcript.update(message)
        retry = msgs.ServerHello(
            legacy_version=msgs.LEGACY_HELLO_VERSION,
            random=msgs.HELLO_RETRY_REQUEST_RANDOM,
            session_id=hello.session_id,
            cipher_suite=self._suite.code if self._suite else keysched.OFFERED[0],
            extensions=(
                (
                    msgs.EXT_SUPPORTED_VERSIONS,
                    msgs.encode_supported_versions_server(msgs.TLS_1_3),
                ),
                (msgs.EXT_KEY_SHARE, msgs.encode_key_share_hrr(msgs.GROUP_X25519)),
            ),
        )
        body = msgs.encode_server_hello(retry)
        message = msgs.encode_handshake(msgs.SERVER_HELLO, body)
        self._transcript.update(message)
        self._out_plaintext(records.CONTENT_HANDSHAKE, message, records.LEGACY_VERSION)
        self._out_plaintext(  # D.4: the dummy record after the first handshake message
            records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
        )
        self._state = _State.SERVER_WAIT_CH2

    def _second_hello_checked(self, hello: msgs.ClientHello) -> None:
        if hello.random != self._ch1_random:
            self._refuse(
                "the second ClientHello changes its random (RFC 8446 §4.1.4)",
                "illegal_parameter",
            )
        if hello.session_id != self._session_id:
            self._refuse(
                "the second ClientHello changes its legacy_session_id (RFC 8446 §4.1.4)",
                "illegal_parameter",
            )

    def _server_hello(self, alpn: str | None, client_share: bytes) -> None:
        assert self._suite is not None  # the suite was selected from the hello
        self._share = self._entropy(32)
        public = self._card.x25519_public(self._share)
        hello = msgs.ServerHello(
            legacy_version=msgs.LEGACY_HELLO_VERSION,
            random=self._entropy(32),
            session_id=self._session_id,  # D.4: echo the client's, byte for byte
            cipher_suite=self._suite.code,
            extensions=(
                (
                    msgs.EXT_SUPPORTED_VERSIONS,
                    msgs.encode_supported_versions_server(msgs.TLS_1_3),
                ),
                (msgs.EXT_KEY_SHARE, msgs.encode_key_share_server(msgs.GROUP_X25519, public)),
            ),
        )
        body = msgs.encode_server_hello(hello)
        message = msgs.encode_handshake(msgs.SERVER_HELLO, body)
        self._transcript.update(message)
        self._out_plaintext(records.CONTENT_HANDSHAKE, message, records.LEGACY_VERSION)
        self._out_plaintext(  # D.4: the dummy record after the first handshake message
            records.CONTENT_CHANGE_CIPHER_SPEC, b"\x01", records.LEGACY_VERSION
        )
        self._derive_handshake(client_share)
        self._server_flight(alpn)

    def _server_flight(self, alpn: str | None) -> None:
        extensions: list[tuple[int, bytes]] = []
        if alpn is not None:
            extensions.append((msgs.EXT_ALPN, msgs.encode_alpn_selected(alpn)))
        ee = msgs.encode_handshake(
            msgs.ENCRYPTED_EXTENSIONS, msgs.encode_encrypted_extensions(tuple(extensions))
        )
        self._transcript.update(ee)
        identity = certs.certificate_of(self._config.certificates[0])
        entries = (msgs.CertificateEntry(identity.leaf, ()),) + tuple(
            msgs.CertificateEntry(chain, ()) for chain in identity.chain
        )
        certificate = msgs.encode_handshake(
            msgs.CERTIFICATE, msgs.encode_certificate(b"", entries)
        )
        self._transcript.update(certificate)
        content = (
            b"\x20" * 64
            + b"TLS 1.3, server CertificateVerify"
            + b"\x00"
            + self._transcript.hash()
        )
        signature = ed25519.sign(identity.key.seed, content)
        verify = msgs.encode_handshake(
            msgs.CERTIFICATE_VERIFY,
            msgs.encode_certificate_verify(
                msgs.CertificateVerify(msgs.SCHEME_ED25519, signature)
            ),
        )
        self._transcript.update(verify)
        assert self._suite is not None
        key = keysched.finished_key(self._card, self._s_hs)
        mine = keysched.verify_data(self._card, key, self._transcript.hash())
        finished = msgs.encode_handshake(msgs.FINISHED, msgs.encode_finished(mine))
        self._transcript.update(finished)
        self._out_protected(records.CONTENT_HANDSHAKE, ee + certificate + verify + finished)
        th = self._transcript.hash()
        self._derive_master()
        c_ap = keysched.derive_secret(self._card, self._master, "c ap traffic", th)
        s_ap = keysched.derive_secret(self._card, self._master, "s ap traffic", th)
        # The client's Finished is verified under the handshake keys; the write
        # side already moves to the application keys (RFC 8446 §5.1).
        self._send = Protection(self._card, self._suite.cipher, self._traffic(s_ap))
        self._c_ap = self._traffic(c_ap)
        self._state = _State.SERVER_WAIT_FINISHED

    def _server_finished(self, body: bytes) -> None:
        assert self._suite is not None
        key = keysched.finished_key(self._card, self._c_hs)
        expected = keysched.verify_data(self._card, key, self._transcript.hash())
        if body != expected:
            self._refuse("the client's Finished does not verify (§4.4.4)", "decrypt_error")
        self._transcript.update(msgs.encode_handshake(msgs.FINISHED, body))
        assert self._c_ap is not None
        self._recv = Protection(self._card, self._suite.cipher, self._c_ap)
        self._state = _State.ESTABLISHED

    # ---- the key schedule wiring (§7.1) ----

    def _derive_handshake(self, peer_share: bytes) -> None:
        assert self._suite is not None
        try:
            ecdhe = self._card.x25519(self._share, peer_share)
        except CryptoRefusal as error:  # a low-order point is the card's named refusal
            self._refuse(f"the key share is not usable: {error}", "illegal_parameter")
        empty = self._card.sha256(b"")
        early = self._card.hkdf_extract(
            self._suite.hash, keysched.HASH_LEN * b"\x00", keysched.HASH_LEN * b"\x00"
        )
        derived = keysched.derive_secret(self._card, early, "derived", empty)
        handshake = self._card.hkdf_extract(self._suite.hash, derived, ecdhe)
        th = self._transcript.hash()
        self._c_hs = keysched.derive_secret(self._card, handshake, "c hs traffic", th)
        self._s_hs = keysched.derive_secret(self._card, handshake, "s hs traffic", th)
        self._hs_root = handshake
        if self._config.role is Role.CLIENT:
            self._recv = Protection(self._card, self._suite.cipher, self._traffic(self._s_hs))
            self._send = Protection(self._card, self._suite.cipher, self._traffic(self._c_hs))
        else:
            self._send = Protection(self._card, self._suite.cipher, self._traffic(self._s_hs))
            self._recv = Protection(self._card, self._suite.cipher, self._traffic(self._c_hs))

    def _derive_master(self) -> None:
        assert self._suite is not None
        empty = self._card.sha256(b"")
        derived = keysched.derive_secret(self._card, self._hs_root, "derived", empty)
        self._master = self._card.hkdf_extract(
            self._suite.hash, derived, keysched.HASH_LEN * b"\x00"
        )

    def _traffic(self, secret: bytes) -> keysched.TrafficKeys:
        assert self._suite is not None
        return keysched.traffic_keys(self._card, secret, self._suite.key_len, self._suite.iv_len)

    def _install_application(self, c_ap: bytes, s_ap: bytes) -> None:
        assert self._suite is not None
        if self._config.role is Role.CLIENT:
            self._send = Protection(self._card, self._suite.cipher, self._traffic(c_ap))
            self._recv = Protection(self._card, self._suite.cipher, self._traffic(s_ap))
        else:
            self._send = Protection(self._card, self._suite.cipher, self._traffic(s_ap))
            self._recv = Protection(self._card, self._suite.cipher, self._traffic(c_ap))


def msgs_msg_name(msg_type: int) -> str:
    """A handshake type's honest name for a refusal message."""
    names = {
        msgs.CLIENT_HELLO: "ClientHello",
        msgs.SERVER_HELLO: "ServerHello",
        msgs.NEW_SESSION_TICKET: "NewSessionTicket",
        msgs.END_OF_EARLY_DATA: "EndOfEarlyData",
        msgs.ENCRYPTED_EXTENSIONS: "EncryptedExtensions",
        msgs.CERTIFICATE: "Certificate",
        msgs.CERTIFICATE_REQUEST: "CertificateRequest",
        msgs.CERTIFICATE_VERIFY: "CertificateVerify",
        msgs.FINISHED: "Finished",
        msgs.KEY_UPDATE: "KeyUpdate",
    }
    return names.get(msg_type, f"handshake message {msg_type}")
