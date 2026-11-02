# 37 - The graph and its one language

Status: **direction**, set by the owner on 2026-11-02. No code follows from this note yet. It
supersedes parts of [23](23-MICROPYTHON-EXPANSION.md) and [29](29-THE-SEED-COMPILER.md) (listed in
section 4) once the owner confirms the list.

## 1. The decisions

1. **The object graph is the only representation of a program.** There is no second one: no
   `.rxfs` text that is compiled, no IR that is imported, no bytecode that is the source of truth.
2. **There is one language, and it is the text form of the graph.** Every well-formed text means
   exactly one graph, and every graph prints as exactly one text. The canonical form (including
   layout) is part of the language, so the text is a view of the graph and never an authority beside it.
   Nothing can be said in the language that the graph cannot hold.
3. **Intrinsics are the only code that is not graph.** They are the smallest set that maps one to one
   to CPU operations, written as assembler or raw bytes per target. Everything above them is composed
   from them, without exception.
4. **Foreign languages are not doors.** No LLVM import, no Python, C, C++ or Rust assimilation. A
   program is written in the one language or it is not in the image.

## 2. The floor

The floor is a C library (C11, freestanding, no libc) that defines the essential data structures
and the platform glue, and nothing above them.

- **Data structures:** superblock, record header, FREE ring, slot index, name and symbol tables,
  spec and relation rows, journal, handle packing, delta reader and applier, canonical digest
  ([32](32-ONE-ARENA-OBJECT-MODEL.md) is the specification).
- **Platform glue:** a memory region with commit and grow, fence and atomics, a clock, a character
  output (the UART), and panic. The host simulates exactly this and no more.
- **Headers:** `.h` is the ABI and the only definition. `.hpp` is thin inline sugar (types, RAII,
  namespaces) without logic; if a decision lives in a `.hpp` it belongs in the `.h`. Python is exported
  from the `.hpp` through nanobind. Rust gets a `-sys` crate from the `.h` and an object layer on top.
- **The existing C++ core** becomes the oracle of the differential tests, not a second implementation.

## 3. The ladder

| Level | What exists | How it is made |
|---|---|---|
| L0 | the C library and the intrinsics' bytes | C, assembler, raw bytes |
| L1 | an empty image that grows: root, nodes, types, functions | a sequence of deltas (`.rxfd`) |
| L2 | the image runs and extends itself | programs in the one language |

Programs that today sit in the monoliths (the live pump, the compiler, the interpreter) are written
in the one language on top of L0. The compiler from graph to machine code is one of them; its first
form is bootstrapped from the intrinsics and the C library.

## 4. What this retires

Sizes are what the tree holds on 2026-11-02.

| Item | Size | Fate |
|---|---|---|
| `native/*.rxfs` (live, compile, graph, front, compose, tiny, ...) | 27.8k lines | replaced by programs in the one language |
| the twin law (`live.cpp` / `live.rxfs`, `compile.rxfs` / `native/tiny`) | two definitions each | gone: one definition |
| `tools/native/rximport.py` (the LLVM-IR strand) | 4.6k lines | retired |
| `rxpy` (the Python strand) | - | retired |
| docs/29 decision C ("one body seen two ways", `.rxfs` as what runs) | - | superseded by decision 2 above |
| docs/23 import strands (lwIP card, MicroPython as guest, uasyncio by import) | - | superseded, see section 5 |

Measured reason (2026-11-02): the running image is 15.6 MB, of which 12.2 MB are machine code
blobs; three programs (`live`, `compile`, `graph`) hold 72 % of all source text, and the arena and
the compiler each exist twice.

## 5. What it costs

- **lwIP and MicroPython do not arrive by import any more.** The network stack, the transports and
  the REPL must be written in the one language, or they are not there. The vendored lwIP stays a
  reference for tests only.
- **The tests that prove the import lanes** (`test_import`, `test_lwip_*`, `test_rxpy*`) lose their
  subject and are retired with it.
- **The one language needs a front end before anything is written in it.** Until then the existing
  `.rxfs` stays as the scaffold that builds L0's first programs, and is removed when they exist.

## 6. Open

- The concrete syntax of the one language, and its smallest type system.
- The exact intrinsic set (derive it from what `live`, `compile` and `graph` use; the table in
  [29](29-THE-SEED-COMPILER.md) is the start).
- How the first graph-to-machine compiler is bootstrapped, and where its seed is trusted.
- How much of the host the glue must simulate on each seat.
