# RXF/RXFB — The Plan

The working plan of the rxf-w7 effort: what stands, what is open, in which order it
is done, and how the work runs. Every work package has a `done when` line — a
package is only closed when its prove passes, never when it "looks right".

Status markers: `[ ]` open, `[~]` in progress, `[x]` done (prove green).
Prove numbers are the ones the commit message or `night.log` measured, not re-runs,
unless a line says "re-checked".

Last reconciled against HEAD `7999377` (2026-10-31). The queue of 2026-10-29 was
written before about 25 commits landed; this revision follows what landed.

---

## 0. Where the code stands (2026-10-31)

- **The v7 one-arena model is in place** (docs/32): one binary memory image,
  superblock `RXFA`, slot table, packed handles `(gen << 20) | index`, the
  founding core in every image (docs/36), `sys/specs` and `sys/relations`.
- **The canonical dump and the write slice are proven** (docs/32 5.4):
  - fixpoint: the rebuild answers `place_world`'s bytes **byte for byte**
    (`1373a1c`, `5b1bb80`: the walk is INDEX->TREE with a payload remap)
  - SET-parity and CREATE-parity: `STAGE_COMMIT` answers the digest of the
    world the delta produced (`8812218`, `d6f0046`)
  - after a commit the lookup table is rebuilt (`429a9b8`); a refused apply leaves
    the arena byte for byte as it was
  - **not yet**: after an apply the dump is the canonical form *of that arena*, not
    of the fresh export of the same world (absorbed rooms, see W1/W2)
- **The live layer is on v7 on both twins** (host engine `core/live` and the
  native pump `live.rxfs`), the read path at parity. Live: the staged write
  (`STAGE_RESET`/`STAGE_WRITE`/`STAGE_COMMIT`, applying SET, CREATE, DELETE, RENAME),
  DIGEST and EMIT, and now **COMPACT** (engine `b584d82`, twin `dea2ea0`); RELINK answers its honest refusal on the engine (reason 15,
  no runtime context) and a real answer on the twin.
- **The twin's apply and dump are whole on the wasm32 seat** (`a5daeeb`: seven root
  causes in `s_release`/SET resize/CREATE kind/`m_destroy`/`s_create` room/`src_syms`/the
  dead handle remap; `5e03371`: `s_refs_of`, `check_all`, relink and `m_into_module`
  on the engine's `fixup_refs` rule). The seat's emit region is byte-identical to the
  engine's dump (16,451,800 bytes).
- **The boot seats run the linked v7 image**: boot smoke PASS on x86_64-bios,
  aarch64-virt, x86_64-uefi, wasm32-node (`0c13dc8`, the cutover; one map,
  `boot/image_stub/world_v7.h`); the wasm32 seat runs the live pump (`c26cd28`).
- **The UI works**: web console (`who`/`honors`, `digest`, the three modifications
  acts, forks that carry the act history), browser preview with the live snapshot,
  frames byte-identical to the Python client; the inspector shows the **module tree**
  as the import face beside the object tree (`1614197`, 13 tests); the 3D world films
  its own flight (`5da88d5`, `captureStream` + `MediaRecorder`).
- **rxfb is a library on four faces** (section 2): C, C++, CPython, Rust over one
  freestanding C core.
- **The machine grew a handle operand** (`7999377`, docs/23 4.9 steps 0-3):
  `RXF_OPERAND_HANDLE = 3` is resolved at each use on the oracle, the interpreter
  (`graph.rxfs`) and the host seat; no leaves and no compilers yet (queue H1).
- **The import lanes grew**: lwIP 2.2.0 is a vendored, imported card with a SLIP line
  face and a real UDP echo differential (queue Q1); the Python strand has imports,
  attributes, simple classes, defaults, procedures and tables (queue Y1); a uasyncio
  ring model is proven against MicroPython's asyncio (`33e2838`).

Seven root causes were closed on the way to the parity (capacity formula,
module-head shift, unset of dead handles, sibling-walk re-entry, `next_child`'s
candidate pointer, `under()`'s subtree root, the genesis tables' placement), then
four more in the canonical walk (`into_module` without the chain head, the
bootstrap capacity counting dead slots, `place_one` subtracting the module head,
the demoted module).

Hygiene facts re-checked at HEAD: `pytest tests --collect-only` collects 5117 tests
with no collection error; `git stash list` is empty; `clang-18` and
`clang-format-18` are installed; `shellcheck` is **not**.

---

## 1. The queue

Count of the 17 numbered packages (W, U, S, M, L1): **5 done, 5 in progress, 7 open**
(was 0 done, 1 in progress, 16 open). New packages from the night's findings are
numbered separately below and are all open or in progress.

### P0 — the write slice closes (tests + live path)

- **[~] W1: the v7 test migration.** The tests' oracle model moves to the v7:
  `tests/support.py`'s `Bridge` (model ↔ arena handles via `link.arena_map`),
  `digest_of`/`canonical_of`/`model_copy` oracles.
  *Landed:* the live fixtures (`e962563`, `tests/support.py` itself in `5b1bb80`),
  the delta binary's base check on the v7 seam (`ab1ca38`), the web console against
  the v7 oracle (`c5d4726`); `test_live_link.py` digest compares moved with
  `e962563`. Write-slice suites after the landings: `test_live` + `test_delta_binary`
  + `test_live_link` + `test_web_console` = **32 passed, 17 xfailed** (`b584d82`);
  with the twin suites 95 passed, 17 xfailed (`a5daeeb`).
  *Remains:* the 17 non-strict xfails (8 `test_live.py`, 8 `test_delta_binary.py`
  incl. 6 seeds, 1 `test_live_link.py`). Their reasons: (a) the apply's write side
  leaves absorbed rooms, so its dump differs from the fresh export (see W2);
  (b) the CLI's `delta patch` and `live apply` read the running image back with the
  v6 `Image.from_bytes` over v7 bytes - the Python side needs a v7 image reader
  (queue W6). One reason text is stale: the lookup-table mark still names the
  pre-apply index that `429a9b8` rebuilds (re-measure, see P4).
  *done when:* `pytest tests/ -q` is green and no test reads the arena with the
  v6 reader or compares against a v6 digest. *Not met:* 17 xfails and two v6
  readbacks remain.
- **[~] W2: `STAGE_COMMIT` answers `BAD_IMAGE` on one edit shape.** The typeless
  create of `Image.add(...)` and the refusing dump are **healed**: a typeless create
  pushes no pending-type fixup and a leaf that gains a child becomes a module
  (`5b1bb80`, `into_module`), and the twin matches (`5e03371`). Evidence: SET, ADD
  and RENAME commit in sequence on the engine and on the wasm32 seat, each digest
  equal (`51cd60a`, `a5daeeb`); `test_the_counters_of_a_session_move...` is the
  only named test of the old shape and it is **still xfailed**, now on the next
  defect: a committed apply answers a digest that is not the fresh export's. Root
  cause (`b584d82`, night.log 19:55): the apply's allocations absorb whole spans
  (docs/32 2), e.g. `pymergetic.rxf.model.generics` carries room 24 where the export
  asks 16, and the room is not recoverable from the bytes.
  *Remains:* pin the oracle where docs/32 5.4 puts it - the image is compacted
  before it is dumped - i.e. compare `COMPACT`'s digest (landed) with the export, or
  make the apply allocate the writer's rooms.
  *done when:* the counters/session tests apply cleanly and `emit()` answers
  the canonical bytes after every edit shape.
- **[x] W3: `COMPACT` and `RELINK` in the live path.** Engine `b584d82`: COMPACT
  slides the arena onto its canonical form in place (pin check first, reason 13;
  lookup table rebuilt; second compact reclaims 0), RELINK refuses honestly (15).
  Twin `dea2ea0` serves both on the wasm32 seat; `5e03371` pins the twin's COMPACT
  to the engine's 56 bytes. `test_compaction_in_place_gives_the_canonical_arena`
  and `test_a_pinned_object_is_not_moved` are off xfail and on the v7 oracle.
  Prove: write-slice suites 32 passed / 17 xfailed (was 30 / 19); `test_twin_seat.py`
  5 passed.
- **[ ] W4: two engine findings of the twin's round 2.** (a) The engine's
  `STRING_NONE` patch branch writes at `pt.offset` without the module head
  (`core/live/live.cpp`, apply, `set_payload_at(handle, pt.offset, ...)`; the twin is
  right). (b) The engine's apply has no `check_all`: a delete that leaves a
  declared slot dangling commits and the dump unsets it (dropped with the v6
  machinery in `e97d17e`); the twin refuses and rolls back.
  *done when:* a SET of a module with its own record leaves the engine's digest
  unchanged, and the engine refuses the `uint8_t` delete with DANGLING exactly as
  `test_twin_seat.py` pins for the seat.
- **[ ] W5: the twin on the emulated seats.** `m_code_addr` still reads v6 cell
  views (`ca_pay = cell + 64`), so it is right only where the machines have function
  addresses (x86_64/aarch64) and wrong for a v7 view; the stale-code test's xfail
  mark holds for the emulated seats only (hours to run).
  *done when:* the x86_64 and aarch64 seats pass the stale-code test and the mark is
  gone.
- **[ ] W6: a v7 image reader on the Python side.** `Image.from_bytes` is the v6
  reader; the CLI's `delta patch` and `live apply` over a v7 arena die with
  `BadImage`. (`pymergetic.rxfb` already reads v7 zero-copy; the decision is whether
  the model's `Image` loads through it.)
  *done when:* the two CLI xfails (`test_the_commands_convert_both_ways`,
  `test_the_rxf_live_commands_work_on_a_pty`) pass.

### P1 — the UI finishes

- **[x] U1: the console's "modifications" acts.** `POST /compile` on `web/serve.py`
  (host compiler door `web/acts.py`, the one compiler `live/physical.py`), acts as
  console commands (`rename`, `set`, `add`) and as side-panel clicks, each prints the
  was/now digest, refusals are words, forks carry the act history (`51cd60a`). The
  seat leg came with the twin's apply (`a5daeeb`: the page's SET/ADD/RENAME commit,
  each digest equal to the engine's). Prove: `test_web_console.py` 17 passed.
- **[ ] U2: the browser's "modification"** — bytes and digest visibly change
  while dragging (the third preview point). *Verified open:* the world's drag is a
  camera pan (`rxf-world.js`), the console shows digests only per act, there is no
  byte-diff view.
  *done when:* a drag in the preview shows the byte diff and the digest
  updating live.
- **[~] U3: `rxf-reader.js` onto v7.** *Landed:* the reader has an `RxfArena` layer
  (superblock, slot table, records, `sys/syms`, `sys/specs`, `sys/relations`) and
  dispatches on the magic (`7ced79e`; the Python twin `ArenaScene` agrees byte for
  byte; `test_reader_js.py` 7 tests, 3 re-checked here: v7 vs exporter, spec row,
  file identity); `demo-scene.json` is gone (`12f2789`).
  *Remains:* the page itself still reads v6: `/world/scene.bin` serves
  `Image.to_bytes()` (RXFB v6) and the checked-in `scene.bin` is `RXFB` version 6,
  so the static seats never touch the v7 face.
  *done when:* the reader parses a v7 image **and the world page's seats read v7
  bytes through it** (`scene.bin` is the v7 arena of the image).
- **[x] U4: the three mirrors onto v7** — `rxf-reader.js` (`7ced79e`),
  `tests/host/smp_arena.h` (`a53b46c`: the 24-byte record, pin tests map the header
  onto `ground.h`), `live.rxfs` (`56247df`, `9341f16`, then the walks `7103839`,
  `a5daeeb`, `5e03371`) and the C++ read side (the engine's `ObjectArena`; the slim
  `rxfb_layout.h` is pinned to `ground.h` for 4 targets x {C11, C++20}, `0ffc4ed`).
- **[ ] U5: load-time work** — cable parts binary instead of JSON megabytes
  (~20 s target), on-demand layer paging; the duct-packing leaves the pipeline.
  *Verified open:* the cable bundles are still the Python `packing=True` pass and
  `/world/scene.json` is still the part stream; nothing on paging was committed.
- **[ ] U6 (epic, own series): management + UI inside the rxfb binary (OpenGL).**
  Separate seat series per the all-platforms rule. Not started.

### P2 — the seats converge

- **[x] S1: the four-seat boot cutover to the v7 linked image**, one change
  (`0c13dc8`): the stub world is gone, one map (`world_v7.h`) every carrier is
  checked against, x86_64 base `0x100000 -> 0x4000000` (OVMF owns the old range), UEFI
  claims once, the aarch64 seat's body pins the glue bytes. Prove: `sh
  boot/run_smoke.sh` PASS on all four seats; 43 passed across `test_boot_smoke`,
  `test_boot_glue`, `test_boot_codes`, `test_source_objects`, `test_image`,
  `test_starter_delta`, `test_arch_proxy`; `sync.py --check` up to date.
- **[~] S2: guest assertions** — the machines say `who()`. *Landed:* `who()` is a
  composed semantic function (160 bytes of bytecode, pinned identity, `5816291`);
  `honors` answers on the host build and `test_honors_is_answered_with_the_foundings_
  handle` resolves the founding core on the wasm32 seat (`c5d4726`, `ff3443c`).
  *Remains:* the boot smoke asserts only `RXFOK` and the result word
  (`0x4b4f465852`); no seat executes `who()` and asserts the headcount.
  *done when:* each of the four seats runs `who()` in the guest and the smoke checks
  its answer.
- **[ ] S3: the frame-size decision** (1024-byte frames vs the slow emulated
  boots) — a user decision, then the change. *Premise moved:* the run cell is
  variable-frame with an 8192-byte budget (docs/20 S6; `RXF_BOOT_FRAME_SIZE` is the
  cell), `RXF_GRAPH_FRAME` 1024 stays the composer's and the front's frame; the
  measured pressure is now the run-cell budget (queue H2), not 1024.

### P3 — the long strands

- **[~] M1: MicroPython convergence** (docs/23): Pointer, Dispatch, **lwIP**,
  uasyncio. Measured table: docs/23 section 7 (generated, pinned).
  - Pointer (i2) and runtime Dispatch (i3): **landed** (`tests/test_import.py`,
    `test_the_unary_slot_dispatch_routes_to_the_type_body`).
  - lwIP: vendored and imported, the real UDP echo is byte-equal to native lwIP on
    the host seat once the interpreter has room (queue Q1-Q3, I1-I2, H2).
  - uasyncio: semantics landed **as a model** (`33e2838`, ring binding of S3,
    `test_smp_asyncio.py` 110 passed); the source as imported graphs is **not**
    (0/15 defs, queue Y1); the guest `mp_import_stat` fill does not exist.
  - Python strand: `rxpy` imports, classes, defaults, procedures, tables (queue Y1).
  *done when:* MicroPython and uasyncio run on the image, lwIP echoes over a seat's
  line, each with its differential.
- **[ ] M2: x25519 speed debt** — the bit-serial kernel misses the ~1 s
  first-flight budget (own session: word/codegen speed work). *Verified open:*
  `native/crypto/x25519_kernel.rxfs` untouched since the 2026-10-22 composition
  (`5b20aaa`); `ece50fe` fixed the card's build link (`tools/native/seed_object.py`),
  not the speed.
- **[ ] M3: the crypto and MicroPython faces into the image**, then their
  `embodies` refs (aumasson→blake2s), last a commit founding the honors-tree
  digest. **The order is not negotiable.** *Verified open:* `embodies` exists in
  `honors/` code, tests and `arena_export.py`, no ref lands in the image's code.
- **[ ] M4: the Feature/Distribution manifest** — feature gates, managed deps,
  composable builds (a network-less image). Open fork: the shape of
  identity/manifest. Nothing landed; docs/33 only names distribution packages.

### P3b — what the night produced (queues, all from `night.log`'s "what remains")

**H — the machine lane (docs/23 4.9; owner: the machine lane's worker).**

- **[~] H1: the handle operand and the allocation leaf.** Steps 0-3 landed in
  `7999377`: step 0 (seven premises corrected in docs/23), step 1 (`graph.h`
  operand + allocator-cell constants, no leaf ids yet), step 2 for the operand
  (`tests/test_graph_handle.py`, oracle + simulator), step 3 (`graph.rxfs`
  `m_locate`/`m_load`/`m_call_leaf`/`m_call_program`); host runner heap, pool and
  sliding pump. Prove: `test_graph_handle.py` 12 passed (x oracle, x86_64, aarch64,
  wasm32, host seat), `test_graph.py` + `test_graph_checks.py` 47 passed, S8 pump
  >25 moves, answer 35. *Remains, in order:*
  4. `native/runtime/runtime.rxfs` leaf `runtime_allocate` (50, shape `QQQQQPO`,
     allocator state behind capability 3) and `runtime_release` as leaf 51 (the
     function exists; it is not yet a leaf), `seed_build` row, `scenario.py` letters;
  5. the composers (`compose.py` marker `ObjectHandle` -> kind 3, its twin in
     `native/compose/*.rxfs`, parity test), `rxemit` constant (separate commit), the
     decoders (`rxfgraph.py`, `rxfc.py`, the inspector);
  6. the compilers refuse kind 3 with 16 (`compile.rxfs`, `native/tiny`);
  7. the contexts: `tests/host/runner.c`, `graph_world.py`, `link.py`/`boot_image.py`
     pool rows and capability;
  8. the boot mini interpreter pin (kind 3 answers `OUT_OF_RANGE`);
  9. **the starter and its pins, last and alone** (Functions for 50/51, `ObjectHandle`,
     sync order, `test_boot_codes` pins in the same commit; the tests that hard-code
     49 leaves move here: `test_compose`, `test_rxfb`, `test_rxfb_rust`, `test_graph`);
  10. docs (20 S6/S7, 18, docs/23 section 7 row flips).
  Uncommitted work of this lane is visible in the shared tree (`test_runtime_allocate.py`,
  `test_compile_handle.py`, edits in `native/` and `tools/native/`); it is not counted
  as landed.
  *done when:* the prove matrix of docs/23 4.9 is green per layer and seat and the
  pins are regenerated.
- **[ ] H2: the run-cell budget.** The shipped interpreter (`native/graph.rxfs`:
  cell 8192, 704-byte frames, `depth + 1 >= 8`) refuses the lwIP echo with 18
  (`RXF_REFUSE_CELL_BUDGET`). Measured chain: 14960 bytes at depth 9 (`84497d1`);
  cell 15360/9, 16384/9, 16384/10 and 32768/16 pass, 12288/12 -> 18, 32768/8 -> 17.
  *done when:* `RXF_GRAPH_CELL_BYTES >= 16384` (32768 for margin) and
  `RXF_GRAPH_CALL_DEPTH >= 10` (16 for margin) in `native/graph.h` and
  `native/graph.rxfs`, and the strict xfail "shipped interpreter echo" flips. Touches
  every pin that hashes the interpreter, so it lands with H1's step 9 or right
  before it, and `RXF_BOOT_FRAME_SIZE` follows.

**I — the importer (`tools/native/rximport.py`; owner: the importer lane).**

- **[ ] I1: the pointer-to-caller-local defect (silent wrong word).**
  `icmp_dest_unreach` passes `&iphdr_src` (a wide local) to `ip4_output_if_src`; the
  call card reads the destination through a pointer to the caller's frame slot and
  gets 0.0.0.0 (native: 10.0.0.1), so the ICMP port-unreachable reply to a closed
  port has the wrong destination. Pinned by a strict xfail; a minimal repro with a
  wider reader segfaults the host runner (not committed). Refusing alone would
  un-compose `ip4_input`/`udp_input`, so the fix is the cell route. Same family: the
  out-param call card (`netif_add`, `pbuf_dechain`), which flips the by-hand netif
  attach of the echo test; local-array address arithmetic (`ip4addr_aton/ntoa/ntoa_r`,
  `ipaddr_addr`; needs the stack-array decision of docs/23 4.1).
  *done when:* the strict xfails flip and `netif_add` composes.
- **[ ] I2: libc faces and leaves.** `strlen`/`strncmp`/`strtol` as IR under
  `RXF_IMPORT_FACES` (the 6 unlinked bodies); an overlap-safe `llvm.memmove` leaf (the
  copy leaf is forward-only; `memory_move` exists in `memory.rxfs` without a leaf id;
  `lwip_itoa`, `netif_index_to_name`); `llvm.usub.sat.i16` when a body needs it.
  *done when:* lwIP's refused count reaches 0 and unlinked 0.

**Q — the lwIP card (`native/lwip`, `tools/native/lwip_import.py`, docs/35).**

- **[~] Q1: lwIP as a card.** *Landed:* lwIP 2.2.0 vendored unmodified, pinned commit
  and tree digest (`276cd09`); the NO_SYS=1 port face and the import lane, docs/35
  generated tables (`b07cbb7`); importer intrinsics `bswap`, `umin/umax/smin/smax/abs`,
  constant-expression arithmetic over `ptrtoint`, and the call-card pointer fix
  (`70a5f4b`, `477f599`, `8523ba9`, `fc996fe`; `test_import.py` 244 passed); the SLIP
  line face imported with the card and a host-seat fill that meets the contract
  (`93fe952`: 106 of 120 bodies composed, the whole echo path composes); the real UDP
  echo differential against native lwIP, 7 of 8 scenarios byte-equal (`84497d1`); the
  tap bridge on a datagram stand-in (`50ba25c`). Prove: `test_lwip_slip` +
  `test_lwip_import` 22 passed, 5 xfailed (the tun open, js seat, uart seat, shipped
  interpreter echo, icmp-unreachable defect), 354 s, in a clean scratch worktree.
  *Remains:* Q2, Q3, H2, I1, I2.
- **[ ] Q2: the SLIP line fills per seat** (all-platforms: the module and face are
  the same, each seat's prove hits it). Status: host socketpair PROVEN; unix tap
  bridge proven on a stand-in, `TUNSETIFF` is EPERM here (no CAP_NET_ADMIN) and
  UNPROVEN; browser `slip_line_js.c` not written (decided line: `rxf net gateway`, SLIP
  over WebSocket, docs/23 decision 8); firmware `slip_line_uart.c` (16550/PL011) not
  written; the unix µPy and wasmmod seats do not carry the card at all. A missing fill is
  a stub that fails its prove, not a dark port.
  *done when:* the contract suite (`LineContract`/`PacketContract`) passes on every
  seat with its fill.
- **[ ] Q3: RX readiness park/wake and the rest of the card.** Today `slipif_poll`
  polls `sio_tryread`; `rxf_slip_line_ready` is the word to watch (docs/25 4) — the
  run-cell machine needs the park/wake. Then per-upstream-file citations through
  `#include` (provenance), TCP + the socket echo, and the uasyncio binding.
  *done when:* a parked echo run is woken by the RX word and replies without polling.

**Y — the Python strand and uasyncio (`tools/native/rxpy.py`, docs/23).**

- **[~] Y1: the Python strand.** *Landed:* imports as a lookup of named cells
  (`cd2d0d8`, `test_rxpy_imports.py` 202 on both MicroPython seats); attributes and
  simple classes, a method per instance over its cell (`a1b5463`, `test_rxpy_objects.py`
  43); defaults and keywords, procedures (None as absence of an answer), tables as data
  cells, every module cell emitted, classes probed (`7c69b5e`, `test_rxpy_subset.py`
  255); the differential reference found (it had skipped silently in every worktree,
  1086 pins, `test_rxpy.py` 2253 passed); the gap to uasyncio measured and pinned
  (`52909ad`, `ae50e13`, `test_rxpy_asyncio_gap.py` 10): defs 0/15, classes 0/12,
  methods 4/38, top reasons: object held in a name 10, module-level non-integer
  binding 10, `async def` 8, `yield` 6, call of a name not compiled 6.
  *Remains, in order:* (1) `try`/`raise` on the nlr seam, (2) generators, then `async`/
  `await` (each flips `test_the_blocking_constructs_are_the_measured_work_list` on
  purpose), (3) the guest stdlib (`time`, `sys`, `select`, `io`) in the module tree so
  imports resolve, (4) the guest `mp_import_stat` fill over `Image.resolve_module` on
  every seat (no RXF hook in `packages/micropython` or `metalpython` yet), (5) debug-
  info (`!DILocation`/AST) citations instead of file-level spans, (6) after H1:
  `self` as a parameter, one graph per class, `Foo(args)` as `runtime_allocate` +
  field stores, `None` for object-typed values only.
  *done when:* the six `extmod/asyncio` files compile as graphs and the asyncio
  corpus runs on them against MicroPython.
- **[ ] Y2: the docs/20 S4 token decision.** The token outlives a successful wake, so
  the next park returns at once (a spurious resume; the binding re-checks the
  registration). Open: should `smp_wake` consume its own token? Decide in docs/20 S4,
  then change `tests/host/smp_sched.c` and the binding accordingly (the S3 binding,
  `smp_asyncio.c`, 900-run stress loop, TSan/ASan stay the prove).
  *done when:* the docs say which, the model and the sched implement it, and the
  binding no longer re-checks as a workaround.

### P4 — hygiene (whenever the machine is idle)

- [ ] the full-suite sweep; an overnight aarch64 differential run (5117 tests collect;
  nobody has swept them since the v7 landings)
- [x] the 3 test files that did not collect (`httpx`/`starlette.testclient`) — re-checked:
  collect-only reports 5117 tests, no error (a deprecation warning only)
- [x] the `stash@{0}` hazard — `git stash list` is empty; the rule stays: never blind-pop,
  never stash a shared tree
- [x] the 2 wasm32 `test_safe_points` failures — healed by the twin's walk (`7103839`: 5
  passed, 1 xfailed; `a5daeeb`: `-k wasm32` 6 passed)
- [~] environment debt: `clang-18`/`clang-format-18` installed (done); `shellcheck` is not
  installed (the lint rule asks for it when present); the `test_compose_image` runtime
  (8:14 h, `nightly` + `slow`) is unchanged
- [ ] re-measure the 17 non-strict xfails with `--runxfail` and rewrite the reasons that
  name defects since fixed (the lookup-table mark names the pre-apply index of `429a9b8`)
- [ ] a shared rxfb `tests/conftest.py`: `test_rxfb_cpp.py`, `test_rxfb_rust.py` and
  `test_rxfb_py.py` import the C test's fixtures by name (file-level `noqa F811/F401`)
- [ ] cargo in CI: `.github/workflows/core.yml` runs only pytest and the sanitizers;
  `rxfb/rust` (cargo test, clippy `-D warnings`, fmt, the `no_std` builds for
  `wasm32-unknown-unknown` and `x86_64-unknown-none`) is not wired, and the root CMake
  adds `rxfb` as `EXCLUDE_FROM_ALL` without cargo; also missing: a wasm32 *link/run* of
  a Rust consumer, and `rxfb-write` behind a feature gate

---

## 2. The new piece: rxfb as a backend library

> "eine extrem schmale version von rxfb als backend für c cpp cpython und rust
> zur verfügung stellen, also quasi als library anstatt als eigenes programm,
> sodass andere frameworks das zero copy layout und die code geschichte intern
> auch nutzen können."

**[x] L1: the slim reader core (`rxfb`).** Landed in four faces over one C core.
Design decisions (as built):

- **Shape:** a single C translation unit + one header (`rxfb.h`), freestanding
  (no libc, no allocation) — the engine's read path already is this. It maps a
  `.rxfb` image and answers **views**, never copies: `rxfb_attach(bytes, len)`,
  `rxfb_lookup(id or path)`, `rxfb_record`, `rxfb_payload` (`const uint8_t *,
  uint32_t`), `rxfb_children`, `rxfb_refs`, `rxfb_path`, `rxfb_spec`.
- **The layout is the ABI**: `ground.h`'s pinned structs (the superblock, the
  slot, the record, the ref row, the spec row) are the contract; the library
  ships that header as its own (`rxfb_layout.h`) so other trees never include
  the engine.
- **The code story**: the arena's Code objects are the bodies and a function
  slot points into them. The library exposes the same resolution the seats use
  (`rxfb_slot(i) -> (entry, len)`), so a host framework can JIT/interpret or
  link the same bytes — the zero-copy layout *and* the code story in one place.
- **Faces by binding, one muscle language (the rules):**
  - **C** (`0ffc4ed`): `rxfb.h` is the API; `rxfb_dump` is the contract output.
    `tests/test_rxfb.py` 38 passed: freestanding on four targets at -O0/-O2, layout
    pins, all 22257 walk lines of the starter equal to the engine's, function slots
    1..49 x three machines, refusals, 400 damaged images under ASan+UBSan.
  - **C++** (`7368469`): `rxfb.hpp`, a header over the same C symbols (freestanding
    C++20, example diffed against the C one); `test_rxfb_cpp.py` 35 passed.
  - **CPython** (`a06c32c`): `pymergetic.rxfb`, one nanobind extension over the same C
    core (zero-copy `memoryview` views, typed refusals, generated stub);
    `test_rxfb_py.py` 22 passed.
  - **Rust** (`170c655`): `rxfb-sys` (raw bindings, `build.rs` compiles `rxfb.c`) +
    `rxfb` (`#![no_std]`, borrowed `&[u8]` views, iterators over children/refs);
    `test_rxfb_rust.py` 15 passed; cargo test 6 passed, clippy and fmt clean, builds
    for `wasm32-unknown-unknown` and `x86_64-unknown-none`.
- **The seam with the host side**: building/writing stays where it is
  (`place_world`, `arena_map`, the journal write path). The library is the
  runtime side: map, look up, read, resolve code. The write path can come
  later behind a feature gate (`rxfb-write`).
  *done when:* a C, a C++, a CPython and a Rust example each map one `.rxfb`
  image and print the same path/payload/digest, with no allocation in the core.
  *Met:* the four outputs equal the same `rxfb_dump` text byte for byte on the
  starter (and the Rust example equals the C one on 150 damaged images too). The
  leftovers (cargo in CI, wasm32 link/run, `rxfb-write`, the engine linking the
  library one day) are P4 items above.

---

## 3. How the work runs

- The queue is followed top-down, one package at a time; each `weiter` continues
  the current package or opens the next.
- Every edit is linted right away (pyright 0 / `bash -n`+shellcheck / the
  language's linter); a broken file is never left behind.
- A card/face is wired on **every seat in the same change** (all-platforms).
- One defining language per module; no source generators; C ABI names:
  `init`/`deinit`, `create`/`destroy`.
- The chat is German, everything else (code, docs, commit messages) English.
