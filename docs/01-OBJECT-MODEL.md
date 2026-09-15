# RXF v5 object model

RXF has one semantic identity space and one object graph. `ObjectId`, `NodeId`, `TypeId`, parent, owner, reference targets, and generations are unsigned 64-bit values. `0` is the root ID. `UINT64_MAX` is the only invalid ID. IDs are durable identities, never table indexes or heap offsets.

`NodeKind` values `0..8` retain their meanings. Numeric slot `9` is reserved and must never be decoded as an object kind; later values remain `SECTION=10` through `TRANSACTION=18`.

A node records semantic parentage, an optional ordinary owner link, an `OwnerKind`, and orthogonal object state. Ownership classifies responsibility as `SYSTEM`, `USER`, `APPLICATION`, `BUILD`, `DEVICE`, `EXTERNAL`, or `SHARED`; it is not identity, lifetime, a storage domain, or a heap. The owner field refers to an ordinary object in the same ID space. The first slice permits classification without requiring owner objects.

Object state has four independent axes:

- provenance: `RUNTIME` or `IMAGE`;
- disposition: `RETAIN`, `TRANSIENT`, or `TOMBSTONE`;
- cleanliness: `CLEAN` or `DIRTY`;
- mobility: `MOVABLE`, `PINNED`, or `RELOCATABLE`.

Typed enums are packed into flags. Unknown bit patterns and invalid enum values are rejected. The node-table state and cell-header flags must agree. Lifecycle disposition stays separate from ownership.

Every data-bearing retained object has one cell in the global heap. The serialized file extent is `image_size`; `image_size <= committed_size`, `frontier <= committed_size`, and a known limit bounds `committed_size`. References name IDs and optional uint64 sub-object offsets, never addresses. Sections use global heap/image offsets. TYPE and Code indexes likewise map IDs directly to global cell offsets.

## Typed modules and names

`MODULE=19` is the next stable `NodeKind`; slot `9` remains reserved and values `10..18` are unchanged. A Module is an ordinary node typed by canonical type ID `27` (`Module`). Its fixed eight-byte payload consists of typed `ModuleCategory` and `ModuleFlags` values, described by ordinary FIELD objects under the Module TYPE.

A Module is only organizational. It is not a storage domain, heap, lifecycle, ownership class, Python package, or hidden namespace mechanism. Its parent is ROOT or another Module. All non-root names are one non-empty path component.

Fully qualified names are derived by walking semantic parent links to root and joining components with dots. They are never persisted in attributes or a parallel index. The walk refuses missing parents and cycles. FIELD objects remain children of their TYPE, so `Type.form` follows naturally from the same rule.

The canonical built-in hierarchy starts:

```text
root
└── pymergetic
    └── rxf
        ├── primitives
        │   ├── void, bool
        │   ├── uint8_t, uint16_t, uint32_t, uint64_t
        │   ├── int8_t, int16_t, int32_t, int64_t
        │   └── float, double
        ├── model
        ├── execution
        │   └── basic
        └── application
            └── counter
```

Thus the canonical unsigned 64-bit type is `pymergetic.rxf.primitives.uint64_t`; no generic `uint` type is introduced.


## Canonical portable primitives

The persisted primitive family is `void`, `bool`, the four exact-width unsigned and signed integers, and IEEE-754 `float` (binary32, size/alignment 4) and `double` (binary64, size/alignment 8). All use canonical little-endian wire bytes. `SIGNED` describes integer signedness, so it is set only on `int*_t`, not floating-point TYPEs.

RXF does not persist a native raw pointer, `size_t`, or `uintptr_t` primitive. Those vary with target ABI and would make an image architecture-dependent. Persisted identities and heap sub-object offsets use `uint64_t`; pointer width and native addresses, where needed for execution, belong in target-specific metadata and relocation/binding state.


## Templates and traits

Generic and trait declarations are ordinary typed DATA objects. TYPE IDs 49..57 describe their fixed payloads, Modules 70..73 organize them, and FIELD IDs 80..110 describe every field. IDs and object references are uint64; enums, flags, and counts are uint32. See [templates, traits, and static dispatch](14-TEMPLATES-TRAITS-AND-DISPATCH.md). Composition through fields and traits is supported; class inheritance is not.
