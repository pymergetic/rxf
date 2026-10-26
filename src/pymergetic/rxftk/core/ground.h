#ifndef PYMERGETIC_RXF_CORE_GROUND_H
#define PYMERGETIC_RXF_CORE_GROUND_H

/*
 * Ground types of the RXF arena (RXFB v6). Plain C structs are the typeset:
 * the C++ core, the nanobind module, and Python reuse exactly these layouts.
 * Layout and rules: docs/17-OPERATING-SYSTEM-PATH.md, "Layout rules".
 *
 * Width rule: identities, addresses, offsets, and sizes are 64-bit. Small
 * enumerations, flags, and per-object counters stay narrow.
 * Every layout is pinned by static assertions that compile as C11 and C++20
 * for x86-64, AArch64, i386, and wasm32 (tests/test_ground_layout.py).
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#define PYMERGETIC_RXF_GROUND_ASSERT(condition, message) static_assert(condition, message)
#define PYMERGETIC_RXF_ALIGNOF(type) alignof(type)
#else
#define PYMERGETIC_RXF_GROUND_ASSERT(condition, message) _Static_assert(condition, message)
#define PYMERGETIC_RXF_ALIGNOF(type) _Alignof(type)
#endif

/* Pins one field: its offset and its size. Padding cannot hide a change. */
#define PYMERGETIC_RXF_PIN(type, field, offset, size)                                              \
    PYMERGETIC_RXF_GROUND_ASSERT(offsetof(type, field) == (offset), #type "." #field " offset");   \
    PYMERGETIC_RXF_GROUND_ASSERT(sizeof(((type *)0)->field) == (size), #type "." #field " size")

/* A handle is an image-local slot number. It never leaves its image and never
 * appears in a delta; the identity across images is the object path. */
typedef uint64_t pymergetic_rxf_handle;
#define PYMERGETIC_RXF_HANDLE_NONE 0xFFFFFFFFFFFFFFFFULL
#define PYMERGETIC_RXF_LIMIT_UNKNOWN 0xFFFFFFFFFFFFFFFFULL

/* The format fixes only these handles; the allocator needs them before any
 * lookup by path works. Every other object is found by its path. */
#define PYMERGETIC_RXF_HANDLE_ROOT 0ULL
#define PYMERGETIC_RXF_HANDLE_TYPE 1ULL
#define PYMERGETIC_RXF_HANDLE_FIELD 2ULL

#define PYMERGETIC_RXF_ARENA_VERSION 6U
#define PYMERGETIC_RXF_CELL_ALIGN 16U
#define PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES 4096U

#define PYMERGETIC_RXF_ALIGNED8 __attribute__((aligned(8)))

/* ---- arena header: 192 bytes, 64 reserved for growth ------------------- */
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_arena_header {
    uint8_t magic[4];     /* "RXFB" */
    uint32_t version;     /* PYMERGETIC_RXF_ARENA_VERSION */
    uint32_t header_size; /* 192 */
    uint32_t flags;
    uint64_t body_size; /* bytes from arena start to the end of the cell stream */
    uint64_t committed_size;
    uint64_t limit;     /* PYMERGETIC_RXF_LIMIT_UNKNOWN = unknown */
    uint64_t frontier;  /* relative to cells_off */
    uint64_t index_off; /* directory of index chunk cell offsets; it ends at cells_off */
    uint64_t cells_off;
    uint64_t names_off;    /* offset of the NAME block cell that takes the next name */
    uint64_t names_size;   /* bytes of its payload in use */
    uint64_t index_chunks; /* INDEX block cells in use */
    uint64_t handle_count; /* slots handed out */
    uint64_t free_head;    /* first free slot, or PYMERGETIC_RXF_HANDLE_NONE */
    uint64_t entry;        /* entry Function handle, or PYMERGETIC_RXF_HANDLE_NONE */
    uint64_t root;         /* root module handle */
    uint32_t cell_align;   /* PYMERGETIC_RXF_CELL_ALIGN */
    uint32_t page_size;
    uint8_t reserved[64]; /* must be zero */
} pymergetic_rxf_arena_header;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_arena_header) == 192,
                             "arena header is 192 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_arena_header) % 64 == 0,
                             "single block: multiple of 64");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_arena_header) == 8,
                             "arena header alignment");
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, magic, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, version, 4, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, header_size, 8, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, flags, 12, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, body_size, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, committed_size, 24, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, limit, 32, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, frontier, 40, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, index_off, 48, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, cells_off, 56, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, names_off, 64, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, names_size, 72, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, index_chunks, 80, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, handle_count, 88, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, free_head, 96, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, entry, 104, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, root, 112, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, cell_align, 120, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, page_size, 124, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, reserved, 128, 64);

/* ---- index: an entry is a cell offset (16-aligned), or a free link ----- */
typedef uint64_t pymergetic_rxf_index_entry;
#define PYMERGETIC_RXF_INDEX_FREE                                                                  \
    1ULL /* bit 0 set: free; the remaining bits link the next free slot */
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_index_entry) == 8, "array record: power of two");

/* ---- object cell header: 64 bytes -------------------------------------- */
#define PYMERGETIC_RXF_FLAG_KIND_SHIFT 0 /* node kind, 8 bits */
#define PYMERGETIC_RXF_FLAG_STATE_SHIFT                                                            \
    8 /* provenance, disposition, cleanliness, mobility: 6 bits */
#define PYMERGETIC_RXF_FLAG_OWNER_SHIFT 14 /* owner kind, 4 bits */
#define PYMERGETIC_RXF_FLAG_KIND_MASK 0xFFU

/* Kind 255 marks a block cell: arena bookkeeping that lives in the cell stream
 * so that one sequential walk covers everything. A block has no handle. Its
 * `type` field holds the block kind. */
#define PYMERGETIC_RXF_KIND_BLOCK 255U
/* Kind 254 marks a dead cell: its slot was freed or now holds a newer cell. The
 * bytes stay in the stream until compaction. */
#define PYMERGETIC_RXF_KIND_DEAD 254U
#define PYMERGETIC_RXF_BLOCK_INDEX                                                                 \
    1U /* payload: PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES index entries */
#define PYMERGETIC_RXF_BLOCK_NAMES 2U /* payload: NUL-terminated names, packed */
#define PYMERGETIC_RXF_NAME_BLOCK_PAYLOAD 4096U

/* Object kinds (v5 NodeKind values; 9 stays reserved; 20 and 21 are new). The kind of a
 * new object is the `node_kind` of its Type, and DATA when the Type names none. */
enum {
    PYMERGETIC_RXF_NODE_ROOT = 0,
    PYMERGETIC_RXF_NODE_GROUP = 1,
    PYMERGETIC_RXF_NODE_NAMESPACE = 2,
    PYMERGETIC_RXF_NODE_CARD = 3,
    PYMERGETIC_RXF_NODE_TYPE = 4,
    PYMERGETIC_RXF_NODE_FIELD = 5,
    PYMERGETIC_RXF_NODE_FN = 6,
    PYMERGETIC_RXF_NODE_CODE = 7,
    PYMERGETIC_RXF_NODE_DATA = 8,
    PYMERGETIC_RXF_NODE_SECTION = 10,
    PYMERGETIC_RXF_NODE_LIMIT = 11,
    PYMERGETIC_RXF_NODE_IMPORT = 12,
    PYMERGETIC_RXF_NODE_EXPORT = 13,
    PYMERGETIC_RXF_NODE_VIEW = 14,
    PYMERGETIC_RXF_NODE_TOOL = 15,
    PYMERGETIC_RXF_NODE_BLOB = 16,
    PYMERGETIC_RXF_NODE_CHANNEL = 17,
    PYMERGETIC_RXF_NODE_TRANSACTION = 18,
    PYMERGETIC_RXF_NODE_MODULE = 19,
    PYMERGETIC_RXF_NODE_ENUMERATOR = 20, /* one named value of an enumeration Type */
    PYMERGETIC_RXF_NODE_ALIAS = 21 /* a short name for another object, in the scope of a Module */
};

/* Object state bits inside the state field of the flags. */
#define PYMERGETIC_RXF_STATE_PROVENANCE_SHIFT 0
#define PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT 1
#define PYMERGETIC_RXF_STATE_CLEANLINESS_SHIFT 3
#define PYMERGETIC_RXF_STATE_MOBILITY_SHIFT 4
enum { PYMERGETIC_RXF_PROVENANCE_RUNTIME = 0, PYMERGETIC_RXF_PROVENANCE_IMAGE = 1 };
enum {
    PYMERGETIC_RXF_DISPOSITION_RETAIN = 0,
    PYMERGETIC_RXF_DISPOSITION_TRANSIENT = 1,
    PYMERGETIC_RXF_DISPOSITION_TOMBSTONE = 2
};
enum { PYMERGETIC_RXF_CLEANLINESS_CLEAN = 0, PYMERGETIC_RXF_CLEANLINESS_DIRTY = 1 };
enum {
    PYMERGETIC_RXF_MOBILITY_MOVABLE = 0,
    PYMERGETIC_RXF_MOBILITY_PINNED = 1,
    PYMERGETIC_RXF_MOBILITY_RELOCATABLE = 2
};
#define PYMERGETIC_RXF_FLAG_STATE_MASK 0x3FU
#define PYMERGETIC_RXF_FLAG_OWNER_MASK 0xFU

/* Owner kinds: the values of v5 OwnerKind. An owner that is more specific than
 * its kind is an optional reference of kind PYMERGETIC_RXF_REF_OWNER on the cell. */
enum {
    PYMERGETIC_RXF_OWNER_SYSTEM = 0,
    PYMERGETIC_RXF_OWNER_USER = 1,
    PYMERGETIC_RXF_OWNER_APPLICATION = 2,
    PYMERGETIC_RXF_OWNER_BUILD = 3,
    PYMERGETIC_RXF_OWNER_DEVICE = 4,
    PYMERGETIC_RXF_OWNER_EXTERNAL = 5,
    PYMERGETIC_RXF_OWNER_SHARED = 6
};

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_cell {
    pymergetic_rxf_handle type;
    pymergetic_rxf_handle parent; /* PYMERGETIC_RXF_HANDLE_NONE for the root */
    uint64_t payload_size;
    uint64_t generation;
    uint64_t name; /* arena offset of a NUL-terminated name inside a NAME block; 0 = none */
    uint32_t flags;
    uint32_t ref_count;
    uint64_t forward; /* compaction forwarding offset, 0 when unused */
    uint32_t index; /* position among the siblings that are listed in order; 0 when the order is the
                       name */
    uint32_t reserved; /* must be zero */
} pymergetic_rxf_cell;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_cell) == 64, "cell header is 64 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_cell) == 8,
                             "cell header alignment");
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, type, 0, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, parent, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, payload_size, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, generation, 24, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, name, 32, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, flags, 40, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, ref_count, 44, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, forward, 48, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, index, 56, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, reserved, 60, 4);

/* ---- reference: 24 bytes, stored inline after the payload -------------- */
/* `role` is the handle of the object that names the relationship (a Role
 * object: its path is the name), `index` the position when the role repeats
 * (argument 0, 1, 2 of a call). No handle means no role. */
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_ref {
    pymergetic_rxf_handle target;
    pymergetic_rxf_handle role;
    uint16_t kind;
    uint16_t binding;
    uint32_t index;
} pymergetic_rxf_ref;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_ref) == 24, "reference record");
PYMERGETIC_RXF_PIN(pymergetic_rxf_ref, target, 0, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_ref, role, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_ref, kind, 16, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_ref, binding, 18, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_ref, index, 20, 4);

/* Reference kinds 0 to 4 keep their v5 values. */
enum {
    PYMERGETIC_RXF_REF_CALL = 0,
    PYMERGETIC_RXF_REF_DATA = 1,
    PYMERGETIC_RXF_REF_ENTRY = 2,
    PYMERGETIC_RXF_REF_TYPE = 3,
    PYMERGETIC_RXF_REF_IMPORT = 4,
    PYMERGETIC_RXF_REF_IMPLEMENTATION = 5, /* Function to its Code objects */
    PYMERGETIC_RXF_REF_OWNER = 6,          /* optional: the object that owns this one */
    PYMERGETIC_RXF_REF_DOC = 7             /* editorial: a placement of one Doc object (docs/22) */
};
enum { PYMERGETIC_RXF_BINDING_MANDATORY = 0, PYMERGETIC_RXF_BINDING_OPTIONAL = 1 };

/* cell size = 64 + align16(payload_size) + 24 * ref_count, rounded up to 16 */
static inline __attribute__((unused)) uint64_t
pymergetic_rxf_cell_size(const pymergetic_rxf_cell *cell) {
    const uint64_t raw = (uint64_t)sizeof(pymergetic_rxf_cell) +
                         ((cell->payload_size + 15ULL) & ~15ULL) +
                         (uint64_t)cell->ref_count * (uint64_t)sizeof(pymergetic_rxf_ref);
    return (raw + 15ULL) & ~15ULL;
}

/* ---- Type and Field payloads: the self-describing core ----------------- */
/* Bits 0 to 2 keep their v5 meaning (FieldFlags); bit 3 is new. */
#define PYMERGETIC_RXF_FIELD_FLAG_MUTABLE 0x1U
#define PYMERGETIC_RXF_FIELD_FLAG_OPTIONAL 0x2U
#define PYMERGETIC_RXF_FIELD_FLAG_REPEATED 0x4U
#define PYMERGETIC_RXF_FIELD_FLAG_REF 0x8U /* the field holds an object reference (a handle) */
/* A declared relation, not stored in the payload: */
#define PYMERGETIC_RXF_FIELD_FLAG_CHILDREN                                                         \
    0x10U /* the children of the value type below an object are the list this field names */
#define PYMERGETIC_RXF_FIELD_FLAG_INVERSE                                                          \
    0x20U /* the objects of the value type that reach this object through `via` */

/* The forms of a Type (v5 TypeForm values). */
enum {
    PYMERGETIC_RXF_FORM_META = 0,
    PYMERGETIC_RXF_FORM_VOID = 1,
    PYMERGETIC_RXF_FORM_BOOL = 2,
    PYMERGETIC_RXF_FORM_UINT = 3,
    PYMERGETIC_RXF_FORM_SINT = 4,
    PYMERGETIC_RXF_FORM_FLOAT = 5,
    PYMERGETIC_RXF_FORM_BYTES = 6,
    PYMERGETIC_RXF_FORM_STRUCT = 7,
    PYMERGETIC_RXF_FORM_UNION = 8,
    PYMERGETIC_RXF_FORM_ENUM = 9,
    PYMERGETIC_RXF_FORM_ARRAY = 10,
    PYMERGETIC_RXF_FORM_REF = 11,
    PYMERGETIC_RXF_FORM_FUNCTION = 12,
    PYMERGETIC_RXF_FORM_CONTROL = 13,
    PYMERGETIC_RXF_FORM_EXPRESSION = 14,
    PYMERGETIC_RXF_FORM_EXECUTION = 15,
    PYMERGETIC_RXF_FORM_RESERVED_16 = 16,
    PYMERGETIC_RXF_FORM_OPAQUE = 17,
    PYMERGETIC_RXF_FORM_COUNT = 18
};

/* The bits of the flags of a Type. */
#define PYMERGETIC_RXF_TYPE_FLAG_VARIABLE 0x1U /* the payload may be longer than `size` */
#define PYMERGETIC_RXF_TYPE_FLAG_SIGNED 0x2U   /* an integer that carries a sign */
/* the layout is described by Field objects */
#define PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING 0x4U
/* an enumeration whose values are bits that combine */
#define PYMERGETIC_RXF_TYPE_FLAG_BITSET 0x8U

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_type {
    uint32_t descriptor_tag;
    uint32_t form;
    uint64_t size;
    pymergetic_rxf_handle inner_type;
    pymergetic_rxf_handle return_type;
    uint32_t align;
    uint32_t flags;
    uint32_t field_count;
    uint32_t node_kind; /* kind of the objects of this type; 0 means DATA */
} pymergetic_rxf_type;

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_field {
    uint32_t descriptor_tag;
    uint32_t flags; /* PYMERGETIC_RXF_FIELD_FLAG_REF, ... */
    pymergetic_rxf_handle owner_type;
    pymergetic_rxf_handle value_type;
    uint64_t offset; /* byte offset inside the owner payload */
    uint64_t count;  /* array length, 1 for a scalar */
    uint64_t via;    /* INVERSE: the Field or Role the other objects point through; else unset */
} pymergetic_rxf_field;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_type) == 48, "type payload is 48 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_field) == 48, "field payload is 48 bytes");
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, descriptor_tag, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, form, 4, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, size, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, inner_type, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, return_type, 24, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, align, 32, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, flags, 36, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, field_count, 40, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_type, node_kind, 44, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, descriptor_tag, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, flags, 4, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, owner_type, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, value_type, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, offset, 24, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, count, 32, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_field, via, 40, 8);

/* ---- Doc payload: the text of documentation (docs/22) ------------------ */
/* A Doc object holds text. The payload opens with these words, and the rest of
 * it is that text in the codec the flag names. Placements of the doc are
 * PYMERGETIC_RXF_REF_DOC references, never a runtime ref. */

/* Kinds of doc. */
enum {
    PYMERGETIC_RXF_DOC_KIND_NARRATES = 0, /* the text of an object, its subject */
    PYMERGETIC_RXF_DOC_KIND_TOPIC = 1,    /* a named topic of the documentation */
    PYMERGETIC_RXF_DOC_KIND_TODO = 2      /* an intent to change the image */
};

/* The status word of a todo: open or resolved. */
enum { PYMERGETIC_RXF_DOC_STATUS_OPEN = 0, PYMERGETIC_RXF_DOC_STATUS_RESOLVED = 1 };

/* The codec of the text behind the payload. LZ4 is the default where it pays;
 * RAW keeps the text as it was written. */
enum { PYMERGETIC_RXF_DOC_CODEC_LZ4 = 0, PYMERGETIC_RXF_DOC_CODEC_RAW = 1 };

/* The role of a doc edge: why the doc sits on the object or topic. A placement
 * narrates, attaches, or references its source; a topic carries parent
 * (acyclic containment) and related (see-also) to the topics around it. */
enum {
    PYMERGETIC_RXF_DOC_ROLE_NARRATES = 0,
    PYMERGETIC_RXF_DOC_ROLE_ATTACHES = 1,
    PYMERGETIC_RXF_DOC_ROLE_REFERENCES = 2,
    PYMERGETIC_RXF_DOC_ROLE_PARENT = 3,
    PYMERGETIC_RXF_DOC_ROLE_RELATED = 4
};

typedef struct pymergetic_rxf_doc {
    uint32_t kind;   /* PYMERGETIC_RXF_DOC_KIND_* */
    uint32_t status; /* PYMERGETIC_RXF_DOC_STATUS_* */
    uint32_t codec;  /* PYMERGETIC_RXF_DOC_CODEC_* */
} pymergetic_rxf_doc;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_doc) == 12, "doc payload words are 12 bytes");
PYMERGETIC_RXF_PIN(pymergetic_rxf_doc, kind, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_doc, status, 4, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_doc, codec, 8, 4);

/* ---- one arena: the superblock and the object record (docs/32) ----------
 * The one-arena object model supersedes the RXFB v6 header/index/cell split
 * above: one linear region, a 4 KiB superblock as the only fixed structure,
 * and everything after the superblock an object. A handle is (index, gen) into
 * the object table, so a stale handle fails its check instead of resolving.
 * The object record is a 24-byte header with its payload inline behind it,
 * packed in 8-byte slots. Cell size = 24 + ceil(payload/8) slots.
 */

/* The superblock: 4 KiB at byte 0, the only non-object. The rest of it is
 * zero, reserved; the version byte is the gate for later formats. */
#define PYMERGETIC_RXF_SUPER_SIZE 4096U
#define PYMERGETIC_RXF_SUPER_VERSION 1U

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_superblock {
    uint8_t magic[4];  /* "RXFA" */
    uint32_t version;  /* PYMERGETIC_RXF_SUPER_VERSION */
    uint64_t committed; /* first free offset */
    uint64_t limit;     /* end of the claimed region */
    uint32_t root;      /* root handle: the image itself */
    uint32_t entry;     /* entry handle: the first run */
    uint8_t reserved[PYMERGETIC_RXF_SUPER_SIZE - 32]; /* must be zero */
} pymergetic_rxf_superblock;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_superblock) == PYMERGETIC_RXF_SUPER_SIZE,
                             "superblock is one page");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_superblock) == 8,
                             "superblock alignment");
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, magic, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, version, 4, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, committed, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, limit, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, root, 24, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, entry, 28, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_superblock, reserved, 32, PYMERGETIC_RXF_SUPER_SIZE - 32);

/* A handle is (index, gen): the index names a slot of the object table and the
 * generation makes a stale handle fail its check instead of resolving. The
 * all-ones handle is PYMERGETIC_RXF_HANDLE32_NONE. */
typedef uint32_t pymergetic_rxf_handle32;
#define PYMERGETIC_RXF_HANDLE32_NONE 0xFFFFFFFFU
#define PYMERGETIC_RXF_HANDLE32_INDEX_BITS 20U
#define PYMERGETIC_RXF_HANDLE32_GEN_BITS 12U
#define PYMERGETIC_RXF_HANDLE32_INDEX_MASK                                                             \
    ((1U << PYMERGETIC_RXF_HANDLE32_INDEX_BITS) - 1U)
#define PYMERGETIC_RXF_HANDLE32_GEN_MASK ((1U << PYMERGETIC_RXF_HANDLE32_GEN_BITS) - 1U)
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_HANDLE32_INDEX_BITS + PYMERGETIC_RXF_HANDLE32_GEN_BITS ==
                                 32,
                             "a handle fills 32 bits");

static inline pymergetic_rxf_handle32 pymergetic_rxf_handle32_pack(uint32_t index, uint32_t gen) {
    return (pymergetic_rxf_handle32)(((gen & PYMERGETIC_RXF_HANDLE32_GEN_MASK)
                                      << PYMERGETIC_RXF_HANDLE32_INDEX_BITS) |
                                     (index & PYMERGETIC_RXF_HANDLE32_INDEX_MASK));
}

static inline uint32_t pymergetic_rxf_handle32_index(pymergetic_rxf_handle32 handle) {
    return handle & PYMERGETIC_RXF_HANDLE32_INDEX_MASK;
}

static inline uint32_t pymergetic_rxf_handle32_gen(pymergetic_rxf_handle32 handle) {
    return (handle >> PYMERGETIC_RXF_HANDLE32_INDEX_BITS) & PYMERGETIC_RXF_HANDLE32_GEN_MASK;
}

/* Object kinds. FREE marks a free span: its payload carries the FREE ring
 * links, so a later allocation can split one. */
enum {
    PYMERGETIC_RXF_OBJ_MODULE = 1,
    PYMERGETIC_RXF_OBJ_FUNC = 2,
    PYMERGETIC_RXF_OBJ_HASH = 3,
    PYMERGETIC_RXF_OBJ_SPEC = 4,
    PYMERGETIC_RXF_OBJ_SYM = 5,
    PYMERGETIC_RXF_OBJ_BLOB = 6,
    PYMERGETIC_RXF_OBJ_FRAME = 7
};
#define PYMERGETIC_RXF_OBJ_FREE 0xFFFFU

/* Object flags. */
#define PYMERGETIC_RXF_OFLAG_RO 0x1U
#define PYMERGETIC_RXF_OFLAG_ROOT 0x2U
#define PYMERGETIC_RXF_OFLAG_DIRTY 0x4U
#define PYMERGETIC_RXF_OFLAG_JOURNALED 0x8U

/* The object header: 24 bytes, then the payload inline in 8-byte slots. */
#define PYMERGETIC_RXF_OBJECT_ALIGN 8U
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_object {
    uint16_t kind;    /* PYMERGETIC_RXF_OBJ_* */
    uint16_t gen;     /* generation (stale-handle detection) */
    uint16_t flags;   /* PYMERGETIC_RXF_OFLAG_* */
    uint16_t name;    /* symbol id (interned, 0 = anonymous) */
    uint32_t parent;  /* handle of the parent object */
    uint32_t link;    /* next sibling under the parent */
    uint32_t refs;    /* reference count */
    uint32_t payload; /* payload length in bytes */
} pymergetic_rxf_object;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_object) == 24, "object header is 24 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_object) ==
                                 PYMERGETIC_RXF_OBJECT_ALIGN,
                             "object header alignment");
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, kind, 0, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, gen, 2, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, flags, 4, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, name, 6, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, parent, 8, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, link, 12, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, refs, 16, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_object, payload, 20, 4);

/* object size = 24 + ceil(payload/8) slots, packed 8-byte aligned */
static inline __attribute__((unused)) uint64_t
pymergetic_rxf_object_size(const pymergetic_rxf_object *object) {
    return (uint64_t)sizeof(pymergetic_rxf_object) +
           (((uint64_t)object->payload + 7ULL) & ~7ULL);
}

/* The FREE ring links (docs/32 1.3): a FREE span whose payload room is at least
 * 16 bytes carries the ring in its payload - prev and next are the arena offsets
 * of the neighbouring FREE objects - and the ring is circular. The span length is
 * the object size (24 + room), so the links are the only payload words. The end
 * marker (the FREE span at superblock.committed) is the ring anchor: a span with
 * room below 16 cannot carry links and stays a linkless fragment until the tail
 * merge or compaction takes it. */
#define PYMERGETIC_RXF_FREE_PREV 0U
#define PYMERGETIC_RXF_FREE_NEXT 8U
#define PYMERGETIC_RXF_FREE_LINKS 16U
#define PYMERGETIC_RXF_FREE_MIN_SPAN                                                                    \
    ((uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS)

PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_FREE_MIN_SPAN == 40,
                             "a ring free span is 40 bytes (24 header + 16 links)");

/* The object index (sys/objects, docs/32 2): the payload of the index object is a
 * two-word head and then `capacity` slot entries. slot[index] is the one hop a
 * handle takes (docs/32 4); the entry carries the published identity - kind and
 * generation - and the arena slot of the record. The record mirrors both and a
 * handle is checked against both before it touches payload bytes. */
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_slot {
    uint16_t kind;
    uint16_t gen;
    uint32_t reserved;
    uint64_t slot; /* the arena offset of the record */
} pymergetic_rxf_slot;

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_slots {
    uint32_t count;    /* entries published: the next free index */
    uint32_t capacity; /* entries this table holds */
    /* pymergetic_rxf_slot slot[capacity]; */
} pymergetic_rxf_slots;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_slot) == 16, "a slot entry is 16 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_slots) == 8, "the slot head is 8 bytes");
PYMERGETIC_RXF_PIN(pymergetic_rxf_slot, kind, 0, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_slot, gen, 2, 2);
PYMERGETIC_RXF_PIN(pymergetic_rxf_slot, slot, 8, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_slots, count, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_slots, capacity, 4, 4);

/* The object index is the first object of the arena (docs/32 1: the skeleton at
 * byte 4096) and it never moves: it is the one anchor a cold attach needs, since
 * every handle resolves through the table. Entry 0 is the table's own identity.
 * The capacity is chosen at bootstrap and a full table refuses (IndexFull), the
 * shape the v6 index has. */
#define PYMERGETIC_RXF_INDEX_OFFSET PYMERGETIC_RXF_SUPER_SIZE
#define PYMERGETIC_RXF_INDEX_SELF 0U
#define PYMERGETIC_RXF_INDEX_SYMS 1U /* the interned symbol table's entry */

/* A Module's payload (docs/32 1.2): the head of the children chain and its
 * length. The chain itself is the header `link` fields - one forward walk from
 * the head, not a ring. */
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_module {
    uint32_t children; /* handle32 of the first child, PYMERGETIC_RXF_HANDLE32_NONE = none */
    uint32_t count;
} pymergetic_rxf_module;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_module) == 8, "a module payload is 8 bytes");
PYMERGETIC_RXF_PIN(pymergetic_rxf_module, children, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_module, count, 4, 4);

/* The interned symbol table (sys/syms, docs/32 2): a head, then `capacity` string
 * offsets from the payload start, then the packed NUL-terminated strings. Id 0 is
 * anonymous and stored nowhere; the first interned string takes id 1. The table
 * grows by moving to a larger object: the index entry is repointed and the handle
 * stands. */
typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_syms {
    uint32_t count;    /* the next id to hand out */
    uint32_t capacity; /* string offsets this table holds */
    /* uint32_t offset[capacity]; the strings are packed behind the offsets */
} pymergetic_rxf_syms;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_syms) == 8, "the syms head is 8 bytes");
PYMERGETIC_RXF_PIN(pymergetic_rxf_syms, count, 0, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_syms, capacity, 4, 4);
#define PYMERGETIC_RXF_SYMS_ANONYMOUS 0U

#ifdef __cplusplus
}
#endif

#endif
