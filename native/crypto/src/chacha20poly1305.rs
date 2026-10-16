//! ChaCha20-Poly1305 AEAD (RFC 8439), cipher id 0 — the WireGuard lane's AEAD
//! and the first TLS 1.3 suite's companion.
//!
//! The pieces are exposed inside the crate because the published vectors pin
//! them separately: `chacha20_block` (RFC 8439 §2.3/§2.8.2), `poly1305`
//! (§2.5/§A.3), and the AEAD construction (§2.8).
//!
//! Timing: ChaCha20 is arithmetic (add/xor/rotate) with no tables; Poly1305
//! multiplies and carries on fixed limb positions; the tag compare XORs all
//! bytes. No branches or lookups on key, nonce, plaintext, or tag bytes.

pub const KEY_LEN: usize = 32;
pub const NONCE_LEN: usize = 12;
pub const TAG_LEN: usize = 16;

fn quarter_round(s: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize) {
    s[a] = s[a].wrapping_add(s[b]);
    s[d] = (s[d] ^ s[a]).rotate_left(16);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_left(12);
    s[a] = s[a].wrapping_add(s[b]);
    s[d] = (s[d] ^ s[a]).rotate_left(8);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_left(7);
}

/// The ChaCha20 block function (RFC 8439 §2.3): 64 keystream bytes for a
/// 32-byte key, 12-byte nonce, and 32-bit block counter.
pub fn chacha20_block(key: &[u8; KEY_LEN], nonce: &[u8; NONCE_LEN], counter: u32) -> [u8; 64] {
    let mut s = [0u32; 16];
    s[0] = 0x61707865; // "expa"
    s[1] = 0x3320646e; // "nd 3"
    s[2] = 0x79622d32; // "2-by"
    s[3] = 0x6b206574; // "te k"
    for i in 0..8 {
        let mut w = [0u8; 4];
        w.copy_from_slice(&key[4 * i..4 * i + 4]);
        s[4 + i] = u32::from_le_bytes(w);
    }
    s[12] = counter;
    for i in 0..3 {
        let mut w = [0u8; 4];
        w.copy_from_slice(&nonce[4 * i..4 * i + 4]);
        s[13 + i] = u32::from_le_bytes(w);
    }
    let mut w = s;
    for _ in 0..10 {
        quarter_round(&mut w, 0, 4, 8, 12);
        quarter_round(&mut w, 1, 5, 9, 13);
        quarter_round(&mut w, 2, 6, 10, 14);
        quarter_round(&mut w, 3, 7, 11, 15);
        quarter_round(&mut w, 0, 5, 10, 15);
        quarter_round(&mut w, 1, 6, 11, 12);
        quarter_round(&mut w, 2, 7, 8, 13);
        quarter_round(&mut w, 3, 4, 9, 14);
    }
    let mut out = [0u8; 64];
    for i in 0..16 {
        out[4 * i..4 * i + 4].copy_from_slice(&w[i].wrapping_add(s[i]).to_le_bytes());
    }
    out
}

/// XOR `buf` with the ChaCha20 keystream from `counter` on (RFC 8439 §2.4).
pub fn chacha20_apply(key: &[u8; KEY_LEN], nonce: &[u8; NONCE_LEN], counter: u32, buf: &mut [u8]) {
    let mut counter = counter;
    for chunk in buf.chunks_mut(64) {
        let block = chacha20_block(key, nonce, counter);
        counter = counter.wrapping_add(1);
        for (b, k) in chunk.iter_mut().zip(block.iter()) {
            *b ^= k;
        }
    }
}

fn u32le(b: &[u8]) -> u32 {
    u32::from_le_bytes(b.try_into().unwrap())
}

/// Poly1305 (RFC 8439 §2.5): 16-byte tag under a 32-byte one-time key.
/// The accumulator is five 26-bit limbs (as the reference `poly1305-donna`).
pub fn poly1305(key: &[u8; 32], msg: &[u8]) -> [u8; TAG_LEN] {
    // r &= 0x0ffffffc0ffffffc0ffffffc0fffffff (little-endian), in 26-bit limbs.
    let r = [
        u32le(&key[0..4]) & 0x03ff_ffff,
        (u32le(&key[3..7]) >> 2) & 0x03ff_ff03,
        (u32le(&key[6..10]) >> 4) & 0x03ff_c0ff,
        (u32le(&key[9..13]) >> 6) & 0x03f0_3fff,
        (u32le(&key[12..16]) >> 8) & 0x000f_ffff,
    ];
    let s = [
        u32le(&key[16..20]),
        u32le(&key[20..24]),
        u32le(&key[24..28]),
        u32le(&key[28..32]),
    ];
    let r0 = r[0] as u64;
    let (r1, r2, r3, r4) = (r[1] as u64, r[2] as u64, r[3] as u64, r[4] as u64);
    let (s1, s2, s3, s4) = (5 * r1, 5 * r2, 5 * r3, 5 * r4);

    let mask: u64 = 0x03ff_ffff;
    let mut h = [0u64; 5];
    let mut data = msg;
    while !data.is_empty() {
        let n = data.len().min(16);
        // The block as a 130-bit little-endian number: n message bytes, then 1.
        let mut block = [0u8; 17];
        block[..n].copy_from_slice(&data[..n]);
        block[n] = 1;
        data = &data[n..];

        h[0] += (u32le(&block[0..4]) as u64) & mask;
        h[1] += ((u32le(&block[3..7]) as u64) >> 2) & mask;
        h[2] += ((u32le(&block[6..10]) as u64) >> 4) & mask;
        h[3] += ((u32le(&block[9..13]) as u64) >> 6) & mask;
        h[4] += ((u32le(&block[12..16]) as u64) >> 8) | ((block[16] as u64) << 24);

        // h *= r (limbs are < 2^27 here; the products fit u64)
        let (h0, h1, h2, h3, h4) = (h[0], h[1], h[2], h[3], h[4]);
        let d0 = h0 * r0 + h1 * s4 + h2 * s3 + h3 * s2 + h4 * s1;
        let d1 = h0 * r1 + h1 * r0 + h2 * s4 + h3 * s3 + h4 * s2;
        let d2 = h0 * r2 + h1 * r1 + h2 * r0 + h3 * s4 + h4 * s3;
        let d3 = h0 * r3 + h1 * r2 + h2 * r1 + h3 * r0 + h4 * s4;
        let d4 = h0 * r4 + h1 * r3 + h2 * r2 + h3 * r1 + h4 * r0;

        let mut c = d0 >> 26;
        h[0] = d0 & mask;
        let d1 = d1 + c;
        c = d1 >> 26;
        h[1] = d1 & mask;
        let d2 = d2 + c;
        c = d2 >> 26;
        h[2] = d2 & mask;
        let d3 = d3 + c;
        c = d3 >> 26;
        h[3] = d3 & mask;
        let d4 = d4 + c;
        c = d4 >> 26;
        h[4] = d4 & mask;
        h[0] += c * 5;
        c = h[0] >> 26;
        h[0] &= mask;
        h[1] += c;
    }

    // Fully carry (the 2^130 ≡ 5 fold keeps the value congruent mod p).
    let mut c = h[1] >> 26;
    h[1] &= mask;
    h[2] += c;
    c = h[2] >> 26;
    h[2] &= mask;
    h[3] += c;
    c = h[3] >> 26;
    h[3] &= mask;
    h[4] += c;
    c = h[4] >> 26;
    h[4] &= mask;
    h[0] += c * 5;
    c = h[0] >> 26;
    h[0] &= mask;
    h[1] += c;

    // Select the representative in [0, p): use h + 5 - 2^130 when h >= p.
    // The mask is computed from the wrap bit, not from a branch.
    let mut g = [0u64; 5];
    g[0] = h[0] + 5;
    let mut c = g[0] >> 26;
    g[0] &= mask;
    for i in 1..4 {
        g[i] = h[i] + c;
        c = g[i] >> 26;
        g[i] &= mask;
    }
    g[4] = h[4].wrapping_add(c).wrapping_sub(1 << 26);
    let keep = (g[4] >> 63).wrapping_sub(1); // all ones when h < p (g wrapped)
    for i in 0..5 {
        h[i] = (h[i] & !keep) | (g[i] & keep);
    }

    // Exact digit normalization (value unchanged): every limb < 2^26.
    for _ in 0..3 {
        for i in 0..4 {
            let c = h[i] >> 26;
            h[i] &= mask;
            h[i + 1] += c;
        }
    }

    // tag = (h + s) mod 2^128, little-endian.
    let v = (h[0] as u128)
        .wrapping_add((h[1] as u128) << 26)
        .wrapping_add((h[2] as u128) << 52)
        .wrapping_add((h[3] as u128) << 78)
        .wrapping_add((h[4] as u128) << 104);
    let sv = (s[0] as u128)
        | ((s[1] as u128) << 32)
        | ((s[2] as u128) << 64)
        | ((s[3] as u128) << 96);
    v.wrapping_add(sv).to_le_bytes()[..TAG_LEN].try_into().unwrap()
}

fn poly_key(key: &[u8; KEY_LEN], nonce: &[u8; NONCE_LEN]) -> [u8; 32] {
    // RFC 8439 §2.6: the first 32 bytes of ChaCha20 block counter 0.
    chacha20_block(key, nonce, 0)[..32].try_into().unwrap()
}

/// The authenticated tag over aad and ct (RFC 8439 §2.8).
fn aead_tag(key: &[u8; KEY_LEN], nonce: &[u8; NONCE_LEN], aad: &[u8], ct: &[u8]) -> [u8; TAG_LEN] {
    let poly_key = poly_key(key, nonce);
    let mut mac = Vec::with_capacity(aad.len() + ct.len() + 32);
    mac.extend_from_slice(aad);
    mac.extend_from_slice(&[0; 16][..((16 - aad.len() % 16) % 16)]);
    mac.extend_from_slice(ct);
    mac.extend_from_slice(&[0; 16][..((16 - ct.len() % 16) % 16)]);
    mac.extend_from_slice(&(aad.len() as u64).to_le_bytes());
    mac.extend_from_slice(&(ct.len() as u64).to_le_bytes());
    poly1305(&poly_key, &mac)
}

/// Constant-time 16-byte compare: 1 if equal.
fn ct_eq(a: &[u8], b: &[u8]) -> u8 {
    let mut d = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        d |= x ^ y;
    }
    (d == 0) as u8
}

/// Seal: writes ciphertext || 16-byte tag into `out`, returns pt_len + 16.
/// `out` must hold at least pt_len + 16 bytes (the C ABI contract).
pub fn seal(
    key: &[u8; KEY_LEN],
    nonce: &[u8; NONCE_LEN],
    aad: &[u8],
    pt: &[u8],
    out: &mut [u8],
) -> usize {
    assert!(out.len() >= pt.len() + TAG_LEN);
    out[..pt.len()].copy_from_slice(pt);
    chacha20_apply(key, nonce, 1, &mut out[..pt.len()]);
    let tag = aead_tag(key, nonce, aad, &out[..pt.len()]);
    out[pt.len()..pt.len() + TAG_LEN].copy_from_slice(&tag);
    pt.len() + TAG_LEN
}

/// Open: verifies the trailing tag of `ct` and writes ct_len - 16 plaintext
/// bytes into `out`. False (nothing trusted) on a bad tag.
pub fn open(
    key: &[u8; KEY_LEN],
    nonce: &[u8; NONCE_LEN],
    aad: &[u8],
    ct: &[u8],
    out: &mut [u8],
) -> bool {
    if ct.len() < TAG_LEN {
        return false;
    }
    let body = &ct[..ct.len() - TAG_LEN];
    let tag = &ct[ct.len() - TAG_LEN..];
    let expect = aead_tag(key, nonce, aad, body);
    if ct_eq(&expect, tag) != 1 {
        return false;
    }
    assert!(out.len() >= body.len());
    out[..body.len()].copy_from_slice(body);
    chacha20_apply(key, nonce, 1, &mut out[..body.len()]);
    true
}
