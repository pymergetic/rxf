#include <stdint.h>
#include <stdlib.h>

/* A body whose every path panics has no Return of its own: it imports as a graph that
   refuses every run (RXF_REFUSE_UNSUPPORTED), the Refuse-only shape the panic policy
   needs (every MicroPython mp_raise_* definition is this shape). A body with a path that
   flows on and still answers nothing (an endless loop) stays refused at import. */

__attribute__((noinline, noreturn)) void my_boom(int n) {
    (void)n;
    abort();
}

__attribute__((noreturn)) void always_booms(uint64_t x) {
    my_boom((int)x);
}

/* an endless loop answers nothing: with no Return and a cycle the entry reaches, it
   stays refused at import rather than importing as a graph that refuses every run */
__attribute__((noinline)) uint64_t never_answers(uint64_t x) {
    for (;;) {
        x += 1;
    }
}
