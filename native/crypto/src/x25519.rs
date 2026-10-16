//! X25519 (RFC 7748): scalar multiplication on the Montgomery curve Curve25519.
//!
//! Field arithmetic is 5×51-bit limbs mod 2^255-19. The Montgomery ladder runs
//! exactly 255 iterations with swaps selected by mask (no secret-dependent
//! branches or memory indexes), and field reduction carries on fixed limb
//! positions — constant-time with respect to the scalar and the point as far as
//! plain Rust goes (no assembly barriers; the code is straight-line either way).
//! The final inversion is a fixed square-and-multiply over the public exponent
//! p-2 (its branches are on public exponent bits).
//!
//! The caller (the C ABI) refuses an all-zero output: RFC 7748 §6 — the output
//! is zero exactly when the input point has small order (dividing the cofactor).

const MASK: u64 = (1 << 51) - 1;

/// A field element mod 2^255-19 as five 51-bit little-endian limbs.
/// Invariant after every operation here: each limb < 2^51 + 32.
type Fe = [u64; 5];

const ZERO: Fe = [0; 5];
const ONE: Fe = [1, 0, 0, 0, 0];

/// 2p = 2^256 - 38 in 51-bit limbs, for subtraction without negatives.
const TWO_P: Fe = [
    0xffff_ffff_fffda,
    0xffff_ffff_ffffe,
    0xffff_ffff_ffffe,
    0xffff_ffff_ffffe,
    0xffff_ffff_ffffe,
];

fn carry(mut h: Fe) -> Fe {
    let mut c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    c = h[1] >> 51;
    h[1] &= MASK;
    h[2] += c;
    c = h[2] >> 51;
    h[2] &= MASK;
    h[3] += c;
    c = h[3] >> 51;
    h[3] &= MASK;
    h[4] += c;
    c = h[4] >> 51;
    h[4] &= MASK;
    h[0] += 19 * c;
    c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    h
}

fn fe_add(a: &Fe, b: &Fe) -> Fe {
    let mut r = [0u64; 5];
    for i in 0..5 {
        r[i] = a[i] + b[i];
    }
    carry(r)
}

/// a - b mod p.
fn fe_sub(a: &Fe, b: &Fe) -> Fe {
    let mut r = [0u64; 5];
    for i in 0..5 {
        r[i] = a[i] + TWO_P[i] - b[i];
    }
    carry(r)
}

fn fe_mul(a: &Fe, b: &Fe) -> Fe {
    let (a0, a1, a2, a3, a4) = (
        a[0] as u128,
        a[1] as u128,
        a[2] as u128,
        a[3] as u128,
        a[4] as u128,
    );
    let (b0, b1, b2, b3, b4) = (
        b[0] as u128,
        b[1] as u128,
        b[2] as u128,
        b[3] as u128,
        b[4] as u128,
    );
    let (b1_19, b2_19, b3_19, b4_19) = (b1 * 19, b2 * 19, b3 * 19, b4 * 19);

    let d0 = a0 * b0 + a1 * b4_19 + a2 * b3_19 + a3 * b2_19 + a4 * b1_19;
    let d1 = a0 * b1 + a1 * b0 + a2 * b4_19 + a3 * b3_19 + a4 * b2_19;
    let d2 = a0 * b2 + a1 * b1 + a2 * b0 + a3 * b4_19 + a4 * b3_19;
    let d3 = a0 * b3 + a1 * b2 + a2 * b1 + a3 * b0 + a4 * b4_19;
    let d4 = a0 * b4 + a1 * b3 + a2 * b2 + a3 * b1 + a4 * b0;

    let mut h = [0u64; 5];
    let mut c = d0 >> 51;
    h[0] = d0 as u64 & MASK;
    let d1 = d1 + c;
    c = d1 >> 51;
    h[1] = d1 as u64 & MASK;
    let d2 = d2 + c;
    c = d2 >> 51;
    h[2] = d2 as u64 & MASK;
    let d3 = d3 + c;
    c = d3 >> 51;
    h[3] = d3 as u64 & MASK;
    let d4 = d4 + c;
    c = d4 >> 51;
    h[4] = d4 as u64 & MASK;
    h[0] += 19 * (c as u64);
    let c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    h
}

fn fe_sq(a: &Fe) -> Fe {
    fe_mul(a, a)
}

/// z^(p-2) mod p — the inversion. The exponent is public.
fn fe_invert(z: &Fe) -> Fe {
    // p - 2 = 2^255 - 21: little-endian bytes eb ff … ff 7f.
    let exponent: [u8; 32] = [
        0xeb, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
        0xff, 0x7f,
    ];
    let mut r = ONE;
    for byte in exponent.iter().rev() {
        for bit in (0..8).rev() {
            r = fe_sq(&r);
            if (byte >> bit) & 1 == 1 {
                r = fe_mul(&r, z);
            }
        }
    }
    r
}

fn fe_frombytes(b: &[u8; 32]) -> Fe {
    let mut w = [0u64; 4];
    for (i, word) in w.iter_mut().enumerate() {
        let mut t = [0u8; 8];
        t.copy_from_slice(&b[8 * i..8 * i + 8]);
        *word = u64::from_le_bytes(t);
    }
    let mut h = [0u64; 5];
    for (i, limb) in h.iter_mut().enumerate() {
        let bit = 51 * i;
        let (word, shift) = (bit / 64, bit % 64);
        let mut v = w[word] >> shift;
        if shift > 0 && word + 1 < 4 {
            v |= w[word + 1] << (64 - shift);
        }
        *limb = v & MASK; // drops the high bit of byte 31 (RFC 7748 §5)
    }
    h
}

fn fe_tobytes(h: &Fe) -> [u8; 32] {
    let mut h = carry(*h);
    // q = 1 exactly when h >= p = 2^255-19; then h -= p via +19 and dropping 2^255.
    let mut q = (h[0] + 19) >> 51;
    q = (h[1] + q) >> 51;
    q = (h[2] + q) >> 51;
    q = (h[3] + q) >> 51;
    q = (h[4] + q) >> 51;
    h[0] += 19 * q;
    let mut c = h[0] >> 51;
    h[0] &= MASK;
    h[1] += c;
    c = h[1] >> 51;
    h[1] &= MASK;
    h[2] += c;
    c = h[2] >> 51;
    h[2] &= MASK;
    h[3] += c;
    c = h[3] >> 51;
    h[3] &= MASK;
    h[4] += c;
    h[4] &= MASK;

    // Pack the five 51-bit limbs into four little-endian 64-bit words.
    let mut w = [0u64; 4];
    for (i, limb) in h.iter().enumerate() {
        let bit = 51 * i;
        let (word, shift) = (bit / 64, bit % 64);
        w[word] |= limb << shift;
        if shift + 51 > 64 {
            w[word + 1] |= limb >> (64 - shift);
        }
    }
    let mut out = [0u8; 32];
    for (i, word) in w.iter().enumerate() {
        out[8 * i..8 * i + 8].copy_from_slice(&word.to_le_bytes());
    }
    out
}

/// Swap (a, b) for every limb when `swap` is 1; `swap` is 0 or 1.
fn cswap(swap: u64, a: &mut Fe, b: &mut Fe) {
    let mask = 0u64.wrapping_sub(swap);
    for i in 0..5 {
        let t = mask & (a[i] ^ b[i]);
        a[i] ^= t;
        b[i] ^= t;
    }
}

/// X25519(k, u): the u-coordinate of k·P_u (RFC 7748 §5).
/// Any 32-byte scalar and u-coordinate are accepted (scalar clamped,
/// high bit of u masked), as the RFC specifies.
pub fn x25519(scalar: &[u8; 32], u: &[u8; 32]) -> [u8; 32] {
    let mut k = *scalar;
    k[0] &= 248;
    k[31] &= 127;
    k[31] |= 64;

    let x1 = fe_frombytes(u);
    let mut x2 = ONE;
    let mut z2 = ZERO;
    let mut x3 = x1;
    let mut z3 = ONE;
    let mut swap = 0u64;

    for t in (0..255).rev() {
        let kt = ((k[t >> 3] >> (t & 7)) & 1) as u64;
        swap ^= kt;
        cswap(swap, &mut x2, &mut x3);
        cswap(swap, &mut z2, &mut z3);
        swap = kt;

        let a = fe_add(&x2, &z2);
        let aa = fe_sq(&a);
        let b = fe_sub(&x2, &z2);
        let bb = fe_sq(&b);
        let e = fe_sub(&aa, &bb);
        let c = fe_add(&x3, &z3);
        let d = fe_sub(&x3, &z3);
        let da = fe_mul(&d, &a);
        let cb = fe_mul(&c, &b);
        x3 = fe_sq(&fe_add(&da, &cb));
        z3 = fe_mul(&x1, &fe_sq(&fe_sub(&da, &cb)));
        x2 = fe_mul(&aa, &bb);
        // z2 = e · (aa + a24 · e), with a24 = 121665 = (A - 2) / 4.
        let a24_e = fe_mul(&e, &[121665, 0, 0, 0, 0]);
        z2 = fe_mul(&e, &fe_add(&aa, &a24_e));
    }
    cswap(swap, &mut x2, &mut x3);
    cswap(swap, &mut z2, &mut z3);
    fe_tobytes(&fe_mul(&x2, &fe_invert(&z2)))
}

/// X25519(k, 9): the public key for a private scalar (RFC 7748 §6.1).
pub fn x25519_public(scalar: &[u8; 32]) -> [u8; 32] {
    let mut base = [0u8; 32];
    base[0] = 9;
    x25519(scalar, &base)
}
