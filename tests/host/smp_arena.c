/*
 * The arena of the SMP proof (docs/20): allocation by handle, and compaction that slides cells and
 * records where each one went in C_FORWARD. Nothing in this file knows about runs or carriers; it
 * is the persistence half of the policy. See smp_arena.h for the rules it enforces.
 */
#include "smp_arena.h"

#include <stdlib.h>
#include <string.h>

#ifdef RXF_SMP_ASAN
#include <sanitizer/asan_interface.h>
#endif

#define RXF_SMP_DEAD_TYPE 254u

static inline uint64_t read_u64(const uint8_t *p) {
    uint64_t v;
    memcpy(&v, p, 8);
    return v;
}

static inline void write_u64(uint8_t *p, uint64_t v) { memcpy(p, &v, 8); }

static inline uint64_t align16(uint64_t n) { return (n + 15u) & ~(uint64_t)15u; }

/* The whole extent of a cell: a 64-byte header and the payload padded to 16. The cells of this
 * proof carry no inline references (docs/17 keeps them after the payload; nothing here uses them,
 * so the walk is header + payload only). */
static uint64_t cell_bytes(uint64_t payload_size) {
    return align16(RXF_SMP_CELL_BYTES + align16(payload_size));
}

static inline uint32_t cell_type(const uint8_t *c) { return (uint32_t)read_u64(c + RXF_SMP_C_TYPE); }

static inline uint64_t cell_payload_size(const uint8_t *c) {
    return read_u64(c + RXF_SMP_C_PAYLOAD_SIZE);
}

static inline uint32_t cell_flags(const uint8_t *c) {
    return (uint32_t)read_u64(c + RXF_SMP_C_FLAGS);
}

static inline uint32_t cell_generation(const uint8_t *c) {
    return (uint32_t)read_u64(c + RXF_SMP_C_GENERATION);
}

int rxf_smp_arena_create(rxf_smp_arena *a, uint64_t bytes, uint32_t slot_count) {
    memset(a, 0, sizeof(*a));
    a->base = (uint8_t *)calloc(1, (size_t)bytes);
    a->slots = (rxf_smp_slot *)calloc(slot_count, sizeof(rxf_smp_slot));
    a->mv_src = (uint64_t *)calloc(slot_count, sizeof(uint64_t));
    a->mv_dst = (uint64_t *)calloc(slot_count, sizeof(uint64_t));
    a->mv_size = (uint64_t *)calloc(slot_count, sizeof(uint64_t));
    if (!a->base || !a->slots || !a->mv_src || !a->mv_dst || !a->mv_size) {
        rxf_smp_arena_destroy(a);
        return -1;
    }
    a->bytes = bytes;
    a->frontier = 64; /* offset 0 is never a cell, so 0 means "free" everywhere */
    a->slot_count = slot_count;
    a->next_index = 1; /* slot 0 is never handed out: the all-zero handle stays invalid */
    return 0;
}

void rxf_smp_arena_destroy(rxf_smp_arena *a) {
    free(a->base);
    free(a->slots);
    free(a->mv_src);
    free(a->mv_dst);
    free(a->mv_size);
    memset(a, 0, sizeof(*a));
}

rxf_smp_handle rxf_smp_arena_alloc(rxf_smp_arena *a, uint32_t type, uint64_t payload_size,
                                   uint32_t flags) {
    uint32_t index = 0;
    for (uint32_t i = 1; i < a->next_index; i++) {
        if (atomic_load_explicit(&a->slots[i].offset, memory_order_acquire) == 0) {
            index = i;
            break;
        }
    }
    if (index == 0) {
        if (a->next_index >= a->slot_count)
            return RXF_SMP_HANDLE_NONE;
        index = a->next_index++;
    }
    rxf_smp_slot *s = &a->slots[index];
    uint32_t generation = atomic_load_explicit(&s->generation, memory_order_relaxed) + 1u;
    if (generation == 0)
        generation = 1;
    uint64_t size = cell_bytes(payload_size);
    if (a->frontier + size > a->bytes)
        return RXF_SMP_HANDLE_NONE;
    uint64_t off = a->frontier;
    a->frontier += size;
    uint8_t *c = a->base + off;
#ifdef RXF_SMP_ASAN
    /* The bytes a compaction vacated were poisoned (rxf_smp_compact_finish); a cell that takes
     * them back is live memory again. */
    __asan_unpoison_memory_region(c, (size_t)size);
#endif
    memset(c, 0, (size_t)size);
    write_u64(c + RXF_SMP_C_TYPE, type);
    write_u64(c + RXF_SMP_C_PAYLOAD_SIZE, payload_size);
    write_u64(c + RXF_SMP_C_GENERATION, generation);
    write_u64(c + RXF_SMP_C_FLAGS, flags);
    write_u64(c + RXF_SMP_C_INDEX, index);
    atomic_store_explicit(&s->generation, generation, memory_order_release);
    atomic_store_explicit(&s->offset, off, memory_order_release);
    return RXF_SMP_HANDLE(index, generation);
}

void rxf_smp_arena_free(rxf_smp_arena *a, rxf_smp_handle h) {
    uint32_t index = RXF_SMP_H_INDEX(h);
    if (index == 0 || index >= a->next_index)
        return;
    rxf_smp_slot *s = &a->slots[index];
    if (atomic_load_explicit(&s->generation, memory_order_acquire) != RXF_SMP_H_GENERATION(h))
        return; /* a stale handle frees nothing */
    /* The free takes the cell out from under everyone the way the mover does: an exclusive claim,
     * so no reader and no copy is in flight on these bytes. */
    for (;;) {
        uint32_t want = 0;
        if (atomic_compare_exchange_strong_explicit(&s->claim, &want, RXF_SMP_CLAIM_MOVING,
                                                    memory_order_acq_rel, memory_order_acquire))
            break;
    }
    uint64_t off = atomic_load_explicit(&s->offset, memory_order_acquire);
    if (off == 0) {
        atomic_store_explicit(&s->claim, 0, memory_order_release);
        return;
    }
    uint8_t *c = a->base + off;
    write_u64(c + RXF_SMP_C_TYPE, RXF_SMP_DEAD_TYPE); /* kind 254 of docs/17: a dead cell */
    atomic_store_explicit(&s->offset, 0, memory_order_release);
    atomic_store_explicit(&s->claim, 0, memory_order_release);
    a->freed++;
}

void *rxf_smp_arena_resolve(rxf_smp_arena *a, rxf_smp_handle h) {
    uint32_t index = RXF_SMP_H_INDEX(h);
    if (index == 0 || index >= a->next_index)
        return NULL;
    rxf_smp_slot *s = &a->slots[index];
    uint32_t seen = atomic_load_explicit(&s->claim, memory_order_relaxed);
    while ((seen & RXF_SMP_CLAIM_MOVING) == 0) {
        if (atomic_compare_exchange_weak_explicit(&s->claim, &seen, seen + 1, memory_order_acq_rel,
                                                  memory_order_relaxed))
            break;
    }
    if (seen & RXF_SMP_CLAIM_MOVING)
        return NULL; /* the compactor is copying this cell right now: try again at the next step */
    uint32_t generation = atomic_load_explicit(&s->generation, memory_order_acquire);
    uint64_t off = atomic_load_explicit(&s->offset, memory_order_acquire);
    if (generation != RXF_SMP_H_GENERATION(h) || off == 0) {
        atomic_fetch_sub_explicit(&s->claim, 1, memory_order_release);
        return NULL; /* stale: the slot moved on; the handle names nothing and never the new cell */
    }
    /* The cell is handed out as its 64-byte header plus its payload: the caller gets the payload,
     * exactly where the compactor's observer is handed it (docs/20, S7). */
    return a->base + off + RXF_SMP_CELL_BYTES;
}

void rxf_smp_arena_release(rxf_smp_arena *a, rxf_smp_handle h) {
    uint32_t index = RXF_SMP_H_INDEX(h);
    if (index == 0 || index >= a->next_index)
        return;
    /* A claim is never held across a safe point, so the holder is always the one who releases it. */
    atomic_fetch_sub_explicit(&a->slots[index].claim, 1, memory_order_release);
}

uint64_t rxf_smp_arena_offset(rxf_smp_arena *a, rxf_smp_handle h) {
    uint32_t index = RXF_SMP_H_INDEX(h);
    if (index == 0 || index >= a->next_index)
        return 0;
    rxf_smp_slot *s = &a->slots[index];
    if (atomic_load_explicit(&s->generation, memory_order_acquire) != RXF_SMP_H_GENERATION(h))
        return 0;
    return atomic_load_explicit(&s->offset, memory_order_acquire);
}

int rxf_smp_arena_is_stale(rxf_smp_arena *a, rxf_smp_handle h) {
    uint32_t index = RXF_SMP_H_INDEX(h);
    if (index == 0 || index >= a->next_index)
        return 1; /* an index that was never handed out is stale by definition */
    rxf_smp_slot *s = &a->slots[index];
    return atomic_load_explicit(&s->generation, memory_order_acquire) != RXF_SMP_H_GENERATION(h) ||
           atomic_load_explicit(&s->offset, memory_order_acquire) == 0;
}

/* Take a claim by spinning until the cell has no address holders left: the holders are carriers
 * between two steps, and each gives its claims up at its next safe point (bounded by one step,
 * docs/20 S5). The spins are counted so the proof can show the wait is bounded in practice. */
static uint8_t *claim_cell(rxf_smp_arena *a, rxf_smp_slot *s) {
    uint64_t spins = 0;
    for (;;) {
        uint32_t want = 0;
        if (atomic_compare_exchange_strong_explicit(&s->claim, &want, RXF_SMP_CLAIM_MOVING,
                                                    memory_order_acq_rel, memory_order_acquire)) {
            uint64_t off = atomic_load_explicit(&s->offset, memory_order_acquire);
            if (off == 0) { /* freed while we waited: nothing to move */
                atomic_store_explicit(&s->claim, 0, memory_order_release);
                return NULL;
            }
            a->claim_spins += spins;
            if (spins > a->claim_spins_max)
                a->claim_spins_max = spins;
            return a->base + off;
        }
        spins++;
    }
}

/* The checksum over the payload of a cell: what the pass proves it copied whole. */
static uint64_t payload_checksum(const uint8_t *payload, uint64_t size) {
    uint64_t sum = 0x9E3779B97F4A7C15ULL;
    for (uint64_t i = 0; i < size; i++)
        sum = (sum ^ payload[i]) * 0x100000001B3ULL;
    return sum;
}

void rxf_smp_compact_move(rxf_smp_arena *a, rxf_smp_compact_stats *st) {
    /* The caller owns the stats and sets ctx and observe before the call; the numeric fields
     * hold what this pass did. */
    st->moved_cells = st->moved_bytes = st->pinned_cells = 0;
    st->checksum_fails = st->dead_cells = 0;
    st->moved_by_type[0] = st->moved_by_type[1] = st->moved_by_type[2] = st->moved_by_type[3] = 0;
    st->moved_by_type[4] = st->moved_by_type[5] = st->moved_by_type[6] = st->moved_by_type[7] = 0;
    st->parked_moved = st->midflight_moved = 0;
    st->claim_spins = st->claim_spins_max = 0;
    uint64_t spins_before = a->claim_spins;
    a->mv_count = 0;
    a->old_frontier = a->frontier;
    uint32_t n = a->next_index > 1 ? a->next_index - 1 : 0;
    if (n == 0) {
        st->claim_spins = 0;
        st->claim_spins_max = a->claim_spins_max;
        return;
    }
    /* Walk the cells in offset order: sliding compaction is a walk of the stream (docs/03), and
     * index order is not offset order. */
    uint32_t *order = (uint32_t *)calloc(n, sizeof(uint32_t));
    if (!order)
        return;
    for (uint32_t i = 0; i < n; i++)
        order[i] = i + 1;
    for (uint32_t i = 1; i < n; i++) { /* insertion sort: n is small in the proof */
        uint64_t key_off =
            atomic_load_explicit(&a->slots[order[i]].offset, memory_order_relaxed);
        uint32_t key = order[i];
        int64_t j = (int64_t)i - 1;
        while (j >= 0 && atomic_load_explicit(&a->slots[order[j]].offset, memory_order_relaxed) >
                            key_off) {
            order[j + 1] = order[j];
            j--;
        }
        order[j + 1] = key;
    }
    uint64_t front = 64;
    for (uint32_t i = 0; i < n; i++) {
        rxf_smp_slot *s = &a->slots[order[i]];
        uint8_t *c = claim_cell(a, s);
        if (!c)
            continue;
        uint32_t type = cell_type(c);
        uint64_t size = cell_bytes(cell_payload_size(c));
        if (type == RXF_SMP_DEAD_TYPE) {
            /* A freed cell (kind 254 of docs/17): it vanishes and its slot stays free. */
            st->dead_cells++;
            atomic_store_explicit(&s->offset, 0, memory_order_release);
            write_u64(c + RXF_SMP_C_TYPE, 0);
            atomic_store_explicit(&s->claim, 0, memory_order_release);
            continue;
        }
        uint64_t old = (uint64_t)(c - a->base);
        uint64_t new_off = front;
        if (cell_flags(c) & RXF_SMP_FLAG_PINNED) {
            /* A pin holds its running source address (docs/03), so the cell stays where it is and
             * the space before it is a gap this pass cannot cross. */
            st->pinned_cells++;
            new_off = old;
            front = old + size;
            atomic_store_explicit(&s->claim, 0, memory_order_release);
            continue;
        }
        if (old != front) {
            /* Slide the cell down. The claim we hold is what makes this copy safe against readers
             * of the old place: a reader is refused while it is held, and when it is released the
             * new offset is already published (below). The old header keeps the forwarding offset
             * until the pass finishes (docs/03). The forwarding word is written BEFORE the copy
             * and travels with it into the new header: written after, it would land inside the
             * new cell's payload whenever the slide overlaps the old place (a gap smaller than
             * the cell, which the churn of the delta produces), tearing the payload the pass
             * just proved whole. At the old place the record survives exactly as long as no
             * later slide covers it; the reader of the record (the mutator's check in
             * smp_witness.c) counts only what the geometry leaves. */
            write_u64(c + RXF_SMP_C_FORWARD, front);
            uint64_t want = payload_checksum(c + RXF_SMP_CELL_BYTES, cell_payload_size(c));
            memmove(a->base + front, c, (size_t)size);
            uint8_t *nw = a->base + front;
            if (payload_checksum(nw + RXF_SMP_CELL_BYTES, cell_payload_size(nw)) != want)
                st->checksum_fails++; /* a copy that broke: never observed */
            st->moved_cells++;
            st->moved_bytes += size;
            st->moved_by_type[type < 8 ? type : 0]++;
            if (st->observe) {
                /* The harness looks at every moved cell: 1 when it belongs to a run that is
                 * parked at this instant (docs/20, S7), 2 when the run is between safe points
                 * (mid-flight, S8). */
                int seen = st->observe(st->ctx, nw + RXF_SMP_CELL_BYTES, type,
                                       RXF_SMP_HANDLE(*(uint32_t *)(nw + RXF_SMP_C_INDEX),
                                                      cell_generation(nw)));
                if (seen & 1)
                    st->parked_moved++;
                if (seen & 2)
                    st->midflight_moved++;
            }
            a->mv_src[a->mv_count] = old;
            a->mv_dst[a->mv_count] = front;
            a->mv_size[a->mv_count] = size;
            a->mv_count++;
        }
        front = new_off + size;
        atomic_store_explicit(&s->offset, new_off, memory_order_release);
        atomic_store_explicit(&s->claim, 0, memory_order_release);
    }
    a->frontier = front;
    st->claim_spins = a->claim_spins - spins_before;
    st->claim_spins_max = a->claim_spins_max;
    free(order);
}

void rxf_smp_compact_finish(rxf_smp_arena *a) {
    /* Every handle now names the new place. The forwarding offset did its job for the readers that
     * arrived during the pass; the bytes the pass vacated (the tail after the new frontier) are
     * poisoned, so a stale raw address that still points there reads poison and is caught, and a
     * stale handle resolves to nothing. The old places of cells that slid under later cells cannot
     * be poisoned: those bytes are live cells again; nothing poisons a live cell. */
    if (a->old_frontier > a->frontier) {
        memset(a->base + a->frontier, 0xA5, (size_t)(a->old_frontier - a->frontier));
#ifdef RXF_SMP_ASAN
        __asan_poison_memory_region(a->base + a->frontier, (size_t)(a->old_frontier - a->frontier));
#endif
    }
    a->mv_count = 0;
}
