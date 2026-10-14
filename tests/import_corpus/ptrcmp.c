#include <stdint.h>

/* The pointer compares of the subset: an eq/ne of two address words is the word compare of
   the flat profile (MicroPython's mp_obj_t is a ptr in the IR: every immediate-object
   compare arrives as an icmp of ptr). ptr_is14 compares against an inttoptr constant
   expression (the REPR A immediate objects are spelled that way). */

uint64_t ptr_same(const void *a, const void *b) {
    return a == b;
}

uint64_t ptr_diff(const void *a, const void *b) {
    return a != b;
}

uint64_t ptr_null(const void *p) {
    return p == 0;
}

uint64_t ptr_is14(const void *p) {
    return p == (const void *)14;
}
