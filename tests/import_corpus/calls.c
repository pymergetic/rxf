#include <stdint.h>

/* Calls between imported functions: the composer's inline path. The helpers are noinline
   so clang leaves the calls in the IR: this pass inlines them into the caller's graph.
   ping and pong are refused at import (mutual recursion). count_down becomes a loop at -O1
   (clang turns its tail call into a loop), so it is a case like any other. The helpers take
   names no C library has: the differential calls them through dlsym, where a libc name
   (glibc has a POSIX `step`) would shadow them and crash the native side. */

__attribute__((noinline)) uint64_t blend(uint64_t a, uint64_t b) {
    return (a & 3) + (b >> 1);
}

__attribute__((noinline)) uint64_t nudge(uint64_t x) {
    return blend(x, x + 7);
}

__attribute__((noinline)) uint64_t chain(uint64_t a, uint64_t b) {
    return blend(nudge(a), blend(a, b));
}

__attribute__((noinline)) uint64_t twice(uint64_t a) {
    return nudge(a) + nudge(a + 1);
}

__attribute__((noinline)) uint64_t count_down(uint64_t n) {
    if (n == 0) {
        return 0;
    }
    return count_down(n - 1);
}

__attribute__((noinline)) uint64_t ping(uint64_t n);

__attribute__((noinline)) uint64_t pong(uint64_t n) {
    if (n == 0) {
        return 1;
    }
    return ping(n - 1);
}

__attribute__((noinline)) uint64_t ping(uint64_t n) {
    if (n == 0) {
        return 0;
    }
    return pong(n - 1);
}
