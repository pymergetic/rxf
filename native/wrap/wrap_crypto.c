/*
 * The verification primitives of the activation seam (wrap_crypto.h). Freestanding:
 * no libc, no state, nothing that signs. The code is the standard constructions --
 * Merkle-Damgard with the FIPS 180-4 constants for the hashes, the Edwards curve
 * edwards25519 in extended coordinates for RFC 8032 verification -- and the pinned
 * vectors of rxf_wrap_crypto_vectors are the gate (docs/26 Recipe 1: if a vector
 * disagrees with the code, the code is wrong).
 */
#include "wrap_crypto.h"

typedef rxf_u8 u8;
typedef rxf_u32 u32;
typedef rxf_u64 u64;
typedef unsigned __int128 u128;

/* ---- bytes ---- */

static u64 load8(const u8 *p) {
    u64 word = 0;
    for (u32 i = 0; i < 8; ++i) word |= (u64)p[i] << (8 * i);
    return word;
}

static void store8(u8 *p, u64 word) {
    for (u32 i = 0; i < 8; ++i) p[i] = (u8)(word >> (8 * i));
}

static u32 bytes_equal(const u8 *a, const u8 *b, u32 n) {
    u32 difference = 0;
    for (u32 i = 0; i < n; ++i) difference |= (u32)(a[i] ^ b[i]);
    return difference == 0;
}

/* ---- SHA-256 (FIPS 180-4) ---- */

static const u32 K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
    0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
    0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
    0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
    0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
    0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
    0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
    0xc67178f2,
};

static u32 rotr32(u32 word, u32 count) { return (word >> count) | (word << (32 - count)); }

static void sha256_block(u32 *state, const u8 *block) {
    u32 w[64];
    for (u32 i = 0; i < 16; ++i) {
        w[i] = ((u32)block[4 * i] << 24) | ((u32)block[4 * i + 1] << 16) |
               ((u32)block[4 * i + 2] << 8) | (u32)block[4 * i + 3];
    }
    for (u32 i = 16; i < 64; ++i) {
        u32 s0 = rotr32(w[i - 15], 7) ^ rotr32(w[i - 15], 18) ^ (w[i - 15] >> 3);
        u32 s1 = rotr32(w[i - 2], 17) ^ rotr32(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    u32 a = state[0], b = state[1], c = state[2], d = state[3];
    u32 e = state[4], f = state[5], g = state[6], h = state[7];
    for (u32 i = 0; i < 64; ++i) {
        u32 s1 = rotr32(e, 6) ^ rotr32(e, 11) ^ rotr32(e, 25);
        u32 ch = (e & f) ^ (~e & g);
        u32 t1 = h + s1 + ch + K256[i] + w[i];
        u32 s0 = rotr32(a, 2) ^ rotr32(a, 13) ^ rotr32(a, 22);
        u32 maj = (a & b) ^ (a & c) ^ (b & c);
        u32 t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

void rxf_wrap_sha256(const void *data, rxf_u64 bytes, u8 *out) {
    const u8 *input = (const u8 *)data;
    u32 state[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    u64 whole = bytes & ~((u64)63);
    for (u64 at = 0; at < whole; at += 64) sha256_block(state, input + at);
    u8 tail[128];
    u32 rest = (u32)(bytes - whole);
    for (u32 i = 0; i < rest; ++i) tail[i] = input[whole + i];
    tail[rest] = 0x80;
    u32 padded = rest < 56 ? 64 : 128;
    for (u32 i = rest + 1; i < padded - 8; ++i) tail[i] = 0;
    u64 bits = bytes * 8;
    for (u32 i = 0; i < 8; ++i) tail[padded - 8 + i] = (u8)(bits >> (56 - 8 * i));
    sha256_block(state, tail);
    if (padded == 128) sha256_block(state, tail + 64);
    for (u32 i = 0; i < 8; ++i) {
        out[4 * i] = (u8)(state[i] >> 24);
        out[4 * i + 1] = (u8)(state[i] >> 16);
        out[4 * i + 2] = (u8)(state[i] >> 8);
        out[4 * i + 3] = (u8)state[i];
    }
}

/* ---- SHA-512 (FIPS 180-4), incremental so the hash of a statement can stream ---- */

static const u64 K512[80] = {
    0x428a2f98d728ae22, 0x7137449123ef65cd, 0xb5c0fbcfec4d3b2f, 0xe9b5dba58189dbbc,
    0x3956c25bf348b538, 0x59f111f1b605d019, 0x923f82a4af194f9b, 0xab1c5ed5da6d8118,
    0xd807aa98a3030242, 0x12835b0145706fbe, 0x243185be4ee4b28c, 0x550c7dc3d5ffb4e2,
    0x72be5d74f27b896f, 0x80deb1fe3b1696b1, 0x9bdc06a725c71235, 0xc19bf174cf692694,
    0xe49b69c19ef14ad2, 0xefbe4786384f25e3, 0x0fc19dc68b8cd5b5, 0x240ca1cc77ac9c65,
    0x2de92c6f592b0275, 0x4a7484aa6ea6e483, 0x5cb0a9dcbd41fbd4, 0x76f988da831153b5,
    0x983e5152ee66dfab, 0xa831c66d2db43210, 0xb00327c898fb213f, 0xbf597fc7beef0ee4,
    0xc6e00bf33da88fc2, 0xd5a79147930aa725, 0x06ca6351e003826f, 0x142929670a0e6e70,
    0x27b70a8546d22ffc, 0x2e1b21385c26c926, 0x4d2c6dfc5ac42aed, 0x53380d139d95b3df,
    0x650a73548baf63de, 0x766a0abb3c77b2a8, 0x81c2c92e47edaee6, 0x92722c851482353b,
    0xa2bfe8a14cf10364, 0xa81a664bbc423001, 0xc24b8b70d0f89791, 0xc76c51a30654be30,
    0xd192e819d6ef5218, 0xd69906245565a910, 0xf40e35855771202a, 0x106aa07032bbd1b8,
    0x19a4c116b8d2d0c8, 0x1e376c085141ab53, 0x2748774cdf8eeb99, 0x34b0bcb5e19b48a8,
    0x391c0cb3c5c95a63, 0x4ed8aa4ae3418acb, 0x5b9cca4f7763e373, 0x682e6ff3d6b2b8a3,
    0x748f82ee5defb2fc, 0x78a5636f43172f60, 0x84c87814a1f0ab72, 0x8cc702081a6439ec,
    0x90befffa23631e28, 0xa4506cebde82bde9, 0xbef9a3f7b2c67915, 0xc67178f2e372532b,
    0xca273eceea26619c, 0xd186b8c721c0c207, 0xeada7dd6cde0eb1e, 0xf57d4f7fee6ed178,
    0x06f067aa72176fba, 0x0a637dc5a2c898a6, 0x113f9804bef90dae, 0x1b710b35131c471b,
    0x28db77f523047d84, 0x32caab7b40c72493, 0x3c9ebe0a15c9bebc, 0x431d67c49c100d4c,
    0x4cc5d4becb3e42b6, 0x597f299cfc657e2a, 0x5fcb6fab3ad6faec, 0x6c44198c4a475817,
};

static u64 rotr64(u64 word, u32 count) { return (word >> count) | (word << (64 - count)); }

static void sha512_block(u64 *state, const u8 *block) {
    u64 w[80];
    for (u32 i = 0; i < 16; ++i) {
        w[i] = ((u64)block[8 * i] << 56) | ((u64)block[8 * i + 1] << 48) |
               ((u64)block[8 * i + 2] << 40) | ((u64)block[8 * i + 3] << 32) |
               ((u64)block[8 * i + 4] << 24) | ((u64)block[8 * i + 5] << 16) |
               ((u64)block[8 * i + 6] << 8) | (u64)block[8 * i + 7];
    }
    for (u32 i = 16; i < 80; ++i) {
        u64 s0 = rotr64(w[i - 15], 1) ^ rotr64(w[i - 15], 8) ^ (w[i - 15] >> 7);
        u64 s1 = rotr64(w[i - 2], 19) ^ rotr64(w[i - 2], 61) ^ (w[i - 2] >> 6);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    u64 a = state[0], b = state[1], c = state[2], d = state[3];
    u64 e = state[4], f = state[5], g = state[6], h = state[7];
    for (u32 i = 0; i < 80; ++i) {
        u64 s1 = rotr64(e, 14) ^ rotr64(e, 18) ^ rotr64(e, 41);
        u64 ch = (e & f) ^ (~e & g);
        u64 t1 = h + s1 + ch + K512[i] + w[i];
        u64 s0 = rotr64(a, 28) ^ rotr64(a, 34) ^ rotr64(a, 39);
        u64 maj = (a & b) ^ (a & c) ^ (b & c);
        u64 t2 = s0 + maj;
        h = g; g = f; f = e; e = d + t1;
        d = c; c = b; b = a; a = t1 + t2;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}

typedef struct {
    u64 state[8];
    u8 partial[128];
    u32 have;
    u64 length; /* the message bytes; a 64-bit count is all a package ever needs */
} sha512_ctx;

static void sha512_init(sha512_ctx *ctx) {
    u64 start[8] = {0x6a09e667f3bcc908, 0xbb67ae8584caa73b, 0x3c6ef372fe94f82b,
                    0xa54ff53a5f1d36f1, 0x510e527fade682d1, 0x9b05688c2b3e6c1f,
                    0x1f83d9abfb41bd6b, 0x5be0cd19137e2179};
    for (u32 i = 0; i < 8; ++i) ctx->state[i] = start[i];
    ctx->have = 0;
    ctx->length = 0;
}

static void sha512_update(sha512_ctx *ctx, const void *data, u64 bytes) {
    const u8 *input = (const u8 *)data;
    ctx->length += bytes;
    while (bytes > 0) {
        u32 take = 128 - ctx->have;
        if ((u64)take > bytes) take = (u32)bytes;
        for (u32 i = 0; i < take; ++i) ctx->partial[ctx->have + i] = input[i];
        ctx->have += take;
        input += take;
        bytes -= take;
        if (ctx->have == 128) {
            sha512_block(ctx->state, ctx->partial);
            ctx->have = 0;
        }
    }
}

static void sha512_final(sha512_ctx *ctx, u8 *out) {
    u64 bits_high = ctx->length >> 61;
    u64 bits_low = ctx->length << 3;
    u8 stop = 0x80, zero = 0;
    sha512_update(ctx, &stop, 1);
    while (ctx->have != 112) sha512_update(ctx, &zero, 1);
    u8 count[16];
    for (u32 i = 0; i < 8; ++i) {
        count[i] = (u8)(bits_high >> (56 - 8 * i));
        count[8 + i] = (u8)(bits_low >> (56 - 8 * i));
    }
    sha512_update(ctx, count, 16);
    for (u32 i = 0; i < 8; ++i) {
        u64 word = ctx->state[i];
        for (u32 j = 0; j < 8; ++j) out[8 * i + j] = (u8)(word >> (56 - 8 * j));
    }
}

void rxf_wrap_sha512(const void *data, rxf_u64 bytes, u8 *out) {
    sha512_ctx ctx;
    sha512_init(&ctx);
    sha512_update(&ctx, data, bytes);
    sha512_final(&ctx, out);
}

/* ---- the field of edwards25519, five 51-bit limbs (RFC 8032 section 5.1) ---- */

#define MASK51 0x7ffffffffffffULL

typedef struct {
    u64 v[5];
} fe;

static void fe_zero(fe *h) { for (u32 i = 0; i < 5; ++i) h->v[i] = 0; }

static void fe_one(fe *h) {
    fe_zero(h);
    h->v[0] = 1;
}

static void fe_copy(fe *h, const fe *f) { for (u32 i = 0; i < 5; ++i) h->v[i] = f->v[i]; }

static void fe_carry(fe *h) {
    for (u32 round = 0; round < 2; ++round) {
        u64 c = 0;
        for (u32 i = 0; i < 5; ++i) {
            h->v[i] += c;
            c = h->v[i] >> 51;
            h->v[i] &= MASK51;
        }
        h->v[0] += 19 * c;
    }
}

static void fe_add(fe *h, const fe *f, const fe *g) {
    for (u32 i = 0; i < 5; ++i) h->v[i] = f->v[i] + g->v[i];
    fe_carry(h);
}

/* 2p in five limbs, so a subtraction never underflows. */
static const u64 TWO_P[5] = {0xfffffffffffdaULL, 0xffffffffffffeULL, 0xffffffffffffeULL,
                             0xffffffffffffeULL, 0xffffffffffffeULL};

static void fe_sub(fe *h, const fe *f, const fe *g) {
    for (u32 i = 0; i < 5; ++i) h->v[i] = f->v[i] + TWO_P[i] - g->v[i];
    fe_carry(h);
}

static void fe_mul(fe *h, const fe *f, const fe *g) {
    u64 f0 = f->v[0], f1 = f->v[1], f2 = f->v[2], f3 = f->v[3], f4 = f->v[4];
    u64 g0 = g->v[0], g1 = g->v[1], g2 = g->v[2], g3 = g->v[3], g4 = g->v[4];
    u128 t0 = (u128)f0 * g0 + 19 * ((u128)f1 * g4 + (u128)f2 * g3 + (u128)f3 * g2 + (u128)f4 * g1);
    u128 t1 = (u128)f0 * g1 + (u128)f1 * g0 + 19 * ((u128)f2 * g4 + (u128)f3 * g3 + (u128)f4 * g2);
    u128 t2 = (u128)f0 * g2 + (u128)f1 * g1 + (u128)f2 * g0 + 19 * ((u128)f3 * g4 + (u128)f4 * g3);
    u128 t3 = (u128)f0 * g3 + (u128)f1 * g2 + (u128)f2 * g1 + (u128)f3 * g0 + 19 * ((u128)f4 * g4);
    u128 t4 = (u128)f0 * g4 + (u128)f1 * g3 + (u128)f2 * g2 + (u128)f3 * g1 + (u128)f4 * g0;
    u64 c;
    c = (u64)(t0 >> 51); h->v[0] = (u64)t0 & MASK51;
    t1 += c; c = (u64)(t1 >> 51); h->v[1] = (u64)t1 & MASK51;
    t2 += c; c = (u64)(t2 >> 51); h->v[2] = (u64)t2 & MASK51;
    t3 += c; c = (u64)(t3 >> 51); h->v[3] = (u64)t3 & MASK51;
    t4 += c; c = (u64)(t4 >> 51); h->v[4] = (u64)t4 & MASK51;
    h->v[0] += 19 * c;
    c = h->v[0] >> 51; h->v[0] &= MASK51;
    h->v[1] += c;
}

static void fe_sq(fe *h, const fe *f) { fe_mul(h, f, f); }

static void fe_frombytes(fe *h, const u8 *s) {
    u64 t0 = load8(s), t1 = load8(s + 8), t2 = load8(s + 16), t3 = load8(s + 24);
    h->v[0] = t0 & MASK51;
    h->v[1] = ((t0 >> 51) | (t1 << 13)) & MASK51;
    h->v[2] = ((t1 >> 38) | (t2 << 26)) & MASK51;
    h->v[3] = ((t2 >> 25) | (t3 << 39)) & MASK51;
    h->v[4] = (t3 >> 12) & MASK51;
}

static void fe_tobytes(u8 *s, const fe *f) {
    fe copy = *f;
    fe_carry(&copy);
    /* The value is the limbs at 51-bit offsets; add each into a 256-bit word set
     * (positionally exact -- no limb overlaps another without carrying). */
    u64 w[4] = {0, 0, 0, 0};
    for (u32 i = 0; i < 5; ++i) {
        u32 at = 51 * i, word = at / 64, shift = at % 64;
        u64 value = copy.v[i];
        /* The shifted limb fits in 128 bits, so the carry out of the low word is
         * all of it that sits above bit 63 -- it is added once, never twice. */
        u128 total = (u128)w[word] + ((u128)value << shift);
        w[word] = (u64)total;
        u64 carry = (u64)(total >> 64);
        if (word + 1 < 4 && carry) {
            total = (u128)w[word + 1] + carry;
            w[word + 1] = (u64)total;
            carry = (u64)(total >> 64);
            if (carry && word + 2 < 4) w[word + 2] += carry;
        }
    }
    u64 w0 = w[0], w1 = w[1], w2 = w[2], w3 = w[3];
    /* p = 2^255 - 19 as four words; subtract while the value is at least p. */
    for (u32 round = 0; round < 2; ++round) {
        u64 p0 = 0xffffffffffffffedULL, p1 = 0xffffffffffffffffULL;
        u64 p2 = 0xffffffffffffffffULL, p3 = 0x7fffffffffffffffULL;
        u64 d0 = w0 - p0;
        u64 borrow = w0 < p0;
        u64 d1 = w1 - p1 - borrow;
        borrow = (w1 < p1) || (w1 == p1 && borrow);
        u64 d2 = w2 - p2 - borrow;
        borrow = (w2 < p2) || (w2 == p2 && borrow);
        u64 d3 = w3 - p3 - borrow;
        borrow = (w3 < p3) || (w3 == p3 && borrow);
        if (!borrow) {
            w0 = d0; w1 = d1; w2 = d2; w3 = d3;
        }
    }
    store8(s, w0);
    store8(s + 8, w1);
    store8(s + 16, w2);
    store8(s + 24, w3);
}

static u32 fe_equal(const fe *f, const fe *g) {
    u8 a[32], b[32];
    fe_tobytes(a, f);
    fe_tobytes(b, g);
    return bytes_equal(a, b, 32);
}

/* out = base^exponent, the exponent a 32-byte little-endian word. */
static void fe_pow(fe *out, const fe *base, const u8 *exponent) {
    fe acc;
    fe_one(&acc);
    for (u32 bit = 256; bit-- > 0;) {
        fe_sq(&acc, &acc);
        if ((exponent[bit / 8] >> (bit % 8)) & 1) fe_mul(&acc, &acc, base);
    }
    fe_copy(out, &acc);
}

/* p - 2, (p + 3) / 8 and (p - 1) / 4 as little-endian words (RFC 8032 section 5.1). */
static const u8 P_MINUS_2[32] = {0xeb, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                 0xff, 0x7f};
static const u8 P_PLUS_3_OVER_8[32] = {0xfe, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                       0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                       0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                       0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x0f};
static const u8 P_MINUS_1_OVER_4[32] = {0xfb, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff,
                                        0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x1f};

static void fe_invert(fe *out, const fe *f) { fe_pow(out, f, P_MINUS_2); }

static void fe_sqrt_m1(fe *out) {
    fe two;
    fe_zero(&two);
    two.v[0] = 2;
    fe_pow(out, &two, P_MINUS_1_OVER_4);
}

/* d = -121665 / 121666 mod p, the curve word of edwards25519 (RFC 8032 section 5.1). */
static void fe_curve_d(fe *out) {
    fe n, inverse, zero;
    fe_zero(&n);
    n.v[0] = 121665;
    fe_zero(&inverse);
    inverse.v[0] = 121666;
    fe_invert(&inverse, &inverse);
    fe_mul(out, &n, &inverse);
    fe_zero(&zero);
    fe_sub(out, &zero, out);
}

/* ---- points in extended coordinates (X, Y, Z, T): x = X/Z, y = Y/Z, X*Y = Z*T ---- */

typedef struct {
    fe x, y, z, t;
} point;

static void point_identity(point *p) {
    fe_zero(&p->x);
    fe_one(&p->y);
    fe_one(&p->z);
    fe_zero(&p->t);
}

/* add-2008-hwcd-3 (the curve has a = -1). */
static void point_add(point *r, const point *p, const point *q, const fe *d2) {
    fe a, b, c, d, e, f, g, h, t;
    fe_sub(&t, &p->y, &p->x);
    fe_sub(&a, &q->y, &q->x);
    fe_mul(&a, &t, &a);
    fe_add(&t, &p->y, &p->x);
    fe_add(&b, &q->y, &q->x);
    fe_mul(&b, &t, &b);
    fe_mul(&c, &p->t, d2);
    fe_mul(&c, &c, &q->t);
    fe_add(&d, &p->z, &p->z);
    fe_mul(&d, &d, &q->z);
    fe_sub(&e, &b, &a);
    fe_sub(&f, &d, &c);
    fe_add(&g, &d, &c);
    fe_add(&h, &b, &a);
    fe_mul(&r->x, &e, &f);
    fe_mul(&r->y, &g, &h);
    fe_mul(&r->t, &e, &h);
    fe_mul(&r->z, &f, &g);
}

/* dbl-2008-hwcd is not needed: the unified add below doubles as well (the Python
 * reference of the lane does the same, _add(point, point)), one formula less to
 * carry and one fewer thing to be wrong about. */

static u32 point_equal(const point *p, const point *q) {
    fe left, right;
    fe_mul(&left, &p->x, &q->z);
    fe_mul(&right, &q->x, &p->z);
    if (!fe_equal(&left, &right)) return 0;
    fe_mul(&left, &p->y, &q->z);
    fe_mul(&right, &q->y, &p->z);
    return fe_equal(&left, &right);
}

static void point_scalarmult(point *r, const u8 *scalar, const point *p, const fe *d2) {
    point acc;
    point_identity(&acc);
    for (u32 bit = 256; bit-- > 0;) {
        point_add(&acc, &acc, &acc, d2);
        if ((scalar[bit / 8] >> (bit % 8)) & 1) point_add(&acc, &acc, p, d2);
    }
    *r = acc;
}

/*
 * The encoding of a point: the y word (255 bits, little-endian) with the sign of x
 * in the top bit. The decoding of RFC 8032 section 5.1.3, refusing every encoding
 * that is not the canonical one of the point it stands for.
 */
static u32 point_decompress(point *p, const u8 *encoded, const fe *d, const fe *sqrt_m1) {
    u8 sign = encoded[31] >> 7;
    u8 ybytes[32], canonical[32];
    for (u32 i = 0; i < 32; ++i) ybytes[i] = encoded[i];
    ybytes[31] &= 0x7f;
    fe_frombytes(&p->y, ybytes);
    fe_tobytes(canonical, &p->y);
    if (!bytes_equal(canonical, ybytes, 32)) return 1; /* y is not the canonical word */
    fe one, zero, yy, u, v, x2, x, check;
    fe_one(&one);
    fe_zero(&zero);
    fe_sq(&yy, &p->y);
    fe_sub(&u, &yy, &one);
    fe_mul(&v, &yy, d);
    fe_add(&v, &v, &one);
    fe_invert(&v, &v);
    fe_mul(&x2, &u, &v);
    fe_pow(&x, &x2, P_PLUS_3_OVER_8);
    fe_sq(&check, &x);
    if (!fe_equal(&check, &x2)) {
        fe_mul(&x, &x, sqrt_m1);
        fe_sq(&check, &x);
        if (!fe_equal(&check, &x2)) return 2; /* the curve has no point at this y */
    }
    u8 xbytes[32];
    fe_tobytes(xbytes, &x);
    if ((xbytes[0] & 1) != sign) {
        if (fe_equal(&x, &zero)) return 3; /* x is 0 but the sign bit says 1 */
        fe_sub(&x, &zero, &x);
    }
    fe_copy(&p->x, &x);
    fe_one(&p->z);
    fe_mul(&p->t, &p->x, &p->y);
    return 0;
}

/* ---- scalars: the group order L = 2^252 + 27742317777372353535851937790883648493 ---- */

static const u64 L_WORDS[4] = {0x5812631a5cf5d3edULL, 0x14def9dea2f79cd6ULL, 0,
                               0x1000000000000000ULL};

/* 32 little-endian bytes at or beyond L: the signature's S word must be below. */
static u32 scalar_at_or_beyond_l(const u8 *scalar) {
    for (u32 i = 32; i-- > 0;) {
        u8 s = scalar[i], l = (u8)(L_WORDS[i / 8] >> (8 * (i % 8)));
        if (s != l) return s > l;
    }
    return 1; /* exactly L */
}

/* 64 little-endian bytes reduced mod L into 32, one bit at a time (boot runs this
 * once per verification; the obvious algorithm is the one that is right). */
static void scalar_reduce(u8 *out, const u8 *in) {
    u64 r[4] = {0, 0, 0, 0};
    for (u32 bit = 512; bit-- > 0;) {
        u64 carry = 0;
        for (u32 i = 0; i < 4; ++i) {
            u64 next = r[i] >> 63;
            r[i] = (r[i] << 1) | carry;
            carry = next;
        }
        if ((in[bit / 8] >> (bit % 8)) & 1) {
            u64 carry = 1;
            for (u32 i = 0; i < 4; ++i) {
                u64 sum = r[i] + carry;
                carry = sum < r[i];
                r[i] = sum;
                if (!carry) break;
            }
        }
        /* r was below L before the shift, so one subtraction closes it again. */
        u64 borrow = 0, delta[4];
        for (u32 i = 0; i < 4; ++i) {
            delta[i] = r[i] - L_WORDS[i] - borrow;
            borrow = (r[i] < L_WORDS[i]) || (r[i] == L_WORDS[i] && borrow);
        }
        if (!borrow) {
            for (u32 i = 0; i < 4; ++i) r[i] = delta[i];
        }
    }
    for (u32 i = 0; i < 4; ++i) store8(out + 8 * i, r[i]);
}

/* ---- verification (RFC 8032 section 5.1.7) ---- */

/* The base point of edwards25519, compressed: y = 4/5, x even. */
static const u8 BASE_POINT[32] = {0x58, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66,
                                  0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66,
                                  0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x66,
                                  0x66, 0x66, 0x66, 0x66, 0x66};

rxf_u32 rxf_wrap_ed25519_verify(const u8 *key, const void *data, rxf_u64 bytes,
                                const u8 *signature) {
    fe d, d2, sqrt_m1;
    fe_curve_d(&d);
    fe_add(&d2, &d, &d);
    fe_sqrt_m1(&sqrt_m1);
    if (scalar_at_or_beyond_l(signature + 32)) return 1;
    point a, r, base, sa, ha, right;
    if (point_decompress(&a, key, &d, &sqrt_m1)) return 2;
    if (point_decompress(&r, signature, &d, &sqrt_m1)) return 3;
    if (point_decompress(&base, BASE_POINT, &d, &sqrt_m1)) return 4;
    u8 hash[64], h[32];
    sha512_ctx ctx;
    sha512_init(&ctx);
    sha512_update(&ctx, signature, 32);
    sha512_update(&ctx, key, 32);
    sha512_update(&ctx, data, bytes);
    sha512_final(&ctx, hash);
    scalar_reduce(h, hash);
    point_scalarmult(&sa, signature + 32, &base, &d2);
    point_scalarmult(&ha, h, &a, &d2);
    point_add(&right, &ha, &r, &d2);
    return point_equal(&sa, &right) ? 0 : 5;
}

/* ---- the pinned vectors (the gate) ---- */

static u32 hex_nibble(u8 c) {
    if (c >= '0' && c <= '9') return (u32)(c - '0');
    if (c >= 'a' && c <= 'f') return (u32)(c - 'a') + 10;
    return 0xff;
}

static void hex_decode(u8 *out, const char *hex) {
    for (u32 i = 0; hex[2 * i]; ++i) {
        out[i] = (u8)((hex_nibble((u8)hex[2 * i]) << 4) | hex_nibble((u8)hex[2 * i + 1]));
    }
}

static u32 hex_equal(const u8 *bytes, const char *hex) {
    u8 decoded[64];
    hex_decode(decoded, hex);
    u32 words = 0;
    while (hex[2 * words]) ++words;
    return bytes_equal(bytes, decoded, words);
}

/* One Ed25519 vector: its key, its message, its signature -- and its refusal when
 * one bit of the signature is flipped. */
static u32 ed25519_vector(const char *key_hex, const u8 *message, u32 message_bytes,
                          const char *signature_hex) {
    u8 key[32], signature[64];
    hex_decode(key, key_hex);
    hex_decode(signature, signature_hex);
    if (rxf_wrap_ed25519_verify(key, message, message_bytes, signature) != 0) return 1;
    u8 tampered[64];
    for (u32 i = 0; i < 64; ++i) tampered[i] = signature[i];
    tampered[0] ^= 1;
    return rxf_wrap_ed25519_verify(key, message, message_bytes, tampered) == 0;
}

/*
 * The published vectors: FIPS 180-4 B.1/B.2 (and the empty message) for the hashes,
 * RFC 8032 section 7.1 (the three vectors) for Ed25519, and one vector generated
 * once by the Python seat (src/pymergetic/rxftk/certify/ed25519.py) and pinned here
 * -- provenance labeled honestly, as the recipe demands (docs/26 Recipe 1).
 */
rxf_u32 rxf_wrap_crypto_vectors(void) {
    u32 failed = 0;
    u8 out[64];

    rxf_wrap_sha256("", 0, out);
    failed += !hex_equal(out, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    rxf_wrap_sha256("abc", 3, out);
    failed += !hex_equal(out, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    rxf_wrap_sha256("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq", 56, out);
    failed += !hex_equal(out, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");

    rxf_wrap_sha512("", 0, out);
    failed += !hex_equal(
        out, "cf83e1357eefb8bdf1542850d66d8007d620e4050b5715dc83f4a921d36ce9ce"
             "47d0d13c5d85f2b0ff8318d2877eec2f63b931bd47417a81a538327af927da3e");
    rxf_wrap_sha512("abc", 3, out);
    failed += !hex_equal(
        out, "ddaf35a193617abacc417349ae20413112e6fa4e89a97ea20a9eeee64b55d39a"
             "2192992a274fc1a836ba3c23a3feebbd454d4423643ce80e2a9ac94fa54ca49f");

    /* RFC 8032 section 7.1, vectors 1 to 3. */
    failed += ed25519_vector(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
        (const u8 *)"", 0,
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b");
    failed += ed25519_vector(
        "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c",
        (const u8 *)"\x72", 1,
        "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da"
        "085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00");
    failed += ed25519_vector(
        "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025",
        (const u8 *)"\xaf\x82", 2,
        "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac"
        "18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a");

    /* The self-pinned vector: the Python seat signed this message once; the seed
     * 0x11 repeated is its first test word. */
    failed += ed25519_vector(
        "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737",
        (const u8 *)"rxf wrap crypto", 15,
        "ca05334f3ce257f6b6bf3472d61cfb561738765dbe13da543d5cb2a50c44afaf"
        "2e86948316fd10f72df967f6cd8625665204e63b595be2ee2c0d9d31939eb102");

    return failed;
}
