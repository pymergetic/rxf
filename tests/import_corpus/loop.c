/* The C -> RXF import corpus: loops, which are phi nodes and block parameters of
 * the graph. sum_bits runs exactly x rounds of the mix (none when x is 0) and
 * count_ones walks the bits of x, so the tests keep x of sum_bits small.
 */

#include <stdint.h>

uint64_t sum_bits(uint64_t x, uint64_t acc) {
    for (uint64_t i = 0; i != x; i++) {
        acc = (acc >> 3) + acc;
        acc ^= 0x9E3779B97F4A7C15ULL;
    }
    return acc;
}

uint64_t count_ones(uint64_t x) {
    uint64_t n = 0;
    for (; x; x >>= 1) n += x & 1;
    return n;
}
