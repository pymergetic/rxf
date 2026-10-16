"""The gates of the cap.crypto import lane: the card's Rust source on image words.

``tools/native/crypto_import.py`` lowers THE SAME source (``native/crypto/src``)
through the LLVM-IR subset of ``tools/native/rximport.py`` onto the image's
semantic graphs and composes program words; this suite runs those words on the
interpreter of ``native/graph.c`` (``tests/rxf_native.py``) and pins them
against the published vectors the native card's own gate
(``tests/test_crypto_card.py``) cites -- RFC 7748 (X25519), RFC 8439
(ChaCha20-Poly1305), RFC 5869 (HKDF-SHA256), FIPS 180-4 (SHA-256), RFC 7693
(BLAKE2s), FIPS 197 (AES), and the NIST CAVP AES-GCM rows. One definition, one
set of vectors, two seating paths.

Three things are pinned here:

(a) the words of every imported function RUN on the interpreter and answer the
    published vectors of their primitive (RFC 8439 §2.1.1 and §2.2.1 for the
    ChaCha quarter round, FIPS 197 §4.2.1 and §4.2.2 for xtime, RFC 7693 §3.1
    for the BLAKE2s G step -- the three kernels -- and the i32 0 of
    ``pm_crypto_deinit``);
(b) the differential: where both seats can name the same face, the image words
    and the native C ABI answer the same (``pm_crypto_deinit``, whose native
    seat is ``card.close()``), and for every imported kernel the same source's
    native seat answers the primitive's family vectors while the report's named
    gap records that the kernel itself has no native face to differ against
    (the card's kernels are private module fns -- rustc E0603 on any wrapper's
    path -- and the card files carry ``//!`` inner docs, so ``include!`` refuses
    them: rustc E0753);
(c) the refusal ledger: every function the subset cannot take, pinned BY NAME
    with the IR construct that refused it, so the named gaps stay pinned and
    silent drift is a test diff.

The native seat of (b) is built cargo-free: the same ``lib.rs`` through the same
rustc to a cdylib (the gate is rustc + cargo-free by mandate), loaded through
the same ``src/pymergetic/rxf/net/crypto.py`` binding the card's gate uses.
Everything here is deterministic and offline.
"""

from __future__ import annotations

import struct
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import crypto_import
import pytest

from pymergetic.rxf.net import crypto

from . import rxf_native

ROOT = Path(__file__).resolve().parents[1]

# The imported set of the card source at this door (the report pins it BY NAME):
# the one C ABI entry the subset takes plus the three kernels of the primitives.
IMPORTED_FACES = {
    "pm_crypto_deinit",
    "aes_gcm::xtime",
    "blake2s::g",
    "chacha20poly1305::quarter_round",
}

# Every refusal of the subset, pinned BY NAME: (face, the function the refusing
# construct sits in, policy or gap, the construct). "policy" is rximport's own
# vocabulary (the construct is outside the subset by design); "gap" is the
# importer's own missing handler, named by the exception. The eight constructs:
# sret aggregate return, aggregate struct return, i128 return, indirect call,
# gep of a local, global without bytes, value carried through memory, and the
# `unreachable` terminator (the importer gap).
REFUSAL_LEDGER = [
    ("pm_crypto_aead_open", "aead", "policy", "indirect call"),
    ("pm_crypto_aead_seal", "aead", "policy", "indirect call"),
    ("pm_crypto_blake2s", "out_slice", "policy", "aggregate struct return"),
    ("pm_crypto_blake2s_keyed", "out_slice", "policy", "aggregate struct return"),
    ("pm_crypto_hkdf_expand", "pm_crypto_hkdf_expand", "policy", "global without bytes"),
    ("pm_crypto_hkdf_extract", "pm_crypto_hkdf_extract", "policy", "global without bytes"),
    ("pm_crypto_init", "pm_crypto_init", "policy", "gep of a local"),
    ("pm_crypto_sha256", "out_slice", "policy", "aggregate struct return"),
    ("pm_crypto_x25519", "out_slice", "policy", "aggregate struct return"),
    ("pm_crypto_x25519_public", "out_slice", "policy", "aggregate struct return"),
    ("aead", "aead", "policy", "indirect call"),
    (
        "aes_gcm::Aes256::encrypt_block",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("aes_gcm::Aes256::new", "aes_gcm::Aes256::new", "policy", "sret aggregate return"),
    ("aes_gcm::from_u", "aes_gcm::from_u", "policy", "sret aggregate return"),
    ("aes_gcm::gctr", "aes_gcm::gctr", "policy", "global without bytes"),
    ("aes_gcm::gf_mul", "aes_gcm::gf_mul", "policy", "i128 return"),
    ("aes_gcm::ghash", "aes_gcm::ghash", "policy", "i128 return"),
    (
        "aes_gcm::inc",
        "core::iter::traits::iterator::Iterator::rev",
        "policy",
        "aggregate struct return",
    ),
    ("aes_gcm::j", "aes_gcm::j", "policy", "sret aggregate return"),
    (
        "aes_gcm::mix_columns",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("aes_gcm::open", None, "gap", "unreachable terminator (importer gap)"),
    ("aes_gcm::seal", None, "gap", "unreachable terminator (importer gap)"),
    (
        "aes_gcm::shift_rows",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("aes_gcm::to_u", "aes_gcm::to_u", "policy", "i128 return"),
    (
        "aes_gcm::xor",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    (
        "blake2s::Blake2s::finalize",
        "core::fmt::Arguments::from_str",
        "policy",
        "aggregate struct return",
    ),
    ("blake2s::Blake2s::new", "blake2s::Blake2s::new", "policy", "sret aggregate return"),
    ("blake2s::Blake2s::update", None, "gap", "unreachable terminator (importer gap)"),
    ("blake2s::blake2s", "blake2s::blake2s", "policy", "sret aggregate return"),
    ("blake2s::blake2s_keyed", "blake2s::blake2s_keyed", "policy", "sret aggregate return"),
    (
        "blake2s::compress",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("chacha20poly1305::aead_tag", "chacha20poly1305::aead_tag", "policy", "sret aggregate return"),
    (
        "chacha20poly1305::chacha20_apply",
        "chacha20poly1305::chacha20_apply",
        "policy",
        "global without bytes",
    ),
    (
        "chacha20poly1305::chacha20_block",
        "chacha20poly1305::chacha20_block",
        "policy",
        "sret aggregate return",
    ),
    (
        "chacha20poly1305::ct_eq",
        "core::slice::_<impl $u5b$T$u5d$>::iter",
        "policy",
        "aggregate struct return",
    ),
    ("chacha20poly1305::open", None, "gap", "unreachable terminator (importer gap)"),
    ("chacha20poly1305::poly", "chacha20poly1305::poly", "policy", "sret aggregate return"),
    ("chacha20poly1305::poly_key", "chacha20poly1305::poly_key", "policy", "sret aggregate return"),
    ("chacha20poly1305::seal", None, "gap", "unreachable terminator (importer gap)"),
    ("chacha20poly1305::u32le", None, "gap", "unreachable terminator (importer gap)"),
    ("fixed", "fixed", "policy", "value carried through memory"),
    ("fixed", "fixed", "policy", "value carried through memory"),
    ("hkdf::Digest::bytes", "hkdf::Digest::bytes", "policy", "value carried through memory"),
    ("hkdf::expand", "hkdf::expand", "policy", "value carried through memory"),
    ("hkdf::extract", "hkdf::extract", "policy", "sret aggregate return"),
    ("hkdf::hash_of", "hkdf::hash_of", "policy", "sret aggregate return"),
    ("hkdf::hmac", "hkdf::hmac", "policy", "sret aggregate return"),
    ("out_slice", "out_slice", "policy", "aggregate struct return"),
    (
        "pm_crypto_x25519::_{{closure}}",
        "pm_crypto_x25519::_{{closure}}",
        "policy",
        "global without bytes",
    ),
    ("sha256::Sha256::finalize", "sha256::Sha256::finalize", "policy", "sret aggregate return"),
    ("sha256::Sha256::new", "sha256::Sha256::new", "policy", "sret aggregate return"),
    ("sha256::Sha256::update", None, "gap", "unreachable terminator (importer gap)"),
    (
        "sha256::compress",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("sha256::sha", "sha256::sha", "policy", "sret aggregate return"),
    ("slice", "slice", "policy", "aggregate struct return"),
    ("x25519::carry", "x25519::carry", "policy", "sret aggregate return"),
    (
        "x25519::cswap",
        "_<I as core::iter::traits::collect::IntoIterator>::into_iter",
        "policy",
        "aggregate struct return",
    ),
    ("x25519::fe_add", "x25519::fe_add", "policy", "sret aggregate return"),
    ("x25519::fe_frombytes", "x25519::fe_frombytes", "policy", "sret aggregate return"),
    ("x25519::fe_invert", "x25519::fe_invert", "policy", "sret aggregate return"),
    ("x25519::fe_mul", "x25519::fe_mul", "policy", "sret aggregate return"),
    ("x25519::fe_sq", "x25519::fe_sq", "policy", "sret aggregate return"),
    ("x25519::fe_sub", "x25519::fe_sub", "policy", "sret aggregate return"),
    ("x25519::fe_tobytes", "x25519::fe_tobytes", "policy", "sret aggregate return"),
    ("x25519::x", "x25519::x", "policy", "sret aggregate return"),
    ("x25519::x25519_public", "x25519::x25519_public", "policy", "sret aggregate return"),
]

CONSTRUCT_NEEDLES = [
    "returns through an sret pointer",
    "returns }, not an integer",
    "returns i128, not an integer",
    "an indirect call without its callee",
    "getelementptr of the local",
    "has no payload this pass can place",
    "is read where only another block wrote it",
    "no attribute 'lower_unreachable'",
]

CONSTRUCTS = [
    "sret aggregate return",
    "aggregate struct return",
    "i128 return",
    "indirect call",
    "gep of a local",
    "global without bytes",
    "value carried through memory",
    "unreachable terminator (importer gap)",
]


def h(text: str) -> bytes:
    return bytes.fromhex(text)


@pytest.fixture(scope="module")
def card() -> crypto_import.CardImport:
    """The card source through the door: rustc -> rximport -> graphs -> words."""
    return crypto_import.import_card()


@pytest.fixture(scope="session")
def native_card() -> Iterator[crypto.Crypto]:
    """The same source's other seat: the C ABI of the same lib.rs, built
    cargo-free (the gate's mandate) and loaded through the same binding the
    card's own gate uses."""
    with tempfile.TemporaryDirectory(prefix="crypto-native-") as own:
        built = Path(own) / "libpm_crypto.so"
        made = subprocess.run(
            [
                crypto_import.RUSTC,
                "--crate-type=cdylib",
                "--edition",
                crypto_import.crate_edition(),
                "-C",
                "opt-level=0",
                "-C",
                "panic=abort",
                str(crypto_import.CARD),
                "-o",
                str(built),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert made.returncode == 0, made.stderr
        with crypto.Crypto(built) as loaded:
            yield loaded


def call(
    memory: rxf_native.Memory, card: crypto_import.CardImport, face: str, values: tuple[int, ...]
) -> tuple[int, int]:
    """One run of an imported word on the interpreter, by face name."""
    row = card.report(face)
    program = card.words[row.name]
    (arity,) = struct.unpack_from("<1Q", program, 24)
    assert arity == len(values), (face, arity, len(values))
    return rxf_native.run(program, struct.pack(f"<{len(values)}Q", *values), memory)


def test_the_report_takes_every_card_function(card: crypto_import.CardImport) -> None:
    # the report is the refusal ledger of the source: nothing falls out silently
    assert {row.face for row in card.imported()} == IMPORTED_FACES
    assert all(row.name.startswith("pm_crypto_") for row in card.reports if row.entry)
    assert {face for face, _, _, _ in REFUSAL_LEDGER} <= {row.face for row in card.refused()}
    assert len(card.reports) == len(IMPORTED_FACES) + len(REFUSAL_LEDGER)
    assert len({row.name for row in card.reports}) == len(card.reports)


# ------------------------------------------- (a) the words run on the image ----


def test_quarter_round_pins_rfc_8439_section_2_1_1(card: crypto_import.CardImport) -> None:
    # RFC 8439 §2.1.1: the quarter round of (a, b, c, d) as a state of its own
    state = (0x11111111, 0x01020304, 0x9B8D6F43, 0x01234567) + (0,) * 12
    with rxf_native.Memory(1 << 16) as memory:
        memory.write(0x200, struct.pack("<16I", *state))
        status, _ = call(
            memory, card, "chacha20poly1305::quarter_round", (memory.at(0x200), 0, 1, 2, 3)
        )
        assert status == 0
        out = struct.unpack("<16I", memory.read(0x200, 64))
    assert out[:4] == (0xEA2A92F4, 0xCB1CF8CE, 0x4581472E, 0x5881C4BB)
    assert out[4:] == (0,) * 12  # the round touches only its four words


def test_quarter_round_pins_rfc_8439_section_2_2_1(card: crypto_import.CardImport) -> None:
    # RFC 8439 §2.2.1: QUARTERROUND(2, 7, 8, 13) on the sample ChaCha state
    sample = [
        0x879531E0, 0xC5ECF37D, 0x516461B1, 0xC9A62F8A,
        0x44C20EF3, 0x3390AF7F, 0xD9FC690B, 0x2A5F714C,
        0x53372767, 0xB00A5631, 0x974C541A, 0x359E9963,
        0x5C971061, 0x3D631689, 0x2098D9D6, 0x91DBD320,
    ]
    published = [
        0x879531E0, 0xC5ECF37D, 0xBDB886DC, 0xC9A62F8A,
        0x44C20EF3, 0x3390AF7F, 0xD9FC690B, 0xCFACAFD2,
        0xE46BEA80, 0xB00A5631, 0x974C541A, 0x359E9963,
        0x5C971061, 0xCCC07C79, 0x2098D9D6, 0x91DBD320,
    ]
    with rxf_native.Memory(1 << 16) as memory:
        memory.write(0x200, struct.pack("<16I", *sample))
        status, _ = call(
            memory, card, "chacha20poly1305::quarter_round", (memory.at(0x200), 2, 7, 8, 13)
        )
        assert status == 0
        out = struct.unpack("<16I", memory.read(0x200, 64))
    assert list(out) == published


def test_xtime_pins_fips_197_section_4_2(card: crypto_import.CardImport) -> None:
    # FIPS 197 §4.2.1 (the xtime definition) and §4.2.2 (the 57·13 worked
    # example: 57·02 = ae, 57·04 = 47, 57·08 = 8e) and the edges of the
    # definition (00 -> 00, 01 -> 02, 80 -> 1b, ff -> e5).
    published = [
        (0x57, 0xAE),
        (0xAE, 0x47),
        (0x47, 0x8E),
        (0x8E, 0x07),
        (0x00, 0x00),
        (0x01, 0x02),
        (0x80, 0x1B),
        (0xFF, 0xE5),
    ]
    with rxf_native.Memory(1 << 16) as memory:
        for given, expected in published:
            status, value = call(memory, card, "aes_gcm::xtime", (given,))
            assert (status, value & 0xFF) == (0, expected), given


def test_g_pins_rfc_7693_section_3_1(card: crypto_import.CardImport) -> None:
    # RFC 7693 §3.1 G for BLAKE2s (w = 32, rotations (16, 12, 8, 7) of §2.1).
    # The RFC publishes no step vector of its own, so the cases below are the
    # definition evaluated by hand (each line of §3.1 in the comment) and then
    # confirmed by the words -- one definition, written down and pinned:
    #   case 1, v = 0, x = 1, y = 0, (a, b, c, d) = (0, 1, 2, 3):
    #     v[0] = 0 + 0 + 1 = 1;  v[3] = rotr(0 ^ 1, 16) = 0x00010000
    #     v[2] = 0 + 0x00010000 = 0x00010000;  v[1] = rotr(0 ^ 0x00010000, 12) = 0x10
    #     v[0] = 1 + 0x10 + 0 = 0x11;  v[3] = rotr(0x00010000 ^ 0x11, 8) = 0x11000100
    #     v[2] = 0x00010000 + 0x11000100 = 0x11010100
    #     v[1] = rotr(0x10 ^ 0x11010100, 7) = 0x20220202
    cases = [
        ([0] * 16, 0, 1, 2, 3, 0, 0, [0, 0, 0, 0]),
        ([0] * 16, 0, 1, 2, 3, 1, 0, [0x00000011, 0x20220202, 0x11010100, 0x11000100]),
        (
            [1, 2, 3, 4] + [0] * 12,
            0,
            1,
            2,
            3,
            0,
            0,
            [0x00100073, 0xE6E62E2E, 0x73071703, 0x73001700],
        ),
        (
            [0] * 16,
            5,
            6,
            7,
            8,
            0x01234567,
            0x89ABCDEF,
            [0x9D0369C6, 0xF6721667, 0x2B3F658B, 0xE5D86468],
        ),
    ]
    with rxf_native.Memory(1 << 16) as memory:
        for given, a, b, c, d, x, y, expected in cases:
            memory.write(0x200, struct.pack("<16I", *given))
            status, _ = call(memory, card, "blake2s::g", (memory.at(0x200), a, b, c, d, x, y))
            assert status == 0
            out = struct.unpack("<16I", memory.read(0x200, 64))
            assert [out[a], out[b], out[c], out[d]] == expected, (given, x, y)


def test_deinit_answers_its_i32_zero(card: crypto_import.CardImport) -> None:
    # the one C ABI entry the subset takes: the teardown of the card's context,
    # which is a completion (the run's status 0) and the entry's i32 0
    with rxf_native.Memory(1 << 16) as memory:
        assert call(memory, card, "pm_crypto_deinit", ()) == (0, 0)


# ------------------------------------------------ (b) the differential pins ---


def test_the_native_seat_answers_the_kernel_families(
    native_card: crypto.Crypto,
) -> None:
    # the imported kernels are private module fns: the C ABI cannot name them
    # (the report's named gap says so BY NAME). What the SAME source answers on
    # the native seat is the families' published vectors -- the kernels' own
    # definitions rolled up -- and those are pinned here:
    # RFC 8439 §2.8.2 (quarter_round's family), RFC 7693 (g's), and the NIST
    # CAVP AES-GCM row (xtime's).
    sealed = native_card.aead_seal(
        crypto.CIPHER_CHACHA20_POLY1305,
        h("808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f"),
        h("070000004041424344454647"),
        h("50515253c0c1c2c3c4c5c6c7"),
        b"Ladies and Gentlemen of the class of '99: If I could offer you "
        b"only one tip for the future, sunscreen would be it.",
    )
    assert sealed == h(
        "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
        "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
        "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
        "3ff4def08e4b7a9de576d26586cec64b6116"
        "1ae10b594f09e26a7e902ecbd0600691"
    )
    assert native_card.blake2s(b"abc") == h(
        "508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982"
    )
    assert native_card.aead_seal(
        crypto.CIPHER_AES_256_GCM,
        h("31bdadd96698c204aa9ce1448ea94ae1fb4a9a0b3c9d773b51bb1822666b8f22"),
        h("0d18e06c7c725ac9e362e1ce"),
        b"",
        h("2db5168e932556f8089a0622981d017d"),
    ) == h("fa4362189661d163fcd6a56d8bf0405ad636ac1bbedd5cc3ee727dc2ab4a9489")


def test_the_deinit_face_agrees_on_both_seats(
    card: crypto_import.CardImport, native_card: crypto.Crypto
) -> None:
    # the one imported entry both seats can name: the words answer (0, 0) and
    # the native seat's same face (pm_crypto_deinit, through card.close())
    # completes on the same (empty) input set -- same source, both seats. The
    # close is the seat's own teardown, so this is the last use of the card.
    with rxf_native.Memory(1 << 16) as memory:
        assert call(memory, card, "pm_crypto_deinit", ()) == (0, 0)
    native_card.close()


def test_the_named_gaps_are_pinned(card: crypto_import.CardImport) -> None:
    # the refusal ledger is not the only named part: the kernels' missing
    # differential seat is a named gap of the report, and it stays pinned here
    assert len(crypto_import.NAMED_GAPS) == 1
    (gap,) = crypto_import.NAMED_GAPS
    for named in ("differential seat of the imported kernels", "quarter_round, xtime and g", "E0603", "E0753"):
        assert named in gap
    assert gap in crypto_import.report_json(card)


# ------------------------------------------ (c) the refusal ledger, by name ---


def test_every_refusal_is_named_and_pinned(card: crypto_import.CardImport) -> None:
    # every function the subset cannot take, with the construct that refused it,
    # pinned BY NAME -- the named gaps are pinned, not silent
    assert [
        (row.face, row.site, row.kind, _construct(row.refusal or "")) for row in card.refused()
    ] == REFUSAL_LEDGER


def test_the_constructs_are_named_by_the_subsets_own_vocabulary(
    card: crypto_import.CardImport,
) -> None:
    for row in card.refused():
        assert row.refusal is not None
        named = [needle for needle in CONSTRUCT_NEEDLES if needle in row.refusal]
        assert len(named) == 1, (row.face, row.refusal)
        assert _construct(row.refusal) in CONSTRUCTS


def _construct(refusal: str) -> str:
    """The construct a refusal names, by the subset's own phrases."""
    for needle, construct in zip(CONSTRUCT_NEEDLES, CONSTRUCTS):
        if needle in refusal:
            return construct
    raise AssertionError(f"the refusal names no known construct: {refusal}")


# ------------------------------------------------------- the tool's own faces -


def test_the_tool_reports_and_emits_deterministically(
    card: crypto_import.CardImport, tmp_path: Path
) -> None:
    text = crypto_import.report_text(card)
    assert "pm_crypto_deinit" in text and "imported" in text
    assert "refused (policy)" in text and "refused (gap)" in text
    assert "named gaps:" in text

    again = crypto_import.import_card()
    assert again.ir_sha256 == card.ir_sha256
    assert again.reports == card.reports
    assert again.words == card.words
    assert crypto_import.report_json(again) == crypto_import.report_json(card)

    written = crypto_import.write_words(card, tmp_path)
    assert [path.name for path in written] == sorted(
        f"{crypto_import.MODULE}.{name}.words" for name in card.words
    )
    for path in written:
        name = path.name[len(crypto_import.MODULE) + 1 : -len(".words")]
        assert path.read_bytes() == card.words[name]
