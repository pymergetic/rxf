# RXF — Reversible eXecutable Format

RXF treats a program as a **complete, self-describing object world** rather than a
one-way compilation product. Types, modules, functions, calls, values, ownership,
semantic graphs, target descriptions, ABI contracts, native Code, and live data all
remain ordinary addressable objects in one binary image.

The same image can be:

- authored as JSON and deterministically minted into RXF;
- inspected as an object tree, relation graph, and physical memory layout;
- transformed in either direction without throwing semantic information away;
- bound to native Code for several architectures while retaining other targets;
- wrapped by Linux ELF, UEFI PE/COFF, or a BIOS disk image and run directly; and
- recovered intact from that executable wrapper without source files, symbols, or a sidecar.

The platform format is only a small loading envelope. **RXF remains the program.**
Runtime object pointers and function entry points resolve into the embedded RXF cells;
there is no stripped native program plus a detached metadata copy and no bytecode
interpreter between the object world and its lowest-level machine Code.

![RXF Inspector showing the application object tree, global HeapImage, and selected Function](docs/images/inspector-overview.png)

*One view of the same program: semantic objects on the left, physical cell layout in
the center, and the selected object's type, lifecycle, references, and body on the right.*

## Why

Conventional executable formats preserve enough information to load code, but usually
lose most of the program's authored structure. RXF is designed for systems where a
running or bootable artifact should still be explainable, transformable, movable, and
certifiable. Durable uint64 object IDs keep relationships stable while payloads and Code
move; signatures and target contracts make native binding explicit; copying compaction
produces deterministic successor images.

Native machine code is not opaque external cargo. A Code object names its Function,
RuntimeTarget, ABI, effects, provenance, imports, and relocations, while retaining its
exact bytes inside the object heap:

![RXF Inspector showing an x86-64 Code object, raw bytes, target contract, imports, and relocations](docs/images/inspector-native-code.png)

*An x86-64 terminal Code object remains linked to its semantic Function and target
contract. Other architecture implementations coexist in the same RXF image.*

## Core invariants

- root ID is `0`; invalid ID is `UINT64_MAX`;
- one durable uint64 identity space and one global 48-byte-header cell stream;
- everything with durable identity is an object;
- no DOMAIN semantic objects, arenas, regions, or per-domain heaps;
- ownership is an ordinary typed link, separate from lifecycle state;
- dotted module names are derived from parent links, never persisted as authority;
- lifecycle is provenance × disposition × cleanliness × mobility;
- pages are allocator geometry and policy, not semantic objects;
- unknown heap limits never extend committed write authority; and
- successors are produced by deterministic, non-mutating copying compaction.

## Architecture documentation

Read in order:

1. [Object model](docs/01-OBJECT-MODEL.md)
2. [Container and wire layout](docs/02-CONTAINER.md)
3. [Allocator state](docs/03-ALLOCATOR-STATE.md)
4. [References](docs/04-REFERENCES.md)
5. [Semantic authority](docs/05-SEMANTIC-AUTHORITY.md)
6. [Paths and views](docs/06-PATHS-AND-VIEWS.md)
7. [Successor and activation](docs/07-SUCCESSOR-AND-ACTIVATION.md)
8. [Inventory](docs/08-INVENTORY.md)
9. [Plan](docs/09-PLAN.md)
10. [Faces and channels](docs/10-FACE-AND-CHANNELS.md)
11. [Bootstrap and quorum](docs/11-BOOTSTRAP-AND-QUORUM.md)
12. [Authoring](docs/12-AUTHORING.md)
13. [Execution layers](docs/13-EXECUTION-LAYERS.md)
14. [Templates, traits, and static dispatch](docs/14-TEMPLATES-TRAITS-AND-DISPATCH.md)
15. [Standard library stages 4–8](docs/15-STDLIB-STAGES-4-8.md)
16. [Capabilities and toolchain stages 9–15](docs/16-CAPABILITIES-AND-TOOLCHAIN.md)

Documents use DOMAIN only when explicitly rejecting the obsolete model and use arena
only for identified external Metal implementation details. RXF v5 has neither construct.

## RXF v5 binary model

RXF v5 has one global `HeapImage`, one uint64 object identity space, and one deterministic cell stream with 48-byte headers. Root is `0`, invalid is `UINT64_MAX`, and NodeKind slot 9 remains reserved. Ownership is an ordinary link and typed classification; lifecycle is the independent provenance/disposition/cleanliness/mobility state. Pages are derived allocator geometry, not semantic objects.

Image construction is non-mutating copying compaction: retained objects preserve IDs, type, parent, owner, generation, payload, and mobility; transient/tombstone objects are omitted; mandatory references to omitted objects are rejected; emitted state is `IMAGE/RETAIN/CLEAN`. See [the architecture guide](docs/README.md).

## Starter inspector quickstart

The example configuration exposes one canonical application image, never the internal
regression fixtures:

```sh
rxf serve --config rxf-server.example.toml
```

Open <http://127.0.0.1:8420/>. With the single example mounted, the catalog redirects
directly to `starter.rxf` and selects `pymergetic.rxf.application.main` (#40010).
Use the compact **Entry → Library → Foundation → Code** controls to follow `main`
through `checkout.total` to the checked Basic arithmetic leaves, then inspect either target's extracted native bytes.
Select an active target only when you want binding preflight; Code browsing is target-independent.

The server binds only to `127.0.0.1` by default. To expose it explicitly:

```sh
rxf serve --config rxf-server.example.toml --host 0.0.0.0 --port 8420
```

External exposure is unauthenticated and read-only; restrict it with a host firewall or
a trusted reverse proxy. `counter.rxf` and `native-binding.rxf` remain internal regression
fixtures under `tests/` and are not mounted by the example configuration.

## Build executable images

`rxf build <image.rxf> --entry <function-id> --target <id-or-name> --output <path>` builds static executable ELF64 Linux, PE32+ OVMF/AAVMF UEFI, or raw x86-64 BIOS images. Canonical names are `x86_64_linux_sysv`, `aarch64_linux_aapcs64`, `x86_64_uefi_sysv`, `aarch64_uefi_aapcs64`, and `x86_64_bios_sysv`; the starter entry is `40010`. Linux outputs receive execute mode.

The executable is the complete RXF object image inside a platform loading envelope,
not a reduced native program with a metadata attachment. Original objects, types,
modules, signatures, semantic graphs, and other-target Code stay in the image.
Target materialization adds inspectable Code objects for reachable composed
Functions. Runtime payload pointers and native entry pointers resolve into that
same mapped RXF image; loading wrappers do not own a second copy of the program.

`rxf inspect`, `view`, `dump`, `layout`, `certify`, and `replay` accept executable
wrappers directly as well as raw RXF. Recovery follows validated load descriptors;
it requires neither the original JSON/RXF nor a manifest or debug-symbol file.
`replay` checks byte identity of the recovered RXF, not reconstruction of the
platform envelope. The inspector can discover `.elf`, `.efi`, `.img`, and
extensionless ELF artifacts in its configured mounts.

The immutable RXF image is mapped readable/executable; runtime binding tables and
allocation space are writable/non-executable. ELF zero-filled heap, journal,
cleanup, and stack reservations use memory size without serialized zero bytes.
Build summaries report RXF bytes, platform-envelope overhead, initialized bytes,
and zero-fill separately. Manifests contain geometry and digests, not a second
hex-encoded copy of the program.

Clang, LLD, and LLVM objcopy build Linux/UEFI adapters; BIOS needs Clang/LLD. Direct certification also needs QEMU, OVMF/AAVMF, dosfstools, and mtools. A success atomically publishes non-overwriting output plus deterministic `<path>.manifest.json` with target, entry, Function/Code selection, source/output SHA-256, layout, and toolchain provenance. Validation, capability, import, relocation, target, or W^X refusal writes nothing. Inspector executable planning is read-only. Treat RXF input as untrusted; generated code retains process or firmware authority.
