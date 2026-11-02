# 31 — Self-hosting: the RXFB as its own build history

Goal: a **pure RXFB image**, bootable with minimal glue, carrying its own build
history — code *and* source as objects, under the same provenance rules the
starter snippets follow today.

## 1. The boot image

```
┌───────────────────────────────────────────────┐
│  RXFB = the object world itself               │
│  ├─ Object table (programs, data, code)       │
│  ├─ Code blobs: graph_run, compile_program,   │  ← .rxfs, built by the Seed
│  │   live_pump, leaves (numeric/crypto/uart)  │
│  ├─ Source blobs: the .rxfs/asm/c sources,    │  ← the build history (§3)
│  │   digest-wired to their code objects       │
│  ├─ Capability table (UART, timer, disk)      │  ← the ONLY hardware line
│  └─ Entry program (the first graph run)       │
└───────────────────────────────────────────────┘
     ▲ loads, binds, calls
┌───────────────────────────────────────────────┐
│  MINI GLUE (unavoidable, ~200–500 asm/C)      │
│  1. Reset vector: set stack, zero BSS         │  ← the CPU fetches from a
│  2. Exception vectors (VBAR / IDT)            │     fixed address
│  3. Identity mapping / MMU off (aarch64)      │
│  4. Entry trampoline → graph_run(entry)       │
└───────────────────────────────────────────────┘
     ▲
  Firmware: BIOS sector / UEFI stub / option ROM
```

Header-free depends on the target: BIOS sector / option ROM / qemu raw-loader
are header-free; UEFI demands a PE stub (that one header is glue, not an image
format).

## 2. The self-hosting ladder

| Step | Content | Status |
|---|---|---|
| 1 | Programs as RXF words, interpreter runs them | done |
| 2 | Interpreter as RXF (`native/graph.rxfs`) | done |
| 3 | Compiler as RXF (`native/compile.rxfs`) — runtime code generation | in progress |
| 4 | Live/IO as RXF (`native/live.rxfs`) | in progress |
| 5 | Builders = Seed only (no clang in the path) | done |
| 6 | **Source objects in the image** (§3) | done |
| 7 | Glue source per arch in the image (§4) | done |
| 8 | In-image machine-code builds (the Seed in RXF) | later, optional |
| 9 | Host entries per arch: unix program, bare-metal OS, wasm (§5) | planned |

The Seed stays the **root of the bootstrap chain** on the host. Step 8 pulls
even that into the image; booting does not need it.

## 3. Source as an object class

```
Code   (today):   format=Bytecode|Machine, semantic_digest, rxf:data     ← bytes
Source (new):     lang=rxfs|asm|c, arch=x86_64|aarch64|wasm32|any,       ← text
                  text, semantic_digest, builds→Code
```

Truth = the chain the provenance test already proves, only as objects:
`Source ──(build)──▶ Code ──(graph_run)──▶ effect`. The test
`test_every_starter_snippet_is_what_the_sources_compile_to` is the existing
form of this rule; source objects make it visible inside the image. The build
edge is stored on the built code (its `source` reference: a field is the
reference, the other direction is computed — the codes a source builds are the
code objects that name it).

## 4. Glue per arch (the only place with asm/C)

| Arch | Source | Purpose |
|---|---|---|
| x86_64 | `boot/x86_64.S` | reset vector, IDT, trampoline |
| aarch64 | `boot/aarch64.S` | VBAR, EL setup, MMU off |
| wasm32 | `boot/wasm_shim.c` | host imports, instantiation |
| BIOS/UEFI | `boot/bios.S`, `boot/uefi_stub.c` | firmware contract of the bare-metal host |

One source object per arch, versioned, digest-wired to the built code object —
the same provenance as everything else.

## 5. Host = how the world is run

The same RXF world runs in three host shapes; the host decides which
capabilities exist and where the boundary to the outside is:

| Host | Shape | The glue is | Capabilities map to |
|---|---|---|---|
| **program (unix)** | the world is a process on unix | `main()` shim: wire capabilities, start the entry run | POSIX: files, sockets, clock |
| **os (bare metal)** | the world *is* the operating system | the boot stub (§4): vectors, stack, drivers | hardware: UART, timer, disk |
| **wasm** | the world is a wasm module in a JS host | the host shim: imports, instantiation | host imports: console, fetch, storage |

One image format, three hosts. A distribution is source + code + capabilities +
entry; a host is just the outermost capability set.

## 6. Order of work

1. Ports done (graph/compile/live/numeric/uart — running)
2. Integration: seams, starter regen, full suite green, backup
3. Cleanup: clang out of the pipeline (test_native/provenance strictly Seed),
   delete `native/*.c` + `native/live.cpp` — done. The Seed builders are the
   only source of the native bodies; `native/*.h` stays as the checked-in ABI
   description (the numbers `tools/native/graph_header.py` reads, the contracts
   `boot/` and the tests' C glue include)
4. Source objects into the image (sync work, no compiler needed)
5. Glue source per arch + built code objects + boot smoke (QEMU)
6. Host entries (unix-program, bare-metal-os, wasm)
7. (optional) the Seed in RXF: self-assembler
