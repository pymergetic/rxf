#ifndef RXF_SMP_SCHED_H
#define RXF_SMP_SCHED_H

/*
 * The scheduler of the SMP proof (docs/20, "Suspension is a data property"): runs are cells, the
 * ready-ring is a cell, and N carrier threads run one identical dispatcher loop over both. This
 * file is the whole knowledge the core has of concurrency: two cell layouts and a state machine.
 * Nothing here knows about pthreads, asyncio or Metal: the carriers wait through a callback the
 * edge supplies and the edge learns of work through a notify callback (the edge-shim projection of
 * docs/20, S9). A product runs this file against the real interpreter (native/graph.c) and its
 * safe point (native/safe.c); the proof runs it against the witness programs of smp_witness.c.
 *
 * The rules this implements are S1 to S8 of docs/20. The proofs are tests/host/smp_witness.c and
 * tests/test_smp.py.
 */
#include "smp_arena.h"

#include <stdatomic.h>
#include <stdint.h>

#define RXF_SMP_MAX_CARRIERS 32u

/* The run-cell state machine (docs/20, S1). Every transition is one CAS on the state word of the
 * run cell; this table is the only authority for who may make which one.
 *
 *   READY    -> RUNNING   a carrier, as it takes the run out of the ready-ring (S3)
 *   RUNNING  -> PARKED    the owning carrier, when the program waits (S4)
 *   RUNNING  -> READY     the owning carrier, at a yield (S3)
 *   RUNNING  -> TERMINAL  the owning carrier, at the end of the program (S1)
 *   PARKED   -> READY     whoever satisfies the wait condition (S4)
 *   READY    -> PARKED    the waker of the transition above, as long as it has not enqueued the
 *                         run yet (the wake is committed only by the enqueue, and a waker takes
 *                         its wake back when the ring refuses it or when the proof defers the
 *                         resume to another carrier). A run that is in the ring is never taken
 *                         back, and nothing else may make this transition.
 *
 * A transition that is not in this table fails its CAS and changes nothing (the refusals count).
 * A suspended run is a cell in state PARKED: no word of it names a thread, a stack or a register
 * (S2). */
enum {
    RXF_SMP_STATE_FREE = 0,
    RXF_SMP_STATE_READY = 1,
    RXF_SMP_STATE_RUNNING = 2,
    RXF_SMP_STATE_PARKED = 3,
    RXF_SMP_STATE_TERMINAL = 4
};

/*
 * The run cell (docs/20, S1): the state word, the wake token, the handles of what the run reads
 * (never addresses, S7), and the whole volatile state of the program between two safe points.
 *
 * `state`, `token`, `waker_run`, `midflight`, `mp_raise_slot` and `mp_sched_flag` are written
 * concurrently (a waker from any carrier, the compactor) and are C11 atomics. The fields from
 * run_index to migrate_mask are handoff metadata and proof evidence: written by the owner or by
 * the popper under the state machine and the ring lock. Everything from `self` to the guards is
 * the program state of the run: written only under the ownership of one carrier between two safe
 * points, covered by the checksum, which is verified at the step boundary and rewritten while the
 * run is still owned. The handoff of ownership is the ready-ring: the fields of a run are written
 * by one carrier and read by the next, with the ring lock and the state CAS in between (S5).
 *
 * The step boundary is also where the run cell is sealed (S5): a step's epilogue — `steps`, the
 * guards, `checksum` — is several stores, and it must all land before the state machine lets a
 * next carrier claim the run. The dispatcher therefore seals the cell inside the RUNNING window
 * and makes the state CAS (or the enqueue that follows it) the last thing that happens to the
 * cell: the non-atomic words then have exactly one writer at a time, each publishing through the
 * release CAS of the handoff that the next owner acquires.
 */
typedef struct {
    /* concurrent words: atomics only */
    _Atomic(uint32_t) state;
    _Atomic(uint32_t) token;     /* the wake token (S4): set before a wake, consumed at a park */
    _Atomic(uint32_t) waker_run; /* who woke this run last (evidence, not protocol) */
    _Atomic(uint32_t) midflight; /* bumped when the compactor moves this run while it runs */
    /* the MicroPython seam (docs/20, S11): the pending-flag words of a VM run, deposited by any
     * waker and consumed at the run's own poll (mp_handle_pending) — concurrent like the token */
    _Atomic(uint64_t) mp_raise_slot;
    _Atomic(uint32_t) mp_sched_flag;
    /* handoff metadata and evidence */
    uint32_t run_index;
    uint32_t kind;
    uint32_t park_carrier, deferred;
    uint32_t resume_carrier, last_carrier, resumes, migrate_refs, migrate_mask;
    /* the program state of the run: the owner's alone between two safe points */
    rxf_smp_handle self, data, channel, scratch, stale;
    uint64_t seed, pc, acc_sum, acc_xor, sent, rx_seen, data_offset, steps, target_steps;
    uint32_t ok, verify_bad, relocated, ring_refused, stale_refused;
    /* the VM state of the MicroPython shape (S11): per-run, so the interpreter reads its own
     * run's VM state with no lock and no claim — the checkpoint an nlr-shaped jump restores */
    uint64_t mp_frame_sum, mp_frame_xor;
    uint32_t mp_block, mp_iter, mp_polls, mp_unwinds;
    uint32_t mp_raise_seen, mp_sched_seen, mp_events, mp_raise_events, mp_won, mp_refused;
    uint32_t mp_deposits;
    uint32_t guard1[2], guard2[2];
    uint64_t checksum;
} smp_run_payload;

/*
 * The ready-ring cell (docs/20, S3): a FIFO of run handles of `capacity` slots, with its own lock
 * word in the cell like any other word. The handles are index | generation, so a handle to a run
 * slot that was freed and handed out again is refused at the pop (rxf_smp_arena_is_stale) and
 * never rebound to the new cell of that slot: the ring never resumes the wrong run (the ABA
 * refusal of S3). A run that is enqueued twice is refused at the READY to RUNNING CAS and counts
 * as one execution. A push that finds the ring full changes nothing; the pusher retries.
 */
typedef struct {
    _Atomic(uint32_t) lock;
    uint32_t head, tail, capacity;
    uint64_t pushed, popped, refused_stale, refused_full, wakes;
    rxf_smp_handle slots[1]; /* capacity of them; the cell holds the rest */
} smp_ring_payload;

/* The result of one step of a run: what the program answers to the dispatcher. */
enum {
    SMP_STEP_AGAIN = 0,   /* more work: RUNNING to READY, back into the ring */
    SMP_STEP_PARK = 1,    /* the run is PARKED (or was woken before it slept and requeued) */
    SMP_STEP_DONE = 2,    /* the program ended: RUNNING to TERMINAL */
    SMP_STEP_SELFWAKE = 3 /* the run woke itself at its own park; the dispatcher requeues it */
};

struct smp_world;

/*
 * One step of a run: the program the run cells carry. The proof's programs live in the witness; in
 * a product this is one instruction of the interpreter (native/graph.c) or one safe-point interval
 * of compiled code. It runs with the run owned and its claims held, and it must release every
 * claim and keep no address across its end: the end of a step is a safe point (S7).
 */
typedef int (*smp_step_fn)(void *ctx, struct smp_world *w, smp_run_payload *run, uint32_t carrier);

/* The end of a step: what a program must do to its own run cell before it is sealed. The proof's
 * programs scribble their guard words during a step (a torn copy of the run cell must change
 * them) and restore them here, while the run is still owned and claimed. The seal itself — this
 * duty, the step count, the checksum — is the dispatcher's (or the park's, when the step ends in
 * a park: that handoff seals before the RUNNING to PARKED CAS, smp_sched.c), and no program word
 * of the cell is written after it (docs/20, S5). A product with no guards may leave this NULL. */
typedef void (*smp_step_end_fn)(void *ctx, struct smp_world *w, smp_run_payload *run,
                                uint32_t carrier);

/* How a carrier waits when no run is ready: the edge shim (a condvar on the host). Returns when
 * work may exist or the wait timed out; the dispatcher never learns what the shim is. */
typedef void (*smp_wait_fn)(void *ctx);
/* How a carrier steps aside while it defers a run to another carrier (the proof knob of S4). The
 * edge decides how it waits, as with smp_wait_fn; the core stays free of the platform. */
typedef void (*smp_backoff_fn)(void *ctx);
/* The doorbell: the core tells the edge that the ring is not empty. The whole knowledge the core
 * has of pthreads, asyncio or Metal is these three callbacks. */
typedef void (*smp_notify_fn)(void *ctx);

typedef struct smp_world {
    rxf_smp_arena *arena;
    rxf_smp_handle ring;    /* the ready-ring cell (pinned while the world runs, docs/20 S8) */
    rxf_smp_handle channel; /* the channel frame the runs park on (docs/20, S4) */
    rxf_smp_handle *runs;
    uint32_t n_runs;
    _Atomic(uint32_t) terminal;
    _Atomic(uint32_t) stop;
    smp_step_fn step;
    smp_step_end_fn step_end; /* the step's own closing duty, before the seal (S5) */
    void *step_ctx;
    smp_wait_fn wait;
    smp_backoff_fn backoff;
    smp_notify_fn notify;
    void *edge_ctx;
    /* Proof knob: when set, a carrier that would resume the run it parked steps aside and lets
     * another carrier have it (docs/20, S4: READY to PARKED is not needed; the run is pushed back
     * untouched). A product does not need it; the stress run counts unprompted cross-carrier
     * resumes without it. */
    int force_cross_resume;
    /* The evidence the proof reads. */
    _Atomic(uint64_t) pops, steps_total, ring_stale_refused, handle_stale_refused;
    _Atomic(uint64_t) state_refused, checksum_fails;
    _Atomic(uint64_t) ring_empty, cross_resumes, deferred_cross;
    _Atomic(uint64_t) carrier_steps[RXF_SMP_MAX_CARRIERS];
    _Atomic(uint64_t) carrier_pops[RXF_SMP_MAX_CARRIERS];
} smp_world;

void smp_world_init(smp_world *w, rxf_smp_arena *arena, uint32_t n_runs, smp_step_fn step,
                    void *step_ctx);
void smp_world_deinit(smp_world *w);

/* The ready-ring protocol (docs/20, S3). */
int smp_ring_push(smp_world *w, rxf_smp_handle run); /* 0 pushed, 1 refused full */
int smp_ring_pop(smp_world *w, rxf_smp_handle *out); /* 0 popped, 1 empty */
int smp_ring_peek(smp_world *w);                     /* 1 when the ring is not empty (S9) */

/* The wake (docs/20, S4): set the token, then PARKED to READY, then into the ring. 0 when the run
 * is in the ring, 1 when it was not parked (a no-op: nothing is enqueued twice), 2 when the ring
 * refused it and the wake was taken back (the caller retries), 3 when the proof deferred it. */
int smp_wake(smp_world *w, rxf_smp_handle target, uint32_t waker);

/* The park (docs/20, S4): RUNNING to PARKED with the token protocol (a wake that arrived before
 * the park is consumed here, so no wake is ever lost). It answers with the step result the
 * dispatcher must act on: SMP_STEP_AGAIN when the run is still RUNNING (a wake was already
 * waiting), SMP_STEP_SELFWAKE when the run woke itself and must be requeued (the dispatcher does
 * it: the run cell is already sealed and never handed back half-written), and SMP_STEP_PARK when
 * the run sleeps.
 *
 * The park is the step's handoff (S5), so it seals the cell — the step's closing duty, the step
 * count, the checksum — before the RUNNING to PARKED CAS: a waker may requeue a PARKED run at
 * once, and the next owner must find whole state. The park is therefore the step's last act on
 * the run cell: after it answers, the program touches nothing of the cell again and returns the
 * answer (counters of the world are fine; program words are not). */
int smp_park(smp_world *w, smp_run_payload *run, uint32_t carrier);

/* The dispatcher: one step of one run (0), or the answers 1 (ring empty) and 2 (world stopped).
 * The loop every carrier runs is smp_dispatch_loop; it is the same code for every carrier. */
int smp_dispatch_one(smp_world *w, uint32_t carrier);
int smp_dispatch_loop(smp_world *w, uint32_t carrier);

/* The checksum over the volatile fields of a run cell: what proves no step saw a half-written run
 * and no compaction torn one (S8). It is compared exactly at the step boundary the ownership claim
 * marks — the one point where the cell must be whole and its writer is known — and rewritten by
 * the owner before the state machine may hand the run on. Never check it at any other position:
 * a comparison against a cell whose owner is still closing its step is a race in the check, not a
 * torn cell (docs/20, S8). */
uint64_t smp_run_checksum(const smp_run_payload *run);

#endif
