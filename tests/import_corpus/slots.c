#include <stdint.h>

#include "../../native/stdlib_memory.h"

/* The locals of a function. vol_round keeps its scalars in slots (what a store named, a load
   reads back; the frame is the machine's stack). local_round takes the address of its locals:
   they are wide slots of the frame, and rxf_memory_copy reads and writes their bytes. */

uint64_t vol_round(uint64_t a, uint64_t b) {
    volatile uint64_t t = a;
    t += b;
    t ^= 3;
    return t;
}

uint64_t local_round(uint64_t a, uint64_t b) {
    uint64_t t = a;
    uint64_t u = b;
    rxf_memory_copy((rxf_u8 *)&t, 16, (const rxf_u8 *)&u, 16, 8);
    return t;
}

__attribute__((noinline)) uint64_t bump(uint64_t x) {
    volatile uint64_t t = x;
    t += 1;
    t += 2;
    t += 3;
    t += 4;
    t += 5;
    t += 6;
    t += 7;
    t += 8;
    t += 9;
    t += 10;
    t += 11;
    t += 12;
    t += 13;
    t += 14;
    t += 15;
    t += 16;
    t += 17;
    t += 18;
    t += 19;
    t += 20;
    t += 21;
    t += 22;
    t += 23;
    t += 24;
    t += 25;
    t += 26;
    t += 27;
    t += 28;
    t += 29;
    t += 30;
    return t;
}
