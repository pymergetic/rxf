/*
 * The dispatcher of the SMP proof (docs/20): the run-cell state machine, the ready-ring cell, and
 * the park/wake protocol. One loop, the same for every carrier; see smp_sched.h for the rules.
 */
#include "smp_sched.h"

#include <stddef.h>
#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- the checksum over the program state of a run cell (docs/20, S8) ---- */

static uint64_t fnv(const void *p, size_t n, uint64_t h) {
    const uint8_t *b = (const uint8_t *)p;
    for (size_t i = 0; i < n; i++)
        h = (h ^ b[i]) * 0x100000001B3ULL;
    return h;
}

uint64_t smp_run_checksum(const smp_run_payload *r) {
    /* Only the program state of the run is covered (S8): the handoff metadata and evidence
     * (run_index .. migrate_mask) is written under the state machine and the ring lock and is not
     * part of what a step must see whole. */
    return fnv(&r->self, offsetof(smp_run_payload, checksum) - offsetof(smp_run_payload, self),
               0xcbf29ce484222325ULL);
}

/* ---- the ready-ring cell ---- */

static smp_ring_payload *ring_lock(smp_world *w) {
    for (;;) {
        void *p = rxf_smp_arena_resolve(w->arena, w->ring);
        if (!p) {
            if (rxf_smp_arena_is_stale(w->arena, w->ring))
                return NULL; /* the ring cell itself is gone: the world is over */
            continue;        /* the compactor holds the ring for one copy: wait out the claim */
        }
        smp_ring_payload *ring = (smp_ring_payload *)p;
        uint32_t want = 0;
        if (atomic_compare_exchange_strong_explicit(&ring->lock, &want, 1, memory_order_acquire,
                                                    memory_order_relaxed))
            return ring;
        rxf_smp_arena_release(w->arena, w->ring);
    }
}

static void ring_unlock(smp_world *w, smp_ring_payload *ring) {
    atomic_store_explicit(&ring->lock, 0, memory_order_release);
    rxf_smp_arena_release(w->arena, w->ring);
}

int smp_ring_push(smp_world *w, rxf_smp_handle run) {
    smp_ring_payload *ring = ring_lock(w);
    if (!ring)
        return 1;
    uint32_t next = ring->head + 1;
    if (next >= ring->capacity)
        next = 0;
    int answer;
    if (next == ring->tail) {
        ring->refused_full++;
        answer = 1;
    } else {
        ring->slots[ring->head] = run;
        ring->head = next;
        ring->pushed++;
        answer = 0;
    }
    ring_unlock(w, ring);
    return answer;
}

int smp_ring_pop(smp_world *w, rxf_smp_handle *out) {
    smp_ring_payload *ring = ring_lock(w);
    if (!ring)
        return 1;
    int answer = 1;
    while (ring->tail != ring->head) {
        rxf_smp_handle h = ring->slots[ring->tail];
        ring->slots[ring->tail] = RXF_SMP_HANDLE_NONE;
        ring->tail = ring->tail + 1 >= ring->capacity ? 0 : ring->tail + 1;
        if (rxf_smp_arena_is_stale(w->arena, h)) {
            /* The ABA refusal (docs/20, S3): the run of this slot is gone; the handle names
             * nothing and never the new cell of the slot. The ring goes on. */
            ring->refused_stale++;
            atomic_fetch_add_explicit(&w->ring_stale_refused, 1, memory_order_relaxed);
            continue;
        }
        ring->popped++;
        *out = h;
        answer = 0;
        break;
    }
    ring_unlock(w, ring);
    return answer;
}

int smp_ring_peek(smp_world *w) {
    smp_ring_payload *ring = ring_lock(w);
    if (!ring)
        return 0;
    int full = ring->tail != ring->head;
    ring_unlock(w, ring);
    return full;
}

/* ---- world ---- */

void smp_world_init(smp_world *w, rxf_smp_arena *arena, uint32_t n_runs, smp_step_fn step,
                    void *step_ctx) {
    memset(w, 0, sizeof(*w));
    w->arena = arena;
    w->n_runs = n_runs;
    w->step = step;
    w->step_ctx = step_ctx;
    /* The ready-ring cell holds head, tail, capacity and the counters before the slots. Room is
     * left for the harness to push stale handles on purpose (docs/20, S3: the ABA refusal). */
    uint64_t ring_payload = offsetof(smp_ring_payload, slots) +
                            (uint64_t)(n_runs + 16) * sizeof(rxf_smp_handle);
    w->ring = rxf_smp_arena_alloc(arena, RXF_SMP_CELL_RING, ring_payload, 0);
    w->channel = rxf_smp_arena_alloc(arena, RXF_SMP_CELL_CHANNEL, 256, 0);
    w->runs = (rxf_smp_handle *)calloc(n_runs, sizeof(rxf_smp_handle));
    /* The capacity is a word of the cell like every other (docs/20, S3). */
    smp_ring_payload *ring = (smp_ring_payload *)rxf_smp_arena_resolve(arena, w->ring);
    if (ring) {
        ring->capacity = n_runs + 16;
        rxf_smp_arena_release(arena, w->ring);
    }
}

void smp_world_deinit(smp_world *w) {
    free(w->runs);
    w->runs = NULL;
}

/* ---- the wake (docs/20, S4) ---- */

int smp_wake(smp_world *w, rxf_smp_handle target, uint32_t waker) {
    smp_run_payload *run = (smp_run_payload *)rxf_smp_arena_resolve(w->arena, target);
    if (!run)
        return 1;
    /* The token first: a wake that lands while the target still runs is not lost, the target
     * consumes it at its next park and does not sleep (S4). The waker is recorded either way. */
    atomic_store_explicit(&run->token, 1, memory_order_release);
    atomic_store_explicit(&run->waker_run, waker, memory_order_relaxed);
    uint32_t want = RXF_SMP_STATE_PARKED;
    if (!atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_READY,
                                                 memory_order_acq_rel, memory_order_acquire)) {
        rxf_smp_arena_release(w->arena, target);
        return 1; /* not parked: the token carries it to the target's next park */
    }
    /* We won PARKED to READY, so the enqueue is ours (S4). */
    rxf_smp_arena_release(w->arena, target);
    for (;;) {
        if (smp_ring_push(w, target) == 0) {
            if (w->notify)
                w->notify(w->edge_ctx);
            return 0;
        }
        if (atomic_load_explicit(&w->stop, memory_order_relaxed)) {
            /* The wake is taken back (READY to PARKED, the legal retract of S1): the run stays
             * parked, and the ring never saw it. */
            run = (smp_run_payload *)rxf_smp_arena_resolve(w->arena, target);
            if (run) {
                want = RXF_SMP_STATE_READY;
                atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_PARKED,
                                                        memory_order_acq_rel,
                                                        memory_order_acquire);
                rxf_smp_arena_release(w->arena, target);
            }
            return 2;
        }
    }
}

/* ---- the park (docs/20, S4) ---- */

/* The seal of a step (docs/20, S5): close the run cell before it can change hands — the step's
 * own closing duty (the guard words of the proof's run cells), the step count, and the checksum
 * over the result. It runs while the run is still owned and claimed, and every transition that
 * can hand the run to another carrier follows it. This is the shape of the handoff the policy
 * requires ("the checksum rewritten before the requeue", docs/20 S5): the requeue of a parked run
 * is committed by its waker the moment the run is PARKED, so the seal must precede the park
 * transition just as it precedes the dispatcher's own requeue. */
static void run_seal(smp_world *w, smp_run_payload *run, uint32_t carrier) {
    if (w->step_end)
        w->step_end(w->step_ctx, w, run, carrier);
    /* A step is one resume of the run: resumes counts the resumes, steps the steps. */
    run->steps++;
    run->checksum = smp_run_checksum(run);
}

int smp_park(smp_world *w, smp_run_payload *run, uint32_t carrier) {
    run->park_carrier = carrier + 1;
    run->deferred = 0;
    /* Consume a wake that landed while we ran: then we never sleep (S4, no lost wake), the run is
     * still RUNNING and still ours, and the dispatcher seals it and takes the RUNNING to READY
     * transition to requeue us. */
    if (atomic_exchange_explicit(&run->token, 0, memory_order_acq_rel) == 1)
        return SMP_STEP_AGAIN;
    /* The park is the step's handoff, so it seals first (S5): the RUNNING to PARKED CAS below is
     * a release a waker runs against the moment it lands — a waker that wins PARKED to READY
     * enqueues this run and another carrier claims it at once — so every byte of the step must be
     * final before it. The park's answer (PARK or SELFWAKE) tells the dispatcher the cell is
     * already sealed: it must not write the run cell again. */
    run_seal(w, run, carrier);
    uint32_t want = RXF_SMP_STATE_RUNNING;
    if (!atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_PARKED,
                                                 memory_order_acq_rel, memory_order_acquire))
        return SMP_STEP_PARK; /* the owner is the only one that can fail this CAS */
    if (atomic_exchange_explicit(&run->token, 0, memory_order_acq_rel) == 1) {
        /* A wake arrived while we became PARKED. Whoever wins PARKED to READY owns the enqueue:
         * if we win it, the dispatcher requeues us (the cell is sealed — above — so the next
         * carrier never sees a half-written run cell); if the waker won, the run is already on
         * its way into the ring. */
        want = RXF_SMP_STATE_PARKED;
        if (atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_READY,
                                                    memory_order_acq_rel, memory_order_acquire))
            return SMP_STEP_SELFWAKE;
    }
    return SMP_STEP_PARK;
}

/* ---- the dispatcher ---- */

static smp_run_payload *resolve_spin(smp_world *w, rxf_smp_handle h) {
    for (;;) {
        void *p = rxf_smp_arena_resolve(w->arena, h);
        if (p)
            return (smp_run_payload *)p;
        if (rxf_smp_arena_is_stale(w->arena, h))
            return NULL;
    }
}

int smp_dispatch_one(smp_world *w, uint32_t carrier) {
    for (;;) {
        if (atomic_load_explicit(&w->stop, memory_order_relaxed))
            return 2;
        rxf_smp_handle h;
        if (smp_ring_pop(w, &h) != 0) {
            atomic_fetch_add_explicit(&w->ring_empty, 1, memory_order_relaxed);
            return 1;
        }
        atomic_fetch_add_explicit(&w->pops, 1, memory_order_relaxed);
        atomic_fetch_add_explicit(&w->carrier_pops[carrier], 1, memory_order_relaxed);
        smp_run_payload *run = resolve_spin(w, h);
        if (!run) {
            atomic_fetch_add_explicit(&w->handle_stale_refused, 1, memory_order_relaxed);
            continue;
        }
        /* The proof knob (docs/20, S4): the carrier that parked this run steps aside a couple of
         * times so the resume is provably on another carrier: it pushes the run back, rings the
         * edge doorbell, and waits a moment for another carrier to take it. After a few tries it
         * takes the run itself, so progress never depends on the knob. */
        if (w->force_cross_resume && run->park_carrier == carrier + 1 && run->deferred < 2 &&
            run->park_carrier != 0) {
            run->deferred++;
            atomic_fetch_add_explicit(&w->deferred_cross, 1, memory_order_relaxed);
            for (;;)
                if (smp_ring_push(w, h) == 0)
                    break;
            if (w->notify)
                w->notify(w->edge_ctx);
            rxf_smp_arena_release(w->arena, h);
            if (w->backoff)
                w->backoff(w->edge_ctx); /* step aside: another carrier takes the ring */
            continue;
        }
        uint32_t want = RXF_SMP_STATE_READY;
        if (!atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_RUNNING,
                                                     memory_order_acq_rel,
                                                     memory_order_acquire)) {
            /* Enqueued twice or already taken: the ring gave it away, nothing is stepped twice. */
            atomic_fetch_add_explicit(&w->state_refused, 1, memory_order_relaxed);
            rxf_smp_arena_release(w->arena, h);
            continue;
        }
        /* Ownership passed through the ring cell: this carrier is the only one that may touch the
         * program state of the run until the next safe point (docs/20, S5). */
        run->resumes++;
        run->resume_carrier = carrier + 1;
        run->migrate_mask |= 1u << carrier; /* which carriers ever stepped this run (S5) */
        if (run->park_carrier != 0 && run->park_carrier != carrier + 1) {
            /* The run really migrated: parked by one carrier, resumed by another. */
            atomic_fetch_add_explicit(&w->cross_resumes, 1, memory_order_relaxed);
            run->migrate_refs++;
        }
        run->last_carrier = carrier + 1;
        /* The check is exactly here — at the step boundary, with the run owned and claimed (the
         * READY to RUNNING CAS above is that boundary), the one position where the program state
         * of the run must be whole (S8). The previous owner sealed the cell before it let the run
         * change hands (smp_park and the seal below), so a failure here is a real tear and never
         * a comparison against a step epilogue still in flight. Never move this check to any
         * other position: checked while a cell is legitimately mid-update it is a race in the
         * check, not evidence of a torn cell. */
        if (smp_run_checksum(run) != run->checksum) {
            atomic_fetch_add_explicit(&w->checksum_fails, 1, memory_order_relaxed);
            run->checksum = smp_run_checksum(run);
        }
        int result = w->step(w->step_ctx, w, run, carrier);
        /* The seal before any handoff (S5): a step that parked answered with PARK or SELFWAKE and
         * sealed its cell in smp_park, at the moment the run left RUNNING — the park is itself the
         * handoff, since a waker may requeue a PARKED run at once — so the dispatcher seals only
         * the results that are still RUNNING here (AGAIN, DONE) and then makes its transition.
         * Every transition below follows the seal; after it this carrier writes only atomics and
         * the ring, never a program word of the cell. */
        if (result == SMP_STEP_AGAIN || result == SMP_STEP_DONE)
            run_seal(w, run, carrier);
        atomic_fetch_add_explicit(&w->steps_total, 1, memory_order_relaxed);
        atomic_fetch_add_explicit(&w->carrier_steps[carrier], 1, memory_order_relaxed);
        if (result == SMP_STEP_DONE) {
            /* TERMINAL is absorbing: a state CAS that nothing reads back, so a final wake races
             * teardown and is refused by the state machine (the exit burst of S11). The cell is
             * sealed (above) before the transition. */
            want = RXF_SMP_STATE_RUNNING;
            if (atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_TERMINAL,
                                                        memory_order_acq_rel,
                                                        memory_order_acquire))
                atomic_fetch_add_explicit(&w->terminal, 1, memory_order_release);
        } else if (result == SMP_STEP_SELFWAKE) {
            /* The run woke itself at its own park: it is READY and the enqueue is ours. The cell
             * was sealed in smp_park, before the park transition, so the next carrier never sees
             * a half-written run cell (the handoff rule of S5). */
            for (;;)
                if (smp_ring_push(w, h) == 0)
                    break;
            if (w->notify)
                w->notify(w->edge_ctx);
        } else if (result == SMP_STEP_PARK) {
            /* The run is PARKED (sealed in smp_park before the transition), or the waker that
             * raced us is already enqueuing it. Nothing is left to do here — in particular no
             * write to the run cell: its next owner may already be stepping it. */
        } else {
            want = RXF_SMP_STATE_RUNNING;
            if (atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_READY,
                                                        memory_order_acq_rel,
                                                        memory_order_acquire)) {
                for (;;)
                    if (smp_ring_push(w, h) == 0)
                        break;
                if (w->notify)
                    w->notify(w->edge_ctx);
            }
        }
        rxf_smp_arena_release(w->arena, h);
        return 0;
    }
}

int smp_dispatch_loop(smp_world *w, uint32_t carrier) {
    for (;;) {
        int answer = smp_dispatch_one(w, carrier);
        if (answer == 2)
            return 0;
        if (answer == 1 && w->wait)
            w->wait(w->edge_ctx); /* the edge shim decides how to wait; the core never learns */
    }
}
