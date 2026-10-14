/*
 * The SMP proof harness (docs/20, C2): N pthread carriers over the scheduler of smp_sched.c, run
 * and data cells in a real arena, and the evidence the proof reads. This is the edge shim of the
 * policy: the core (smp_sched.c) learns nothing of pthreads except through three callbacks
 * (wait, backoff, notify), and the harness projects the run-cell states onto pthreads the way an
 * asyncio Future or a Metal event ring would project them onto its own edge (docs/20, S9).
 *
 * The programs are deterministic functions of (seed, run index, step index): a step reads and
 * writes its own run cell and its own data cell and never the data of any other run, so the result
 * of a whole program is a function of the seed alone whatever the interleaving (S10). What the
 * interleaving decides is only who executes a step and when; the proof reads that back as evidence
 * (per-carrier counters, park/resume pairs, moves observed under runs) and never mixes it into the
 * result.
 *
 * Everything in the arena moves (the compactor slides cells), so nothing here ever keeps a payload
 * address: only handles, resolved at the start of a step and released at its end (docs/20, S7).
 * The claim of smp_arena.c is what makes that address valid for exactly that window.
 */
#include "smp_witness.h"

#include "smp_arena.h"
#include "smp_sched.h"

#include <pthread.h>
#include <stdatomic.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifdef RXF_SMP_ASAN
#include <sanitizer/asan_interface.h>
#endif

/* The kinds of program a run cell carries (docs/20, S6): the park/wake protocols the proof
 * demonstrates. The programs are deterministic functions of (seed, run index, step index). */
enum {
    WIT_PROGRAM_WORKER = 1,  /* spinning steps, occasional parks: the migration traffic */
    WIT_PROGRAM_CHANNEL = 2, /* two runs and a channel frame: park, send, wake, cross resume */
    WIT_PROGRAM_STALE = 3    /* resolves a handle whose slot generation moved: must be refused */
};

#define WIT_MAX_RUNS 512u
#define WIT_MAX_CARRIERS RXF_SMP_MAX_CARRIERS

/* The data cell of a run: the cell the compactor moves under live runs. Its content is a function
 * of the run alone; `delta_stamp` is the one field the mutator's delta writes and no run reads. */
typedef struct {
    uint64_t run_index, seed, sum, xor_value, delta_stamp, guard[2];
} wit_data_payload;

/* The channel frame of a pair (docs/20, S4): a cell the two runs park on and wake each other
 * through. The head and tail are what makes a word visible across carriers. */
typedef struct {
    _Atomic(uint32_t) head, tail;
    uint32_t pad[2];
    uint64_t words[16];
} wit_channel_payload;

/* The edge shim (S9): a condvar and a predicate. The dispatcher waits when the ring is empty and
 * notifies when it is not; the harness sleeps in pthread_cond_timedwait and nothing in the core
 * knows what a pthread is. */
typedef struct {
    pthread_mutex_t lock;
    pthread_cond_t cond;
    int work; /* the predicate: the ring was not empty at the last notify */
} wit_edge;

void wit_edge_init(wit_edge *e);
void wit_edge_deinit(wit_edge *e);

/* ---- small helpers ---- */

static uint64_t mix64(uint64_t x) {
    x ^= x >> 33;
    x *= 0xFF51AFD7ED558CCDULL;
    x ^= x >> 33;
    x *= 0xC4CEB9FE1A85EC53ULL;
    x ^= x >> 33;
    return x;
}

/* One step's value: a function of the seed, the run and the step index, never of the carrier or
 * of the wall clock (S10: same program, same seed, same result). */
static uint64_t step_value(uint64_t seed, uint64_t run_index, uint64_t step) {
    return mix64(seed * 0x9E3779B97F4A7C15ULL + run_index * 0xC2B2AE3D27D4EB4FULL + step);
}

static uint64_t word_value(uint64_t seed, uint64_t pair, uint64_t index) {
    return mix64(seed * 0xD6E8FEB86659FD93ULL + pair * 0xA5A5A5A5A5A5A5A5ULL + index);
}

static void sleep_ns(long ns) {
    struct timespec ts = {0, ns};
    nanosleep(&ts, NULL);
}

/* ---- the data cell of a run (the cell the compactor moves under live runs) ---- */

static uint64_t data_guard(uint64_t seed, uint64_t run_index, int which) {
    return mix64(seed ^ (run_index * 3 + (uint64_t)which * 7 + 1)) | 1ULL;
}

/* ---- the edge shim (S9): a condvar the core knows nothing about ---- */

void wit_edge_init(wit_edge *e) {
    pthread_mutex_init(&e->lock, NULL);
    pthread_cond_init(&e->cond, NULL);
    e->work = 1;
}

void wit_edge_deinit(wit_edge *e) {
    pthread_mutex_destroy(&e->lock);
    pthread_cond_destroy(&e->cond);
}

static void edge_notify(void *ctx) {
    wit_edge *e = (wit_edge *)ctx;
    pthread_mutex_lock(&e->lock);
    e->work = 1;
    pthread_cond_broadcast(&e->cond);
    pthread_mutex_unlock(&e->lock);
}

static void edge_wait(void *ctx) {
    wit_edge *e = (wit_edge *)ctx;
    pthread_mutex_lock(&e->lock);
    if (e->work) {
        e->work = 0; /* the work is claimed by one carrier; the others re-poll the ring */
        pthread_mutex_unlock(&e->lock);
        return;
    }
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_nsec += 2 * 1000 * 1000; /* 2 ms */
    if (ts.tv_nsec >= 1000 * 1000 * 1000) {
        ts.tv_sec += 1;
        ts.tv_nsec -= 1000 * 1000 * 1000;
    }
    pthread_cond_timedwait(&e->cond, &e->lock, &ts);
    pthread_mutex_unlock(&e->lock);
}

static void edge_backoff(void *ctx) {
    (void)ctx;
    sleep_ns(100 * 1000); /* the proof knob: pause long enough for another carrier to take the
                           * ring — a yield of the carrier, never a sleep of the run */
}

/* ---- the world the programs run in ---- */

typedef struct {
    smp_world w;
    rxf_smp_arena arena;
    wit_edge edge;
    /* Handles only: every payload in the arena moves (S7), so no pointer outlives a step. */
    rxf_smp_handle run_handles[WIT_MAX_RUNS];
    rxf_smp_handle data_handles[WIT_MAX_RUNS];
    rxf_smp_handle pair_channels[8];
    /* The churn cells: scratch cells laid between the run and data cells of the setup, freed by
     * the delta so the compactor has real gaps to close in the middle of the stream. */
    rxf_smp_handle churn[WIT_MAX_RUNS / 2];
    uint32_t churn_count, churn_used;
    uint64_t steps_per_run;
    uint32_t messages, seed, flags, n_runs, n_carriers;
    /* what the compactor did, over all passes (S8): the numbers the proof reads */
    rxf_smp_compact_stats acc;
    uint64_t passes;
    /* the evidence */
    _Atomic(uint64_t) parks, wakes, selfwakes, watchdog_wakes, relocated_steps;
    _Atomic(uint64_t) deltas, delta_cells, delta_freed;
    _Atomic(uint64_t) parked_moved, midflight_moved, moved_run_cells, moved_data_cells, moved_ring;
    _Atomic(uint64_t) forward_ok, forward_lost, verify_bad;
    _Atomic(uint32_t) pair_park, pair_resume, pair_waker, pair_recorded;
    uint64_t ring_offset_before, ring_offset_after;
} wit_world;

/* ---- the programs (docs/20, S6) ----

 * Every step resolves the cells it needs by handle and releases them before it answers: the end of
 * a step is a safe point, and nothing is held across it. The data cell is checked against what the
 * run itself wrote at its last step and against the guards of the setup, so a torn copy, a stale
 * read or a wrong rebind is caught right where it would happen. */

static int step_run(void *ctx, smp_world *w, smp_run_payload *run, uint32_t carrier) {
    wit_world *ww = (wit_world *)ctx;

    if (run->kind == WIT_PROGRAM_STALE) {
        /* The ABA program (S3, S7): one handle names a freed and re-used slot. It must be refused
         * (its generation moved) and must never resolve to the new cell of that slot; the fresh
         * handle of the same slot must resolve. */
        void *p = rxf_smp_arena_resolve(&ww->arena, run->stale);
        if (p) {
            run->ok = 0; /* a stale handle that resolved: the proof fails */
            rxf_smp_arena_release(&ww->arena, run->stale);
        } else if (rxf_smp_arena_is_stale(&ww->arena, run->stale)) {
            run->stale_refused++;
            atomic_fetch_add_explicit(&ww->w.handle_stale_refused, 1, memory_order_relaxed);
            void *q = rxf_smp_arena_resolve(&ww->arena, run->scratch);
            if (q) {
                rxf_smp_arena_release(&ww->arena, run->scratch);
                run->ok = 1; /* refused on the old generation, resolved on the new one */
            }
        }
        return SMP_STEP_DONE;
    }

    /* The data cell: resolved by handle every step (S7). */
    wit_data_payload *data = (wit_data_payload *)rxf_smp_arena_resolve(&ww->arena, run->data);
    if (!data) {
        if (rxf_smp_arena_is_stale(&ww->arena, run->data))
            run->stale_refused++; /* a stale data handle would be a proof failure; counted */
        else
            run->ring_refused++; /* a transient claim collision: counted, retried next step */
        return SMP_STEP_AGAIN;
    }
    uint64_t offset = rxf_smp_arena_offset(&ww->arena, run->data);
    if (run->data_offset != 0 && offset != run->data_offset) {
        /* The cell moved under us (between two safe points) and the handle found it: the whole
         * rule S8 in one line. */
        run->relocated++;
        atomic_fetch_add_explicit(&ww->relocated_steps, 1, memory_order_relaxed);
    }
    run->data_offset = offset;

    /* The coherence check: what this run wrote at its last step must still be there, and the
     * guards of the setup must be intact. A stale raw address or a torn copy fails here. */
    if (data->run_index != run->run_index || data->seed != run->seed ||
        data->sum != run->acc_sum || data->xor_value != run->acc_xor ||
        data->guard[0] != data_guard(run->seed, run->run_index, 0) ||
        data->guard[1] != data_guard(run->seed, run->run_index, 1)) {
        run->verify_bad++;
        atomic_fetch_add_explicit(&ww->verify_bad, 1, memory_order_relaxed);
    }

    if (run->kind == WIT_PROGRAM_WORKER) {
        /* The worker: every dispatched step is progress, and the mix decides when it parks (the
         * mix is a function of the step index alone). */
        uint64_t value = step_value(run->seed, run->run_index, run->pc);
        run->acc_sum += value;
        run->acc_xor ^= value;
        run->pc++;
        data->sum = run->acc_sum;
        data->xor_value = run->acc_xor;
        if ((value & 63u) == 0 && run->pc < run->target_steps) {
            rxf_smp_arena_release(&ww->arena, run->data);
            atomic_fetch_add_explicit(&ww->parks, 1, memory_order_relaxed);
            int answer = smp_park(w, run, carrier);
            if (answer != SMP_STEP_PARK) /* a wake was already waiting: it never slept */
                atomic_fetch_add_explicit(&ww->selfwakes, 1, memory_order_relaxed);
            return answer;
        }
        rxf_smp_arena_release(&ww->arena, run->data);
        return run->pc >= run->target_steps ? SMP_STEP_DONE : SMP_STEP_AGAIN;
    }

    /* A pair of runs over one channel frame (S4): the sender fills a word and wakes the
     * receiver (a run wakes a run); the receiver parks on the channel frame until a word is
     * there. The wake token of smp_park guarantees no wake is lost. The result state advances
     * only on progress (S10): a step that finds nothing to do — an empty channel, a full frame,
     * a refused resolve — consumes an attempt and no result. Attempts are timing, and timing is
     * evidence, never the answer. */
    int result = SMP_STEP_AGAIN;
    uint64_t value;
    wit_channel_payload *ch =
        (wit_channel_payload *)rxf_smp_arena_resolve(&ww->arena, run->channel);
    if (!ch) {
        rxf_smp_arena_release(&ww->arena, run->data);
        return SMP_STEP_AGAIN;
    }
    if (run->run_index % 2 == 0) { /* the sender */
        uint32_t pair = run->run_index / 2;
        if (run->sent < ww->messages) {
            uint32_t head = atomic_load_explicit(&ch->head, memory_order_relaxed);
            uint32_t tail = atomic_load_explicit(&ch->tail, memory_order_acquire);
            if (head - tail < 16) {
                ch->words[head & 15] = word_value(ww->seed, pair, head);
                atomic_store_explicit(&ch->head, head + 1, memory_order_release);
                value = step_value(run->seed, run->run_index, run->sent);
                run->acc_sum += value;
                run->acc_xor ^= value;
                run->sent++;
                data->sum = run->acc_sum;
                data->xor_value = run->acc_xor;
                /* The wake (S4): the token reaches the run even if it still runs; PARKED to
                 * READY and into the ring when it already slept. */
                if (smp_wake(w, w->runs[run->run_index + 1], run->run_index + 1) != 2)
                    atomic_fetch_add_explicit(&ww->wakes, 1, memory_order_relaxed);
            }
        }
        result = run->sent >= ww->messages ? SMP_STEP_DONE : SMP_STEP_AGAIN;
    } else { /* the receiver */
        uint32_t tail = atomic_load_explicit(&ch->tail, memory_order_relaxed);
        uint32_t head = atomic_load_explicit(&ch->head, memory_order_acquire);
        if (tail < head) {
            uint64_t word = ch->words[tail & 15];
            value = step_value(run->seed, run->run_index, run->rx_seen);
            run->acc_sum += value + word;
            run->acc_xor ^= value;
            atomic_store_explicit(&ch->tail, tail + 1, memory_order_release);
            run->rx_seen++;
            data->sum = run->acc_sum;
            data->xor_value = run->acc_xor;
            result = run->rx_seen >= ww->messages ? SMP_STEP_DONE : SMP_STEP_AGAIN;
        } else {
            /* The park (S4): the run cell enters PARKED and nothing of it names a thread. */
            rxf_smp_arena_release(&ww->arena, run->channel);
            rxf_smp_arena_release(&ww->arena, run->data);
            atomic_fetch_add_explicit(&ww->parks, 1, memory_order_relaxed);
            int answer = smp_park(w, run, carrier);
            if (answer != SMP_STEP_PARK) /* a wake was already waiting: it never slept */
                atomic_fetch_add_explicit(&ww->selfwakes, 1, memory_order_relaxed);
            return answer;
        }
    }
    rxf_smp_arena_release(&ww->arena, run->channel);
    rxf_smp_arena_release(&ww->arena, run->data);

    /* The pair evidence (S4): the receiver of pair 0 records the first handoff it sees, park on
     * one carrier and resume on another. */
    if (run->kind == WIT_PROGRAM_CHANNEL && run->run_index % 2 == 1 &&
        run->park_carrier != 0 && run->resume_carrier != 0 &&
        run->resume_carrier != run->park_carrier) {
        uint32_t want = 0;
        if (atomic_compare_exchange_strong_explicit(&ww->pair_recorded, &want, 1,
                                                    memory_order_acq_rel, memory_order_acquire)) {
            atomic_store_explicit(&ww->pair_park, run->park_carrier, memory_order_relaxed);
            atomic_store_explicit(&ww->pair_resume, run->resume_carrier, memory_order_relaxed);
            atomic_store_explicit(&ww->pair_waker,
                                  atomic_load_explicit(&run->waker_run, memory_order_relaxed),
                                  memory_order_relaxed);
        }
    }
    return result;
}

/* ---- the observer of the compactor (S7, S8): what the proof counts ---- */

static int compact_observe(void *ctx, void *payload, uint32_t type, rxf_smp_handle h) {
    wit_world *ww = (wit_world *)ctx;
    (void)h;
    int seen = 0;
    if (type == RXF_SMP_CELL_RUN) {
        smp_run_payload *run = (smp_run_payload *)payload;
        uint32_t state = atomic_load_explicit(&run->state, memory_order_acquire);
        if (state == RXF_SMP_STATE_PARKED) {
            seen |= 1;
            atomic_fetch_add_explicit(&ww->parked_moved, 1, memory_order_relaxed);
        } else if (state == RXF_SMP_STATE_READY || state == RXF_SMP_STATE_RUNNING) {
            /* The run is between safe points and its cell moved: legal (S7), and the next step
             * resolves the handle again and finds the new place (relocated_steps). A RUNNING run
             * is never seen here — it holds its claim and the mover waits (the discipline of S5,
             * proven by construction) — so the counter shows the moves under live runs in READY,
             * owning nothing. */
            seen |= 2;
            atomic_fetch_add_explicit(&ww->midflight_moved, 1, memory_order_relaxed);
        }
        atomic_fetch_add_explicit(&ww->moved_run_cells, 1, memory_order_relaxed);
    } else if (type == RXF_SMP_CELL_DATA) {
        atomic_fetch_add_explicit(&ww->moved_data_cells, 1, memory_order_relaxed);
    } else if (type == RXF_SMP_CELL_RING) {
        atomic_fetch_add_explicit(&ww->moved_ring, 1, memory_order_relaxed);
    }
    return seen;
}

/* ---- the carriers ---- */

typedef struct {
    wit_world *ww;
    uint32_t index;
} carrier_arg;

static void *carrier_main(void *p) {
    carrier_arg *arg = (carrier_arg *)p;
    smp_dispatch_loop(&arg->ww->w, arg->index);
    return NULL;
}

/* ---- the mutator: the concurrent delta and compaction (S8) ---- */

static void *mutator_main(void *p) {
    wit_world *ww = (wit_world *)p;
    for (uint32_t round = 0; round < 32 && !atomic_load_explicit(&ww->w.stop, memory_order_relaxed);
         round++) {
        sleep_ns(200 * 1000); /* let the carriers run for a while first */
        if (ww->flags & WIT_FLAG_DELTA) {
            /* A delta: write to data cells through handles (like the image applies deltas under a
             * journal, docs/19: one writer), grow the arena with a scratch cell, and free it
             * again. The runs see whole payloads because every write goes through the claim of
             * the cell, and the one field the delta writes is one no run reads. */
            for (uint32_t i = 0; i < ww->n_runs; i += 7) {
                wit_data_payload *d =
                    (wit_data_payload *)rxf_smp_arena_resolve(&ww->arena, ww->data_handles[i]);
                if (d) {
                    d->delta_stamp = round + 1;
                    rxf_smp_arena_release(&ww->arena, ww->data_handles[i]);
                    atomic_fetch_add_explicit(&ww->delta_cells, 1, memory_order_relaxed);
                }
            }
            for (int k = 0; k < 2 && ww->churn_used < ww->churn_count; k++) {
                /* The freed churn cells sit in the middle of the stream: the pass below closes
                 * the gaps by sliding every later live cell down (docs/20, S8). */
                rxf_smp_arena_free(&ww->arena, ww->churn[ww->churn_used++]);
                atomic_fetch_add_explicit(&ww->delta_freed, 1, memory_order_relaxed);
            }
            rxf_smp_handle scratch =
                rxf_smp_arena_alloc(&ww->arena, RXF_SMP_CELL_SCRATCH, 32, 0);
            if (scratch != RXF_SMP_HANDLE_NONE) {
                atomic_fetch_add_explicit(&ww->delta_cells, 1, memory_order_relaxed);
                rxf_smp_arena_free(&ww->arena, scratch);
                atomic_fetch_add_explicit(&ww->delta_freed, 1, memory_order_relaxed);
            }
            atomic_fetch_add_explicit(&ww->deltas, 1, memory_order_relaxed);
        }
        /* The compaction (S8): move every unpinned cell down while the carriers run, in two
         * halves so the proof can look between them. */
        rxf_smp_compact_stats st;
        memset(&st, 0, sizeof(st));
        st.observe = compact_observe;
        st.ctx = ww;
        rxf_smp_compact_move(&ww->arena, &st);
        uint64_t forward_ok = 0, forward_lost = 0, forward_bad = 0;
        for (uint32_t i = 0; i < ww->arena.mv_count; i++) {
            /* The C_FORWARD of a moved cell is written before the copy (smp_arena.c) and is read
             * back at the old place before finish: this is the relocation tracking of docs/03.
             * The record survives only while the geometry says so: when the slide overlapped the
             * old place, or a later slide of this pass covered it, the old header is another
             * cell's bytes now and there is nothing to read. Lost and read account for every
             * moved cell exactly (S8). */
            uint64_t at = ww->arena.mv_src[i] + RXF_SMP_C_FORWARD;
            int covered = 0;
            for (uint32_t j = 0; j < ww->arena.mv_count; j++)
                if (ww->arena.mv_dst[j] <= at && at < ww->arena.mv_dst[j] + ww->arena.mv_size[j]) {
                    covered = 1;
                    break;
                }
            if (covered) {
                forward_lost++;
                continue;
            }
            uint8_t *old = ww->arena.base + ww->arena.mv_src[i];
            uint64_t fwd = 0;
            memcpy(&fwd, old + RXF_SMP_C_FORWARD, 8);
            if (fwd == ww->arena.mv_dst[i])
                forward_ok++;
            else
                forward_bad++; /* a record the geometry left in place and the value is wrong */
        }
        atomic_fetch_add_explicit(&ww->forward_ok, forward_ok, memory_order_relaxed);
        atomic_fetch_add_explicit(&ww->forward_lost, forward_lost, memory_order_relaxed);
        if (forward_bad)
            atomic_fetch_add_explicit(&ww->verify_bad, forward_bad, memory_order_relaxed);
        rxf_smp_compact_finish(&ww->arena);
        /* What this pass did accumulates for the proof (the mutator owns the struct; the main
         * thread reads it after the join). */
        ww->acc.moved_cells += st.moved_cells;
        ww->acc.moved_bytes += st.moved_bytes;
        ww->acc.pinned_cells += st.pinned_cells;
        ww->acc.checksum_fails += st.checksum_fails;
        ww->acc.dead_cells += st.dead_cells;
        ww->acc.claim_spins += st.claim_spins;
        if (st.claim_spins_max > ww->acc.claim_spins_max)
            ww->acc.claim_spins_max = st.claim_spins_max;
        ww->passes++;
    }
    return NULL;
}

/* ---- the watchdog: the edge shim at its most honest (S9) ----

 * Workers park at random steps and have no partner to wake them: the edge shim (a host timer)
 * wakes them, as a real provider would. The channel pairs are woken by their partner runs only,
 * so the run-to-run wake of S4 is never polluted by the shim. */
static void *watchdog_main(void *p) {
    wit_world *ww = (wit_world *)p;
    while (!atomic_load_explicit(&ww->w.stop, memory_order_relaxed)) {
        sleep_ns(1000 * 1000);
        for (uint32_t i = 0; i < ww->n_runs; i++) {
            smp_run_payload *run =
                (smp_run_payload *)rxf_smp_arena_resolve(&ww->arena, ww->run_handles[i]);
            if (!run)
                continue;
            int parked = atomic_load_explicit(&run->state, memory_order_acquire) ==
                         RXF_SMP_STATE_PARKED;
            uint32_t kind = run->kind;
            rxf_smp_handle self = run->self;
            rxf_smp_arena_release(&ww->arena, ww->run_handles[i]);
            if (parked && kind == WIT_PROGRAM_WORKER) {
                if (smp_wake(&ww->w, self, 0xFFFF) == 0)
                    atomic_fetch_add_explicit(&ww->watchdog_wakes, 1, memory_order_relaxed);
            }
        }
    }
    return NULL;
}

/* ---- one whole witness run ---- */

void wit_go(uint32_t n_runs, uint32_t n_carriers, uint32_t seed, uint64_t steps_per_run,
            uint32_t messages, uint32_t flags, uint64_t *out) {
    if (n_runs < 2)
        n_runs = 2;
    if (n_runs > WIT_MAX_RUNS)
        n_runs = WIT_MAX_RUNS;
    if (n_carriers < 1)
        n_carriers = 1;
    if (n_carriers > WIT_MAX_CARRIERS)
        n_carriers = WIT_MAX_CARRIERS;
    if (messages > 16)
        messages = 16;

    wit_world ww;
    memset(&ww, 0, sizeof(ww));
    memset(out, 0, WIT_OUT_WORDS * sizeof(uint64_t));
    ww.steps_per_run = steps_per_run;
    ww.messages = messages;
    ww.seed = seed;
    ww.flags = flags;
    ww.n_runs = n_runs;
    ww.n_carriers = n_carriers;

    rxf_smp_arena_create(&ww.arena, 4u * 1024 * 1024, n_runs * 4 + 64);
    smp_world_init(&ww.w, &ww.arena, n_runs, step_run, &ww);
    ww.w.wait = edge_wait;
    ww.w.backoff = edge_backoff;
    ww.w.notify = edge_notify;
    ww.w.edge_ctx = &ww.edge;
    ww.w.force_cross_resume = (flags & WIT_FLAG_FORCE_CROSS) ? 1 : 0;
    wit_edge_init(&ww.edge);

    /* The programs: pairs of channel runs first, workers after, one stale-handle run last. */
    uint32_t stale_index = n_runs >= 3 ? n_runs - 1 : 0xFFFFFFFFu;
    uint32_t pairs = (n_runs >= 3 ? (n_runs - 1) / 2 : n_runs / 2);
    if (pairs > 8)
        pairs = 8;
    for (uint32_t p = 0; p < pairs; p++) {
        rxf_smp_handle ch = rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_CHANNEL,
                                                sizeof(wit_channel_payload), 0);
        wit_channel_payload *payload =
            (wit_channel_payload *)rxf_smp_arena_resolve(&ww.arena, ch);
        memset(payload, 0, sizeof(*payload));
        rxf_smp_arena_release(&ww.arena, ch);
        ww.pair_channels[p] = ch;
    }
    for (uint32_t i = 0; i < n_runs; i++) {
        uint32_t kind = WIT_PROGRAM_WORKER;
        if (i == stale_index)
            kind = WIT_PROGRAM_STALE;
        else if (i / 2 < pairs)
            kind = WIT_PROGRAM_CHANNEL;
        rxf_smp_handle rh =
            rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_RUN, sizeof(smp_run_payload), 0);
        smp_run_payload *run = (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, rh);
        memset(run, 0, sizeof(*run));
        run->self = rh;
        run->run_index = i;
        run->kind = kind;
        run->seed = seed;
        run->target_steps = steps_per_run;
        run->guard1[0] = 0x55667788u;
        run->guard1[1] = (uint32_t)mix64(seed ^ i);
        run->guard2[0] = ~run->guard1[0];
        run->guard2[1] = (uint32_t)mix64((seed ^ i) + 7);
        if (kind == WIT_PROGRAM_CHANNEL)
            run->channel = ww.pair_channels[i / 2];
        run->checksum = smp_run_checksum(run);
        rxf_smp_arena_release(&ww.arena, rh);
        ww.run_handles[i] = rh;
        ww.w.runs[i] = rh;

        rxf_smp_handle dh =
            rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_DATA, sizeof(wit_data_payload), 0);
        wit_data_payload *data = (wit_data_payload *)rxf_smp_arena_resolve(&ww.arena, dh);
        memset(data, 0, sizeof(*data));
        data->run_index = i;
        data->seed = seed;
        data->guard[0] = data_guard(seed, i, 0);
        data->guard[1] = data_guard(seed, i, 1);
        rxf_smp_arena_release(&ww.arena, dh);
        ww.data_handles[i] = dh;
        run = (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, rh);
        run->data = dh;
        run->checksum = smp_run_checksum(run);
        rxf_smp_arena_release(&ww.arena, rh);
        /* A churn cell between the live cells: the delta frees these in the middle of the stream
         * so the compactor has real gaps to close under the live runs (S8). */
        if ((i & 1u) == 0 && ww.churn_count < WIT_MAX_RUNS / 2) {
            rxf_smp_handle churn =
                rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_SCRATCH, 24, 0);
            if (churn != RXF_SMP_HANDLE_NONE)
                ww.churn[ww.churn_count++] = churn;
        }
    }
    /* Every eighth pair keeps one pinned data cell (docs/03: a pin holds its source address; the
     * compactor must leave it and slide around it). */
    for (uint32_t p = 0; p < pairs; p += 8) {
        uint8_t *cell = (uint8_t *)rxf_smp_arena_resolve(&ww.arena, ww.data_handles[2 * p + 1]);
        if (!cell)
            continue;
        cell -= RXF_SMP_CELL_BYTES;
        uint64_t flags_word;
        memcpy(&flags_word, cell + RXF_SMP_C_FLAGS, 8);
        flags_word |= RXF_SMP_FLAG_PINNED;
        memcpy(cell + RXF_SMP_C_FLAGS, &flags_word, 8);
        rxf_smp_arena_release(&ww.arena, ww.data_handles[2 * p + 1]);
    }
    /* The ABA setup (S3): one slot is freed and handed out again. The old handle must be refused
     * at every resolve and at every ring pop; the new handle of the same slot must work. */
    rxf_smp_handle stale_handle = RXF_SMP_HANDLE_NONE, fresh_handle = RXF_SMP_HANDLE_NONE;
    {
        rxf_smp_handle s1 = rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_SCRATCH, 16, 0);
        stale_handle = s1;
        rxf_smp_arena_free(&ww.arena, s1);
        fresh_handle = rxf_smp_arena_alloc(&ww.arena, RXF_SMP_CELL_SCRATCH, 16, 0);
        if (stale_index != 0xFFFFFFFFu) {
            smp_run_payload *run =
                (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, ww.run_handles[stale_index]);
            run->stale = stale_handle;
            run->scratch = fresh_handle;
            run->checksum = smp_run_checksum(run);
            rxf_smp_arena_release(&ww.arena, ww.run_handles[stale_index]);
        }
    }
    ww.ring_offset_before = rxf_smp_arena_offset(&ww.arena, ww.w.ring);

    /* The initial ready-ring: the stale handle first (the pop must refuse it), then every run. */
    smp_ring_push(&ww.w, stale_handle);
    for (uint32_t i = 0; i < n_runs; i++) {
        smp_run_payload *run =
            (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, ww.run_handles[i]);
        uint32_t want = RXF_SMP_STATE_FREE;
        int ready = atomic_compare_exchange_strong_explicit(&run->state, &want, RXF_SMP_STATE_READY,
                                                            memory_order_acq_rel,
                                                            memory_order_acquire);
        rxf_smp_arena_release(&ww.arena, ww.run_handles[i]);
        if (ready)
            while (smp_ring_push(&ww.w, ww.run_handles[i]) != 0)
                sleep_ns(1000);
    }
    edge_notify(&ww.edge);

    pthread_t carriers[RXF_SMP_MAX_CARRIERS];
    carrier_arg args[RXF_SMP_MAX_CARRIERS];
    pthread_t mutator, watchdog;
    for (uint32_t i = 0; i < n_carriers; i++) {
        args[i].ww = &ww;
        args[i].index = i;
        pthread_create(&carriers[i], NULL, carrier_main, &args[i]);
    }
    int have_mutator = (flags & WIT_FLAG_RELOC) != 0;
    if (have_mutator)
        pthread_create(&mutator, NULL, mutator_main, &ww);
    pthread_create(&watchdog, NULL, watchdog_main, &ww);

    /* The join: every run ends, or the deadline reports a stall. */
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    uint32_t done = 0;
    for (;;) {
        sleep_ns(1000 * 1000);
        done = atomic_load_explicit(&ww.w.terminal, memory_order_acquire);
        clock_gettime(CLOCK_MONOTONIC, &now);
        if (done >= n_runs || now.tv_sec - start.tv_sec > 30)
            break;
    }
    atomic_store_explicit(&ww.w.stop, 1, memory_order_release);
    edge_notify(&ww.edge);
    for (uint32_t i = 0; i < n_carriers; i++)
        pthread_join(carriers[i], NULL);
    if (have_mutator)
        pthread_join(mutator, NULL);
    pthread_join(watchdog, NULL);
    ww.ring_offset_after = rxf_smp_arena_offset(&ww.arena, ww.w.ring);

    /* The final verification: every data cell must still hold exactly what its run wrote. */
    uint64_t sum = 0, x = 0, verify_bad = atomic_load(&ww.verify_bad);
    uint32_t moved_runs = 0, max_carriers_run = 0;
    uint64_t min_steps = ~0ULL, max_steps = 0;
    for (uint32_t i = 0; i < n_runs; i++) {
        smp_run_payload *run =
            (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, ww.run_handles[i]);
        wit_data_payload *data =
            (wit_data_payload *)rxf_smp_arena_resolve(&ww.arena, ww.data_handles[i]);
        if (run && data) {
            if (data->run_index != i || data->seed != seed || data->sum != run->acc_sum ||
                data->xor_value != run->acc_xor ||
                data->guard[0] != data_guard(seed, i, 0) ||
                data->guard[1] != data_guard(seed, i, 1))
                verify_bad++;
            sum += run->acc_sum;
            x ^= run->acc_xor;
            uint32_t count = 0;
            for (uint32_t b = 0; b < 32; b++)
                if (run->migrate_mask & (1u << b))
                    count++;
            if (count > 1)
                moved_runs++;
            if (count > max_carriers_run)
                max_carriers_run = count;
            if (run->steps < min_steps)
                min_steps = run->steps;
            if (run->steps > max_steps)
                max_steps = run->steps;
        }
        if (run)
            rxf_smp_arena_release(&ww.arena, ww.run_handles[i]);
        if (data)
            rxf_smp_arena_release(&ww.arena, ww.data_handles[i]);
    }

    /* The poison check (S8): what the passes vacated must still read as poison; anything else is
     * a stale raw address that got away. */
    uint64_t poison_ok = 1;
    if (ww.arena.old_frontier > ww.arena.frontier) {
        uint8_t *tail = ww.arena.base + ww.arena.frontier;
        uint64_t len = ww.arena.old_frontier - ww.arena.frontier;
#ifdef RXF_SMP_ASAN
        __asan_unpoison_memory_region(tail, (size_t)len);
#endif
        for (uint64_t i = 0; i < len; i++)
            if (tail[i] != 0xA5) {
                poison_ok = 0;
                break;
            }
    }

    /* The result (words 0 to 2: the deterministic part, S10) and the evidence. */
    uint64_t digest = 0;
    for (uint32_t i = 0; i < n_runs; i++) {
        smp_run_payload *run =
            (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, ww.run_handles[i]);
        if (run) {
            digest = mix64(digest ^ mix64(run->acc_sum + i * 0x9E3779B97F4A7C15ULL) ^
                           mix64(run->acc_xor + i * 0xC2B2AE3D27D4EB4FULL));
            rxf_smp_arena_release(&ww.arena, ww.run_handles[i]);
        }
    }
    out[WIT_OUT_DIGEST] = digest;
    out[WIT_OUT_SUM] = sum;
    out[WIT_OUT_XOR] = x;
    out[WIT_OUT_STEPS] = atomic_load(&ww.w.steps_total);
    out[WIT_OUT_CROSS] = atomic_load(&ww.w.cross_resumes);
    out[WIT_OUT_DEFERRED] = atomic_load(&ww.w.deferred_cross);
    out[WIT_OUT_STATE_REFUSED] = atomic_load(&ww.w.state_refused);
    out[WIT_OUT_CHECKSUM_BAD] = atomic_load(&ww.w.checksum_fails);
    out[WIT_OUT_RING_STALE] = atomic_load(&ww.w.ring_stale_refused);
    out[WIT_OUT_HANDLE_STALE] = atomic_load(&ww.w.handle_stale_refused);
    out[WIT_OUT_RING_FULL] = 0;
    out[WIT_OUT_CLAIM_SPINS] = ww.acc.claim_spins;
    out[WIT_OUT_CLAIM_SPINS_MAX] = ww.acc.claim_spins_max;
    out[WIT_OUT_MOVED_CELLS] = ww.acc.moved_cells;
    out[WIT_OUT_MOVED_BYTES] = ww.acc.moved_bytes;
    out[WIT_OUT_DEAD_CELLS] = ww.acc.dead_cells;
    out[WIT_OUT_PINNED_CELLS] = ww.acc.pinned_cells;
    out[WIT_OUT_PARKED_MOVED] = atomic_load(&ww.parked_moved);
    out[WIT_OUT_MIDFLIGHT_MOVED] = atomic_load(&ww.midflight_moved);
    out[WIT_OUT_MOVED_RUN_CELLS] = atomic_load(&ww.moved_run_cells);
    out[WIT_OUT_MOVED_DATA_CELLS] = atomic_load(&ww.moved_data_cells);
    out[WIT_OUT_MOVED_RING] = atomic_load(&ww.moved_ring);
    out[WIT_OUT_COMPACT_PASSES] = ww.passes;
    out[WIT_OUT_PARKS] = atomic_load(&ww.parks);
    out[WIT_OUT_WAKES] = atomic_load(&ww.wakes);
    out[WIT_OUT_SELFWAKES] = atomic_load(&ww.selfwakes);
    out[WIT_OUT_WATCHDOG_WAKES] = atomic_load(&ww.watchdog_wakes);
    out[WIT_OUT_DONE_RUNS] = done;
    out[WIT_OUT_MOVED_RUNS] = moved_runs;
    out[WIT_OUT_CARRIERS_MAX] = max_carriers_run;
    out[WIT_OUT_MIN_RUN_STEPS] = min_steps == ~0ULL ? 0 : min_steps;
    out[WIT_OUT_MAX_RUN_STEPS] = max_steps;
    out[WIT_OUT_RELOCATED_STEPS] = atomic_load(&ww.relocated_steps);
    out[WIT_OUT_DELTAS] = atomic_load(&ww.deltas);
    out[WIT_OUT_DELTA_CELLS] = atomic_load(&ww.delta_cells);
    out[WIT_OUT_DELTA_FREED] = atomic_load(&ww.delta_freed);
    out[WIT_OUT_FORWARD_OK] = atomic_load(&ww.forward_ok);
    out[WIT_OUT_FORWARD_LOST] = atomic_load(&ww.forward_lost);
    out[WIT_OUT_POISON_OK] = poison_ok;
    out[WIT_OUT_RING_OFFSET_0] = ww.ring_offset_before;
    out[WIT_OUT_RING_OFFSET_1] = ww.ring_offset_after;
    out[WIT_OUT_PAIR_PARK] = atomic_load(&ww.pair_park);
    out[WIT_OUT_PAIR_RESUME] = atomic_load(&ww.pair_resume);
    out[WIT_OUT_PAIR_WAKER] = atomic_load(&ww.pair_waker);
    out[WIT_OUT_VERIFY_BAD] = verify_bad + ww.acc.checksum_fails;
    out[WIT_OUT_ABA_OK] = 0;
    out[WIT_OUT_STALLED] = done >= n_runs ? 0 : n_runs - done;
    out[WIT_OUT_SPINS] = ww.acc.claim_spins;
    out[WIT_OUT_SPINS_MAX] = ww.acc.claim_spins_max;
    /* The ABA answer: the stale handle was refused and the fresh one of the same slot resolved. */
    if (stale_index != 0xFFFFFFFFu) {
        smp_run_payload *run =
            (smp_run_payload *)rxf_smp_arena_resolve(&ww.arena, ww.run_handles[stale_index]);
        if (run && run->ok == 1 && run->stale_refused >= 1)
            out[WIT_OUT_ABA_OK] = 1;
        if (run)
            rxf_smp_arena_release(&ww.arena, ww.run_handles[stale_index]);
        out[WIT_OUT_ABA_OK] |=
            RXF_SMP_H_INDEX(stale_handle) == RXF_SMP_H_INDEX(fresh_handle) ? 2 : 0;
    }
    for (uint32_t i = 0; i < n_carriers && i < 32; i++) {
        out[WIT_OUT_CARRIER_STEPS + i] = atomic_load(&ww.w.carrier_steps[i]);
        out[WIT_OUT_CARRIER_POPS + i] = atomic_load(&ww.w.carrier_pops[i]);
    }

    smp_world_deinit(&ww.w);
    wit_edge_deinit(&ww.edge);
    rxf_smp_arena_destroy(&ww.arena);
}
