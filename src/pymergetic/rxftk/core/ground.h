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
#define PYMERGETIC_RXF_PIN(type, field, offset, size)                                          \
    PYMERGETIC_RXF_GROUND_ASSERT(offsetof(type, field) == (offset), #type "." #field " offset"); \
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
    uint8_t magic[4];       /* "RXFB" */
    uint32_t version;       /* PYMERGETIC_RXF_ARENA_VERSION */
    uint32_t header_size;   /* 192 */
    uint32_t flags;
    uint64_t body_size;     /* bytes from arena start to the end of the cell stream */
    uint64_t committed_size;
    uint64_t limit;         /* PYMERGETIC_RXF_LIMIT_UNKNOWN = unknown */
    uint64_t frontier;      /* relative to cells_off */
    uint64_t index_off;     /* directory of index chunk cell offsets; it ends at cells_off */
    uint64_t cells_off;
    uint64_t names_off;     /* offset of the NAME block cell that takes the next name */
    uint64_t names_size;    /* bytes of its payload in use */
    uint64_t index_chunks;  /* INDEX block cells in use */
    uint64_t handle_count;  /* slots handed out */
    uint64_t free_head;     /* first free slot, or PYMERGETIC_RXF_HANDLE_NONE */
    uint64_t entry;         /* entry Function handle, or PYMERGETIC_RXF_HANDLE_NONE */
    uint64_t root;          /* root module handle */
    uint32_t cell_align;    /* PYMERGETIC_RXF_CELL_ALIGN */
    uint32_t page_size;
    uint8_t reserved[64];   /* must be zero */
} pymergetic_rxf_arena_header;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_arena_header) == 192, "arena header is 192 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_arena_header) % 64 == 0, "single block: multiple of 64");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_arena_header) == 8, "arena header alignment");
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
#define PYMERGETIC_RXF_INDEX_FREE 1ULL /* bit 0 set: free; the remaining bits link the next free slot */
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_index_entry) == 8, "array record: power of two");

/* ---- object cell header: 64 bytes -------------------------------------- */
#define PYMERGETIC_RXF_FLAG_KIND_SHIFT 0   /* node kind, 8 bits */
#define PYMERGETIC_RXF_FLAG_STATE_SHIFT 8  /* provenance, disposition, cleanliness, mobility: 6 bits */
#define PYMERGETIC_RXF_FLAG_OWNER_SHIFT 14 /* owner kind, 4 bits */
#define PYMERGETIC_RXF_FLAG_KIND_MASK 0xFFU

/* Kind 255 marks a block cell: arena bookkeeping that lives in the cell stream
 * so that one sequential walk covers everything. A block has no handle. Its
 * `type` field holds the block kind. */
#define PYMERGETIC_RXF_KIND_BLOCK 255U
/* Kind 254 marks a dead cell: its slot was freed or now holds a newer cell. The
 * bytes stay in the stream until compaction. */
#define PYMERGETIC_RXF_KIND_DEAD 254U
#define PYMERGETIC_RXF_BLOCK_INDEX 1U /* payload: PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES index entries */
#define PYMERGETIC_RXF_BLOCK_NAMES 2U /* payload: NUL-terminated names, packed */
#define PYMERGETIC_RXF_NAME_BLOCK_PAYLOAD 4096U

/* Object kinds (v5 NodeKind values; 9 stays reserved; 20 and 21 are new). The kind of a
 * new object is the `node_kind` of its Type, and DATA when the Type names none. */
enum {
    PYMERGETIC_RXF_NODE_ROOT = 0, PYMERGETIC_RXF_NODE_GROUP = 1, PYMERGETIC_RXF_NODE_NAMESPACE = 2, PYMERGETIC_RXF_NODE_CARD = 3,
    PYMERGETIC_RXF_NODE_TYPE = 4, PYMERGETIC_RXF_NODE_FIELD = 5, PYMERGETIC_RXF_NODE_FN = 6, PYMERGETIC_RXF_NODE_CODE = 7,
    PYMERGETIC_RXF_NODE_DATA = 8, PYMERGETIC_RXF_NODE_SECTION = 10, PYMERGETIC_RXF_NODE_LIMIT = 11, PYMERGETIC_RXF_NODE_IMPORT = 12,
    PYMERGETIC_RXF_NODE_EXPORT = 13, PYMERGETIC_RXF_NODE_VIEW = 14, PYMERGETIC_RXF_NODE_TOOL = 15, PYMERGETIC_RXF_NODE_BLOB = 16,
    PYMERGETIC_RXF_NODE_CHANNEL = 17, PYMERGETIC_RXF_NODE_TRANSACTION = 18, PYMERGETIC_RXF_NODE_MODULE = 19,
    PYMERGETIC_RXF_NODE_ENUMERATOR = 20, /* one named value of an enumeration Type */
    PYMERGETIC_RXF_NODE_ALIAS = 21       /* a short name for another object, in the scope of a Module */
};

/* Object state bits inside the state field of the flags. */
#define PYMERGETIC_RXF_STATE_PROVENANCE_SHIFT 0
#define PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT 1
#define PYMERGETIC_RXF_STATE_CLEANLINESS_SHIFT 3
#define PYMERGETIC_RXF_STATE_MOBILITY_SHIFT 4
enum { PYMERGETIC_RXF_PROVENANCE_RUNTIME = 0, PYMERGETIC_RXF_PROVENANCE_IMAGE = 1 };
enum { PYMERGETIC_RXF_DISPOSITION_RETAIN = 0, PYMERGETIC_RXF_DISPOSITION_TRANSIENT = 1, PYMERGETIC_RXF_DISPOSITION_TOMBSTONE = 2 };
enum { PYMERGETIC_RXF_CLEANLINESS_CLEAN = 0, PYMERGETIC_RXF_CLEANLINESS_DIRTY = 1 };
enum { PYMERGETIC_RXF_MOBILITY_MOVABLE = 0, PYMERGETIC_RXF_MOBILITY_PINNED = 1, PYMERGETIC_RXF_MOBILITY_RELOCATABLE = 2 };
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
    pymergetic_rxf_handle parent;     /* PYMERGETIC_RXF_HANDLE_NONE for the root */
    uint64_t payload_size;
    uint64_t generation;
    uint64_t name;          /* arena offset of a NUL-terminated name inside a NAME block; 0 = none */
    uint32_t flags;
    uint32_t ref_count;
    uint64_t forward;       /* compaction forwarding offset, 0 when unused */
    uint32_t index;         /* position among the siblings that are listed in order; 0 when the order is the name */
    uint32_t reserved;      /* must be zero */
} pymergetic_rxf_cell;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_cell) == 64, "cell header is 64 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(PYMERGETIC_RXF_ALIGNOF(pymergetic_rxf_cell) == 8, "cell header alignment");
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
static inline __attribute__((unused)) uint64_t pymergetic_rxf_cell_size(const pymergetic_rxf_cell *cell) {
    const uint64_t raw = (uint64_t)sizeof(pymergetic_rxf_cell) + ((cell->payload_size + 15ULL) & ~15ULL) +
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
#define PYMERGETIC_RXF_FIELD_FLAG_CHILDREN 0x10U /* the children of the value type below an object are the list this field names */
#define PYMERGETIC_RXF_FIELD_FLAG_INVERSE 0x20U  /* the objects of the value type that reach this object through `via` */

/* The forms of a Type (v5 TypeForm values). */
enum {
    PYMERGETIC_RXF_FORM_META = 0, PYMERGETIC_RXF_FORM_VOID = 1, PYMERGETIC_RXF_FORM_BOOL = 2, PYMERGETIC_RXF_FORM_UINT = 3,
    PYMERGETIC_RXF_FORM_SINT = 4, PYMERGETIC_RXF_FORM_FLOAT = 5, PYMERGETIC_RXF_FORM_BYTES = 6, PYMERGETIC_RXF_FORM_STRUCT = 7,
    PYMERGETIC_RXF_FORM_UNION = 8, PYMERGETIC_RXF_FORM_ENUM = 9, PYMERGETIC_RXF_FORM_ARRAY = 10, PYMERGETIC_RXF_FORM_REF = 11,
    PYMERGETIC_RXF_FORM_FUNCTION = 12, PYMERGETIC_RXF_FORM_CONTROL = 13, PYMERGETIC_RXF_FORM_EXPRESSION = 14,
    PYMERGETIC_RXF_FORM_EXECUTION = 15, PYMERGETIC_RXF_FORM_RESERVED_16 = 16, PYMERGETIC_RXF_FORM_OPAQUE = 17,
    PYMERGETIC_RXF_FORM_COUNT = 18
};

/* The bits of the flags of a Type. */
#define PYMERGETIC_RXF_TYPE_FLAG_VARIABLE 0x1U         /* the payload may be longer than `size` */
#define PYMERGETIC_RXF_TYPE_FLAG_SIGNED 0x2U           /* an integer that carries a sign */
#define PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING 0x4U  /* the layout is described by Field objects */
#define PYMERGETIC_RXF_TYPE_FLAG_BITSET 0x8U           /* an enumeration whose values are bits that combine */

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_type {
    uint32_t descriptor_tag;
    uint32_t form;
    uint64_t size;
    pymergetic_rxf_handle inner_type;
    pymergetic_rxf_handle return_type;
    uint32_t align;
    uint32_t flags;
    uint32_t field_count;
    uint32_t node_kind;     /* kind of the objects of this type; 0 means DATA */
} pymergetic_rxf_type;

typedef struct PYMERGETIC_RXF_ALIGNED8 pymergetic_rxf_field {
    uint32_t descriptor_tag;
    uint32_t flags;         /* PYMERGETIC_RXF_FIELD_FLAG_REF, ... */
    pymergetic_rxf_handle owner_type;
    pymergetic_rxf_handle value_type;
    uint64_t offset;        /* byte offset inside the owner payload */
    uint64_t count;         /* array length, 1 for a scalar */
    uint64_t via;           /* INVERSE: the Field or Role the other objects point through; else unset */
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

#ifdef __cplusplus
}
#endif

#endif
