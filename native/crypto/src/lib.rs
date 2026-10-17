//! pm_crypto — the cap.crypto card of the RXF net stack (docs/25 §11).
//!
//! One card, one defining lang (RS), one set of test vectors. This crate is the
//! card's only implementation of its primitives; `include/pm_crypto.h` is its
//! only face, and `src/pymergetic/rxftk/net/crypto.py` is a ctypes fill over that
//! face — there is no second implementation in any other language.
//!
//! Primitives:
//! - SHA-256 (FIPS 180-4) — `sha256`
//! - BLAKE2s-256, unkeyed and keyed (RFC 7693) — `blake2s`
//! - X25519 (RFC 7748) — `x25519`
//! - ChaCha20-Poly1305 AEAD (RFC 8439) — `chacha20poly1305`
//! - AES-256-GCM AEAD (NIST SP 800-38D) — `aes_gcm`
//! - HKDF over either hash (RFC 5869; see `hkdf` for the BLAKE2s note) — `hkdf`
//!
//! The C ABI below is frozen: later lanes code against these exact names and
//! signatures. 0 = ok, negative = refusal; there is no global state, and
//! `init`/`deinit` are the module pair.

pub mod aes_gcm;
pub mod blake2s;
pub mod chacha20poly1305;
pub mod hkdf;
pub mod sha256;
pub mod x25519;

/// Everything checked out; nothing to report.
pub const PM_OK: i32 = 0;
/// Bad argument: unknown cipher/hash id, null pointer, or bad fixed length.
pub const PM_ERR_ARGUMENT: i32 = -1;
/// Verification refused: the AEAD tag does not match.
pub const PM_ERR_TAG: i32 = -2;
/// X25519 refused: the output is all zeros (input point of small order).
pub const PM_ERR_LOW_ORDER: i32 = -3;
/// HKDF-Expand refused: out_len over the RFC 5869 limit (255 · 32).
pub const PM_ERR_LENGTH: i32 = -4;

/// Cipher id 0 — ChaCha20-Poly1305.
pub const PM_CIPHER_CHACHA20_POLY1305: i32 = 0;
/// Cipher id 1 — AES-256-GCM.
pub const PM_CIPHER_AES_256_GCM: i32 = 1;

/// Hash id 0 — SHA-256.
pub const PM_HASH_SHA256: i32 = 0;
/// Hash id 1 — BLAKE2s-256.
pub const PM_HASH_BLAKE2S: i32 = 1;

const TAG_LEN: usize = 16;

/// Slice from a pointer and length: null is accepted only for len 0.
unsafe fn slice<'a>(p: *const u8, len: usize) -> Option<&'a [u8]> {
    if len == 0 {
        return Some(&[]);
    }
    if p.is_null() {
        return None;
    }
    Some(core::slice::from_raw_parts(p, len))
}

unsafe fn out_slice<'a>(p: *mut u8, len: usize) -> Option<&'a mut [u8]> {
    if len == 0 {
        return Some(&mut []);
    }
    if p.is_null() {
        return None;
    }
    Some(core::slice::from_raw_parts_mut(p, len))
}

unsafe fn fixed<'a, const N: usize>(p: *const u8) -> Option<&'a [u8; N]> {
    if p.is_null() {
        return None;
    }
    Some(&*(p as *const [u8; N]))
}

/// Open the card. The primitives are stateless; this is the module pair's
/// open (never `fini` on the close side — see `pm_crypto_deinit`), and it
/// refuses if the card's own SHA-256 known answer does not hold.
#[no_mangle]
pub extern "C" fn pm_crypto_init() -> i32 {
    // FIPS 180-4 Appendix B.1: SHA-256("abc").
    let abc = b"abc";
    let expect: [u8; 32] = [
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40, 0xde, 0x5d, 0xae, 0x22,
        0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17, 0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00,
        0x15, 0xad,
    ];
    if sha256::sha256(abc) == expect {
        PM_OK
    } else {
        PM_ERR_ARGUMENT
    }
}

/// Close the card. No state to release; the pair exists for lanes that open
/// and close the card as a module.
#[no_mangle]
pub extern "C" fn pm_crypto_deinit() {}

/// SHA-256: writes 32 bytes to `out`. `data` may be null when len is 0.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_sha256(out: *mut u8, data: *const u8, len: usize) {
    let (Some(out), Some(data)) = (out_slice(out, 32), slice(data, len)) else {
        return;
    };
    out.copy_from_slice(&sha256::sha256(data));
}

/// BLAKE2s-256, unkeyed: writes 32 bytes to `out`.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_blake2s(out: *mut u8, data: *const u8, len: usize) {
    let (Some(out), Some(data)) = (out_slice(out, 32), slice(data, len)) else {
        return;
    };
    out.copy_from_slice(&blake2s::blake2s(data));
}

/// Keyed BLAKE2s (1..=32 byte key) at the caller's width: writes `out_len` bytes
/// to `out` (1..=32 — the WireGuard MAC form is 16; truncating the 32-byte digest
/// is not that value).
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_blake2s_keyed(
    out: *mut u8,
    out_len: usize,
    key: *const u8,
    key_len: usize,
    data: *const u8,
    len: usize,
) {
    if out_len == 0 || out_len > 32 {
        return;
    }
    let (Some(out), Some(key), Some(data)) =
        (out_slice(out, out_len), slice(key, key_len), slice(data, len))
    else {
        return;
    };
    if key.is_empty() || key.len() > 32 {
        return;
    }
    blake2s::blake2s_keyed_n(key, data, out);
}

/// X25519 public key: writes X25519(scalar, 9) to `out`.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_x25519_public(out: *mut u8, scalar: *const u8) {
    let (Some(out), Some(scalar)) = (out_slice(out, 32), fixed::<32>(scalar)) else {
        return;
    };
    out.copy_from_slice(&x25519::x25519_public(scalar));
}

/// X25519: writes the shared u-coordinate to `out`. Refuses (PM_ERR_LOW_ORDER)
/// when the output is all zeros — RFC 7748 §6, the small-order point check.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_x25519(
    out: *mut u8,
    scalar: *const u8,
    point: *const u8,
) -> i32 {
    let (Some(out), Some(scalar), Some(point)) =
        (out_slice(out, 32), fixed::<32>(scalar), fixed::<32>(point))
    else {
        return PM_ERR_ARGUMENT;
    };
    let shared = x25519::x25519(scalar, point);
    if shared.iter().fold(0u8, |a, b| a | b) == 0 {
        return PM_ERR_LOW_ORDER;
    }
    out.copy_from_slice(&shared);
    PM_OK
}

unsafe fn aead(
    cipher: i32,
    key: *const u8,
    nonce: *const u8,
    aad: *const u8,
    aad_len: usize,
    body: *const u8,
    body_len: usize,
    out: *mut u8,
    out_len: *mut usize,
    seal_not_open: bool,
) -> i32 {
    let (Some(key), Some(nonce)) = (fixed::<32>(key), fixed::<12>(nonce)) else {
        return PM_ERR_ARGUMENT;
    };
    let (Some(aad), Some(out_len)) = (slice(aad, aad_len), out_len.as_mut()) else {
        return PM_ERR_ARGUMENT;
    };
    let room = if seal_not_open {
        body_len.saturating_add(TAG_LEN)
    } else {
        body_len.saturating_sub(TAG_LEN)
    };
    let (Some(out), Some(body)) = (out_slice(out, room), slice(body, body_len)) else {
        return PM_ERR_ARGUMENT;
    };
    if !seal_not_open && body_len < TAG_LEN {
        return PM_ERR_ARGUMENT;
    }
    match (cipher, seal_not_open) {
        (PM_CIPHER_CHACHA20_POLY1305, true) => {
            *out_len = chacha20poly1305::seal(key, nonce, aad, body, out);
        }
        (PM_CIPHER_CHACHA20_POLY1305, false) => {
            if !chacha20poly1305::open(key, nonce, aad, body, out) {
                return PM_ERR_TAG;
            }
            *out_len = body_len - TAG_LEN;
        }
        (PM_CIPHER_AES_256_GCM, true) => {
            *out_len = aes_gcm::seal(key, nonce, aad, body, out);
        }
        (PM_CIPHER_AES_256_GCM, false) => {
            if !aes_gcm::open(key, nonce, aad, body, out) {
                return PM_ERR_TAG;
            }
            *out_len = body_len - TAG_LEN;
        }
        _ => return PM_ERR_ARGUMENT,
    }
    PM_OK
}

/// AEAD seal (either cipher id): writes pt || 16-byte tag to `out` and sets
/// `*out_len = pt_len + 16`. The caller's `out` holds at least that; `out_len`
/// is written on success (the produced length), per the card's ABI contract.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_aead_seal(
    cipher: i32,
    out: *mut u8,
    out_len: *mut usize,
    key: *const u8,
    nonce: *const u8,
    aad: *const u8,
    aad_len: usize,
    pt: *const u8,
    pt_len: usize,
) -> i32 {
    aead(
        cipher, key, nonce, aad, aad_len, pt, pt_len, out, out_len, true,
    )
}

/// AEAD open (either cipher id): verifies the trailing 16-byte tag of `ct`
/// and writes `ct_len - 16` plaintext bytes to `out`, setting `*out_len`.
/// Refuses (PM_ERR_TAG) on a bad tag; refuses (PM_ERR_ARGUMENT) when
/// ct_len < 16 or on any bad argument.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_aead_open(
    cipher: i32,
    out: *mut u8,
    out_len: *mut usize,
    key: *const u8,
    nonce: *const u8,
    aad: *const u8,
    aad_len: usize,
    ct: *const u8,
    ct_len: usize,
) -> i32 {
    aead(
        cipher, key, nonce, aad, aad_len, ct, ct_len, out, out_len, false,
    )
}

/// HKDF-Extract (RFC 5869 §2.2) under the hash id: writes 32 bytes to `out`.
/// A zero-length salt is the RFC's "absent salt" (HashLen zeros).
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_hkdf_extract(
    hash: i32,
    out: *mut u8,
    salt: *const u8,
    salt_len: usize,
    ikm: *const u8,
    ikm_len: usize,
) -> i32 {
    if !(PM_HASH_SHA256..=PM_HASH_BLAKE2S).contains(&hash) {
        return PM_ERR_ARGUMENT;
    }
    let (Some(out), Some(salt), Some(ikm)) =
        (out_slice(out, 32), slice(salt, salt_len), slice(ikm, ikm_len))
    else {
        return PM_ERR_ARGUMENT;
    };
    out.copy_from_slice(&hkdf::extract(hash, salt, ikm));
    PM_OK
}

/// HKDF-Expand (RFC 5869 §2.3) under the hash id: writes `out_len` bytes to
/// `out`. Refuses (PM_ERR_LENGTH) when out_len > 255 · 32.
#[no_mangle]
pub unsafe extern "C" fn pm_crypto_hkdf_expand(
    hash: i32,
    out: *mut u8,
    out_len: usize,
    prk: *const u8,
    info: *const u8,
    info_len: usize,
) -> i32 {
    if !(PM_HASH_SHA256..=PM_HASH_BLAKE2S).contains(&hash) {
        return PM_ERR_ARGUMENT;
    }
    let (Some(out), Some(info)) = (out_slice(out, out_len), slice(info, info_len)) else {
        return PM_ERR_ARGUMENT;
    };
    let Some(prk) = fixed::<32>(prk) else {
        return PM_ERR_ARGUMENT;
    };
    match hkdf::expand(hash, prk, info, out) {
        Ok(()) => PM_OK,
        Err(()) => PM_ERR_LENGTH,
    }
}
