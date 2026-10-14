#include <stdint.h>

/* A frame over the budget of a run (RXF_GRAPH_FRAME is 1024 bytes): every value of a graph
   takes 8 bytes of the frame, and inlining six bodies of ~30 values each is past 1024. The
   import refuses wide at compile time (rximport.Unsupported) rather than misplace the frame. */

static __attribute__((noinline)) uint64_t b(uint64_t x) {
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
    return t;
}

uint64_t wide(uint64_t x) {
    return b(b(b(b(b(b(x))))));
}
