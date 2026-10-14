/* A cell this pass cannot place: a global whose initializer is a relocation (its bytes
   would be a link-time address this pass cannot know), and a declaration without bytes.
   Both refuse at the use -- the cell has no payload -- rather than guessing. */
#include <stdint.h>

static uint64_t helper(uint64_t x) {
    return x + 1;
}

/* the function pointer of MicroPython's mp_plat_print shape: a cell whose initializer is
   the address of another symbol. It is volatile so the load of the cell survives
   opt-level 1 (a constant cell alone folds away): the refusal is at that load */
static uint64_t (*volatile picker)(uint64_t) = helper;

void reset_picker(void) {
    picker = helper;
}

extern uint64_t elsewhere; /* a declaration without bytes */

uint64_t use_reloc(uint64_t x) {
    return picker(x);
}

uint64_t use_decl(void) {
    return elsewhere;
}
