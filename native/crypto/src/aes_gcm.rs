//! AES-256-GCM (NIST SP 800-38D), cipher id 1 — the TLS suite AEAD.
//!
//! AES-256 encryption only (GCM never decrypts with the block cipher), GHASH
//! over GF(2^128), and the GCM construction. The nonce is the card's fixed
//! 12 bytes, so J0 = nonce || 0x00000001 (SP 800-38D §7.1).
//!
//! Timing, honestly: the arithmetic (GHASH, GCTR) is branch-free and
//! table-free — the GF(2^128) multiply runs a fixed 128 iterations with mask
//! selection, and the tag compare XORs all bytes. AES itself uses the standard
//! 256-byte S-box table, and the table index is a secret-dependent byte, so
//! cache timing can leak round-state bits: this module is NOT claimed
//! constant-time. What is done: no secret-dependent branches anywhere, and the
//! S-box is the single known table-lookup caveat (documented in the header).

pub const KEY_LEN: usize = 32;
pub const NONCE_LEN: usize = 12;
pub const TAG_LEN: usize = 16;

const SBOX: [u8; 256] = [
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
];

const RCON: [u8; 8] = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80];

/// AES-256: 14 rounds, 15 round keys of 16 bytes.
pub struct Aes256 {
    rk: [[u8; 16]; 15],
}

impl Aes256 {
    pub fn new(key: &[u8; KEY_LEN]) -> Self {
        // Key expansion (FIPS 197 §5.2): 60 words, Nk = 8, Nr = 14.
        let mut w = [[0u8; 4]; 60];
        w[..8].copy_from_slice(&{
            let mut k = [[0u8; 4]; 8];
            for i in 0..8 {
                k[i].copy_from_slice(&key[4 * i..4 * i + 4]);
            }
            k
        });
        for i in 8..60 {
            let mut temp = w[i - 1];
            if i % 8 == 0 {
                temp = [temp[1], temp[2], temp[3], temp[0]]; // RotWord
                for b in temp.iter_mut() {
                    *b = SBOX[*b as usize];
                }
                temp[0] ^= RCON[i / 8 - 1];
            } else if i % 8 == 4 {
                for b in temp.iter_mut() {
                    *b = SBOX[*b as usize];
                }
            }
            for j in 0..4 {
                w[i][j] = w[i - 8][j] ^ temp[j];
            }
        }
        let mut rk = [[0u8; 16]; 15];
        for r in 0..15 {
            for c in 0..4 {
                rk[r][4 * c..4 * c + 4].copy_from_slice(&w[4 * r + c]);
            }
        }
        Aes256 { rk }
    }

    /// One block, encryption direction (FIPS 197 §5.1).
    pub fn encrypt_block(&self, block: &mut [u8; 16]) {
        let mut s = *block;
        xor16(&mut s, &self.rk[0]);
        for round in 1..14 {
            for b in s.iter_mut() {
                *b = SBOX[*b as usize];
            }
            shift_rows(&mut s);
            mix_columns(&mut s);
            xor16(&mut s, &self.rk[round]);
        }
        for b in s.iter_mut() {
            *b = SBOX[*b as usize];
        }
        shift_rows(&mut s);
        xor16(&mut s, &self.rk[14]);
        *block = s;
    }
}

fn xor16(a: &mut [u8; 16], b: &[u8; 16]) {
    for i in 0..16 {
        a[i] ^= b[i];
    }
}

fn shift_rows(s: &mut [u8; 16]) {
    let t = *s;
    for r in 1..4 {
        for c in 0..4 {
            s[4 * c + r] = t[4 * ((c + r) % 4) + r];
        }
    }
}

/// xtime: multiply by x in GF(2^8) — the reduction branch is masked, not taken.
fn xtime(a: u8) -> u8 {
    let hi = (a >> 7) & 1;
    (a << 1) ^ (0x1b & 0u8.wrapping_sub(hi))
}

fn mix_columns(s: &mut [u8; 16]) {
    for c in 0..4 {
        let (a0, a1, a2, a3) = (s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]);
        s[4 * c] = xtime(a0) ^ (xtime(a1) ^ a1) ^ a2 ^ a3;
        s[4 * c + 1] = a0 ^ xtime(a1) ^ (xtime(a2) ^ a2) ^ a3;
        s[4 * c + 2] = a0 ^ a1 ^ xtime(a2) ^ (xtime(a3) ^ a3);
        s[4 * c + 3] = (xtime(a0) ^ a0) ^ a1 ^ a2 ^ xtime(a3);
    }
}

/// GHASH multiplication in GF(2^128) with the GCM polynomial
/// x^128 + x^7 + x^2 + x + 1 (SP 800-38D §6.3). Fixed 128 shift-and-mask
/// iterations; no branches on the operands.
fn gf_mul(x: u128, y: u128) -> u128 {
    let mut z = 0u128;
    let mut v = y;
    for i in 0..128 {
        let bit = (x >> (127 - i)) & 1;
        let m = 0u128.wrapping_sub(bit);
        z ^= v & m;
        let lsb = v & 1;
        let rm = 0u128.wrapping_sub(lsb);
        v = (v >> 1) ^ (0xe100_0000_0000_0000_0000_0000_0000_0000u128 & rm);
    }
    z
}

fn to_u128(b: &[u8]) -> u128 {
    let mut w = [0u8; 16];
    w.copy_from_slice(b);
    u128::from_be_bytes(w)
}

fn from_u128(v: u128) -> [u8; 16] {
    v.to_be_bytes()
}

/// GHASH over aad and ct with the length block (SP 800-38D §6.4/§7.1).
fn ghash(h: u128, aad: &[u8], ct: &[u8]) -> u128 {
    let mut y = 0u128;
    for part in [aad, ct] {
        for chunk in part.chunks(16) {
            let mut block = [0u8; 16];
            block[..chunk.len()].copy_from_slice(chunk);
            y = gf_mul(h, y ^ to_u128(&block));
        }
    }
    let mut lens = [0u8; 16];
    lens[..8].copy_from_slice(&((aad.len() as u64) * 8).to_be_bytes());
    lens[8..].copy_from_slice(&((ct.len() as u64) * 8).to_be_bytes());
    gf_mul(h, y ^ to_u128(&lens))
}

/// GCTR: XOR `buf` with E_k over the counter blocks from `ctr`.
fn gctr(aes: &Aes256, mut ctr: [u8; 16], buf: &mut [u8]) {
    for chunk in buf.chunks_mut(16) {
        let mut ks = ctr;
        aes.encrypt_block(&mut ks);
        for (b, k) in chunk.iter_mut().zip(ks.iter()) {
            *b ^= k;
        }
        // 32-bit big-endian counter at the tail (SP 800-38D §7.2).
        for i in (12..16).rev() {
            ctr[i] = ctr[i].wrapping_add(1);
            if ctr[i] != 0 {
                break;
            }
        }
    }
}

fn inc32(block: &mut [u8; 16]) {
    for i in (12..16).rev() {
        block[i] = block[i].wrapping_add(1);
        if block[i] != 0 {
            break;
        }
    }
}

/// The J0 counter block for a 12-byte nonce (SP 800-38D §7.1).
fn j0(nonce: &[u8; NONCE_LEN]) -> [u8; 16] {
    let mut j = [0u8; 16];
    j[..12].copy_from_slice(nonce);
    j[15] = 1;
    j
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
    let aes = Aes256::new(key);
    let h = {
        let mut zero = [0u8; 16];
        aes.encrypt_block(&mut zero);
        to_u128(&zero)
    };
    let j = j0(nonce);
    let mut ctr = j;
    inc32(&mut ctr);
    out[..pt.len()].copy_from_slice(pt);
    gctr(&aes, ctr, &mut out[..pt.len()]);
    let s = ghash(h, aad, &out[..pt.len()]);
    let mut ej = j;
    aes.encrypt_block(&mut ej);
    let tag = from_u128(to_u128(&ej) ^ s);
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
    let aes = Aes256::new(key);
    let h = {
        let mut zero = [0u8; 16];
        aes.encrypt_block(&mut zero);
        to_u128(&zero)
    };
    let j = j0(nonce);
    let body = &ct[..ct.len() - TAG_LEN];
    let tag = &ct[ct.len() - TAG_LEN..];
    let s = ghash(h, aad, body);
    let mut ej = j;
    aes.encrypt_block(&mut ej);
    let expect = from_u128(to_u128(&ej) ^ s);
    // Constant-time compare.
    let mut d = 0u8;
    for (x, y) in expect.iter().zip(tag.iter()) {
        d |= x ^ y;
    }
    if d != 0 {
        return false;
    }
    assert!(out.len() >= body.len());
    out[..body.len()].copy_from_slice(body);
    let mut ctr = j;
    inc32(&mut ctr);
    gctr(&aes, ctr, &mut out[..body.len()]);
    true
}
