---

## 10. `graph_run` -> `native/graph.rxfs` — the translation

`native/graph.c` (771 lines, deleted in the cleanup of docs/31 §6 step 3) was a
64-bit word interpreter. Every helper and the
main loop translate 1:1 into `.rxfs`; **exactly one** spot is muscle: `call()`
dispatches 16 native function-pointer shapes (`rxf_call_qo` …
`rxf_call_hqhqpo`). That goes away — the uniform object ABI
`(context, args, argc, out) -> status` replaces every shape, and leaves that are
still C are reached through **one** dispatcher.

### 10.1 The muscle dispatcher (the only new native piece)

```c
/* rxf_muscle_call(leaf_id, args, argc, out) -> status: the one shape. Every leaf that is not
 * .rxfs yet is reached through this -- the shim reads the uniform words and does the shaped call
 * internally. When a leaf moves to .rxfs, its table entry stops naming a muscle id. */
```

* `find_leaf` stays a table walk: `(id, address)` pairs in the context. An
  address is either a muscle-id range (dispatcher) or the entry handle of an
  `.rxfs` object (direct call).
* The 16 shapes exist only **inside** the dispatcher (transitional muscle) and
  shrink as leaves move to `.rxfs`.

### 10.2 C -> `.rxfs` translation table (helpers become macros or loops)

| C (`graph.c`) | `.rxfs` | Notes |
|---|---|---|
| `copy_bytes/zero_bytes/read_bytes/write_bytes` | byte loops | inline in macros |
| `have(g, at, n)` | `m_have` | `at <= count && n <= count - at` |
| `in_frame(g, off, w)` | `m_in_frame` | against `frame_bytes` |
| `locate(g, op, need, &where)` | `m_locate` | slot = frame + op[1], literal = &op[1], object = `entry(c, op[1])->payload`; `need > size` refuses OUT_OF_RANGE |
| `load` / `pointer` / `store` / `move` | `m_load` / `m_pointer` / `m_store` / `m_move` | as in C |
| `take(g, at, &next)` / `edge_words` | `m_take` | two-pass staging (`RXF_GRAPH_COPY` = 256) |
| `find_leaf` | `m_find_leaf` | table in the context |
| `answer(g, result, bytes, width, &next)` | `m_answer` | out word + status slot of the caller; `depth--`, frame links back — reloads `code`/`count` from the *caller* frame |
| `call_program(g, at, &next)` | `m_call_program` | frame carved from the cell: `base = frames[depth].base + frames[depth].frame_bytes`, budget `RXF_GRAPH_CELL_BYTES` = 8192 |
| `rebind(g)` | `m_rebind` | after every safe point: rebind all frames (generation/live/alignment) |
| `safe_point(g)` | `m_safe_point` | channel area (capability 1), `RXF_AREA_*` (RX_HEAD 120, RX_TAIL 124, BREAK_REQUEST 176, BREAK_ACK 180, IN_RUN 256); the pump is leaf 29 |
| `bind(g, program)` | `m_bind` | entry: `frames[0]`, base 0, `RXF_GRAPH_MAGIC` = `0x31475852` |
| `rxf_graph_run` (the loop) | `object graph.run` in `native/graph.rxfs` | 12 opcodes: CALL=1, MOVE=2, MAP=3, FIELD=4, CONSTRUCT=5, LESS=6, INCREMENT=7, GOTO=8, BRANCH=9, SWITCH=10, RETURN=11, REFUSE=12 |

* Operands: `[mode, x, width, …]`, `RXF_OPERAND_SLOT=0 / LITERAL=1 / OBJECT=2`;
  `RXF_GRAPH_NONE = ~0`.
* Edge copy = 5 words (`RXF_EDGE_COPY_WORDS`), edge header 2 words, call header
  6 words (`[op, leaf|0, count, out_slot, out_width, status_slot]`), program
  header 5 (`RXF_SLOT_WORDS`), program = MAGIC, frame_bytes, parameters, words,
  entry_pc, then (slot, width) pairs.
* Cell layout = exactly the C layout: frame headers (11 words) live **in the
  cell**, the `frames[]` links in the interpreter frame — memory parity is part
  of the bit parity.
* `refuse expr;` (Seed extension): `OP_REFUSE` returns a dynamic status.

### 10.3 Differential oracle

`tests/graph_world.py` builds the cases (Call/Move/Map/Field/Construct/Less/
Increment/Goto/Branch/Switch/Return/Refuse + refused runs). Acceptance: same
words in, same words and same status out, same memory state, on x86_64,
aarch64 and wasm32 — `tests/test_graph_build.py` (304 cases × 3 seats,
bit-exact vs `tests/graph_reference.py`).

---

## 11. `compile_program` -> `native/compile.rxfs` — the translation

The former `native/compile.c` (2026 lines) was the in-image compiler: `rxf_compile_program(context, request)`
turns a program into machine code for the machine it runs on. The object-ABI spelling is
`!pp`: `compile_program(c, request) -> status` — **every exit is a `refuse`** (the value is not
the run's; `out_size` and `at` are written through the request struct at `+48` and `+56`,
exactly as the C writes them through `rxf_compile_request`). The request is 8 u64 words:
program, program_size, out, out_capacity, work, work_capacity, out_size, at — read with
`load64(request + k * 8)`, written with `store64`.

### 11.1 The state of `rxf_cc` is fn variables

The writer state (`rxf_cc`: code/count/buf/cap/size/frame/alloc/failed/write/index/offset/
instructions/current/nest/at/area/safe) is one `var` block of the fn, named `cc_*` / `o_*`;
`index` and `offset` are one `work` region (`offset = index + 4 * count`, both `store32`'d).
`refuse N;` is the C `return N` of the leaf (no store through `out`).

### 11.2 C -> `.rxfs` translation table

| C (`compile.c`) | `.rxfs` | Notes |
|---|---|---|
| `put/put32/put64` | `m_put/m_put32/m_put64` | pass 1 only counts (`o_write == 0`); `o_failed` when `size >= cap`; a write is `o_buf[o_size]`, little-endian |
| `patch32` | `m_patch32` | only when `o_write`; skipped when `at + 4 > o_cap` |
| `pick` | `m_pick` | 8/4/2/1-byte pieces where both ends align; the quirk (`left >= 16` halves) kept |
| `plan` | `m_plan` | the shape of a leaf -> argument words and call types in the shared `m_arg`/`m_type` regions |
| `x_*` (x86_64 encoders) | `m_x_*` | same instruction words; `x_rax_imm` = `48 B8` + 8 bytes |
| `a_*` (aarch64 encoders) | `m_a_*` | same instruction words; `a_imm` is movz/movk, `a_load/a_store` size-indexed |
| `w_*` (wasm32 encoders) | `m_w_*` | uleb/sleb, `br_table` dispatch, `w_leave` closes the blocks |
| `be_begin` (3 versions) | `m_be_begin` + `m_be_begin_{x,a,w}` | the dispatcher picks by `host()` — the C's `#if defined(__x86_64__)`-style seam folds to one seat per codegen run |
| `be_param/be_move/be_call/be_return/be_refuse/be_safe_point/be_zero/be_slot_copy/be_less/be_increment/be_value` | `m_be_*` dispatchers | each picks the seat's encoder family by `host()` |
| `be_if_zero/be_if_else/be_if_end` | `m_be_if_*` | backpatching branch; wasm32 is structured blocks |
| `be_dispatch/be_enter/be_jump/be_tail` | `m_be_*` | wasm32 `br_table` over a jump table; the x86_64/aarch64 jump has one size whatever its distance |
| `have` / `in_frame` / `find_leaf` / `channel_area` | `m_have` / `m_in_frame` / `m_find_leaf` / `m_channel_area` | as in `graph.rxfs` (`entry` walk on the context) |
| `check` / `value_check` / `terminal` | `m_check` / `m_value_check` / `m_terminal` | operand kinds (slot/literal/object) with the same refusals |
| `compile_program_call` (1499) | `m_compile_program_call` | `name = w + RXF_CALL_HEADER`, `operands = name + RXF_OPERAND_WORDS` (24 bytes); callee program validated as a program |
| `compile_call` (1584) | `m_compile_call` | `operands = w + 48` (RXF_CALL_HEADER words); the `'b'` operand quirk (literal or 1-byte slot only) refuses UNSUPPORTED |
| `edge_size/instruction_size` | `m_edge_size/m_instruction_size` | every opcode's word count; the partial-program refusals keep `at` at the offending word |
| `compile_edge/compile_instruction` | `m_compile_edge/m_compile_instruction` | the 12 opcodes; copies of an edge; the interpreter keeps safe points |
| `compile_pass` | `m_compile_pass` | count pass then write pass; the macros splice at each use, so the two C calls are two expansions (same behavior, larger code) |
| `rxf_compile_program` (1957) | `fn compile_program` | the entry: `valid()` is the context shape check (`load64(c) == 2`, `load64(c + 8) >= 144`, request nonzero), `RXF_GRAPH_MAGIC` = `0x31475852`, `RXF_NO_INSTRUCTION` = `0xFFFFFFFF` |

* Refusal codes are the numbers of `stdlib_runtime.h`: 6 OUT_OF_RANGE, 9 OUT_OF_MEMORY,
  13 UNBOUND, 14 TYPE_MISMATCH, 16 UNSUPPORTED (left to the interpreter) — the same
  `at`, the same `out_size` (a program that does not fit is counted first, refused after).
* `host()` (the seed intrinsic) returns 0 x86_64, 1 aarch64, 2 wasm32 — the one seat seam
  where the C has `#if`. Every `m_be_*` dispatcher branches on it.
* Memory effects are identical: the tables in `work` are `0xFFFFFFFF`/0-filled, `out_size`
  and `at` are stored through the request on every path including the refusals.

### 11.3 The seed's two fixes this port needed (docs/29)

* `macro_expand` was a fixpoint that respliced the whole token stream per use — quadratic on a
  program of this size (minutes). It is now one linear recursive walk (same semantics, same
  error messages), with growable token buffers.
* aarch64 `cbz` has but ±1 MB (`imm19`) and silently truncated: a heavily macro-spliced
  `if` block is larger, so `be_if_open`/`be_loop_break` now emit `cbnz x0, .+8` over an
  unconditional `b` (`imm26`, ±128 MB) that is patched, and `a_patch` refuses out-of-range
  branches loudly. x86_64 (`rel32`) and wasm32 (structured blocks) are unchanged — the
  graph build's bytes are identical there, and `test_graph_run_is_bit_exact` passes with the
  new aarch64 code. The trampoline adds 4 bytes per conditional on aarch64.

### 11.4 Differential oracle

During the migration, `tests/graph_world.py` `compiled()`/`request_of()`/`Compiled(status, at, size, outcome)`
ran the seed-built `compile_program` and the clang one on the same cases and held them to the same
status, the refused word, `out_size` **and the executed outcome** of the code they wrote. The
clang body is deleted now (docs/31 §6 step 3) and the cases pin the seed's own words; the dev
harness was `diff_compile.py` (the case builders of `tests/test_compile_cases.py`
and `tests/test_tiny_corpus.py`): 129 cases + 28 refusals per seat, parity on x86_64,
aarch64 and wasm32. The code area of `tests/emulator.py` grew (the one-pass seed bodies are
much larger than clang's) — that is a constants change in the emulator only.

---

## 12. `live_pump` -> `native/live.rxfs` — the translation

The former `native/live.cpp` (151 lines, the leaf) over `src/pymergetic/rxftk/core/live/live.cpp`
(2013 lines, the area and the frames) and `src/pymergetic/rxftk/core/arena/arena.cpp`
(601 lines, the arena) plus `core/arena/mem.hpp` and `core/hash/sha256_core.hpp`, all of it
into the one leaf `fn live_pump(context, area)` of `native/live.rxfs` (leaf 29,
`RXF_LEAF_LIVE_PUMP`, shape `!pp`, status-only: every exit is a `refuse`, there is no
store-through-out). `safe_point` (leaf 38) is **not** part of this file: its one defining
source is `native/safe/safe.rxfs` (`seed_build.SOURCES`), and `tools/native/live_build.py`
refuses a source that defines it.

### 12.1 Flattening (the C++ that has no seat in the dialect)

The C++ is already freestanding ("no virtual call, nothing from a library", `live.cpp:9`);
what remains is flattened by hand, as `native/live.rxfs` records in its header:

* `Session` and `Arena` members become the `var` words of the single fn (the fn's var block
  is the object's state); every member function is a macro family whose header names the C++
  it mirrors. There are no vtables to flatten.
* A helper's `return` becomes `h_ok = 0/1` plus a guarded block — a macro expands where it
  stands, so a `return` inside one would leave the whole fn.
* RAII (`Arena out;` in the emit path) is an explicit init over the emit region; the
  destructors are the explicit teardown the C++ scope ends spelled out.
* `load<T>/store<T>` are the width intrinsics (`load8..load64`, `store8..store64`),
  `literal<N>` is byte stores (the SHA-256 K table), `[[clang::musttail]]` is the seed
  extension `tailcall_pp` (the relocation tail-call out of the self-copy).
* Nested loops that would recurse stay loops.

### 12.2 The frame path and the doorbell words

* Two rings in the area (`ar`), each `capacity` bytes with `head`/`tail` u32 word offsets
  (`ar + 88` rx base, `ar + 96` rx mask, `ar + 104` tx base, `ar + 112` tx mask): the frame
  bytes are copied through `rqbuf[1044]`/`rsbuf[1044]` (16-byte header + 1024 payload +
  4-byte trailer), the ring access wraps with `& mask`.
* Frame header is `struct <HBBIIHH`: magic `0x5852`, op u8, flags u8, sequence u32,
  argument u32, length u16, status u16 (16 bytes), then the payload, then `crc32` of
  everything before it. A bad magic or a length over 1024 walks the tail one byte
  (resynchronise); a bad crc drops the frame the same way. A reply is the request's op
  `| 0x80`, the echoed sequence, `sv_status`, `sv_rsl`, `sv_rarg`.
* The doorbells the interpreter touches: `RX_HEAD` 120, `RX_TAIL` 124, `TX_HEAD` 128,
  `TX_TAIL` 132, `BREAK_REQUEST` 176, `BREAK_ACK` 180, the grow-wanted word 232,
  `IN_RUN` 256, `relocated` 260, `relocate` 264, `epoch` 268 (`core/live/live.h`, the truth).
* The pump serves at most `pu_limit` frames per call (64, the batch; a break request stops
  the batch and parks in the two phases the C++ reaches its four `pump()` call sites from —
  wasm stop / relocated / batch / decay-failure — through `pu_limit`/`pu_stop_after`/
  `pu_phase`/`pu_again`, so the serve machinery exists exactly once in the emitted body:
  the self-copy in the relocation path copies **this** body).
* `m_serve` is the op dispatch (PING 1 echoes the payload, HELLO 2, DUMP/EMIT 3, RESOLVE 4,
  …), every arm the `serve()` case of `live.cpp:180`.
* Refusals are the numbers of `stdlib_runtime.h`, identical to the C++: 3 (BROKEN/DIGEST),
  4 (BAD_FRAME), 9 OUT_OF_MEMORY, 12 STALE_GENERATION, 13 UNBOUND (no area in the
  context's capabilities: `refuse 13` at the entry), 14 TYPE_MISMATCH. The statuses a frame
  can answer (OK/overflow/…) travel in the frame's `status` half-word, not as refusals.

### 12.3 C -> `.rxfs` translation table

| C++ | `.rxfs` | Notes |
|---|---|---|
| `mem.hpp` `load/store/zero/copy/compare` | `s_cpy` / `s_zero` / `s_cmp` / `m_mv` | byte loops, shared by every macro |
| `crc32` | `m_crc` + `s_crc32` | `crc_r` out word |
| `sha256_core.hpp` | `m_sha_k` / `s_sha_init` / `s_sha_block` / `s_sha_upd` / `s_sha_finish` | `literal<N>` = the K table as byte stores |
| `Arena` fields | `ab`/`acomm`/`alim`/`ar`/`ah`… the fn's `var` words | the word offsets of `live.h`/`arena.h` are kept literally (`ar + 120` is `RX_HEAD`) |
| `Arena::attach/slot/payload/refs/name/cell` | `s_attach` / `s_slot` / `s_payload` / `s_refs` / `s_name` / `s_cell` | the chunk directory at `load64(ah + 48)` |
| `Arena::allocate/create/resize/destroy` | `s_allocate` / `s_create` / `s_resize` / `m_destroy` | `h_ok` flags instead of returns |
| `Arena::intern`, name lookup | `m_intern`, `s_find_child`, `s_name_hash`, `s_home` | 256-slot tables `shk`/`shw`/`shh`/`shd` |
| `commit/rollback/before_write` (the log) | `m_begin` / `s_before_write` / `m_commit` / `m_rollback` / `s_ensure_log` | the log region `alog_base..alog_lim` |
| `compact()` (scan/mark/copy) | `m_compact_scan` / `m_compact_copy` | in-place compaction of the arena |
| `emit()` (the dump) | `m_emit` | RAII -> explicit init over the emit region (`jemit_base`/`jemit_cap`) |
| `apply` (prepare/replay) | `m_apply_prepare` / `m_apply_replay` / `m_check_all` | the physical delta of `rxftk.live.physical` |
| `relink` | `m_relink` / `s_linkable` | rewrites the context's slots/entries |
| `grow` | `m_run_job` paths + the 232 doorbell | a grow request records the wanted size |
| `own_body`/`publish_code` | `m_own_body` / `m_code_addr` | `pc()` finds the slot that owns the running code |
| `pump()` (the batch loop) | `m_pump` | doorbells, ring arithmetic, the frame loop |
| `rxf_live_pump` (the entry) | `fn live_pump(context, area)` | the area from `area` or the first capability (`refuse 13` if none) |
| `[[clang::musttail]]` (the relocation tail-call) | `tailcall_pp;` (seed extension) | out of the self-copy for good |
| aarch64 `dc cvau`/`ic ivau` (`publish_code`) | **dropped** | not expressible in the dialect: a no-op in the seed body. The hardware gap is named here: a relocated aarch64 body must still have its i-cache flushed by the caller (the C++ did it in the leaf). x86_64 needs nothing; the emulator seats model no caches. |

### 12.4 The memory model, spelled out

The C++ ring words are volatile u32s paired with acquire/release (one producer, one consumer
per ring). The Seed models them as plain `load32`/`store32`. The assumption, explicit:
u32 accesses are atomic on x86_64/aarch64/wasm32, the stores that publish the ring bytes
happen before the stores that move the counters, and the pump runs at safe points where the
producer is quiescent. The differential seats are single-threaded, so plain accesses see the
same interleaving there; a multi-threaded host producer needs a barrier at the two counter
stores (named here rather than hidden in the dialect).

### 12.5 The seed's fixes this port needed (docs/29)

* `callsub name(a, b);` becomes `param = a; param = b; callsub name;` before parsing (the
  sub's parameters are named variables of the caller's frame) — on wasm32 the call is then
  spliced inline; on x86_64/aarch64 it stays a `call`/`bl`.
* `sub_emit_used` patches a sub's call sites **after every body is out**: a sub that calls a
  sub emitted before it records its fixups only while its own body is parsed.
* `be_mstore(2)` on x86_64 writes `mov [rcx], ax` (it wrote `dx`, so `store16` stored
  nothing of the value).
* The frame's declaration scans (`sub_scan_decls` and the fn-body scan) register **every**
  name of a `var a, b, c[8];` line — a missed name took a fresh scalar slot where the
  regions live and silently overwrote `rqbuf`/`rsbuf`.
* `sub_expand` marks a sub `spliced` only on wasm32: a native call with arguments rewrites
  the arguments but keeps the body, so its declarations still need scanning.

### 12.6 Differential oracle

The dev harness is `diff_live.py`: the cases of `tests/test_live_native.py` and the live
world transplanted (ping, no_channel, reads, dump, apply, refused, random_edits, relink,
linkable, compact_canonical, compact_garbage, behind_compaction, in_run, no_log_room,
pinned, grow), each run **twice** per seat — once over the C++ body and once over the seed
body in the Code cell — and the two records must be equal, on x86_64, aarch64 (Unicorn) and
wasm32 (node). Content-addressed values (digests, dumps) appear as booleans "matches the
host build" (an `HostImage` oracle inside the scenario), so a record is comparable across
the two bodies; the runs are checked against their own base image (the Code cell differs,
so the arena bytes do).

`tools/native/live_build.py` is the one build path: `python tools/native/live_build.py`
prints the three body sizes and checks the native ones against `LiveSpec.log` (256 KiB —
the relocation path copies the body there), `--json` answers `{machine: hex}`.

### 12.7 Wire-in (`tools/native/live_sync.py`)

The starter's `live.pump` Code cells are written by `tools/native/sync.py` from the one build
table (`tools/native/native_build.build()` over `seed_build`, `live_pump` included). The
`(live.cpp, live_pump)` recipe is gone with the cleanup (docs/31 §6 step 3; the file is
deleted). `tools/native/live_sync.py` `MODULES["live"]` names
`(native/live.rxfs, core/live/live.h)` — the semantic digest is off the C++.
`python tools/native/sync.py` regenerates the starter; the proves are the targeted
`tests/test_live_native.py` cases and `diff_live.py` on the three seats.

Environment note (not this port): the harness starter is ~29 MB and `HostImage`'s default
arena is 8 MiB, so `test_reads_match_the_host_build` and friends raise "the image does not
fit the arena" before they reach the pump — the oracle needs an arena sized to the image
(`HostImage(raw, arena=len(raw) + (1 << 20))`) or a smaller fixture.
