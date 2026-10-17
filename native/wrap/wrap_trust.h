/*
 * The trust anchor of a deployment: the words of docs/11 compiled into the wrapper and
 * never into the image. The build writes the anchor to rxf_wrap_trust.bin in its work
 * directory (the trust store as bytes, binary.builder.trust_block) and assembles
 * wrap_trust.S beside it; every seat that activates an image reads the same two
 * symbols below.
 *
 * The layout, little-endian like the package header (trust_block writes it, this
 * reads it):
 *
 *     0   4   "RXFT"
 *     4   4   version (1)
 *     8   8   now       \
 *     16  8   max_age    > the policy words of the store (docs/11: typed metadata,
 *     24  8   skew      /  never inferred; `now` is a word, the check reads no clock)
 *     32  4   quorum
 *     36  4   key_count
 *     40  4   revoked_count
 *     44  4   zero
 *     48      keys: key_count of RXF_WRAP_TRUST_KEY_BYTES (the key id zero-padded to
 *             RXF_WRAP_TRUST_ID_BYTES, then the 32-byte public word), then the revoked
 *             ids: revoked_count of RXF_WRAP_TRUST_ID_BYTES each.
 */
#ifndef RXF_WRAP_TRUST_H
#define RXF_WRAP_TRUST_H

#include "wrap.h"

#define RXF_WRAP_TRUST_MAGIC "RXFT"
#define RXF_WRAP_TRUST_VERSION 1
#define RXF_WRAP_TRUST_BYTES 48
#define RXF_WRAP_TRUST_KEY_BYTES 96
#define RXF_WRAP_TRUST_ID_BYTES 64

extern const rxf_u8 rxf_wrap_trust_start[];
extern const rxf_u8 rxf_wrap_trust_end[];

#endif
