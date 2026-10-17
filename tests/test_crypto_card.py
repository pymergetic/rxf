"""The Python gate for the cap.crypto card: one set of vectors, twice proven.

Every published vector is run through the ctypes binding, so this suite proves
that the C ABI and the single Rust implementation (``native/crypto``) agree
with the published sources — the Python layer marshals only. The vectors are
the same ones pinned in ``native/crypto/tests/vectors.rs``; where a source has
no published vector (HKDF-BLAKE2s) the triple is pinned once by the
implementation and labeled as such.

Needs ``cargo`` (the card is ``impl = rs``); without it the suite skips with
the reason below.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "native"))

import crypto_build

from pymergetic.rxftk.net import crypto

pytestmark = pytest.mark.skipif(
    shutil.which("cargo") is None,
    reason="cargo is not installed; cap.crypto is impl = rs and cannot be built",
)


def h(text: str) -> bytes:
    return bytes.fromhex(text)


@pytest.fixture(scope="session")
def card() -> Iterator[crypto.Crypto]:
    library = crypto_build.build()
    with crypto.load(library) as loaded:
        yield loaded


# --------------------------------------------------------------------- build --


def test_build_and_load_helpers(card: crypto.Crypto) -> None:
    """build() runs the one cargo helper and load() opens the library."""
    built = crypto.build()
    assert built.is_file()
    assert built.name == crypto_build.LIB_NAME
    assert crypto_build.is_stale() is False
    assert crypto.build() == built  # second build is the cached one
    assert card.sha256(b"abc").hex() == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_init_deinit_module_pair() -> None:
    """The lifecycle pair is init/deinit (pm_crypto.h; never fini)."""
    with crypto.load() as opened:
        assert opened._live is True
    assert opened._live is False
    opened.close()  # idempotent


# -------------------------------------------------------------------- SHA-256 --


def test_sha256_vectors(card: crypto.Crypto) -> None:
    # Empty input: NIST CAVP SHA test vectors, SHA-256 ShortMsg Len = 0.
    assert card.sha256(b"").hex() == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    # FIPS 180-4 Appendix B.1.
    assert card.sha256(b"abc").hex() == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    # FIPS 180-4 Appendix B.2.
    assert card.sha256(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq").hex() == (
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
    )


def test_sha256_matches_hashlib(card: crypto.Crypto) -> None:
    for n in (0, 1, 55, 56, 63, 64, 65, 127, 128, 1000):
        data = bytes(range(256))[: n % 256] * (n // 256 + 1)
        data = data[:n]
        assert card.sha256(data) == hashlib.sha256(data).digest()


def test_block_boundary_lengths(card: crypto.Crypto) -> None:
    # Every length around the 64-byte block boundary and past the first
    # AEAD chunk: hash/BLAKE2s against hashlib, AEAD round-trips in both
    # ciphers with matching AAD lengths. Guards the buffered paths.
    for n in range(200):
        data = bytes((i * 13 + n) % 256 for i in range(n))
        assert card.sha256(data) == hashlib.sha256(data).digest()
        assert card.blake2s(data) == hashlib.blake2s(data).digest()
        for cipher in (crypto.CIPHER_CHACHA20_POLY1305, crypto.CIPHER_AES_256_GCM):
            sealed = card.aead_seal(cipher, bytes(range(32)), bytes(12), data, data)
            assert len(sealed) == n + 16
            assert card.aead_open(cipher, bytes(range(32)), bytes(12), data, sealed) == data


# -------------------------------------------------------------------- BLAKE2s --


def test_blake2s_vectors(card: crypto.Crypto) -> None:
    # RFC 7693 Appendix B: BLAKE2s-256("abc").
    assert card.blake2s(b"abc").hex() == (
        "508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982"
    )


def test_blake2s_keyed_vectors(card: crypto.Crypto) -> None:
    # Keyed BLAKE2s has no published 256-bit triple in RFC 7693 (its keyed
    # coverage is the Appendix E self-test, which hashes outputs of several
    # lengths — the width rides the face as out_len); these match CPython's
    # hashlib.blake2s(key=…), the independent implementation.
    assert card.blake2s_keyed(h("0001020304050607"), b"abc").hex() == (
        "5cbb58e3b3c8a5b9cb8f209c2cb1f0f11d19bb4fe534c5c3baecd9d861a6aadd"
    )
    assert card.blake2s_keyed(bytes(range(32)), b"").hex() == hashlib.blake2s(
        b"", key=bytes(range(32))
    ).hexdigest()


def test_the_digest_width_rides_the_face(card: crypto.Crypto) -> None:
    """`out_len` is the face's word (RFC 7693 parameterizes the width into the
    digest): 16 bytes is the WireGuard MAC form, and truncating the 32-byte
    digest is a different value — the corpus's `mac16_vectors` say so."""
    key, data = bytes(range(1, 17)), b"initiation packet"
    sixteen = card.blake2s_keyed(key, data, out_len=16)
    assert sixteen == hashlib.blake2s(data, key=key, digest_size=16).digest()
    assert sixteen != card.blake2s_keyed(key, data)[:16]
    assert len(card.blake2s_keyed(key, data, out_len=1)) == 1


def test_blake2s_matches_hashlib(card: crypto.Crypto) -> None:
    for n in (0, 1, 63, 64, 65, 255, 1024):
        data = bytes((i * 7) % 256 for i in range(n))
        assert card.blake2s(data) == hashlib.blake2s(data).digest()
        key = bytes(range(1, 17))
        assert card.blake2s_keyed(key, data) == hashlib.blake2s(
            data, key=key
        ).digest()


# --------------------------------------------------------------------- X25519 --


def test_x25519_vectors(card: crypto.Crypto) -> None:
    # RFC 7748 §5.2: two scalar/u/output triples.
    cases = [
        (
            "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4",
            "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
            "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552",
        ),
        (
            "4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d",
            "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493",
            "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957",
        ),
    ]
    for scalar, point, out in cases:
        assert card.x25519(h(scalar), h(point)) == h(out)


def test_x25519_diffie_hellman(card: crypto.Crypto) -> None:
    # RFC 7748 §6.1: Alice, Bob, and the shared secret.
    alice = h(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
    )
    bob = h("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    shared = h(
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"
    )
    assert card.x25519_public(alice).hex() == (
        "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    )
    assert card.x25519_public(bob).hex() == (
        "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    )
    assert card.x25519(alice, card.x25519_public(bob)) == shared
    assert card.x25519(bob, card.x25519_public(alice)) == shared


def test_x25519_iterated(card: crypto.Crypto) -> None:
    # RFC 7748 §5.2, iterated: k = u = 09||00…00, one iteration.
    k = h("0900000000000000000000000000000000000000000000000000000000000000")
    assert card.x25519(k, k).hex() == (
        "422c8e7a6227d7bca1350b3e2bb7279f7897b87bb6854b783c60e80311ae3079"
    )


# ------------------------------------- ChaCha20-Poly1305 (RFC 8439) ----------

KEY_2_8_2 = bytes.fromhex(
    "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f"
)
NONCE_2_8_2 = bytes.fromhex("070000004041424344454647")
AAD_2_8_2 = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
PT_2_8_2 = (
    b"Ladies and Gentlemen of the class of '99: If I could offer you "
    b"only one tip for the future, sunscreen would be it."
)
CT_2_8_2 = (
    "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
    "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
    "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
    "3ff4def08e4b7a9de576d26586cec64b6116"
)
TAG_2_8_2 = "1ae10b594f09e26a7e902ecbd0600691"


def test_aead_chacha20poly1305_section_2_8_2(card: crypto.Crypto) -> None:
    # RFC 8439 §2.8.2: the worked AEAD example.
    sealed = card.aead_seal(
        crypto.CIPHER_CHACHA20_POLY1305, KEY_2_8_2, NONCE_2_8_2, AAD_2_8_2, PT_2_8_2
    )
    assert sealed == h(CT_2_8_2 + TAG_2_8_2)
    opened = card.aead_open(
        crypto.CIPHER_CHACHA20_POLY1305,
        KEY_2_8_2,
        NONCE_2_8_2,
        AAD_2_8_2,
        h(CT_2_8_2 + TAG_2_8_2),
    )
    assert opened == PT_2_8_2


def test_aead_chacha20poly1305_appendix_a_5(card: crypto.Crypto) -> None:
    # RFC 8439 §A.5: the decryption example. The plaintext is the RFC's hex
    # dump exactly (each smart quote is preceded by a literal '/').
    key = h(
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0"
    )
    nonce = h("000000000102030405060708")
    aad = h("f33388860000000000004e91")
    sealed = h(
        "64a0861575861af460f062c79be643bd5e805cfd345cf389f108670ac76c8cb2"
        "4c6cfc18755d43eea09ee94e382d26b0bdb7b73c321b0100d4f03b7f355894cf"
        "332f830e710b97ce98c8a84abd0b948114ad176e008d33bd60f982b1ff37c855"
        "9797a06ef4f0ef61c186324e2b3506383606907b6a7c02b0f9f6157b53c867e4"
        "b9166c767b804d46a59b5216cde7a4e99040c5a40433225ee282a1b0a06c523e"
        "af4534d7f83fa1155b0047718cbc546a0d072b04b3564eea1b422273f548271a"
        "0bb2316053fa76991955ebd63159434ecebb4e466dae5a1073a6727627097a10"
        "49e617d91d361094fa68f0ff77987130305beaba2eda04df997b714d6c6f2c29"
        "a6ad5cb4022b02709b"
        "eead9d67890cbb22392336fea1851f38"
    )
    pt = (
        "Internet-Drafts are draft documents valid for a maximum of six "
        "months and may be updated, replaced, or obsoleted by other "
        "documents at any time. It is inappropriate to use Internet-Drafts "
        "as reference material or to cite them other than as /"
        "\u201cwork in progress./\u201d"
    ).encode()
    assert card.aead_open(crypto.CIPHER_CHACHA20_POLY1305, key, nonce, aad, sealed) == pt
    assert card.aead_seal(crypto.CIPHER_CHACHA20_POLY1305, key, nonce, aad, pt) == sealed


# ------------------------------------------------ AES-256-GCM (NIST CAVP) -----


def test_aead_aes256_gcm_cavp_encrypt(card: crypto.Crypto) -> None:
    # NIST CAVP gcmEncryptExtIV256.rsp [Keylen=256][IVlen=96][Taglen=128],
    # quoted from the verbatim mirror in google/quiche
    # quiche/quic/core/crypto/aes_256_gcm_encrypter_test.cc.
    # [PTlen = 0][AADlen = 0], Count = 0 (seal output is the tag alone):
    key = h("b52c505a37d78eda5dd34f20c22540ea1b58963cf8e5bf8ffa85f9f2492505b4")
    nonce = h("516c33929df5a3284ff463d7")
    assert card.aead_seal(crypto.CIPHER_AES_256_GCM, key, nonce, b"", b"") == h(
        "bdc1ac884d332457a1d2664f168c76f0"
    )
    # [PTlen = 128][AADlen = 0], Count = 0 (seal output = CT || Tag):
    key = h("31bdadd96698c204aa9ce1448ea94ae1fb4a9a0b3c9d773b51bb1822666b8f22")
    nonce = h("0d18e06c7c725ac9e362e1ce")
    pt = h("2db5168e932556f8089a0622981d017d")
    sealed = h(
        "fa4362189661d163fcd6a56d8bf0405a" "d636ac1bbedd5cc3ee727dc2ab4a9489"
    )
    assert card.aead_seal(crypto.CIPHER_AES_256_GCM, key, nonce, b"", pt) == sealed
    assert card.aead_open(crypto.CIPHER_AES_256_GCM, key, nonce, b"", sealed) == pt


def test_aead_aes256_gcm_cavp_decrypt(card: crypto.Crypto) -> None:
    # NIST CAVP gcmDecrypt256.rsp [Keylen=256][IVlen=96][PTlen=128]
    # [AADlen=128][Taglen=128], Count = 0 (verifies):
    key = h("54e352ea1d84bfe64a1011096111fbe7668ad2203d902a01458c3bbd85bfce14")
    nonce = h("df7c3bca00396d0c018495d9")
    aad = h("7e968d71b50c1f11fd001f3fef49d045")
    sealed = h(
        "426e0efc693b7be1f3018db7ddbb7e4d" "ee8257795be6a1164d7e1d2d6cac77a7"
    )
    assert card.aead_open(crypto.CIPHER_AES_256_GCM, key, nonce, aad, sealed) == h(
        "85fc3dfad9b5a8d3258e4fc44571bd3b"
    )
    # Count = 2 of that section is a FAIL entry: the tag must not open.
    key = h("9a0343f850a6427120f764789ffec6d237447b898fbf51d2182f065d3861497d")
    nonce = h("3deef6f453dd70d92143adcd")
    aad = h("dbb8226a624520863db6897017b2a4f8")
    sealed = h(
        "e93165935ac18e3a2845d15fe31a9286" "f5fc50d18766bc3d9e16dd136d45816b"
    )
    with pytest.raises(crypto.CryptoTagMismatch):
        card.aead_open(crypto.CIPHER_AES_256_GCM, key, nonce, aad, sealed)
    # [PTlen = 0][AADlen = 0][Taglen = 128], Count = 0 verifies empty, and
    # Count = 1 is a FAIL entry.
    key = h("f5a2b27c74355872eb3ef6c5feafaa740e6ae990d9d48c3bd9bb8235e589f010")
    nonce = h("58d2240f580a31c1d24948e9")
    assert card.aead_open(
        crypto.CIPHER_AES_256_GCM, key, nonce, b"", h("15e051a5e4a5f5da6cea92e2ebee5bac")
    ) == b""
    key = h("e5a8123f2e2e007d4e379ba114a2fb66e6613f57c72d4e4f024964053028a831")
    nonce = h("51e43385bf533e168427e1ad")
    with pytest.raises(crypto.CryptoTagMismatch):
        card.aead_open(
            crypto.CIPHER_AES_256_GCM, key, nonce, b"", h("38fe845c66e66bdd884c2aecafd280e6")
        )


# ----------------------------------------------------------------- refusals ---


def test_aead_bad_tag_refused(card: crypto.Crypto) -> None:
    # A flipped tag byte must refuse (RFC 8439 §2.8.2 vector, tag flipped).
    sealed = bytearray(h(CT_2_8_2 + TAG_2_8_2))
    sealed[-1] ^= 0x01
    with pytest.raises(crypto.CryptoTagMismatch):
        card.aead_open(
            crypto.CIPHER_CHACHA20_POLY1305,
            KEY_2_8_2,
            NONCE_2_8_2,
            AAD_2_8_2,
            bytes(sealed),
        )
    with pytest.raises(crypto.CryptoTagMismatch):
        card.aead_open(crypto.CIPHER_AES_256_GCM, KEY_2_8_2, NONCE_2_8_2, b"", bytes(sealed))


def test_x25519_low_order_point_refused(card: crypto.Crypto) -> None:
    # u = 0 is the order-2 point on the Montgomery curve: every scalar
    # multiple of it is the identity and the shared secret is all zeros
    # (RFC 7748 §6.1 warns about such points); the card refuses. The second
    # case is the non-canonical encoding of p ≡ 0 (same point, more bytes).
    scalar = bytes(range(32))
    with pytest.raises(crypto.CryptoLowOrderPoint):
        card.x25519(scalar, bytes(32))
    with pytest.raises(crypto.CryptoLowOrderPoint):
        card.x25519(scalar, bytes([0xED]) + bytes([0xFF] * 30) + bytes([0x7F]))


def test_hkdf_expand_length_limits(card: crypto.Crypto) -> None:
    # RFC 5869 caps Expand at 255 * HashLen; one byte more is refused.
    prk = card.hkdf_extract(crypto.HASH_SHA256, b"salt", b"ikm")
    assert len(card.hkdf_expand(crypto.HASH_SHA256, prk, b"info", 255 * 32)) == 255 * 32
    with pytest.raises(crypto.CryptoLength):
        card.hkdf_expand(crypto.HASH_SHA256, prk, b"info", 255 * 32 + 1)
    # ... and the marshalling guard refuses absurd sizes before allocating.
    with pytest.raises(crypto.CryptoLength):
        card.hkdf_expand(crypto.HASH_SHA256, prk, b"info", 1 << 40)


def test_bad_ids_and_short_inputs_refused(card: crypto.Crypto) -> None:
    with pytest.raises(crypto.CryptoArgument):
        card.aead_seal(7, bytes(32), bytes(12), b"", b"")  # unknown cipher id
    with pytest.raises(crypto.CryptoArgument):
        card.hkdf_extract(5, b"salt", b"ikm")  # unknown hash id
    with pytest.raises(crypto.CryptoArgument):
        card.aead_seal(crypto.CIPHER_AES_256_GCM, bytes(31), bytes(12), b"", b"")
    with pytest.raises(crypto.CryptoArgument):
        card.aead_seal(crypto.CIPHER_AES_256_GCM, bytes(32), bytes(11), b"", b"")
    with pytest.raises(crypto.CryptoArgument):
        card.aead_open(crypto.CIPHER_AES_256_GCM, bytes(32), bytes(12), b"", bytes(8))
    with pytest.raises(crypto.CryptoArgument):
        card.x25519(bytes(31), bytes(32))
    with pytest.raises(crypto.CryptoArgument):
        card.blake2s_keyed(b"", b"data")


# --------------------------------------------------------------------- HKDF --


def _hkdf_case(
    card: crypto.Crypto,
    hash_id: int,
    ikm: bytes,
    salt: bytes,
    info: bytes,
    prk: bytes,
    okm: bytes,
) -> None:
    assert card.hkdf_extract(hash_id, salt, ikm) == prk
    assert card.hkdf_expand(hash_id, prk, info, len(okm)) == okm


def test_hkdf_sha256_rfc5869(card: crypto.Crypto) -> None:
    # RFC 5869 Appendix A, SHA-256 test cases 1-3 (extract + expand).
    _hkdf_case(
        card,
        crypto.HASH_SHA256,
        h("0b" * 22),
        h("000102030405060708090a0b0c"),
        h("f0f1f2f3f4f5f6f7f8f9"),
        h("077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5"),
        h(
            "3cb25f25faacd57a90434f64d0362f2a"
            "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        ),
    )
    _hkdf_case(
        card,
        crypto.HASH_SHA256,
        bytes(range(0x4F + 1)),
        bytes(range(0x60, 0xAF + 1)),
        bytes(range(0xB0, 0xFF + 1)),
        h("06a6b88c5853361a06104c9ceb35b45cef760014904671014a193f40c15fc244"),
        h(
            "b11e398dc80327a1c8e7f78c596a4934"
            "4f012eda2d4efad8a050cc4c19afa97c"
            "59045a99cac7827271cb41c65e590e09"
            "da3275600c2f09b8367793a9aca3db71"
            "cc30c58179ec3e87c14c01d5c1f3434f1d87"
        ),
    )
    _hkdf_case(
        card,
        crypto.HASH_SHA256,
        h("0b" * 22),
        b"",
        b"",
        h("19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04"),
        h(
            "8da4e775a563c18f715f802a063c5a31"
            "b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8"
        ),
    )


def test_hkdf_blake2s_pinned_triple(card: crypto.Crypto) -> None:
    # HKDF-BLAKE2s (HMAC-BLAKE2s-256 over RFC 5869) has no published vectors;
    # this triple was generated once by the implementation and pinned — the
    # same provenance as native/crypto/tests/vectors.rs.
    _hkdf_case(
        card,
        crypto.HASH_BLAKE2S,
        b"cap.crypto hkdf-blake2s pin: ikm",
        b"cap.crypto hkdf-blake2s pin: salt",
        b"cap.crypto hkdf-blake2s pin: info",
        h("6c6fe5e799e3addd8506a1d8d29209c9d3067a6bbf24059ddb6877acb109023e"),
        h(
            "076d127611325d03ea8c849fd471bb6e"
            "1ab73b435da3aef2909fc842c7544a29"
            "261f5b33e51978ba14cc"
        ),
    )
