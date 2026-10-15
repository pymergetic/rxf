/* The obj core of MicroPython (py/obj.c) over a bounded-arena port, as the corpus of
   the importer: every body below the scaffolding is the verbatim body of py/obj.c, and
   the scaffolding is what a port supplies (py/obj.h's and py/mpconfigport.h's face).
   The differential compiles this file twice -- tools/native/rximport.py's compile_c
   defines RXF_IMPORT_FACES and tests/test_import.py links the same source without it.
   The class needs no libc face (its faces are the port's own below), so both builds
   lower the same words.

   The class is the object constructors and the type tests that dispatch on the
   @mp_type_* globals: mp_obj_malloc_helper (the ctor that stamps base->type) and
   mp_obj_get_type (the type test -- the REPR A tag dispatch, else the header read).
   What refuses around them is pinned in tests/test_import.py's REFUSED: the type ->
   name path (mp_obj_get_type_str -> qstr_str, the interning of the qstr corpus -- its
   need is call support between imported graphs, the design note of the slice) and the
   runtime-dispatch class (mp_obj_is_true, mp_obj_get_int_maybe: the slots and the
   objint face, the runtime dispatch of slices 4-5).

   The scaffolding, by the piece it stands in for:

     config       REPR A as docs/20 S11 decides it -- bit 0 set is a small int and
                  needs no cell, everything else is a cell of the arena -- which is
                  MICROPY_OBJ_IMMEDIATE_OBJS 0: the immediate qstr/bool/None words of
                  stock REPR A are off and None/False/True are the ROM objects of
                  objnone.c/objbool.c, whose header names their type.  Floats off.
     the types    mp_obj_type_t of py/obj.h as the bodies see it (->name, the slot
                  index words), the REPR A tag macros of obj.h verbatim, and the slot
                  macros of obj.h verbatim (MP_OBJ_TYPE_HAS_SLOT and friends)
     the heap     m_malloc of py/misc.h over a bounded arena of one global buffer
                  (224 bytes: the arena is a cell of the image and the world's
                  payload room is 3840 bytes whose starter cells leave 528 -- the
                  class's cells take 280 of them (the five types and three ROM
                  objects of the face, the four marker classes' header words, the
                  bump's counter) and this arena the rest; the gate bumps five
                  24-byte objects, 160 bytes of the arena)
                  -- the qstr corpus's port allocator
     the qstrs    the name of a type is a qstr; the corpus's enum stands in for
                  genhdr's (nothing of the class reads it -- mp_obj_get_type_str, the
                  reader, is refused at qstr_str before its bytes matter)
     the type     the @mp_type_* globals are static const data whose initializers
     cells        carry link-time addresses (every type is its own object with
                  .base = { &mp_type_type }, and the ROM objects name their type):
                  the flat profile places a cell's payload as bytes and refuses a
                  relocation it cannot know (global_reloc.c), so this port builds the
                  cells the way qstr.c's pool is built -- a byte image with the
                  pointer words zero, and obj_port_boot stores the addresses the
                  static const initializers would carry before anything runs

   What is not here, by choice: the immediate-objects dispatch table of stock REPR A
   (mp_obj_get_type's static const types[] -- an initializer that is a relocation, and
   no body-local static is a place a boot can reach; the config above is the run-cell
   machine's own REPR A), the slots of every type (NULL: the class calls through
   none -- the runtime dispatch of slices 4-5 owns the calls and the function words
   behind them), floats, and the objint face behind mp_obj_get_int_maybe. */

#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>

/* ---- the port's config (py/mpconfig.h's CORE_FEATURES level) ---- */

#define MICROPY_OBJ_REPR_A (1)
#define MICROPY_OBJ_REPR_B (2)
#define MICROPY_OBJ_REPR_C (3)
#define MICROPY_OBJ_REPR_D (4)
#define MICROPY_OBJ_REPR (MICROPY_OBJ_REPR_A)
/* docs/20 S11's REPR A: only a small int is immediate (bit 0), everything else is a
   cell of the arena -- a handle word in the run-cell machine, never the immediate
   qstr/bool/None word of stock REPR A */
#define MICROPY_OBJ_IMMEDIATE_OBJS (0)
#define MICROPY_PY_BUILTINS_FLOAT (0)
#define MICROPY_ENABLE_FINALISER (0)
#define MICROPY_DEBUG_MP_OBJ_SENTINELS (0)

/* the error reporting of py/mpconfig.h: NORMAL keeps the full raise paths of the
   bodies (the %q of a unary op's method name, the type name of its argument) */
#define MICROPY_ERROR_REPORTING_TERSE (1)
#define MICROPY_ERROR_REPORTING_NORMAL (2)
#define MICROPY_ERROR_REPORTING_DETAILED (3)
#define MICROPY_ERROR_REPORTING (MICROPY_ERROR_REPORTING_TERSE)
#define MICROPY_PY_ALL_SPECIAL_METHODS (1)
#define MICROPY_PY_SYS_GETSIZEOF (0)
#define MICROPY_PY_BUILTINS_STR_UNICODE (0)

#define MICROPY_WRAP_MP_OBJ_GET_TYPE(f) (f)
#define MP_NOINLINE __attribute__((noinline))

typedef size_t mp_uint_t;
typedef ptrdiff_t mp_int_t;
typedef void *mp_obj_t;
typedef const void *mp_const_obj_t;

/* ---- the object model of py/obj.h: the base, the type, the REPR A tags ---- */

typedef struct _mp_obj_type_t mp_obj_type_t;

typedef struct _mp_obj_base_t {
    const mp_obj_type_t *type;
} mp_obj_base_t;

/* the type of py/obj.h: the header of an object, then the flags and the name (a
   qstr), then the slot index words, then the slots the class calls through (the
   slots[] of obj.h is variable; this port gives it the two words its own types
   wire -- the unary route of the class and the str type's binary_op word) */
struct _mp_obj_type_t {
    mp_obj_base_t base;
    uint16_t flags;
    uint16_t name;
    uint8_t slot_index_make_new;
    uint8_t slot_index_print;
    uint8_t slot_index_call;
    uint8_t slot_index_unary_op;
    uint8_t slot_index_binary_op;
    uint8_t slot_index_attr;
    uint8_t slot_index_subscr;
    uint8_t slot_index_iter;
    uint8_t slot_index_buffer;
    uint8_t slot_index_protocol;
    uint8_t slot_index_parent;
    uint8_t slot_index_locals_dict;
    const void *slots[2];
};

/* the operator words of py/runtime0.h, verbatim */
typedef enum {
    // These ops may appear in the bytecode. Changing this group
    // in any way requires changing the bytecode version.
    MP_UNARY_OP_POSITIVE,
    MP_UNARY_OP_NEGATIVE,
    MP_UNARY_OP_INVERT,
    MP_UNARY_OP_NOT,

    // Following ops cannot appear in the bytecode
    MP_UNARY_OP_BOOL, // __bool__
    MP_UNARY_OP_LEN, // __len__
    MP_UNARY_OP_HASH, // __hash__; must return a small int
    MP_UNARY_OP_ABS, // __abs__
    MP_UNARY_OP_INT_MAYBE, // __int__; must return MP_OBJ_NULL, or an object satisfying mp_obj_is_int()
    MP_UNARY_OP_FLOAT_MAYBE, // __float__
    MP_UNARY_OP_COMPLEX_MAYBE, // __complex__
    MP_UNARY_OP_SIZEOF, // for sys.getsizeof()
} mp_unary_op_t;

typedef mp_obj_t (*mp_unary_op_fun_t)(mp_unary_op_t op, mp_obj_t);
typedef mp_obj_t (*mp_obj_binary_op_fun_t)(mp_obj_t, mp_obj_t, int);

/* the slot macros of py/obj.h, verbatim */
#define MP_OBJ_TYPE_HAS_SLOT(t, f) ((t)->slot_index_##f)
#define _MP_OBJ_TYPE_SLOT_TYPE_unary_op (mp_unary_op_fun_t)
#define _MP_OBJ_TYPE_SLOT_TYPE_binary_op (mp_obj_binary_op_fun_t)
#define MP_OBJ_TYPE_GET_SLOT(t, f) (_MP_OBJ_TYPE_SLOT_TYPE_##f(t)->slots[(t)->slot_index_##f - 1])
#define MP_OBJ_TYPE_GET_SLOT_OR_NULL(t, f) (_MP_OBJ_TYPE_SLOT_TYPE_##f(MP_OBJ_TYPE_HAS_SLOT(t, f) ? MP_OBJ_TYPE_GET_SLOT(t, f) : NULL))

/* the REPR A object words of py/obj.h, verbatim */
#define MP_OBJ_NULL (MP_OBJ_FROM_PTR((void *)0))

static inline bool mp_obj_is_small_int(mp_const_obj_t o) {
    return (((mp_int_t)(o)) & 1) != 0;
}
#define MP_OBJ_SMALL_INT_VALUE(o) (((mp_int_t)(o)) >> 1)
#define MP_OBJ_NEW_SMALL_INT(small_int) ((mp_obj_t)((((mp_uint_t)(small_int)) << 1) | 1))

static inline bool mp_obj_is_qstr(mp_const_obj_t o) {
    return (((mp_int_t)(o)) & 7) == 2;
}
#define MP_OBJ_QSTR_VALUE(o) (((mp_uint_t)(o)) >> 3)
#define MP_OBJ_NEW_QSTR(qst) ((mp_obj_t)((((mp_uint_t)(qst)) << 3) | 2))

static inline __attribute__((unused)) bool mp_obj_is_immediate_obj(mp_const_obj_t o) {
    return (((mp_int_t)(o)) & 7) == 6;
}
#define MP_OBJ_IMMEDIATE_OBJ_VALUE(o) (((mp_uint_t)(o)) >> 3)

static inline bool mp_obj_is_obj(mp_const_obj_t o) {
    return (((mp_int_t)(o)) & 3) == 0;
}

#define MP_OBJ_TO_PTR(o) ((void *)(uintptr_t)(o))
#define MP_OBJ_FROM_PTR(p) ((mp_obj_t)((uintptr_t)(p)))

/* the type tests of py/obj.h that read the header directly */
#define mp_obj_is_exact_type(o, t) (mp_obj_is_obj(o) && (((mp_obj_base_t *)MP_OBJ_TO_PTR(o))->type == (t)))

/* the None/False/True of objnone.c/objbool.c: ROM objects whose header names their
   type (MICROPY_OBJ_IMMEDIATE_OBJS 0 -- the words of stock REPR A are handle words
   in docs/20 S11's machine) */
typedef struct _mp_obj_none_t {
    mp_obj_base_t base;
} mp_obj_none_t;

typedef struct _mp_obj_bool_t {
    mp_obj_base_t base;
    bool value;
} mp_obj_bool_t;

#define mp_const_none (MP_OBJ_FROM_PTR(&mp_const_none_obj))
#define mp_const_false (MP_OBJ_FROM_PTR(&mp_const_false_obj))
#define mp_const_true (MP_OBJ_FROM_PTR(&mp_const_true_obj))

/* ---- the unary route: the small ints, the slots and the strings the bodies read ---- */

/* the small-int words of py/smallint.h, verbatim (the 63-bit REPR A small int) */
#define MP_OBJ_WORD_MSBIT_HIGH (((mp_uint_t)1) << (sizeof(mp_uint_t) * 8 - 1))
#define MP_SMALL_INT_MIN ((mp_int_t)(((mp_int_t)MP_OBJ_WORD_MSBIT_HIGH) >> 1))
#define MP_SMALL_INT_FITS(n) ((((n) ^ ((mp_uint_t)(n) << 1)) & MP_OBJ_WORD_MSBIT_HIGH) == 0)
#define MP_SMALL_INT_POSITIVE_MASK ~(MP_OBJ_WORD_MSBIT_HIGH | (MP_OBJ_WORD_MSBIT_HIGH >> 1))

/* the operator counts of py/runtime0.h, verbatim */
#define MP_UNARY_OP_NUM_BYTECODE (MP_UNARY_OP_NOT + 1)
#define MP_UNARY_OP_NUM_RUNTIME (MP_UNARY_OP_SIZEOF + 1)

/* the error words of py/runtime.h and the debug print of py/vm.c's dispatch */
typedef const char *mp_rom_error_text_t;
#define MP_ERROR_TEXT(s) (s)
#define DEBUG_OP_printf(...) (void)0

/* the type tests of py/obj.h, verbatim (the asserts of the debug build are the
   words the bodies see: the test of the header against one named type) */
#define mp_obj_is_type(o, t) (mp_obj_is_exact_type(o, t))
#define mp_obj_is_str(o) (mp_obj_is_qstr(o) || mp_obj_is_exact_type(o, &mp_type_str))
// https://github.com/micropython/micropython/issues/19048
#define mp_obj_is_str_or_bytes(o) \
    (mp_obj_is_qstr(o) || (mp_obj_is_obj(o) && \
        MP_OBJ_TYPE_GET_SLOT_OR_NULL(((mp_obj_base_t *)MP_OBJ_TO_PTR(o))->type, binary_op) == mp_obj_str_binary_op))

extern mp_obj_none_t mp_const_none_obj;
extern mp_obj_bool_t mp_const_false_obj;
extern mp_obj_bool_t mp_const_true_obj;

static inline mp_obj_t mp_obj_new_bool(mp_int_t x) {
    return x ? mp_const_true : mp_const_false;
}

/* the strings of py/objstr.h (the non-immediate REPR A form), verbatim */
typedef unsigned char byte;

typedef struct _mp_obj_str_t {
    mp_obj_base_t base;
    size_t hash;
    // len == number of bytes used in data, alloc = len + 1 because (at the moment) we
    // also append a null byte
    size_t len;
    const byte *data;
} mp_obj_str_t;

#define MP_STATIC_ASSERT_STR_ARRAY_COMPATIBLE ((void)0)

// use this macro to extract the string hash
#define GET_STR_HASH(str_obj_in, str_hash) \
    size_t str_hash; \
    if (mp_obj_is_qstr(str_obj_in)) { \
        str_hash = qstr_hash(MP_OBJ_QSTR_VALUE(str_obj_in)); \
    } else { \
        str_hash = ((mp_obj_str_t *)MP_OBJ_TO_PTR(str_obj_in))->hash; \
    }

// use this macro to extract the string length
#define GET_STR_LEN(str_obj_in, str_len) \
    size_t str_len; \
    if (mp_obj_is_qstr(str_obj_in)) { \
        str_len = qstr_len(MP_OBJ_QSTR_VALUE(str_obj_in)); \
    } else { \
        str_len = ((mp_obj_str_t *)MP_OBJ_TO_PTR(str_obj_in))->len; \
    }

// use this macro to extract the string data and length
#define GET_STR_DATA_LEN(str_obj_in, str_data, str_len) \
    const byte *str_data; \
    size_t str_len; \
    if (mp_obj_is_qstr(str_obj_in)) { \
        str_data = qstr_data(MP_OBJ_QSTR_VALUE(str_obj_in), &str_len); \
    } else { \
        MP_STATIC_ASSERT_STR_ARRAY_COMPATIBLE; \
        str_len = ((mp_obj_str_t *)MP_OBJ_TO_PTR(str_obj_in))->len; \
        str_data = ((mp_obj_str_t *)MP_OBJ_TO_PTR(str_obj_in))->data; \
    }

/* the value read of py/objbool.c, verbatim (the non-immediate form) */
#define BOOL_VALUE(o) (((mp_obj_bool_t *)MP_OBJ_TO_PTR(o))->value)

/* ---- the port's heap: one bounded arena, the m_malloc of py/misc.h over it ---- */

#define ARENA_BYTES 224
static unsigned char m_arena[ARENA_BYTES];
static size_t m_arena_used; /* the bump: everything below is handed out */

void *m_malloc_maybe(size_t n) {
    size_t want = (n + 15) & ~(size_t)15;
    if (want > ARENA_BYTES - m_arena_used) {
        return NULL;
    }
    void *p = &m_arena[m_arena_used];
    m_arena_used += want;
    return p;
}

__attribute__((noinline)) _Noreturn void m_malloc_fail(size_t n) {
    (void)n;
    abort();
}

void *m_malloc(size_t n) {
    void *p = m_malloc_maybe(n);
    if (p == NULL) {
        m_malloc_fail(n);
    }
    return p;
}

/* ---- the qstrs: the name of a type is a qstr (genhdr's enum, small ---- */

enum {
    MP_QSTRnull = 0,
    MP_QSTR_type = 1,
    MP_QSTR_int = 2,
    MP_QSTR_str = 3,
    MP_QSTR_bool = 4,
    MP_QSTR_NoneType = 5,
    MP_QSTR_TypeError = 6,
    MP_QSTR_OverflowError = 7,
    MP_QSTR_ValueError = 8,
    MP_QSTR___bool__ = 9,
    MP_QSTR___len__ = 10,
    MP_QSTR___hash__ = 11,
    MP_QSTR___int__ = 12,
    MP_QSTR___pos__ = 13,
    MP_QSTR___neg__ = 14,
    MP_QSTR___invert__ = 15,
    MP_QSTR___abs__ = 16,
    MP_QSTR_bytes = 17,
};

/* ---- the @mp_type_* globals: static const data as cells of literal bytes ----

   The initializers of py/objtype.c and friends carry link-time addresses (.base = {
   &mp_type_type }, the ROM objects' headers): the flat profile refuses them as
   relocations (global_reloc.c), so the cells start as byte images with the pointer
   words zero and obj_port_boot stores what the static const initializers would
   carry -- the qstr corpus's pool rule, one file over.  The slots stay NULL: the
   class calls through none. */

extern mp_obj_none_t mp_const_none_obj;
extern mp_obj_bool_t mp_const_false_obj;
extern mp_obj_bool_t mp_const_true_obj;

mp_obj_type_t mp_type_type = {
    {NULL},    /* base.type: obj_port_boot stores &mp_type_type (a type is its own type) */
    0,         /* flags */
    MP_QSTR_type,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, /* no slot index is set */
};

mp_obj_type_t mp_type_int = {
    {NULL},
    0,
    MP_QSTR_int,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

mp_obj_type_t mp_type_str = {
    {NULL},
    0,
    MP_QSTR_str,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

mp_obj_type_t mp_type_bool = {
    {NULL},
    0,
    MP_QSTR_bool,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

mp_obj_type_t mp_type_NoneType = {
    {NULL},
    0,
    MP_QSTR_NoneType,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

/* the marker classes: a type this class only compares by address (the bytes check of
   the length path, the classes the raise paths name -- the cells of py/objexcept.c
   are another class's) carries its header word alone. The flat profile gives it no
   name or slot word of its own, and no body of this class reads one: the identity is
   the cell's address. */
mp_obj_base_t mp_type_bytes = {NULL};
mp_obj_base_t mp_type_TypeError = {NULL};
mp_obj_base_t mp_type_OverflowError = {NULL};
mp_obj_base_t mp_type_ValueError = {NULL};

mp_obj_none_t mp_const_none_obj = {{NULL}};
mp_obj_bool_t mp_const_false_obj = {{NULL}, false};
mp_obj_bool_t mp_const_true_obj = {{NULL}, true};

/* the per-type unary bodies and the str type's binary_op word (their bodies are
   verbatim below; the boot wires the words into the type cells' slots) */
mp_obj_t mp_obj_int_unary_op(mp_unary_op_t op, mp_obj_t o_in);
mp_obj_t bool_unary_op(mp_unary_op_t op, mp_obj_t o_in);
mp_obj_t uni_unary_op(mp_unary_op_t op, mp_obj_t self_in);
mp_obj_t mp_obj_str_binary_op(mp_obj_t o1, mp_obj_t o2, int op);

/* the port's boot: the addresses the static const initializers of objtype.c,
   objbool.c and objnone.c would carry as relocations, stored into the cells before
   any body runs.  It wires the slots too: the unary_op slot of each type carries the
   function word of its per-type body (objtype.c/objbool.c/objstrunicode.c's), and the
   words of those stores are what a call through a register dispatches on (the
   route of lower_indirect in the importer -- mp_unary_op calls the slot) */
void obj_port_boot(void) {
    mp_type_type.base.type = &mp_type_type;
    mp_type_int.base.type = &mp_type_type;
    mp_type_str.base.type = &mp_type_type;
    mp_type_bool.base.type = &mp_type_type;
    mp_type_NoneType.base.type = &mp_type_type;
    mp_type_bytes.type = &mp_type_type;
    mp_type_TypeError.type = &mp_type_type;
    mp_type_OverflowError.type = &mp_type_type;
    mp_type_ValueError.type = &mp_type_type;
    mp_const_none_obj.base.type = &mp_type_NoneType;
    mp_const_false_obj.base.type = &mp_type_bool;
    mp_const_true_obj.base.type = &mp_type_bool;
    mp_type_int.slot_index_unary_op = 1;
    mp_type_int.slots[0] = (const void *)&mp_obj_int_unary_op;
    mp_type_bool.slot_index_unary_op = 1;
    mp_type_bool.slots[0] = (const void *)&bool_unary_op;
    mp_type_str.slot_index_unary_op = 1;
    mp_type_str.slots[0] = (const void *)&uni_unary_op;
    mp_type_str.slot_index_binary_op = 2;
    mp_type_str.slots[1] = (const void *)&mp_obj_str_binary_op;
}

/* the addresses the differential names the cells by: what a run resolves an Object
   handle to, so a case can compare an answer against the cell it should name */
const void *obj_type_type_at(void) { return &mp_type_type; }
const void *obj_type_int_at(void) { return &mp_type_int; }
const void *obj_type_str_at(void) { return &mp_type_str; }
const void *obj_type_bool_at(void) { return &mp_type_bool; }
const void *obj_type_nonetype_at(void) { return &mp_type_NoneType; }
const void *obj_none_at(void) { return &mp_const_none_obj; }
const void *obj_false_at(void) { return &mp_const_false_obj; }
const void *obj_true_at(void) { return &mp_const_true_obj; }
const void *obj_arena_at(void) { return &m_arena[0]; }

/* ---- the ABI the bodies call and this TU does not carry ----

   MicroPython keeps each of these in a translation unit of its own (py/qstr.c,
   py/objint*.c, py/runtime.c): the corpus declares them the way py's headers do and
   the differential links its machine elsewhere (FRIENDS of test_import.py) or the
   call refuses at import (the honest boundary the REFUSED records) -- exactly the
   nlr_call.c rule. */

typedef size_t qstr;
const char *qstr_str(qstr q);
/* the qstr corpus's accessors (py/qstr.c): the GET_STR_* macros above read through
   them -- imported where the qstr corpus is linked in (known of test_import.py), a
   face that aborts where it is not */
size_t qstr_hash(qstr q);
qstr qstr_compute_hash(const byte *data, size_t len);
const byte *qstr_data(qstr q, size_t *len);
size_t qstr_len(qstr q);
mp_int_t mp_obj_int_get_checked(mp_const_obj_t o);
/* the raise faces of py/runtime.c: the nlr machine behind them is not this class's
   (nlr_call.c's rule: the raise path diverges and the run refuses there) */
_Noreturn void mp_raise_msg(const mp_obj_type_t *exc_type, mp_rom_error_text_t msg);
_Noreturn void mp_raise_msg_varg(const mp_obj_type_t *exc_type, mp_rom_error_text_t fmt, ...);
#define mp_raise_TypeError(msg) \
    mp_raise_msg((const mp_obj_type_t *)&mp_type_TypeError, msg)
/* the unary route: these bodies are this TU's own now (the verbatim section below) */
mp_obj_t mp_obj_len_maybe(mp_obj_t o_in);
mp_obj_t mp_unary_op(mp_unary_op_t op, mp_obj_t o);

/* ---- the bodies of py/obj.c, verbatim ---- */

// Allocates an object and also sets type, for mp_obj_malloc{,_var} macros.
MP_NOINLINE void *mp_obj_malloc_helper(size_t num_bytes, const mp_obj_type_t *type) {
    mp_obj_base_t *base = (mp_obj_base_t *)m_malloc(num_bytes);
    base->type = type;
    return base;
}

const mp_obj_type_t *MICROPY_WRAP_MP_OBJ_GET_TYPE(mp_obj_get_type)(mp_const_obj_t o_in) {
    #if MICROPY_OBJ_IMMEDIATE_OBJS && MICROPY_OBJ_REPR == MICROPY_OBJ_REPR_A

    if (mp_obj_is_obj(o_in)) {
        const mp_obj_base_t *o = MP_OBJ_TO_PTR(o_in);
        return o->type;
    } else {
        static const mp_obj_type_t *const types[] = {
            NULL, &mp_type_int, &mp_type_str, &mp_type_int,
            NULL, &mp_type_int, &mp_type_NoneType, &mp_type_int,
            NULL, &mp_type_int, &mp_type_str, &mp_type_int,
            NULL, &mp_type_int, &mp_type_bool, &mp_type_int,
        };
        return types[(uintptr_t)o_in & 0xf];
    }

    #elif MICROPY_OBJ_IMMEDIATE_OBJS && MICROPY_OBJ_REPR == MICROPY_OBJ_REPR_C

    if (mp_obj_is_small_int(o_in)) {
        return &mp_type_int;
    } else if (mp_obj_is_obj(o_in)) {
        const mp_obj_base_t *o = MP_OBJ_TO_PTR(o_in);
        return o->type;
    #if MICROPY_PY_BUILTINS_FLOAT
    } else if ((((mp_uint_t)(o_in)) & 0xff800007) != 0x00000006) {
        return &mp_type_float;
    #endif
    } else {
        static const mp_obj_type_t *const types[] = {
            &mp_type_str, &mp_type_NoneType, &mp_type_str, &mp_type_bool,
        };
        return types[((uintptr_t)o_in >> 3) & 3];
    }

    #else

    if (mp_obj_is_small_int(o_in)) {
        return &mp_type_int;
    } else if (mp_obj_is_qstr(o_in)) {
        return &mp_type_str;
        #if MICROPY_PY_BUILTINS_FLOAT && ( \
            MICROPY_OBJ_REPR == MICROPY_OBJ_REPR_C || MICROPY_OBJ_REPR == MICROPY_OBJ_REPR_D)
    } else if (mp_obj_is_float(o_in)) {
        return &mp_type_float;
        #endif
    #if MICROPY_OBJ_IMMEDIATE_OBJS
    } else if (mp_obj_is_immediate_obj(o_in)) {
        static const mp_obj_type_t *const types[2] = {&mp_type_NoneType, &mp_type_bool};
        return types[MP_OBJ_IMMEDIATE_OBJ_VALUE(o_in) & 1];
    #endif
    } else {
        const mp_obj_base_t *o = MP_OBJ_TO_PTR(o_in);
        return o->type;
    }

    #endif
}

const char *mp_obj_get_type_str(mp_const_obj_t o_in) {
    return qstr_str(mp_obj_get_type(o_in)->name);
}

bool mp_obj_is_true(mp_obj_t arg) {
    if (arg == mp_const_false) {
        return 0;
    } else if (arg == mp_const_true) {
        return 1;
    } else if (arg == mp_const_none) {
        return 0;
    } else if (mp_obj_is_small_int(arg)) {
        if (arg == MP_OBJ_NEW_SMALL_INT(0)) {
            return 0;
        } else {
            return 1;
        }
    } else {
        const mp_obj_type_t *type = mp_obj_get_type(arg);
        if (MP_OBJ_TYPE_HAS_SLOT(type, unary_op)) {
            mp_obj_t result = MP_OBJ_TYPE_GET_SLOT(type, unary_op)(MP_UNARY_OP_BOOL, arg);
            if (result != MP_OBJ_NULL) {
                return result == mp_const_true;
            }
        }

        mp_obj_t len = mp_obj_len_maybe(arg);
        if (len != MP_OBJ_NULL) {
            // obj has a length, truth determined if len != 0
            return len != MP_OBJ_NEW_SMALL_INT(0);
        } else {
            // any other obj is true per Python semantics
            return 1;
        }
    }
}

// returns false if arg is not of integral type
// returns true and sets *value if it is of integral type
// can throw OverflowError if arg is of integral type, but doesn't fit in a mp_int_t
bool mp_obj_get_int_maybe(mp_const_obj_t arg, mp_int_t *value) {
    if (arg == mp_const_false) {
        *value = 0;
    } else if (arg == mp_const_true) {
        *value = 1;
    } else if (mp_obj_is_small_int(arg)) {
        *value = MP_OBJ_SMALL_INT_VALUE(arg);
    } else if (mp_obj_is_exact_type(arg, &mp_type_int)) {
        *value = mp_obj_int_get_checked(arg);
    } else {
        arg = mp_unary_op(MP_UNARY_OP_INT_MAYBE, (mp_obj_t)arg);
        if (arg != MP_OBJ_NULL) {
            *value = mp_obj_int_get_checked(arg);
        } else {
            return false;
        }
    }
    return true;
}

/* ---- the unary route: py/runtime.c's mp_unary_op and the per-type slot bodies of
   objint.c, objbool.c and objstrunicode.c, verbatim (the bodies the boot's slot words
   name; the call through the slot is the dispatch the importer routes on those words) */

/* the op names of py/objtype.c, verbatim (the %q of a raise's message) */
const byte mp_unary_op_method_name[MP_UNARY_OP_NUM_RUNTIME] = {
    [MP_UNARY_OP_BOOL] = MP_QSTR___bool__,
    [MP_UNARY_OP_LEN] = MP_QSTR___len__,
    [MP_UNARY_OP_HASH] = MP_QSTR___hash__,
    [MP_UNARY_OP_INT_MAYBE] = MP_QSTR___int__,
    #if MICROPY_PY_ALL_SPECIAL_METHODS
    [MP_UNARY_OP_POSITIVE] = MP_QSTR___pos__,
    [MP_UNARY_OP_NEGATIVE] = MP_QSTR___neg__,
    [MP_UNARY_OP_INVERT] = MP_QSTR___invert__,
    [MP_UNARY_OP_ABS] = MP_QSTR___abs__,
    #endif
};

// This is called when an integer larger than a SMALL_INT is needed (although val might still fit in a SMALL_INT)
mp_obj_t mp_obj_new_int_from_ll(long long val) {
    (void)val;
    mp_raise_msg((const mp_obj_type_t *)&mp_type_OverflowError, MP_ERROR_TEXT("small int overflow"));
    return mp_const_none;
}

mp_obj_t mp_obj_new_int(mp_int_t value) {
    if (MP_SMALL_INT_FITS(value)) {
        return MP_OBJ_NEW_SMALL_INT(value);
    }
    mp_raise_msg((const mp_obj_type_t *)&mp_type_OverflowError, MP_ERROR_TEXT("small int overflow"));
    return mp_const_none;
}

// py/objint.c, verbatim
mp_obj_t mp_obj_int_unary_op(mp_unary_op_t op, mp_obj_t o_in) {
    (void)op;
    (void)o_in;
    return MP_OBJ_NULL; // op not supported
}

// py/objbool.c, verbatim (its static slot body, named here so the boot's word is a
// body of the run: the bool type's unary_op slot carries it)
mp_obj_t bool_unary_op(mp_unary_op_t op, mp_obj_t o_in) {
    if (op == MP_UNARY_OP_LEN) {
        return MP_OBJ_NULL;
    }
    bool value = BOOL_VALUE(o_in);
    return mp_unary_op(op, MP_OBJ_NEW_SMALL_INT(value));
}

/* py/unicode.c's utf8_charlen and its UTF8_IS_CONT of py/unicode.h, verbatim */
#define UTF8_IS_CONT(c) (((c) & 0xc0) == 0x80)

size_t utf8_charlen(const byte *str, size_t len) {
    size_t charlen = 0;
    for (const byte *top = str + len; str < top; ++str) {
        if (!UTF8_IS_CONT(*str)) {
            ++charlen;
        }
    }
    return charlen;
}

// py/objstrunicode.c, verbatim (the str type's unary_op slot body)
mp_obj_t uni_unary_op(mp_unary_op_t op, mp_obj_t self_in) {
    GET_STR_DATA_LEN(self_in, str_data, str_len);
    switch (op) {
        case MP_UNARY_OP_BOOL:
            return mp_obj_new_bool(str_len != 0);
        case MP_UNARY_OP_LEN:
            return MP_OBJ_NEW_SMALL_INT(utf8_charlen(str_data, str_len));
        default:
            return MP_OBJ_NULL; // op not supported
    }
}

// may return MP_OBJ_NULL
// py/obj.c, verbatim
mp_obj_t mp_obj_len_maybe(mp_obj_t o_in) {
    if (
        #if !MICROPY_PY_BUILTINS_STR_UNICODE
        // It's simple - unicode is slow, non-unicode is fast
        mp_obj_is_str(o_in) ||
        #endif
        mp_obj_is_type(o_in, (const mp_obj_type_t *)&mp_type_bytes)) {
        GET_STR_LEN(o_in, l);
        return MP_OBJ_NEW_SMALL_INT(l);
    } else {
        const mp_obj_type_t *type = mp_obj_get_type(o_in);
        if (MP_OBJ_TYPE_HAS_SLOT(type, unary_op)) {
            return MP_OBJ_TYPE_GET_SLOT(type, unary_op)(MP_UNARY_OP_LEN, o_in);
        } else {
            return MP_OBJ_NULL;
        }
    }
}

// py/runtime.c, verbatim: the unary dispatch of the class -- the fast paths of the
// immediate words, then the type's unary_op slot (the words the boot wires route the
// call through it to the per-type body)
mp_obj_t mp_unary_op(mp_unary_op_t op, mp_obj_t arg) {
    DEBUG_OP_printf("unary " UINT_FMT " %q %p\n", op, mp_unary_op_method_name[op], arg);

    if (op == MP_UNARY_OP_NOT) {
        // "not x" is the negative of whether "x" is true per Python semantics
        return mp_obj_new_bool(mp_obj_is_true(arg) == 0);
    } else if (mp_obj_is_small_int(arg)) {
        mp_int_t val = MP_OBJ_SMALL_INT_VALUE(arg);
        switch (op) {
            case MP_UNARY_OP_BOOL:
                return mp_obj_new_bool(val != 0);
            case MP_UNARY_OP_HASH:
                return arg;
            case MP_UNARY_OP_POSITIVE:
            case MP_UNARY_OP_INT_MAYBE:
                return arg;
            case MP_UNARY_OP_NEGATIVE:
                // check for overflow
                if (val == MP_SMALL_INT_MIN) {
                    return mp_obj_new_int(-val);
                } else {
                    return MP_OBJ_NEW_SMALL_INT(-val);
                }
            case MP_UNARY_OP_ABS:
                if (val >= 0) {
                    return arg;
                } else if (val == MP_SMALL_INT_MIN) {
                    // check for overflow
                    return mp_obj_new_int(-val);
                } else {
                    return MP_OBJ_NEW_SMALL_INT(-val);
                }
            default:
                assert(op == MP_UNARY_OP_INVERT);
                return MP_OBJ_NEW_SMALL_INT(~val);
        }
    } else if (op == MP_UNARY_OP_HASH && mp_obj_is_str_or_bytes(arg)) {
        // fast path for hashing str/bytes
        GET_STR_HASH(arg, h);
        if (h == 0) {
            GET_STR_DATA_LEN(arg, data, len);
            h = qstr_compute_hash(data, len);
        }
        return MP_OBJ_NEW_SMALL_INT(h);
    } else {
        const mp_obj_type_t *type = mp_obj_get_type(arg);
        if (MP_OBJ_TYPE_HAS_SLOT(type, unary_op)) {
            mp_obj_t result = MP_OBJ_TYPE_GET_SLOT(type, unary_op)(op, arg);
            if (result != MP_OBJ_NULL) {
                return result;
            }
        } else if (op == MP_UNARY_OP_HASH) {
            // Type doesn't have unary_op so use hash of object instance.
            return MP_OBJ_NEW_SMALL_INT((mp_uint_t)arg);
        }
        if (op == MP_UNARY_OP_BOOL) {
            // Type doesn't have unary_op (or didn't handle MP_UNARY_OP_BOOL),
            // so is implicitly True as this code path is impossible to reach
            // if arg==mp_const_none.
            return mp_const_true;
        }
        if (op == MP_UNARY_OP_INT_MAYBE
            #if MICROPY_PY_BUILTINS_FLOAT
            || op == MP_UNARY_OP_FLOAT_MAYBE
            #if MICROPY_PY_BUILTINS_COMPLEX
            || op == MP_UNARY_OP_COMPLEX_MAYBE
            #endif
            #endif
            ) {
            // These operators may return MP_OBJ_NULL if they are not supported by the type.
            return MP_OBJ_NULL;
        }
        #if MICROPY_ERROR_REPORTING <= MICROPY_ERROR_REPORTING_TERSE
        mp_raise_TypeError(MP_ERROR_TEXT("unsupported type for operator"));
        #else
        mp_raise_msg_varg((const mp_obj_type_t *)&mp_type_TypeError,
            MP_ERROR_TEXT("unsupported type for %q: '%s'"),
            mp_unary_op_method_name[op], mp_obj_get_type_str(arg));
        #endif
    }
}
