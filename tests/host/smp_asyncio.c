/*
 * The model of the uasyncio binding (smp_asyncio.h; docs/23 4.5). Not uasyncio: the proof that a
 * task is a run cell, that the ready-ring IS the loop's ready deque, that an await is a park and
 * a set_result/Event.set a wake targeting the run, that sleep is the edge timer, and that a
 * cancel is a raise deposit plus a wake. The scheduler is smp_sched.c, unchanged and shared with
 * the witness; the MicroPython seam words are smp_mpmodel.c; this file adds the loop objects
 * (waiter lists, the timer set, the virtual clock) and the interpreter of the task ops.
 */
#include "smp_asyncio.h"

#include "smp_arena.h"
#include "smp_mpmodel.h"
#include "smp_sched.h"

#include <ctype.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ---- the text form of a program ---- */

static const char *const OP_NAMES[] = {"mark", "emit", "sleep", "spawn",  "join",
                                       "wait", "set",  "clear", "cancel", "now"};
#define OP_COUNT (sizeof(OP_NAMES) / sizeof(OP_NAMES[0]))

static int parse_fail(char *err, unsigned long err_size, const char *message, unsigned line) {
    snprintf(err, err_size, "line %u: %s", line, message);
    return 1;
}

int aio_program_parse(aio_program *program, const char *text, char *err, unsigned long err_size) {
    memset(program, 0, sizeof(*program));
    int current = -1;
    unsigned line_no = 0;
    const char *p = text;
    while (*p) {
        const char *end = strchr(p, '\n');
        size_t length = end ? (size_t)(end - p) : strlen(p);
        char line[128];
        line_no++;
        if (length >= sizeof(line))
            return parse_fail(err, err_size, "line too long", line_no);
        memcpy(line, p, length);
        line[length] = '\0';
        p += length + (end ? 1 : 0);

        char word[16] = "";
        char name[16] = "";
        unsigned long value = 0;
        int got = sscanf(line, "%15s %15s %lu", word, name, &value);
        if (got <= 0)
            continue; /* a blank line */
        if (strcmp(word, "events") == 0) {
            /* "events N": the second word is the count */
            unsigned long count = strtoul(name, NULL, 10);
            if (got < 2 || count > AIO_MAX_EVENTS)
                return parse_fail(err, err_size, "events: a count up to AIO_MAX_EVENTS", line_no);
            program->n_events = (uint32_t)count;
        } else if (strcmp(word, "task") == 0) {
            unsigned long index = strtoul(name, NULL, 10);
            if (got < 2 || index != program->n_tasks || index >= AIO_MAX_TASKS)
                return parse_fail(err, err_size, "task: indices count up from 0", line_no);
            program->n_tasks = (uint32_t)index + 1;
            current = (int)index;
        } else if (strcmp(word, "op") == 0) {
            if (current < 0)
                return parse_fail(err, err_size, "op before any task", line_no);
            uint32_t kind = OP_COUNT;
            for (uint32_t i = 0; i < OP_COUNT; i++)
                if (strcmp(name, OP_NAMES[i]) == 0)
                    kind = i;
            if (kind == OP_COUNT)
                return parse_fail(err, err_size, "op: unknown name (refused by name)", line_no);
            if (program->n_ops[current] >= AIO_MAX_OPS)
                return parse_fail(err, err_size, "op: the task has too many ops", line_no);
            if (kind == AIO_OP_NOW && got != 2)
                return parse_fail(err, err_size, "op now takes no argument", line_no);
            if (kind != AIO_OP_NOW && got != 3)
                return parse_fail(err, err_size, "op: one argument expected", line_no);
            program->ops[current][program->n_ops[current]++] =
                (aio_op){.kind = kind, .arg = (uint32_t)value};
        } else {
            return parse_fail(err, err_size, "unknown line", line_no);
        }
    }
    /* The checks the executors share: every reference names something that exists. */
    for (uint32_t t = 0; t < program->n_tasks; t++)
        for (uint32_t i = 0; i < program->n_ops[t]; i++) {
            const aio_op *op = &program->ops[t][i];
            switch (op->kind) {
            case AIO_OP_SPAWN:
            case AIO_OP_JOIN:
            case AIO_OP_CANCEL:
                if (op->arg >= program->n_tasks || op->arg == t)
                    return parse_fail(err, err_size,
                                      "a task op names a task that does not exist, or itself "
                                      "(uasyncio refuses to cancel or await oneself)",
                                      0);
                break;
            case AIO_OP_WAIT:
            case AIO_OP_SET:
            case AIO_OP_CLEAR:
                if (op->arg >= program->n_events)
                    return parse_fail(err, err_size, "an event op names no event", 0);
                break;
            default:
                break;
            }
        }
    if (program->n_tasks == 0)
        return parse_fail(err, err_size, "a program needs a main task", 0);
    return 0;
}

/* ---- the world ---- */

typedef enum { BLOCKED_NONE = 0, BLOCKED_SLEEP, BLOCKED_EVENT, BLOCKED_TASK } blocked_kind;

typedef struct {
    smp_world w;
    rxf_smp_arena arena;
    const aio_program *program;
    rxf_smp_handle runs[AIO_MAX_TASKS];
    rxf_smp_handle churn[AIO_MAX_TASKS];
    uint32_t churn_count;
    _Atomic(uint32_t) churn_used;
    uint32_t flags;
    /* the edge doorbell (S9): the core knows only the three callbacks */
    pthread_mutex_t edge_lock;
    pthread_cond_t edge_cond;
    int edge_work;
    /* the loop objects, guarded by loop_lock (in the product: cells, the lock a cell word) */
    _Atomic(uint32_t) loop_lock;
    uint32_t done[AIO_MAX_TASKS], cancelled[AIO_MAX_TASKS], result[AIO_MAX_TASKS];
    uint32_t n_waiters[AIO_MAX_TASKS], waiters[AIO_MAX_TASKS][AIO_MAX_TASKS];
    uint32_t ev_set[AIO_MAX_EVENTS], n_ev_waiters[AIO_MAX_EVENTS];
    uint32_t ev_waiters[AIO_MAX_EVENTS][AIO_MAX_TASKS];
    uint32_t blocked[AIO_MAX_TASKS], blocked_arg[AIO_MAX_TASKS];
    uint32_t timer_live[AIO_MAX_TASKS];
    uint64_t timer_due[AIO_MAX_TASKS], timer_seq[AIO_MAX_TASKS];
    uint64_t vnow, next_seq;
    /* a wake or a spawn bumps this before it acts: the quiescence scan re-reads it */
    _Atomic(uint64_t) epoch;
    _Atomic(uint32_t) trace_n;
    uint32_t trace_task[AIO_MAX_TRACE], trace_value[AIO_MAX_TRACE];
    /* the evidence */
    _Atomic(uint64_t) wakes, parks, timer_fires, cancels, raise_polls, refused_spawns, scans;
    _Atomic(uint64_t) moved_cells, compact_passes, spurious;
} aio_world;

static void loop_lock(aio_world *a) {
    while (atomic_exchange_explicit(&a->loop_lock, 1, memory_order_acquire))
        ;
}

static void loop_unlock(aio_world *a) {
    atomic_store_explicit(&a->loop_lock, 0, memory_order_release);
}

static uint32_t fold(uint32_t result, uint32_t word) {
    return result * 31u + word + 1u;
}

static void sleep_ns(long ns) {
    struct timespec ts = {0, ns};
    nanosleep(&ts, NULL);
}

/* ---- the edge shim (S9): a condvar the core knows nothing about ---- */

static void edge_notify(void *ctx) {
    aio_world *a = (aio_world *)ctx;
    pthread_mutex_lock(&a->edge_lock);
    a->edge_work = 1;
    pthread_cond_broadcast(&a->edge_cond);
    pthread_mutex_unlock(&a->edge_lock);
}

static void edge_wait(void *ctx) {
    aio_world *a = (aio_world *)ctx;
    pthread_mutex_lock(&a->edge_lock);
    if (a->edge_work) {
        a->edge_work = 0;
        pthread_mutex_unlock(&a->edge_lock);
        return;
    }
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    ts.tv_nsec += 2 * 1000 * 1000;
    if (ts.tv_nsec >= 1000 * 1000 * 1000) {
        ts.tv_sec += 1;
        ts.tv_nsec -= 1000 * 1000 * 1000;
    }
    pthread_cond_timedwait(&a->edge_cond, &a->edge_lock, &ts);
    pthread_mutex_unlock(&a->edge_lock);
}

/* ---- the loop's primitives: each is a machine word, never a second queue ---- */

/* A wake targeting the RUN: the token, PARKED to READY, the ring (smp_wake, S4). The epoch moves
 * first so a quiescence scan that straddles this wake is refused. */
static void aio_wake(aio_world *a, uint32_t task, uint32_t waker) {
    atomic_fetch_add_explicit(&a->epoch, 1, memory_order_acq_rel);
    if (smp_wake(&a->w, a->runs[task], waker) == 0)
        atomic_fetch_add_explicit(&a->wakes, 1, memory_order_relaxed);
}

/* create_task: the run goes FREE to READY and into the ring — call_soon of its first step. */
static void aio_spawn(aio_world *a, uint32_t task) {
    atomic_fetch_add_explicit(&a->epoch, 1, memory_order_acq_rel);
    smp_run_payload *run = smp_run_resolve(&a->w, a->runs[task]);
    uint32_t want = RXF_SMP_STATE_FREE;
    int started = atomic_compare_exchange_strong_explicit(
        &run->state, &want, RXF_SMP_STATE_READY, memory_order_acq_rel, memory_order_acquire);
    rxf_smp_arena_release(&a->arena, a->runs[task]);
    if (!started) {
        atomic_fetch_add_explicit(&a->refused_spawns, 1, memory_order_relaxed);
        return;
    }
    while (smp_ring_push(&a->w, a->runs[task]) != 0)
        ;
    edge_notify(a);
}

/* The end of a task: the result is published and every waiter wakes, in arrival order. */
static void aio_finish(aio_world *a, uint32_t task, int cancelled, uint32_t result) {
    uint32_t woken[AIO_MAX_TASKS];
    loop_lock(a);
    a->done[task] = 1;
    a->cancelled[task] = cancelled ? 1u : 0u;
    a->result[task] = cancelled ? 0u : result;
    a->blocked[task] = BLOCKED_NONE;
    uint32_t n = a->n_waiters[task];
    memcpy(woken, a->waiters[task], n * sizeof(uint32_t));
    a->n_waiters[task] = 0;
    for (uint32_t i = 0; i < n; i++)
        a->blocked[woken[i]] = BLOCKED_NONE; /* woken: no longer where a cancel may reach it */
    loop_unlock(a);
    for (uint32_t i = 0; i < n; i++)
        aio_wake(a, woken[i], task + 1);
}

/* Take a task off whatever it is registered on (the lock is held). A task is registered on at
 * most one thing: a timer, an event's waiters, or the waiters of another task. */
static void unregister_locked(aio_world *a, uint32_t task) {
    uint32_t *list = NULL, *count = NULL;
    switch (a->blocked[task]) {
    case BLOCKED_SLEEP:
        a->timer_live[task] = 0;
        break;
    case BLOCKED_EVENT:
        list = a->ev_waiters[a->blocked_arg[task]];
        count = &a->n_ev_waiters[a->blocked_arg[task]];
        break;
    case BLOCKED_TASK:
        list = a->waiters[a->blocked_arg[task]];
        count = &a->n_waiters[a->blocked_arg[task]];
        break;
    default:
        break;
    }
    if (list) {
        uint32_t keep = 0;
        for (uint32_t i = 0; i < *count; i++)
            if (list[i] != task)
                list[keep++] = list[i];
        *count = keep;
    }
    a->blocked[task] = BLOCKED_NONE;
}

/* The suspension of an op that registered: re-check for a raise that landed before the park
 * (then the registration is taken back and the run takes its poll at once), else smp_park. The
 * park is the last act on the run cell. A park answers PARK or SELFWAKE when the run really
 * slept; AGAIN means a token was waiting and the run is still RUNNING. */
static int aio_block(aio_world *a, smp_world *w, smp_run_payload *run, uint32_t carrier) {
    run->acc_xor = 1;
    if (atomic_load_explicit(&run->mp_raise_slot, memory_order_acquire) != 0) {
        loop_lock(a);
        unregister_locked(a, run->run_index);
        loop_unlock(a);
        run->acc_xor = 0;
        return SMP_STEP_AGAIN; /* the next step's poll finds the raise */
    }
    int answer = smp_park(w, run, carrier);
    if (answer != SMP_STEP_AGAIN)
        atomic_fetch_add_explicit(&a->parks, 1, memory_order_relaxed);
    return answer;
}

/* Task.cancel: forwarded down the chain of tasks it awaits (as uasyncio forwards it), the
 * registration of the deepest one removed under the lock — so exactly one party wakes it — then a
 * raise deposit into the run's own slot and, when it was blocked, a wake. A target that is not
 * blocked (READY, RUNNING) finds the deposit at its next poll. */
static void aio_cancel(aio_world *a, uint32_t target, uint32_t canceller) {
    loop_lock(a);
    if (a->done[target]) {
        loop_unlock(a);
        return; /* cancel of a finished task answers False and does nothing */
    }
    uint32_t victim = target;
    for (uint32_t hops = 0; a->blocked[victim] == BLOCKED_TASK && hops < a->program->n_tasks;
         hops++)
        victim = a->blocked_arg[victim];
    int was_blocked = a->blocked[victim] != BLOCKED_NONE;
    unregister_locked(a, victim);
    /* The deposit is made INSIDE the lock: a victim that registers after this point sees the
     * pending raise when it re-checks before it parks (aio_block), and one that registered
     * before it was found above — so no cancel is ever left on a task that sleeps. */
    smp_run_payload *run = smp_run_resolve(&a->w, a->runs[victim]);
    if (run) {
        smp_mp_schedule_raise(&run->mp_raise_slot, 1);
        rxf_smp_arena_release(&a->arena, a->runs[victim]);
    }
    loop_unlock(a);
    atomic_fetch_add_explicit(&a->cancels, 1, memory_order_relaxed);
    if (was_blocked)
        aio_wake(a, victim, canceller + 1);
}

/* ---- the interpreter of the task ops: one step is one segment, up to the next suspension ---- */

static int aio_step(void *ctx, smp_world *w, smp_run_payload *run, uint32_t carrier) {
    aio_world *a = (aio_world *)ctx;
    const uint32_t t = run->run_index;
    const uint32_t n_ops = a->program->n_ops[t];

    /* The safe point (S11): mp_handle_pending is the poll at the head of every step. A pending
     * raise is the CancelledError and ends the task, wherever its pc was. */
    uint64_t raise = 0;
    uint32_t sched = 0;
    atomic_fetch_add_explicit(&a->raise_polls, 1, memory_order_relaxed);
    if (smp_mp_handle_pending(&run->mp_raise_slot, &run->mp_sched_flag, &raise, &sched) &
        SMP_MP_RAISE) {
        aio_finish(a, t, 1, 0);
        return SMP_STEP_DONE;
    }

    for (;;) {
        if (run->pc >= n_ops) {
            aio_finish(a, t, 0, (uint32_t)run->acc_sum);
            return SMP_STEP_DONE;
        }
        const aio_op op = a->program->ops[t][run->pc];
        const int resumed = run->acc_xor == 1; /* the phase: this op registered and parked */
        if (resumed) {
            /* A wake is a hint, the condition is data (S4): the token a successful wake leaves
             * set makes the NEXT park of the run return at once, so a resume may be spurious.
             * The waker clears the registration under the lock before it wakes, so a run that
             * is still registered has not been served and goes back to sleep. */
            loop_lock(a);
            int served = a->blocked[t] == BLOCKED_NONE;
            loop_unlock(a);
            if (!served) {
                atomic_fetch_add_explicit(&a->spurious, 1, memory_order_relaxed);
                return aio_block(a, w, run, carrier);
            }
        }
        switch (op.kind) {
        case AIO_OP_MARK:
            run->acc_sum = fold((uint32_t)run->acc_sum, op.arg);
            break;
        case AIO_OP_EMIT: {
            uint32_t at = atomic_fetch_add_explicit(&a->trace_n, 1, memory_order_relaxed);
            if (at < AIO_MAX_TRACE) {
                a->trace_task[at] = t;
                a->trace_value[at] = op.arg;
            }
            break;
        }
        case AIO_OP_NOW:
            loop_lock(a);
            run->acc_sum = fold((uint32_t)run->acc_sum, (uint32_t)a->vnow);
            loop_unlock(a);
            break;
        case AIO_OP_SPAWN:
            aio_spawn(a, op.arg);
            break;
        case AIO_OP_SET: {
            uint32_t woken[AIO_MAX_TASKS];
            loop_lock(a);
            a->ev_set[op.arg] = 1;
            uint32_t n = a->n_ev_waiters[op.arg];
            memcpy(woken, a->ev_waiters[op.arg], n * sizeof(uint32_t));
            for (uint32_t i = 0; i < n; i++)
                a->blocked[woken[i]] = BLOCKED_NONE;
            a->n_ev_waiters[op.arg] = 0;
            loop_unlock(a);
            for (uint32_t i = 0; i < n; i++)
                aio_wake(a, woken[i], t + 1);
            break;
        }
        case AIO_OP_CLEAR:
            loop_lock(a);
            a->ev_set[op.arg] = 0;
            loop_unlock(a);
            break;
        case AIO_OP_CANCEL:
            aio_cancel(a, op.arg, t);
            break;
        case AIO_OP_SLEEP:
            if (op.arg == 0) { /* a yield: back into the ring, call_soon of oneself */
                run->pc++;
                return SMP_STEP_AGAIN;
            }
            if (!resumed) {
                loop_lock(a);
                a->timer_live[t] = 1;
                a->timer_due[t] = a->vnow + op.arg;
                a->timer_seq[t] = a->next_seq++;
                a->blocked[t] = BLOCKED_SLEEP;
                loop_unlock(a);
                return aio_block(a, w, run, carrier); /* the last act on the run cell */
            }
            run->acc_sum = fold((uint32_t)run->acc_sum, op.arg);
            run->acc_xor = 0;
            break;
        case AIO_OP_WAIT:
            if (!resumed) {
                loop_lock(a);
                if (!a->ev_set[op.arg]) {
                    a->ev_waiters[op.arg][a->n_ev_waiters[op.arg]++] = t;
                    a->blocked[t] = BLOCKED_EVENT;
                    a->blocked_arg[t] = op.arg;
                    loop_unlock(a);
                    return aio_block(a, w, run, carrier);
                }
                loop_unlock(a);
            }
            run->acc_sum = fold((uint32_t)run->acc_sum, 1);
            run->acc_xor = 0;
            break;
        case AIO_OP_JOIN:
            if (!resumed) {
                loop_lock(a);
                if (!a->done[op.arg]) {
                    a->waiters[op.arg][a->n_waiters[op.arg]++] = t;
                    a->blocked[t] = BLOCKED_TASK;
                    a->blocked_arg[t] = op.arg;
                    loop_unlock(a);
                    return aio_block(a, w, run, carrier);
                }
                loop_unlock(a);
            }
            /* the awaited task is done by now (its end is what woke this run) */
            loop_lock(a);
            run->acc_sum = fold((uint32_t)run->acc_sum,
                                a->cancelled[op.arg] ? AIO_CANCEL_WORD : a->result[op.arg]);
            loop_unlock(a);
            run->acc_xor = 0;
            break;
        default:
            break;
        }
        run->pc++;
    }
}

/* ---- the edge: the timer (S9) and the quiescence it waits for ---- */

enum { SCAN_BUSY = 0, SCAN_QUIET = 1, SCAN_MAIN_DONE = 2 };

/* Quiescent: no run READY or RUNNING (a wake in flight leaves its target READY until it is
 * popped; a spawn leaves it READY), and no wake or spawn began during the scan (the epoch). */
static int aio_scan(aio_world *a) {
    atomic_fetch_add_explicit(&a->scans, 1, memory_order_relaxed);
    uint64_t before = atomic_load_explicit(&a->epoch, memory_order_acquire);
    for (uint32_t i = 0; i < a->program->n_tasks; i++) {
        smp_run_payload *run = (smp_run_payload *)rxf_smp_arena_resolve(&a->arena, a->runs[i]);
        if (!run)
            return SCAN_BUSY; /* the compactor holds the cell: look again */
        uint32_t state = atomic_load_explicit(&run->state, memory_order_acquire);
        rxf_smp_arena_release(&a->arena, a->runs[i]);
        if (i == 0 && state == RXF_SMP_STATE_TERMINAL)
            return SCAN_MAIN_DONE;
        if (state == RXF_SMP_STATE_READY || state == RXF_SMP_STATE_RUNNING)
            return SCAN_BUSY;
    }
    return atomic_load_explicit(&a->epoch, memory_order_acquire) == before ? SCAN_QUIET : SCAN_BUSY;
}

/* The edge timer fires: the clock jumps to the earliest deadline and every timer due then wakes
 * its run, in the order the sleeps were made (what uasyncio's queue does with equal keys).
 * 0 when no timer is armed. */
static int aio_fire(aio_world *a) {
    uint32_t order[AIO_MAX_TASKS], n = 0;
    loop_lock(a);
    int have = 0;
    uint64_t due = 0;
    for (uint32_t t = 0; t < a->program->n_tasks; t++)
        if (a->timer_live[t] && (!have || a->timer_due[t] < due)) {
            due = a->timer_due[t];
            have = 1;
        }
    if (!have) {
        loop_unlock(a);
        return 0;
    }
    a->vnow = due;
    for (;;) {
        int pick = -1;
        for (uint32_t t = 0; t < a->program->n_tasks; t++)
            if (a->timer_live[t] && a->timer_due[t] == due &&
                (pick < 0 || a->timer_seq[t] < a->timer_seq[pick]))
                pick = (int)t;
        if (pick < 0)
            break;
        a->timer_live[pick] = 0;
        a->blocked[pick] = BLOCKED_NONE;
        order[n++] = (uint32_t)pick;
    }
    loop_unlock(a);
    for (uint32_t i = 0; i < n; i++)
        aio_wake(a, order[i], 0xFFFF);
    atomic_fetch_add_explicit(&a->timer_fires, 1, memory_order_relaxed);
    return 1;
}

/* ---- the carriers and the compactor ---- */

typedef struct {
    aio_world *a;
    uint32_t index;
} carrier_arg;

static void *carrier_main(void *p) {
    carrier_arg *arg = (carrier_arg *)p;
    smp_dispatch_loop(&arg->a->w, arg->index);
    return NULL;
}

/* The sliding pass (S8) under live tasks: gaps are freed in the middle of the cell stream and
 * every later cell — task run cells, the ring — slides down while the carriers run. */
static void *compactor_main(void *p) {
    aio_world *a = (aio_world *)p;
    for (uint32_t round = 0; round < 256 && !atomic_load_explicit(&a->w.stop, memory_order_relaxed);
         round++) {
        uint32_t used = atomic_load_explicit(&a->churn_used, memory_order_relaxed);
        if (used < a->churn_count) {
            rxf_smp_arena_free(&a->arena, a->churn[used]);
            atomic_store_explicit(&a->churn_used, used + 1, memory_order_relaxed);
        }
        rxf_smp_compact_stats st;
        memset(&st, 0, sizeof(st));
        rxf_smp_compact_move(&a->arena, &st);
        rxf_smp_compact_finish(&a->arena);
        atomic_fetch_add_explicit(&a->moved_cells, st.moved_cells, memory_order_relaxed);
        atomic_fetch_add_explicit(&a->compact_passes, 1, memory_order_relaxed);
        sleep_ns(30 * 1000);
    }
    return NULL;
}

int aio_run(const aio_program *program, uint32_t n_carriers, uint32_t flags, aio_report *report) {
    if (n_carriers < 1)
        n_carriers = 1;
    if (n_carriers > RXF_SMP_MAX_CARRIERS)
        n_carriers = RXF_SMP_MAX_CARRIERS;
    aio_world *a = (aio_world *)calloc(1, sizeof(*a));
    if (!a)
        return 2;
    memset(report, 0, sizeof(*report));
    a->program = program;
    a->flags = flags;
    pthread_mutex_init(&a->edge_lock, NULL);
    pthread_cond_init(&a->edge_cond, NULL);
    a->edge_work = 1;

    rxf_smp_arena_create(&a->arena, 2u * 1024 * 1024, program->n_tasks * 4 + 64);
    smp_world_init(&a->w, &a->arena, program->n_tasks, aio_step, a);
    a->w.wait = edge_wait;
    a->w.notify = edge_notify;
    a->w.edge_ctx = a;

    for (uint32_t t = 0; t < program->n_tasks; t++) {
        rxf_smp_handle h =
            rxf_smp_arena_alloc(&a->arena, RXF_SMP_CELL_RUN, sizeof(smp_run_payload), 0);
        smp_run_payload *run = (smp_run_payload *)rxf_smp_arena_resolve(&a->arena, h);
        memset(run, 0, sizeof(*run));
        run->self = h;
        run->run_index = t;
        run->acc_sum = AIO_FOLD_START;
        run->checksum = smp_run_checksum(run);
        rxf_smp_arena_release(&a->arena, h);
        a->runs[t] = h;
        a->w.runs[t] = h;
        /* a churn cell after each run cell: the gaps the sliding pass closes */
        if ((flags & AIO_FLAG_COMPACT) && a->churn_count < AIO_MAX_TASKS) {
            rxf_smp_handle gap = rxf_smp_arena_alloc(&a->arena, RXF_SMP_CELL_SCRATCH, 24, 0);
            if (gap != RXF_SMP_HANDLE_NONE)
                a->churn[a->churn_count++] = gap;
        }
    }

    pthread_t carriers[RXF_SMP_MAX_CARRIERS], compactor;
    carrier_arg args[RXF_SMP_MAX_CARRIERS];
    aio_spawn(a, 0); /* run_until_complete(main): the main task is the first call_soon */
    for (uint32_t i = 0; i < n_carriers; i++) {
        args[i].a = a;
        args[i].index = i;
        pthread_create(&carriers[i], NULL, carrier_main, &args[i]);
    }
    int compacting = (flags & AIO_FLAG_COMPACT) != 0;
    if (compacting)
        pthread_create(&compactor, NULL, compactor_main, a);

    /* The edge loop: wait for quiescence, then fire the timer; the main task terminal ends the
     * loop at once (run_until_complete returns); a quiescent world with no timer is a deadlock
     * and is reported, never waited on. */
    struct timespec start, now;
    clock_gettime(CLOCK_MONOTONIC, &start);
    for (;;) {
        int scan = aio_scan(a);
        if (scan == SCAN_MAIN_DONE)
            break;
        if (scan == SCAN_QUIET) {
            if (!aio_fire(a)) {
                report->deadlock = 1;
                break;
            }
            continue;
        }
        sleep_ns(20 * 1000);
        clock_gettime(CLOCK_MONOTONIC, &now);
        if (now.tv_sec - start.tv_sec > 30) {
            report->deadlock = 1; /* the deadline: a stall is a report, not a hang */
            break;
        }
    }
    atomic_store_explicit(&a->w.stop, 1, memory_order_release);
    edge_notify(a);
    for (uint32_t i = 0; i < n_carriers; i++)
        pthread_join(carriers[i], NULL);
    if (compacting)
        pthread_join(compactor, NULL);

    for (uint32_t t = 0; t < program->n_tasks; t++) {
        report->state[t] = !a->done[t]       ? AIO_STATE_PENDING
                           : a->cancelled[t] ? AIO_STATE_CANCELLED
                                             : AIO_STATE_DONE;
        report->result[t] = report->state[t] == AIO_STATE_DONE ? a->result[t] : 0;
    }
    uint32_t n = atomic_load(&a->trace_n);
    report->trace_len = n < AIO_MAX_TRACE ? n : AIO_MAX_TRACE;
    memcpy(report->trace_task, a->trace_task, report->trace_len * sizeof(uint32_t));
    memcpy(report->trace_value, a->trace_value, report->trace_len * sizeof(uint32_t));
    report->pops = atomic_load(&a->w.pops);
    report->parks = atomic_load(&a->parks);
    report->wakes = atomic_load(&a->wakes);
    report->timer_fires = atomic_load(&a->timer_fires);
    report->cancels = atomic_load(&a->cancels);
    report->raise_polls = atomic_load(&a->raise_polls);
    report->cross_resumes = atomic_load(&a->w.cross_resumes);
    report->state_refused = atomic_load(&a->w.state_refused);
    report->checksum_fails = atomic_load(&a->w.checksum_fails);
    report->refused_spawns = atomic_load(&a->refused_spawns);
    report->quiescent_scans = atomic_load(&a->scans);
    report->clock_end = a->vnow;
    smp_ring_payload *ring = (smp_ring_payload *)rxf_smp_arena_resolve(&a->arena, a->w.ring);
    if (ring) {
        report->ring_pushed = ring->pushed;
        report->ring_popped = ring->popped;
        rxf_smp_arena_release(&a->arena, a->w.ring);
    }
    for (uint32_t i = 0; i < n_carriers; i++)
        if (atomic_load(&a->w.carrier_pops[i]) > 0)
            report->carriers_stepping++;
    report->spurious_resumes = atomic_load(&a->spurious);
    report->compact_passes = atomic_load(&a->compact_passes);
    report->moved_cells = atomic_load(&a->moved_cells);

    smp_world_deinit(&a->w);
    rxf_smp_arena_destroy(&a->arena);
    pthread_mutex_destroy(&a->edge_lock);
    pthread_cond_destroy(&a->edge_cond);
    free(a);
    return 0;
}
