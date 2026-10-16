# The arena and allocator state

An RXF image is one arena: an arena header, an index directory, and a cell stream that holds object cells and block cells (index chunks and name blocks) (layout in [the operating-system path](17-OPERATING-SYSTEM-PATH.md), "Arena and RXFB v6", and `src/pymergetic/rxftk/core/ground.h`). Pages are allocator geometry and derived policy, not semantic objects or lifecycle boundaries. Ownership may guide page clustering, but clustering creates no identities and does not change parentage or lifetime.

## Identity and handles

The identity of an object is its path, unique per parent. A handle is an image-local `uint64` slot number assigned by the allocator. It is stable while the image is live, it never appears in a delta, and it is not an identity across images. A successor may assign different handles to the same paths.

## State

The arena header holds `body_size`, `committed_size`, `limit`, and `frontier`, plus alignment and page size, all `uint64` where they are sizes. The header also holds `handle_count` (slots handed out) and `free_head` (the first free slot, or `PYMERGETIC_RXF_HANDLE_NONE`).

- `body_size` is the exact serialized extent measured from the arena base. `frontier` is relative to `cells_off`.
- The invariants are `frontier <= committed_size`, `body_size <= committed_size`, and, when the limit is known, `committed_size <= limit`.
- An unknown limit does not authorize a write beyond committed bytes. `UINT64_MAX` is the sole on-wire unknown-limit sentinel. All persisted sizes and limits remain `uint64` regardless of target, and a 32-bit loader must reject values that cannot be checked and converted to local `size_t`.

## Bootstrap

An empty image has 99 handles, the **ground**: the root (0), `Type` (1), `Field` (2), `uint32_t`, `uint64_t`, `Enumerator`, the eight enumerations of the format (`NodeKind`, `RefKind`, `Binding`, `OwnerKind`, `Mobility`, `Form`, `TypeFlags`, `FieldFlags`) with their enumerators, and the `Field` children that describe `Type` and `Field`. They are created by `Image::bootstrap()`, are the same in every image (the link check compares them with a fresh bootstrap), and cannot be changed or deleted by a delta. The root takes children. Everything else is created by operations ([15](15-DELTAS-AND-XML.md)) and described by objects below `Type` and `Field` ([16](16-SELF-DESCRIPTION.md)).

## Allocation

A new cell takes a handle from the free-slot chain (`free_head`) or, when the chain is empty, the next number from `handle_count`. The cell is placed at the frontier, 16-aligned, as header, payload, and references. Allocation is a deterministic aligned frontier bump. It refuses a committed-boundary overflow, a known-limit overflow, and a duplicate name under one parent. The index grows by whole chunks. A chunk is a block cell, so growth is one more allocation at the frontier. The directory has a fixed capacity; when it is full, `create` returns `IndexFull`, and the way on is a compaction into an arena with a larger directory. A name is copied into the open name block; when it does not fit, a new block is opened. There are no domains, arenas, or per-owner regions inside the image.

## Compaction

Image construction is copying compaction, not mutation. The compaction pass:

1. snapshots retained objects without changing the source arena;
2. omits transient and tombstone objects;
3. rejects every mandatory reference to an omitted object;
4. orders retained objects by path;
5. copies payloads and references into a compact cell stream and assigns dense handles in that order;
6. preserves path, type, semantic parent, owner kind, generation, and payload, and keeps references by path so the new handles resolve;
7. emits `IMAGE / RETAIN / CLEAN`, preserving mobility.

A pinned source object may be copied because pinning constrains the running source address, not construction of a successor image. The source stays unchanged. Optional references to excluded objects are omitted in the emitted image.

Compaction in place (sliding, honoring pins and borrows) must produce the same bytes as copying compaction for the same live set. Cells that moved leave a forwarding offset in the cell header until the pass finishes.

## Deferred

Reclamation strategy, free-span reuse, owner-aware page clustering, page commitment and decommitment, and concurrent allocation. These policies must continue to preserve the one-arena and path-identity invariants.
