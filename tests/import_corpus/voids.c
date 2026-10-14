#include <stdint.h>

/* A void body answers 0 -- the run's one word for a function that returns nothing (a
   caller of a void function reads none of it, so the value only fills the run's answer).
   v_store is also the out-parameter idiom: the inlined callee writes through a pointer
   parameter into the caller's wide local. */

__attribute__((noinline)) void v_store(uint64_t *p, uint64_t v) {
    *p = v;
}

uint64_t v_wrap(uint64_t *p, uint64_t v) {
    v_store(p, v);
    return v + 1;
}

__attribute__((noinline)) void v_nothing(void) {
}

uint64_t v_call_nothing(uint64_t x) {
    v_nothing();
    return x + 3;
}
