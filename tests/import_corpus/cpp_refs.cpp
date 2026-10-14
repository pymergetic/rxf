/* C++ of the subset: extern "C" entry points around the C++ shapes the flat profile
   covers. A reference is the address of its object (a pointer parameter of the IR), a
   method is its this pointer plus the fields the body reads and writes, and a bool
   parameter is the i1 the ABI zero-extends (the corpus feeds 0/1 for it). */

#include <stdint.h>

extern "C" uint64_t ref_add(uint64_t& a, uint64_t b) {
    a += b;
    return a;
}

extern "C" uint64_t bool_pick(bool a, bool b, uint64_t x) {
    return a ? (x + (b ? 1 : 2)) : x;
}

struct Counter {
    uint64_t n;
    __attribute__((noinline)) uint64_t bump(uint64_t d) {
        n += d;
        return n;
    }
};

extern "C" uint64_t counter_bump(void* c, uint64_t d) {
    return static_cast<Counter*>(c)->bump(d);
}
