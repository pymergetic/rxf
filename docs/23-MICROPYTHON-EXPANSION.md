# 23 — MicroPython on RXF

**Status: design target for the expansion.** This document designs the MicroPython-on-RXF
expansion: lwIP as a native card (NETZ), uasyncio on the run-cell machine (orchestration), a
second import strand for Python sources, the hybrid module tree, and the breadth story. It is
the seam contract for the queued lanes (i2 the pointer/out-param model, i3 runtime dispatch, the
Python-import strand, the lwIP card, uasyncio). What it names as landed is landed: the Doc type
and its LZ4 codec (`core/doc/codec.cpp`), call cards and variable-length frames (`native/graph.h`,
[docs/20](20-EXECUTION-POLICY.md) S6/S7), the MicroPython seam as a pinned model (S11,
`tests/host/smp_mpmodel.c`). What is queued is queued: `tools/native/rximport.py` has no
`--provenance` knob and no debug-info parse yet, and the comment-lifting API of the doc format is
in flight ([docs/22](22-DOCUMENTATION-AND-INTENT.md)). This document pins the call sites that API
must serve; it edits nothing.

## 1. Overview and the mapping

MicroPython runs **as a guest of the image**: the interpreter's words are cells, its safe points
are the run cell's safe points, and its `import` is a lookup in the object tree. The expansion is
five pieces, in this order of dependency:

- **(a) NETZ.** lwIP becomes a **native card behind the net/io face** (or later the object
  world): one imported graph — the whole TCP/IP core — behind the one `net.io` face, with a
  SLIP/netif bridge carrying IP packets across the host↔guest boundary. Its prerequisites are the
  i2 slice (pointer/out-param model: lwIP's API is out-params and pbuf pointers) and the i3 slice
  (runtime dispatch: lwIP is callbacks through function words — `netif->input`, `udp_recv`,
  `tcp_accept`).
- **(b) ORCHESTRATION.** uasyncio maps onto the run-cell machine. The scheduler seam already
  exists: `mp_sched_*` and `mp_handle_pending` are S11 safe points. An asyncio Task is a run
  cell, the event loop's ready deque **is** the S3 ready-ring (one queue, not two), and the 3D
  world keeps its honest picture: the ring road is the ring cell, a tram is one run cell in a
  ring slot, and a `PARKED` run is not on the road at all.
- **(c) BUILTINS + PYTHON-IMPORT.** A **second import strand** beside `rximport`: Python sources
  (`.py`) compile through the toolchain's Python→RXF compiler. Imported Python is DOC-marked
  *was once Python* (docs/22) and takes the same per-import `--provenance=full|cite|hash` switch.
  C builtins go through `rximport` with debug-info citations. Python-implemented builtins and C
  builtins both end as RXF objects; only their provenance differs.
- **(d) HYBRID MODULE TREE.** `mp_import_stat` resolves over **named cells under the root** — the
  image *is* the module tree. No filesystem, no `.mpy` loader. Two front doors, one namespace
  (docs/22).
- **(e) BREADTH.** The same pipeline scales: the corpus (`tests/import_corpus/`, 34 C files plus
  2 C++ and 5 Rust today) is the start, MicroPython's `py/*.c` and `extmod/uasyncio` are the
  middle, lwIP is the first large foreign body. Every scale step reuses the same seams: one
  emitter, one provenance machine, one seat matrix.

### The mapping table

| MicroPython | the RXF concept | pinned at |
|---|---|---|
| module (C or Python) | a `Module` cell (NodeKind 19) under root; its globals are named cells under it | docs/22, docs/20 S11 |
| `mp_obj_t` (REPR A) | one word: bit 0 set is a small int (no cell); otherwise `handle << 1`, resolved at use | docs/20 S11 |
| type slot (`mp_obj_type_t`->`unary_op`, ...) | a function word of the type cell; a call through it is one call card per arm, named by the store that wires the slot | `lower_indirect`, `function_wiring` (`rximport.py`), docs/20 S6 |
| qstr | an interned name of the image; the pool is shared, immutable after init, read claims only | docs/20 S11 |
| `nlr_push` / `nlr_jump` | the run's handler chain and the frame checkpoint (`mp_frame_sum`/`mp_frame_xor`); legal within one step, never across a safe point | docs/20 S11 |
| scheduler (`mp_sched_schedule`) | one coalescing `mp_sched_flag` per run + a wake-token op (S4) | docs/20 S11 |
| pending exception | `mp_raise_slot`, one atomic word; a deposit is a CAS and may be refused | docs/20 S11 |
| heap (`mp_state_mem`) | the arena (docs/03): a heap object IS a cell; collection is the sliding pass (S8); a cached reference across a safe point is a handle | docs/20 S11 |
| blocking hal call (`mp_hal_delay_ms`) | a park (S4) with a timed edge wake (S9) | docs/20 S11 |
| `import` / `mp_import_stat` | a lookup of named cells under root | docs/22, §4.6 |
| coroutine / `uasyncio.Task` | a run cell (S1) whose coroutine frame chain lives in the heap | §4.5 |
| C call chain | run-cell frame links: `(base, size)` frames carved from the run cell, at most `RXF_GRAPH_CALL_DEPTH` (16) open, their sum within `RXF_GRAPH_CELL_BYTES` (16384); a Python call chain is heap frames (`mp_code_state_t`), not links | docs/20 S6, `native/graph.h` |

The C stack of a carrier is empty at every safe point; the guest's C-graph nesting is the bounded
link stack of the run cell, and its Python nesting is the heap frame chain. The two do not share a
budget: a refused call chain is `RXF_REFUSE_CALL_DEPTH` / `RXF_REFUSE_CELL_BUDGET`, never a native
stack overflow.

## 2. The seat matrix

The all-platforms rule: a face has **one Python/C face on every seat**; only the fills differ, and
a missing fill is a stub that fails the prove, not a dark port. The seats are the mp/upywm seats
(`packages/REPO_LAYOUT.md`); wiring them happens in those trees in the same change as the face.

| key | seat | tree | prove |
|---|---|---|---|
| H | host C | mp `extmod/metal` | `make -C extmod/metal test` |
| U | unix µPy | mp `ports/unix` | `make -C extmod/metal upy` |
| B | emcc browser | mp `ports/webassembly` | `make -C extmod/metal browser` |
| F | firmware BIOS + UEFI | mp `extmod/metal/port` | `make -C extmod/metal firmware` |
| W | unix µPy wasmmod-only | upywm `ports/unix` | `make -C ports/unix MICROPY_PY_WASM=1` |

New faces, and what varies per seat (the fill). Where every cell says the same thing the row is
deliberately uniform — that is the honest "one face, one body" case.

| face | H | U | B | F | W |
|---|---|---|---|---|---|
| `net.io` body (the lwIP card) | one imported graph | same words | same words | same words | same words |
| the SLIP line (the card's byte source/sink) | pty/socketpair to the harness | tap/utun over host loopback | the wasm32 FIFO block ↔ a host WebSocket bridge | the `RXF_UART` capability (16550/PL011) to a gateway | tap/utun (no metal) |
| the VM seam (S11) and its S9 shim | pthreads projection | pthreads/POSIX projection | asyncio projection (emscripten) | Metal/fence projection | pthreads projection |
| hal timers / blocking calls | the shim's timed wait | same | same | the Metal timer | same |
| `import` (`mp_import_stat`, §4.6) | the image lookup | same | same | same | same |
| C builtins (rximport strand) | objects in the image | same | same | same | same |
| Python builtins + uasyncio (Python strand) | objects in the image | same | same | same | same |

The rxf-tree gates (`pytest tests/test_import.py`, the `tests/host` harness,
`pytest tests/test_smp.py`) are **necessary but not sufficient**: they prove the strands and the
seam model; the seat proves hit the face with the guest running. What each seat's prove must do
to the new faces:

| face | the stimulus every seat's prove must hit |
|---|---|
| `net.io` | a TCP and a UDP round trip through the card, and at least one SLIP frame pump (H: vectors on the pty; U/W: a real socket through tap; B: an echo through the bridge; F: UART loopback or the gateway) |
| VM seam | a guest run that parks and is woken (a blocking hal call, an interrupt deposit) — the S11 identities under the seat's shim |
| `import` | the guest executes `import a.b` over named cells and gets the module — the same test source on all five seats |
| C builtins | one imported builtin called from guest Python |
| Python builtins + uasyncio | one `.py` module imported and one task awaited to completion |

## 3. One defining language

Every piece has one muscle language. The rule forbids a second implementation so one seat can
skip the first — and it binds here more than anywhere, because the guest is a third-party body.

| piece | defining language | where it lives | what the rule forbids |
|---|---|---|---|
| run-cell machine, arena, Doc codec | C11 (`tests/host/smp_*.c`), C++20 (the core) | landed | a Python stand-in of the machine for tests |
| rximport (the C/IR strand) | Python | `tools/native/rximport.py` over the C++ core | a C twin importer; a binary importer |
| the Python→RXF compiler | Python | `tools/native/`, one new tool beside `rximport.py` | a Python parser inside the core; Python source pasted into C strings (source-in-its-lang) |
| the emitter (the delta/XML vocabulary) | one implementation | `emit_delta`/`_function` in `rximport.py` today | a second emitter for the second strand |
| lwIP card | **vendored C as source-of-record; the card is the imported RXF graph** | `third_party/lwip` (pinned upstream, BSD-3) → `rximport` | `liblwip.a` per seat; a host stub; one stack per seat |
| uasyncio | Python (upstream `extmod/uasyncio`) | the Python strand | a C event loop beside it |
| comment lifting | Python tool + one core API | `tools/native/` + `core/doc` | lift logic inside `rximport.py` |
| the SLIP line fills | C (seat drivers) | one file per seat, like `wasmmod/ports/webassembly/io_browser.c` is for `io.fetch` | a second stack inside a fill |

Two sharpenings the question deserves:

- **What is the lwIP card's defining language?** C is its *source of record* — the origin record
  and every citation point at upstream lwIP C (docs/22 provenance) — but the card's *defining
  artifact* is the imported RXF graph, compiled **once** by `rximport` and run as the same words
  on every seat. The differential's native compile of corpus sources (the harness compiles the
  same C without `RXF_IMPORT_FACES` to compare answers) is a **test reference**, never a seat
  fill. A seat that links a native lwIP has a second implementation and fails the rule.
- **Where does the Python→RXF compiler live?** In `tools/native/` beside `rximport.py`, in
  Python: the toolchain's orchestration muscle is already Python over the C++ core (`rxfc.py`,
  `compose.py`, `rximport.py`), the source-in-its-lang rule keeps Python compilation logic in a
  `.py`, and no seat needs a Python parser in the runtime. The self-build story (docs/19)
  eventually imports the compiler itself through its own strand — the compiler is its own first
  *was once Python* case — but that is a later edition, not the lane's first commit.

## 4. The seam tables

Each queued lane: what it must touch, what interface it must implement, what it unrefuses, and
how the comment-lifting API reaches its objects (§4.7 is the contract; nothing here may require
an edit of `rximport.py` for the lift).

### 4.1 i2 — the object and pointer model (out cells)

Today the importer refuses a body that stores through a pointer parameter outside its caller's
inlined locals ("a pointer to a value", `lower()`); `tests/import_corpus/outparam.c` proves the
inlined shape (wide frame slots a callee's stores land in). docs/20 S6 fixes the destination
model: a callee writes through an **Object** its caller passes — an out cell whose handle the
caller holds — never through a raw pointer to a caller's value.

| | |
|---|---|
| touches | `tools/native/rximport.py` (the pointer rules of `lower()`, operand emission in `_function`/`_operation`, `function_wiring`); `native/graph.h`/`native/graph.c` (the call step's handle arguments — documented already, verify the binding); `tests/import_corpus/outparam.c` (extend to the call-card shape: the callee out of line); `tests/test_import.py` (the REFUSED table flips) |
| implements | a call-card argument in a pointer/out position is a **handle operand** (three words, the run-entry `ARGS` shapes); the callee resolves the Object at each use and writes its out word through it; a scalar `RETURN` still copies to the caller-named destination. A pointer into flat memory keeps the landed profile (getelementptr arithmetic, loads/stores at a named address) and a pointer the pass cannot name is still refused |
| unrefuses (gate) | a corpus body where the out-param callee is a **call card**, not an inlined body, and its writes land in the caller's slot; then the out-param half of `mp_obj_get_int_maybe` |
| docs | out cells and imported bodies are named objects (the paths `import_into` fixes); the lift places on them by path. No new contract |

**Status of the local route (2026-10-31).** The gate above has a second half the first slice did not
draw: a pointer to a LOCAL of the caller's frame handed to a call card. A callee runs in a frame of its
own and the call copies the bytes of the value it is handed, so the callee used to receive the local's
*content* where it expected an address (lwIP's `icmp_dest_unreach` sent its ICMP reply to 0.0.0.0, a
larger reader segfaulted the host runner: a silent wrong word, the same family as the card's pointer
carrier). Landed in `rximport.lower_card` (`local_cell_argument`) with the **existing operands**: the
local rides in a cell the caller owns, an Object like a static (`<module>.localcell<i>`, one per
argument position, 16 bytes); the bytes go in before the call, the callee gets the flat address the
cell has, they come back after it, and the cell's previous bytes are saved in the frame and put back
so a re-entering callee finds it as its own activation left it. `tests/import_corpus/localptr.c` pins
read, write, both, two calls in a row, two locals in one call and three levels of re-entry; every one
crashed or answered wrong before. What this does NOT cover, and what it needs: two runs of one graph in
flight at once share the cell as statics do (the SMP policy); the block-per-activation answer is the
allocation leaf of §4.9, which would replace the cell. A local larger than 16 bytes is refused where
its slot is made (it used to overflow into the next value of the frame), and the address of a local
derived by getelementptr (local arrays and structs) stays refused by name.

### 4.2 i3 — runtime dispatch (function words)

`lower_indirect` already dispatches a call through a function word where the **same import** wires
the word: the store that fills the slot names the body, one call card per arm, and a word no store
wires refuses the import (`tests/import_corpus/dispatch.c` pins it). What is queued is the
universe: a slot wired in one import (a boot) and read in another (a body).

| | |
|---|---|
| touches | `rximport.py` (`lower_indirect`, `function_wiring`, the prescan); `native/graph.h`/`native/graph.c` (the word dispatch: a compare per wired word and its call card — the run refusal when no arm names the word); `tests/import_corpus/obj.c`/`slots.c`; `tests/test_import.py` |
| implements | a **wiring universe** that survives an import run as objects: the words a boot stores and the Function paths their bodies sit at (today's `function_wiring` table, made a link fact), so a dispatch resolves at run time against the union of the linked imports. `known=` covers direct calls only and is not this. A word no arm names refuses the **run** (`slot`), never the answer |
| unrefuses (gate) | `mp_obj_is_true` and the dispatch half of `mp_obj_get_int_maybe` (both pinned in REFUSED as "function word"); the corpus gate is boot + bodies across **two** import runs |
| docs | the wiring facts are objects (stores name bodies); the lift narrates the store's body by path. No new contract |

### 4.3 The Python-import strand

A second strand beside `rximport`, not a mode of it: `.py` → the toolchain's Python→RXF compiler
→ the same object vocabulary (a `Function` with a `SemanticGraph`, a `Module` cell, `Code` where
the bytecode view earns its place) → the same delta vocabulary → one image.

| | |
|---|---|
| touches | `tools/native/` (the compiler, new — see §3); the emitter (shared with `rximport`, one implementation — see open decision 2); `src/pymergetic/rxftk/core/` (no new kinds: `Module`, `Function`, `SemanticGraph`, `Doc` exist); a `tests/py_corpus/` differential; the starter's module tree for the guest stdlib |
| implements | `import_py_file(image, source, module, provenance=...)` with the same shape as `import_source_file`; per-import `--provenance=full\|cite\|hash` (§5); the *was once Python* Doc per module; Python semantics lowered to the same machine — a method call is i3 dispatch, an `mp_raise_*` is a Refuse leaf, `try/except` is the nlr seam of S11, `await` is §4.5 |
| refuses (by name, at import) | whatever the subset does not lower: `eval`/`exec`, runtime module creation, C-extension capsules, ... — each with the reason, never a wrong word |
| docs | the compiler sees its own AST line map: docstrings and comments lift **at compile time** through the same two calls of §4.7 (the module's provenance Doc carries the marker *was once Python*) |

### 4.4 The lwIP card and the SLIP/netif bridge (NETZ)

lwIP is imported **once** — `import_source_file` over the vendored C, `-g` for citations — and the
card is that graph set on every seat. The bridge is SLIP (RFC 1055) over a byte line; the line is
the per-seat part, and it is a **fill**, like `io.fetch` has fills.

Packet path (both directions are the same story reversed):

1. the host side hands an IP datagram to the seat's line provider (tap/pty, WebSocket bridge,
   UART — §2's table);
2. SLIP framing crosses the host↔guest boundary as bytes (RFC 1055 END/ESC stuffing; VJ header
   compression stays **off** — a refused card, not a silent default);
3. the guest's `slipif` driver deframes and calls `netif->input` — the i3 callback the boot
   wires;
4. lwIP builds pbufs (arena cells, see risk 4) and runs its core as imported graphs;
5. the guest's socket face / `io.fetch` answers Python; a TX packet takes the path back.

| | |
|---|---|
| touches | `third_party/lwip` (new, pinned); `tools/native/rximport.py` (used as it is — the whole point of i2/i3 landing first); one line driver per seat (`slip_line_host.c`, `slip_line_tap.c`, `slip_line_js.c`, `slip_line_uart.c`); the lwIP port face `sys_arch` (new, behind the VM seam): a mailbox is a channel frame (docs/10), a semaphore is the park/wake pair (S4), `sys_now` is the edge timer (S9) |
| implements | the `net.io` face's body as one card; `mp_hal`-style blocking reads park the run; the boot-wiring card that stores callback words into `netif`/pcb cells (the `obj_port_boot` idiom of `tests/import_corpus/obj.c`, generalized — static initializers carrying addresses are refused relocations, `global_reloc.c`) |
| unrefuses (gate) | i2 + i3 first; then the corpus gate: `slipif` + `tcpip` core + one socket echo **as imported graphs** on H, with the seat proves of §2 |
| docs | upstream lwIP comments lift through §4.7's tool (definition comments and free-floating ones, TODOs as `DocKind.Todo`); at `cite` level the citations are the permalinks into the pinned upstream commit |

**Status (slice 1, measured):** lwIP 2.2.0 is vendored and pinned (`third_party/lwip`), the NO_SYS=1 port
face is `native/lwip/`, and the import lane has been run over IPv4 + UDP + ICMP + `slipif` as far as it
honestly goes -- 85 of 115 bodies import and compose, 8 are refused with the importer's own reasons
(two for `llvm.memmove`, four local-array address arithmetics, two out-pointer writes), 22 wait for libc faces, the SLIP line fills or a body of the path that waits.
Slice 1 first measured 68 / 38, 30 of the refusals for `llvm.bswap`/`umin`/`abs` intrinsics; the
importer lowers those now (docs/35 §3.1) and every body of the UDP echo path lowers, the path ends
at the SLIP line. The table, the differentials that run today and the next packages are
[docs/35](35-LWIP-IMPORT-TABLE.md); `tests/test_lwip_import.py` pins them.

The face is `net.io` **now** and the object world later (net as addressable objects, like
everything else); that is a rename of the body's owner, not a second face (the platform rule:
never `net.fetch` beside `io.fetch`).

### 4.5 uasyncio: orchestration on the run-cell machine

uasyncio arrives through the Python strand (upstream `extmod/uasyncio`, provenance *was once
Python*); what this seam adds is the binding of its primitives to machine words — no second
scheduler, and above all **no second queue**:

| uasyncio | the run-cell machine |
|---|---|
| `Task` | one run cell (S1); the coroutine's frame chain is the heap's (`mp_code_state_t`) |
| the loop's ready deque | **the S3 ready-ring itself** — `call_soon` enqueues, the loop is the ring's dispatcher |
| `await` on a Future/Event | `PARKED` (S4); `set_result` is a wake targeting the run, never a carrier |
| `call_later` / `ticks_ms` | the edge timer (S9) — the watchdog's sibling, counted apart |
| socket/stream readiness | the net card's line events into the shim's wait/notify |
| task cancellation | a raise deposit (`mp_raise_slot`, the `CancelledError`) plus a wake — interrupts are deposits, never signals (S11) |
| `Queue`/`Stream` backpressure | channel frames (docs/10) or the Future park/wake pair |
| `run_until_complete` | the top-level task's run reaching `TERMINAL` |

The world's picture stays honest exactly one way: the ring road **is** the ring cell, a tram is
one run cell sitting in a ring slot (READY/RUNNING), and a `PARKED` run is off the road — a
building's quiet floor, not a circulating tram. The world must never draw a second queue the
machine does not have; its HUD shows the state words (`FREE/READY/RUNNING/PARKED/TERMINAL`) and
the pending pair, and a pin test asserts the tram count equals the ring's occupied slots.

| | |
|---|---|
| touches | the Python strand (§4.3) for `extmod/uasyncio`; the VM seam needs list (docs/20 S11: `mp_sched_schedule` as a wake-token op, `mp_handle_pending` as the safe point, blocking hal as parks) — the seam's model is landed (`smp_mpmodel.c`), the MicroPython side is the import lane; the world/inspector (already draws the ring) |
| implements | the mapping table above as one Python-visible face (`uasyncio`'s loop binds to `mp_sched_*`/`mp_handle_pending`; nothing in the loop reaches a carrier) |
| unrefuses (gate) | a corpus of asyncio programs whose results are byte-identical at 1 and N carriers (the S10 differential extended to task-level results) |
| docs | uasyncio's own docstrings lift at compile time (§4.7); the mapping table above becomes S11 topics when docs/20's policy topics land (docs/22, "policy is documentation") |

### 4.6 The hybrid module tree

`mp_import_stat(path)` resolves over named cells under the root: each segment is a child name of
a `Module` cell (NodeKind 19); a module with sub-modules answers `MP_IMPORT_STAT_DIR`, a leaf
module `MP_IMPORT_STAT_FILE`, anything else `MP_IMPORT_STAT_NO`. The image **is** the tree — the
module-per-file XML of docs/15 is its storage view, the same fact. `sys.path` is empty by policy;
a `.mpy` loader does not exist and will not: a compiled Python module is a `Module` cell of
graphs, never a bytecode loader at runtime.

| | |
|---|---|
| touches | the guest import face (`builtinimport.c`'s `mp_import_stat` fill and its call sites), one face on every seat; the starter's module tree; `tests/` (one guest `import` test run per seat) |
| implements | `mp_import_stat` + the name→cell lookup over the image; one namespace for both strands (a C builtin module and a Python module are siblings; docs/22: two front doors, one namespace) |
| unrefuses (gate) | the guest imports a Python module and a C builtin module from one package |
| docs | each module cell carries its provenance Doc (§5); the lift places file comments on the module (docs/22) |

### 4.7 The comment-lifting call-site contract

The API is the doc format's, in flight with the follow-up lane; this is the contract its call
sites pin. It is served **without editing `rximport.py`** — everything the lift needs it derives
itself or reads from what the importer already fixes.

The API (core's Python-visible face; the text face `Image.doc_text` and the codec are landed):

```python
# pymergetic.rxftk.core.doc — the creation side (proposed shape, the follow-up lane owns it)
doc.create(image, parent: int, name: str, *, kind: DocKind, status: DocStatus,
           codec: DocCodec, text: str) -> int            # a Doc object; text round-trips
doc.place(image, source: int, target: int, role: DocRole) -> None   # a RefKind.Doc placement
```

The call sites (both lane-owned; `rximport.py` is a library they call, never a file they patch):

1. **The lift tool** (e.g. `tools/native/rxdocs.py`) — `lift(source, image, module) -> int`:
   compiles its own `-g` IR with the same invocations as `compile_c`/`compile_cpp`/`compile_rs`
   (same optimization level, so the definition set matches what was imported); derives locators
   from `!DISubprogram`/`!DILocation`/`DIFile` (docs/22: the debug info gives the locator, the
   lift never guesses); lifts the comments that precede a definition **and** free-floating ones
   (a file comment places on the module); creates one Doc per captured comment —
   `DocKind.Narrates` placed `DocRole.Narrates` on the imported object, `DocKind.Todo`
   (`DocStatus.Open`) for TODO/FIXME — and resolves every target by **path**: the paths
   `import_into`/`import_source_file` already fix (the C name → Function path map they return is
   the naming contract; `Image.resolve` reads it back).
2. **The Python strand's compiler** — at compile time, from its AST line map: docstrings and
   comments through the same two calls; one provenance Doc per module carrying the marker
   *was once Python* (§5).

Refusals around the contract: no source map (no `-g` debug info on the C side) ⇒ the lift refuses
to invent docs; an empty body is refused (`DocCodec.Raw` with no text is pinned forbidden —
`core/doc/codec.cpp`); a synthesized Python object with no source lines gets **no line citation**
— at `cite` level it places on the module's provenance Doc instead of inventing a locator. The
stability pins the lift lane owns: the naming contract, the `<rxf:doc>` delta round-trip
(docs/15), and the `DocKind`/`DocRole` enumerations (`core/kinds/`).

### 4.8 The value-width and relocation seam (the emitter's words)

The vocabulary seam `rxemit.py` owns is one model wider than it was: a module's **width model**
(`rxemit.DataModel`, from the IR's `target datalayout` — ILP32 or LP64) and the **relocation**
of a static initializer (`Global.refusal`, `Relocation` in `rximport`). Both extend the seam
backward-compatibly: the delta vocabulary is unchanged (`U64`/`WIDE`/`U32` still the only value
types), and the widths ride what it already states (`literal_size`, the payload of a
`SemanticObject`).

| | |
|---|---|
| touches | `tools/native/rxemit.py` (`DataModel`, `Global.refusal`); `tools/native/rximport.py` (`module_model`/`pointer_width`, the model every sizing takes, `init_bytes`/`place_relocations`/`wires_boot`, the masked words of `function_wiring`/`lower_indirect`); `tests/test_import_width.py`, `tests/import_corpus/width32.c` |
| implements | every cell, slot and store word is the **model's**: a pointer is 8 bytes in LP64 and 4 in ILP32, the function words a dispatch compares are the body's object id masked to that word, and a global initializer that names an address (a function pointer, a pointer to another global) places its byte image with the slot zero and one **wire** per relocation in the module's boot (`__wires_boot`) — the loader pass the native static gets before `main` |
| refuses (by name, at import) | what the model cannot decide honestly: a pointer of a module that states no width, two pointer widths in one layout, a width that is neither 4 nor 8, the alignment of a 64-bit scalar under ILP32 (i386 and arm32 disagree — an ABI decision), a relocation slot that is not at a word of its cell or is narrower than the word, a relocation of a C++ runtime symbol (`_ZTV*`/`_ZTI*` and kin), and a declaration without bytes — each with its reason, never a slot silently left wrong |
| docs | the flip of `global_reloc.c:use_reloc` (the relocation wires now) is in docs/20's flip history; the width policy is pinned in `tests/test_import_width.py`, both models of one source against their own host C builds |

### 4.9 The handle operand and the allocation leaf (machine lane H1, design)

**Status: design, no code.** It is written so that a worker who has not read the transcript that
produced it can execute it. It waits for the twin lane (the starter pins: step 9 regenerates
Code objects), and it is the machine half of the question the measurement of section 7 ended on:
*an object held in a name or a parameter has no cell the graph can resolve* (the largest single
refusal reason of `extmod/asyncio`, and the reason `rxpy` instances are named cells today).

#### What exists today (measured; read this before believing the premise)

The first draft of this lane said "no operand resolves a handle, and no allocation exists". Reading
the machine says it is narrower than that:

| piece | state | where |
|---|---|---|
| the reference interpreter | **`native/graph.c` is gone** (`a8f1cb5`, "the Seed is the only source of truth"). The interpreter is `native/graph.rxfs` (Seed `fn` dialect), seed-built per machine for x86_64, aarch64 and wasm32; the oracle is the Python `tests/graph_reference.py` (written from the header, it calls the leaves as x86_64 code in an emulator); the host seat is `tests/rxf_native.py` over `tests/host/runner.c`; the python tool simulator is `tests/rxf_sim.py` | `native/graph.rxfs`, `tests/graph_reference.py`, `tests/test_graph.py` ("the interpreter on three machines against a second interpreter") |
| operand kinds | three: `SLOT` (frame offset), `LITERAL`, `OBJECT` (a constant durable object id; the value is the payload address). `OBJECT` is resolved **at each use** by `m_locate` (a halving search of the sorted entry table `c->objects`), so a cell may move between uses; its generation is not checked | `native/graph.h` `enum rxf_graph_operand`, `native/graph.rxfs` `m_locate`/`m_load`/`m_pointer` |
| the in-image compiler | resolves `OBJECT` operands **at compile time** and bakes the payload address into the machine code (`m_check`/`m_value_check`); any other kind answers status 6 | `native/compile.rxfs` (x86_64, aarch64, wasm32 backends), `tests/test_compile.py` |
| handles in slots | **exist**: a handle is three words (id, generation, offset) in a 24-byte slot; the leaves take it by value (shape letter `H`: `runtime_resolve`, `read`, `write`, `append`, `pin`, `unpin`, `borrow`, `release_borrow`, `allocate_prepare`, ...); a call card passes it as its three words; a callee returns it in a wide out slot | `native/graph.rxfs` `m_call_leaf` (`cl_ish`), `native/runtime/runtime.rxfs` |
| using a held handle as an address | only through the leaf `runtime_resolve(handle, n) -> address`, which writes a **raw address into a slot** for the next instruction to use. Legal on a single carrier with no compaction; forbidden by S7 under the SMP policy (docs/20: a step is one instruction, the end of a step is a safe point, "after a safe point, resolve again") | `runtime_resolve` |
| allocation | `runtime_allocate_prepare` exists (leaf 9) and is **transactional and caller-named**: it needs an `rxf_transaction` (`runtime_begin_private`), a caller-chosen `id` that is an already reserved free row (state 0) of the sorted entry table (`UNBOUND` otherwise), and bumps `heap_frontier` inside `heap_limit`; the row becomes PENDING until `runtime_publish`. There is no id source, no pool, no single-step form | `native/runtime/runtime.rxfs` `runtime_allocate_prepare` |
| release | `rxf_runtime_release` exists as a C-ABI function but **is not a leaf** (leaf ids stop at 49); it clears the row and returns no memory | `native/stdlib_runtime.h`, `native/graph.h` |
| pins and compaction | `pin`/`unpin` are leaves 14/15 (`pin_count` of the row); the sliding pass of docs/20 S8 and docs/17 (decision 7: compaction, not free lists) skips pinned cells. The host runner has **no heap at all** (`runner.c` sets no `heap_*`, so `allocate_prepare` refuses there); `tests/graph_world.py` cases do carry a context with heap and journal | `tests/host/runner.c`, `tests/graph_world.py` |
| two physical models of "an object" | the **runtime context** (`rxf_runtime_context`, 144 bytes, ABI 2: sorted 80-byte entries id/generation/type/payload/size/capacity/state/borrows/pins/owner) that `graph.rxfs`, `runtime.rxfs`, `compile.rxfs` and the host runner use; and the **v7 slot table** (docs/32 4) that `boot/image_stub/entry.c`'s mini interpreter (MOVE, RETURN, REFUSE, CALL of shape 6 only) and the live engine use. This design is written against the first; the second is named in section "Open" | |
| the machine budget (measured 2026-10-31, after the expansion of this lane) | the run cell is **16384 bytes** (`RXF_GRAPH_CELL_BYTES`) and a call chain at most **16 links** (`RXF_GRAPH_CALL_DEPTH`), each link 88 bytes (11 words, `rxf_call_frame`), each frame variable-length and at most the cell itself. The budget is parameterized inside the language (`m_cell_bytes`, `m_call_depth`, `m_link_bytes`, `m_frames_bytes` of `native/graph.rxfs`) and as the `RXF_*` numbers `native/graph.h` compiles with; `tests/graph_programs.py` pins the two together by the refusals it provokes (a 16400-byte frame and a chain past 16384 bytes answer `RXF_REFUSE_CELL_BUDGET`, sixteen links answer `RXF_REFUSE_CALL_DEPTH`, and 16080 bytes of chain run). The boot carrier gives its guest one **16 KiB frame** at `RXF_BOOT_FRAME` (`0x8800`/`0x4000`), a 2 KiB FIFO at `0xC800` and an 8 KiB entry cell at `RXF_BOOT_ENTRY` (`0xD000`/`0x2000`) | `native/graph.h`, `native/graph.rxfs`, `boot/image_stub/world_v7.h`, `tests/graph_programs.py` |

So the minimum that makes `Foo(...)`, `self.f` with `self` a parameter, and a node holding another
node's handle lowerable **under S7** is two things, and only two: an operand that resolves a held
handle *inside the instruction that uses it*, and a single-step allocation (plus its inverse) that
needs no transaction and no caller-chosen id. A strand that is content with the single-carrier,
no-compaction reading of S7 could lower dynamic objects today as `runtime_resolve` + `memory_copy`
with no machine change; this lane exists because the policy of the project is the SMP one.

#### Step 0, run: what the tree said (2026-10-31, the machine lane's worker)

The premises were checked against the tree before any code. Seven needed a correction and one is
confirmed (the last item); the corrections replace the sentences of this section they touch (the
sections that follow are already written with them).

1. **`runtime_release` is not a new Function.** `fn runtime_release(c, h_id, h_gen, h_off)` is
   already in `native/runtime/runtime.rxfs`, with a row in `seed_build.SOURCES` (`!pH`), the letters
   `pH` in `tests/scenario.py` `SPEC`, a place in `native_build.RUNTIME`, a Function
   `foundation.runtime.release` in the starter, and steps in the scenario that prove it on three
   machines. It already does what the design asks of the leaf (the refusals 10/11/12/13, the row
   back to state 0 with `payload`, `size` and `capacity` cleared and the generation kept). Leaf 51
   is therefore **the id of the function that exists**: step 4 adds one fn (`runtime_allocate`),
   not two, and there is no `runtime_release_leaf`.
2. **`tests/rxf_sim.py` is not an interpreter of contexts.** It runs flat memory with the numeric
   and the memory leaves; it has no entry table, no runtime leaf and no generation. "Learns kind 3
   and the leaves 50/51" reads as: it **refuses** a HANDLE operand by name with
   `RXF_REFUSE_UNSUPPORTED` (16, the answer the compilers give) instead of reading it as a slot,
   which an unknown kind did before. The simulator of runtime leaves is the host seat
   (`tests/host/runner.c`), which gains the heap, the pool and the slide below.
3. **A leaf the header names must not break an image that does not wire it yet.**
   `tools/native/link.py` (`_leaf`) and `tools/native/wasm_table.py` answer an error for any leaf
   of `graph.h` that the image's `graph.run` has no `Leaf` reference for. With ids 50 and 51 in the
   header and the starter regenerated last (step 9), that would break every seat that links the
   starter in between -- the boot smoke, the live engine's twin, the wasm32 table. The rule that
   lasts: **an image's slot table (and its wasm32 table) lists the leaves its `graph.run` wires**;
   an image may wire fewer leaves than the header names (a seat that runs no dynamic guest needs
   no allocator). After step 9 the two lists are equal and nothing changes.
4. **The tests that hard-code the leaf count are step 9's, not step 4's.** `tests/test_compose.py`
   (`range(1, 50)`, "the leaves of the interpreter are the ids of its header"), `tests/test_rxfb.py`
   (`len(codes) == 49`), `tests/test_rxfb_rust.py` (`SLOTS = 1..49`, "0 and 50 are not leaves") and
   `tests/test_graph.py` ("the runs reach every leaf") read the **starter**; they move when the
   starter gains the Functions that wire 50 and 51.
5. **`b` of a HANDLE operand in a composed program is `RXF_GRAPH_NONE`.** The marker type
   (`ObjectHandle`) is untyped as to what it points at, so the composer cannot know the width an
   operand denotes where it resolves; the readers that consult `b` take `min(width, b)`, which is
   `width`, and every reader bounds the access by the row's size anyway. A hand-written program
   may name a smaller `b` (MOVE then copies no more than that).
6. **"A call-card argument that is a handle" is decided by the callee's parameter width**: the
   run-entry shape says a handle is three words, so a parameter 24 bytes wide is in a handle
   position and a HANDLE operand there answers 6; a scalar parameter reads **through** the handle
   (the callee gets the word the field holds).
7. **The allocator cell has four words and the leaf writes one of them.** `pool_first_id`,
   `pool_count`, `next_id` (the scan starts here and wraps once; the leaf writes `id + 1`), and
   `generation`, reserved for the growth of docs/17 phase 2 -- the leaf neither reads nor writes
   it. The numbers are in `graph.h` (`RXF_ALLOCATOR_*`).
8. **Verified as written:** capability id 3 is unused (`graph.h` lists 1 and 2; `link.py` binds
   those two; the starter has no `RXF_CAPABILITY_*`); a seat context already reserves free
   rows (`link.py` `SPARE`, ids 1..8, state 0 -- the pool of a seat can start there); the host
   runner and `graph_world` build their own tables, so they gain the pool and the capability
   themselves; the boot mini interpreter (`boot/image_stub/entry.c`) already answers
   `REFUSE_RANGE` for every operand kind it does not know, which is what the pin of step 8 states.

#### The operand (what the lane builds first)

`RXF_OPERAND_HANDLE = 3`. An operand is three words `(kind, a, b)` as before:

| word | meaning for `HANDLE` |
|---|---|
| `a` | the byte offset **in the frame** of a 24-byte handle (id, generation, offset), exactly like a `SLOT`'s offset; must satisfy `in_frame(a, 24)`, else status 6 |
| `b` | the width the operand denotes at the address it resolves to (the role `OBJECT`'s `b` has: `MOVE` copies `min(width, b)` bytes of it) |

It is resolved by the **one** function every reader of the format already shares, `m_locate`, so
`MOVE`, `FIELD`, `CONSTRUCT` sources, `LESS`, the pointer parameters of a leaf call and the
`m_load` of a pointer argument all gain it at once:

```
locate(HANDLE a b, need):
    h = frame[a .. a+24)                         # (id, gen, off); a refusal here is status 6
    e = entry(c, h.id)                           # the same halving search as OBJECT
    e == 0                       -> 13 UNBOUND
    e.generation != h.gen        -> 12 STALE_GENERATION     # never rebinds to a reused row
    e.state != LIVE(1)           -> 13 UNBOUND              # a PENDING row is not readable here
    h.off > e.size               -> 6
    need > e.size - h.off        -> 6
    where = e.payload + h.off
load(HANDLE)   = where             # as OBJECT: the payload address (for a pointer parameter)
```

Rules the readers enforce, each one a pin of step 2:

1. **Resolve at use, never cache.** The halving search runs for every use of the operand. Nothing
   writes the resolved address into a slot: an operand that must become a *value* in a slot is
   moved with `MOVE` (the bytes), never its address. The only place an address exists is the
   operand of the **same** instruction (a pointer parameter of `memory_copy`, a `FIELD` source).
2. **Handle position refuses.** A leaf parameter that is a handle (shape letter `H`, `cl_ish` in
   `m_call_leaf`) and a call-card argument that is a handle take the three **words** of a slot or of
   an `OBJECT` payload. A `HANDLE`-kind operand in those positions would be a dereference where a
   value is meant: the interpreter answers status 6, and the composer never emits it (see below).
3. **No `HANDLE` destination.** Destinations are slots; the format has no write-through-operand.
   A field is written with `memory_copy` whose pointer parameter is a `HANDLE` operand (the same
   `store` idiom `rxpy` uses on flat memory), a field is read with `FIELD` from a `HANDLE` source.
4. **The compiler refuses, explicitly.** `compile.rxfs` bakes `OBJECT` addresses at compile time,
   which a handle in a slot cannot be. `m_check`/`m_value_check` answer `RXF_REFUSE_UNSUPPORTED`
   (16, "left to the interpreter") for kind 3 instead of the accidental 6 of an unknown kind. The
   three machine backends gain **nothing** in H1; a later lane may emit the entry search inline.
5. **The boot mini interpreter is unchanged.** `boot/image_stub/entry.c` refuses every operand
   kind it does not know with `OUT_OF_RANGE` already; a pin makes that a stated contract.

How a graph says "dereference" (the marker). The semantic graph has per-input type references
(`InputType`, written by `rxemit._operation`, ignored by `compose.py`). The marker is **an input
whose `InputType` is `pymergetic.rxf.stdlib.types.ObjectHandle`** (a 24-byte type the starter must
gain: today only `FixedArrayU8Of16` exists, which is why `rxpy`'s out handles are two words, identity
and offset, the generation being the cell's own). The composer rule, identical in `tools/native/
compose.py` and in the in-image composer `native/compose/*.rxfs`: a value of type `ObjectHandle`
that is an input of a leaf in a **non-handle** position, or the source of a `FIELD`, is emitted as
`HANDLE`; in a handle position or as a call-card argument it stays a `SLOT`. No core enumeration
changes (no new `ValueKind`, no new `RefKind`), which keeps `core/**` out of the lane.

#### Composition with S7 and S8

* **S7.** The claim of a resolve is the claim of the instruction: in the context model there is no
  claim word (one carrier); in the SMP product (`tests/host/smp_*.c`, docs/20) the interpreter takes
  the claim of the entry for the duration of one instruction and releases it before the instruction
  ends. The invariant list the proof reads: no address in a slot after an instruction; a frame holds
  identities, never addresses (`m_rebind` already rebinds the program words of every live frame
  after a safe point, so a handle in a frame needs nothing more); a stale generation is refused,
  never rebound.
* **S8 (the sliding pass).** A slide changes `e.payload` of the rows it moves and nothing else a
  graph can see; because a `HANDLE` operand reads `e.payload` at each use, a program that holds a
  handle across any number of safe points keeps working, and one that holds only the handle survives
  a slide **between two instructions of one run** (the mid-flight case of S8). A pinned row
  (`pin_count > 0`) is skipped by the pass: the pbuf case of decision 4 allocates with the pin flag
  below and releases it explicitly. Compiled code is the exception that proves the rule: it bakes
  `OBJECT` addresses, which is why H1 keeps `HANDLE` out of the compilers.
* **What the handle operand does not do.** It is not an `Object` reference in the image (no new
  object kind): a handle is a run-time value of a frame. Cross-run persistence of a handle is the
  `REFERENCE` field of docs/17 (export turns it into a path), unchanged.

#### The allocation leaf

Two leaves, both with **shapes the machine already has** (so `shape_of`, `m_shape_of`, the plan of
`compile.rxfs` and the Seed's shaped calls grow nothing):

| leaf | id | shape | parameters after the context | answers |
|---|---|---|---|---|
| `runtime_allocate` | 50 | `QQQQQPO` (`call_pqqqqqpp`, the shape of `allocate_prepare`) | `type_id`, `initial_size`, `capacity`, `align`, `flags`, a pointer that must be 0 (the slot the transaction occupies in `allocate_prepare`), out: the 24-byte handle | status 0 and the handle |
| `runtime_release` | 51 | `H` (`call_pH`, the shape of `pin`) | the handle | status 0 |

`runtime_allocate` is `allocate_prepare` + `publish` in one step on a row the leaf picks itself:

1. checks as `allocate_prepare`: valid context (6 on a bad ABI), `type_id != 0`, `initial_size <=
   capacity`, `align` a non-zero power of two (all 6), `flags` has no bit above bit 0 (6; bit 0 is
   *pin*), the pointer parameter is 0 (6);
2. the **allocator state** is a cell (the `AllocatorState` of docs/17 phase 2: `pool_first_id`,
   `pool_count`, `next_id`, `generation`, all words), reached through a **capability**
   `RXF_CAPABILITY_ALLOCATOR = 3` of the context's capability table (ids 1 channel and 2 uart are
   taken). The context struct is untouched: **no ABI 3** (the object of docs/17 decision 8, which stays
   open for growth, and of the twin lane's pins). No such capability bound -> status 13 UNBOUND;
3. the id is the first row of the pool `[pool_first_id, pool_first_id + pool_count)` with state 0,
   scanning from `next_id` and wrapping once; the pool rows exist in the sorted table and are
   reserved free by the context builder; none free -> 9 OUT_OF_MEMORY;
4. the payload is carved from the same bump region `allocate_prepare` uses: `p =
   align_up(heap_frontier, align)`, `capacity <= heap_limit - p` else 9 (the checks and the order
   of `allocate_prepare`, including `p < heap_frontier` for the wrap); `heap_frontier = p +
   capacity`; **the payload is zero-filled for `capacity` bytes** (a Python object's fields are
   deterministic words on every machine; `allocate_prepare` does not zero, the leaf must);
5. the row becomes `generation + 1`, `type_id`, `payload = heap_base + p`, `size = initial_size`,
   `capacity`, state **LIVE (1)** (no journal row, no PENDING window), `pin_count = flags & 1`;
6. `*out = (id, row.generation, 0)`; `next_id = id + 1`.

`runtime_release` is `rxf_runtime_release` made a leaf with the same refusals (`BORROW_CONFLICT`
10 while borrowed, `PINNED` 11 while pinned, `STALE_GENERATION` 12, `UNBOUND` 13) and one more
effect: the row returns to the pool (state 0, `payload`/`size`/`capacity` cleared, `generation`
kept so the next allocation bumps it and a stale handle is refused with 12). **No memory is returned
by a release**: "compaction, not free lists" (docs/17 decision 7) means the sliding pass reclaims
the dead payload; `heap_frontier` only moves down when the pass runs. A guest that wants to free a
pbuf unpins it first (`runtime_unpin`, leaf 15), then releases it.

`allocate_prepare`/`publish`/`rollback` are **unchanged and stay the transactional writers' API**
(the live engine's write side): the lane must prove their pins byte-identical.

#### Prove matrix (what "done" is)

| layer | what runs | where | oracle |
|---|---|---|---|
| operand semantics | every rule above as a table of hand-built programs: ok, stale generation (12), unbound id (13), PENDING row (13), offset or width past the size (6), handle slot outside the frame (6), `HANDLE` in a handle position (6), `FIELD`/`MOVE`/pointer-parameter sources; a handle of a reused row after release | new `tests/test_graph_handle.py` through `tests/graph_world.py` cases | `tests/graph_reference.py` (written first, red first), then `rxf_graph_run` as x86_64 and aarch64 in the emulator and as wasm32 in node: status, out and the whole memory equal, the table of `tests/test_graph.py` |
| S8 mid-flight | a program that holds a handle, calls the safe-point leaf (a pump that slides one registered cell and rewrites its row), and goes on reading and writing through the handle | `tests/host/runner.c` gains a slide mode of its pump; the oracle's pump hook does the same move | host seat vs oracle byte for byte (the other machines run the non-sliding matrix: their pump is the seed's) |
| allocation | allocate -> write -> read -> release -> allocate again (generation +1, the old handle refused 12); exhaustion of the pool (9) and of the heap (9); pin flag (release refused 11 until `unpin`); alignment; zero fill; ids deterministic; `allocate_prepare` pins unchanged | new `tests/test_runtime_allocate.py` over `tests/graph_world.py` (heap, journal and pool rows added to its context) | the oracle plus equality of the whole memory across the three machines |
| the compiler | a program holding a `HANDLE` operand: `compile.program` answers 16 on all three machines and the interpreter runs the same program to the same answer | `tests/test_compile.py` (new cases) | interpreter on the same machine |
| composer parity | the `InputType` marker: `ObjectHandle` in a non-handle position composes to kind 3, in a handle position it stays a `SLOT` (kind 0); the in-image composer reads and writes the same words | `tests/test_compose.py`, `tests/test_compose_corpus.py`, `tests/test_compose_image.py` (new cases) | `tools/native/compose.py` |
| the strand (a later lane, same prove) | dynamic instances, objects as arguments, a linked node, `self` a parameter: differential against a real MicroPython | `tests/test_rxpy_dynamic.py` | MicroPython (decision 10) |
| seats (all-platforms) | the interpreter is seat code of the three machines (x86_64, aarch64, wasm32 -- the `unix`/`emcc`/`firmware` rows of the platform rule run no graph of their own); the boot smoke (`boot/run_smoke.sh`, four seats) must stay green and `entry.c` refuses kind 3 with `OUT_OF_RANGE` (a pin) | `tests/test_graph.py`, `boot/run_smoke.sh` | |

#### The files, in order

0. **Verify the premises** (an hour, no edit): that `RXF_CAPABILITY_ALLOCATOR` 3 is unused
   (`native/graph.h` lists 1 and 2; grep the starter's `capabilities.rxfd`), that the sorted entry
   table of a seat context can carry reserved free rows (`tools/native/link.py` builds it; the
   host runner and `graph_world` build their own), and that the pins named in step 9 are the
   only ones the new leaf ids move.
1. `native/graph.h`: `RXF_OPERAND_HANDLE = 3`; `RXF_LEAF_RUNTIME_ALLOCATE = 50`,
   `RXF_LEAF_RUNTIME_RELEASE = 51`; `shape_of` maps 50 to `RXF_SHAPE_QQQQQPO` and 51 to
   `RXF_SHAPE_H`; `RXF_CAPABILITY_ALLOCATOR 3ULL` with the layout of the allocator-state cell;
   the comment block that documents operands and refusals. (`tools/native/graph_header.py` reads
   the numbers; the Seed knows `graph.h`'s constants already, no table to sync.)
2. **Red tests first**: `tests/graph_reference.py` and `tests/rxf_sim.py` learn kind 3 and the
   leaves 50/51 (their leaf tables), then `tests/test_graph_handle.py` and
   `tests/test_runtime_allocate.py` are written against the oracle and fail against the interpreter.
3. `native/graph.rxfs`: `m_locate` (kind 3, the pseudo-code above), `m_load` (kind 3 like kind 2),
   `m_shape_of` (50 -> 5, 51 -> 8), `m_call_leaf` (a kind 3 operand in a handle position -> 6).
   `m_move`, `m_store` and `m_pointer` need no edit: they all go through `m_locate`/`m_load`.
4. `native/runtime/runtime.rxfs`: `fn runtime_allocate(c, type_id, initial_size, capacity, align,
   flags, zero, out)` and `fn runtime_release_leaf(c, h_id, h_gen, h_off)` reusing the macros
   `rt_ctx`, `rt_entry`, `rt_access_plain`; `tools/native/seed_build.py` (the `SOURCES` row and
   `LEAF_LETTERS`: `"pqqqqqpp"` and `"pH"`) and `tests/scenario.py` (`SPEC` letters).
   `tools/native/link.py` takes the function slots from `graph_header.leaves()` (count 49 -> 51), so
   `tests/test_rxfb.py` / `test_rxfb_*` (which pin "function slots 1..49" and compare the walk to
   the engine) move with it.
5. The writers of the format: `tools/native/compose.py` (the `InputType` marker -> kind 3; `Operand`
   gains the kind) and its twin in `native/compose/*.rxfs` (byte-identical parity is a test);
   `tools/native/rxemit.py` only gains the constant `OBJECT_HANDLE` of the type path (additive,
   separate commit, as every rxemit edit); the decoders `tools/native/rxfgraph.py`, `tools/native/
   rxfc.py` and the inspector's graph views learn to print kind 3.
6. `native/compile.rxfs`: `m_check` and `m_value_check` answer 16 for kind 3 (and a pin in
   `tests/test_compile.py`). `native/tiny/*.rxfs` (the other compiler) takes the same constant
   through its `words.rxfs` and refuses the same way.
7. The contexts that run programs: `tests/host/runner.c` (a heap region, pool rows, the allocator
   capability and the slide-mode pump), `tests/graph_world.py` (pool rows and the capability in
   its context), and, for the seats that will run dynamic guests, `tools/native/link.py` /
   `tools/native/boot_image.py` (reserve the pool rows and bind the capability; a seat that does
   not run such guests binds none and the leaf answers 13).
8. `boot/image_stub/entry.c`: no code change; a test pins that kind 3 answers `OUT_OF_RANGE`.
9. **The starter and its pins**, only after the twin lane has landed: a Foundation Function for
   each of the leaves 50 and 51 and the `ObjectHandle` type in `starter/pymergetic/rxf/stdlib/
   types.rxfd`, then `tools/native/boot_sync.py`, `tools/native/live_sync.py`,
   `tools/native/graph_sync.py`/`blob_sync.py` in the order `tools/native/sync.py` runs them, and
   `tests/test_boot_codes.py`'s pins in the **same commit** (the pattern of `31fb454`). `graph.run`'s
   Code changes (the interpreter is a Code object of the starter), so every `body_entry_*` pin and
   the receipt digest move: that is why this step is last and why it waits.
10. Docs: docs/20 S6/S7 (the operand as policy: no address survives an instruction), docs/18 (the new
    leaves in the snippet list), and this section's table in section 7 flips from *design* to
    *landed*.

Commit shape: one commit per numbered step from 1 to 8 with its prove numbers (a red test commit
is allowed to precede its green partner only on a branch that is not merged), step 9 alone.

#### Step 10: the value words — the None word (landed 2026-10-31)

The word model (`native/graph.h`) has a value for `None` that is not `int 0`, so `is` / `is not`
None is a readable word test and `= None` defaults and `None`-as-value work (the `ph_meld`,
`ph_pairing`, `run_until_complete` and `open_connection` rows of the census below). The shape is
a reserved word in the frame-word space, with the int range declared around it:

* an **int word** is a word whose bits 63 and 62 are the same -- the sign-extended 63-bit word.
  The int range the data model exposes is `[-2^62, 2^62 - 1]`, exactly where a MicroPython small
  int of a 64-bit port is the same number as the word (section 6, decision 10: MicroPython is
  the reference).
* **`RXF_WORD_NONE` is `0x4000000000000000`** (bit 62 set and no other): the first word above
  the int range. None is a **singleton** -- `is` / `is not` lower to word equality with it -- and
  no int word equals it, so the read is exact for every value in the range. It is not the word of
  `int 0` (the wall the census hit) and it is not `1 << 63`: that word is what a signed compare
  lowers through (the bias add of `rxpy.py`) and it stays out of the value faces.
* the words the int range does not take and None does not name are **wrap words** -- what a wrap
  leaves where MicroPython answers a bignum (section 6's one-sided pins). They are raw words to
  the numeric leaves and no Python value.

An **absent argument binds the None word**. The run entry (`rxf_graph_run`'s argc, the shape
run-entry ARGS takes) fills every word of the parameters the arguments do not reach with
`RXF_WORD_NONE`, so a `= None` default folds to the very word a caller passes explicitly and a
callee cannot tell the two apart (which is what Python means). A parameter whose width is not a
whole number of words is refused `OUT_OF_RANGE` where its argument is absent: the None word is a
frame word and is never cut. A **call card is never short** -- its operand count is program text,
and a count that is not the callee's parameter count is refused 6 -- so a lowerer that stages a
default stages the None word as the literal it is.

Wired on every seat in one change (the module/Python/C face is `native/graph.h`, one header):

* the interpreter (`native/graph.rxfs`, its one defining source) on host C
  (`tests/host/runner.c`), x86_64 and aarch64 (the emulators of `tests/graph_world.py`) and
  wasm32 in node: `tests/graph_programs.py`'s `none is none`, `none is not int 0`,
  `none default`, `none default passed` and `none value` run on all of them against the Python
  oracle (`tests/graph_reference.py`, which binds the same words).
* the boot entry (`boot/image_stub/entry.c`, the subset run the four boot images share): a seat
  answers the None word for the parameter its one argument word does not reach
  (`tests/test_boot_words.py`, the wasm32 seat under node -- the entry is shared, so the other
  seats compile the same code; `tests/test_boot_handle.py` holds the `RXFOK` control).
* the sims the suites run (`tests/rxf_sim.py`, `tests/rxf_native.py`) take the same rule.
* the compiler's emitted entry stub (`native/compile.rxfs`, the `m_be_param_x`/`_a`/`_w` of its
  three backends): each parameter word is the argument word where `argc` reaches it and the
  None word else -- one branchless select per word (`cmovbe`, `csel`, `select`), and a
  parameter of a width that is not whole words is given whole or refuses 6 where the prologue
  refuses an `argc` above the words. `tests/test_compile_cases.py`'s `none default` case is the
  prove: the same case runs on the interpreter and on the compiled code and both answer the
  None word.

The machine face is the word and the equality that reads it; the lowering is the strand's
(`tools/native/rxpy.py` owns `int_face` and the call sites): a default is staged as the
`RXF_WORD_NONE` literal and `is None` is `equal_uint64_t` against it. No opcode and no leaf was
added: a program that can compare words can read None.

#### After the machine lane (the strand's half, not part of it)

`rxpy` stops monomorphizing methods per instance: `self` becomes a `Parameter` of type `ObjectHandle`
and the method is compiled **once per class**; `Foo(args)` becomes `runtime_allocate` + the field
stores; an object passed to a function is a three-word argument; a node holding another node is a
24-byte field copied with `memory_copy`. `None` is the word of step 10 (`RXF_WORD_NONE` of
`native/graph.h`): a value slot of any type reads it and `x is None` is an equality with the word,
so no type tracking is needed to tell it from `int 0` (the zero-handle plan this paragraph used to
carry is gone with it). A handle that is None is not dereferenceable -- every word of it is the
None word and no row has that id, so a resolve answers `UNBOUND`, the same honest refusal the zero
handle had. Inheritance and
`isinstance` are i3 function words over a type id in the object's row (`type_id` is already a row
field): a separate lane.

#### Open (named, not decided here)

* **The v7 slot table.** The handle operand is specified against the context's entry table. The boot
  seats resolve through the v7 slot table (`entry.c`'s `bind`: index/generation packed in word 0).
  A seat that runs this operand on the superblock directly needs the same rules with the slot
  index and generation of docs/32 4; the mini interpreter refuses kind 3 until then.
* **Growth of the heap** (docs/17 decision 8): the leaf bumps `heap_frontier` inside `heap_limit`
  as `allocate_prepare` does; dynamic growth stays the ABI 3 question and needs no change here.
* **Claims in the SMP product**: the interpreter of `tests/host/smp_*.c` models claims, the
  context-model interpreter has none; the instruction-scoped claim above is the contract the SMP
  seat must satisfy when it runs graphs.

## 5. Provenance: both import strands

One machinery, one knob per import run, two entrances (docs/22 extends verbatim; only the
citation source differs). Both strands land in **one** module tree, and a hybrid package may
carry both provenances side by side.

| level | the C strand (`.c`/`.cpp`/`.rs` → `.ll`) | the Python strand (`.py`) | size |
|---|---|---|---|
| `full` | origin record + citations + the source (lz4) | same, and the source Doc carries the marker *was once Python* | big, on demand |
| `cite` (default) | origin record + citations: `path + line range (+ content hash)` from the debug info — a permalink into the pinned commit | origin record + citations: line ranges from the AST (a `.py` **is** its own source map) | a few dozen bytes per object |
| `hash` | only the content SHAs of the imported source files | only the SHAs — plus the marker, which is cheap and permanent | one hash per file, shared |

- **Origin record** (from `cite` up), one Doc per source set: upstream name, repository permalink,
  pinned commit, license, importer version. For lwIP it is the upstream repo; for MicroPython's
  `py/*.c` and `extmod/uasyncio` the pinned tree.
- **Citation per object** (from `cite` up): a `RefKind.Doc` placement onto the file's provenance
  Doc with a locator. The C strand's locators come from `!DILocation`/`DIFile`; the Python
  strand's from the compiler's AST. Neither ever guesses.
- **The marker** *was once Python* is a fact at **every** level (docs/22: provenance is comment
  lifting at module granularity); the source bytes exist only at `full`. An object whose source
  compiled away is not undocumented — its Doc is right there, compressed (the codec tier:
  `DocCodec.Lz4` default, `raw` where it does not pay).
- The floor is `hash` for both strands: the importer takes no anonymous input, and the image says
  at which level its objects were claimed. Deltas preserve citations; an object edited later
  keeps its provenance and gains the edition that changed it (docs/15).

What the code shows today: `rximport.py` has neither the knob nor the debug-info parse (it strips
comments from IR text and nothing else), so both strands' provenance rows are design target until
the lanes land — the shape above is what their flags and Doc writes must satisfy.

## 6. Risks and open decisions

**All fourteen are decided or resolved (2026-10-15) — the ledger at the end of this section
records each.** The items below stand as the risk statement that prompted them.

1. **Value width.** The importer assumes 64-bit words ("the value-width policy for MicroPython's
   word (32- vs 64-bit) is the one open piece" — `rximport.py`). Most MicroPython ports run
   32-bit `mp_int_t`. Until this is decided, the breadth claim is x86-64/wasm32-shaped.
2. **The lowerers' frame budget is not flipped.** The run's variable-length frames landed
   (`RXF_GRAPH_CELL_BYTES` 16384), but `RXF_GRAPH_FRAME` (1024) still refuses at import:
   `qstr_from_strn` is 1608 frame bytes and stays refused ("needs a frame"), and qstr interning
   is on every builtin's path. `native/graph.h` names the flip "the importer lane's follow-up" —
   breadth is blocked on it.
3. **The wiring universe (i3).** A slot wired by one import run and read by another needs a link
   fact (`function_wiring` made durable); `known=` covers direct calls only. Without it the
   whole MicroPython import must be **one run** — possible, but it makes the per-module
   provenance knob awkward.
4. **lwIP's pointer discipline vs the moving arena.** lwIP caches `struct pbuf *` freely; the
   arena slides cells (S8) and S7 refuses a raw address across a safe point. Either i2 extends
   from out cells to general object pointers (resolve at use), or pbufs live in a pinned/
   non-moving region — one arena with discipline, or two memories. Decide before the card, not
   during it.
5. **Static initializers.** lwIP's structs of callbacks are relocations the flat profile refuses
   ("a relocation it cannot know", `global_reloc.c`); the `obj_port_boot` boot-wiring idiom must
   be generalized (a boot card per subsystem) or the card has no dispatch words to find.
6. **Recursion and call depth.** `calls.c` `ping`/`pong` still refuse as "recursion" although
   call cards land cross-file (`qstr_str` is a call card), and `RXF_GRAPH_CALL_DEPTH` is 16 links
   with `RXF_REFUSE_CALL_DEPTH` past it. C builtins that recurse (repr of nested structures,
   nested packages) need either graph-call recursion or a heap-frame story at the C level too.
   Note: the header's "next steps" list still names "graph calls" as missing — the tests say call
   cards land; trust the tests, and re-pin the recursion question explicitly.
7. **uasyncio semantics.** FIFO `call_soon` matches the ring, but threads
   (`run_in_executor`), timer resolution, and cancellation timing are unmodeled; the S10
   differential must cover task-level results before the mapping table above is claimed proven.
8. **The browser line.** SLIP over WebSocket needs a host-side gateway — who runs it (`rxf net
   gateway`?), and what serves B when no gateway is present? The alternative is `js.fetch` as
   B's fill, which leaves the lwIP card unproven on emcc and the platform rule unmet. Decide
   before wiring.
9. **The emitter question.** The Python strand must share one emitter with `rximport`
   (one-defining-lang) — either it calls `emit_delta` on IR-shaped graphs, or the emitter is
   extracted into a shared module. Extraction edits `rximport.py`, which other lanes own:
   sequencing decision, not a design fork.
10. **The Python differential.** The semantics contract must name the reference: CPython or
    MicroPython (they differ in integer width, small-int identity, `dict` order assumptions).
    "Same observable behavior as the function compiled natively" (the C contract) has no
    obvious Python twin until this is named.
11. **Inlined bodies and citations.** `graphs_of` inlines callees into callers; a citation must
    not claim the caller's lines for an inlined body. The lift cites the definition the body
    came from (per-function line maps survive inlining via debug info) or refuses the placement.
12. **No filesystem is a refusal.** A guest that expects CPython's `sys.path`/`open()` import
    gets an honest Refusal (the tree is the image, §4.6). Until the filesystem of decision 12
    lands, this is an honest stop — not a gap to paper over.
13. **Vocabulary drift.** The corpus/tests speak of "slices 4-5" for runtime dispatch and
    "slice 1" for a leaf; this design and the owner speak of i2/i3. Pin one vocabulary when the
    slices land, in the tests first.
14. **docs/19 is referenced but absent.** The self-editing path doc (README, docs/22) is not in
    the tree;     the Python strand's self-build story (the compiler compiling itself) depends on it.
    Track the lane; do not assume its content.

### The decisions (2026-10-15)

All fourteen are decided; two were answered by work that landed in the meantime.

1. **Value width — decided: dual policy from the start.** 32- and 64-bit operand widths are
   both first-class; the importer's operand policy carries the width. No deferral — the
   breadth claim stops being x86-64/wasm32-shaped when the lanes land.
2. **Frame budget — resolved by landed work.** Frames measure against `RXF_GRAPH_CELL_BYTES`
   (16384, expanded 2026-10-31); `qstr_from_strn` (1608 B) is its own program and no longer refused for a frame.
3. **Wiring universe — decided: a link fact.** `function_wiring` survives import runs as
   objects; a boot and its bodies may be separate imports, so the per-module provenance knob
   stays clean.
4. **lwIP pbufs — decided: pinned cells.** One arena: a pbuf is a pinned cell the sliding
   pass (S8) skips (`foundation.heap.pin`/`unpin`); no second memory, no resolve-at-use tax.
5. **Static initializers — decided: the importer learns them.** Real relocation support in
   `rximport.py` for structs of callbacks; the `obj_port_boot` idiom remains the fallback
   shape, not the only door.
6. **Recursion — decided: one frame model.** C recursion uses heap frames like Python calls
   (no frame-link ceiling for it); the budgets stay honest (`RXF_GRAPH_CELL_BYTES`, the cell
   budget) and a refused chain is still a budget refusal, never a stack overflow.
7. **uasyncio semantics — decided: model everything.** The run model unifies threads and
   async (S11 is no-GIL): `run_in_executor` spawns a run cell — no OS threads, no second
   scheduler — timers and cancellation timing are modeled, and the S10 differential covers
   task-level results.
8. **The browser line — decided: `rxf net gateway`.** SLIP over WebSocket to a host gateway
   (the router the browser dials); no gateway running is an honest refusal on seat B. A
   `js.fetch` fill is not the card's proof and does not replace it.
9. **The emitter — decided: extract now.** One shared emitter module; the window is open
   (i2/i3 landed, no lane owns `rximport.py`), and the Python strand calls the same emitter.
10. **The Python differential — decided: MicroPython is the reference.** Observable behavior
    equals MicroPython's, not CPython's (int width, small-int identity, dict order included).
11. **Inlined bodies and citations — decided: cite the caller's lines, marked as such.** An
    inlined body's locator carries an inline marker naming the caller's lines; the citation
    never silently claims them as the definition's.
12. **The filesystem — decided: it is coming, as an RXF object world.** Files are cells,
    mounts are subtrees (the module tree's shape); `open()`/`sys.path` resolve honestly over
    it, and rump/virtio-blk drivers back real storage under it later. Until then guests get
    honest refusals — a temporary stop, not the design promise it was drafted as.
13. **Vocabulary — decided: i2/i3 is canonical.** The renaming pass landed in the tests
    (2026-10-15); the "slices 4-5"/"slice 1" wording is retired there.
14. **docs/19 — resolved by landed work.** `docs/19-SELF-EDITING-PATH.md` is in the tree
    (verified 2026-10-15; its links resolve).

## 7. Measured status of the pieces (2026-10-31)

The design above was written before several lanes landed. This section is a **measurement**, not
a plan: for every piece of docs/23, whether code exists, where, which test proves it, and the gap.
It supersedes the prose of the status block and §5 ("`rximport.py` has neither the knob nor the
debug-info parse") where they disagree: the `--provenance` knob, the Python strand and the shared
emitter exist (below); the debug-info citation does not.

| piece | exists? | where | proven by | gap |
|---|---|---|---|---|
| (a) lwIP card, SLIP/netif bridge | in flight (another worker; not measured here) | `third_party/lwip`, `native/lwip`, `tools/native/lwip_import.py` (uncommitted) | — | everything past the vendored import |
| i2 out cells | **landed** | `rximport.py` `lower()`, `native/graph.h` | `tests/test_import.py` (`test_the_out_param_callee_is_a_call_card`) | — |
| i3 runtime dispatch | **landed** | `rximport.py` `lower_indirect`/`function_wiring`, `native/graph.c` | `tests/test_import.py` (`test_the_unary_slot_dispatch_routes_to_the_type_body`) | — |
| MicroPython seam model (S11) | **landed** (a model, not MicroPython) | `tests/host/smp_mpmodel.c`, docs/20 S11 | `tests/test_smp.py` (10 passed) | the real interpreter does not run on the image yet |
| value width (decision 1) | **landed** | `rxemit.DataModel`, `rximport.module_model` | `tests/test_import_width.py` | — |
| shared emitter (decision 9) | **landed** (extracted) | `tools/native/rxemit.py` (`emit_delta`; `rximport` and `rxpy` both call it) | `tests/test_import.py`, `tests/test_rxpy.py` | — |
| Python-import strand (4.3) | **landed, a subset** | `tools/native/rxpy.py` (`import_py_file(image, source, module, known=, provenance=)`; ints, `if`/`while`/`break`/`continue`, calls with default parameters and keyword arguments, tuple returns as out cells, procedures (a def that answers None), module cells, tables (tuple / list / bytes data cells), imports of modules of the image, simple classes as named instance cells) | `tests/test_rxpy.py`: 2253 passed (both MicroPython seats, 1076 in-domain differential pins and the one-sided out-of-domain pins per seat, 91 shape/refusal pins); `tests/test_rxpy_imports.py` 202; `tests/test_rxpy_objects.py` 43; `tests/test_rxpy_subset.py` 255 | no `try`, `raise`, `for`, generators, `async`/`await`, strings, floats, `None` as a value, slices, dict/set; an instance is a named cell only (nothing made at run time, no object in a name or a parameter) — see the census below |
| imports as a lookup of named cells (4.6, compile side) | **landed (2026-10-31)** | `rxpy.Resolver` (reads `Image.resolve_module`, the children of a `Module` cell, a Function's Signature), `bind_import`, `nest` (dotted modules as nested Modules, `rxf:parent` onto an existing package), `rxemit.LValue.foreign` | `tests/test_rxpy_imports.py` (202 passed on both MicroPython seats: the reference IMPORTS the corpus `tests/rxpy_corpus/imports` with its own machinery, packages and relative imports included; 18 named refusals) | a cell no def reads or writes is not in the image, so it cannot be imported (the emitter writes cells from their uses); the guest side is the next row |
| simple classes (1, 4.3) | **landed (2026-10-31)** | `rxpy.declare_class`/`declare_instance`: `NAME = C(constants)` is one named cell of the fields' words, a method is a graph per instance over that cell | `tests/test_rxpy_objects.py` (43 passed on both seats: three call streams incl. methods called directly as `m.a.bump`, layout, 30 named refusals) | no run-time construction and no object held in a name or a parameter: the graph machine's one cell operand (`OBJECT`) names a cell by a constant id, no operand resolves a handle held in a slot (docs/23 4.1 "resolve at use", risk 4) and no allocation leaf exists; no inheritance; an instance of another module is reached through that module's functions only (the layout is not in the image) |
| in-subset closures (2026-10-31): defaults, procedures, tables, every cell emitted, probed classes | **landed** | `rxpy.py`: `Sig.defaults/names/procedure`, `return_shape`, `DataCell`/`table_of`, `with_cells`, `probe_classes`, `compile_module` | `tests/test_rxpy_subset.py` (255 passed on both MicroPython seats: pure cases, three call streams, one-sided out-of-domain pins for a table index past the length, 31 named refusals) | `None` has no word (a word cannot be told from the int 0, so `is None` would answer wrong words): only the absence of an answer is representable (a procedure: a Signature with no Result), so `NAME = None` cells and `None` as a value stay refused; an imported function carries neither parameter names nor defaults (the Signature has none), so it takes its words by position; a table of another module is not readable by an importer (its length is not in the image); strings have no operation in the subset |
| Python differential (decision 10) | **landed** | `tests/rxpy_mp_answer.py` runs the corpus under a real MicroPython unix port | `tests/test_rxpy.py`. Until 2026-10-31 it **skipped silently in every worktree**: the binary search used `parents[2]`, which is `worktrees/` there. `tests/mp_reference.py` now walks up to `packages/`; the 1086 pins run | — |
| provenance knob, `was once Python` marker (5) | **landed** | `rximport.Provenance`, `--provenance` of `rximport.py` and `rxpy.py` | `tests/test_import.py` (C import not marked), `tests/test_rxpy.py` (marker set, report shape) | per-object citations from debug info (`!DILocation`): the citation spans the whole file ("the importer never guesses") |
| comment lifting (4.7) | partly | `rximport.py` comment capture, `Provenance` | `tests/test_import.py` | the call-site contract of 4.7 as a separate tool |
| hybrid module tree (4.6), host side | **landed** | `Image.resolve_module`/`module_children`/`module_roots`, `src/pymergetic/rxftk/mount/` | `tests/test_module_tree.py` (10), `tests/test_module_tree_view.py`, `tests/test_mount.py` | — |
| `mp_import_stat` fill, guest side | **no** | none: no RXF hook in `packages/micropython`'s `py/builtinimport.c` or `packages/metalpython/extmod` | — | the guest import face on every seat (unix, emcc, firmware); needs the interpreter on the image |
| uasyncio: semantics of the mapping (4.5) | **landed as a model (this lane)** | `tests/host/smp_asyncio.{h,c}`, `smp_asyncio_prove.c`; corpus `tests/asyncio_corpus/` | `tests/test_smp_asyncio.py` (110 passed) | the model is not uasyncio compiled; see the next row |
| uasyncio: the source as imported graphs | **no** | `tests/test_rxpy_asyncio_gap.py` + `tests/rxpy_gap.py` pin the distance | the same file (9 passed): against the image as it is all six upstream `extmod/asyncio` files stop at their first import and the refusal names the module the tree lacks (`time`, the package `asyncio`) | the guest stdlib is not in the module tree; with the imports served the per-statement answer is the table below |
| S3 ready-ring binding of asyncio tasks | **landed as a model (this lane)** | `smp_asyncio.c`: a Task is a run cell, `create_task`/yield push `smp_ring_push`, an await is `smp_park`, a task end / `Event.set` is `smp_wake` on the run | `tests/test_smp_asyncio.py` (the reference differential and the S10 differential, below) | the product binding of `mp_sched_*` inside the compiled loop |
| world picture: tram count = ring occupancy (4.5) | **landed** | `server`/world export | `tests/test_world3d.py` (36), `tests/test_reader_js.py` | — |
| handle operand + allocation leaf (machine lane H1) | **design (4.9), nothing built** | `native/graph.h`, `native/graph.rxfs`, `native/runtime/runtime.rxfs`, `native/compile.rxfs`, the composers, the host runner (file list and order in 4.9) | — (the prove matrix is in 4.9) | waits for the twin lane's starter pins; unlocks instances made at run time and objects held in names or parameters |
| breadth: corpus (e) | partial | `tests/import_corpus` (48 digests), `tests/rxpy_corpus` (8 modules) | `tests/test_import.py`, `tests/test_rxpy.py` | MicroPython `py/*.c`, `extmod/uasyncio`, lwIP |

**The gap to uasyncio's source, measured before and after the imports / attributes / classes
lane (2026-10-31).** `tests/rxpy_gap.py` is the measurement (`PYTHONPATH=tools/native:. python -m
tests.rxpy_gap --write` regenerates `tests/rxpy_asyncio_gap.json` and prints the rows below from
the one run); `tests/test_rxpy_asyncio_gap.py` pins both and goes red when the strand learns or
loses a construct.

Before (HEAD 52909ad, AST node kinds in the six pinned `extmod/asyncio` files): imports 14,
classes 13, attributes 377, `try` 12, `raise` 18, `assert` 7, `del` 1, `yield` 11, `yield from` 1,
`async def` 13, `await` 9, `for` 4, comprehension 1, `nonlocal` 1, subscripts 51 — **every file
refused at its first statement** ("an import"), 0 of 15 top-level defs and 0 of 12 top-level
classes compiled (the 13th class sits inside a `try`), every one of the 38 methods refused.

After: import, class and attribute **left the unconditional blockers** (an import binds a module
of the image, a simple class has a layout and instances, an attribute of an imported module or a
named instance is a field or a cell); the kinds still blocking are `try`, `raise`, `assert`,
`del`, `yield`, `yield from`, `async def`, `await`, `for`, comprehension, `nonlocal`, subscripts
(same counts as before). Against the image as it is, **no file moves past its first import** — but
the refusal now names the module the tree lacks (`time`, the package `asyncio`) instead of "an
import": the guest stdlib is not in the module tree. With every import served **and the file's own
defs bound where the file binds them** (the measurement's `Served` resolver plus a `known` map of
the file's defs: a call of a sibling def names the function the file defines, so a def's refusal is
a construct of the def, not of the file's glue) the answer per top-level statement is:

<!-- rxpy_gap:begin (generated: python -m tests.rxpy_gap) -->
| file | statements of the file served | compiles (ok / refused) | first refusal against the image |
|---|---|---|---|
| `core.py` | 2 imports, 6 dropped | def 2/7, class 0/5, method 3/12 | line 4: from time import ...: the image has no module time (the image is the module tree: import it first) |
| `task.py` | 1 imports, 0 dropped | def 0/3, class 0/2, method 0/6 | line 7: from asyncio import ...: the image has no module asyncio (the image is the module tree: import it first) |
| `event.py` | 1 imports, 1 dropped | def 0/0, class 0/1, method 0/4 | line 4: from asyncio import ...: the image has no module asyncio (the image is the module tree: import it first) |
| `funcs.py` | 1 imports, 2 dropped | def 0/2, class 0/1, method 1/0 | line 4: from asyncio import ...: the image has no module asyncio (the image is the module tree: import it first) |
| `lock.py` | 1 imports, 0 dropped | def 0/0, class 0/1, method 0/3 | line 4: from asyncio import ...: the image has no module asyncio (the image is the module tree: import it first) |
| `stream.py` | 1 imports, 7 dropped | def 0/1, class 0/2, method 1/8 | line 4: from asyncio import ...: the image has no module asyncio (the image is the module tree: import it first) |

| def | the first construct that refuses it |
|---|---|
| `sleep_ms` (`core.py`) | a default that folds to no word (None or a call) |
| `sleep` (`core.py`) | compiles |
| `_promote_to_task` (`core.py`) | a conditional expression |
| `create_task` (`core.py`) | raise (the exception seam) |
| `run_until_complete` (`core.py`) | a default that folds to no word (None or a call) |
| `run` (`core.py`) | compiles |
| `get_event_loop` (`core.py`) | a name the subset cannot bind (a class or a global of no cell) |
| `current_task` (`core.py`) | raise (the exception seam) |
| `new_event_loop` (`core.py`) | global of a name that is not an integer cell |
| `ph_meld` (`task.py`) | is / is not / in / not in (identity and membership) |
| `ph_pairing` (`task.py`) | None as a value (no word of its own) |
| `ph_delete` (`task.py`) | is / is not / in / not in (identity and membership) |
| `wait_for_ms` (`funcs.py`) | a function as a value (a callee is not a word) |
| `gather` (`funcs.py`) | *args, **kwargs or a keyword-only parameter |
| `open_connection` (`stream.py`) | a default that folds to no word (None or a call) |

| reason (first refusal of a def, a method, a class or a statement) | count |
|---|---|
| an object held in a name or a parameter (no cell the graph can resolve) | 11 |
| async def (the coroutine frame of 4.5) | 8 |
| yield (the coroutine frame of 4.5) | 6 |
| a default that folds to no word (None or a call) | 5 |
| a module-level binding that is not an integer constant | 5 |
| raise (the exception seam) | 4 |
| a module-level binding that is not NAME = constant | 3 |
| an attribute store on an object held in a name | 3 |
| assert (the panic seam) | 3 |
| global of a name that is not an integer cell | 3 |
| try/except (the nlr seam of S11) | 3 |
| a special method (only __init__ is of the subset) | 2 |
| for (the subset loops with while) | 2 |
| inheritance | 2 |
| is / is not / in / not in (identity and membership) | 2 |
| *args, **kwargs or a keyword-only parameter | 1 |
| None as a value (no word of its own) | 1 |
| a call of a name the strand does not compile (a builtin or a face) | 1 |
| a callee that is not a name or an attribute | 1 |
| a class attribute or a nested class | 1 |
| a conditional expression | 1 |
| a decorator | 1 |
| a function as a value (a callee is not a word) | 1 |
| a name the subset cannot bind (a class or a global of no cell) | 1 |
| a subscript (a sequence cell) | 1 |
| an expression or a control statement at module level | 1 |
| del | 1 |
<!-- rxpy_gap:end -->

Reading it: **2 of 15 top-level defs, 0 of 12 top-level classes and 5 of 38 methods compile.**
The per-def rows are this census' contract: every still-refused def names the first construct of
*the def* that refuses it, and a def counts as compiled when every construct of the def lowers --
a callee's own refusal is the callee's row (`run` calls `run_until_complete` and `create_task`,
whose rows name their blockers).

| census | before this package | after this package | why it moved |
|---|---|---|---|
| top-level defs compiling | 0 of 15 | 2 of 15 (`run`, `sleep`) | `run` moved when the measurement started binding the file's own defs (the old measurement compiled each def alone and refused `run`, `sleep` and `wait_for_ms` at a call of a function of their own file -- a call, not a construct); `sleep` moved on the `int` builtin this package lowered |
| top-level classes compiling | 0 of 12 | 0 of 12 | no class's methods pass over its probe instance yet |
| methods compiling | 4 of 38 | 5 of 38 | `Loop.create_task` binds its sibling `create_task` the same honest way |

**The construct that unblocks the most defs is measured, and it is not `async def`/`await` nor
generators**: none of the fifteen plain defs is refused at one -- uasyncio's coroutines are the
five `async def` top-level defs the census does not count (`_stopper`, `_run`, `wait_for`,
`start_server`, `stream_awrite`; `wait_for` is the name the earlier table listed as a plain def)
and `yield` inside methods, which are the 8 + 6 histogram rows above and the first refusal of no
plain def. Measured per def, the construct that carries the most first refusals is **a default
that folds to no word** (3 of the 15 rows: `sleep_ms`'s `SingletonGenerator()` default, a
run-time allocation, and the `= None` defaults of `run_until_complete` and `open_connection`);
behind the two `= None` rows stands the **None word**, which in total stands behind 4 rows
(`None` as a value in `ph_pairing`, `is None` in `ph_meld`, and those two defaults). Lowering the
default alone does not compile one of the three, and that is measured too (every default of the
three folded to a word, then re-compiled): `sleep_ms` then stops at `assert` (the panic seam),
`run_until_complete` at a body that answers a word on one path and `None` on another,
`open_connection` at an import inside a def. Both walls are machine-lane decisions and not this
strand's to make: the word ABI has no spare word for `None` (a word cannot be told from the int 0
-- the refusal says so by name) and a class-call default needs the handle-resolving operand and
heap allocation of 4.1. This package therefore lowered the construct it can reach and pin
honestly: **the `int` builtin**, `sleep`'s one construct -- the word-coercion face, `int` of a
word is the word, differentially pinned against real MicroPython (`tests/rxpy_corpus/words.py`
`coerce*`, in-domain answers identical). The next single item is the **None word** (with
`is`/`is not` against it and `= None` defaults): it stands behind 4 of the 15 rows and unblocks
`ph_pairing`, `ph_meld`'s first line and the two default rows -- but it is an ABI decision the
machine lane must take first.

Earlier (the imports / attributes / classes lane), for the record: before the in-subset closures
(defaults, procedures, tables, probed classes) the reading was 0 / 3 of 12 (by layout only) / 1 of
38; a class now counts as compiled only when its methods pass over a probe instance, which is why
the class column reads 0 while the method column is at 5. `event.py` and `lock.py` do not become
importable: their methods read `self.x` of an object **held in a name** (the largest single reason
of the histogram, with the module-level bindings that are not integer constants -- chiefly
`NAME = None` and dict literals: `None` has no word) and `wait`/`acquire` are generators /
`async def` (the coroutine frame of 4.5). Integer default parameters, which were 7 of the first
refusals, are gone from the table; what stays of defaults is named for what it is, a default that
folds to no word.

**What this lane landed (2026-10-31): the binding as a tested model.** Option (1) of the lane's
priority list: nothing of the ring binding existed, so the model came first (the compiler strand
exists; the semantics it must reach did not have a pin).

`tests/host/smp_asyncio.c` maps the table of 4.5 onto `smp_sched.c` (unchanged scheduler, shared
with the S10 witness): a **Task is a run cell** (pc and the result fold live in the cell);
`create_task`/`call_soon` is `READY` plus `smp_ring_push` — **the ring is the loop's ready
deque**, there is no second queue; `await` on a Task or an Event registers the waiter on the
object's list under one loop lock and calls `smp_park` (**`PARKED`**); a task's end or `Event.set`
calls `smp_wake` on each waiting **run** (never a carrier); `sleep` is the **edge timer** — the
edge fires it on a virtual clock that advances only when the world is quiescent (no run `READY`
or `RUNNING`, an epoch re-check closes the scan against a wake in flight), so the answer never
depends on which carrier ran what; `Task.cancel` is a **raise deposit** into `mp_raise_slot`
(`smp_mp_schedule_raise`) plus a wake, consumed at the head of the next step by
`smp_mp_handle_pending` — the safe point — and it is forwarded to the task the target awaits,
exactly as uasyncio forwards it; `run_until_complete` is the main run reaching `TERMINAL`.

The corpus (`tests/asyncio_corpus/programs.py`, 15 programs) is **data** — tasks of ops, no code
in strings — run by two executors: MicroPython's real `asyncio` (`reference.py`, the reference of
decision 10, both unix seats) and the model. What is pinned:

- **reference differential**: at one carrier the model's report (every task's end state and
  result fold, and the shared trace in order) equals MicroPython's for all 15 programs: the
  ring's FIFO order is uasyncio's queue order, timers, event fan-out, cancel and forwarded
  cancel included;
- **the S10 differential extended to task-level results** (risk 7, decision 7): at 1, 2, 4, 8
  carriers, with and without the sliding compaction pass running under the live tasks, results are
  byte-identical to one carrier and equal MicroPython's; the trace's multiset and each task's own
  sequence are equal (the total order across tasks is the interleaving: evidence, not result);
  900 additional runs at 2 to 8 carriers were byte-identical in a stress loop; ThreadSanitizer and
  AddressSanitizer/UBSan clean;
- **the mapping as evidence**: ring pops equal steps, parks equal wakes (plus the tasks left
  pending), a cancel is one deposit plus a wake, the clock moves only by edge-timer fires, a
  deadlock is a report (never a hang), and 10 refusals are named by the parser.

Two defects the corpus found in the shared scheduler (`tests/host/smp_sched.c`), fixed in the
same lane:

1. **Lost wake under compaction.** `smp_wake` resolved the target with a plain resolve and
   answered "not parked" when the compactor held the cell for its copy: no token, no wake. The
   S10 witness hid it behind its watchdog; an asyncio program has none. The wake (and the
   cancel's deposit, and the retract) now resolve through `smp_run_resolve`, which waits out the
   claim and answers NULL only for a stale handle.
2. **The token outlives a successful wake.** `smp_wake` stores the token before the
   `PARKED to READY` attempt and nothing takes it back when the attempt wins, so the run's *next*
   park returns at once. The binding's rule is the condvar's: a wake is a hint and the condition
   is data — a blocked op re-checks its registration (the waker clears it under the lock before
   it wakes) and parks again when it has not been served (`spurious_resumes` is the evidence,
   pinned). Whether `smp_wake` should consume its own token is a protocol question for docs/20 S4;
   the model does not depend on the answer.

**Seats (all-platforms, honestly):** host C only. This is a model proof like `smp_mpmodel.c`.
The unix/emcc/firmware/upywm seats run the real interpreter lane (4.3 and 4.6); none of them has
uasyncio on the image, and none is claimed. The model's report format is the contract those
seats reproduce when the lane lands.

**What remains, in dependency order:**

1. the graph machine learns an operand that resolves a handle held in a slot (and an allocation
   leaf): it unlocks instances made at run time, objects passed to functions, and the 10 + 3
   "object held in a name" refusals above; then the strand's `try`/`raise` (the nlr seam), then
   generators and `async` (they unlock `core.py` and `stream.py`); each construct that leaves the
   census flips `test_the_blocking_constructs_are_the_measured_work_list` on purpose;
   (default parameters, procedures, tables, emitted cells and probed classes are done: the
   in-subset closures row above);
2. the guest `mp_import_stat` fill on every seat over `Image.resolve_module`;
3. per-object citations from debug info (`!DILocation`) in `rximport`, and from the AST in `rxpy`;
4. the net card's line events into the shim's wait/notify (docs/25), which this model leaves as
   the edge timer's sibling (the `Readiness` frame is another wake targeting a run).
