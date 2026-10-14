#include <stdint.h>

/* Lines the IR writer wraps past its column limit: the ten-argument header and the call
   both split across lines, and the parser joins them before it reads them. */

__attribute__((noinline)) uint64_t ten(uint64_t a, uint64_t b, uint64_t c, uint64_t d,
                                       uint64_t e, uint64_t f, uint64_t g, uint64_t h,
                                       uint64_t i, uint64_t j) {
    return a + b + c + d + e + f + g + h + i + j;
}

uint64_t ten_sum(uint64_t x) {
    return ten(1, 2, 3, 4, 5, 6, 7, 8, 9, 10) + x;
}
