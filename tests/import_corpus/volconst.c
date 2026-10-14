#include <stdint.h>

/* A volatile local that keeps a constant: the load reads back the literal its own block
   stored there (the shape MicroPython's immediate objects take in a slot at opt-level 0). */

uint64_t vol_const(uint64_t x) {
    volatile uint64_t c = 30;
    return c + x;
}
