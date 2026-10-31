#ifndef RXF_SMP_ASYNCIO_H
#define RXF_SMP_ASYNCIO_H

/*
 * A model of the uasyncio binding (docs/23 4.5; docs/20, S11) — NOT uasyncio, and never a second
 * scheduler. It is the standalone C model the proof runs so the mapping table of docs/23 4.5 is
 * written down once and proven on the host, against the run-cell machine of smp_sched.c:
 *
 *   uasyncio                      the model (one machine, no second queue)
 *   ----------------------------  -----------------------------------------------------------
 *   Task                          one run cell (S1); its program counter and result fold live
 *                                 in the run cell, the "coroutine frame chain" is that state
 *   create_task / call_soon       READY, then smp_ring_push: THE ready-ring is the loop's
 *                                 ready deque (S3)
 *   await on a Task / Event       the waiter registers on the object's list (under the loop
 *                                 lock), then smp_park: PARKED (S4)
 *   Task end / Event.set          the waker takes the list and calls smp_wake once per waiter,
 *                                 targeting the RUN — never a carrier (S4)
 *   sleep_ms / ticks_ms           the edge timer (S9): a timer set the EDGE fires, at world
 *                                 quiescence, on a virtual clock — never a carrier either
 *   Task.cancel                   a raise deposit (smp_mp_schedule_raise into mp_raise_slot,
 *                                 the CancelledError) plus a wake; the target consumes it at
 *                                 its next poll, the safe point (smp_mp_handle_pending, S11)
 *   run_until_complete            the main task's run reaching TERMINAL
 *
 * Results are a function of the PROGRAM alone for every carrier count when the tasks
 * communicate through awaits only: the loop objects (waiter lists, timer set, virtual clock)
 * are guarded by one lock word (in the product they are cells and the lock a cell word, like
 * the ring's), and the clock advances only when the world is quiescent — no run READY or
 * RUNNING and the ring empty (an epoch re-check closes the scan against a wake in flight), so
 * "what time it is" never depends on which carrier ran what. The ORDER of `emit` entries across
 * tasks is the interleaving: at one carrier it is the ring's FIFO order (the order of uasyncio's
 * own queue), at N carriers it is evidence (the multiset and each task's own sequence are
 * results, the total order is not).
 *
 * The program is data: tasks of ops (smp_asyncio_prove.c reads the text form), so the same
 * corpus runs under MicroPython's real asyncio (tests/asyncio_corpus/reference.py) and under
 * this model, and the two reports are compared line by line.
 */
#include <stdint.h>

#define AIO_MAX_TASKS 16u
#define AIO_MAX_OPS 512u
#define AIO_MAX_EVENTS 8u
#define AIO_MAX_TRACE 1024u

/* The ops of a task (the asyncio surface the corpus uses). */
typedef enum {
    AIO_OP_MARK = 0, /* fold the argument into the task's result */
    AIO_OP_EMIT = 1, /* append (task, argument) to the shared trace */
    AIO_OP_SLEEP =
        2, /* await sleep: 0 is a yield (the run goes back into the ring), else a timer */
    AIO_OP_SPAWN = 3, /* create_task(task argument): call_soon of its first step */
    AIO_OP_JOIN = 4,  /* await task argument; folds its result (or the cancel marker) */
    AIO_OP_WAIT = 5,  /* await event argument (returns at once when it is set) */
    AIO_OP_SET = 6,   /* event argument .set(): every waiter wakes, in arrival order */
    AIO_OP_CLEAR = 7, /* event argument .clear() */
    AIO_OP_CANCEL =
        8,         /* task argument .cancel(): forwarded to the task it awaits, as uasyncio does */
    AIO_OP_NOW = 9 /* fold the clock (ticks) into the result */
} aio_op_kind;

/* The words every executor folds: result = result * 31 + word + 1 (mod 2^32), starting at 7. The
 * cancel marker is what a join of a cancelled task folds. */
#define AIO_FOLD_START 7u
#define AIO_CANCEL_WORD 3148u

typedef struct {
    uint32_t kind;
    uint32_t arg;
} aio_op;

typedef struct {
    uint32_t n_tasks, n_events;
    uint32_t n_ops[AIO_MAX_TASKS];
    aio_op ops[AIO_MAX_TASKS][AIO_MAX_OPS];
} aio_program;

/* Task end states of the report. */
enum { AIO_STATE_PENDING = 0, AIO_STATE_DONE = 1, AIO_STATE_CANCELLED = 2 };

/* The flags of a run: what the harness does around the program, never what it computes. */
#define AIO_FLAG_COMPACT 1u /* compact the arena (sliding pass, S8) while the tasks run */

typedef struct {
    uint32_t state[AIO_MAX_TASKS];
    uint32_t result[AIO_MAX_TASKS]; /* the fold of a DONE task; 0 otherwise */
    uint32_t trace_len;
    uint32_t trace_task[AIO_MAX_TRACE];
    uint32_t trace_value[AIO_MAX_TRACE];
    /* Evidence (never a result). */
    uint64_t pops, parks, wakes, timer_fires, cancels, raise_polls, ring_pushed, ring_popped;
    uint64_t cross_resumes, state_refused, checksum_fails, moved_cells, compact_passes;
    uint64_t refused_spawns, quiescent_scans, clock_end, spurious_resumes;
    uint32_t carriers_stepping; /* how many distinct carriers ran at least one step */
    uint32_t deadlock;          /* 1 when the world stalled with tasks pending and no timer */
} aio_report;

/* Parse the text form into a program. Answers 0, or a nonzero code and a one-line reason in
 * err. The text form is: "events N", then per task "task I" followed by "op NAME [ARG]" lines. */
int aio_program_parse(aio_program *program, const char *text, char *err, unsigned long err_size);

/* Run the program on n_carriers pthread carriers over the ready-ring. 0 on completion (the main
 * task terminal, or a deadlock reported in the report), nonzero on a harness failure. */
int aio_run(const aio_program *program, uint32_t n_carriers, uint32_t flags, aio_report *report);

#endif
