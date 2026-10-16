//! HKDF (RFC 5869) over SHA-256 (hash id 0) and BLAKE2s-256 (hash id 1).
//!
//! HKDF-SHA256 is RFC 5869 as written. **HKDF-BLAKE2s is this card's own
//! instantiation**: RFC 5869's extract/expand with HMAC-BLAKE2s-256 as the PRF
//! (HMAC block size 64, output 32, exactly as RFC 2104 defines HMAC for any
//! hash). No RFC publishes this combination — its vectors in the test suites
//! are generated once by this implementation and pinned, and say so.
//!
//! Timing: HMAC is composed of the hash modules (see their notes); the extra
//! key/XOR work is on fixed-length blocks. No secret-dependent branches.

use crate::blake2s::{self, Blake2s};
use crate::sha256::{self, Sha256};

/// Hash id 0 — SHA-256.
pub const HASH_SHA256: i32 = 0;
/// Hash id 1 — BLAKE2s-256.
pub const HASH_BLAKE2S: i32 = 1;

pub const OUT_LEN: usize = 32;
/// RFC 5869 §2.3: L <= 255 · HashLen.
pub const MAX_EXPAND: usize = 255 * OUT_LEN;

/// A 32-byte digest under either card hash.
#[derive(Clone, Copy)]
pub enum Digest {
    Sha256([u8; 32]),
    Blake2s([u8; 32]),
}

impl Digest {
    pub fn bytes(&self) -> &[u8; 32] {
        match self {
            Digest::Sha256(b) => b,
            Digest::Blake2s(b) => b,
        }
    }
}

fn hash_of(id: i32, data: &[u8]) -> Digest {
    match id {
        HASH_SHA256 => Digest::Sha256(sha256::sha256(data)),
        HASH_BLAKE2S => Digest::Blake2s(blake2s::blake2s(data)),
        _ => unreachable!("caller checks the hash id"),
    }
}

/// HMAC (RFC 2104) under the card hash: block size 64 for both.
fn hmac(id: i32, key: &[u8], msg: &[u8]) -> Digest {
    const BLOCK: usize = 64;
    let mut k = [0u8; BLOCK];
    if key.len() > BLOCK {
        let d = hash_of(id, key);
        k[..32].copy_from_slice(d.bytes());
    } else {
        k[..key.len()].copy_from_slice(key);
    }
    let mut inner_key = [0x36u8; BLOCK];
    let mut outer_key = [0x5cu8; BLOCK];
    for i in 0..BLOCK {
        inner_key[i] ^= k[i];
        outer_key[i] ^= k[i];
    }
    let inner = match id {
        HASH_SHA256 => {
            let mut h = Sha256::new();
            h.update(&inner_key);
            h.update(msg);
            Digest::Sha256(h.finalize())
        }
        _ => {
            let mut h = Blake2s::new(32, &[]);
            h.update(&inner_key);
            h.update(msg);
            let mut out = [0u8; 32];
            h.finalize(&mut out);
            Digest::Blake2s(out)
        }
    };
    match id {
        HASH_SHA256 => {
            let mut h = Sha256::new();
            h.update(&outer_key);
            h.update(inner.bytes());
            Digest::Sha256(h.finalize())
        }
        _ => {
            let mut h = Blake2s::new(32, &[]);
            h.update(&outer_key);
            h.update(inner.bytes());
            let mut out = [0u8; 32];
            h.finalize(&mut out);
            Digest::Blake2s(out)
        }
    }
}

/// HKDF-Extract (RFC 5869 §2.2): PRK = HMAC-Hash(salt, IKM).
/// An absent or zero-length salt is HashLen zeros, as the RFC says.
pub fn extract(hash: i32, salt: &[u8], ikm: &[u8]) -> [u8; OUT_LEN] {
    let zeros = [0u8; OUT_LEN];
    let salt = if salt.is_empty() { &zeros[..] } else { salt };
    *hmac(hash, salt, ikm).bytes()
}

/// HKDF-Expand (RFC 5869 §2.3): T(i) = HMAC(PRK, T(i-1) || info || i).
/// Refuses (Err) when out.len() > 255 · 32.
pub fn expand(hash: i32, prk: &[u8; OUT_LEN], info: &[u8], out: &mut [u8]) -> Result<(), ()> {
    if out.len() > MAX_EXPAND {
        return Err(());
    }
    let mut t: [u8; 32] = [0; 32];
    let mut done = 0;
    let mut counter: u8 = 1;
    while done < out.len() {
        let d = if counter == 1 {
            // T(1) = HMAC(PRK, info || 0x01)
            let mut msg = Vec::with_capacity(info.len() + 1);
            msg.extend_from_slice(info);
            msg.push(1);
            hmac(hash, prk, &msg)
        } else {
            let mut msg = Vec::with_capacity(32 + info.len() + 1);
            msg.extend_from_slice(&t);
            msg.extend_from_slice(info);
            msg.push(counter);
            hmac(hash, prk, &msg)
        };
        t = *d.bytes();
        let n = (out.len() - done).min(32);
        out[done..done + n].copy_from_slice(&t[..n]);
        done += n;
        counter = counter.wrapping_add(1);
    }
    Ok(())
}
