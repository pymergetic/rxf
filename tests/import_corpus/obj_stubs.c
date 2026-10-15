#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

/* The machines behind the ABI obj.c declares and this corpus does not carry
   (py/qstr.c's accessors, py/objint*.c's mp_obj_int_get_checked, py/runtime.c's
   mp_raise_* and py/objstr.c's mp_obj_str_binary_op, whose word the str type's
   binary_op slot carries but whose body is another class's): the differential links
   its own stubs with a corpus file that only declares the ABI, the way MicroPython's
   build keeps each of these in a translation unit of its own and the way nlr_call.c
   links nlr_stubs.c -- so the IR the importer reads keeps every one of these calls
   opaque and refuses at it (tests/test_import.py's REFUSED).

   The class of obj.c reaches these only where a case walks off its own paths (a
   raise, a qstr face where no qstr corpus is linked): a stub that is reached is the
   end of the process here (the nlr_jump rule). */

__attribute__((noinline, noreturn)) const char *qstr_str(size_t q) {
    (void)q;
    abort();
}

__attribute__((noinline, noreturn)) size_t qstr_hash(size_t q) {
    (void)q;
    abort();
}

__attribute__((noinline, noreturn)) size_t qstr_compute_hash(const unsigned char *data, size_t len) {
    (void)data;
    (void)len;
    abort();
}

__attribute__((noinline, noreturn)) const unsigned char *qstr_data(size_t q, size_t *len) {
    (void)q;
    (void)len;
    abort();
}

__attribute__((noinline, noreturn)) size_t qstr_len(size_t q) {
    (void)q;
    abort();
}

__attribute__((noinline, noreturn)) ptrdiff_t mp_obj_int_get_checked(const void *o) {
    (void)o;
    abort();
}

__attribute__((noinline, noreturn)) void mp_raise_msg(const void *exc_type, const char *msg) {
    (void)exc_type;
    (void)msg;
    abort();
}

__attribute__((noinline, noreturn)) void mp_raise_msg_varg(const void *exc_type, const char *fmt, ...) {
    (void)exc_type;
    (void)fmt;
    abort();
}

__attribute__((noinline)) uintptr_t mp_obj_str_binary_op(uintptr_t o1, uintptr_t o2, int op) {
    (void)o1;
    (void)o2;
    (void)op;
    return 0;
}
