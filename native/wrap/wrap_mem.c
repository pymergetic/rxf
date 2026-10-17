/*
 * The freestanding memory words: what the compiler may call in a seat that has no
 * libc -- a boot, a firmware application, a static process. Structure copies and
 * aggregates compile to calls of these names however the source is written
 * (the wrapper's own copies are loops for the same reason), so a wrapper provides
 * them itself: four small loops and no runtime.
 *
 * The signatures are the ones the compiler expects (size_t and int), matched here
 * with the built-in types so no header of any libc is pulled in.
 */
#include "wrap.h"

void *memcpy(void *to, const void *from, __SIZE_TYPE__ bytes) {
    rxf_u8 *out = (rxf_u8 *)to;
    const rxf_u8 *in = (const rxf_u8 *)from;
    while (bytes--)
        *out++ = *in++;
    return to;
}

void *memmove(void *to, const void *from, __SIZE_TYPE__ bytes) {
    rxf_u8 *out = (rxf_u8 *)to;
    const rxf_u8 *in = (const rxf_u8 *)from;
    if (out < in) {
        while (bytes--)
            *out++ = *in++;
    } else {
        out += bytes;
        in += bytes;
        while (bytes--)
            *--out = *--in;
    }
    return to;
}

void *memset(void *to, int byte, __SIZE_TYPE__ bytes) {
    rxf_u8 *out = (rxf_u8 *)to;
    while (bytes--)
        *out++ = (rxf_u8)byte;
    return to;
}

int memcmp(const void *left, const void *right, __SIZE_TYPE__ bytes) {
    const rxf_u8 *a = (const rxf_u8 *)left;
    const rxf_u8 *b = (const rxf_u8 *)right;
    while (bytes--) {
        if (*a != *b)
            return (int)*a - (int)*b;
        ++a;
        ++b;
    }
    return 0;
}
