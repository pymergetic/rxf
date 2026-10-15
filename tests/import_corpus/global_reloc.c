/* A static initializer the importer lowers (docs/23 6 q4): @picker's initializer is a
   relocation (it names helper), so the cell is placed as its byte image -- the slot the
   zero the flat profile carries, the native static before its loader places the
   relocations -- and the wire of the module's boot (__wires_boot) stores helper's word
   into it. The declaration beside it (elsewhere) still refuses at the use: it has no
   payload this pass can place. */
#include <stdint.h>

static uint64_t helper(uint64_t x) {
    return x + 1;
}

/* the function pointer of MicroPython's mp_plat_print shape: a cell whose initializer is
   the address of another symbol. It is volatile so the load of the cell survives
   opt-level 1 (a constant cell alone folds away) */
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
