#ifndef RXFB_LAYOUT_H
#define RXFB_LAYOUT_H

/*
 * The layout of an RXFB v7 image (the one arena, docs/32), as the reader library ships it.
 *
 * These are the pinned structs of src/pymergetic/rxftk/core/ground.h that a reader needs, under
 * the library's own names, so that no other tree ever includes the engine. The layout is the ABI:
 * every struct is pinned by static assertions (C11 and C++, x86-64, AArch64, i386, wasm32), and
 * rxfb/test/layout_pins.c compiles this header next to ground.h and fails when the two differ in a
 * single size or offset. Little-endian only.
 *
 * An image is read in place. Every struct below is a view onto the mapped bytes, so each carries
 * `may_alias`: a reader that casts an image pointer to a struct pointer is not a strict-aliasing
 * violation.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#define RXFB_ASSERT(condition, message) static_assert(condition, message)
#define RXFB_ALIGNOF(type) alignof(type)
#else
#define RXFB_ASSERT(condition, message) _Static_assert(condition, message)
#define RXFB_ALIGNOF(type) _Alignof(type)
#endif

#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "an RXFB image is little-endian; this reader reads it in place"
#endif

#define RXFB_VIEW __attribute__((aligned(8), may_alias))

/* Pins one field: its offset and its size. Padding cannot hide a change. */
#define RXFB_PIN(type, field, offset, size)                                                        \
    RXFB_ASSERT(offsetof(type, field) == (offset), #type "." #field " offset");                    \
    RXFB_ASSERT(sizeof(((type *)0)->field) == (size), #type "." #field " size")

/* ---- the superblock: 4 KiB at byte 0, the only thing in the image that is not an object --- */
#define RXFB_SUPER_SIZE 4096U
#define RXFB_SUPER_VERSION 1U

typedef struct RXFB_VIEW rxfb_superblock {
    uint8_t magic[4];                       /* "RXFA" */
    uint32_t version;                       /* RXFB_SUPER_VERSION */
    uint64_t committed;                     /* first free offset: the end of the objects */
    uint64_t limit;                         /* end of the claimed region */
    uint32_t root;                          /* root handle: the image itself */
    uint32_t entry;                         /* entry handle: the first run */
    uint8_t reserved[RXFB_SUPER_SIZE - 32]; /* zero */
} rxfb_superblock;

RXFB_ASSERT(sizeof(rxfb_superblock) == RXFB_SUPER_SIZE, "superblock is one page");
RXFB_ASSERT(RXFB_ALIGNOF(rxfb_superblock) == 8, "superblock alignment");
RXFB_PIN(rxfb_superblock, magic, 0, 4);
RXFB_PIN(rxfb_superblock, version, 4, 4);
RXFB_PIN(rxfb_superblock, committed, 8, 8);
RXFB_PIN(rxfb_superblock, limit, 16, 8);
RXFB_PIN(rxfb_superblock, root, 24, 4);
RXFB_PIN(rxfb_superblock, entry, 28, 4);
RXFB_PIN(rxfb_superblock, reserved, 32, RXFB_SUPER_SIZE - 32);

/* ---- handles: (generation << 20) | index; a stale generation fails its check --------------- */
typedef uint32_t rxfb_handle;
#define RXFB_HANDLE_NONE 0xFFFFFFFFU
#define RXFB_HANDLE_INDEX_BITS 20U
#define RXFB_HANDLE_GEN_BITS 12U
#define RXFB_HANDLE_INDEX_MASK ((1U << RXFB_HANDLE_INDEX_BITS) - 1U)
#define RXFB_HANDLE_GEN_MASK ((1U << RXFB_HANDLE_GEN_BITS) - 1U)
RXFB_ASSERT(RXFB_HANDLE_INDEX_BITS + RXFB_HANDLE_GEN_BITS == 32, "a handle fills 32 bits");

static inline __attribute__((unused)) rxfb_handle rxfb_handle_pack(uint32_t index, uint32_t gen) {
    return (rxfb_handle)(((gen & RXFB_HANDLE_GEN_MASK) << RXFB_HANDLE_INDEX_BITS) |
                         (index & RXFB_HANDLE_INDEX_MASK));
}

static inline __attribute__((unused)) uint32_t rxfb_handle_index(rxfb_handle handle) {
    return handle & RXFB_HANDLE_INDEX_MASK;
}

static inline __attribute__((unused)) uint32_t rxfb_handle_gen(rxfb_handle handle) {
    return (handle >> RXFB_HANDLE_INDEX_BITS) & RXFB_HANDLE_GEN_MASK;
}

/* ---- the object record: a 24-byte header, then the payload inline in 8-byte slots ---------- */
enum {
    RXFB_OBJ_MODULE = 1, /* a node with children: its payload opens with the chain head */
    RXFB_OBJ_FUNC = 2,   /* a Code object (the bytes of a function for one machine) */
    RXFB_OBJ_HASH = 3,
    RXFB_OBJ_SPEC = 4, /* a table of rows: sys/specs, one relation table */
    RXFB_OBJ_SYM = 5,  /* the interned symbol table */
    RXFB_OBJ_BLOB = 6, /* opaque bytes: a CodeBlob */
    RXFB_OBJ_FRAME = 7,
    RXFB_OBJ_JOURNAL = 8
};
#define RXFB_OBJ_FREE 0xFFFFU

/* Object flags. */
#define RXFB_OFLAG_RO 0x1U
#define RXFB_OFLAG_ROOT 0x2U
#define RXFB_OFLAG_DIRTY 0x4U
#define RXFB_OFLAG_JOURNALED 0x8U

#define RXFB_OBJECT_ALIGN 8U
typedef struct RXFB_VIEW rxfb_object {
    uint16_t kind;    /* RXFB_OBJ_* */
    uint16_t gen;     /* generation (stale-handle detection) */
    uint16_t flags;   /* RXFB_OFLAG_* */
    uint16_t name;    /* symbol id (interned, 0 = anonymous) */
    uint32_t parent;  /* handle of the parent object */
    uint32_t link;    /* next sibling under the parent */
    uint32_t refs;    /* reference count */
    uint32_t payload; /* the payload room in bytes (a whole number of 8-byte slots) */
} rxfb_object;

RXFB_ASSERT(sizeof(rxfb_object) == 24, "object header is 24 bytes");
RXFB_ASSERT(RXFB_ALIGNOF(rxfb_object) == RXFB_OBJECT_ALIGN, "object header alignment");
RXFB_PIN(rxfb_object, kind, 0, 2);
RXFB_PIN(rxfb_object, gen, 2, 2);
RXFB_PIN(rxfb_object, flags, 4, 2);
RXFB_PIN(rxfb_object, name, 6, 2);
RXFB_PIN(rxfb_object, parent, 8, 4);
RXFB_PIN(rxfb_object, link, 12, 4);
RXFB_PIN(rxfb_object, refs, 16, 4);
RXFB_PIN(rxfb_object, payload, 20, 4);

/* ---- the object index: the first object of the image (byte 4096), the one cold-attach anchor --
 */
typedef struct RXFB_VIEW rxfb_index_entry {
    uint16_t kind;
    uint16_t gen;
    uint32_t reserved;
    uint64_t slot; /* the offset of the record in the image; 0 = no object */
} rxfb_index_entry;

typedef struct RXFB_VIEW rxfb_index_head {
    uint32_t count;    /* entries published: the next free index */
    uint32_t capacity; /* entries this table holds */
    /* rxfb_index_entry entry[capacity]; */
} rxfb_index_head;

RXFB_ASSERT(sizeof(rxfb_index_entry) == 16, "an index entry is 16 bytes");
RXFB_ASSERT(sizeof(rxfb_index_head) == 8, "the index head is 8 bytes");
RXFB_PIN(rxfb_index_entry, kind, 0, 2);
RXFB_PIN(rxfb_index_entry, gen, 2, 2);
RXFB_PIN(rxfb_index_entry, slot, 8, 8);
RXFB_PIN(rxfb_index_head, count, 0, 4);
RXFB_PIN(rxfb_index_head, capacity, 4, 4);

#define RXFB_INDEX_OFFSET RXFB_SUPER_SIZE
#define RXFB_INDEX_SELF 0U /* entry 0 is the table's own identity */
#define RXFB_INDEX_SYMS 1U /* the symbol table's entry */

/* ---- a Module's payload: the head of the children chain (the chain is the `link` fields) ---- */
typedef struct RXFB_VIEW rxfb_module {
    uint32_t children; /* handle of the first child, RXFB_HANDLE_NONE = none */
    uint32_t count;
} rxfb_module;

RXFB_ASSERT(sizeof(rxfb_module) == 8, "a module payload head is 8 bytes");
RXFB_PIN(rxfb_module, children, 0, 4);
RXFB_PIN(rxfb_module, count, 4, 4);

/* ---- the symbol table (sys/syms): a head, `capacity` offsets, then the NUL-terminated strings --
 */
typedef struct RXFB_VIEW rxfb_syms {
    uint32_t count;    /* the next id to hand out */
    uint32_t capacity; /* string offsets this table holds */
    /* uint32_t offset[capacity]; the strings are packed behind the offsets */
} rxfb_syms;

RXFB_ASSERT(sizeof(rxfb_syms) == 8, "the symbol head is 8 bytes");
RXFB_PIN(rxfb_syms, count, 0, 4);
RXFB_PIN(rxfb_syms, capacity, 4, 4);
#define RXFB_SYMS_ANONYMOUS 0U

/* ---- sys/specs: the semantic face of an object, one row per index ------------------------- */
typedef struct RXFB_VIEW rxfb_spec_row {
    uint32_t type;       /* handle of the Type object, RXFB_HANDLE_NONE = untyped */
    uint32_t flags;      /* the world's flags word: kind | state | owner */
    uint32_t index;      /* position among the ordered siblings; 0 = the order is the name */
    uint32_t reserved;   /* zero */
    uint64_t generation; /* the world's generation of the object */
} rxfb_spec_row;

RXFB_ASSERT(sizeof(rxfb_spec_row) == 24, "a spec row is 24 bytes");
RXFB_PIN(rxfb_spec_row, type, 0, 4);
RXFB_PIN(rxfb_spec_row, flags, 4, 4);
RXFB_PIN(rxfb_spec_row, index, 8, 4);
RXFB_PIN(rxfb_spec_row, reserved, 12, 4);
RXFB_PIN(rxfb_spec_row, generation, 16, 8);

/* The packed flags word of a spec row: node kind, state and owner. */
#define RXFB_FLAG_KIND_SHIFT 0
#define RXFB_FLAG_KIND_MASK 0xFFU
#define RXFB_FLAG_STATE_SHIFT 8
#define RXFB_FLAG_STATE_MASK 0x3FU
#define RXFB_FLAG_OWNER_SHIFT 14
#define RXFB_FLAG_OWNER_MASK 0xFU

/* ---- sys/relations: one table per related object, its payload the reference rows ----------- */
typedef struct RXFB_VIEW rxfb_ref {
    uint64_t target; /* a handle, zero-extended */
    uint64_t role;   /* a handle, zero-extended; the 64-bit none = no role */
    uint16_t kind;   /* RXFB_REF_* */
    uint16_t binding;
    uint32_t index; /* the argument or leaf number */
} rxfb_ref;

RXFB_ASSERT(sizeof(rxfb_ref) == 24, "reference record");
RXFB_PIN(rxfb_ref, target, 0, 8);
RXFB_PIN(rxfb_ref, role, 8, 8);
RXFB_PIN(rxfb_ref, kind, 16, 2);
RXFB_PIN(rxfb_ref, binding, 18, 2);
RXFB_PIN(rxfb_ref, index, 20, 4);

#define RXFB_REF_NONE 0xFFFFFFFFFFFFFFFFULL

enum {
    RXFB_REF_CALL = 0,
    RXFB_REF_DATA = 1,
    RXFB_REF_ENTRY = 2,
    RXFB_REF_TYPE = 3,
    RXFB_REF_IMPORT = 4,
    RXFB_REF_IMPLEMENTATION = 5,
    RXFB_REF_OWNER = 6,
    RXFB_REF_DOC = 7
};
enum { RXFB_BINDING_MANDATORY = 0, RXFB_BINDING_OPTIONAL = 1 };

/* ---- the code story: where the bytes of a function are --------------------------------------
 * A Function is a Module; for each machine it has a child Code (kind RXFB_OBJ_FUNC) named after
 * the machine ("x86_64_linux", "aarch64_linux", "wasm32", ...). The Code names its CodeBlob (kind
 * RXFB_OBJ_BLOB) through `body`; the blob's payload is a fixed part and then the bytes. These are
 * the offsets of those payloads (the Code and CodeBlob types of the execution module; the test
 * suite pins them against the type layouts the starter carries). */
#define RXFB_CODE_ENTRY_OFFSET 40U /* u64: where the function starts inside the blob's bytes */
#define RXFB_CODE_BYTE_COUNT 48U   /* u64: the length of the code */
#define RXFB_CODE_BODY 96U         /* u64: the handle of the CodeBlob, RXFB_REF_NONE = no bytes */
#define RXFB_CODE_SIZE 104U        /* the Code payload this reader needs */
#define RXFB_BLOB_BYTE_COUNT 0U    /* u64: the number of bytes */
#define RXFB_BLOB_DATA 40U         /* where the bytes start in the blob's payload */

#ifdef __cplusplus
}
#endif

#endif
