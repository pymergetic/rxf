/* L0 memory: the one arena, as the smallest building block (docs/32 is the specification).
 *
 * One linear region. EVERYTHING in it is an object: a record followed inline by its payload.
 * There is no fixed part that is not an object: the superblock is the first object (kind
 * SUPERBLOCK), the slot index is the second (kind INDEX). What the reader of an image needs
 * before it can read anything is the object at offset 0, and that is all.
 *
 * Layout only, freestanding C11, no behaviour. These structs ARE the arena; the pinning test
 * checks their sizes and offsets against the running system.
 */
#ifndef PYMERGETIC_RXF_L0_MEMORY_H
#define PYMERGETIC_RXF_L0_MEMORY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- the record: the one header every object in the arena carries --------------------
 * kind and generation make the handle; name is a symbol id (0 anonymous); parent is the
 * containing object; link is the next sibling; payload is the byte count that follows inline.
 */
typedef struct pymergetic_rxf_record {
    uint16_t kind;    /* what the object is (see kind_t) */
    uint16_t gen;     /* the generation: a stale handle fails */
    uint16_t flags;   /* const, static, weak, children, inline */
    uint16_t name;    /* symbol id, 0 is anonymous */
    uint32_t parent;  /* handle32 of the containing object */
    uint32_t link;    /* handle32 of the next sibling */
    uint32_t refs;    /* the reference count */
    uint32_t payload; /* payload bytes, follow inline, 8-byte aligned */
} pymergetic_rxf_record_t;

/* A FREE span is a record with its two ring links behind the header. */
typedef struct pymergetic_rxf_free {
    pymergetic_rxf_record_t record;
    uint32_t prev; /* handle32 of the previous free span */
    uint32_t next; /* handle32 of the next free span */
} pymergetic_rxf_free_t;

/* ---- the handle ---------------------------------------------------------------------
 * Index (20 bits) and generation (12 bits). Index 0 with generation 0 is no object.
 */
typedef uint32_t pymergetic_rxf_handle_t;
#define PYMERGETIC_RXF_HANDLE_NONE 0u

/* ---- the kinds ----------------------------------------------------------------------
 * The kinds of the arena itself and of the tree the language reads. The numbers are NOT
 * reserved or assigned by meaning here: an object's kind says what the READER expects to find
 * behind its record, nothing more. The language's kinds are ordinary entries of this list,
 * beside the arena's own.
 */
typedef enum {
    pymergetic_rxf_kind_free = 0xffff,
    pymergetic_rxf_kind_superblock = 1,
    pymergetic_rxf_kind_index = 2,
    pymergetic_rxf_kind_module = 3,
    pymergetic_rxf_kind_sym = 4,
    pymergetic_rxf_kind_spec = 5,
    pymergetic_rxf_kind_blob = 6,
    pymergetic_rxf_kind_function = 7,
    pymergetic_rxf_kind_parameter = 8,
    pymergetic_rxf_kind_graph = 9,
    pymergetic_rxf_kind_gblock = 10,
    pymergetic_rxf_kind_operation = 11,
    pymergetic_rxf_kind_value = 12,
    pymergetic_rxf_kind_type = 13,
    pymergetic_rxf_kind_field = 14,
    pymergetic_rxf_kind_import = 15,
    pymergetic_rxf_kind_alias = 16,
    pymergetic_rxf_kind_doc = 17,
    pymergetic_rxf_kind_instance = 18,
    pymergetic_rxf_kind_belt = 19,
    pymergetic_rxf_kind_stage = 20
} pymergetic_rxf_kind_t;

/* ---- the flags ----------------------------------------------------------------------
 * The markers of docs/38: const, static, weak, plus what a payload holds.
 */
typedef enum : uint16_t {
    pymergetic_rxf_flag_const = 1u << 0,    /* read only */
    pymergetic_rxf_flag_static = 1u << 1,   /* bound to the class, not each instance */
    pymergetic_rxf_flag_weak = 1u << 2,     /* does not keep its object alive */
    pymergetic_rxf_flag_children = 1u << 3, /* the payload holds the children (a list) */
    pymergetic_rxf_flag_inline = 1u << 4    /* the value is in the payload, not a reference */
} pymergetic_rxf_flag_t;

/* ---- the superblock: an object, the first one ---------------------------------------
 * Its payload is the words a cold attach needs: the magic and version, how far the stream is
 * written, where the root and the index are. It sits at offset 0 with its record in front.
 */
typedef struct pymergetic_rxf_superblock {
    pymergetic_rxf_record_t record; /* kind superblock, name 0, no parent */
    uint32_t magic;                 /* "RXFA" as a little-endian u32 */
    uint32_t version;               /* 1 */
    uint64_t committed;             /* the first free offset of the stream */
    uint64_t limit;                 /* the claimed end (may grow) */
    pymergetic_rxf_handle_t root;   /* the tree's root object */
    pymergetic_rxf_handle_t index;  /* the slot index object */
    uint8_t reserved[4040];         /* the page stays whole: 24 record + 32 words */
} pymergetic_rxf_superblock_t;

/* ---- the slot index: an object, the second one ---------------------------------------
 * One entry per handle index: where the record of that index sits, and its generation.
 */
typedef struct pymergetic_rxf_index_entry {
    uint16_t kind;   /* mirrors the record's kind */
    uint16_t gen;    /* mirrors the record's generation */
    uint32_t slot;   /* reserved, 0 */
    uint64_t offset; /* the record's offset in the arena, 0 is a dead slot */
} pymergetic_rxf_index_entry_t;

typedef struct pymergetic_rxf_index {
    pymergetic_rxf_record_t record; /* kind index, the superblock's first child */
    uint32_t count;                 /* entries in use */
    uint32_t capacity;              /* entries allocated */
    /* the entries follow inline: index_entry_t entries[capacity]; */
} pymergetic_rxf_index_t;

/* A module's payload opens with the arena's chain head (docs/32 1.2): the value of a module
 * starts behind it. */
#define PYMERGETIC_RXF_MODULE_HEAD 8u

#ifdef __cplusplus
}
#endif
#endif /* PYMERGETIC_RXF_L0_MEMORY_H */
