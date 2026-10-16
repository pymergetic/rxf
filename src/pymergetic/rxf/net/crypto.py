"""Host binding of the cap.crypto card: ctypes over the one Rust C ABI.

The card is ``impl = rs`` (``native/crypto``); this file is only the host fill,
like the other capability fills — **no crypto lives here**. Python reaches the
card through the frozen C ABI of ``native/crypto/include/pm_crypto.h`` (docs/25
§11: one card, one defining lang, one set of test vectors), and a negative
return becomes a named Python exception rather than a bare error code.

    from pymergetic.rxf.net import crypto

    card = crypto.load()                    # builds via cargo if the library is missing
    tag_wrapped = card.aead_seal(
        crypto.CIPHER_CHACHA20_POLY1305, key, nonce, aad, plaintext
    )
    card.close()                            # pm_crypto_deinit

Lifecycle: the module pair is ``init``/``deinit`` (never ``fini``); ``Crypto``
calls ``pm_crypto_init()`` on construction and ``pm_crypto_deinit()`` on
``close()``. There is no global state in the card itself.

Timing: the Rust primitives aim at constant time where the language allows
(mask-swapped X25519 ladder, fixed-iteration GHASH, XOR tag compares); AES
uses a table S-box and is not claimed constant-time against cache timing. See
``native/crypto/src/*.rs`` for exactly what is done.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Final, NoReturn, Self

__all__ = [
    "CIPHER_AES_256_GCM",
    "CIPHER_CHACHA20_POLY1305",
    "HASH_BLAKE2S",
    "HASH_SHA256",
    "Crypto",
    "CryptoArgument",
    "CryptoBuildError",
    "CryptoLength",
    "CryptoLowOrderPoint",
    "CryptoRefusal",
    "CryptoTagMismatch",
    "build",
    "load",
]

# The ids of the frozen ABI (pm_crypto.h).
HASH_SHA256: Final = 0
HASH_BLAKE2S: Final = 1
CIPHER_CHACHA20_POLY1305: Final = 0
CIPHER_AES_256_GCM: Final = 1

# The refusal codes of the frozen ABI (pm_crypto.h).
PM_ERR_ARGUMENT: Final = -1
PM_ERR_TAG: Final = -2
PM_ERR_LOW_ORDER: Final = -3
PM_ERR_LENGTH: Final = -4

KEY_LEN: Final = 32
NONCE_LEN: Final = 12
HASH_LEN: Final = 32
TAG_LEN: Final = 16

# Marshalling guard only: the card refuses HKDF-Expand over 255 * 32 bytes
# (RFC 5869) with PM_ERR_LENGTH; anything this large never gets allocated here.
_EXPAND_ALLOC_LIMIT: Final = 1024 * 1024


class CryptoRefusal(ValueError):
    """The card refused the operation (a negative return from the C ABI)."""


class CryptoArgument(CryptoRefusal):
    """PM_ERR_ARGUMENT (-1): bad id, bad fixed length, or a too-short buffer."""


class CryptoTagMismatch(CryptoRefusal):
    """PM_ERR_TAG (-2): the AEAD tag does not verify; nothing was written."""


class CryptoLowOrderPoint(CryptoRefusal):
    """PM_ERR_LOW_ORDER (-3): X25519 output all zeros (small-order point)."""


class CryptoLength(CryptoRefusal):
    """PM_ERR_LENGTH (-4): HKDF-Expand over 255 * 32 bytes (RFC 5869)."""


class CryptoBuildError(RuntimeError):
    """cargo is missing, the build failed, or the card library is absent."""


_REFUSALS: Final = {
    PM_ERR_ARGUMENT: CryptoArgument,
    PM_ERR_TAG: CryptoTagMismatch,
    PM_ERR_LOW_ORDER: CryptoLowOrderPoint,
    PM_ERR_LENGTH: CryptoLength,
}


def _refuse(rc: int, what: str) -> NoReturn:
    kind = _REFUSALS.get(rc, CryptoRefusal)
    raise kind(f"{what}: the card refused (pm_crypto rc = {rc})")


def _check(rc: int, what: str) -> None:
    if rc != 0:
        _refuse(rc, what)


def _fixed(what: str, value: bytes, size: int) -> bytes:
    if len(value) != size:
        raise CryptoArgument(f"{what} must be exactly {size} bytes, got {len(value)}")
    return value


def _buf(data: bytes | None) -> ctypes.Array[ctypes.c_uint8] | None:
    """A copy of ``data`` for the call; NULL for empty input (as the ABI allows)."""
    if not data:
        return None
    return (ctypes.c_uint8 * len(data)).from_buffer_copy(data)


def _out(n: int) -> ctypes.Array[ctypes.c_uint8]:
    return (ctypes.c_uint8 * n)()


def _bytes(array: ctypes.Array[ctypes.c_uint8], n: int) -> bytes:
    return bytes(array[:n])


class Crypto:
    """One loaded card library over the frozen C ABI.

    Construction runs ``pm_crypto_init``; ``close()`` runs ``pm_crypto_deinit``
    (the pair that opens with ``init`` closes with ``deinit``). Usable as a
    context manager. The methods mirror the ABI names without the
    ``pm_crypto_`` prefix; the Rust card is the only implementation.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lib = ctypes.CDLL(str(self.path))
        self._bind()
        _check(self._lib.pm_crypto_init(), "pm_crypto_init")
        self._live = True

    def _bind(self) -> None:
        u8p = ctypes.POINTER(ctypes.c_uint8)
        sizep = ctypes.POINTER(ctypes.c_size_t)
        lib = self._lib
        lib.pm_crypto_init.argtypes = []
        lib.pm_crypto_init.restype = ctypes.c_int32
        lib.pm_crypto_deinit.argtypes = []
        lib.pm_crypto_deinit.restype = None
        lib.pm_crypto_sha256.argtypes = [u8p, u8p, ctypes.c_size_t]
        lib.pm_crypto_sha256.restype = None
        lib.pm_crypto_blake2s.argtypes = [u8p, u8p, ctypes.c_size_t]
        lib.pm_crypto_blake2s.restype = None
        lib.pm_crypto_blake2s_keyed.argtypes = [
            u8p,
            u8p,
            ctypes.c_size_t,
            u8p,
            ctypes.c_size_t,
        ]
        lib.pm_crypto_blake2s_keyed.restype = None
        lib.pm_crypto_x25519_public.argtypes = [u8p, u8p]
        lib.pm_crypto_x25519_public.restype = None
        lib.pm_crypto_x25519.argtypes = [u8p, u8p, u8p]
        lib.pm_crypto_x25519.restype = ctypes.c_int32
        lib.pm_crypto_aead_seal.argtypes = [
            ctypes.c_int32,
            u8p,
            sizep,
            u8p,
            u8p,
            u8p,
            ctypes.c_size_t,
            u8p,
            ctypes.c_size_t,
        ]
        lib.pm_crypto_aead_seal.restype = ctypes.c_int32
        lib.pm_crypto_aead_open.argtypes = [
            ctypes.c_int32,
            u8p,
            sizep,
            u8p,
            u8p,
            u8p,
            ctypes.c_size_t,
            u8p,
            ctypes.c_size_t,
        ]
        lib.pm_crypto_aead_open.restype = ctypes.c_int32
        lib.pm_crypto_hkdf_extract.argtypes = [
            ctypes.c_int32,
            u8p,
            u8p,
            ctypes.c_size_t,
            u8p,
            ctypes.c_size_t,
        ]
        lib.pm_crypto_hkdf_extract.restype = ctypes.c_int32
        lib.pm_crypto_hkdf_expand.argtypes = [
            ctypes.c_int32,
            u8p,
            ctypes.c_size_t,
            u8p,
            u8p,
            ctypes.c_size_t,
        ]
        lib.pm_crypto_hkdf_expand.restype = ctypes.c_int32

    def close(self) -> None:
        """``pm_crypto_deinit``: close the module pair. Idempotent."""
        if self._live:
            self._lib.pm_crypto_deinit()
            self._live = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- hashes (32 bytes out) ----

    def sha256(self, data: bytes) -> bytes:
        """SHA-256 (FIPS 180-4) of ``data``."""
        out = _out(HASH_LEN)
        self._lib.pm_crypto_sha256(out, _buf(data), len(data))
        return _bytes(out, HASH_LEN)

    def blake2s(self, data: bytes) -> bytes:
        """Unkeyed BLAKE2s-256 (RFC 7693) of ``data``."""
        out = _out(HASH_LEN)
        self._lib.pm_crypto_blake2s(out, _buf(data), len(data))
        return _bytes(out, HASH_LEN)

    def blake2s_keyed(self, key: bytes, data: bytes) -> bytes:
        """Keyed BLAKE2s-256 (RFC 7693); ``key`` is 1..=32 bytes."""
        if not 1 <= len(key) <= 32:
            raise CryptoArgument(
                f"blake2s_keyed key must be 1..=32 bytes, got {len(key)}"
            )
        out = _out(HASH_LEN)
        self._lib.pm_crypto_blake2s_keyed(
            out, _buf(key), len(key), _buf(data), len(data)
        )
        return _bytes(out, HASH_LEN)

    # ---- X25519 (RFC 7748) ----

    def x25519_public(self, scalar: bytes) -> bytes:
        """X25519(scalar, 9): the 32-byte public key."""
        scalar = _fixed("x25519 scalar", scalar, KEY_LEN)
        out = _out(KEY_LEN)
        self._lib.pm_crypto_x25519_public(out, _buf(scalar))
        return _bytes(out, KEY_LEN)

    def x25519(self, scalar: bytes, point: bytes) -> bytes:
        """X25519(scalar, point). Refuses with ``CryptoLowOrderPoint`` on an
        all-zero output (RFC 7748 §6: a point of small order)."""
        scalar = _fixed("x25519 scalar", scalar, KEY_LEN)
        point = _fixed("x25519 point", point, KEY_LEN)
        out = _out(KEY_LEN)
        rc = self._lib.pm_crypto_x25519(out, _buf(scalar), _buf(point))
        _check(rc, "pm_crypto_x25519")
        return _bytes(out, KEY_LEN)

    # ---- AEAD (ciphertext || 16-byte tag) ----

    def aead_seal(
        self, cipher: int, key: bytes, nonce: bytes, aad: bytes, pt: bytes
    ) -> bytes:
        """Encrypt ``pt``; returns ciphertext || 16-byte tag."""
        key = _fixed("aead key", key, KEY_LEN)
        nonce = _fixed("aead nonce", nonce, NONCE_LEN)
        out = _out(len(pt) + TAG_LEN)
        out_len = ctypes.c_size_t(0)
        rc = self._lib.pm_crypto_aead_seal(
            cipher,
            out,
            ctypes.byref(out_len),
            _buf(key),
            _buf(nonce),
            _buf(aad),
            len(aad),
            _buf(pt),
            len(pt),
        )
        _check(rc, "pm_crypto_aead_seal")
        return _bytes(out, out_len.value)

    def aead_open(
        self, cipher: int, key: bytes, nonce: bytes, aad: bytes, sealed: bytes
    ) -> bytes:
        """Verify the trailing tag of ``sealed`` (ciphertext || tag) and return
        the plaintext. Refuses with ``CryptoTagMismatch`` on a bad tag and with
        ``CryptoArgument`` when the input is shorter than one tag."""
        key = _fixed("aead key", key, KEY_LEN)
        nonce = _fixed("aead nonce", nonce, NONCE_LEN)
        out = _out(max(len(sealed) - TAG_LEN, 0))
        out_len = ctypes.c_size_t(0)
        rc = self._lib.pm_crypto_aead_open(
            cipher,
            out,
            ctypes.byref(out_len),
            _buf(key),
            _buf(nonce),
            _buf(aad),
            len(aad),
            _buf(sealed),
            len(sealed),
        )
        _check(rc, "pm_crypto_aead_open")
        return _bytes(out, out_len.value)

    # ---- HKDF (RFC 5869) ----

    def hkdf_extract(self, hash: int, salt: bytes, ikm: bytes) -> bytes:
        """HKDF-Extract(salt, ikm) -> 32-byte PRK. A zero-length ``salt`` is
        the RFC's absent salt (HashLen zeros)."""
        out = _out(HASH_LEN)
        rc = self._lib.pm_crypto_hkdf_extract(
            hash, out, _buf(salt), len(salt), _buf(ikm), len(ikm)
        )
        _check(rc, "pm_crypto_hkdf_extract")
        return _bytes(out, HASH_LEN)

    def hkdf_expand(self, hash: int, prk: bytes, info: bytes, out_len: int) -> bytes:
        """HKDF-Expand(prk, info, out_len). Refuses with ``CryptoLength`` when
        ``out_len`` exceeds 255 * 32 (RFC 5869)."""
        prk = _fixed("hkdf prk", prk, KEY_LEN)
        if out_len < 0:
            raise CryptoArgument(f"hkdf_expand out_len must be >= 0, got {out_len}")
        if out_len > _EXPAND_ALLOC_LIMIT:
            raise CryptoLength(
                f"hkdf_expand out_len {out_len} over {255 * HASH_LEN} (RFC 5869)"
            )
        out = _out(out_len)
        rc = self._lib.pm_crypto_hkdf_expand(
            hash, out, out_len, _buf(prk), _buf(info), len(info)
        )
        _check(rc, "pm_crypto_hkdf_expand")
        return _bytes(out, out_len)


def _helper() -> Path:
    """``tools/native/crypto_build.py`` — the one build helper (mtime-cached)."""
    root = Path(__file__).resolve().parents[4]
    script = root / "tools" / "native" / "crypto_build.py"
    if not script.is_file():
        raise CryptoBuildError(f"build helper not found at {script}")
    return script


def build(*, force: bool = False) -> Path:
    """Build the Rust card with cargo (via ``tools/native/crypto_build.py``)
    and return the library path. Cached by source mtime unless ``force``.

    With cargo's default target directory the library lands at
    ``native/crypto/target/release/libpm_crypto.so``.
    """
    command = [sys.executable, str(_helper())]
    if force:
        command.append("--force")
    ran = subprocess.run(command, capture_output=True, text=True, check=False)
    if ran.returncode != 0:
        raise CryptoBuildError(
            f"building the cap.crypto card failed (exit {ran.returncode}):\n"
            f"{ran.stderr.strip() or ran.stdout.strip()}"
        )
    lines = ran.stdout.strip().splitlines()
    if not lines:
        raise CryptoBuildError("the build helper printed no library path")
    return Path(lines[-1])


def load(path: str | Path | None = None) -> Crypto:
    """Load the card library. With no ``path``, build it first (cached) and
    load what the build produced."""
    return Crypto(path if path is not None else build())
