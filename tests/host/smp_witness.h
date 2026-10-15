#ifndef RXF_SMP_WITNESS_H
#define RXF_SMP_WITNESS_H

/*
 * The SMP proof harness (docs/20, C2): N pthread carriers over the scheduler of smp_sched.c, run
 * and data cells in a real arena, and the evidence the proof reads. This is the edge shim of the
 * policy: the core (smp_sched.c) learns nothing here of pthreads except through smp_wait_fn and
 * smp_notify_fn, and the harness projects the run-cell states onto pthreads the way an asyncio
 * Future or a Metal event ring would project them onto its own edge (docs/20, S9).
 *
 * The face of the harness is one call: wit_go runs one whole witness program on n_carriers threads
 * and writes 128 words of result and evidence. Everything about the program is a function of
 * (seed, n_runs, steps, messages): a step reads and writes its own run cell and its own data cell
 * and never the program state of any other run (the seam of S11 deposits into another run's
 * concurrent words exactly as a wake does), so the result is the same whatever the carrier count
 * and whatever the interleaving (docs/20, S10). What the interleaving decides is only who executes
 * a step and when; the proof reads that back as evidence (per-carrier counters, park/resume pairs,
 * moves observed under runs) and never mixes it into the result.
 */
#include <stdint.h>

/* The flags of a witness run: what the harness does around the program, never what the program
 * computes. The differential runs the same program with and without them and needs the same
 * result words (0 to 2) either way. */
#define WIT_FLAG_RELOC 2u       /* compact the arena while the runs execute */
#define WIT_FLAG_DELTA 4u       /* apply a delta (writes, new cells, frees) while they execute */
#define WIT_FLAG_FORCE_CROSS 8u /* the proof knob: a resume never lands on the parking carrier */

/* The evidence of one run, as words. The words 0 to 2 are the deterministic result of the program
 * and must be equal for every carrier count; the rest is evidence and may differ. */
#define WIT_OUT_WORDS 128u

enum {
    WIT_OUT_DIGEST = 0, /* the deterministic result of the whole program */
    WIT_OUT_SUM = 1,    /* the deterministic accumulations */
    WIT_OUT_XOR = 2,
    WIT_OUT_STEPS = 3,         /* steps executed in total */
    WIT_OUT_CROSS = 4,         /* resumes by a carrier other than the one that parked the run */
    WIT_OUT_DEFERRED = 5,      /* the proof knob moved aside (docs/20, S4) */
    WIT_OUT_STATE_REFUSED = 6, /* a READY to RUNNING CAS that failed: a run enqueued twice */
    WIT_OUT_CHECKSUM_BAD = 7,  /* a step that found a torn run cell */
    WIT_OUT_RING_STALE = 8,    /* the ABA refusal at the pop (docs/20, S3) */
    WIT_OUT_HANDLE_STALE = 9,  /* a stale handle refused at a resolve (docs/20, S7) */
    WIT_OUT_RING_FULL = 10,    /* a push that found the ring full */
    WIT_OUT_CLAIM_SPINS = 11,  /* what the compactor waited for claims (docs/20, S5) */
    WIT_OUT_CLAIM_SPINS_MAX = 12,
    WIT_OUT_MOVED_CELLS = 13,
    WIT_OUT_MOVED_BYTES = 14,
    WIT_OUT_DEAD_CELLS = 15,
    WIT_OUT_PINNED_CELLS = 16,
    WIT_OUT_PARKED_MOVED = 17,    /* cells moved under a parked run (docs/20, S7) */
    WIT_OUT_MIDFLIGHT_MOVED = 18, /* cells moved under a live run between safe points */
    WIT_OUT_MOVED_RUN_CELLS = 19,
    WIT_OUT_MOVED_DATA_CELLS = 20,
    WIT_OUT_MOVED_RING = 21, /* 1 when the ready-ring cell itself was relocated */
    WIT_OUT_COMPACT_PASSES = 22,
    WIT_OUT_DELTAS = 23,
    WIT_OUT_DELTA_CELLS = 24,
    WIT_OUT_DELTA_FREED = 25,
    WIT_OUT_RELOCATED_STEPS = 26, /* steps that saw their data cell at a new offset */
    WIT_OUT_DONE_RUNS = 27,
    WIT_OUT_MOVED_RUNS = 28,   /* runs whose steps were seen on more than one carrier (S5) */
    WIT_OUT_CARRIERS_MAX = 29, /* the most carriers any one run was seen on */
    WIT_OUT_MIN_RUN_STEPS = 30,
    WIT_OUT_MAX_RUN_STEPS = 31,
    WIT_OUT_FORWARD_OK = 32, /* moved cells whose C_FORWARD was read back during the pass */
    WIT_OUT_POISON_OK = 33,  /* passes whose vacated bytes stayed poison (nothing touched them) */
    WIT_OUT_RING_OFFSET_0 = 34,
    WIT_OUT_RING_OFFSET_1 = 35,
    WIT_OUT_PARKS = 36,          /* steps that ended in a park */
    WIT_OUT_WAKES = 37,          /* wakes one run performed on another, while it was parked */
    WIT_OUT_SELFWAKES = 38,      /* parks that found a wake waiting (token protocol, S4) */
    WIT_OUT_WATCHDOG_WAKES = 39, /* the edge shim woke a parked run (timeout liveness, S9) */
    WIT_OUT_CARRIER_STEPS = 40,  /* 40 .. 40 + 31 */
    WIT_OUT_CARRIER_POPS = 72,   /* 72 .. 72 + 31 */
    WIT_OUT_ABA_OK = 104,      /* the stale handle was refused on a re-used slot (S3) */
    WIT_OUT_PAIR_PARK = 105,   /* the carrier that parked the channel receiver */
    WIT_OUT_PAIR_RESUME = 106, /* the carrier that resumed it (must differ, S4) */
    WIT_OUT_VERIFY_BAD = 107,  /* a step that found its data cell wrong (S8) */
    WIT_OUT_GATE_DRAINS = 108, /* the proof knob froze the dispatchers (the safe-point gate) */
    WIT_OUT_POISON_BAD = 109,  /* passes whose vacated bytes were touched (must be 0) */
    WIT_OUT_STALLED = 110,     /* runs that did not end before the deadline (must be 0) */
    WIT_OUT_SPINS = 111,       /* what the compactor waited for claims in total */
    WIT_OUT_SPINS_MAX = 112,
    WIT_OUT_PAIR_WAKER = 113,  /* the run that woke the channel receiver (the sender's identity) */
    WIT_OUT_FORWARD_LOST = 114, /* moved cells whose old place a later slide covered: the record
                                 * cannot survive there; with FORWARD_OK it accounts for every
                                 * moved cell exactly (S8) */
    /* The MicroPython seam (docs/20, S11): the pending-word traffic of the MPVM programs. */
    WIT_OUT_MP_EVENTS = 115,        /* events the interrupt sources attempted (raises + scheds) */
    WIT_OUT_MP_RAISE_EVENTS = 116,  /* of those, raises (the even schedule of the source) */
    WIT_OUT_MP_RAISE_WON = 117,     /* raises that won the pending slot */
    WIT_OUT_MP_RAISE_REFUSED = 118, /* the Refuse path: a raise was already pending */
    WIT_OUT_MP_DELIVERED = 119,     /* raises delivered at a poll */
    WIT_OUT_MP_PENDING_END = 120,   /* raises still pending when the VM run ended */
    WIT_OUT_MP_SCHED = 121,         /* scheduled callbacks consumed at polls */
    WIT_OUT_MP_UNWINDS = 122,       /* nlr-shaped unwinds: the block rewound and retried */
    WIT_OUT_MP_POLLS = 123,         /* mp_handle_pending calls (the VM runs' safe points) */
    WIT_OUT_MP_RAISE_DEPOSITS = 124 /* raise deposits attempted (won + refused accounts for
                                     * every one of them exactly, S11) */
};

void wit_go(uint32_t n_runs, uint32_t n_carriers, uint32_t seed, uint64_t steps_per_run,
            uint32_t messages, uint32_t flags, uint64_t *out);

#endif
