#ifndef RXF_SMP_MPMODEL_H
#define RXF_SMP_MPMODEL_H

/*
 * A model of the MicroPython seam (docs/20, S11) — NOT MicroPython, and never a second
 * implementation of anything in the native trees. It is the standalone C model the proof runs so
 * a witness program can play the shape of a MicroPython VM against the run-cell machine of
 * smp_sched.c. The real seam is lowered by the import lane (the needs list is in docs/20, S11);
 * this file exists so the seam's semantics are written down once and proven on the host.
 *
 * What it models is two words per VM and one poll:
 *
 *   - the pending-flag word, in the shape of mp_pending_exception: ONE slot. A second raise
 *     before the poll is refused and the caller decides, exactly as mp_sched_schedule answers
 *     false when its queue is full;
 *   - the scheduler's pending flag: a level bit that coalesces (two deposits before a poll are
 *     one callback run — the shape of a one-deep sched queue);
 *   - mp_handle_pending, the poll: one exchange of each word, raise first, then the sched. The
 *     poll is the safe point of the run cell (docs/20, S11): a deposit that lands during the
 *     poll is found at the next one — never lost, never delivered twice.
 */

#include <stdatomic.h>
#include <stdint.h>

#define SMP_MP_NONE 0u
#define SMP_MP_RAISE 1u /* the poll found a pending raise (mp_pending_exception) */
#define SMP_MP_SCHED 2u /* the poll found a scheduled callback (mp_sched_run_pending) */

/* The deposit of a raise (an ISR, or any other run): one CAS that takes the empty slot. Answers
 * 1 when the raise landed (the caller counts it), 0 when a raise was already pending (the Refuse
 * path: the caller decides what to do, as the caller of mp_sched_schedule does). */
uint32_t smp_mp_schedule_raise(_Atomic(uint64_t) *raise_slot, uint64_t payload);

/* The deposit of a scheduled callback: a level bit that coalesces. */
void smp_mp_schedule_sched(_Atomic(uint32_t) *sched_flag);

/* The poll (mp_handle_pending): atomically consume both words and answer what was pending. The
 * raise payload is reported through *raise_payload, the sched through *sched_seen (0 or 1); the
 * answer is the SMP_MP_* bitset, SMP_MP_NONE when nothing was pending. */
uint32_t smp_mp_handle_pending(_Atomic(uint64_t) *raise_slot, _Atomic(uint32_t) *sched_flag,
                               uint64_t *raise_payload, uint32_t *sched_seen);

#endif
