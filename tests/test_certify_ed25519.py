"""The voice's scheme pinned to RFC 8032: correctness is not a matter of trust.

These are the published section 7.1 test vectors (1 to 3), hex as they appear in
the RFC. `public_key`, `sign` and `verify` must reproduce them byte for byte —
if this file passes, the scheme is the reference algorithm, not an imitation.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.certify import ed25519

VECTORS = (
    (
        "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        "",
        (
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
            "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        ),
    ),
    (
        "4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        "72",
        (
            "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
            "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"
        ),
    ),
    (
        "c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        "af82",
        (
            "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
            "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"
        ),
    ),
)


def test_the_scheme_reproduces_the_rfc_8032_vectors():
    """The public key of a seed, the signature of a message, and its verification."""
    for seed_hex, public_hex, message_hex, signature_hex in VECTORS:
        seed, public = bytes.fromhex(seed_hex), bytes.fromhex(public_hex)
        message, signature = bytes.fromhex(message_hex), bytes.fromhex(signature_hex)
        assert ed25519.public_key(seed) == public
        assert ed25519.sign(seed, message) == signature  # deterministic per RFC 8032
        assert ed25519.verify(public, message, signature)


def test_a_tampered_message_is_not_verified() -> None:
    """One byte of the message more, and the signature is no longer the message's."""
    seed_hex, public_hex, message_hex, signature_hex = VECTORS[2]
    assert not ed25519.verify(
        bytes.fromhex(public_hex),
        bytes.fromhex(message_hex) + b"\x00",
        bytes.fromhex(signature_hex),
    )
    assert not ed25519.verify(
        bytes.fromhex(public_hex), b"", bytes.fromhex(signature_hex)
    )
    assert ed25519.public_key(bytes.fromhex(seed_hex)) == bytes.fromhex(public_hex)


def test_a_tampered_signature_is_not_verified() -> None:
    """A flipped bit of R or S verifies against nothing."""
    _, public_hex, message_hex, signature_hex = VECTORS[1]
    public, message = bytes.fromhex(public_hex), bytes.fromhex(message_hex)
    signature = bytearray.fromhex(signature_hex)
    for index in (0, 32, 63):
        broken = bytearray(signature)
        broken[index] ^= 0x01
        assert not ed25519.verify(public, message, bytes(broken))
    assert ed25519.verify(public, message, bytes(signature))


def test_another_public_key_does_not_verify() -> None:
    """The signature is one voice's: another key's word accepts none of it."""
    _, public_hex, message_hex, signature_hex = VECTORS[0]
    other = ed25519.public_key(bytes.fromhex(VECTORS[1][0]))
    assert not ed25519.verify(
        other, bytes.fromhex(message_hex), bytes.fromhex(signature_hex)
    )
    assert ed25519.verify(
        bytes.fromhex(public_hex),
        bytes.fromhex(message_hex),
        bytes.fromhex(signature_hex),
    )


def test_s_and_r_outside_the_group_verify_nothing() -> None:
    """S >= q and a 32-byte R that names no point are refused (RFC 8032 section 5.1.7)."""
    _, public_hex, message_hex, signature_hex = VECTORS[0]
    public, message = bytes.fromhex(public_hex), bytes.fromhex(message_hex)
    over = (ed25519.Q).to_bytes(32, "little") + bytes.fromhex(signature_hex)[32:]
    assert not ed25519.verify(public, message, over)
    no_point = (ed25519.P).to_bytes(32, "little") + bytes.fromhex(signature_hex)[32:]
    assert not ed25519.verify(public, message, no_point)


def test_the_scheme_refuses_key_material_of_the_wrong_size() -> None:
    """A seed that is not 32 bytes is refused, not expanded into something."""
    with pytest.raises(ValueError):
        ed25519.public_key(b"\x00" * 31)
    with pytest.raises(ValueError):
        ed25519.sign(b"\x00" * 33, b"")
    with pytest.raises(ValueError):
        ed25519.verify(b"\x00" * 31, b"", bytes(64))
