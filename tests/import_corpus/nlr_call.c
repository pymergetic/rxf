#include <stdint.h>

/* The nlr ABI of MicroPython (py/nlr.h) in the shape the importer lowers by name: a
   setjmp-style push, a noreturn jump that unwinds the C stack, and a pop. The graph has
   no stack to unwind -- lower_call inlines callees by substitution -- so the push lowers
   to its first-return 0, the pop to nothing, and the jump is the divergence it always is
   in the IR (the unreachable after it refuses the run). The policy and its soundness
   boundary live at ``lower_nlr`` of tools/native/rximport.py; the differential below runs
   the safe path natively and the imported graph, and the raise path refuses the run the
   way every mp_raise_* path does (the status-branch idiom of noreturn.c).

   The ABI is external to every caller: its machine lives in another translation unit
   (nlr_posix.c of MicroPython, nlr_stubs.c of the differential), so the IR keeps every
   call opaque exactly as MicroPython's own build does. */

typedef struct _nlr_buf_t {
    void *jmp; /* the jmp_buf the machine behind the ABI keeps */
    uintptr_t ret_val; /* what nlr_jump carries back into the frame */
} nlr_buf_t;

int nlr_push(nlr_buf_t *buf);
void nlr_pop(nlr_buf_t *buf);
__attribute__((noreturn)) void nlr_jump(void *val);

/* a raiser of MicroPython's shape: noreturn, its body ends in the nlr_jump */
__attribute__((noinline, noreturn)) void nlr_boom(uint64_t code) {
    nlr_jump((void *)(uintptr_t)code);
}

/* the guard idiom of py/runtime.c: the frame is pushed, the work runs, and the raise path
   jumps out of it. The second return of the setjmp (the else below) is dead in the graph
   (the push answers 0 there); it lowers because it touches no buffer -- a body that would
   need the bytes of the frame is refused at import instead (nlr_abuse_* below). */
uint64_t nlr_guarded(uint64_t x) {
    nlr_buf_t frame;
    if (nlr_push(&frame) != 0) {
        return 0;
    }
    if (x == 0) {
        nlr_boom(x);
    }
    nlr_pop(&frame);
    return x + 1;
}

/* the soundness boundary of the policy: the buffer the frame alone keeps alive is opaque,
   and tests/test_import.py asserts these refuse at import rather than guess the bytes the
   unwinding would have carried */
uint64_t nlr_abuse_read(uint64_t x) {
    nlr_buf_t frame;
    nlr_push(&frame);
    return (uint64_t)(uintptr_t)frame.jmp + x; /* only the jump could have written it */
}

uint64_t nlr_abuse_write(uint64_t x) {
    nlr_buf_t frame;
    nlr_push(&frame);
    frame.jmp = (void *)(uintptr_t)x; /* only the jump could read it */
    nlr_pop(&frame);
    return 0;
}
