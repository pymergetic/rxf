"""Certificates and key material for the TLS card: cells in, refusals out.

`TlsConfig` names cells, never material: `certificates` are `Certificate` cell
paths (docs/25 §3: "chain, leaf, key: cells under net/secret/"), `roots` is the
trust-store cell path. This module holds the material behind those cells in a
vault keyed by cell path (the honest stand-in until the image cell store lands —
see the seam request in the report) and hands it to the handshake in exactly two
shapes: an `Identity` (the leaf, its chain, and the Ed25519 key as key material)
and a `certify.TrustStore` (the admitted voices of the roots cell).

Key material is a `SecretValue`: export is refused by default (§17's
"private-key export from Secret cells by default" being an honest refusal) and
`repr`/`str` show the cell's path and never a byte of key material. This is the
same contract `net/link/wireguard.py`'s `SecretValue` states; the tree's one
holder should move to a `net/secret` module both cards import (seam request) —
it lives here because the transport layer cannot import the WireGuard card (that
card already imports `net.transport`).

The certificate form is this card's self-signed identity statement
("rxf-tls-cert-v1"): subject, Ed25519 public key, key id, stamp, signature over
those words. The signature is `pymergetic.rxf.certify.ed25519` — the tree's one
Ed25519 (RFC 8032, vectors pinned in `tests/test_certify_ed25519.py`) — and the
roots cell is a `certify.TrustStore`, so a pinned voice is admitted exactly the
docs/11 way (admitted, not revoked, inside the policy's time words). X.509 and
chain building are refused by name: a named gap, never a guessed parse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ....certify import ed25519
from ....certify.schema import (
    SEED_BYTES,
    KeyId,
    Policy,
    TrustKey,
    TrustStore,
)
from ...schema import NotSupported, PermissionDenied, Secret

CERT_MAGIC: Final = "rxf-tls-cert-v1"
PUBLIC_BYTES: Final = 32
SIGNATURE_BYTES: Final = 64


class SecretValue:
    """Key material behind one `Secret` cell (docs/25 §11); export refused (§17).

    The bytes exist for the signature step and for nothing else: `repr` and
    `str` are the cell's path — key material never appears in a rendering — and
    `export()` refuses `PermissionDenied` by name. The handshake reads the
    material internally to hand it to `certify.ed25519`; nothing else can.
    """

    __slots__ = ("_material", "_path")

    def __init__(self, path: str, material: bytes) -> None:
        if not isinstance(material, bytes) or len(material) != SEED_BYTES:
            raise NotSupported(
                f"key material is {SEED_BYTES} bytes (an Ed25519 seed): this value is not one"
            )
        self._path = path
        self._material = material

    @property
    def path(self) -> str:
        """The `Secret` cell's path — the only word of this value that travels."""
        return self._path

    @property
    def cell(self) -> Secret:
        """The `Secret` cell this material sits behind (schema: a cell path)."""
        return Secret(self._path)

    @property
    def seed(self) -> bytes:
        """The seed for the signing step — the card's internal read, nothing else's."""
        return self._material

    def export(self) -> bytes:
        """Export is refused by name (§17) — key material leaves this cell through
        `certify.ed25519`'s signing and nowhere else."""
        raise PermissionDenied(
            "export of a Secret cell's key material is refused by name (§17)"
        )

    def __repr__(self) -> str:
        return f"SecretValue(path={self._path!r})"

    def __str__(self) -> str:
        return self.__repr__()


# ---- the identity statement ("rxf-tls-cert-v1") ----


def _is_word(word: str) -> bool:
    return bool(word) and all(0x21 <= ord(c) <= 0x7E for c in word)


def canonical(subject: str, public: bytes, key_id: KeyId, issued: int) -> bytes:
    """The exact bytes an identity signs: the statement minus its signature line."""
    if not _is_word(subject):
        raise NotSupported("a certificate subject is printable ASCII without spaces")
    if not _is_word(key_id):
        raise NotSupported("a certificate key id is printable ASCII without spaces")
    if len(public) != PUBLIC_BYTES:
        raise NotSupported(f"a public key is {PUBLIC_BYTES} bytes")
    if not 0 <= issued < 2**64:
        raise NotSupported("issued is a 64-bit word")
    return (
        f"{CERT_MAGIC}\n"
        f"subject {subject}\n"
        f"key {public.hex()}\n"
        f"key_id {key_id}\n"
        f"issued {issued}\n"
    ).encode("ascii")


def sign_certificate(subject: str, key_id: KeyId, key: SecretValue, issued: int) -> bytes:
    """The self-signed identity statement of one voice (RFC 8032, via certify)."""
    public = ed25519.public_key(key.seed)
    signature = ed25519.sign(key.seed, canonical(subject, public, key_id, issued))
    return canonical(subject, public, key_id, issued) + f"signature {signature.hex()}\n".encode(
        "ascii"
    )


@dataclass(frozen=True)
class Identity:
    """The material behind one `Certificate` cell: chain, leaf, key (§3/§11).

    The leaf is the identity statement; the key is key material (a
    `SecretValue`), so this value renders its cell path and never key bytes.
    """

    path: str
    leaf: bytes
    key: SecretValue
    chain: tuple[bytes, ...] = ()

    @property
    def cell(self) -> str:
        return self.path

    def __repr__(self) -> str:
        return f"Identity(path={self.path!r}, chain={len(self.chain)})"

    def __str__(self) -> str:
        return self.__repr__()


def parse_certificate(payload: bytes) -> tuple[str, bytes, KeyId, int, bytes]:
    """(subject, public, key_id, issued, signature) of one identity statement."""
    try:
        text = payload.decode("ascii")
    except UnicodeDecodeError as error:
        raise NotSupported(f"not a {CERT_MAGIC} certificate: it is not ASCII") from error
    lines = text.splitlines()
    if not lines or lines[0] != CERT_MAGIC:
        raise NotSupported(f"not a {CERT_MAGIC} certificate (X.509 is refused by name)")
    fields: dict[str, str] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(" ")
        if not sep or name in fields:
            raise NotSupported(f"a {CERT_MAGIC} certificate carries one {line!r} line")
        fields[name] = value
    if set(fields) != {"subject", "key", "key_id", "issued", "signature"}:
        raise NotSupported(f"a {CERT_MAGIC} certificate carries exactly its five lines")
    try:
        public = bytes.fromhex(fields["key"])
        signature = bytes.fromhex(fields["signature"])
        issued = int(fields["issued"])
    except ValueError as error:
        raise NotSupported(f"a {CERT_MAGIC} certificate carries hex keys and a word stamp") from error
    subject, key_id = fields["subject"], fields["key_id"]
    if len(public) != PUBLIC_BYTES or len(signature) != SIGNATURE_BYTES:
        raise NotSupported(
            f"a {CERT_MAGIC} certificate key is {PUBLIC_BYTES} bytes and its signature {SIGNATURE_BYTES}"
        )
    return subject, public, key_id, issued, signature


def verify_certificate(
    payload: bytes, store: TrustStore, *, subject: str | None = None
) -> bytes:
    """The public key of an admitted identity — or a refusal that names why not.

    The pins are the docs/11 words: the voice is admitted in the roots cell, not
    revoked, and stamped inside the policy's window; then the signature over the
    canonical statement verifies under the pinned key. `subject`, when the
    config names one (SNI), must be the certificate's.
    """
    got, public, key_id, issued, signature = parse_certificate(payload)
    voice = store.voice(key_id)
    if voice is None:
        raise NotSupported(
            f"the certificate names voice {key_id!r}, and the roots cell admits no such voice"
        )
    if key_id in store.revoked:
        raise NotSupported(f"the certificate names voice {key_id!r}, and it is revoked")
    if voice.public != public:
        raise NotSupported(
            f"the certificate's key is not the one the roots cell pins for {key_id!r}"
        )
    policy: Policy = store.policy
    if issued > policy.now + policy.skew:
        raise NotSupported(
            f"the certificate is stamped {issued}: after the roots policy's now {policy.now}"
        )
    if policy.now > issued + policy.max_age:
        raise NotSupported(
            f"the certificate is stamped {issued}: older than max_age {policy.max_age}"
        )
    if not ed25519.verify(public, canonical(got, public, key_id, issued), signature):
        raise NotSupported("the certificate's signature is not the one its words carry")
    if subject is not None and got != subject:
        raise NotSupported(
            f"the certificate speaks for {got!r}, not the name this client asked for {subject!r}"
        )
    return public


# ---- the cell vault: material behind the paths TlsConfig names ----

_certificates: dict[str, Identity] = {}
_roots: dict[str, TrustStore] = {}


def put_certificate(path: str, identity: Identity) -> None:
    """Put one Certificate cell's material under its path (the cell store's seam)."""
    if path in _certificates:
        raise NotSupported(f"two materials name one Certificate cell: {path} (§17)")
    _certificates[path] = identity


def put_roots(path: str, store: TrustStore) -> None:
    """Put one trust-store cell's material under its path (the cell store's seam)."""
    if path in _roots:
        raise NotSupported(f"two materials name one trust-store cell: {path} (§17)")
    _roots[path] = store


def certificate_of(path: str) -> Identity:
    """The material behind a Certificate cell path; a refusal when none was given."""
    try:
        return _certificates[path]
    except KeyError:
        raise NotSupported(
            f"the config names the Certificate cell {path}, and no material for it was given"
        ) from None


def roots_of(path: str) -> TrustStore:
    """The material behind a trust-store cell path; a refusal when none was given."""
    try:
        return _roots[path]
    except KeyError:
        raise NotSupported(
            f"the config names the trust-store cell {path}, and no material for it was given"
        ) from None


def clear() -> None:
    """Take the vault's material away again (the tests' cell store reset)."""
    _certificates.clear()
    _roots.clear()


def pinned(key_id: KeyId, public: bytes, *, now: int, max_age: int) -> TrustStore:
    """One honest trust-store cell for the tests: a single admitted voice."""
    return TrustStore(Policy(now=now, max_age=max_age), (TrustKey(key_id, public),))
