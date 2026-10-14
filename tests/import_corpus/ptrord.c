#include <stdint.h>

/* The ordered compare of two address words: the C that range-checks an immediate-object
   word arrives as an icmp of ptr (MicroPython's mp_binary_op compares mp_obj_t words as
   unsigned). An unsigned order of two addresses lowers as less_uint64_t on the words they
   carry; a signed order of addresses stays outside the subset. The C orders the uintptr_t
   values, which is a defined integer compare of the addresses. */

uint64_t ptr_ult(const void *a, const void *b) {
    return (uintptr_t)a < (uintptr_t)b;
}

uint64_t ptr_uge(const void *a, const void *b) {
    return (uintptr_t)a >= (uintptr_t)b;
}

uint64_t ptr_ule(const void *a, const void *b) {
    return (uintptr_t)a <= (uintptr_t)b;
}

uint64_t ptr_ugt(const void *a, const void *b) {
    return (uintptr_t)a > (uintptr_t)b;
}

/* the mp_binary_op range check shape: low <= p < high */
uint64_t ptr_between(const void *p, const void *low) {
    return (uintptr_t)p >= (uintptr_t)low && (uintptr_t)p < (uintptr_t)low + 32;
}
