/*
 * The verification primitives of the activation seam (native/wrap/wrap_activate.c),
 * freestanding: SHA-256 (the image digest), SHA-512 (the hash of Ed25519) and
 * Ed25519 verification (RFC 8032 section 5.1.7), the scheme the certification
 * voices sign with (src/pymergetic/rxftk/certify/ed25519.py). Verification only:
 * nothing here signs, and no private word ever appears.
 *
 * Correctness is not a matter of trust: `rxf_wrap_crypto_vectors` runs the same
 * published vectors the Python seat pins (FIPS 180-4 for the hashes, RFC 8032
 * section 7.1 for Ed25519) and answers 0 only when every one passes. If a vector
 * disagrees with this code, this code is wrong (docs/26 Recipe 1).
 */
#ifndef RXF_WRAP_CRYPTO_H
#define RXF_WRAP_CRYPTO_H

#include "wrap.h"

/* SHA-256 of `bytes` at `data` into `out` (32 bytes). */
void rxf_wrap_sha256(const void *data, rxf_u64 bytes, rxf_u8 *out);

/* SHA-512 of `bytes` at `data` into `out` (64 bytes). */
void rxf_wrap_sha512(const void *data, rxf_u64 bytes, rxf_u8 *out);

/*
 * Ed25519 verification of `signature` (64 bytes: R || S) over `bytes` at `data`
 * under `key` (32 bytes, the encoded public point). Answers 0 when the signature
 * is the one these bytes carry (RFC 8032 section 5.1.7), anything else to refuse:
 * a non-canonical encoding, a scalar at or beyond the group order, or a mismatch.
 */
rxf_u32 rxf_wrap_ed25519_verify(const rxf_u8 *key, const void *data, rxf_u64 bytes,
                                const rxf_u8 *signature);

/*
 * The pinned vectors (FIPS 180-4 B.1/B.2/B.3 for the hashes, RFC 8032 section
 * 7.1 for Ed25519): answers 0 when every vector passed, else the count that
 * failed. This is the probe of the primitives -- the gate, not a debug aid.
 */
rxf_u32 rxf_wrap_crypto_vectors(void);

#endif
