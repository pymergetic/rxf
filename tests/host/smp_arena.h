#ifndef RXF_SMP_ARENA_H
#define RXF_SMP_ARENA_H

/*
 * The arena of the SMP proof (docs/20, "Suspension is a data property"): one cell stream, a handle
 * index (index | generation), and compaction that slides cells and records where each one went in
 * C_FORWARD. This is the persistence half of the policy. The persistent part names everything by
 * handle and offset, never by a raw address, so a cell may move while runs are parked or mid-flight.
 *
 * A claim word per slot bounds how long an address stays valid: a carrier resolves a handle to a
 * payload address and claims the cell, and that address is good until the claim is released, which
 * happens at the next safe point. Compaction takes the claim itself before it copies, so a copy
 * never overlaps a live read or write. A moved cell leaves its forwarding offset in the old header
 * until the pass finishes (rxf_smp_compact_move then rxf_smp_compact_finish), and the old bytes are
 * poisoned afterwards: a stale raw pointer reads poison and is caught, it never reads a lie.
 */
#include <stdatomic.h>
#include <stdint.h>

/* A handle names a cell as index | generation: the low 32 bits are the slot of the index and the
 * high 32 bits the generation of that slot. Slots are handed out again when a cell is freed, so a
 * handle kept across a free is stale: its generation no longer matches, it resolves to nothing, and
 * it is refused. It never names the new cell of the same slot (the ABA refusal of docs/20). */
typedef uint64_t rxf_smp_handle;

#define RXF_SMP_HANDLE_NONE ((rxf_smp_handle)0)
#define RXF_SMP_HANDLE(index, generation) \
    (((rxf_smp_handle)(uint32_t)(generation) << 32) | (uint32_t)(index))
#define RXF_SMP_H_INDEX(h) ((uint32_t)(h))
#define RXF_SMP_H_GENERATION(h) ((uint32_t)((h) >> 32))

/* The 64-byte cell header of the arena (docs/17 "Arena and RXFB v6", the numbers of
 * native/compose/words.rxfs): C_FORWARD at byte 48 is the compaction forwarding offset. */
#define RXF_SMP_CELL_BYTES 64u
#define RXF_SMP_C_TYPE 0u
#define RXF_SMP_C_PAYLOAD_SIZE 16u
#define RXF_SMP_C_GENERATION 24u
#define RXF_SMP_C_FLAGS 40u
#define RXF_SMP_C_FORWARD 48u
#define RXF_SMP_C_INDEX 56u
#define RXF_SMP_FLAG_PINNED 1u

/* The kinds of cell this proof makes. */
enum {
    RXF_SMP_CELL_RUN = 1,
    RXF_SMP_CELL_DATA = 2,
    RXF_SMP_CELL_CHANNEL = 3,
    RXF_SMP_CELL_RING = 4,
    RXF_SMP_CELL_DELTA = 5,
    RXF_SMP_CELL_SCRATCH = 6
};

/* A claim word counts the addresses that are live on a cell: 0 is free, 1..n are the carriers
 * holding a resolved address, and RXF_SMP_CLAIM_MOVING is the compactor while it copies. The
 * claims are shared (a cell may have several readers) and exclusive only for the mover, because
 * compaction is the only thing an address has to be kept stable against. A carrier releases every
 * claim at the next safe point; it never holds an address longer than that, and it never carries
 * one across a safe point (docs/20, rule S7). */
#define RXF_SMP_CLAIM_MOVING 0x80000000u

/* When the build has AddressSanitizer, a vacated range is poisoned with the sanitizer as well as
 * with 0xA5 bytes: a stale raw address into it is a sanitizer error in that build and a checksum
 * failure in every build (docs/20, S8). */
#if defined(__SANITIZE_ADDRESS__)
#define RXF_SMP_ASAN 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define RXF_SMP_ASAN 1
#endif
#endif

/* One slot of the index: where the cell is, which generation the slot is on, and who claims it. */
typedef struct {
    _Atomic(uint32_t) claim;
    _Atomic(uint32_t) generation;
    _Atomic(uint64_t) offset; /* byte offset of the cell; 0 when the slot is free */
} rxf_smp_slot;

typedef struct {
    uint64_t moved_cells, moved_bytes, pinned_cells, checksum_fails, dead_cells;
    uint64_t claim_spins, claim_spins_max;
    uint64_t moved_by_type[8];
    uint64_t parked_moved, midflight_moved;
    /* The observer the harness gives the pass (docs/20, S7 and S8): it is called with the new
     * address of every moved run cell and data cell, with the claim of the cell held, and answers
     * 1 when the cell belongs to a run that is parked at this instant. It is also where the
     * harness counts a cell moved while its owner run is between safe points (mid-flight). */
    void *ctx;
    int (*observe)(void *ctx, void *payload, uint32_t type, rxf_smp_handle h);
} rxf_smp_compact_stats;

typedef struct {
    uint8_t *base;  /* the one arena block */
    uint64_t bytes; /* its size; offset 0 is left unused so that 0 means "free" */
    uint64_t frontier;
    rxf_smp_slot *slots;
    uint32_t slot_count;
    uint32_t next_index;
    /* The log of the last compaction pass: where every cell was and where it went. What the proof
     * reads to show relocation tracked in C_FORWARD while the carriers ran on (docs/20, S8). */
    uint64_t *mv_src, *mv_dst, *mv_size;
    uint32_t mv_count;
    uint64_t old_frontier;
    uint64_t freed; /* cells freed (kind 254 of docs/17) */
    /* What the compactor waited for claims (docs/20, S5): the wait is bounded by one safe point. */
    uint64_t claim_spins, claim_spins_max;
} rxf_smp_arena;

int rxf_smp_arena_create(rxf_smp_arena *a, uint64_t bytes, uint32_t slot_count);
void rxf_smp_arena_destroy(rxf_smp_arena *a);

/* Allocation is an edge operation here (the harness makes every cell before the carriers start;
 * the one writer that creates and frees scratch cells is the mutator of the witness, and concurrent
 * allocation is the allocator work of docs/17, not part of this proof). */
rxf_smp_handle rxf_smp_arena_alloc(rxf_smp_arena *a, uint32_t type, uint64_t payload_size,
                                   uint32_t flags);
void rxf_smp_arena_free(rxf_smp_arena *a, rxf_smp_handle h);

/* Resolve a handle to the payload address of the cell and add one claim to it. NULL when the
 * handle is stale (its slot generation moved) or when the compactor is moving the cell right now:
 * rxf_smp_arena_is_stale tells the two apart. A stale handle is refused, never rebound to the new
 * cell of the slot. The address is valid until rxf_smp_arena_release: across a safe point it is
 * not valid at all. */
void *rxf_smp_arena_resolve(rxf_smp_arena *a, rxf_smp_handle h);
void rxf_smp_arena_release(rxf_smp_arena *a, rxf_smp_handle h);
int rxf_smp_arena_is_stale(rxf_smp_arena *a, rxf_smp_handle h);

/* The offset a handle resolves to now (0 when stale). Only for the witness and the tests. */
uint64_t rxf_smp_arena_offset(rxf_smp_arena *a, rxf_smp_handle h);

/* One compaction pass, in two halves so the proof can look between them: move copies every live
 * cell down (honoring pins), writes the forwarding offset into each old header and publishes the
 * new offset in the index; finish poisons the bytes the pass vacated and leaves the rest to the
 * next pass. A pass may run while carriers execute: it waits for each claim in turn, bounded by one
 * safe point, and never touches a claimed cell. */
void rxf_smp_compact_move(rxf_smp_arena *a, rxf_smp_compact_stats *st);
void rxf_smp_compact_finish(rxf_smp_arena *a);

#endif
