"""The TLS 1.3 handshake messages (RFC 8446 §4): one codec, both roles.

ClientHello, ServerHello (and its HelloRetryRequest twin), EncryptedExtensions,
Certificate, CertificateVerify, Finished — the full 1-RTT set (§4.1–§4.4) — plus
the extension words the card negotiates: `server_name` (§4.2.1), `alpn` (§4.2.1),
`supported_groups` (§4.2.7), `signature_algorithms` (§4.2.3),
`supported_versions` (§4.2.1) and `key_share` (§4.2.8). Every parse is total:
bytes that do not form the shape are `MalformedMessage`, never a half-read word.

Extensions this card refuses by name (a mode it does not implement is never
silently ignored where the RFC would let it ride): `session_ticket` (35),
`pre_shared_key` (41), `early_data` (42), `cookie` (44) and
`psk_key_exchange_modes` (45) — PSK, resumption, 0-RTT and stateless-HRR cookies
are named refusals (docs/25 §17). Everything else unrecognised is ignored as
RFC 8446 §4.2 demands.

Signature schemes: the tree has exactly one, Ed25519 (0x0807, RFC 8032) in
`pymergetic.rxftk.certify.ed25519`, and this card speaks that one scheme. RSA-PSS,
ECDSA and friends are refused by name when they appear (seam request: the
schemes' primitives do not exist in this tree).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ...schema import MalformedMessage

# HandshakeType (RFC 8446 §4).
CLIENT_HELLO: Final = 1
SERVER_HELLO: Final = 2
NEW_SESSION_TICKET: Final = 4
END_OF_EARLY_DATA: Final = 5
ENCRYPTED_EXTENSIONS: Final = 8
CERTIFICATE: Final = 11
CERTIFICATE_REQUEST: Final = 13
CERTIFICATE_VERIFY: Final = 15
FINISHED: Final = 14
KEY_UPDATE: Final = 24
MESSAGE_HASH: Final = 254  # the synthetic transcript message of §4.4.1

# ExtensionType (IANA registry) — the words this card knows by name.
EXT_SERVER_NAME: Final = 0
EXT_ALPN: Final = 16
EXT_SUPPORTED_GROUPS: Final = 10
EXT_SIGNATURE_ALGORITHMS: Final = 13
EXT_SESSION_TICKET: Final = 35
EXT_PRE_SHARED_KEY: Final = 41
EXT_EARLY_DATA: Final = 42
EXT_SUPPORTED_VERSIONS: Final = 43
EXT_COOKIE: Final = 44
EXT_PSK_KEY_EXCHANGE_MODES: Final = 45
EXT_KEY_SHARE: Final = 51

# The extensions whose mode this card refuses by name (docs/25 §17).
REFUSED_EXTENSIONS: Final = {
    EXT_SESSION_TICKET: "session_ticket (session resumption)",
    EXT_PRE_SHARED_KEY: "pre_shared_key (PSK / session resumption)",
    EXT_EARLY_DATA: "early_data (0-RTT)",
    EXT_COOKIE: "cookie (stateless HelloRetryRequest)",
    EXT_PSK_KEY_EXCHANGE_MODES: "psk_key_exchange_modes (PSK / session resumption)",
}

# NamedGroup (RFC 8446 §4.2.7): the one key-exchange group this card implements.
GROUP_X25519: Final = 0x001D
GROUP_NAMES: Final = {GROUP_X25519: "x25519"}

# SignatureScheme (RFC 8446 §4.2.3): the one scheme this card implements.
SCHEME_ED25519: Final = 0x0807
SCHEME_NAMES: Final = {
    SCHEME_ED25519: "ed25519",
    0x0201: "rsa_pkcs1_sha1",
    0x0203: "ecdsa_sha1",
    0x0401: "rsa_pkcs1_sha256",
    0x0403: "ecdsa_secp256r1_sha256",
    0x0501: "rsa_pkcs1_sha384",
    0x0503: "ecdsa_secp384r1_sha384",
    0x0601: "rsa_pkcs1_sha512",
    0x0603: "ecdsa_secp521r1_sha512",
    0x0804: "rsa_pss_rsae_sha256",
    0x0805: "rsa_pss_rsae_sha384",
    0x0806: "rsa_pss_rsae_sha512",
    0x0809: "ed448",
}

# TLS 1.3 says legacy_version is 0x0303 in every hello (§4.1.2, §4.1.3).
LEGACY_HELLO_VERSION: Final = 0x0303
TLS_1_3: Final = 0x0304

# The magic HelloRetryRequest random (§4.1.3): SHA-256("HelloRetryRequest").
HELLO_RETRY_REQUEST_RANDOM: Final = bytes.fromhex(
    "cf21ad74e59a6111be1d8c021e65b891c2a211167abb8c5e079e09e2c8a8339c"
)


def group_name(group: int) -> str:
    """A named group's honest name; unknown ones name their code point."""
    return GROUP_NAMES.get(group, f"group_0x{group:04x}")


def scheme_name(scheme: int) -> str:
    """A signature scheme's honest name; unknown ones name their code point."""
    return SCHEME_NAMES.get(scheme, f"signature_scheme_0x{scheme:04x}")


# ---- byte readers and writers (total parses; nothing half-read) ----


class _Reader:
    __slots__ = ("at", "data")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.at = 0

    def _take(self, count: int, what: str) -> bytes:
        if count < 0 or self.at + count > len(self.data):
            raise MalformedMessage(f"truncated {what} in a handshake message")
        chunk = self.data[self.at : self.at + count]
        self.at += count
        return chunk

    def u8(self, what: str) -> int:
        return self._take(1, what)[0]

    def u16(self, what: str) -> int:
        return int.from_bytes(self._take(2, what), "big")

    def u24(self, what: str) -> int:
        return int.from_bytes(self._take(3, what), "big")

    def fixed(self, count: int, what: str) -> bytes:
        return self._take(count, what)

    def vector(self, width: int, what: str) -> bytes:
        length = int.from_bytes(self._take(width, what + " length"), "big")
        return self._take(length, what)

    def done(self, what: str) -> None:
        if self.at != len(self.data):
            raise MalformedMessage(f"trailing bytes after {what} in a handshake message")


def _vector(payload: bytes, width: int) -> bytes:
    return len(payload).to_bytes(width, "big") + payload


# ---- extensions ----

Extensions = tuple[tuple[int, bytes], ...]


def encode_extension(kind: int, data: bytes) -> bytes:
    return kind.to_bytes(2, "big") + _vector(data, 2)


def encode_extensions(extensions: Extensions) -> bytes:
    body = b"".join(encode_extension(kind, data) for kind, data in extensions)
    return _vector(body, 2)


def decode_extensions(body: bytes) -> Extensions:
    """The extension list of one extensions field body (its length already read).

    The inverse of `encode_extensions` minus the outer measure: callers read the
    `extensions<0..2^16-1>` length once and hand in what it measures.
    """
    inner = _Reader(body)
    out: list[tuple[int, bytes]] = []
    while inner.at < len(inner.data):
        kind = inner.u16("extension type")
        out.append((kind, inner.vector(2, "extension data")))
    return tuple(out)


def extension(extensions: Extensions, kind: int) -> bytes | None:
    """The one extension body of this kind (§4.2: at most one of each), or none."""
    found: bytes | None = None
    for have, data in extensions:
        if have == kind:
            if found is not None:
                raise MalformedMessage(f"extension {kind} appears twice in one message")
            found = data
    return found


def refused_extensions(extensions: Extensions) -> str | None:
    """The name of the first extension whose mode this card refuses, if any."""
    for kind, _ in extensions:
        name = REFUSED_EXTENSIONS.get(kind)
        if name is not None:
            return name
    return None


# ---- the key_share extension (§4.2.8) ----


def encode_key_share_client(shares: tuple[tuple[int, bytes], ...]) -> bytes:
    entries = b"".join(
        group.to_bytes(2, "big") + _vector(key, 2) for group, key in shares
    )
    return _vector(entries, 2)


def decode_key_share_client(data: bytes) -> tuple[tuple[int, bytes], ...]:
    reader = _Reader(data)
    body = reader.vector(2, "client_shares")
    reader.done("client_shares")
    inner = _Reader(body)
    shares: list[tuple[int, bytes]] = []
    while inner.at < len(inner.data):
        group = inner.u16("key share group")
        shares.append((group, inner.vector(2, "key exchange")))
    return tuple(shares)


def encode_key_share_server(group: int, key: bytes) -> bytes:
    return group.to_bytes(2, "big") + _vector(key, 2)


def decode_key_share_server(data: bytes) -> tuple[int, bytes]:
    reader = _Reader(data)
    group = reader.u16("key share group")
    key = reader.vector(2, "key exchange")
    reader.done("key share")
    return group, key


def encode_key_share_hrr(group: int) -> bytes:
    """A HelloRetryRequest's key_share body is the selected group and nothing else."""
    return group.to_bytes(2, "big")


def decode_key_share_hrr(data: bytes) -> int:
    reader = _Reader(data)
    group = reader.u16("selected group")
    reader.done("selected group")
    return group


# ---- supported_versions (§4.2.1) ----


def encode_supported_versions_client(versions: tuple[int, ...]) -> bytes:
    body = b"".join(version.to_bytes(2, "big") for version in versions)
    return _vector(body, 2)


def decode_supported_versions_client(data: bytes) -> tuple[int, ...]:
    reader = _Reader(data)
    body = reader.vector(2, "supported_versions")
    reader.done("supported_versions")
    if not body or len(body) % 2:
        raise MalformedMessage("supported_versions is a non-empty list of two-byte words")
    return tuple(int.from_bytes(body[i : i + 2], "big") for i in range(0, len(body), 2))


def encode_supported_versions_server(version: int) -> bytes:
    return version.to_bytes(2, "big")


def decode_supported_versions_server(data: bytes) -> int:
    reader = _Reader(data)
    version = reader.u16("selected version")
    reader.done("selected version")
    return version


# ---- server_name (§4.2.1) and alpn (RFC 7301) ----


def encode_server_name(host: str) -> bytes:
    name = host.encode("ascii")
    entry = bytes([0]) + _vector(name, 2)  # NameType host_name(0)
    return _vector(entry, 2)


def decode_server_name(data: bytes) -> str | None:
    reader = _Reader(data)
    body = reader.vector(2, "server_name list")
    reader.done("server_name")
    inner = _Reader(body)
    host: str | None = None
    while inner.at < len(inner.data):
        kind = inner.u8("name type")
        name = inner.vector(2, "host name")
        if kind != 0:
            continue  # an unknown name type is not ours to read (§4.2.1)
        if host is not None:
            raise MalformedMessage("server_name carries two host names")
        try:
            host = name.decode("ascii")
        except UnicodeDecodeError as error:
            raise MalformedMessage("a host name is not ASCII") from error
    return host


def encode_alpn(protocols: tuple[str, ...]) -> bytes:
    entries = b"".join(_vector(name.encode("ascii"), 1) for name in protocols)
    return _vector(entries, 2)


def decode_alpn(data: bytes) -> tuple[str, ...]:
    reader = _Reader(data)
    body = reader.vector(2, "protocol_name_list")
    reader.done("alpn")
    inner = _Reader(body)
    names: list[str] = []
    while inner.at < len(inner.data):
        raw = inner.vector(1, "protocol name")
        if not raw:
            raise MalformedMessage("an ALPN protocol name is never empty (RFC 7301)")
        try:
            names.append(raw.decode("ascii"))
        except UnicodeDecodeError as error:
            raise MalformedMessage("an ALPN protocol name is not ASCII") from error
    return tuple(names)


def encode_alpn_selected(protocol: str) -> bytes:
    return encode_alpn((protocol,))


def decode_alpn_selected(data: bytes) -> str:
    names = decode_alpn(data)
    if len(names) != 1:
        raise MalformedMessage("the server's alpn selects exactly one protocol")
    return names[0]


# ---- supported_groups (§4.2.7) and signature_algorithms (§4.2.3) ----


def encode_groups(groups: tuple[int, ...]) -> bytes:
    body = b"".join(group.to_bytes(2, "big") for group in groups)
    return _vector(body, 2)


def decode_groups(data: bytes) -> tuple[int, ...]:
    reader = _Reader(data)
    body = reader.vector(2, "supported_groups")
    reader.done("supported_groups")
    if not body or len(body) % 2:
        raise MalformedMessage("supported_groups is a non-empty list of two-byte words")
    return tuple(int.from_bytes(body[i : i + 2], "big") for i in range(0, len(body), 2))


def encode_schemes(schemes: tuple[int, ...]) -> bytes:
    body = b"".join(scheme.to_bytes(2, "big") for scheme in schemes)
    return _vector(body, 2)


def decode_schemes(data: bytes) -> tuple[int, ...]:
    reader = _Reader(data)
    body = reader.vector(2, "signature_algorithms")
    reader.done("signature_algorithms")
    if not body or len(body) % 2:
        raise MalformedMessage(
            "signature_algorithms is a non-empty list of two-byte words"
        )
    return tuple(int.from_bytes(body[i : i + 2], "big") for i in range(0, len(body), 2))


# ---- the hello messages (§4.1) ----


@dataclass(frozen=True)
class ClientHello:
    """The client's first word: versions, random, session id, suites, extensions.

    `legacy_version` is parsed and carried, never judged here: the handshake is
    what refuses TLS 1.2 and older **by name** (docs/25 §17), so the parse stays
    total and the refusal stays typed.
    """

    legacy_version: int
    random: bytes
    session_id: bytes
    cipher_suites: tuple[int, ...]
    extensions: Extensions


@dataclass(frozen=True)
class ServerHello:
    """The server's answer — or, with the magic random, a HelloRetryRequest."""

    legacy_version: int
    random: bytes
    session_id: bytes
    cipher_suite: int
    extensions: Extensions

    def retry_request(self) -> bool:
        """Whether this is the HelloRetryRequest twin of §4.1.3 (the random is the tell)."""
        return self.random == HELLO_RETRY_REQUEST_RANDOM


def encode_client_hello(hello: ClientHello) -> bytes:
    suites = b"".join(code.to_bytes(2, "big") for code in hello.cipher_suites)
    body = (
        hello.legacy_version.to_bytes(2, "big")
        + hello.random
        + _vector(hello.session_id, 1)
        + _vector(suites, 2)
        + _vector(b"\x00", 1)  # legacy_compression_methods: null and nothing else
        + encode_extensions(hello.extensions)
    )
    return body


def decode_client_hello(body: bytes) -> ClientHello:
    reader = _Reader(body)
    legacy = reader.u16("legacy_version")
    random = reader.fixed(32, "random")
    session_id = reader.vector(1, "legacy_session_id")
    if len(session_id) > 32:
        raise MalformedMessage("legacy_session_id is at most 32 bytes")
    suites_raw = reader.vector(2, "cipher_suites")
    if not suites_raw or len(suites_raw) % 2:
        raise MalformedMessage("cipher_suites is a non-empty list of two-byte words")
    suites = tuple(
        int.from_bytes(suites_raw[i : i + 2], "big") for i in range(0, len(suites_raw), 2)
    )
    compression = reader.vector(1, "legacy_compression_methods")
    if compression != b"\x00":
        raise MalformedMessage("TLS 1.3 admits only the null compression method (§4.1.2)")
    extensions = decode_extensions(reader.vector(2, "extensions"))
    reader.done("ClientHello")
    return ClientHello(legacy, random, session_id, suites, extensions)


def encode_server_hello(hello: ServerHello) -> bytes:
    body = (
        hello.legacy_version.to_bytes(2, "big")
        + hello.random
        + _vector(hello.session_id, 1)
        + hello.cipher_suite.to_bytes(2, "big")
        + b"\x00"  # legacy_compression_method: null
        + encode_extensions(hello.extensions)
    )
    return body


def decode_server_hello(body: bytes) -> ServerHello:
    reader = _Reader(body)
    legacy = reader.u16("legacy_version")
    random = reader.fixed(32, "random")
    session_id = reader.vector(1, "legacy_session_id_echo")
    if len(session_id) > 32:
        raise MalformedMessage("legacy_session_id_echo is at most 32 bytes")
    suite = reader.u16("cipher_suite")
    if reader.u8("legacy_compression_method") != 0:
        raise MalformedMessage("TLS 1.3 admits only the null compression method (§4.1.3)")
    extensions = decode_extensions(reader.vector(2, "extensions"))
    reader.done("ServerHello")
    return ServerHello(legacy, random, session_id, suite, extensions)


# ---- the rest of the 1-RTT flight (§4.2–§4.4) ----


def encode_encrypted_extensions(extensions: Extensions) -> bytes:
    return encode_extensions(extensions)


def decode_encrypted_extensions(body: bytes) -> Extensions:
    reader = _Reader(body)
    listed = reader.vector(2, "extensions")
    reader.done("EncryptedExtensions")
    return decode_extensions(listed)


@dataclass(frozen=True)
class CertificateEntry:
    """One §4.4.2 entry: opaque certificate bytes plus its entry extensions."""

    cert_data: bytes
    extensions: Extensions


def encode_certificate(context: bytes, entries: tuple[CertificateEntry, ...]) -> bytes:
    listed = b"".join(
        _vector(entry.cert_data, 3) + encode_extensions(entry.extensions)
        for entry in entries
    )
    return _vector(context, 1) + _vector(listed, 3)


def decode_certificate(body: bytes) -> tuple[bytes, tuple[CertificateEntry, ...]]:
    reader = _Reader(body)
    context = reader.vector(1, "certificate_request_context")
    listed = reader.vector(3, "certificate_list")
    reader.done("Certificate")
    inner = _Reader(listed)
    entries: list[CertificateEntry] = []
    while inner.at < len(inner.data):
        cert_data = inner.vector(3, "cert_data")
        if not cert_data:
            raise MalformedMessage("a cert_data entry is never empty (§4.4.2)")
        entries.append(CertificateEntry(cert_data, decode_extensions(inner.vector(2, "entry extensions"))))
    return context, tuple(entries)


@dataclass(frozen=True)
class CertificateVerify:
    """The §4.4.3 proof of the certificate's key: scheme plus signature."""

    algorithm: int
    signature: bytes


def encode_certificate_verify(verify: CertificateVerify) -> bytes:
    return verify.algorithm.to_bytes(2, "big") + _vector(verify.signature, 2)


def decode_certificate_verify(body: bytes) -> CertificateVerify:
    reader = _Reader(body)
    algorithm = reader.u16("signature scheme")
    signature = reader.vector(2, "signature")
    reader.done("CertificateVerify")
    return CertificateVerify(algorithm, signature)


def encode_finished(verify_data: bytes) -> bytes:
    return verify_data


def decode_finished(body: bytes) -> bytes:
    return body


# ---- handshake framing (§4): type(1) || uint24 length || body ----


def encode_handshake(msg_type: int, body: bytes) -> bytes:
    return (
        bytes([msg_type])
        + len(body).to_bytes(3, "big")
        + body
    )


def message_hash_message(ch_hash: bytes) -> bytes:
    """The synthetic message of §4.4.1: the HRR transcript's stand-in for CH1."""
    return encode_handshake(MESSAGE_HASH, ch_hash)


class HandshakeBuffer:
    """The reassembly of handshake messages across record boundaries (§4)."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> None:
        self._buffer.extend(data)

    def take(self) -> tuple[int, bytes] | None:
        """The next whole (type, body), or None when one has not fully arrived."""
        if len(self._buffer) < 4:
            return None
        msg_type = self._buffer[0]
        length = int.from_bytes(self._buffer[1:4], "big")
        if len(self._buffer) < 4 + length:
            return None
        body = bytes(self._buffer[4 : 4 + length])
        del self._buffer[: 4 + length]
        return msg_type, body

    @property
    def pending(self) -> int:
        return len(self._buffer)
