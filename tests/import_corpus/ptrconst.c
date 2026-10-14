#include <stdint.h>

/* Constant-expression operands: inttoptr constants (the immediate objects of MicroPython's
   REPR A) as a call argument and as the two arms of a select of addresses. */

__attribute__((noinline)) uint64_t addr_val(const void *p) {
    return (uint64_t)(uintptr_t)p;
}

uint64_t pass14(uint64_t x) {
    return addr_val((const void *)14) + x;
}

uint64_t pass30(uint64_t x) {
    return addr_val((const void *)30) + x;
}

uint64_t pick1430(uint64_t c) {
    return addr_val(c ? (const void *)14 : (const void *)30);
}
