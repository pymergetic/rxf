#include <stdint.h>

/* The address of the CALLER'S LOCAL handed to an out-of-line call (a call card: the callee is past
   the inline budget, so the caller really calls it) that reads it, writes it, or both. The
   callee's frame is not the caller's: a pointer to a slot of the caller's frame is no address the
   callee can name, so the bytes of the local must reach it as a cell the caller owns, and what
   the callee writes must be what the caller reads back. lwIP's icmp_dest_unreach passes
   &iphdr_src to ip4_output_if_src exactly like read_u32 below (the ICMP reply went to 0.0.0.0).
   The rounds put each callee far past INLINE_WORDS. */

#define ROUND(x) x = (x * 0x9E3779B97F4A7C15ULL) ^ (x >> 29)
#define MANY(x)                                                                                    \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x);                                                                                      \
    ROUND(x)

/* reads and writes through the pointer: the local is both an input and an output */
__attribute__((noinline)) uint64_t rw_u64(uint64_t *p, uint64_t k) {
    uint64_t x = *p ^ k;
    MANY(x);
    *p = x + *p;
    return (x >> 7) + 1;
}

/* reads only: the local is an input (the shape of ip4_output_if_src's dest) */
__attribute__((noinline)) uint64_t read_u32(const uint32_t *p, uint64_t k) {
    uint64_t x = *p ^ k;
    MANY(x);
    return x + *p;
}

/* writes only: the local is an output (the out-param idiom, callee past the inline budget) */
__attribute__((noinline)) void write_u64(uint64_t *out, uint64_t k) {
    uint64_t x = k;
    MANY(x);
    *out = x;
}

uint64_t local_rw(uint64_t a, uint64_t k) {
    uint64_t v = a;
    uint64_t r = rw_u64(&v, k);
    return r ^ v;
}

uint64_t local_read(uint64_t a, uint64_t k) {
    uint32_t v = (uint32_t)a;
    return read_u32(&v, k);
}

uint64_t local_write(uint64_t a, uint64_t k) {
    uint64_t out = a;
    write_u64(&out, k);
    return out + a;
}

/* two calls over the same local: what the first writes is what the second reads */
uint64_t local_twice(uint64_t a, uint64_t k) {
    uint64_t v = a;
    uint64_t r1 = rw_u64(&v, k);
    uint64_t r2 = rw_u64(&v, k ^ 0x55);
    return r1 + r2 + v;
}

/* two locals to one call (the destination of ip4_output_if_src and its source): each rides in
   a cell of its own, and a local that is passed on unchanged stays what it was */
__attribute__((noinline)) uint64_t pick_two(const uint64_t *a, const uint64_t *b, uint64_t k) {
    uint64_t x = *a + 3 * *b + k;
    MANY(x);
    return x ^ *a ^ *b;
}

uint64_t local_pair(uint64_t a, uint64_t k) {
    uint64_t first = a, second = a ^ k;
    uint64_t r = pick_two(&first, &second, k);
    return r + first + second;
}

/* a callee that comes back into its own caller while it still reads the local: the activation
   inside finds the same call site, so the cell that carries the local must come back to the
   outer activation as it was (the local of each activation is its own, as on a C stack).
   The call goes through a runtime word so it stays a call through a word (the boot arms it:
   function_wiring's link fact), the way outparam.c does it. */

static uint64_t (*volatile rec_word)(uint64_t, uint64_t) = 0;

__attribute__((noinline)) uint64_t around(uint64_t *p, uint64_t n, uint64_t k) {
    uint64_t before = *p;
    uint64_t x = before ^ k;
    MANY(x);
    uint64_t inner = n ? rec_word(n - 1, k + 1) : 0;
    return before * 1000003 + *p + (x & 0xFF) + inner;
}

__attribute__((noinline)) uint64_t rec_body(uint64_t n, uint64_t k) {
    uint64_t v = n * 4099 + k;
    uint64_t r = around(&v, n, k);
    return r ^ v;
}

void rec_boot(void) {
    rec_word = rec_body;
}

uint64_t rec_top(uint64_t n, uint64_t k) {
    return rec_word(n, k);
}
