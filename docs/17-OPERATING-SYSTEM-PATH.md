# Path to a self-contained RXF operating system

This document records the intended path from today's RXF (format, toolchain, small native runtime) to an image that can boot as an operating system and evolve itself. It is a plan, not a status claim. Statements marked **[present]** are backed by the repository; **[partial]** and **[missing]** are open work.

RXF stays independent of any runtime provider. Metal and other environments may satisfy capability requirements (see [integrations/metal.md](integrations/metal.md)); nothing here depends on them.

## Goal

An RXF image can boot on bare metal, run its own services, and inspect and change its own typed object world while running. It then produces and validates a successor image that boots with those changes. Once the lowest foundation for a target is complete, new behavior is composed from typed Functions inside the image.

## Component inventory

### Format and object world
- Object model, types, lifecycle: **[present]**; path identity and image-local handles: **[present]** in the core (RXFB 6)
- Arena, cell stream, link check, digests, canonical delta round trip: **[present]** (RXFB 6; the v5 container, checker, dump, and layout tools are removed)
- Live arena with C++ allocator and object access: **[present]** (`arena`, `object`; freestanding arena, host image with paths, names, compaction)
- Delta format (XML), replay, and link check: **[present]** ([15](15-DELTAS-AND-XML.md)); atomic replay, forward references, canonical export, digests
- Self-description of all payload types: **[present]** in the core ([16](16-SELF-DESCRIPTION.md)); the starter carries all 105 types with fields and reference flags
- Successor by copying compaction: **[present]** in C++ on a live image (canonical, idempotent, payload handles rewritten); not yet on running native state

### Code and execution
- Function/Code split, binder, relocations for x86-64 SysV and AArch64 AAPCS64: **[present]**
- Semantic functions (58: graphs and calls) composed to one target-independent program each, run by a native interpreter on x86-64, AArch64 and wasm32 ([18](18-NATIVE-SNIPPETS.md)): **[present]**, the composer as an external Python tool; a compiler from graphs to machine code: **[present]**: leaf 31 `compile_program` compiles every instruction of a program on the three machines ([19](19-SELF-EDITING-PATH.md)); the same compiler again, `tiny.compile`, is itself a program written in `.rxf`, writes the bytes of leaf 31 on every program of the corpus, and compiles itself to a byte-equal fixpoint on the three machines (`native/tiny`, [19](19-SELF-EDITING-PATH.md)); leaf 31 stays native C as the seed (stage 0); compiled loops serve the channel and can be broken at their edges that go back (leaf 38); the front end of `.rxf` and the composer are still the host's
- Native foundation (9 memory Functions, 21 runtime leaves) and 286 numeric Functions, each with code for x86-64 (Linux, UEFI, BIOS), AArch64 (Linux, UEFI) and wasm32: **[present]** ([18](18-NATIVE-SNIPPETS.md))
- `wasm32` target: **[present]** as snippets ([18](18-NATIVE-SNIPPETS.md)); a browser envelope that instantiates them: **[missing]**
- Live layer (a channel with CRC-framed requests, reads, delta applied under a journal, compaction, relink, safe points and break, providers UART/shared memory/pty): **[present]** ([19](19-SELF-EDITING-PATH.md))
- Lowering and code emission inside the image: **[present, partial]** (the call subset; no self-compilation yet)
- Loading further Code or images at run time: **[missing]**

### Boot and hardware foundation
- Linux ELF, UEFI PE/COFF, BIOS x86-64 envelopes: **[present]**
- BIOS stage 2 long-mode entry with page tables: **[present]**, minimal
- Console output: **[partial]** (BIOS COM1 and QEMU exit only)
- Real memory map (E820/UEFI): **[missing]**
- Interrupts and exceptions (IDT, APIC, GIC): **[missing]**
- Timers and clocks: **[missing]** (declared as capabilities, no provider)
- Multiple CPUs: **[missing]**
- Device discovery (PCI, ACPI, device tree): **[missing]**
- Reset and power control: **[missing]**

### Memory
- Heap with bump allocation, transactions, borrow and pin: **[partial]**
- Release with compaction, dynamic growth, page acquisition: **[missing]**

### Concurrency and protection
- Tasks and scheduler: **[missing]**
- Atomics, locks, IPC: **[missing]**
- Isolation between programs: **[missing]**
- Capability enforcement at run time: **[partial]** (requirements are objects; provider slots are built empty)

### Drivers and I/O
- Driver foundation (port I/O, MMIO, DMA, interrupt binding): **[missing]**
- Block, network, input and display drivers: **[missing]**

### Persistence and network
- Persistence through successor images: **[partial]** (concept; no live dump)
- Block storage and file system or image store: **[missing]**
- Network stack (Ethernet, IP, TCP/UDP, DHCP, DNS, TLS): **[missing]**

### Trust
- Semantic digests, provenance, reproducible builds: **[present]**
- Signatures, trust policy, resource limits: **[partial]** (planned)
- Secure boot and measurement: **[missing]**

### Self-modification and tooling
- Read-only inspector, CLI, Python face: **[present]**
- Mutation protocol (`stage`, `check`, `emit`) on the running image: **[missing]**
- Incremental compilation and linking: **[missing]**
- Tracing, profiling, failure capsules: **[missing]**
- Shell or console inside the image, broader standard library (files, time, JSON, HTTP): **[missing]**

## Decisions recorded

1. **The live RXF is an arena.** One allocated blob: a target-specific glue head, then the RXF body. A C++ allocator and object access operate on it in place. Tooling, interpretation, and glue creation work on a live arena, not on a database or an object graph.
2. **Identity is the path.** An object is addressed by module and object path (`pymergetic.rxf.application.main`), unique per parent. Handles are image-local `uint64` slots assigned by the allocator at load. They never appear in deltas and never identify anything outside their image. Reason: with divergent module histories no global counter stays unique, and a number that differs between two images names nothing outside its own image.
3. **Deltas are the storage and exchange format.** Position-neutral and handle-neutral XML, field by field, applied to an empty arena as a stack or as one total delta (see "Live arena, deltas, and the C++ machine").
4. **One image for program, server, browser application, and operating system.** The RXF body is identical in every deployment. Only the header for the target platform and the target glue differ.
5. **`wasm32` is a planned target** next to x86-64 and AArch64. This changes the current statement that RXF contains no Wasm/browser layer: `wasm32` is a native target of the browser's engine, and instantiation of Code stays outside the format in an external envelope and provider.
6. **Target glue is generated, never collected.** Until the image can lower code itself, the external toolchain produces glue by the same procedure the image will use later. Live lowering for further targets is a later step on the same path.
7. **Compaction, not free lists.** Handles are slots, never addresses, so sliding compaction updates only index entries. Pinned objects block movement.
8. **The arena grows dynamically.** This needs a page-acquisition foundation Function and commit/capacity fields in the runtime context, which most likely means runtime ABI v3 and rebuilding all generated Code objects. The decision is made when allocator phase 1 is implemented.
9. **Layout.** One format break (RXFB v6) with the arena layout below: object cells with inline references, a growable index, no separate node, type, code, section, or attribute tables.
10. **Languages and substrate.** Basic types are C structs (the typeset), reused by C++, nanobind, and Python. The core is C++. Deltas are XML. Metal supplies start code and hooks for the BIOS, UEFI, Unix, and WebAssembly seats; Zephyr and NuttX are not used as a base. How firmware is built today stays as it is.
11. **A new version, not a migration.** The C++ core with Python as orchestration replaces the v5 Python stack, which is removed from the tree (its last state is the git tag `legacy-v5-python`; nothing of it is kept or imported). The format number (`RXFB 6`, in the arena header) and the package version are independent: the package stays at `0.x` (pre-alpha) until the core carries the whole toolchain.

## Layout rules

Rules: single blocks (headers, glue head, index directory) are multiples of 64 bytes. Array records are powers of two. Sizes dictated by external formats (ELF, PE) do not change.

### Arena and RXFB v6

Width rule: identities, addresses, offsets, and sizes are 64-bit. Handles, generations, payload sizes, name offsets, and index entries are `uint64`, because a fixed 32-bit identity space is a decision that cannot be revisited once images exist. Small enumerations, flags, and per-object counters stay narrow.

| Part | Definition |
|---|---|
| Glue head | Target-specific, multiple of 64. Startup code and the runtime tables. Not part of the body digest. |
| Arena header | 192 bytes: magic, version, sizes, index directory, entry and root handles, the open name block, heap bounds, 64 bytes reserved (must be zero). |
| Index | Two levels. The directory follows the header and has a fixed capacity (a multiple of 8 chunks, set when the arena is created). A chunk holds 4096 entries of 8 bytes (cell offset, or a free-slot link in bit 0), 32 KiB per chunk, and is a block cell in the cell stream. A handle is the slot number, `uint64`. |
| Object cell | 64-byte header, then the payload padded to 16, then the references. Cells are aligned to 16. |
| Cell header | `type`, `parent`, `payload_size`, `generation`, `name` (all `uint64`), `flags` and `ref_count` (`uint32`), `forward` (compaction forwarding offset), `reserved`. |
| Reference | 24 bytes: target handle `uint64`, role handle `uint64` (the handle of a `Role` object, or none), kind `uint16`, binding `uint16`, index `uint32` (the position in a list), stored inline after the payload. |
| Block cell | Arena bookkeeping inside the cell stream, kind 255, with no handle: an index chunk or a name block (4 KiB of packed NUL-terminated UTF-8 names; a longer name gets a block of its own). One sequential walk covers objects and blocks. |
| Dead cell | Kind 254: a cell whose slot was freed or now holds a newer cell (`forward`). Its bytes stay until compaction. |
| `Type` and `Field` payload | 48 bytes each; every reference inside them is a 64-bit handle. |

The cell carries no ID: its handle is its slot. A cell's size is `64 + align16(payload_size) + 24 * ref_count`, rounded up to 16, so a sequential walk needs no extra field. Derived lookups (name to child, role to reference) are built in memory at load and are not part of the format. The ground types are defined once as C structs in `src/pymergetic/rxf/core/ground.h` with `static_assert` on `sizeof` and `offsetof`. `tests/test_ground_layout.py` compiles the header as C11 and C++20 for x86-64, AArch64, i386, and wasm32. **[present]**

Removed against RXFB v5: node table, type table, code table, section table, attribute blob. Types are ordinary objects (`Type` with `Field` children). The binder finds Code through `IMPLEMENTATION` references. The path is the fully qualified name, and a derived object is recomputed on load and not stored.

`owner_kind` stays: 4 bits in the cell `flags` at shift 14 (the 7 values of v5 `OwnerKind`). `owner` is not a header field. v5 never set it (0 of 14,242 objects), so an owner more specific than its kind is an optional reference of kind `OWNER` on the cell.

Measured for the starter image: 14,498 objects and 39,180 references in 3.27 MB (the v5 image of the same program was 4.37 MB). As text it is 9.7 MB in 33 module files.

### Runtime tables in the glue head

Unchanged from the earlier size decision, because they are separate from the body.

| Structure | Now | Target |
|---|---|---|
| Runtime context | 144 | 256 (room for capacity and commit fields) |
| Function slot, capability slot | 24 | 32 |
| Object entry | 80 | 128 |
| Transaction | 48 | 64 |
| Journal member | 72 | 128 |
| BIOS manifest header, segment descriptor | 60, 40 | 64, 64 |
| Code header | 88 | 128 |

Hash-bucket layouts in the standard library are data structures and are decided separately. The hard-coded strides in the native emitters, `pe.py`, the ctypes mirrors, the C header, the four start stubs, and the BIOS runtime are replaced by one set of constants generated from the C structs.

## One body, target glue

Target-specific bytes are the only non-portable part of an image. They are treated as **derived artifacts**, never as authored sources.

- **Two kinds of glue.**
  - *Derivable*: Code that a lowerer can produce from the semantic graph of a Function for any target.
  - *Primitive*: a small, enumerated set of operations that a graph cannot express (system call or host import, port I/O, MMIO access, atomics and fences, interrupt entry, context switch, page-table and TLB operations, page acquisition). Each target provides these as data in a target description.
- **Target pack.** All Code objects for one target tuple (architecture, ABI, environment) form a pack. A Code object carries provenance: the Function's semantic digest, the lowerer and its version, and the target. Code is a cache and never an authority. Any Code object can be regenerated and compared.
- **Body identity.** The image has a body region and a glue region. Code objects reference their owner Function; a Function also holds `IMPLEMENTATION` references to its Code objects. Body cells keep identical content in every projection, and Code objects form the glue region. Identity is the digest over the body region (cells and references, excluding `IMPLEMENTATION` references), computed over the canonical snapshot. The header selects the target and the envelope.
- **Projection.** A canonical image can carry packs for several targets. Emission of a successor keeps only the pack for the chosen target, using the existing rule that omitted objects are legal when no mandatory reference points to them.
- **Same path for generation and live lowering.** The external lowerer is the reference. An in-image lowerer must produce byte-identical Code for the same graph and target (parity test, like allocator compaction). Live lowering for a further target then yields an ordinary Code object with provenance "in-image lowerer".
- **Budget.** The set of Foundation Functions and the primitive set per target are fixed by tests. A new Foundation Function needs a written reason: it must touch hardware or environment state, or bootstrap the allocator or loader.

Open checks before this becomes normative:

- how many Basic Functions are `CODE_BACKED` rather than `COMPOSED` today (every `CODE_BACKED` Function multiplies with the number of targets),
- which producer is the reference for Code bytes. Today there are two: terminal leaves come from pinned Clang output (generated native manifests), and `COMPOSED` Functions are compiled by the Python compiler when an executable is planned,
- how body identity is defined. A Function holds an `IMPLEMENTATION` reference to each Code object (written when Code is materialized), so a Function cell differs per target in its references while its payload stays identical. The body digest must therefore cover cells and references except `IMPLEMENTATION` references,
- `wasm32` needs structured control flow, so the lowerer needs a pass that turns the CFG into blocks and loops; calls through import slots map to table indices; live lowering in a browser needs a host capability to instantiate new modules.

## Allocator phases

Each phase ends with a test that runs on every target the test environment supports.

1. **Contract.** Define `committed` for a pre-reserved image (`committed == limit` at boot) and fix the builder, which currently writes `heap_committed = 0`. Decide ABI v3 (if the glue-head tables need it) and RXFB v6 with the arena layout above as one format break.
2. **Allocator state as an object.** A typed `AllocatorState` records frontier, `next_id`, limit, committed size, and generation. The builder reserves free slots; handles come from the free-slot chain and `next_handle` deterministically.
3. **Allocation emits cells.** `allocate_prepare` writes the cell header, the payload, and the references so the live arena validates as RXF.
4. **Release and in-place compaction.** Sliding compaction in canonical path order, honoring pins and borrows. Test: native compaction is byte-identical to the Python reference for the same live set.
5. **Successor from live state.** Assemble header, node rows, and heap bytes into valid RXF. Test: dump, `inspect`, `replay`, then boot the dump and obtain the same result.
6. **Self-edit demonstration.** The image creates a new Function from existing Functions, emits a successor, and the successor runs it. **[present]** with the program composed on the host (`tests/test_self_edit.py`); composing it in the image is open ([19](19-SELF-EDITING-PATH.md)).

## Critical path

1. Allocator and growth (phases 1–5).
2. Foundation Functions for interrupts, timers, and the memory map on both targets.
3. Page management (`memory.page_acquire`) behind dynamic growth.
4. Scheduler and tasks without an interpreter, then atomics and IPC.
5. Driver foundation (port I/O, MMIO, DMA), then the first driver composed as a Function graph inside the image.
6. In-image lowering so that new compositions execute without an external compiler.

## Live arena, deltas, and the C++ machine

The live arena is the state. An RXFB snapshot is its compacted body; the glue head is target-specific and produced by the envelope. SQLite, a JSON directory view, an object graph in Python, and the earlier "triangle" are not part of the core. SQLite may remain an optional query view.

- **Ground types. [present]** Four C structs are native in the core: the arena header, the object cell header, `Type`, and `Field`. Every other layout is described inside the image ([16](16-SELF-DESCRIPTION.md)). The bootstrap makes 99 handles: the root (0), `Type` (1), `Field` (2), `uint32_t`, `uint64_t`, `Enumerator`, the enumerations of the format with their enumerators, and the `Field` objects of `Type` and `Field`. In v5, 37 of 105 types had `FIELD` children; the other 28 types with instances (`Function`, `Signature`, `SemanticValue`, `ABIValueLocation`, ...) are described by `legacy/spec.py`, taken from the Python packers.
- **Reference fields. [present]** `Field.flags` has `MUTABLE` (1), `OPTIONAL` (2), `REPEATED` (4), and `REFERENCE` (8). A `REFERENCE` field is the only place a handle may sit in a payload; export turns it into a path, import turns it back into a handle, compaction rewrites it, and the link check follows it.
- **Typeset.** C structs with explicit padding and `static_assert` on `sizeof` and `offsetof` per target. The structs use `_Alignas(8)` because i386 aligns a `uint64` to 4.
- **Delta model. [present]** Operations `Create`, `Set`, `Delete`, `Entry`, addressed by path, serialized as XML ([15](15-DELTAS-AND-XML.md) is normative). Fields are written by name from the in-image `Type` and `Field` objects; only bytes a type does not describe (Code bytes, a union) are hex. Derived fields (`field_count`, `owner_type`, tags) are not written and are recomputed on load. Element names are type names and attributes are field names; numbers are text, so 64-bit values have no precision problem. XML parsing lives on hosts; targets receive RXFB. The example below is the original sketch; the real syntax is in Doc 15.

```xml
<delta>
  <module path="pymergetic.rxf.application">
    <uint32_t name="unit_price" value="125"/>
    <function name="main" implementation="COMPOSED" layer="APPLICATION"
              semantic_version="1" signature=".signature" body=".checkout">
      <signature name="signature"> <result name="total"/> </signature>
      <call name="checkout" callee="pymergetic.rxf.library.checkout_total"/>
    </function>
  </module>
</delta>
```

- **Replay and commit. [present]** A delta applies to an arena through the allocator, which depends only on the operation sequence, never on hash order or addresses. References may point forward; a commit point checks that every mandatory reference resolves. The canonical result is `compact(replay(stack))`, and digests cover that compacted snapshot.
- **Names. [present]** Siblings have unique names (the image refuses a second one). A list is named by position, `name[n]`; a reference says what it is for by a `Role` object and its `index` ([15](15-DELTAS-AND-XML.md)).
- **Divergent histories.** A module is identified by its path; its version is the digest of its canonical delta, like a commit. No ordering is needed. A conflict exists only when one image receives two different digests for the same path.
- **Completeness is the link step.** Loading resolves every mandatory reference and reports what is missing and who needs it. For a target without a given module, only what is reachable from the entry counts (`execution/reachability.py`).
- **The machine. [partial]** The C++ core holds the allocator, the index, object access, delta replay, the link check, interpretation, and glue creation, all on the live arena. Glue creation emits `create` operations for Code objects, so a target pack is a delta. nanobind exposes views to Python, which only orchestrates. The host runs Code objects of its own target from mapped bytes and places trampolines into function-table and provider slots to intercept, trace, or serve Functions and capabilities. An interpreter over the verified `SemanticIRFunction` graphs (not yet written) serves instruction-level stepping and as reference semantics. Other architectures and BIOS or UEFI boot run in an external emulator such as QEMU.
- **Default image. [present]** The starter is checked in as a module tree, `starter/` (33 files, 14,498 objects; [15](15-DELTAS-AND-XML.md)); `tests/test_starter_delta.py` pins its image and delta digests. It is the source of truth: it is edited as deltas, not generated. An optional cache is an RXFB snapshot keyed by a digest over the sources and the layout constants.
- **Proof. [present]** Delta to image to delta returns the same canonical text and digest, and snapshot to image to snapshot the same bytes, for the starter and for delta stacks with deletions (`tests/test_delta.py`, `tests/test_tree.py`, `tests/test_starter_delta.py`). The loader and the parser are fuzzed (`tests/test_fuzz.py`, `RXF_FUZZ=n` for more rounds) and the core tests run clean under AddressSanitizer and UBSan.

## Boundary principle: the body stays independent of its surroundings

The body of an RXF image (application, library, basic Functions, types, state) refers only to Functions and to capability requirements. It never names a provider, a driver, a host API, or a native address. Provider manifests and addresses are transient activation input and are never serialized.

Network access is the first case where this matters, and the same rule applies to every capability:

- **Contract ladder.** The body depends on the highest-level contract it needs. Lower contracts exist so a provider can be replaced by composed RXF Functions. A socket-level contract (today's `network_*` capabilities) can be satisfied by a host provider or by an RXF-composed stack over a frame-level contract, which in turn is satisfied by a driver composed over the driver foundation.
- **Same image, two bindings.** The same application image must run against a hosted provider and against the in-image stack. Both bindings pass one shared contract test suite. That suite is the proof of independence.
- **State classification.** State that must survive a successor is durable RXF state (configuration, listeners, routes). Connections are `TRANSIENT` objects and are omitted from successors. Code that holds a connection handle must treat it as optional and re-establish it, because mandatory references to omitted objects are rejected.
- **Buffer ownership.** Buffers belong to the RXF heap. A provider receives a borrow for the duration of a call. A buffer that a device accesses through DMA is pinned and immobile, and compaction never moves it.
- **Errors.** Providers map their errors to the declared refusal set. The body never sees host error codes.
- **Gaps in today's network contract to close first.** There is no non-blocking or readiness model, no datagram operation, no timeout, no frame-level contract, and the capability handle carries no generation.

This is a design direction. It must be checked against the templates and traits design before it becomes normative.
