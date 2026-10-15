#include <stdint.h>

/* The out-parameter idiom: a local's address reaches a function of the module whose
   pointer parameter the inlined callee writes through (the shape of mp_obj_get_int_maybe
   and kin). The locals are wide slots of the frame; the callee's stores land in them and
   the loads after the call read them back. */

__attribute__((noinline)) void get_pair(uint64_t x, uint64_t *lo, uint64_t *hi) {
    *lo = x & 0xFFFF;
    *hi = x >> 16;
}

uint64_t sum_pair(uint64_t x) {
    uint64_t lo, hi;
    get_pair(x, &lo, &hi);
    return lo + hi;
}

/* the other half of the idiom: the out-param callee as a call card of its own.  The
   boot wires one word -- pair_word -- and the call through it dispatches to pair_out's
   body as a program (RXF_GRAPH_CALL), never an inline of the caller's blocks.  The word
   is volatile so the call through it stays a call through a runtime word (the store the
   boot makes is what arms it, `function_wiring`'s link fact) -- clang folds a static
   function pointer whose only store it can see into a direct call otherwise, and the
   inline of the callee would hide the card the model is about.  The body crosses a card
   boundary itself (the cross-module face of the qstr corpus), so what it writes through
   its pointer parameters is the out cell of docs/23 4.1 -- the Object its caller passes,
   its handle three words -- never a raw pointer across the boundary. */

extern uint64_t qstr_hash(uint64_t);  /* the face of another translation unit */

__attribute__((noinline)) void pair_out(uint64_t x, uint64_t *lo, uint64_t *hi) {
    qstr_hash(x);  /* a call card of its own: the body crosses the boundary */
    *lo = x & 0xFFFF;
    *hi = x >> 16;
}

static void (*volatile pair_word)(uint64_t, uint64_t *, uint64_t *) = 0;

void outparam_port_boot(void) {
    pair_word = pair_out;
}

uint64_t sum_pair_card(uint64_t x) {
    uint64_t lo, hi;
    pair_word(x, &lo, &hi);
    return lo + hi;
}
