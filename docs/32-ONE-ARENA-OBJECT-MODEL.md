# One arena, one object model

This document is the target design for the one-arena rework (Wave 7 of
[the self-hosting plan](31-SELF-HOSTING.md)). It supersedes the split index/cell
layout of [the binary model](README.md#rxfb-6-binary-model) with a single arena in which
*everything* is an object: the tables that describe objects are themselves objects,
reachable by name in the module tree. The image is self-describing: nothing in it is
meta.

The design answers four questions:

1. What is the fixed part of an image, and what does an object look like?
2. How is the object tree shaped, and where do the system tables live?
3. How does allocation work, and what happens when the arena is full?
4. What does the model cost at run time, and what does it buy in parallelism?

---

## 1. Image layout

One linear region, addressed by 64-bit offsets from the arena start. The only fixed
structure is a 4 KiB superblock at byte 0. Everything after it is arena.

```
+---------------------------------+  <- arena start (byte 0)
| superblock                      |  4 KiB, fixed, the only non-object
|  magic, version, committed,     |
|  limit, root handle, entry      |
+---------------------------------+  <- byte 4096, 16-byte aligned
| first objects                   |  the boot/sys skeleton (see 2)
|  each: 24-byte header + payload |  8-byte aligned, packed
|  ...                            |
|  ...                            |
|  end marker                     |  free-space header, name = FREE ring
+---------------------------------+  <- committed
| unused, up to limit             |  claimed or grown (see 3)
+---------------------------------+  <- limit
```

### 1.1 Superblock

| offset | size | field |
|---|---|---|
| 0x00 | 8 | magic (`RXFA`) + format version |
| 0x08 | 8 | `committed` — first free offset |
| 0x10 | 8 | `limit` — end of the claimed region |
| 0x18 | 4 | root handle |
| 0x1c | 4 | entry handle |

```
0x0000 | 52 58 46 41 00 00 00 01 | magic, version
0x0008 | 00 00 00 00 00 80 00 00 | committed
0x0010 | 00 00 00 00 08 00 00 00 | limit
0x0018 | 00 00 00 03 00 00 00 07 | root handle (3), entry handle (7)
       +-------------------------+
       | (fill to 4096, must be zero, reserved) |
       +-------------------------+
```

No further fields. If a later format needs more, the superblock has 4 KiB of room and
the version byte is the gate.

### 1.2 The object header

24 bytes, then the payload inline, padded to 8 bytes:

| offset | size | field | meaning |
|---|---|---|---|
| 0x00 | 2 | `kind` | Module, Func, Hash, Spec, Sym, Blob, Frame, … |
| 0x02 | 2 | `gen` | generation (stale-handle detection) |
| 0x04 | 2 | `flags` | RO, ROOT, DIRTY, JOURNALED, … |
| 0x06 | 2 | `name` | symbol id (interned, 0 = anonymous) |
| 0x08 | 4 | `parent` | handle |
| 0x0c | 4 | `link` | next sibling in the parent's ring |
| 0x10 | 4 | `refs` | reference count |
| 0x14 | 4 | `payload` | payload length in bytes |
| 0x18 | 8 | payload bytes … (inline, `ceil(payload/8)` slots) |

```
0x00 | 00 01 00 00 03 00 42 00 | kind=Module, gen=0, flags=RW, name="sys.objects"
0x08 | 00 00 00 02 00 00 00 05 | parent = 2 (root), link -> 5 (sys.functions)
0x10 | 01 00 00 00 20 00 00 00 | refs = 1, payload = 32 bytes
0x18 | ... payload: children ring head, hash ring head, ... |
```

The header never changes size. Old fields are reused; new needs go behind a fresh
`kind` with its own payload layout. Everything a cell says about itself lives in the
cell — there is no table outside the arena that the arena depends on.

Payload kinds seen today: byte blobs, slot tables (function bodies), hash buckets
(256 heads, each a ring of `hash_next` links), interned strings, and ring headers.
Rings are circular and doubly linked *within* the payload (`prev/next` slots); the
header `link` field is the single forward chain of a parent's children.

### 1.3 Gaps and free space

Cells pack back to back. When a span frees, it becomes a **free cell**: same 24-byte
header, `kind = FREE`, `name = 0`, and the free cells are kept in a `FREE` ring so a
later allocation can split one. A free cell's payload carries the ring links (`prev`,
`next`, the arena offsets of the neighbours), so a ring cell is 40 bytes or more; a
freed span below that stays linkless until compaction takes it. The end marker — the
free span at `committed`, the anchor of the ring — covers the tail and grows when the
region grows; releasing the span adjacent to it merges into it. The allocator takes
the best-fitting cell and splits it when the remainder can carry links (40 bytes or
more), and the generation of a reused cell is bumped so a stale handle fails its
check.

---

## 2. The object tree

The root object is the image itself (`name = image`, e.g. "boot"). Under it, the
former world-map tables become named modules — that is the whole trick: lookup by
name is a walk into a module, not a magic offset.

```
image ("boot")                      <- root handle
├── sys/                            <- system namespace
│   ├── objects/                    <- object index: kind + slot + gen table
│   ├── functions/                  <- function index: entry, spec, arity
│   ├── journal/                    <- undo/redo ring, granularity, base
│   ├── caps/                       <- capability cells (io, net, time, …)
│   ├── free                        <- FREE ring head (allocator state)
│   └── syms                        <- interned symbol table (string cells)
├── pymergetic/                     <- the Python module tree (starter/)
│   └── rxf/ …
├── io/ net/ time/ …                <- capability-backed faces
└── …
```

Laws:

- **The name tree is acyclic.** Parent/child via `parent` + `link` forms one tree.
- **Rings are weak navigation only.** A ring may close on itself and cross modules;
  it never carries ownership. `link`, `hash_next`, `journal ring`, `name ring`.
- **Ownership is a tree (or refcount-DAG) and is acyclic.** The journal unwinds it
  in reverse.

Objects of code are objects too: a function is a named object whose payload is a
slot table (the compiled body); anonymous bodies live under their stem with variant
names (`__mul_x64`, `__mul_asm`). Nothing outside the arena knows their addresses —
callers hold handles.

## 3. Allocation

The allocator is `s_allocate` plus `s_ensure`, in [the live engine](../native/live.rxfs).
One interrupt walk to fix it in mind:

1. `s_allocate(64)` finds a free cell (the `FREE` ring, best-fit/split).
2. The cell is zeroed; the caller writes the header fields.
3. `s_ensure(at, n)` checks committed headroom for the payload write. If the write
   would cross `committed` into unclaimed space, the growth hook fires (see 3.1).
4. Every granule written is recorded by the undo journal (`before_write`) **before**
   the store. The write lands. The handle is published (`kind`, `gen` bump on reuse).
5. At commit the journal ring is dropped; at refusal `m_rollback` walks the ring in
   reverse and restores every granule. A crash mid-write leaves exactly the
   superblock + committed state from the last commit.

Because the journal's metadata lives in `sys/journal/` — an object — the journal can
grow with the arena and can itself be journaled (its header changes are undoable).

### 3.1 Arena sizing per seat

- **Bare metal (BIOS/UEFI seats):** the arena claims **all of RAM** minus runtime
  needs. The size comes from the firmware memory map — E820 (BIOS) or
  `GetMemoryMap` (UEFI) — no parameter needed. `limit` = end of the largest usable
  region; `committed` grows into it on demand.
- **Hosted seats (Linux):** a size parameter with a sensible default (in bytes),
  grown by `mmap`/`mremap` when `s_ensure` runs out. Growth extends `committed` /
  `limit` through the seat hook. The fixed-region case (`PYMERGETIC_RXF_FIXED_REGION`)
  is just the hook that refuses.
- **wasm:** `memory.grow`, with an initial and a maximum; same hook shape.

This is what makes the arena one object model everywhere: the sizing policy is a
seat fill; the arena logic is identical.

## 4. What it costs, what it buys

**Cost — lookup is one indirection.** A handle is `(index, gen)`. Resolution is
`slot[index]`, one array hop; the generation check makes stale handles *structurally
visible* instead of undefined behavior. No hashing, no tree walk on the hot path:
the module tree is for **name resolution**, and resolution caches handles. Compared
to a vtable call or a bounds-checked object map this is noise on the workloads that
matter (journal writes, frame pumping). If a profile ever disagrees, the escape
hatch is a per-caller slot cache — one inline load instead of two.

The allocator really is deliberately baroque — free-ring split, generational
handles, per-granule undo, growth hooks — for a flat bump. But the point is not
allocation speed. The point is that allocation is the *only* primitive the object
model needs, and everything else (modules, tables, journal, caps) is composed from
it, inside the arena.

**Buy — parallelism falls out of the model.** The pieces that make the object model
safe are exactly the pieces that make concurrency cheap:

- **Rings without locks.** SPSC rings (`rx`/`tx`) are the communication primitive;
  a ring needs no lock (see [the live engine](../native/live.rxfs) header notes).
- **Handles, not pointers.** Cross-thread sharing has no use-after-free: a generation
  bump makes a stale handle fail its check instead of corrupting memory.
- **Journal = transactions.** Any thread or async task may write; undo/commit makes
  the set atomic and crash-consistent. No lock graph, no deadlocks — the journal is
  the only global ordering.
- **Threads and async are unified.** The frame pump is asynchronous by
  construction: request in, response out, nothing blocks between. A thread is a
  task with its own frame; an async is a task sharing the pump. Same arena, same
  journal, same rings. How many pumps run is a runtime-provider detail, not a
  model distinction.

That combination — lock-free parallelism, crash consistency, and object identity
across threads from one mechanism — is the return on the lookup overhead.

## 5. Related

- [The self-hosting plan](31-SELF-HOSTING.md) — waves, and where this rework lands.
- [The arena and allocator state](03-ALLOCATOR-STATE.md) — the current state fields.
- [The memory system](24-MEMORY-SYSTEM.md) — cells as linked memories.
