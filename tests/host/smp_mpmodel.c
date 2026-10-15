/*
 * The model of the MicroPython seam (smp_mpmodel.h; docs/20, S11). Not MicroPython: two words of
 * the pending flag and the poll that consumes them, so the proof can pin the seam's semantics —
 * a single-slot raise with a documented refusal, a coalescing sched bit, and a poll that never
 * loses an arrival — against the run-cell machine of smp_sched.c.
 */
#include "smp_mpmodel.h"

uint32_t smp_mp_schedule_raise(_Atomic(uint64_t) *raise_slot, uint64_t payload) {
    /* One slot, taken by CAS; the payload rides in the slot word itself (payload plus one; the
     * top bit is masked so the word is never the empty encoding). The winner writes nothing
     * else, so the deposit needs no second synchronization with the poll. */
    uint64_t word = (payload & 0x7FFFFFFFFFFFFFFFULL) + 1;
    uint64_t empty = 0;
    return atomic_compare_exchange_strong_explicit(raise_slot, &empty, word, memory_order_acq_rel,
                                                   memory_order_acquire)
               ? 1u
               : 0u;
}

void smp_mp_schedule_sched(_Atomic(uint32_t) *sched_flag) {
    atomic_store_explicit(sched_flag, 1, memory_order_release);
}

uint32_t smp_mp_handle_pending(_Atomic(uint64_t) *raise_slot, _Atomic(uint32_t) *sched_flag,
                               uint64_t *raise_payload, uint32_t *sched_seen) {
    /* The poll: raise first, then the sched, both consumed by one exchange. A deposit that lands
     * during either exchange is left for the next poll — never lost, never twice delivered. */
    uint64_t raise = atomic_exchange_explicit(raise_slot, 0, memory_order_acq_rel);
    uint32_t sched = atomic_exchange_explicit(sched_flag, 0, memory_order_acq_rel);
    uint32_t seen = SMP_MP_NONE;
    if (raise) {
        seen |= SMP_MP_RAISE;
        *raise_payload = raise - 1;
    }
    if (sched) {
        seen |= SMP_MP_SCHED;
        *sched_seen = 1;
    } else {
        *sched_seen = 0;
    }
    return seen;
}
