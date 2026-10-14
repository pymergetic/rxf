#include <stdint.h>

/* The out-parameter idiom: a local's address reaches a function of the module whose
   pointer parameter the inlined callee writes through (the shape of mp_obj_get_int_maybe
   and kin). The locals are wide slots of the frame; the callee's stores land in them and
   the loads after the call read them back. */

__attribute__((noinline)) void get_pair(uint64_t x, uint64_t *lo, uint64_t *hi) {
    *lo = x & 0xFFFF;
    *hi = x >> 16;
}

uint64_t sum_pair(uint64_t x) {
    uint64_t lo, hi;
    get_pair(x, &lo, &hi);
    return lo + hi;
}
