#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>

/* A diverging call: whatever the callee's name, a call directly before an unreachable
   lowers to nothing and the unreachable refuses the run (the panic policy of the Rust
   subset, generalized by shape -- this is how every MicroPython mp_raise_* ends). The
   variadic my_raise also pins the call of a variadic function: the IR prints the function
   type between the return type and the callee. */

__attribute__((noinline, noreturn)) void my_raise(int n, ...) {
    (void)n;
    abort();
}

uint64_t guarded(uint64_t x) {
    if (x == 0) {
        my_raise(1, 2, 3);
    }
    return x + 1;
}

/* the same shape under a name the C library owns: assert() calls __assert_fail */
uint64_t asserted(uint64_t x) {
    assert(x != 7);
    return x + 5;
}
