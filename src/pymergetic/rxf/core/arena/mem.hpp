#ifndef PYMERGETIC_RXF_ARENA_MEM_HPP
#define PYMERGETIC_RXF_ARENA_MEM_HPP

/*
 * Byte operations that need nothing from a C library. The freestanding layers use these and
 * never memcpy, memset or memcmp: a native snippet may not call a function it does not contain,
 * and a compiler turns a block copy of unknown size into such a call.
 */

#include <stdint.h>

namespace pymergetic::rxf::core::mem {

#define PYMERGETIC_RXF_INLINE __attribute__((always_inline)) inline

PYMERGETIC_RXF_INLINE void copy(void *to, const void *from, uint64_t count) {
    uint8_t *d = static_cast<uint8_t *>(to);
    const uint8_t *s = static_cast<const uint8_t *>(from);
    for (uint64_t i = 0; i < count; i++) d[i] = s[i];
}

/* Overlapping ranges are fine. */
PYMERGETIC_RXF_INLINE void move(void *to, const void *from, uint64_t count) {
    uint8_t *d = static_cast<uint8_t *>(to);
    const uint8_t *s = static_cast<const uint8_t *>(from);
    if (d <= s) {
        for (uint64_t i = 0; i < count; i++) d[i] = s[i];
    } else {
        for (uint64_t i = count; i != 0; i--) d[i - 1] = s[i - 1];
    }
}

PYMERGETIC_RXF_INLINE void fill(void *to, uint8_t value, uint64_t count) {
    uint8_t *d = static_cast<uint8_t *>(to);
    for (uint64_t i = 0; i < count; i++) d[i] = value;
}

PYMERGETIC_RXF_INLINE int compare(const void *a, const void *b, uint64_t count) {
    const uint8_t *x = static_cast<const uint8_t *>(a);
    const uint8_t *y = static_cast<const uint8_t *>(b);
    for (uint64_t i = 0; i < count; i++)
        if (x[i] != y[i]) return x[i] < y[i] ? -1 : 1;
    return 0;
}

PYMERGETIC_RXF_INLINE uint64_t length(const char *text) {
    uint64_t n = 0;
    while (text[n] != 0) n++;
    return n;
}

} // namespace pymergetic::rxf::core::mem

#endif
