//! BLAKE2s (RFC 7693), 256-bit output, unkeyed and keyed.
//!
//! The keyed form is the one the WireGuard lane needs (its MACs are keyed
//! BLAKE2s-256). Digest lengths 1..=32 exist in the core because the published
//! keyed vector (the RFC 7693 Appendix E self-test) hashes outputs of several
//! lengths; the C ABI exposes only the 256-bit face.
//!
//! Timing: the compression is arithmetic only — no table lookups, no branches
//! on message, key, or state bytes (the `last` flag and the byte counter are
//! public). Constant-time with respect to input and key.

const IV: [u32; 8] = [
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
];

const SIGMA: [[usize; 16]; 10] = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15],
    [14, 10, 4, 8, 9, 15, 13, 6, 1, 12, 0, 2, 11, 7, 5, 3],
    [11, 8, 12, 0, 5, 2, 15, 13, 10, 14, 3, 6, 7, 1, 9, 4],
    [7, 9, 3, 1, 13, 12, 11, 14, 2, 6, 5, 10, 4, 0, 15, 8],
    [9, 0, 5, 7, 2, 4, 10, 15, 14, 1, 11, 12, 6, 8, 3, 13],
    [2, 12, 6, 10, 0, 11, 8, 3, 4, 13, 7, 5, 15, 14, 1, 9],
    [12, 5, 1, 15, 14, 13, 4, 10, 0, 7, 6, 3, 9, 2, 8, 11],
    [13, 11, 7, 14, 12, 1, 3, 9, 5, 0, 15, 4, 8, 6, 2, 10],
    [6, 15, 14, 9, 11, 3, 0, 8, 12, 2, 13, 7, 1, 4, 10, 5],
    [10, 2, 8, 4, 7, 6, 1, 5, 15, 11, 9, 14, 3, 12, 13, 0],
];

pub const OUT_LEN: usize = 32;
pub const BLOCK_LEN: usize = 64;
pub const MAX_KEY_LEN: usize = 32;

fn g(v: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize, x: u32, y: u32) {
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(x);
    v[d] = (v[d] ^ v[a]).rotate_right(16);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(12);
    v[a] = v[a].wrapping_add(v[b]).wrapping_add(y);
    v[d] = (v[d] ^ v[a]).rotate_right(8);
    v[c] = v[c].wrapping_add(v[d]);
    v[b] = (v[b] ^ v[c]).rotate_right(7);
}

fn compress(h: &mut [u32; 8], block: &[u8; BLOCK_LEN], t: u64, last: bool) {
    let mut m = [0u32; 16];
    for i in 0..16 {
        m[i] = u32::from_le_bytes([
            block[4 * i],
            block[4 * i + 1],
            block[4 * i + 2],
            block[4 * i + 3],
        ]);
    }
    let mut v = [0u32; 16];
    v[..8].copy_from_slice(h);
    v[8..].copy_from_slice(&IV);
    v[12] ^= t as u32;
    v[13] ^= (t >> 32) as u32;
    if last {
        v[14] = !v[14];
    }
    for s in SIGMA.iter() {
        g(&mut v, 0, 4, 8, 12, m[s[0]], m[s[1]]);
        g(&mut v, 1, 5, 9, 13, m[s[2]], m[s[3]]);
        g(&mut v, 2, 6, 10, 14, m[s[4]], m[s[5]]);
        g(&mut v, 3, 7, 11, 15, m[s[6]], m[s[7]]);
        g(&mut v, 0, 5, 10, 15, m[s[8]], m[s[9]]);
        g(&mut v, 1, 6, 11, 12, m[s[10]], m[s[11]]);
        g(&mut v, 2, 7, 8, 13, m[s[12]], m[s[13]]);
        g(&mut v, 3, 4, 9, 14, m[s[14]], m[s[15]]);
    }
    for i in 0..8 {
        h[i] ^= v[i] ^ v[i + 8];
    }
}

/// The streaming state: `outlen` bytes of digest, optionally keyed (RFC 7693
/// §3.2: the padded key block is the first block of the input stream).
pub struct Blake2s {
    h: [u32; 8],
    t: u64,
    buf: [u8; BLOCK_LEN],
    buf_len: usize,
    outlen: usize,
}

impl Blake2s {
    /// `outlen` in 1..=32; `key` empty for unkeyed, else 1..=32 bytes.
    pub fn new(outlen: usize, key: &[u8]) -> Self {
        assert!((1..=OUT_LEN).contains(&outlen), "blake2s outlen 1..=32");
        assert!(key.len() <= MAX_KEY_LEN, "blake2s key 0..=32 bytes");
        let mut h = IV;
        h[0] ^= 0x0101_0000 ^ ((key.len() as u32) << 8) ^ outlen as u32;
        let mut state = Blake2s {
            h,
            t: 0,
            buf: [0; BLOCK_LEN],
            buf_len: 0,
            outlen,
        };
        if !key.is_empty() {
            let mut block = [0u8; BLOCK_LEN];
            block[..key.len()].copy_from_slice(key);
            state.update(&block);
        }
        state
    }

    pub fn update(&mut self, mut data: &[u8]) {
        while !data.is_empty() {
            if self.buf_len == BLOCK_LEN {
                self.t += BLOCK_LEN as u64;
                let block = self.buf;
                compress(&mut self.h, &block, self.t, false);
                self.buf_len = 0;
            }
            let n = (BLOCK_LEN - self.buf_len).min(data.len());
            self.buf[self.buf_len..self.buf_len + n].copy_from_slice(&data[..n]);
            self.buf_len += n;
            data = &data[n..];
        }
    }

    /// Writes `self.outlen` bytes into `out` (zero padding fills the last block).
    pub fn finalize(mut self, out: &mut [u8]) {
        assert_eq!(out.len(), self.outlen, "blake2s finalize buffer is outlen");
        self.t += self.buf_len as u64;
        for b in &mut self.buf[self.buf_len..] {
            *b = 0;
        }
        let block = self.buf;
        compress(&mut self.h, &block, self.t, true);
        let mut full = [0u8; OUT_LEN];
        for i in 0..8 {
            full[4 * i..4 * i + 4].copy_from_slice(&self.h[i].to_le_bytes());
        }
        out.copy_from_slice(&full[..self.outlen]);
    }
}

/// One-shot unkeyed BLAKE2s-256.
pub fn blake2s(data: &[u8]) -> [u8; OUT_LEN] {
    let mut h = Blake2s::new(OUT_LEN, &[]);
    h.update(data);
    let mut out = [0u8; OUT_LEN];
    h.finalize(&mut out);
    out
}

/// One-shot keyed BLAKE2s-256 (key 1..=32 bytes).
pub fn blake2s_keyed(key: &[u8], data: &[u8]) -> [u8; OUT_LEN] {
    assert!(!key.is_empty(), "blake2s_keyed needs a key");
    let mut h = Blake2s::new(OUT_LEN, key);
    h.update(data);
    let mut out = [0u8; OUT_LEN];
    h.finalize(&mut out);
    out
}
