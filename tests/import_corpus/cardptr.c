#include <stdint.h>

/* A pointer answered by an out-of-line call card (the callee is past the inline budget, so the
   caller really calls it) and then used as an ADDRESS: stored through, subtracted from the base
   it came from, compared. The card's answer arrives in a wide carrier whose first bytes are the
   address word; a lowering that hands the carrier itself to the next operation reads the
   carrier's own place in the frame instead of the flat address it holds (the defect this file
   pins, found where lwIP's pbuf_alloc answers a pbuf out of line). The pick below mixes the
   seed with enough rounds that its frame is far past INLINE_WORDS. */

#define ROUND(x) x = (x * 0x9E3779B97F4A7C15ULL) ^ (x >> 29)

__attribute__((noinline)) uint8_t *pick(uint8_t *base, uint64_t seed) {
    uint64_t x = seed | 1;
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    ROUND(x);
    return base + (x & 7);
}

/* the byte the card's pointer names is written; the offset the pointer has from its base and
   the byte read back through a second path answer together */
uint64_t card_ptr_store(uint8_t *base, uint64_t seed) {
    uint8_t *p = pick(base, seed);
    *p = (uint8_t)(seed >> 8) | 0x80;
    return (uint64_t)(p - base) | ((uint64_t)base[(p - base) & 7] << 8) |
           ((uint64_t)(p == base + 3) << 16);
}

/* the pointer as arithmetic only: no memory moves, the address difference is the answer */
uint64_t card_ptr_offset(uint8_t *base, uint64_t seed) {
    uint8_t *p = pick(base, seed);
    return (uint64_t)(p - base) + (uint64_t)(p != base) * 100;
}
