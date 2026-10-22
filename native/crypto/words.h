/* The words of the card's programs, embedded at build time (build/crypto_words.c, generated
 * the .rxfs sources of native/crypto by tools/native/crypto_build.py -- bytes, never a
 * second copy of a program) and found by name by the C fill (pm_crypto.c). */
#ifndef PM_CRYPTO_WORDS_H
#define PM_CRYPTO_WORDS_H

#include "../graph.h"

typedef struct {
    const char *name;
    const rxf_u64 *words;
    rxf_u64 words_bytes;
} pm_words;

extern const pm_words pm_words_table[];

#endif /* PM_CRYPTO_WORDS_H */
