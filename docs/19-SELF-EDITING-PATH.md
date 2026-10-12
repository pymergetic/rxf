# The way to a self-editing image

> Status: packages 0 to 12 are present, with the limits stated under [Where the packages stop](#where-the-packages-stop). It extends [the operating-system path](17-OPERATING-SYSTEM-PATH.md) with the shortest line to an image that can be worked on from inside, from the first run.

## The question

Do the operating-system fundamentals (memory accessor, scheduler, file system, network) have to exist before one can talk to the running image? **No.** What a self-editing image needs is smaller than an operating system:

1. an allocator that can also release (the edit makes garbage),
2. the operations of [doc 10](10-FACE-AND-CHANNELS.md) as native Functions on the live arena (`get`, `children`, `references`, `resolve`, `stage`, `check`, `emit`),
3. the linker, so that the image can build the glue head of its successor,
4. **one channel** to the outside, which carries those operations.

No scheduler, file system or network stack is in this list. They are built later, *in* the image, with the operations that this plan makes possible.

## One channel, three providers

A UART console, a shared-memory bridge to the inspector, a pipe in a test and `stdio` in a browser are the same thing: **two byte rings and a doorbell**. The image sees one capability, `Channel` (the existing `Console` capability is its first, write-only part, **[present]** as `console_log`). Providers differ only in who fills and drains the rings:

| Provider | Rings in | Doorbell | State |
|---|---|---|---|
| UART: leaf 30 `uart_pump`, capability id 2 (x86_64 COM1 port I/O or a 16550 or PL011 in memory, aarch64 PL011 or a 16550 in memory, wasm32 two FIFOs of 256 bytes in linear memory that the host fills and drains; the generation word of the capability says which device, [the UART](#the-uart-devices); without the capability, or with a device the machine cannot have, the leaf answers `UNBOUND`) | the UART FIFO, drained by the leaf into the rx ring and filled from the tx ring | the line status register | **[present]**, tested on x86_64 and aarch64 (every device below), wasm32 |
| Shared memory (host process, inspector) | `core.live.Area.shared` lays out a segment, `Area.attach` rebases it in the other process, `link.SharedMemory` talks through the rings | the ring counters | **[present]** |
| Host pty, serial device, file descriptor | the same frames over a byte stream (`link.FdLink`: `pty:PATH`, `tty:PATH[:BAUD]`, `fd:N`) | the stream | **[present]** |
| Test harness (x86_64 and aarch64 emulator, wasm32 in node) | memory the harness writes | the harness, between two steps | **[present]** |
| Browser | a `MessageChannel` or a shared buffer | `postMessage` or `Atomics` | not in this package: the browser seat lives in another repository |

Because the contract is the rings, the console and the shared-memory bridge are not two features. The shared-memory bridge is the console with a faster provider, and the inspector is a client of the same frames as a terminal.

## What the channel carries: frames, not text

The image speaks **binary frames**; it contains no text parser and no XML parser. A frame is a request or a response of an operation of doc 10, with a request id. A human console is a *host* program (`rxf console`) that reads text, makes frames, and shows the answers; XML stays on the host, as for every other tool.

- Reading: `get`, `children`, `references`, `resolve` return payload bytes and the type; the host decodes them with the self-description ([16](16-SELF-DESCRIPTION.md)).
- Writing: `stage` takes a **binary delta** (the operations `Create`, `Set`, `Delete`, `Entry` of [15](15-DELTAS-AND-XML.md), addressed by path, in a fixed binary encoding), `check` and `emit` follow. The host turns XML into that encoding and back; the proof is that both give the same canonical digest.
- `dump` is `emit` of the arena itself. It is editable on the host as XML, because the core already exports an arena as canonical XML.

## Control: who runs when

There is no scheduler, so control is shared at **safe points**, like a debugger stub:

- The image runs one thing at a time. At a **safe point** it looks at the doorbell: the end of a leaf call of the interpreter, the end of a transaction, an explicit `yield` in a loop. If a request is waiting it serves it, then goes on.
- A **break** is a flag the host sets (shared memory or the UART break); the interpreter stops at the next safe point and serves requests until the host says continue. This is the interrupt mechanism the inspector needs, without a hardware interrupt.
- Reading while running without stopping is allowed only for what cannot move: the host reads the generation word, reads, reads it again (seqlock). Anything that compaction can move waits for a safe point.
- No second CPU, no task switch, no locks are needed for this. They come with the scheduler, later.

This also settles the "who owns the arena" question: the arena has one writer, the running image, and every other party asks it.

## Decisions

### 1. The binary form of a delta: names, not handles

> **As built.** The delta that a running image applies is the *physical* delta (`live/physical.py`, `live.h`): it is compiled on the host from the image before and the image after, and says *what is there afterwards*, object by object, with every handle written as the path of the object it points at (operations `CREATE`, `SET`, `DELETE`, `ENTRY`; one string block; a SHA-256 over it). The image only looks paths up, writes bytes under the journal and checks that the result links, so it contains no compiler for text deltas, and the host can produce the delta from any change of an image (text delta, UI, script). `rxf core delta` converts XML to the physical form and back; both give the same `Image.digest()` (`tests/test_delta_binary.py`). The text below is the design that led to it and the reasons for names over handles, which still hold.

A handle is image-local and never appears in a delta ([17](17-OPERATING-SYSTEM-PATH.md), decision 2), so the binary form addresses by **name and path**, like the XML. It is the XML with its strings interned:

- a header (magic, version, string count, op count), a **string table** (UTF-8, each string once), then the operations;
- an operation is an opcode (`Create`, `Set`, `Delete`, `Entry`), the path as a string index, for `Create` the type name as a string index, then the fields: field name (string index), byte length, value;
- the value is the stored bytes of the field, little endian; a `REFERENCE` field and a reference of the cell carry the path as a string index; an enumeration carries the path of its `Enumerator`; the bytes a type does not describe (`rxf:data`) are raw, not hex;
- derived fields are never written, as in XML; the order is that of `Image.export()` and the string table is in order of first use, so a delta has **one** binary form and a digest of it is defined.

The image resolves each distinct (type name, field name) pair once per delta, so names cost per pair, not per field. `delta_digest` stays the digest of the canonical *text* (the tests pin it); a binary delta has its own digest and the host checks that both agree. Not chosen: handles of the `Field` objects (tied to one image, wrong after any change of types) and a tag per value (the type of the field says what the bytes are; a wrong length is a type mismatch).

### 2. `stage` on a journal, not on a copy

A copy doubles the arena at the peak (the starter's is 3.1 MB), and a committed copy is a successor: heap objects, pins and frames of the running world have to move to it. A journal edits **in place**, so nothing moves, a small edit costs a small log, and a refused stage is undone byte for byte. A successor (copying compaction, `emit`) stays what it is: a new arena, made when a successor is wanted, not on every edit.

The arena already has a narrow write path: cells are appended (`create`, `resize`), slots are freed and reused (`destroy`), and everything else is an in-place write into a cell or into an index chunk (which is a cell too). So one primitive is enough:

- `Arena::begin()` records the 192-byte header and the frontier. Everything at or above that frontier is new: a rollback sets the frontier back and needs no log for it (`allocate` zeroes what it hands out).
- A **write barrier** `before_write(offset, length)` is called before any write *below* the recorded frontier: payload and reference bytes, a cell header (kind `DEAD`, `forward`, generation, flags, parent, name), an index slot. It appends (offset, length, old bytes) to the undo log. A range already logged in this transaction is not logged twice.
- `rollback()` replays the log backwards and restores the header (`free_head`, `handle_count`, `names_off`, `names_size`, `index_chunks`, entry, root all come back with it). `commit()` drops the log.
- All writers go through the barrier; `payload()` and `refs()` stay read pointers and a write is `write(handle, offset, bytes)`. The log lives in the heap of the runtime (a `Heap` object, so it is bounded and refuses with `JOURNAL_FULL`), on the host in a vector.

Anything the layers above keep *outside* the arena (the host image's lookup tables for name to child and path to handle) is derived and is rebuilt or invalidated after a rollback. In the image those lookups are built from the arena, never kept elsewhere.

Test: random sequences of create, destroy, resize and writes, rolled back, give the arena bytes of before (up to the old frontier) and the same `verify()`; `Image.apply` on the journal gives the digests of the starter and of the fuzz inputs that the copy gave; a refused delta leaves the digest unchanged.

### 3. The frame: small, fixed, with a CRC; a delta is a byte stream

Measured on the starter: the largest object payload is 11.5 KB (the interpreter on wasm32), 6 of 11,927 Code payloads exceed 4 KiB, the median is 12 bytes. A frame cannot hold every operation, and it need not.

- **Frame.** 16-byte header (magic, op, flags, request id, payload length, sequence), at most **1,024 bytes** of payload, a CRC-32 of header and payload. The CRC is for UART, which has no integrity; shared memory pays almost nothing for it. 1,024 bytes are 90 ms on a UART of 115,200 baud, short enough to interrupt.
- **A delta is a byte stream.** `stage_write(offset, bytes)` fills a staging object on the heap, `stage_commit(length, digest)` checks the SHA-256 and only then parses. A lost or repeated chunk is seen by the offset, and no operation has to fit a frame.
- **Rings.** Each ring holds at least four frames: 8 KiB (a power of two). The ring is a property of the provider, the frame a constant of the protocol.
- **Reads.** `get` returns up to one frame and says `total`; the host asks for the rest by `offset`. The same rule serves a 12-byte field and an 11.5 KB Code.

## Work packages

All packages are present. Package 0 was the journal: `Arena::begin`, `before_write`, `commit`, `rollback` and the writable accessors (`cell_w`, `payload_w`, `refs_w`), `Image.begin/commit/rollback`, and `Image.apply` runs on the journal and no longer copies the image (`tests/test_journal.py`, also under the sanitizers). The log is one bit per 64-byte granule below the end of the cell stream and 72 bytes for each granule a transaction touches; a full log answers `JournalFull` and the transaction is rolled back.

Every package ends with its exit test, runs on every machine of the test environment where it touches native code (x86_64 and aarch64 in the emulator, wasm32 in node, and the reference), and passes the gates: `ruff check` and `ruff format --check`, `pyright`, `clang-format-18 --dry-run -Werror` for `native/` and the C++ of the core, `sync.py --check`, and the full test suite on Python 3.14 and 3.12. A package whose exit test does not pass is not skipped: the work stops there and says why. Nothing is committed by the work itself.

| # | Package | Exit test |
|---|---|---|
| 0 | **Journal on the arena [present]** (decision 2): `begin`, `before_write`, `rollback`, `commit`; the object layer writes through the barrier; `Image.apply` uses the journal instead of the copy. | Rollback is byte-identical on random sequences; starter and fuzz digests unchanged; refused delta changes nothing; sanitizers clean. |
| 1 | **The object layer without the host library [present]** (`native/live.cpp`, freestanding C++ over the arena, the lookup table kept in the `work` region of the live area). | The freestanding build gives the digests of the host build for the starter, in the emulator on x86_64 and aarch64 and in node on wasm32. |
| 2 | **C++ in the native build [present]** (`native_build.py`: freestanding C++, no exceptions, no RTTI, cut out without relocations; the first C++ leaf is the live pump, leaf 29; `tests/test_arena_freestanding.py` builds the arena without the host library). | `rxf binary check` complete; the leaves run identically on three machines; `test_native` still pins the bytes. |
| 3 | **Allocator in the image [present]**: dead cells are released and reused, `COMPACT` slides the live cells in place honoring pins and borrows, `GROW` commits pages (see the limit on running code below). | Native compaction is byte-identical to the core's for the same live set; dump, load, same result. |
| 4 | **The linker in the image [present]**: `RELINK` writes the entry table (sorted by id), the function slots and the context from the arena, the bytes the host `link.py` writes. | The image links its own arena; the bytes equal what `link.py` makes, on three machines. |
| 5 | **Channel [present]**: capability `Channel`, two rings, doorbell, frame codec with CRC-32 (`live/frames.py`, `live.h`), the harness provider. | Frames in and out on three machines, byte-equal to a Python reference; a lost chunk and a flipped bit are found. |
| 6 | **Read operations [present]**: `RESOLVE`, `PATH`, `GET`, `READ`, `CHILDREN`, `REFS`, `DIGEST`, served over the channel (large payloads and child lists in pages). | For every object of the starter the image answers what the core answers. |
| 7 | **The binary delta in the core [present]** as the physical delta (decision 1), `rxf core delta`. | XML to binary to XML is the same text; both forms give the same `Image.digest()`; fuzzed. |
| 8 | **`stage`, `check`, `commit`, `emit` in the image [present]**: `STAGE_RESET/WRITE/COMMIT` (digest, parse, replay on the journal, link check, rollback on any refusal), `EMIT`/`EMIT_READ` (canonical bytes of the image). | A delta applied from inside gives the digest of the core; a refused one changes nothing; the round trip host XML, image, change from inside, dump, host XML equals the same change on the host. |
| 9 | **Safe points and break [present]**: the interpreter serves the channel after a leaf call; `BREAK_REQUEST`/`BREAK_ACK` in the area stop a run at the next safe point (`graph.c`). Compiled code has them too: at every edge that goes back the compiler (leaf 31 and `tiny.compile`, byte for byte) calls leaf 38 `safe_point` (`native/safe.c`), see below. | A host stops a running program, reads, changes, continues; the result equals the run without the stop. A compiled loop is served and broken the same way, and a commit under it ends it with `STALE_GENERATION` (`tests/test_safe_points.py`, three machines). |
| 10 | **Providers [present]**: UART leaf 30 (x86_64 COM1 or 16550/PL011 in memory, aarch64 PL011 or 16550 in memory), shared-memory segment, pty/tty/fd links, `rxf live` on the host (`info`, `digest`, `emit`, `apply`, `patch`, `halt`, `go`, `ping`, `console`), `live:LINK` as a source of every tool. | The inspector shows and edits a running image through each provider. |
| 11 | **First self-edit [present]** (`tests/test_self_edit.py`): the Function `selfedit.double` (a graph of calls of existing Functions) is composed **on the host** into a program and sent as a delta; the running image applies it, relinks itself and runs the new Function on the interpreter without a restart; the image dumps a successor that runs it on three machines, and the dump is editable XML with the host's digest. The graph is made on the host; making it inside the image is package 12. | The successor returns the new result; its dump is editable XML and gives the same digest on the host. |
| 12 | **The compiler in the image [present; fixpoint met for the compiler `native/tiny`, which equals leaf 31 on every program of the corpus]**: leaf 31 `compile_program` writes machine code for every instruction of the interpreter on x86_64, aarch64 and wasm32, in the image (`native/compile.c`). `tiny.compile` is the same compiler as a program (`native/tiny/*.rxf`, about 650 KB of words, a Function of the Bytecode format in the starter): every instruction, every shape of leaf, handles, objects, safe points; see below. | The code of a program equals what the interpreter computes (met, all 58 programs, 174 argument sets, three machines). Stage 2 and stage 3 of `tiny.compile` are byte-equal on x86_64, aarch64 and wasm32 (met, `tests/test_tiny.py`), and `tiny.compile` writes the bytes and the refusals of leaf 31 on all of the corpus, with and without a Channel (met, `tests/test_tiny_corpus.py`). |

Packages 0 to 4 are the foundation and each stands on the one before. 5 and 7 are independent of 1 to 4 and can be worked in between. 6 needs 1 and 5, 8 needs 0, 1, 3, 4, 5 and 7, 9 needs 5, 10 needs 6 to 9, 11 needs 8 and 10, 12 needs 11. Nothing in this list needs a scheduler, a file system or a network stack.

## Where the packages stop

These are the limits of what exists, not of the plan.

- **The code that is running moves itself out of the way.** The pump is an object of the arena, and in canonical order it is somewhere else. A compaction therefore does not run in it: the pump stops in front of the `COMPACT` frame (`relocate` in the area), its leaf (`native/live.cpp`) copies its own body into the log region, which no request uses meanwhile, and continues there with a tail call, so the original is never returned to. The copy serves that one compaction (the log region is its ground and a delta needs it, so what waits behind it is served by the next call), the function table points at the new place afterwards, and `relocated` is cleared. Every other leaf moves with its Code: only what runs is pinned. The body has no relocations, which is what lets it be copied; the copy finds itself through its slot in the function table and checks that its own instruction is inside the entry it found; on aarch64 the instruction cache is made coherent (`dc cvau`, `ic ivau`) before the jump. Declined, as before, with reason 13 and the arena untouched: when the interpreter or compiled code is on the stack below the pump (`in_run` in the area: it holds the addresses of every leaf and its own body), and when the log region is too small for the copy. wasm32 holds its code outside the arena: there the compaction always goes through. (`tests/test_live_native.py`: the compaction equals the host's byte for byte and links like the linker, the slot moved, the pump serves on; the declines.)
- **Compiled code has safe points, at the edges that go back.** Leaf 31 and `tiny.compile` compile every instruction (`CALL` with and without handles, `MOVE`, `MAP`, `FIELD`, `CONSTRUCT`, `LESS`, `INCREMENT`, `GOTO`, `BRANCH`, `SWITCH`, `RETURN`, `REFUSE`, edges, loops), all 58 programs of the starter, in two passes with a work region for the offsets. It refuses a jump to a word that is not an instruction, an unaligned slot where a value is compared or counted, and an odd width, with `UNSUPPORTED` and the word (`at`). When the context has a Channel and leaf 38, an edge that goes back (a target at or before the instruction itself: every loop has one) is written as its copies, then a call of `safe_point(context, area)` with the area as a literal, then the jump. There the state is whole, as between two instructions of the interpreter. The leaf (`native/safe.c`) looks at the doorbell, runs the pump while a request waits and while the peer holds the break, with `in_run` set. Compiled code holds the addresses of its moment, so if the pump changed the arena or the links under it (the `epoch` word of the area moved: a commit, a compaction, a relink; reading does not move it) the leaf answers `STALE_GENERATION`, the compiled code returns it as its own status, and the caller compiles the program again; the interpreter, which reads its program again, simply goes on. The cost is one call per iteration of a compiled loop; there is no inline test of the doorbell yet. Without a Channel in the context nothing is added: the code is what it was. A compiled function and the interpreter give the same status, value and whole memory on every argument set, with and without the Channel, on all three machines (`tests/test_compile_cases.py`, `tests/test_compile.py`); the safe points themselves are proved by `tests/test_safe_points.py` (served mid-loop, break held and released, stale after a commit, not stale after a read).
- **Self-compilation, and what it is not.** `compile_program` (leaf 31) stays a native snippet made by clang: the seed, stage 0, which is what a bare machine can run before any program exists. The fixpoint is met by the same compiler written in the language `.rxf`, `tiny.compile` (`native/tiny/*.rxf`, made into a program by `tools/native/rxfc.py`: statements, `proc`/`macro`/`fn`, every operator one leaf call, nothing hidden). It takes a request (program, size, output, capacity, work region, machine 1 to 3, the table of the leaves), runs in two passes like `compile.c` and writes **the same bytes**, per machine, as leaf 31 does, and refuses what leaf 31 refuses, with the same word. It is not a subset: `tests/test_tiny_corpus.py` holds it to leaf 31 on every program of the starter and every program of `graph_programs` (all opcodes, every shape of leaf, loops, switches, edges with copies, programs that are refused), with and without a Channel, compiled in the same emulator by leaf 31 itself, on all three machines. The stages (`tests/test_tiny.py`, 28 tests): **A** is leaf 31 compiling `tiny`; **I** is the interpreter running `tiny` on `tiny` and gives the bytes of A; **B** is I as machine code running on `tiny` and gives I; **C** is B running on `tiny` again and gives B. So I = A = B = C, and from stage B the compiler needs no interpreter. Leaf 31 stays, because a seed has to be code that was not made by what it makes; if the two ever differ the corpus test says where. What is not in the image: the `.rxf` sources are the program and are kept in `native/tiny` (the starter holds the compiled words, `tiny.compile`, and the digest of the sources); `rxfc.py` runs on the host because the language has no front end in the image, and the composer (graph to program, `compose.py`) is the host's too. A front end and a composer in the image are programs of the size of `rxfc.py` (870 lines) and `compose.py` (790 lines) in a language without recursion or a heap of its own; they are the next programs of this kind and are not started.
- **The UART** exists on all three machines, and on x86_64 and aarch64 in more than one kind (<a id="the-uart-devices"></a>the devices). The capability has the base of the device as its address and, in its generation word, what the device is (`native/graph.h`): the kind in the low byte, the distance of its registers as a shift in bits 8 to 11 (register n is at `base + (n << shift)`), and a flag for a device whose registers are read and written as 32-bit words. Kind 1 (or 0) is the one of the machine: on x86_64 the 16550 at an I/O port (the same bytes serve BIOS and UEFI on a PC that has COM1), on aarch64 the PL011 in memory. Kind 2 is a 16550 in memory (the 8250 of many boards, the mini UART of a Raspberry Pi, the one of a PC chipset), kind 3 a PL011 in memory, kind 4 the 16550 at an I/O port; kinds 2 and 3 exist on both machines, kind 4 on x86_64 only (aarch64 has no port I/O, and answers `UNBOUND`, as it does for a kind that does not exist). On wasm32 a block of linear memory with a receive and a transmit FIFO (layout in `native/graph.h`) because wasm has no port and no trap on a load; the host is the line and is slow by construction (256 bytes between two stops, so a dump of the image takes many stops). `tests/test_live_uart.py` runs the client over each of eight devices (x86_64: ports, 16550 in memory with shift 0 and 2, PL011 in memory; aarch64: PL011, PL011 spelled out, 16550 in memory with shift 0, and with shift 2 and wide registers). A device that needs more than this (a clock or a baud rate to set, an interrupt line, DMA) is not written: the leaf uses a device that firmware has set up.
- **The browser provider** is not part of this repository.

## What this plan does not do

- No IP stack, no HTTP, no file system. A file system comes with a block driver, which needs the driver foundation of doc 17.
- No concurrent writers. A second writer needs atomics and a scheduler.
- No hardware interrupts. A break is polled at safe points. A real interrupt comes with the IDT and APIC work of doc 17, and only makes the break faster.
- No other tree. The seats of other repositories are not part of this plan.
