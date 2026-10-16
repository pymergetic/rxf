/* pm_crypto.h — the cap.crypto card's one C ABI. impl = rs (native/crypto).
 *
 * This header is the card's ONLY face: one card, one defining lang (RS), one
 * set of test vectors (docs/25 §11). Python reaches the card through the
 * ctypes fill in src/pymergetic/rxftk/net/crypto.py; TLS and WireGuard lanes
 * code against exactly these names and signatures. Frozen — do not rename.
 *
 * Hash ids:  0 = SHA-256, 1 = BLAKE2s-256.
 * Cipher ids: 0 = CHACHA20-POLY1305, 1 = AES-256-GCM.
 *
 * All functions: 0 = ok, negative = refusal. No global state; init/deinit are
 * the module pair (open closes with deinit — never `fini`; create/destroy is
 * for heap objects and `finish` only completes a buffer/operation, so neither
 * applies here).
 *
 *   0   ok
 *  -1   PM_ERR_ARGUMENT  bad id, null pointer, bad fixed length
 *  -2   PM_ERR_TAG       AEAD open: the trailing tag does not verify
 *  -3   PM_ERR_LOW_ORDER X25519: output all zeros (small-order point)
 *  -4   PM_ERR_LENGTH    HKDF-Expand: out_len over 255 * 32 (RFC 5869)
 *
 * Fixed sizes: key = 32 bytes, nonce = 12 bytes, hash/prk/out(aead tag part)
 * as shown below. Pointer+length pairs accept NULL only when the length is 0.
 * Outputs: sha256/blake2s/blake2s_keyed/x25519_public/x25519 write 32 bytes;
 * callers guarantee the output buffer's size (see each declaration).
 *
 * AEAD: seal writes ciphertext || 16-byte tag and sets *out_len = pt_len + 16;
 * `out` must hold at least pt_len + 16 bytes on entry (`out_len` is the
 * produced length on success, not a capacity). open verifies the trailing tag
 * and writes ct_len - 16 bytes, setting *out_len = ct_len - 16; it refuses
 * with -2 on a bad tag and with -1 when ct_len < 16. The nonce is always
 * 12 bytes.
 *
 * X25519: pm_crypto_x25519 refuses (-3) when the output is all zeros
 * (RFC 7748 §6 — an input point of small order; OR the bytes to check without
 * leaking). pm_crypto_x25519_public is X25519(scalar, 9) and never refuses.
 *
 * Timing: the primitives aim at constant time — see the notes in
 * native/crypto/src/{hkdf,x25519,...}.rs for exactly what is done (the X25519 ladder is
 * mask-swapped and table-free, GHASH runs fixed iterations with masked
 * reduction, tag compares XOR all bytes). One documented caveat: AES uses the
 * standard S-box table, whose index is a secret byte, so AES-256-GCM is not
 * claimed constant-time against cache timing.
 */

#ifndef PM_CRYPTO_H
#define PM_CRYPTO_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The module pair. init refuses (-1) if the card's SHA-256 self-check fails. */
int32_t pm_crypto_init(void);
void pm_crypto_deinit(void);

/* Hashes: write 32 bytes to out. data may be NULL when len is 0. */
void pm_crypto_sha256(uint8_t out[32], const uint8_t *data, size_t len);
void pm_crypto_blake2s(uint8_t out[32], const uint8_t *data, size_t len);
/* Keyed BLAKE2s-256 (RFC 7693); key_len in 1..=32 (the WireGuard MAC form). */
void pm_crypto_blake2s_keyed(uint8_t out[32], const uint8_t *key, size_t key_len,
                             const uint8_t *data, size_t len);

/* X25519 (RFC 7748). */
void pm_crypto_x25519_public(uint8_t out[32], const uint8_t scalar[32]);
int32_t pm_crypto_x25519(uint8_t out[32], const uint8_t scalar[32], const uint8_t point[32]);

/* AEAD (cipher id 0 = CHACHA20-POLY1305, 1 = AES-256-GCM). */
int32_t pm_crypto_aead_seal(int32_t cipher, uint8_t *out, size_t *out_len, const uint8_t key[32],
                            const uint8_t nonce[12], const uint8_t *aad, size_t aad_len,
                            const uint8_t *pt, size_t pt_len);
int32_t pm_crypto_aead_open(int32_t cipher, uint8_t *out, size_t *out_len, const uint8_t key[32],
                            const uint8_t nonce[12], const uint8_t *aad, size_t aad_len,
                            const uint8_t *ct, size_t ct_len);

/* HKDF (RFC 5869). hash id 0 = SHA-256 (as the RFC), 1 = BLAKE2s-256 (this
 * card's HMAC-BLAKE2s instantiation — no RFC; vectors pinned, not claimed).
 * extract: a zero-length salt is the RFC's absent salt (HashLen zeros).
 * expand: refuses (-4) when out_len > 255 * 32. */
int32_t pm_crypto_hkdf_extract(int32_t hash, uint8_t out[32], const uint8_t *salt, size_t salt_len,
                               const uint8_t *ikm, size_t ikm_len);
int32_t pm_crypto_hkdf_expand(int32_t hash, uint8_t *out, size_t out_len, const uint8_t prk[32],
                              const uint8_t *info, size_t info_len);

#ifdef __cplusplus
}
#endif

#endif /* PM_CRYPTO_H */
