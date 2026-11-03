/* L0 types: the base types and the tree kinds the language assumes (docs/38 6).
 *
 * The language does not define these; it assumes they exist (the genesis of an image creates
 * them before the first line of any program is read). This header pins their identity: the
 * base type ids are fixed numbers, the same number in the .h and in the image's Type object,
 * so a 1:1 check is possible. Layout only, freestanding C11.
 */
#ifndef PYMERGETIC_RXF_L0_TYPES_H
#define PYMERGETIC_RXF_L0_TYPES_H

#include "rxf_memory.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ---- the base type ids -----------------------------------------------------
 * Fixed, small numbers, stable forever: the genesis writes a Type object with this id for each,
 * and tests assert the image's row carries the same number (docs/38: "1:1, as with the kinds").
 * 0 is "no type". The C width names are the ones the tree already answers (uint8_t, ...).
 */
enum {
    PYMERGETIC_RXF_TYPE_NONE = 0,
    PYMERGETIC_RXF_TYPE_BOOL = 1,
    PYMERGETIC_RXF_TYPE_UINT8 = 2,   /* uint8_t */
    PYMERGETIC_RXF_TYPE_UINT16 = 3,  /* uint16_t */
    PYMERGETIC_RXF_TYPE_UINT32 = 4,  /* uint32_t */
    PYMERGETIC_RXF_TYPE_UINT64 = 5,  /* uint64_t */
    PYMERGETIC_RXF_TYPE_INT8 = 6,    /* int8_t */
    PYMERGETIC_RXF_TYPE_INT16 = 7,   /* int16_t */
    PYMERGETIC_RXF_TYPE_INT32 = 8,   /* int32_t */
    PYMERGETIC_RXF_TYPE_INT64 = 9,   /* int64_t */
    PYMERGETIC_RXF_TYPE_SIZE = 10,   /* size_t of the seat (32 on wasm32) */
    PYMERGETIC_RXF_TYPE_HANDLE = 11, /* an object id (handle32 packed in u32, or u64 in the tree) */
    PYMERGETIC_RXF_TYPE_F32 = 12,    /* reserved (float is not in draft 0) */
    PYMERGETIC_RXF_TYPE_F64 = 13,    /* reserved */
    PYMERGETIC_RXF_TYPE_UTF8 = 14,   /* a u8 array, the language's strings */
    PYMERGETIC_RXF_TYPE_UTF16 = 15,  /* reserved */
    /* 16..1023 reserved for more base types; anything above is an ordinary Type object of the
     * image with an ordinary handle. */
    PYMERGETIC_RXF_BASE_TYPE_MAX = 1023
};

/* ---- the tree kinds the language assumes -----------------------------------
 * The kinds of the definitions the language reads (docs/38 1-7). Fixed numbers in the same
 * space as PYMERGETIC_RXF_KIND_* (rxf_memory.h reserves 9..253 for exactly these).
 */
enum {
    PYMERGETIC_RXF_KIND_IMPORT = 9,     /* an Import object: refers to the node it names */
    PYMERGETIC_RXF_KIND_ALIAS = 10,     /* an Alias: a second name for a node */
    PYMERGETIC_RXF_KIND_DOC = 11,       /* a Doc: attached to the thing it documents */
    PYMERGETIC_RXF_KIND_TYPE = 12,      /* a Type: struct/class/enum, with Fields under it */
    PYMERGETIC_RXF_KIND_FIELD = 13,     /* a Field of a type */
    PYMERGETIC_RXF_KIND_FUNCTION = 14,  /* a Function (a call makes an instance of it) */
    PYMERGETIC_RXF_KIND_PARAMETER = 15, /* a Parameter of a function */
    PYMERGETIC_RXF_KIND_SIGNATURE = 16, /* a Signature: parameters, result, refusal set */
    PYMERGETIC_RXF_KIND_INSTANCE = 17,  /* an Instance: one call's variables as fields */
    PYMERGETIC_RXF_KIND_GRAPH = 18,     /* the body of a function: blocks, ops, terminators */
    PYMERGETIC_RXF_KIND_GBLOCK = 19,    /* a block of a graph (the arena owns BLOCK) */
    PYMERGETIC_RXF_KIND_OPERATION = 20, /* one operation of a block */
    PYMERGETIC_RXF_KIND_VALUE = 21,     /* a value of a block */
    PYMERGETIC_RXF_KIND_BELT = 22,      /* a bounded queue between stages (docs/38 10) */
    PYMERGETIC_RXF_KIND_STAGE = 23      /* a stage: a function a carrier sets onto */
};

/* ---- the flags a record carries -------------------------------------------- */

enum {
    PYMERGETIC_RXF_FLAG_CONST = 1u << 0,    /* read only */
    PYMERGETIC_RXF_FLAG_STATIC = 1u << 1,   /* bound to the class, not each instance */
    PYMERGETIC_RXF_FLAG_WEAK = 1u << 2,     /* does not keep its object alive */
    PYMERGETIC_RXF_FLAG_CHILDREN = 1u << 3, /* the field holds the children (a list) */
    PYMERGETIC_RXF_FLAG_INLINE = 1u << 4    /* the value is in the payload, not a reference */
};

#ifdef __cplusplus
}
#endif
#endif /* PYMERGETIC_RXF_L0_TYPES_H */
