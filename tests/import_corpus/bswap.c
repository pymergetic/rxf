#include <stdint.h>

/* llvm.bswap.i16 / .i32 / .i64: the byte swaps clang -O1 writes for __builtin_bswapN and for
   the shift-and-or idiom lwIP's lwip_htons / lwip_htonl / PP_HTONS spell out. Each answers
   one canonical 64-bit word: the swapped word of its width, zero-extended. The inputs past
   the width of the narrow ones are part of the contract (the parameter truncates them). */

uint64_t bswap16(uint64_t x) {
    return __builtin_bswap16((uint16_t)x);
}

uint64_t bswap32(uint64_t x) {
    return __builtin_bswap32((uint32_t)x);
}

uint64_t bswap64(uint64_t x) {
    return __builtin_bswap64(x);
}

/* the idiom of lwip_htons: clang's InstCombine recognizes it as llvm.bswap.i16 */
uint64_t htons_idiom(uint64_t x) {
    uint16_t v = (uint16_t)x;
    return (uint16_t)(((v & 0xff) << 8) | ((v & 0xff00) >> 8));
}

/* the idiom of lwip_htonl */
uint64_t htonl_idiom(uint64_t x) {
    uint32_t v = (uint32_t)x;
    return ((v & 0x000000ffUL) << 24) | ((v & 0x0000ff00UL) << 8) | ((v & 0x00ff0000UL) >> 8) |
           ((v & 0xff000000UL) >> 24);
}

/* a swap that feeds more arithmetic: the swap of a sum (the swapped word is an operand, not
   the answer) and a swapped compare, the shape of a header field read in host order */
uint64_t swap_sum(uint64_t x, uint64_t y) {
    uint16_t sum = (uint16_t)(__builtin_bswap16((uint16_t)x) + __builtin_bswap16((uint16_t)y));
    return __builtin_bswap16(sum);
}

uint64_t swap_less(uint64_t x, uint64_t y) {
    return __builtin_bswap32((uint32_t)x) < __builtin_bswap32((uint32_t)y);
}

/* a swap applied twice is the identity: the lowering must not leave the word dirty */
uint64_t swap_twice32(uint64_t x) {
    return __builtin_bswap32(__builtin_bswap32((uint32_t)x) + 1u);
}
