# The global heap and allocator state

RXF v5 has one `HeapImage` and one cell stream. Pages are allocator geometry and derived policy, not semantic objects or lifecycle boundaries. Ownership may guide page clustering, but clustering creates no identities and does not change parentage or lifetime.

The heap tracks uint64 `image_size`, `committed_size`, optional known `limit`, and `frontier`, plus alignment and page size. `image_size` is the exact serialized extent measured from image/file base zero; the heap cell frontier is relative to `heap_off`. The invariant is `frontier <= committed_size`, `image_size <= committed_size`, and, when the limit is known, `committed_size <= limit`. An unknown limit does not authorize a write beyond committed bytes. `UINT64_MAX` is the sole on-wire unknown-limit sentinel; all persisted sizes and limits remain uint64 regardless of target, and a 32-bit loader must reject values that cannot be checked and converted to local `size_t`.

Allocation in this first slice is a deterministic aligned frontier bump. It refuses duplicate IDs, committed-boundary overflow, and known-limit overflow. There are no free lists, domains, arenas, or per-owner regions in the image.

Image construction is copying compaction, not mutation. `container_to_layout`:

1. snapshots retained objects without changing the source graph;
2. omits transient and tombstone objects;
3. rejects every mandatory reference to an omitted object;
4. orders retained data-bearing objects by durable ID;
5. copies payloads into a compact global stream;
6. preserves ID, type, semantic parent, owner, generation, and payload;
7. emits `IMAGE / RETAIN / CLEAN`, preserving mobility.

A pinned source object may be copied because pinning constrains the running source address, not construction of a successor image. The source stays unchanged. Optional references to excluded objects are omitted in the emitted image.

Deferred allocator work includes reclamation strategy, free-span reuse, owner-aware page clustering, page commitment/decommitment, and concurrent allocation. Those policies must continue to preserve the one-heap and one-ID-space invariants.
