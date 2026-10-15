#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

/* The machines behind the ABI obj.c declares and this corpus does not carry
   (py/qstr.c's qstr_str, py/obj.c's mp_obj_len_maybe as its runtime-dispatch self,
   py/objint*.c's mp_obj_int_get_checked, py/runtime.c's mp_unary_op): the
   differential links its own stubs with a corpus file that only declares the ABI,
   the way MicroPython's build keeps each of these in a translation unit of its own
   and the way nlr_call.c links nlr_stubs.c -- so the IR the importer reads keeps
   every one of these calls opaque and refuses at it (tests/test_import.py's REFUSED).

   The class of obj.c never reaches these: its imported members call none of them and
   the members that do refuse at import before the call can run.  A stub that is
   reached is a bug in the test, so every one of them ends the process here (the
   nlr_jump rule). */

__attribute__((noinline, noreturn)) const char *qstr_str(size_t q) {
    (void)q;
    abort();
}

__attribute__((noinline, noreturn)) uintptr_t mp_obj_len_maybe(uintptr_t o_in) {
    (void)o_in;
    abort();
}

__attribute__((noinline, noreturn)) ptrdiff_t mp_obj_int_get_checked(const void *o) {
    (void)o;
    abort();
}

__attribute__((noinline, noreturn)) uintptr_t mp_unary_op(int op, uintptr_t o) {
    (void)op;
    (void)o;
    abort();
}
