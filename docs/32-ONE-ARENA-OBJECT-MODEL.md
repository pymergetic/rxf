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
│   ├── specs                       <- semantic rows: type, flags, index, generation (5)
│   ├── journal/                    <- undo/redo ring, granularity, base
│   ├── caps/                       <- capability cells (io, net, time, …)
│   ├── free                        <- FREE ring head (allocator state)
│   ├── syms                        <- interned symbol table (string cells)
│   └── relations/                  <- relation tables, one child per related object
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

Relations are objects too: `(target, role, index, kind, binding)` is the pinned
24-byte reference record (`pymergetic_rxf_ref`), and an object's relations are a
table of those records in a named object under `sys/relations` - one child per
related object, named by its dotted path. `target` and `role` are arena handles
(`(index, gen)`, zero-extended into the record's 64-bit fields); a dangling
target is refused at write time. Relations are data, not navigation: the name
tree stays a tree, and only modules hold children.

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

## 5. The semantic face of an object

One question the layout above does not answer, and every consumer of the format
asks: **where do the semantic fields of a world object live?**

A world object — the model the host toolchain and [the delta format](15-DELTAS-AND-XML.md)
speak — is more than a record. The cell of the superseded binary model carries
`type` (a handle), `generation` (64 bits), `flags` (32 bits, the packed state
among them: provenance, disposition, cleanliness, mobility), and the position
among the siblings. The delta form carries the same fields as `type` (a path),
`flags`, `index`, `generation` (`pymergetic_rxf_delta_object` in
[the live header](../src/pymergetic/rxftk/core/live/live.h)). The 24-byte record
carries none of them: `kind` is the storage role, `gen` is 12 bits of
stale-handle detection, `flags` is 16 bits of storage state (RO, ROOT, DIRTY,
JOURNALED), and the sibling position is the chain order.

Nothing can migrate to the new arena without this answer:

- **the live engine** (`native/live.rxfs` and `core/live/live.cpp`, which must
  move together): `GET` answers a cell header, the apply writes `type`, `flags`,
  `index`, `generation` from a delta, and the parity contract of
  `tests/test_live_native.py` holds the two builds to the same answers;
- **the scene reader**: the kind word, the state word and the type-derived field
  edges of a scene document;
- **the delta apply on the new storage**: `Create` and `Set` carry those fields
  and a refused round trip is a digest break.

### 5.1 The decision: `sys/specs`

The semantic fields are one row per object in a table object, `sys/specs`, keyed
by the same index as the slot table. The options that were weighed - a prefix in
every payload, the fields as relations, and packing them into the spare bits of
the record and the slot entry - lost to this one for the same reason: the value
bytes of an object stay what the world says they are, which is the property every
reader, the linker and the digests lean on, and the table is the pattern the model
already has (`sys/objects`, `sys/relations`: a table is an object).

```
pymergetic_rxf_spec - 24 bytes, ground.h
0x00 | 4 | type        handle32 of the Type object, HANDLE32_NONE = untyped
0x04 | 4 | flags       the world's packed flags word: kind | state | owner
0x08 | 4 | index       position among the ordered siblings, 0 = the order is the name
0x0c | 4 | reserved    zero
0x10 | 8 | generation  the world's 64-bit generation
```

Laws of the table:

- **The flags word is kept whole.** Its layout is the v6 cell's
  (`PYMERGETIC_RXF_FLAG_*_SHIFT`: node kind in bits 0-7, state in 8-13, owner in
  14-17). The record's `kind` is the storage role and flattens the node kind (a
  data object, a module's group and a card are all BLOBs or MODULEs there); the
  row is where the node kind survives.
- **The payload is the rows and nothing else**, `capacity` of them - the capacity
  of the object index - so row `i` is at `24 * i` and an index never has a row
  missing. The table does not grow: the index does not either.
- **A new object has the default row** - untyped, everything else zero - until its
  creator writes one. A released index goes back to the default, so a reused index
  carries nothing of its previous life.
- **A row is written through the journal** like every other store: a rollback
  restores the last commit's row, and `spec` / `set_spec` check the handle before
  they touch the row, so a stale handle answers no row.
- **The table is made before the journal attaches**, like the index and syms: it
  is boot ground, and a journaled fill of its rows would be the biggest thing the
  undo ring ever recorded. It is protected like the index: it cannot be released.
- **It is found by name once**, and the handle is kept: a cold attach has nothing
  cached and reads `sys/specs` as any other object.

### 5.2 The reader's side: path and refs

Two lookups every consumer needs, and neither is stored in the record:

- **The path of an object** is its names below the root, joined with `.` — the
  root itself is the image and no path segment. It is computed by walking
  `parent`, so it never has to be stored twice. An anonymous object contributes no
  segment. `path_of` answers it, and answers `OutOfMemory` with the length the
  buffer has to offer.
- **The refs of an object** are the rows of `sys/relations/<path>`: the table is
  named after the object's path, so the path is the lookup. `refs_of` answers the
  pinned `pymergetic_rxf_ref` rows; an object with no table has no refs and
  answers a count of zero, which is the common case.

A reader therefore resolves an object the way the exporter wrote it: walk the
tree by name to the object, read its spec row for what the world says about it,
and look its path up under `sys/relations` for what it points at. Nothing in that
read touches the record's payload, so the value bytes stay pure.

**Lookup cost, and the live layer's derived index.** A table is named by the
path, so a lookup is a name comparison and a walk costs as much as the table set
has members. That is fine for a reader that asks a few times and wrong for a
consumer that asks per object over a whole image (the canonical form, a relink, a
compaction). Such a consumer builds a derived index once - the object index of a
relation table, or the table of an object - exactly the way the live layer already
builds its name lookup (`work` region, docs/19) and the host `Image` keeps its
parent and name maps. It is derived state: the path stays the one identity, the
index is rebuilt whenever the tree changes, and nothing writes through it.

The writer fills the rows in a third pass (`tools/native/arena_export.py`), after
the tree and the relation tables, because a Type can only be named once every
object is placed; the Genesis objects the writer adds have the default row. The
live engine carries the table next: `GET` answers its row, the apply writes it.

A consequence: the canonical digest of an image is the digest of its canonical
bytes in *this* model, and the old and the new build answer the same one only when
they serve the same arena.

### 5.3 The world cell of the live engine

The live engine speaks a *world cell*: `GET` answers one, a delta writes one. In
the superseded model a cell was a 64-byte record with its refs inline behind its
payload. Here a cell is not stored, it is *derived* - the record and its spec row
together - and the engine materializes it into a 64-byte view whose field offsets
the protocol keeps:

```
0x00 | 8 | type          spec.type, a handle32 zero-extended
0x08 | 8 | parent        record.parent
0x10 | 8 | payload_size  record.payload
0x18 | 8 | generation    spec.generation
0x20 | 8 | name          the offset of the name string from the arena start
0x28 | 4 | flags         spec.flags - the world's packed word (kind | state | owner)
0x2c | 4 | ref_count     the rows of sys/relations/<path>
0x30 | 8 | forward       the compaction home (docs/20 S7: the record's `link` word)
0x38 | 4 | index         spec.index
0x3c | 4 | reserved      zero
```

A view is good until the next call of the accessor that filled it; a call that
keeps two cells takes a second view. The value bytes and the reference rows are
*not* in the view - they stay real pointers into the arena, so a read of a
payload or a ref row never copies.

What the superseded model had that this one does not, and what replaces it:

| superseded | here |
|---|---|
| a block cell (kind 255): index chunks, name blocks | the slot table at byte 4096 and `sys/syms` |
| a dead cell (kind 254) left in the stream | a released index entry, and a FREE span in the ring |
| the index chunk directory at `index_off` | the slot table's entries |
| `names_off` / `names_size` | the `sys/syms` object (relocates like any object) |
| `handle_count`, `free_head` in the header | the slot head's `count`, and the FREE ring anchor |
| inline ref rows behind a payload | `sys/relations/<path>` |
| `forward` in the cell header | the record's `link` word while a compaction is in flight |

The engine's allocator is this model's allocator: best fit over the FREE ring,
the end marker never taken whole, a split keeping the ring position of the taken
span, and the generation of a reused span bumped so a stale handle keeps failing.
Its undo is the granule log of docs/19, which covers the arena bytes above the
superblock; the superblock's own words are saved and restored by `begin`.

---

## 6. Related

- [The self-hosting plan](31-SELF-HOSTING.md) — waves, and where this rework lands.
- [The arena and allocator state](03-ALLOCATOR-STATE.md) — the current state fields.
- [The memory system](24-MEMORY-SYSTEM.md) — cells as linked memories.
