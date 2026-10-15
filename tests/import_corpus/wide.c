#include <stdint.h>

/* A frame over the wall of the old run cell (RXF_GRAPH_FRAME was 1024 bytes): every value of a
   graph takes 8 bytes of the frame, and inlining six bodies of ~30 values each made 1264. The
   import refused wide at compile time (rximport.Unsupported) then rather than misplace the
   frame. REFUSED -> lowered with variable frames: the frames of the run cell are variable-length
   (base, size) links now and the budget is the cell (RXF_GRAPH_CELL_BYTES, 8192), so the same
   body imports and runs -- tests/test_import.py's CASES["wide"] is the differential against the
   natively compiled body of this file. */

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
