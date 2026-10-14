#include <stdint.h>
#include <stdlib.h>

/* The machine behind the nlr ABI (py/nlr.h): the differential's own, linked with a corpus
   file that only declares the ABI. MicroPython keeps this in another translation unit too
   (nlr_posix.c), so the IR the importer reads has the calls of nlr_call.c opaque -- the
   setjmp of the push is never a constant the optimizer could fold away. */

typedef struct _nlr_buf_t {
    void *jmp;
    uintptr_t ret_val;
} nlr_buf_t;

__attribute__((noinline)) int nlr_push(nlr_buf_t *buf) {
    buf->ret_val = 0;
    return 0; /* the first return of the setjmp */
}

__attribute__((noinline)) void nlr_pop(nlr_buf_t *buf) {
    buf->ret_val = 0;
}

__attribute__((noinline, noreturn)) void nlr_jump(void *val) {
    /* the longjmp of the machine: the differential never takes this path (the tests run
       the raise path one-sidedly, like every panic path), so it ends the process here */
    (void)val;
    abort();
}
