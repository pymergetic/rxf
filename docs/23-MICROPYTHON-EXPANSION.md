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
| C call chain | run-cell frame links: `(base, size)` frames carved from the run cell, at most `RXF_GRAPH_CALL_DEPTH` (8) open, their sum within `RXF_GRAPH_CELL_BYTES` (8192); a Python call chain is heap frames (`mp_code_state_t`), not links | docs/20 S6, `native/graph.h` |

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
   (`RXF_GRAPH_CELL_BYTES` 8192), but `RXF_GRAPH_FRAME` (1024) still refuses at import:
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
   call cards land cross-file (`qstr_str` is a call card), and `RXF_GRAPH_CALL_DEPTH` is 8 links
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
   (8192); `qstr_from_strn` (1608 B) is its own program and no longer refused for a frame.
3. **Wiring universe — decided: a link fact.** `function_wiring` survives import runs as
   objects; a boot and its bodies may be separate imports, so the per-module provenance knob
   stays clean.
4. **lwIP pbufs — decided: pinned cells.** One arena: a pbuf is a pinned cell the sliding
   pass (S8) skips (`foundation.heap.pin`/`unpin`); no second memory, no resolve-at-use tax.
5. **Static initializers — decided: the importer learns them.** Real relocation support in
   `rximport.py` for structs of callbacks; the `obj_port_boot` idiom remains the fallback
   shape, not the only door.
6. **Recursion — decided: one frame model.** C recursion uses heap frames like Python calls
   (no depth-8 ceiling for it); the budgets stay honest (`RXF_GRAPH_CELL_BYTES`, the cell
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
