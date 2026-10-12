# RXF object model

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

RXF has one object graph. The identity of an object is its **path**: its name, unique among the children of its parent, joined with the names of its parents up to the root. A path is the same in every image and in every delta.

A **handle** is the image-local `uint64` slot number of a loaded object. The allocator assigns handles when it loads or creates an object. A handle is never a table index into a format table, never a heap offset, and never appears in a delta. Two images that hold the same path may use different handles. Parent, type, and reference targets are handles inside an arena and paths outside it. The invalid handle is `PYMERGETIC_RXF_HANDLE_NONE` (`UINT64_MAX`). The root, `Type`, and `Field` have the fixed handles 0, 1, and 2 (`PYMERGETIC_RXF_HANDLE_ROOT`, `PYMERGETIC_RXF_HANDLE_TYPE`, `PYMERGETIC_RXF_HANDLE_FIELD`); the header also records the root and the entry.

`NodeKind` values `0..8` retain their meanings. Numeric slot `9` is reserved and must never be decoded as an object kind; later values remain `SECTION=10` through `TRANSACTION=18`.

A cell records semantic parentage, an `OwnerKind`, and orthogonal object state. Ownership classifies responsibility as `SYSTEM`, `USER`, `APPLICATION`, `BUILD`, `DEVICE`, `EXTERNAL`, or `SHARED`; it is not identity, lifetime, a storage domain, or a heap. The kind is stored in the cell flags. An owner more specific than its kind is an optional reference of kind `OWNER` to an ordinary object. Classification does not require owner objects.

Object state has four independent axes:

- provenance: `RUNTIME` or `IMAGE`;
- disposition: `RETAIN`, `TRANSIENT`, or `TOMBSTONE`;
- cleanliness: `CLEAN` or `DIRTY`;
- mobility: `MOVABLE`, `PINNED`, or `RELOCATABLE`.

Kind, state, and owner kind are packed into the cell flags. Unknown bit patterns and invalid enum values are rejected. Lifecycle disposition stays separate from ownership.

Every retained object has one cell in the arena: a 64-byte header (with, besides type, parent, name, flags, and generation, the `index` of the object among its ordered siblings), the payload, and its references. Within the arena, `body_size <= committed_size`, `frontier <= committed_size`, and a known limit bounds `committed_size` (see [the arena and allocator state](03-ALLOCATOR-STATE.md)). References name handles and optional `uint64` sub-object offsets, never addresses.

## Typed modules and names

`MODULE=19`, `ENUMERATOR=20`, and `ALIAS=21` are the next stable `NodeKind` values; slot `9` remains reserved and values `10..18` are unchanged. A Module is an ordinary object typed by the built-in `Module` type. Its fixed eight-byte payload consists of typed `ModuleCategory` and `ModuleFlags` values, described by ordinary FIELD objects under the Module TYPE.

A Module is only organizational. It is not a storage domain, heap, lifecycle, ownership class, Python package, or hidden namespace mechanism. Its parent is ROOT or another Module. All non-root names are one non-empty path component, and names under one parent are unique.

The path is derived by walking parent links to the root and joining the names with dots. It is not stored as a string and not kept in a parallel index; the name and the parent link are the identity. The walk refuses missing parents and cycles. FIELD objects remain children of their TYPE, so `Type.form` follows naturally from the same rule.

These three are the only objects whose handles the format fixes, because the allocator and every reader need them before any lookup works. All other objects, including every other type, are found by path. Native Code receives the handles of the types it checks at link time, not as constants in its bytes.

The canonical built-in hierarchy starts:

```text
root
├── void, bool
├── uint8_t, uint16_t, uint32_t, uint64_t
├── int8_t, int16_t, int32_t, int64_t
├── float, double
└── pymergetic
    └── rxf
        ├── model
        ├── execution
        │   └── basic
        └── application
            └── counter
```

Thus the canonical unsigned 64-bit type is `uint64_t`; no generic `uint` type is introduced.


## Canonical portable primitives

The persisted primitive family is `void`, `bool`, the four exact-width unsigned and signed integers, and IEEE-754 `float` (binary32, size/alignment 4) and `double` (binary64, size/alignment 8). All use canonical little-endian wire bytes. `SIGNED` describes integer signedness, so it is set only on `int*_t`, not floating-point TYPEs.

RXF does not persist a native raw pointer, `size_t`, or `uintptr_t` primitive. Those vary with target ABI and would make an image architecture-dependent. Handles, sizes, and heap sub-object offsets use `uint64_t`; pointer width and native addresses, where needed for execution, belong in target-specific metadata and relocation/binding state.


## Templates and traits

Generic and trait declarations are ordinary typed DATA objects. They are organized by modules under `pymergetic.rxf.model`, and their fixed payloads are described by FIELD objects like every other type. Handles and object references are `uint64`; enums, flags, and counts are `uint32`. Their data is in the starter (`pymergetic.rxf.model`, `pymergetic.rxf.templates`, `pymergetic.rxf.traits`). Composition through fields and traits is supported; class inheritance is not.
