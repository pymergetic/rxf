"""Ed25519 (RFC 8032) in pure Python: the scheme one voice signs with.

The reference algorithm, transcribed once. Correctness is not a matter of trust:
`tests/test_certify_ed25519.py` pins `public_key`, `sign` and `verify` against
the RFC 8032 section 7.1 test vectors, byte for byte. SHA-512 comes from
`hashlib`; there is no other dependency and no second scheme.

Points are extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z and X*Y =
Z*T. A signature is R || S (64 bytes); a key is a 32-byte seed whose SHA-512
clamps to the scalar and prefixes the nonce.
"""

from __future__ import annotations

import hashlib

# The curve words of RFC 8032 section 5.1.
P = 2**255 - 19
Q = 2**252 + 27742317777372353535851937790883648493
D = -121665 * pow(121666, P - 2, P) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

Point = tuple[int, int, int, int]  # extended coordinates (X, Y, Z, T)

SEED_BYTES = 32
SIGNATURE_BYTES = 64


def _recover_x(y: int, sign: int) -> int | None:
    """The x of a compressed point at this y with this sign bit; none when it does not exist."""
    if y >= P:
        return None
    denominator = (D * y * y + 1) % P
    if denominator == 0:
        return None
    x2 = (y * y - 1) * pow(denominator, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P != 0:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P != 0:
        return None
    return x if (x & 1) == sign else P - x


def _add(one: Point, other: Point) -> Point:
    """One point added to another (the unified extended formula)."""
    a = (one[1] - one[0]) * (other[1] - other[0]) % P
    b = (one[1] + one[0]) * (other[1] + other[0]) % P
    c = 2 * one[3] * other[3] * D % P
    d = 2 * one[2] * other[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar: int, point: Point) -> Point:
    """A point multiplied by a scalar, double and add."""
    total: Point = (0, 1, 1, 0)  # the neutral element
    while scalar > 0:
        if scalar & 1:
            total = _add(total, point)
        point = _add(point, point)
        scalar >>= 1
    return total


def _equal(one: Point, other: Point) -> bool:
    """Whether two points are one point (compared in projective form)."""
    return (
        (one[0] * other[2] - other[0] * one[2]) % P == 0
        and (one[1] * other[2] - other[1] * one[2]) % P == 0
    )


def _compress(point: Point) -> bytes:
    """The 32-byte compressed encoding of a point (y, with the x sign in the top bit)."""
    zinv = pow(point[2], P - 2, P)
    x = point[0] * zinv % P
    y = point[1] * zinv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decompress(data: bytes) -> Point | None:
    """The point behind a 32-byte encoding; none when the bytes name no point."""
    if len(data) != 32:
        raise ValueError("a compressed point is 32 bytes")
    word = int.from_bytes(data, "little")
    sign, y = word >> 255, word & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % P)


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
assert _GX is not None
G: Point = (_GX, _GY, 1, _GX * _GY % P)  # the base point of RFC 8032 section 5.1


def _expand(seed: bytes) -> tuple[int, bytes]:
    """The clamped scalar and the nonce prefix behind a 32-byte seed."""
    if len(seed) != SEED_BYTES:
        raise ValueError(f"a seed is {SEED_BYTES} bytes")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def _mod_q(data: bytes) -> int:
    """A hash read as a little-endian scalar modulo the group order."""
    return int.from_bytes(hashlib.sha512(data).digest(), "little") % Q


def public_key(seed: bytes) -> bytes:
    """The 32-byte public key of a 32-byte seed."""
    scalar, _ = _expand(seed)
    return _compress(_mul(scalar, G))


def sign(seed: bytes, message: bytes) -> bytes:
    """The 64-byte signature of a message under a seed (deterministic, RFC 8032)."""
    scalar, prefix = _expand(seed)
    public = _compress(_mul(scalar, G))
    nonce = _mod_q(prefix + message)
    r = _compress(_mul(nonce, G))
    s = (nonce + _mod_q(r + public + message) * scalar) % Q
    return r + s.to_bytes(32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """Whether the signature is the message's under this public key (RFC 8032's check)."""
    if len(public) != 32:
        raise ValueError("a public key is 32 bytes")
    if len(signature) != SIGNATURE_BYTES:
        raise ValueError(f"a signature is {SIGNATURE_BYTES} bytes")
    point = _decompress(public)
    if point is None:
        return False
    r_bytes, s_bytes = signature[:32], signature[32:]
    r = _decompress(r_bytes)
    if r is None:
        return False
    s = int.from_bytes(s_bytes, "little")
    if s >= Q:
        return False
    h = _mod_q(r_bytes + public + message)
    return _equal(_mul(s, G), _add(r, _mul(h, point)))
