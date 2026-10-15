# Suspension is a data property — the execution policy

A suspended run is a cell in state `PARKED`. That is the whole policy. No thread, stack, register,
continuation object, or future belongs to a run; nothing of a run names the thing that executes it.
What suspends is data, what resumes is any carrier that reads that data, and what proves the two
views consistent is the same machinery the image already has: one arena, cells that move, handles
that name them, and claims that make an address valid for exactly one window (docs/03, docs/04).

This document is the execution half of that rule. It specifies the run-cell state machine (S1, S2),
the ready-ring protocol (S3), park and wake on channel frames (S4), ownership and the handoff (S5),
the programs a run cell carries (S6), the handle discipline and the refusal of stale addresses
(S7), concurrent delta and compaction under live runs (S8), the edge shim that projects all of this
onto pthreads, asyncio, or Metal (S9), the determinism the whole design buys (S10), and the
MicroPython seam — where the interpreter may suspend, and which of its words are cells (S11). The
proof
is C2 and C3: `tests/host/smp_*.c` (an N-carrier harness on a real arena) and `tests/test_smp.py`, run
under ThreadSanitizer and AddressSanitizer/UBSan.

The core of the policy is one file, `tests/host/smp_sched.c`: the state machine, the ring, and the
park/wake protocol. It knows nothing of pthreads. Everything platform-shaped lives behind three
callbacks (S9), and everything memory-shaped lives in `tests/host/smp_arena.c`, which knows nothing
of runs.

## S1. The run-cell state machine

The run cell is an arena cell whose payload is `smp_run_payload`. Five states, each transition one
CAS on the `state` word:

| from | to | who | when |
|---|---|---|---|
| `FREE` | `READY` | whoever enqueues the run | the initial enqueue (S3) |
| `READY` | `RUNNING` | the carrier that pops it | one CAS at the pop (S3); a failure is a double enqueue and counts `state_refused` |
| `RUNNING` | `PARKED` | the owning carrier | the program waits (S4) |
| `RUNNING` | `READY` | the owning carrier | the program yields (S3) |
| `RUNNING` | `TERMINAL` | the owning carrier | the program ends |
| `PARKED` | `READY` | whoever satisfies the wait condition | the wake (S4) |
| `READY` | `PARKED` | the waker of the line above, and only before it has enqueued the run | the wake retraction (S4) |

A transition not in this table fails its CAS and changes nothing; the refusals are counted, not
retried inside the machine. The state names are `FREE = 0`, `READY = 1`, `RUNNING = 2`,
`PARKED = 3`, `TERMINAL = 4`. `TERMINAL` is absorbing. The `READY to PARKED` line is the only
retreat in the machine and exists because a wake is committed by the enqueue, not by the CAS (S4):
until the run is in the ring the waker may take its wake back (the ring refused it, or the proof
deferred the resume). A run that is in the ring is never taken back.

The payload has three regions, and the boundary between them is the law:

```c
typedef struct {
    /* concurrent words: atomics only */
    _Atomic(uint32_t) state;     /* the machine above */
    _Atomic(uint32_t) token;     /* the wake token (S4) */
    _Atomic(uint32_t) waker_run; /* who woke this run last (evidence, not protocol) */
    _Atomic(uint32_t) midflight; /* bumped when the compactor moves this run while it runs */
    _Atomic(uint64_t) mp_raise_slot; /* the seam's pending pair (S11): deposited by any waker, */
    _Atomic(uint32_t) mp_sched_flag; /* consumed at the run's own poll */
    /* handoff metadata and evidence */
    uint32_t run_index, kind;
    uint32_t park_carrier, deferred;
    uint32_t resume_carrier, last_carrier, resumes, migrate_refs, migrate_mask;
    /* program state: the owner's alone between two safe points */
    rxf_smp_handle self, data, channel, scratch, stale;
    uint64_t seed, pc, acc_sum, acc_xor, sent, rx_seen, data_offset, steps, target_steps;
    uint32_t ok, verify_bad, relocated, ring_refused, stale_refused;
    uint64_t mp_frame_sum, mp_frame_xor; /* the VM's frame checkpoint the nlr jump restores (S11) */
    uint32_t mp_block, mp_iter, mp_polls, mp_unwinds;
    uint32_t mp_raise_seen, mp_sched_seen, mp_events, mp_raise_events, mp_won, mp_refused;
    uint32_t mp_deposits;
    uint32_t guard1[2], guard2[2];
    uint64_t checksum;
} smp_run_payload;
```

- The `_Atomic` words are written concurrently (any carrier, the compactor) and nothing else may
  be written concurrently: the machine's four, and the seam's pending pair of S11.
- The handoff metadata is written by the owner or by the popper under the state machine and the
  ring lock (S5). It is evidence about the run, never authority over it.
- The program state — every field from `self` to `guard2` — is written by exactly one carrier
  between two safe points and is covered by the checksum. `checksum` is an FNV-1a over the program
  state (`&self` up to `checksum` itself), verified at the start of every step and rewritten at its
  end and before any requeue: the run cell is never handed back half-written (S5). A step that
  finds the checksum wrong counts `checksum_fails` and does not trust the state.

## S2. Suspension is a data property

Nothing in the run cell names a carrier, and no carrier owns a run while the run sleeps. `PARKED`
is not a relationship with a thread; it is a value in a word. Therefore:

1. Any carrier may resume any run; resumption is a read of the cell, not a signal to an owner.
2. A parked run outlives whatever parked it. Losing a carrier loses no runs.
3. Migration needs no protocol: the same run cell is stepped by carrier 3 and then by carrier 7,
   and the only trace is the evidence (`resume_carrier`, `migrate_mask`, S5).
4. A wake targets a run, never a carrier. The edge may also wake a run for liveness (S9), which is
   the same operation from the run's point of view.

The price is paid at safe points, and only there (S7): between two safe points the program state is
owned and its addresses are valid; across a safe point nothing but handles survives.

## S3. The ready-ring protocol

The ready-ring is itself a cell (`RXF_SMP_CELL_RING`), because everything is:

```c
typedef struct {
    _Atomic(uint32_t) lock;
    uint32_t head, tail, capacity;
    uint64_t pushed, popped, refused_stale, refused_full, wakes;
    rxf_smp_handle slots[1]; /* capacity of them; the cell holds the rest */
} smp_ring_payload;
```

`capacity` is a word of the cell like every other. The cell may move like any other (the proof
moves it and counts it in `WIT_OUT_MOVED_RING`); every use resolves the cell by handle, takes the
lock, works, releases (S7). A product may pin the ring cell while the world runs, which fixes its
source address and nothing else (docs/03).

**Enqueue (`push`, 0 pushed / 1 refused).** Under the lock: append the handle at `head`, advance.
If `head + 1 == tail` the ring is full: the push changes nothing except `refused_full`, and the
pusher retries (a wake whose push was refused is taken back, S4). A push never drops a run and
never overwrites a slot.

**Dequeue (`pop`, 0 popped / 1 empty).** Under the lock: take the handle at `tail`, clear the slot,
advance. Before handing it out, the handle is checked: a handle whose slot generation moved or
whose slot is free is stale — it names nothing, and in particular it never names the new cell of a
reused slot. The pop refuses it (`refused_stale`, `ring_stale_refused`), and goes on to the next
entry. This is the ABA refusal: a slot freed and handed out again cannot be resumed under its old
name, because a handle is `index | generation << 32` and the generation changes on every reuse.
Slot 0 is never handed out, so the all-zero handle is invalid everywhere.

**Taking a run.** After a successful pop the carrier makes the `READY to RUNNING` CAS. The CAS is
the second line of defense: a run that was enqueued twice is stepped once, refused once, and
counts `state_refused` for the loser. Order among runs is FIFO; the ring is the only queue. A run
is in the ring at most once at any instant (the wake's enqueue is the commit, S4).

## S4. Park and wake on channel frames

A wake is a token protocol so that no wake is ever lost and no run is enqueued twice:

- **wake(run).** Store the waker and the wake token, then attempt `PARKED to READY`. If the run
  was parked, the transition is ours and the enqueue is ours: push into the ring and ring the
  edge doorbell (answer 0). If the run was not parked, the token stays for its next park and the
  wake changes nothing else (answer 1: nothing is enqueued twice). If the ring refuses the push,
  the wake is taken back with the legal `READY to PARKED` retreat and the caller retries
  (answer 2). A waker may deliberately hold the wake back and let another carrier take the resume
  (answer 3, the proof knob; the deferral is bounded — after two retreats the waker enqueues the
  run itself, so progress never depends on the knob — and it costs the waker one pause, a yield
  of the carrier, never a sleep of the run).
- **park(run).** Consume the token first: if a wake arrived while the run ran, the run does not
  sleep at all (self-wake; the dispatcher requeues it after rewriting the checksum). Otherwise the
  run makes `RUNNING to PARKED` and sleeps. The park answers the dispatcher which of the three
  happened, and the answers are `SMP_STEP_PARK`, `SMP_STEP_SELFWAKE`, and `SMP_STEP_AGAIN`.

  The park is the step's own handoff (S5), so it seals the cell first: the step's closing duty,
  the step count and the checksum are final **before** the `RUNNING to PARKED` CAS, because a
  waker may requeue a `PARKED` run the moment that CAS lands and the next owner may step the cell
  at once. A seal after the transition leaves a run cell still being written by its old owner
  while its new owner verifies and steps it — a torn cell is never seen, but the check races the
  seal and reports one. The same rule shapes the dispatcher's own requeue at a yield: seal, then
  the state CAS, then the push. The state CAS (or the enqueue that follows it) is the last thing
  that happens to the cell; after it, the old owner writes no program word again.

Channel frames are how two runs wait on each other (the channel frame of docs/10, as a cell): a
sender fills a word in the frame and wakes the receiver; a receiver with nothing to read parks on
the frame. The frame is a cell like any other — the pair holds its handle, resolves it at the start
of the step, and releases it before any park. The frame's head/tail words are the release/acquire
edge that makes a word visible across carriers: the sender stores the word and releases `head`; the
receiver acquires `head`, consumes the word, releases `tail`. The channel pairs of the proof are
woken only by their partner runs; the edge shim never substitutes for a run-to-run wake (S9).

**Fairness.** FIFO within the ring; at most one enqueue per wake; a full ring refuses without
loss; a deferred resume is bounded; and a park whose partner never comes is an edge-liveness
question, answered by the shim's timer (S9), never by the core.

**Cost of a safe point.** A safe point is: release every held claim (one atomic subtract each),
rewrite the checksum (one FNV over the program state), and, if the step ends in a park, one CAS
plus at most one ring push and one doorbell. A wake is one store, one CAS, one push, one doorbell.
A resolve is one claim CAS and two relaxed loads. The compactor's wait for one cell is bounded by
the longest step of its current holder: a claim is never held across a safe point (S7), so the
compactor waits at most one step and the proof counts the wait (`claim_spins`, `claim_spins_max`).
No safe point requires anything of the edge.

## S5. Ownership and the handoff

Between the `READY to RUNNING` CAS and the next safe point, one carrier owns the program state of
the run and is the only writer of it. The handoff of ownership between carriers is the ready-ring:
the fields written by one carrier are read by the next with the ring lock and the state CAS in
between, and the checksum rewritten before the requeue is the shape of the handoff (S1). No
handshake, no ack, no per-run lock.

What concurrency touches is outside the program state: the four atomic words (S1), the ring cell
(S3), and the claim words of the arena (S7). Migration is observed, not performed: when a run is
resumed by a carrier other than the one that parked it, the proof counts a cross resume and records
the carriers that ever stepped the run (`migrate_mask`), and the differential (S10) shows the
result does not depend on any of it.

## S6. The programs a run cell carries

The proof's programs are deterministic functions of `(seed, run index, step index)` and never read
another run's data; the result state advances only on progress — a step that finds nothing to do
(an empty channel, a full frame, a refused resolve) consumes an attempt and no result. A product's
program is one interpreter step (`native/graph.c`) or one safe-point interval of compiled code,
under the same rules:

- **worker**: spinning steps with occasional parks from the step index alone — the migration
  traffic;
- **channel**: a pair over one channel frame — park, send, wake, cross resume (S4);
- **stale**: resolves a handle whose slot generation moved — it must be refused and must never
  resolve to the new cell of the slot (the ABA program of S3, S7);
- **mpvm**: the MicroPython shape (S11) — a pair where one run plays an interrupt source
  (deposits raises and scheduled callbacks into its partner's pending words and wakes it) and the
  other a VM run (frame iterations with the poll at every back edge, nlr-shaped unwinds, and
  blocking-call parks).

## S7. Handles, claims, and no raw address across a safe point

A payload address is valid for exactly one window: from a resolve to its release. The end of a
step is a safe point, and nothing is held across it.

- **resolve(handle)** claims the cell (the claim word of the slot) and returns the payload. While
  the compactor holds the cell (`CLAIM_MOVING`) a resolve is refused and returns nothing; the
  caller tries again at its next step. A handle whose generation moved or whose slot is free is
  refused (`handle_stale_refused`) and never rebinds to the new cell of a reused slot.
- **release(handle)** drops the claim. A claim is always released by its holder, and always before
  the safe point.
- **C_FORWARD tracking.** When a cell slides (S8), the compactor writes the cell's new offset into
  the old header before the copy; the word travels with the cell into its new header, and the old
  place carries the record until the pass finishes or until the geometry covers it. A reader at an
  old address consults the record; a reader with a handle does not need it. Mid-flight — the cell
  of a live run moved between two of its safe points — is legal and expected: the next step
  resolves the handle again and finds the new place (`relocated_steps`).
- **Refusal on violation.** There is no tracking of raw addresses and no repair for them: a raw
  address that outlives its window is not wrong-but-recoverable, it is refused. The old place is
  not authoritative after a move (`C_FORWARD` is a record, not a second binding); a stale handle
  resolves to nothing; a torn copy fails the copy checksum and the run checksum (S8); a wrong
  rebind fails the data guards and counts `verify_bad`; and a read of vacated memory hits the
  poison of S8 (`0xA5`). The policy for callers is one sentence: after a safe point, resolve
  again.

## S8. Concurrent delta and compaction

Runs execute while the arena is edited and compacted. The delta and the compaction pass are one
writer (the mutator thread of the proof, the image's journal in a product); the carriers are the
readers.

**The delta** writes through handles like everything else: resolve (claim), write the field, drop
the claim. The one field the proof's delta writes (`delta_stamp`) is one no run reads, and the
reads a run does are inside its own step window, so a delta is never observed half-applied. The
delta also grows the arena (a scratch cell) and frees cells in the middle of the stream (the churn
cells), so the compactor has real gaps to close under live runs.

**The pass** slides cells down in offset order, honoring pins:

1. claim the cell exclusively (`CLAIM_MOVING`); every reader is refused while it is held, and the
   claim is what makes the copy safe (the holder of a claim always gives it up at its next safe
   point, so the wait is bounded by one step — S4, S5);
2. a pinned cell keeps its source address: the pass leaves it and slides around it (a pin
   constrains the running source address, not the successor, docs/03);
3. a dead cell (kind 254) vanishes and its slot stays free;
4. an unpinned live cell slides down to the frontier: write `C_FORWARD` into the old header,
   copy the whole cell, publish the new offset in the slot, release the claim. The payload
   checksum is taken before the copy and after it; a copy that does not match is counted and never
   observed (`checksum_fails == 0` is a proof obligation);
5. when the pass ends, the vacated tail (after the new frontier) is filled with `0xA5` and
   ASAN-poisoned. Nothing poisons a live cell; a stale raw address reads poison and is caught.

**The C_FORWARD flip.** The forwarding word is written before the copy and travels with the cell
into its new header; at the old place it survives exactly as long as the geometry says — an
overlapping slide (a gap smaller than the cell) or a later slide into the old place consumes it.
The evidence accounts for every moved cell exactly: `forward_ok` (the record was read back at the
old place and equalled the new offset) plus `forward_lost` (the old place is another cell's bytes
now) equals `moved_cells`, pass for pass. A record that the geometry left in place and whose value
is wrong counts as a verification failure — there is no third case.

**Mid-flight observation.** The pass hands every moved cell to an observer, which classifies the
move while it happens: a run cell in `PARKED` moved (`parked_moved`), a run cell moved while its
run was between safe points (`midflight_moved` — live, in `READY`, owning nothing; a `RUNNING`
run holds its claim and the mover waits for it, so a move mid-step is excluded by construction,
which is the discipline of S5), data cells and the ring cell counted separately. The runs notice
as `relocated_steps`: a step whose data cell is at a new offset from the last step. The runs'
answer to all of it is the checksum at the start of every step and the guards of the data cells;
`checksum_fails == 0` and `verify_bad == 0` are the proof that the observation was not
decorative.

**Staging.** Deltas are staged, applied, and journaled as whole operations (docs/19): one writer,
whole payloads, and a claim window per write. There is no partially visible delta: the fields a
delta writes are fields no in-flight step reads, and every write is inside a claim.

## S9. The edge shim

The core's whole knowledge of concurrency is three callbacks:

```c
typedef void (*smp_wait_fn)(void *ctx);     /* how a carrier waits when the ring is empty */
typedef void (*smp_backoff_fn)(void *ctx);  /* how it steps aside during a deferral (S4) */
typedef void (*smp_notify_fn)(void *ctx);   /* the doorbell: the ring is not empty */
```

No pthread, asyncio, or Metal symbol appears in the core. The projections:

| edge | wait | backoff | notify |
|---|---|---|---|
| pthreads (the proof) | condvar + predicate, timed wait | sleep | broadcast |
| asyncio | await the work Future | `asyncio.sleep(0)` | set the Future |
| Metal | fence or event-ring wait | queue yield | ring doorbell / event |

The rule in every projection: the shim may wake a run but may never run it, may delay a carrier
but may never own a run. Timeout liveness is an edge concern (the proof's watchdog, a host timer,
wakes parked workers that have no partner and VM runs in blocking calls, S11); it uses the same
wake as any other waker and is
counted separately (`watchdog_wakes`), so the run-to-run wake of S4 is never polluted by the shim.
A parked run that the shim wakes goes through `PARKED to READY` and the ring like any other run;
the shim has no private path into the machine (S1).

## S10. Determinism and the differential

The result of a program is a function of the seed alone, whatever the interleaving: a step reads
and writes its own run cell and its own data cell and never the data of any other run, and the
result state advances only on progress (S6) — attempts are timing, and timing is evidence, never
the answer. What the interleaving decides is only who executes a step and when, and that is read
back as evidence (per-carrier step counts, park/resume pairs, moves observed under runs) and never
mixed into the result.

The differential is the shape of the proof: the same program at one carrier and at eight, with
compaction on and off, with deltas on and off, with the deferral knob on and off, produces
byte-identical result words (`digest`, `sum`, `xor` — words 0 to 2 of the evidence). Every other
word is evidence about the interleaving and is allowed to vary; the words that must not vary are
the ones that carry the answer.

## S11. The MicroPython seam (C3: no GIL)

MicroPython's own safe points are the run cell's safe points. The seam between the two machines is
small, and this section fixes it: the mapping of the interpreter's suspension points onto S1 to S9
(the mapping table), the words of the pending flag and the protocol around them, the ownership of
`mp_state_ctx` and friends as cells (which fields need no lock at all), the nlr rule, and the needs
list — what the MicroPython import must provide for the real integration. The needs list is
recorded here, not implemented. The model that pins the seam's semantics on the host is
`tests/host/smp_mpmodel.c` (a standalone C model of the two words and the poll, not MicroPython,
and not a second implementation of anything the import lane owns), and the witness program that
plays the shape is `WIT_PROGRAM_MPVM` in `tests/host/smp_witness.c`.

### The mapping table

| MicroPython | the run-cell machine |
|---|---|
| `mp_handle_pending()` — the pending-flag poll | the safe point itself: the last act of a step, where the claims are released and the checksum rewritten (S7). A bytecode run's step is the interval between two polls |
| the VM loop's periodic check (the back edge of the bytecode loop) | the step boundary: one VM-loop iteration is one dispatched step; the model runs four back-edge polls to one frame (`WIT_MP_BLOCK`) |
| `mp_pending_exception` — the single pending-raise slot | `mp_raise_slot`, one atomic word in the run cell's concurrent words (S1). Written by any waker, consumed at the run's own poll. A second raise before the poll is refused and the caller decides, as `mp_sched_schedule` answers false when its queue is full |
| `mp_sched_schedule` / `mp_sched_run_pending` | `mp_sched_flag`, one coalescing level bit of the run cell: two deposits before a poll are one callback run (the shape of a one-deep queue) |
| `mp_sched_keyboard_interrupt`, `mp_keyboard_interrupt`, an ISR at the hal | a deposit into the pending words plus a wake-token op on the run cell (S4) — never a signal into the run. Interrupts are data the poll finds |
| `nlr_push` / `nlr_pop` | the run's handler chain: per-run program state, checksummed. Legal within one step, never across a safe point |
| `nlr_jump` — the unwind | the restore of the run's frame checkpoint inside one step: the interpreter's heap frame chain (`mp_code_state_t`) is rewound and re-entered, and the retried frame computes the same bytes. No longjmp crosses a safe point (the `lower_nlr` soundness boundary: the frame is lowered, the unwinding machine is not) |
| `mp_raise_*` — the raise leaf | the Refuse leaf: the guarded call diverges and the run refuses (the importer's existing rule) |
| a blocking hal call (`mp_hal_delay_ms`, stdin read) | a park (S4) with a timed edge wake (S9): the watchdog timer is the hal's timer |
| `mp_state_thread` (`nlr_top`, `dict_locals`, `dict_globals`, the frame chain) | per-run program state (the table below): the interpreter reads its own run's VM state with no lock and no claim |
| `mp_state_ctx` — the root of the globals | named cells under root (the landed import model: one named cell per global, handles resolved at use) |
| `mp_state_mem` — the GC heap | the arena (docs/03): a Python heap object IS an arena cell. REPR A as decided: bit 0 set is a small int (no cell); otherwise `handle << 1`, resolved at use |
| the GIL | there is none. Ownership of the run cell is the only mutual exclusion (S5), and the concurrent words are the only cross-run writes |

### The pending protocol, exactly

- **The raise slot.** One atomic word per run: 0 is empty, otherwise the payload plus one (the top
  bit of the payload is masked so the word is never the empty encoding). A deposit is one CAS from
  empty and answers 1 when the raise landed, 0 when a raise was already pending — the Refuse path,
  with the caller deciding what to do with the refusal. The payload rides in the slot word itself,
  so a deposit writes nothing else and needs no second synchronization.
- **The sched bit.** One atomic level bit per run: set by a scheduler, coalescing. The poll
  consumes it and runs the callback inside the step.
- **The poll.** `mp_handle_pending` is one exchange of each word, raise first, then the sched,
  both acq_rel. Delivery is inside the step. A deposit that lands during either exchange is found
  at the next poll: never lost, never delivered twice. The proof pins this as an identity — every
  raise that won the slot is delivered at exactly one poll or is still pending when the run ended
  (`WIT_OUT_MP_RAISE_WON == WIT_OUT_MP_DELIVERED + WIT_OUT_MP_PENDING_END`).
- **The wake.** Every deposit is paired with a wake-token op on the target run (S4): a pending
  event wakes a VM out of a blocking call, and the token protocol keeps a deposit that lands just
  before a park from being lost. The wake of a `TERMINAL` run is refused by the state machine and
  enqueues nothing (S1).
- **Deposits at teardown.** A source may outlive its VM; a deposit into the slot of a run that
  will never poll again is the reentrant case the import must expect. The first deposit may win
  and the rest are refused — guaranteed, since nothing consumes the slot again — and the caller
  sees the refusal. The proof's exit burst exercises exactly this (`WIT_OUT_MP_RAISE_REFUSED`).

### The ownership of `mp_state_ctx` (which fields need no lock)

| field group | owner | rule |
|---|---|---|
| `mp_state_thread`: `nlr_top`, `dict_locals`, `dict_globals`, the frame chain | the run | program state (S1): the interpreter reads and writes its own run's VM state with no lock and no claim, and the checksum covers it |
| `mp_pending_exception` and its payload | the run (concurrent words) | one atomic slot, written by any waker, consumed at the poll (above) |
| `mp_state_vm.mp_kbd_exception` (the exception object) | shared, immutable | a REPR A immediate or a read-claimed cell; never written after init |
| `mp_state_vm.sched_state`, `sched_queue` | shared | named cells under claim discipline (the ready-ring's rule, S3, S7): resolve, work, release |
| `mp_state_mem` (the GC heap) | the arena | allocation under the allocator's discipline; objects are cells (REPR A); a cached object reference across a safe point is a handle, never an address |
| module registries, `sys.path`, qstr pools | shared, immutable after init | read claims only; modules are module cells under root |

The consequence in one line: between two safe points a run needs nothing but its own cell and the
claims it took inside the step, and the only words another run may touch are the concurrent words
(the wake token and the pending pair) — exactly the rule of S1.

### What the MicroPython import must provide (the needs list — recorded, not implemented)

1. **`mp_handle_pending` as a safe-point leaf.** Lowered to the safe point of the run cell: the
   poll becomes the end of a step — release the claims, rewrite the checksum, optionally park. A
   bytecode run's step is one VM-loop interval, between two back-edge polls.
2. **The pending-flag word as per-run cell words.** Split `mp_state_vm`'s pending exception and
   sched-pending state onto the run cell's concurrent words: the single-slot raise with its
   documented refusal, the sched flag with its coalescing. Nothing a second VM would write may
   stay global.
3. **`mp_sched_schedule` as a wake-token op.** The deposit is a CAS on the target run's slot (or
   a set of its sched bit) plus the wake of the target run cell; the false answer is the Refuse
   path and must reach the caller.
4. **Interrupts are deposits, never signals.** `mp_keyboard_interrupt` and friends write the
   pending word and wake; no signal handler may touch a run.
5. **nlr and runs.** `nlr_push`/`nlr_jump` stay legal within one safe-point interval (the
   importer's `lower_nlr` boundary). No longjmp may cross a safe point: a raise delivered at a
   poll unwinds the run's heap frame chain (`mp_code_state_t`) and the frame is re-entered from
   its checkpoint — the carrier's C stack is empty at every safe point (S7), and the run cell is
   never torn by an unwind (the checksum is rewritten before any requeue).
6. **REPR A handles.** Bit 0 set is a small int and needs no cell; otherwise `handle << 1`,
   resolved at use (S7). Every cached object reference across a safe point is a handle; a raw
   pointer may not outlive its step.
7. **Blocking hal calls as parks.** `mp_hal_delay_ms`, stdin reads and friends park the run (S4)
   with a timed edge wake (S9) instead of blocking the carrier.
8. **The memory area as the arena.** `mp_state_mem`'s heap is the arena (docs/03): an allocation
   is an arena alloc under the allocator's discipline, and collection moves cells (the sliding
   pass of S8) — anything caching an object across a collection must hold a handle.

### What the proof shows (C3)

The MPVM programs run the shape above on the machine of C2 — N carriers, a real arena, compaction
and deltas under live runs — and the differential covers them: the seam's result words are part of
words 0 to 2 and are byte-identical at every carrier count and flag set (S10). The seam's
accounting identities hold in every configuration:

- `won + refused == deposits` (every deposit is landed or refused, exactly once);
- `won == delivered + pending_end` (a raise is delivered at exactly one poll, or never);
- `unwinds == delivered` (every delivery lands after work and rewinds its frame; the retry
  computes the same bytes, so the answer never depends on where the raise landed).

The pending pair is concurrent words of the run cell and the whole traffic runs under
ThreadSanitizer; and what the interpreter side sees is exactly the ownership rule above: a VM
run's poll touches only its own cell.

## The proof (C2, C3)

`tests/host/` builds one harness from five files: `smp_arena.c` (handles, claims, sliding
compaction), `smp_sched.c` (S1 to S5), `smp_mpmodel.c` (the model of the MicroPython seam, S11),
`smp_witness.c` (S6 to S9 and S11: the programs, the mutator, the watchdog, the
evidence), and `smp_prove.c` (one JSON door). `tests/test_smp.py` drives it:

- the differential of S10 across carrier counts 1, 2, 4, 8 and flag sets 0 to 15 (compaction,
  delta, deferral), byte-identical result words every time;
- hundreds of runs with randomized parks and wakes: all carriers step (`carrier_steps`), runs
  migrate (`moved_runs`, `cross_resumes`, `pair_park != pair_resume`), the ABA handle is refused
  and its fresh sibling resolves (`ABA_OK`);
- concurrent delta and compaction with sliding (`C_FORWARD`), the accounting identity
  `forward_ok + forward_lost == moved_cells`, relocated steps under live runs, the poison tail
  intact (`poison_ok`), and byte-identical results against the single-carrier reference;
- the MicroPython seam of S11 (the MPVM programs over the model of `smp_mpmodel.c`): the
  `mp_handle_pending` poll is the safe point, pending raises and callbacks are wake-token ops
  between runs with the Refuse path exercised at teardown, and the identities
  `won + refused == deposits`, `won == delivered + pending_end`, `unwinds == delivered` hold under
  full churn;
- the same binary under AddressSanitizer/UBSan and under ThreadSanitizer, clean both ways.

The build is `-std=c11 -Wall -Wextra -Werror -Wimplicit-function-declaration`; the evidence words
are the `WIT_OUT_*` of `smp_witness.h`.
