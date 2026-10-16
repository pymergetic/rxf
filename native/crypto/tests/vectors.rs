//! The cap.crypto card's one set of test vectors (docs/25 §11).
//!
//! Every vector here is pinned to a published source, cited in the comment at
//! the test. One exception is labeled as what it is: HKDF-BLAKE2s has no
//! published vectors, so its triple is generated once by this implementation
//! and pinned. If any vector disagrees with the code, the code is wrong.

use pm_crypto::aes_gcm;
use pm_crypto::blake2s::Blake2s;
use pm_crypto::chacha20poly1305::{self, chacha20_block, poly1305};
use pm_crypto::hkdf;
use pm_crypto::sha256::sha256;
use pm_crypto::x25519::{x25519, x25519_public};

fn h(s: &str) -> Vec<u8> {
    assert!(s.len() % 2 == 0);
    (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
        .collect()
}

fn a32(v: &[u8]) -> [u8; 32] {
    v.try_into().unwrap()
}

fn h16(s: &str) -> [u8; 16] {
    h(s).try_into().unwrap()
}

// ---------------------------------------------------------------- SHA-256 --

#[test]
fn sha256_vectors() {
    // Empty input: the NIST CAVP "SHA Test Vectors for Hashing" byte test
    // vectors, SHA-256 ShortMsg, Len = 0 (FIPS 180-4 itself publishes no
    // empty-input example).
    assert_eq!(
        sha256(b""),
        a32(&h("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"))
    );
    // FIPS 180-4 Appendix B.1: SHA-256("abc").
    assert_eq!(
        sha256(b"abc"),
        a32(&h("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"))
    );
    // FIPS 180-4 Appendix B.2: the 448-bit two-block message.
    assert_eq!(
        sha256(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
        a32(&h("248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"))
    );
}

// ---------------------------------------------------------------- BLAKE2s --

#[test]
fn blake2s_unkeyed_vectors() {
    // RFC 7693 Appendix B: BLAKE2s-256("abc"). (RFC 7693 publishes this one
    // BLAKE2s digest — its Appendix A is the BLAKE2b trace; the keyed vectors
    // are the Appendix E self-test below.)
    assert_eq!(
        pm_crypto::blake2s::blake2s(b"abc"),
        a32(&h("508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982"))
    );
}

#[test]
fn blake2s_keyed_vectors() {
    // Keyed BLAKE2s-256 direct triples. No keyed triple is published in
    // RFC 7693; these values were produced by CPython's hashlib.blake2s(key=…)
    // — an independent implementation — and pinned here. The published keyed
    // coverage is the RFC 7693 Appendix E self-test below.
    assert_eq!(
        pm_crypto::blake2s::blake2s_keyed(&h("0001020304050607"), b"abc"),
        a32(&h("5cbb58e3b3c8a5b9cb8f209c2cb1f0f11d19bb4fe534c5c3baecd9d861a6aadd"))
    );
    assert_eq!(
        pm_crypto::blake2s::blake2s_keyed(
            &h("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"),
            b""
        ),
        a32(&h("48a8997da407876b3d79c0d92325ad3b89cbb754d86ab71aee047ad345fd2c49"))
    );
}

/// The deterministic sequence generator of RFC 7693 Appendix E.
fn selftest_seq(out: &mut [u8], seed: u32) {
    let mut a = 0xDEAD4BADu32.wrapping_mul(seed);
    let mut b = 1u32;
    for byte in out.iter_mut() {
        let t = a.wrapping_add(b);
        a = b;
        b = t;
        *byte = (t >> 24) as u8;
    }
}

#[test]
fn blake2s_rfc7693_appendix_e_selftest() {
    // RFC 7693 Appendix E (the BLAKE2s self-test module): the grand hash of
    // hash results over unkeyed AND keyed BLAKE2s of the deterministic
    // sequences. This is the published keyed BLAKE2s vector — it exercises
    // digest lengths 16/20/28/32 (the ABI exposes only the 256-bit face).
    const BLAKE2S_RES: [u8; 32] = [
        0x6A, 0x41, 0x1F, 0x08, 0xCE, 0x25, 0xAD, 0xCD, 0xFB, 0x02, 0xAB, 0xA6, 0x41, 0x45,
        0x1C, 0xEC, 0x53, 0xC5, 0x98, 0xB2, 0x4F, 0x4F, 0xC7, 0x87, 0xFB, 0xDC, 0x88, 0x79,
        0x7F, 0x4C, 0x1D, 0xFE,
    ];
    let md_lens = [16usize, 20, 28, 32];
    let in_lens = [0usize, 3, 64, 65, 255, 1024];
    let mut grand = Blake2s::new(32, &[]);
    let mut input = [0u8; 1024];
    let mut key = [0u8; 32];
    let mut md = [0u8; 32];
    for outlen in md_lens {
        for inlen in in_lens {
            selftest_seq(&mut input[..inlen], inlen as u32);
            let mut hashed = Blake2s::new(outlen, &[]);
            hashed.update(&input[..inlen]);
            hashed.finalize(&mut md[..outlen]);
            grand.update(&md[..outlen]);

            selftest_seq(&mut key[..outlen], outlen as u32);
            let mut hashed = Blake2s::new(outlen, &key[..outlen]);
            hashed.update(&input[..inlen]);
            hashed.finalize(&mut md[..outlen]);
            grand.update(&md[..outlen]);
        }
    }
    let mut out = [0u8; 32];
    grand.finalize(&mut out);
    assert_eq!(out, BLAKE2S_RES);
}

// ----------------------------------------------------------------- X25519 --

#[test]
fn x25519_rfc7748_section_5_2() {
    // RFC 7748 §5.2, X25519: two scalar/u/output triples.
    let cases = [
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
    ];
    for (scalar, point, out) in cases {
        assert_eq!(x25519(&a32(&h(scalar)), &a32(&h(point))), a32(&h(out)));
    }
}

#[test]
fn x25519_diffie_hellman_rfc7748_section_6_1() {
    // RFC 7748 §6.1, Curve25519: Alice, Bob, and the shared secret.
    let alice = a32(&h(
        "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a",
    ));
    let bob = a32(&h(
        "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb",
    ));
    let alice_pub = a32(&h(
        "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a",
    ));
    let bob_pub = a32(&h(
        "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f",
    ));
    let shared = a32(&h(
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742",
    ));
    assert_eq!(x25519_public(&alice), alice_pub);
    assert_eq!(x25519_public(&bob), bob_pub);
    assert_eq!(x25519(&alice, &bob_pub), shared);
    assert_eq!(x25519(&bob, &alice_pub), shared);
}

#[test]
fn x25519_iterated_rfc7748_section_5_2() {
    // RFC 7748 §5.2, the second test type ("iterated"): start with k and u
    // both 09||00…00; one iteration sets k = X25519(k, u) — the 1-time value.
    let k = a32(&h(
        "0900000000000000000000000000000000000000000000000000000000000000",
    ));
    assert_eq!(
        x25519(&k, &k),
        a32(&h("422c8e7a6227d7bca1350b3e2bb7279f7897b87bb6854b783c60e80311ae3079"))
    );
}

// --------------------------------------------- ChaCha20 / Poly1305 (inner) --

#[test]
fn chacha20_inner_rfc8439_section_2_8_2() {
    // RFC 8439 §2.8.2, the AEAD example's inner cipher: with the §2.8.2 key
    // and nonce, ChaCha20 block counter 0 gives the one-time Poly1305 key,
    // and counter 1 the first keystream block ("keystream bytes").
    let key = a32(&h(
        "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f",
    ));
    let nonce: [u8; 12] = h("070000004041424344454647").try_into().unwrap();
    assert_eq!(
        &chacha20_block(&key, &nonce, 0)[..32],
        &h("7bac2b252db447af09b67a55a4e955840ae1d6731075d9eb2a9375783ed553ff")[..]
    );
    assert_eq!(
        &chacha20_block(&key, &nonce, 1)[..32],
        &h("9f7be95d01fd40ba15e28ffb36810aaec1c0883f09016ededd8ad087558203a5")[..]
    );
    // RFC 8439 §A.4 ("Poly1305 Key Generation Using ChaCha20") and the same
    // step inside §A.5: with the §A.5 key and nonce, block counter 0 gives
    // that example's one-time Poly1305 key.
    let key = a32(&h(
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
    ));
    let nonce: [u8; 12] = h("000000000102030405060708").try_into().unwrap();
    assert_eq!(
        &chacha20_block(&key, &nonce, 0)[..32],
        &h("bdf04aa95ce4de8995b14bb6a18fecaf26478f50c054f563dbc0a21e261572aa")[..]
    );
}

#[test]
fn poly1305_rfc8439_section_2_5_2() {
    // RFC 8439 §2.5.2, "Poly1305 Example and Test Vector": the RFC's
    // canonical Poly1305 example (the one-time key and message).
    let key = a32(&h(
        "85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b",
    ));
    assert_eq!(
        poly1305(&key, b"Cryptographic Forum Research Group"),
        h16("a8061dc1305136c6c22b8baf0c0127a9")
    );
}

#[test]
fn poly1305_rfc8439_appendix_a_3() {
    // RFC 8439 §A.3, Poly1305 MAC test vectors #1, #4, #5, #6, #7.
    // #1: the zero key and 64 zero bytes.
    assert_eq!(
        poly1305(&[0u8; 32], &[0u8; 64]),
        h16("00000000000000000000000000000000")
    );
    // #4: key over the "Jabberwocky" input (127 bytes).
    let key = a32(&h(
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
    ));
    let text = b"'Twas brillig, and the slithy toves\nDid gyre and gimble in the wabe:\nAll mimsy were the borogoves,\nAnd the mome raths outgrabe.";
    assert_eq!(
        poly1305(&key, text),
        h16("4541669a7eaaee61e708dc7cbcc5eb62")
    );
    // #5: the 130-bit partial reduction case.
    let key = a32(&h(
        "0200000000000000000000000000000000000000000000000000000000000000",
    ));
    assert_eq!(
        poly1305(&key, &h("ffffffffffffffffffffffffffffffff")),
        h16("03000000000000000000000000000000")
    );
    // #6: addition of s overflowing mod 2^128.
    let key = a32(&h(
        "02000000000000000000000000000000ffffffffffffffffffffffffffffffff",
    ));
    assert_eq!(
        poly1305(&key, &h("02000000000000000000000000000000")),
        h16("03000000000000000000000000000000")
    );
    // #7: a data limb of all ones with carry from the lower limb.
    let key = a32(&h(
        "0100000000000000000000000000000000000000000000000000000000000000",
    ));
    let msg = h(
        "ffffffffffffffffffffffffffffffff\
         f0ffffffffffffffffffffffffffffff\
         11000000000000000000000000000000",
    );
    assert_eq!(poly1305(&key, &msg), h16("05000000000000000000000000000000"));
    // #8: polynomial part exactly 2^130-5 (the modulus itself).
    let key = a32(&h(
        "0100000000000000000000000000000000000000000000000000000000000000",
    ));
    let msg = h(
        "ffffffffffffffffffffffffffffffff\
         fbfefefefefefefefefefefefefefefe\
         01010101010101010101010101010101",
    );
    assert_eq!(poly1305(&key, &msg), h16("00000000000000000000000000000000"));
    // #9: polynomial part exactly 2^130-6.
    let key = a32(&h(
        "0200000000000000000000000000000000000000000000000000000000000000",
    ));
    assert_eq!(
        poly1305(&key, &h("fdffffffffffffffffffffffffffffff")),
        h16("faffffffffffffffffffffffffffffff")
    );
    // #10: 5*H+L-style reduction with a 131-bit intermediate.
    let key = a32(&h(
        "0100000000000000040000000000000000000000000000000000000000000000",
    ));
    let msg = h(
        "e33594d7505e43b90000000000000000\
         3394d7505e4379cd0100000000000000\
         00000000000000000000000000000000\
         01000000000000000000000000000000",
    );
    assert_eq!(
        poly1305(&key, &msg),
        h16("14000000000000005500000000000000")
    );
    // #11: 5*H+L-style reduction with a 131-bit final result.
    let msg = h(
        "e33594d7505e43b90000000000000000\
         3394d7505e4379cd0100000000000000\
         00000000000000000000000000000000",
    );
    assert_eq!(
        poly1305(&key, &msg),
        h16("13000000000000000000000000000000")
    );
}

// ----------------------------------------------------------- AEAD vectors --

const AAD_2_8_2: &str = "50515253c0c1c2c3c4c5c6c7";
const KEY_2_8_2: &str = "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f";
const NONCE_2_8_2: &str = "070000004041424344454647";
const PT_2_8_2: &[u8] = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.";
const CT_2_8_2: &str = "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d63dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b3692ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc3ff4def08e4b7a9de576d26586cec64b6116";
const TAG_2_8_2: &str = "1ae10b594f09e26a7e902ecbd0600691";

#[test]
fn aead_chacha20poly1305_rfc8439_section_2_8_2() {
    // RFC 8439 §2.8.2, "Example and Test Vector for AEAD_CHACHA20_POLY1305":
    // one of the RFC's two AEAD examples (the other is §A.5 below).
    let key = a32(&h(KEY_2_8_2));
    let nonce: [u8; 12] = h(NONCE_2_8_2).try_into().unwrap();
    let aad = h(AAD_2_8_2);
    let mut sealed = vec![0u8; PT_2_8_2.len() + 16];
    let n = chacha20poly1305::seal(&key, &nonce, &aad, PT_2_8_2, &mut sealed);
    assert_eq!(n, PT_2_8_2.len() + 16);
    assert_eq!(&sealed[..PT_2_8_2.len()], &h(CT_2_8_2)[..]);
    assert_eq!(&sealed[PT_2_8_2.len()..], &h(TAG_2_8_2)[..]);
    let mut opened = vec![0u8; PT_2_8_2.len()];
    assert!(chacha20poly1305::open(
        &key, &nonce, &aad, &sealed, &mut opened
    ));
    assert_eq!(opened, PT_2_8_2);
}

#[test]
fn aead_chacha20poly1305_rfc8439_appendix_a_5() {
    // RFC 8439 §A.5, "ChaCha20-Poly1305 AEAD Decryption" — the RFC's second
    // AEAD example (the first is §2.8.2 above).
    let key = a32(&h(
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
    ));
    let nonce: [u8; 12] = h("000000000102030405060708").try_into().unwrap();
    let aad = h("f33388860000000000004e91");
    let ct = h("64a0861575861af460f062c79be643bd5e805cfd345cf389f108670ac76c8cb2\
4c6cfc18755d43eea09ee94e382d26b0bdb7b73c321b0100d4f03b7f355894cf\
332f830e710b97ce98c8a84abd0b948114ad176e008d33bd60f982b1ff37c855\
9797a06ef4f0ef61c186324e2b3506383606907b6a7c02b0f9f6157b53c867e4\
b9166c767b804d46a59b5216cde7a4e99040c5a40433225ee282a1b0a06c523e\
af4534d7f83fa1155b0047718cbc546a0d072b04b3564eea1b422273f548271a\
0bb2316053fa76991955ebd63159434ecebb4e466dae5a1073a6727627097a10\
49e617d91d361094fa68f0ff77987130305beaba2eda04df997b714d6c6f2c29\
a6ad5cb4022b02709b");
    let tag = h("eead9d67890cbb22392336fea1851f38");
    // The §A.5 plaintext exactly as the RFC's hex dump gives it (offsets
    // 240..270): 2f e2 80 9c … ress. 2f e2 80 9d — i.e. each U+201C/U+201D
    // smart quote is preceded by a literal '/'.
    let pt = "Internet-Drafts are draft documents valid for a maximum of six months and may be updated, replaced, or obsoleted by other documents at any time. It is inappropriate to use Internet-Drafts as reference material or to cite them other than as /\u{201c}work in progress./\u{201d}";
    let mut sealed = ct.clone();
    sealed.extend_from_slice(&tag);
    let mut opened = vec![0u8; ct.len()];
    assert!(chacha20poly1305::open(
        &key, &nonce, &aad, &sealed, &mut opened
    ));
    assert_eq!(opened, pt.as_bytes());
    // And the card's seal produces exactly this ciphertext and tag.
    let mut resealed = vec![0u8; pt.len() + 16];
    chacha20poly1305::seal(&key, &nonce, &aad, pt.as_bytes(), &mut resealed);
    assert_eq!(resealed, sealed);
}

#[test]
fn aead_chacha20poly1305_bad_tag_is_refused() {
    // A flipped tag byte must not open (the §2.8.2 vector, tag flipped).
    let key = a32(&h(KEY_2_8_2));
    let nonce: [u8; 12] = h(NONCE_2_8_2).try_into().unwrap();
    let aad = h(AAD_2_8_2);
    let mut sealed = h(CT_2_8_2);
    sealed.extend_from_slice(&h(TAG_2_8_2));
    let last = sealed.len() - 1;
    sealed[last] ^= 0x01;
    let mut opened = vec![0u8; PT_2_8_2.len()];
    assert!(!chacha20poly1305::open(
        &key, &nonce, &aad, &sealed, &mut opened
    ));
}

// ------------------------------------------------------ AES-256-GCM (CAVP) --

#[test]
fn aes_gcm_nist_cavp_gcmencryptextiv256_vectors() {
    // NIST CAVP AES-GCM Validation System (GCMVS), gcmEncryptExtIV256.rsp
    // ([Keylen=256][IVlen=96][Taglen=128] sections). The .rsp is served as a
    // 3 MB binary, so these records are quoted byte-for-byte from the
    // verbatim mirror in google/quiche
    // quiche/quic/core/crypto/aes_256_gcm_encrypter_test.cc, which cites
    // gcmEncryptExtIV256.rsp downloaded from the CAVP site (2017-09-27).
    //
    // [PTlen = 0][AADlen = 0], Count = 0 (seal output is just the tag):
    let key = a32(&h(
        "b52c505a37d78eda5dd34f20c22540ea1b58963cf8e5bf8ffa85f9f2492505b4",
    ));
    let nonce: [u8; 12] = h("516c33929df5a3284ff463d7").try_into().unwrap();
    let mut out = vec![0u8; 16];
    let n = aes_gcm::seal(&key, &nonce, b"", b"", &mut out);
    assert_eq!(n, 16);
    assert_eq!(out, h("bdc1ac884d332457a1d2664f168c76f0"));
    //
    // [PTlen = 128][AADlen = 0], Count = 0 (seal output = CT || Tag):
    let key = a32(&h(
        "31bdadd96698c204aa9ce1448ea94ae1fb4a9a0b3c9d773b51bb1822666b8f22",
    ));
    let nonce: [u8; 12] = h("0d18e06c7c725ac9e362e1ce").try_into().unwrap();
    let pt = h("2db5168e932556f8089a0622981d017d");
    let ct = h("fa4362189661d163fcd6a56d8bf0405a");
    let tag = h("d636ac1bbedd5cc3ee727dc2ab4a9489");
    let mut out = vec![0u8; 32];
    let n = aes_gcm::seal(&key, &nonce, b"", &pt, &mut out);
    assert_eq!(n, 32);
    assert_eq!(&out[..16], &ct[..]);
    assert_eq!(&out[16..], &tag[..]);
    // ... and open() turns CT || Tag back into the plaintext.
    let mut sealed = ct.clone();
    sealed.extend_from_slice(&tag);
    let mut opened = vec![0u8; 16];
    assert!(aes_gcm::open(&key, &nonce, b"", &sealed, &mut opened));
    assert_eq!(opened, pt);
}

#[test]
fn aes_gcm_nist_cavp_gcmdecrypt256_vectors() {
    // NIST CAVP AES-GCM Validation System (GCMVS), gcmDecrypt256.rsp, taken
    // from the full .rsp, [Keylen=256][IVlen=96][PTlen=128][AADlen=128]
    // [Taglen=128]:
    //
    // Count = 0 (tag verifies):
    let key = a32(&h(
        "54e352ea1d84bfe64a1011096111fbe7668ad2203d902a01458c3bbd85bfce14",
    ));
    let nonce: [u8; 12] = h("df7c3bca00396d0c018495d9").try_into().unwrap();
    let aad = h("7e968d71b50c1f11fd001f3fef49d045");
    let ct = h("426e0efc693b7be1f3018db7ddbb7e4d");
    let tag = h("ee8257795be6a1164d7e1d2d6cac77a7");
    let pt = h("85fc3dfad9b5a8d3258e4fc44571bd3b");
    let mut sealed = ct.clone();
    sealed.extend_from_slice(&tag);
    let mut out = vec![0u8; 16];
    assert!(aes_gcm::open(&key, &nonce, &aad, &sealed, &mut out));
    assert_eq!(out, pt);
    // Seal reproduces exactly this CT and Tag.
    let mut resealed = vec![0u8; 32];
    let n = aes_gcm::seal(&key, &nonce, &aad, &pt, &mut resealed);
    assert_eq!(n, 32);
    assert_eq!(resealed, sealed);
    //
    // Count = 2 of that section is a FAIL entry: the tag must not open.
    let key = a32(&h(
        "9a0343f850a6427120f764789ffec6d237447b898fbf51d2182f065d3861497d",
    ));
    let nonce: [u8; 12] = h("3deef6f453dd70d92143adcd").try_into().unwrap();
    let aad = h("dbb8226a624520863db6897017b2a4f8");
    let sealed =
        h("e93165935ac18e3a2845d15fe31a9286f5fc50d18766bc3d9e16dd136d45816b");
    let mut out = vec![0u8; 16];
    assert!(!aes_gcm::open(&key, &nonce, &aad, &sealed, &mut out));
    //
    // [Keylen=256][IVlen=96][PTlen=0][AADlen=0][Taglen=128], Count = 0
    // (empty plaintext, tag verifies) and Count = 1 (FAIL).
    let key = a32(&h(
        "f5a2b27c74355872eb3ef6c5feafaa740e6ae990d9d48c3bd9bb8235e589f010",
    ));
    let nonce: [u8; 12] = h("58d2240f580a31c1d24948e9").try_into().unwrap();
    let tag = h("15e051a5e4a5f5da6cea92e2ebee5bac");
    let mut out = vec![0u8; 0];
    assert!(aes_gcm::open(&key, &nonce, b"", &tag, &mut out));
    assert!(out.is_empty());
    let key = a32(&h(
        "e5a8123f2e2e007d4e379ba114a2fb66e6613f57c72d4e4f024964053028a831",
    ));
    let nonce: [u8; 12] = h("51e43385bf533e168427e1ad").try_into().unwrap();
    let tag = h("38fe845c66e66bdd884c2aecafd280e6");
    let mut out = vec![0u8; 0];
    assert!(!aes_gcm::open(&key, &nonce, b"", &tag, &mut out));
}

// -------------------------------------------------------------- HKDF --------

fn hkdf_case(hash: i32, ikm: &str, salt: &str, info: &str, prk: &str, okm: &str) {
    let ikm = h(ikm);
    let salt = h(salt);
    let info = h(info);
    assert_eq!(hkdf::extract(hash, &salt, &ikm), a32(&h(prk)));
    let expected = h(okm);
    let mut out = vec![0u8; expected.len()];
    let prk32 = a32(&h(prk));
    hkdf::expand(hash, &prk32, &info, &mut out).unwrap();
    assert_eq!(out, expected);
}

#[test]
fn hkdf_sha256_rfc5869_test_cases_1_to_3() {
    // RFC 5869 Appendix A, SHA-256 test cases 1, 2, 3 (extract + expand).
    hkdf_case(
        hkdf::HASH_SHA256,
        "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
        "000102030405060708090a0b0c",
        "f0f1f2f3f4f5f6f7f8f9",
        "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5",
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865",
    );
    hkdf_case(
        hkdf::HASH_SHA256,
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f\
202122232425262728292a2b2c2d2e2f303132333435363738393a3b3c3d3e3f\
404142434445464748494a4b4c4d4e4f",
        "606162636465666768696a6b6c6d6e6f707172737475767778797a7b7c7d7e7f\
808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f\
a0a1a2a3a4a5a6a7a8a9aaabacadaeaf",
        "b0b1b2b3b4b5b6b7b8b9babbbcbdbebfc0c1c2c3c4c5c6c7c8c9cacbcccdcecf\
d0d1d2d3d4d5d6d7d8d9dadbdcdddedfe0e1e2e3e4e5e6e7e8e9eaebecedeeef\
f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff",
        "06a6b88c5853361a06104c9ceb35b45cef760014904671014a193f40c15fc244",
        "b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c\
59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71\
cc30c58179ec3e87c14c01d5c1f3434f1d87",
    );
    hkdf_case(
        hkdf::HASH_SHA256,
        "0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b",
        "",
        "",
        "19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04",
        "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8",
    );
}

#[test]
fn hkdf_blake2s_pinned_triple() {
    // HKDF-BLAKE2s (this card's HMAC-BLAKE2s-256 instantiation of RFC 5869)
    // has NO published vectors: no RFC defines HKDF over BLAKE2s. This triple
    // was generated once by this implementation and pinned — honest
    // provenance, not an upstream claim.
    hkdf_case(
        hkdf::HASH_BLAKE2S,
        // Inputs are the ASCII strings "cap.crypto hkdf-blake2s pin: ikm",
        // "... pin: salt", "... pin: info" (the hex here is those bytes).
        "6361702e63727970746f20686b64662d626c616b6532732070696e3a20696b6d",
        "6361702e63727970746f20686b64662d626c616b6532732070696e3a2073616c74",
        "6361702e63727970746f20686b64662d626c616b6532732070696e3a20696e666f",
        "6c6fe5e799e3addd8506a1d8d29209c9d3067a6bbf24059ddb6877acb109023e",
        "076d127611325d03ea8c849fd471bb6e1ab73b435da3aef2909fc842c7544a29\
261f5b33e51978ba14cc",
    );
}
