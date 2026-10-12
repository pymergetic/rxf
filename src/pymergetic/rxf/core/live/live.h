#ifndef PYMERGETIC_RXF_LIVE_H
#define PYMERGETIC_RXF_LIVE_H

/*
 * The live area: what a running image shares with whoever talks to it (docs/19). One struct at a
 * fixed place describes the memory around the arena, the two rings, and the state of the layer.
 * Plain C, little-endian, the layout pinned here and in tools/native/link.py.
 *
 *   arena   the image itself (an RXFB 6 arena; `arena_capacity` bytes of address space, of which
 *           `arena_committed` are usable: the peer commits more pages and says so with GROW)
 *   log     the undo log of a transaction
 *   stage   the bytes of the delta that is being sent in
 *   work    the lookup table of the image, then scratch memory of one request
 *   emit    the canonical bytes of the image, written on request
 *   rx, tx  the rings: frames in, frames out (the capacity is a power of two)
 *
 * The ring counters only grow (they wrap at 2^32). The producer owns `head`, the consumer `tail`,
 * so a ring needs no lock. The addresses are plain addresses of the machine that runs the code.
 */

#include <pymergetic/rxf/core/ground.h>

#include <stddef.h>
#include <stdint.h>

#define PYMERGETIC_RXF_LIVE_MAGIC 0x4556494CU /* "LIVE" */
#define PYMERGETIC_RXF_LIVE_VERSION 1U

/* The machines of a linked image (the Code object of a function is named after it). */
#define PYMERGETIC_RXF_MACHINE_X86_64 1U
#define PYMERGETIC_RXF_MACHINE_AARCH64 2U
#define PYMERGETIC_RXF_MACHINE_WASM32 3U

/* The runtime context of a linked image (native/stdlib_runtime.h) as the live layer links it: the
 * function slots (24 bytes each: id, generation, address) and the object entries (80 bytes each:
 * id, generation, type id, address, size, capacity, state, three words; the first
 * PYMERGETIC_RXF_LINK_SPARE are for what a program allocates and are left alone). */
#define PYMERGETIC_RXF_LINK_SPARE 8U
#define PYMERGETIC_RXF_LINK_SLOT_BYTES 24U
#define PYMERGETIC_RXF_LINK_ENTRY_BYTES 80U

typedef struct __attribute__((aligned(8))) pymergetic_rxf_live_area {
    uint32_t magic;
    uint32_t version;
    uint64_t arena_base, arena_capacity;
    uint64_t log_base, log_capacity;
    uint64_t stage_base, stage_capacity;
    uint64_t work_base, work_capacity;
    uint64_t emit_base, emit_capacity;
    uint64_t rx_base, rx_capacity;
    uint64_t tx_base, tx_capacity;
    uint32_t rx_head, rx_tail;   /* the peer writes at head; the image reads at tail */
    uint32_t tx_head, tx_tail;   /* the image writes at head; the peer reads at tail */
    uint32_t table_slots;        /* power of two; 4 bytes each at the start of `work` */
    uint32_t index_state;        /* 1 when the lookup table is in step with the arena */
    uint32_t stage_length;       /* bytes of the staged delta */
    uint32_t last_status;        /* status of the last request */
    uint64_t frames;             /* requests served */
    uint64_t commits;            /* deltas applied */
    uint64_t rejects;            /* deltas refused */
    uint32_t break_request;      /* the peer asks the loop to stop at the next safe point */
    uint32_t break_ack;          /* the loop says it stopped */
    uint64_t emit_length;        /* bytes of the last dump */
    uint8_t emit_digest[32];     /* SHA-256 of the last dump */
    uint64_t arena_committed;    /* bytes of the arena region that are usable; 0: all of arena_capacity */
    uint64_t grow_request;       /* an end offset that should do after a refusal for lack of room (capped by the capacity); 0: none */
    uint64_t compactions;        /* arenas compacted in place */
    uint32_t entries_capacity;   /* object entries of the context that the image may fill (spare ones included); 0: no linking */
    uint32_t machine;            /* PYMERGETIC_RXF_MACHINE_*: whose Code the function slots point at */
    uint32_t in_run;             /* the interpreter is on the stack below the pump (it sets and clears this) */
    uint32_t relocated;          /* the pump runs from a copy in the log region (the leaf sets and clears this) */
    uint32_t relocate;           /* the pump stopped before a compaction and asks its leaf for a copy of itself */
    uint32_t epoch;              /* counts what changed the arena or the links under running code: a commit, a compaction, a relink */
} pymergetic_rxf_live_area;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_live_area) == 272, "live area is 272 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, arena_base) == 8, "arena_base");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, tx_capacity) == 112, "tx_capacity");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, rx_head) == 120, "rx_head");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, table_slots) == 136, "table_slots");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, stage_length) == 144, "stage_length");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, frames) == 152, "frames");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, break_request) == 176, "break_request");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, emit_length) == 184, "emit_length");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, emit_digest) == 192, "emit_digest");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, arena_committed) == 224, "arena_committed");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, compactions) == 240, "compactions");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, entries_capacity) == 248, "entries_capacity");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, in_run) == 256, "in_run");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, relocated) == 260, "relocated");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, relocate) == 264, "relocate");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(pymergetic_rxf_live_area, epoch) == 268, "epoch");

/* ---- frames --------------------------------------------------------------------------------- */
/* A frame is a 16-byte header, up to 1024 bytes of payload, and a CRC-32 (IEEE, reflected) of both.
 * An answer carries the op of the request with the high bit set, the same sequence number, and a
 * status. A receiver that finds no magic, a length over 1024, or a wrong CRC skips one byte. */
#define PYMERGETIC_RXF_FRAME_MAGIC 0x5852U /* "RX" in little-endian bytes */
#define PYMERGETIC_RXF_FRAME_MAX 1024U
#define PYMERGETIC_RXF_FRAME_HEADER 16U
#define PYMERGETIC_RXF_FRAME_TRAILER 4U

typedef struct pymergetic_rxf_frame_header {
    uint16_t magic;
    uint8_t op;
    uint8_t flags;
    uint32_t sequence;
    uint32_t argument;
    uint16_t length;
    uint16_t status;
} pymergetic_rxf_frame_header;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_frame_header) == 16, "frame header is 16 bytes");

enum {
    PYMERGETIC_RXF_OP_PING = 0x01,        /* the payload comes back */
    PYMERGETIC_RXF_OP_HELLO = 0x02,       /* version, handle count, body size, capacities */
    PYMERGETIC_RXF_OP_HEADER = 0x03,      /* the 192 bytes of the arena header */
    PYMERGETIC_RXF_OP_RESOLVE = 0x04,     /* path -> handle */
    PYMERGETIC_RXF_OP_PATH = 0x05,        /* handle -> path */
    PYMERGETIC_RXF_OP_GET = 0x06,         /* handle -> the cell header and the name */
    PYMERGETIC_RXF_OP_READ = 0x07,        /* handle, offset, count -> payload bytes */
    PYMERGETIC_RXF_OP_CHILDREN = 0x08,    /* handle, skip -> a page of child handles */
    PYMERGETIC_RXF_OP_REFS = 0x09,        /* handle, skip -> a page of references */
    PYMERGETIC_RXF_OP_STAGE_RESET = 0x10, /* forget the staged bytes */
    PYMERGETIC_RXF_OP_STAGE_WRITE = 0x11, /* argument = offset in the stage, payload = bytes */
    PYMERGETIC_RXF_OP_STAGE_COMMIT = 0x12,/* length, SHA-256 of the stage: apply it, all or nothing */
    PYMERGETIC_RXF_OP_DIGEST = 0x20,      /* the canonical digest of the image */
    PYMERGETIC_RXF_OP_EMIT = 0x21,        /* write the canonical bytes: length and digest */
    PYMERGETIC_RXF_OP_EMIT_READ = 0x22,   /* argument = offset, payload = u32 count -> bytes of the dump */
    PYMERGETIC_RXF_OP_COMPACT = 0x23,     /* compact the arena in place: handles, body size, digest, bytes won */
    PYMERGETIC_RXF_OP_GROW = 0x24,        /* payload = u64 bytes: the arena region is committed up to there */
    PYMERGETIC_RXF_OP_RELINK = 0x25,      /* write the object entries and function slots of the context from the arena */
    PYMERGETIC_RXF_OP_BREAK = 0x30,       /* argument 1 sets, 0 clears the break request; the answer says whether the run has stopped (u32) */
    PYMERGETIC_RXF_OP_ANSWER = 0x80
};

enum {
    PYMERGETIC_RXF_LIVE_OK = 0,
    PYMERGETIC_RXF_LIVE_BAD_FRAME = 1,
    PYMERGETIC_RXF_LIVE_UNKNOWN_OP = 2,
    PYMERGETIC_RXF_LIVE_NOT_FOUND = 3,
    PYMERGETIC_RXF_LIVE_BAD_REQUEST = 4,
    PYMERGETIC_RXF_LIVE_NO_ROOM = 5,
    PYMERGETIC_RXF_LIVE_DIGEST = 6,   /* the staged bytes are not the bytes that were announced */
    PYMERGETIC_RXF_LIVE_APPLY = 7,    /* the delta was refused; the payload names op and reason; the image is as it was */
    PYMERGETIC_RXF_LIVE_BAD_IMAGE = 8,/* the area or the arena is not usable */
    PYMERGETIC_RXF_LIVE_REFUSED = 9   /* a request that is well formed and not done; the payload is (0, reason) */
};

/* Why an apply was refused: the second word of the payload of an APPLY status. */
enum {
    PYMERGETIC_RXF_REASON_NONE = 0,
    PYMERGETIC_RXF_REASON_FORMAT = 1,    /* the bytes are not a delta */
    PYMERGETIC_RXF_REASON_BASE = 2,      /* the base digest is not the digest of the image */
    PYMERGETIC_RXF_REASON_PATH = 3,      /* a path does not resolve, or a parent is missing */
    PYMERGETIC_RXF_REASON_EXISTS = 4,    /* a create names an object that is there */
    PYMERGETIC_RXF_REASON_NAME = 5,      /* a name cannot be used */
    PYMERGETIC_RXF_REASON_ARENA = 6,     /* the arena has no room, or the index is full */
    PYMERGETIC_RXF_REASON_JOURNAL = 7,   /* the undo log is full */
    PYMERGETIC_RXF_REASON_SCRATCH = 8,   /* the work memory is full */
    PYMERGETIC_RXF_REASON_DANGLING = 9,  /* something points at an object that is gone */
    PYMERGETIC_RXF_REASON_PROTECTED = 10,/* root, Type, Field */
    PYMERGETIC_RXF_REASON_LINK = 11,     /* the image does not link after the delta */
    PYMERGETIC_RXF_REASON_LAYOUT = 12,   /* a type is more than the live layer reads */
    PYMERGETIC_RXF_REASON_PINNED = 13,   /* an object is pinned: compaction would move it */
    PYMERGETIC_RXF_REASON_RANGE = 14,    /* a size that is not between what is committed and what exists */
    PYMERGETIC_RXF_REASON_UNBOUND = 15   /* there is no context to link */
};

/* ---- the delta in physical form -------------------------------------------------------------
 * 64-byte header, a block of NUL-terminated strings, then the operations; all little-endian.
 * Strings are referenced by their offset in the block; a path is the dotted path of an object
 * ("" is the root), 0xFFFFFFFF means none. See docs/19. */
#define PYMERGETIC_RXF_DELTA_MAGIC 0x44465852U /* "RXFD" */
#define PYMERGETIC_RXF_DELTA_VERSION 1U
#define PYMERGETIC_RXF_DELTA_CHECK_BASE 1U     /* header flag: the digest of the image must be base_digest */
#define PYMERGETIC_RXF_STRING_NONE 0xFFFFFFFFU

typedef struct pymergetic_rxf_delta_header {
    uint32_t magic;
    uint32_t version;
    uint32_t op_count;
    uint32_t string_bytes;
    uint32_t flags;
    uint32_t reserved0;
    uint8_t base_digest[32];
    uint8_t reserved1[8];
} pymergetic_rxf_delta_header;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_delta_header) == 64, "delta header is 64 bytes");

enum {
    PYMERGETIC_RXF_DELTA_CREATE = 1, /* a new object; same body as SET */
    PYMERGETIC_RXF_DELTA_SET = 2,    /* the whole state of an object that is there */
    PYMERGETIC_RXF_DELTA_DELETE = 3,
    PYMERGETIC_RXF_DELTA_ENTRY = 4
};

/* CREATE and SET: this header, the payload (padded to 8 bytes), `patch_count` patches, `ref_count` references. */
typedef struct pymergetic_rxf_delta_object {
    uint32_t op;
    uint32_t path;        /* string */
    uint32_t type;        /* string: the path of the Type object */
    uint32_t flags;       /* the flags of the cell */
    uint32_t index;
    uint32_t reserved0;
    uint64_t generation;
    uint32_t payload_length;
    uint32_t ref_count;
    uint32_t patch_count;
    uint32_t reserved1;
} pymergetic_rxf_delta_object;

typedef struct pymergetic_rxf_delta_patch { /* a handle inside the payload, named by a path */
    uint32_t offset;
    uint32_t path; /* string, or PYMERGETIC_RXF_STRING_NONE for an unset handle */
} pymergetic_rxf_delta_patch;

typedef struct pymergetic_rxf_delta_ref {
    uint32_t target; /* string */
    uint32_t role;   /* string or none */
    uint16_t kind;
    uint16_t binding;
    uint32_t index;
} pymergetic_rxf_delta_ref;

typedef struct pymergetic_rxf_delta_path { /* DELETE and ENTRY */
    uint32_t op;
    uint32_t path; /* string; ENTRY: none clears the entry */
} pymergetic_rxf_delta_path;

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_delta_object) == 48, "delta object is 48 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_delta_patch) == 8, "delta patch is 8 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_delta_ref) == 16, "delta ref is 16 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(sizeof(pymergetic_rxf_delta_path) == 8, "delta path is 8 bytes");

#endif
