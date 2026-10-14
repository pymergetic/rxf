#include <stdint.h>

/* The switch of the subset (Terminator.SwitchTag): the case tags mask to the width of the
   switched value -- an i32 case of -1 is the word 0xffffffff the subset keeps a narrow
   value in, not 2^64 - 1. This is the shape of the MicroPython VM dispatch loop. */

uint64_t classify(int32_t x) {
    switch (x) {
        case -1:
            return 10;
        case 0:
            return 20;
        case 7:
            return 30;
        case 100:
            return 40;
        case 101:
            return 41;
        case 102:
            return 42;
        case 1000:
            return 50;
        case -2000:
            return 60;
        default:
            return 99;
    }
}
