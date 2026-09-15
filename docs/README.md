# RXF architecture documentation

RXF format v3 is a self-describing executable object graph with one durable uint64 ID space and one global heap image.

Core invariants:

- root ID is `0`; invalid ID is `UINT64_MAX`;
- NodeKind slot `9` is reserved; `MODULE=19` is stable; `MODULE=19` is stable;
- one global 48-byte-header cell stream;
- no DOMAIN semantic objects, arenas, regions, or per-domain heaps;
- ownership is an ordinary link plus typed classification, separate from lifecycle;
- Module is an ordinary typed organizational object; dotted FQNs are derived from parent links and never persisted;
- Module is an ordinary typed organizational object; dotted FQNs are derived from parent links and never persisted;
- lifecycle state is provenance × disposition × cleanliness × mobility;
- pages are derived allocator geometry/policy;
- unknown heap limits never extend committed write authority;
- successors are built by non-mutating copying compaction.

Read in order:

1. [Object model](01-OBJECT-MODEL.md)
2. [Container and wire layout](02-CONTAINER.md)
3. [Allocator state](03-ALLOCATOR-STATE.md)
4. [References](04-REFERENCES.md)
5. [Semantic authority](05-SEMANTIC-AUTHORITY.md)
6. [Paths and views](06-PATHS-AND-VIEWS.md)
7. [Successor and activation](07-SUCCESSOR-AND-ACTIVATION.md)
8. [Inventory](08-INVENTORY.md)
9. [Plan](09-PLAN.md)
10. [Faces and channels](10-FACE-AND-CHANNELS.md)
11. [Bootstrap and quorum](11-BOOTSTRAP-AND-QUORUM.md)
12. [Authoring](12-AUTHORING.md)
13. [Execution layers](13-EXECUTION-LAYERS.md)
14. [Templates, traits, and static dispatch](14-TEMPLATES-TRAITS-AND-DISPATCH.md)
15. [Standard library stages 4–8](15-STDLIB-STAGES-4-8.md)

Documents in this directory use DOMAIN only when explicitly rejecting the obsolete RXF model, and use arena only for clearly identified external Metal implementation details. RXF v5 itself has neither semantic construct.

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
directly to `starter.rxf` and selects `pymergetic.rxf.application.main` (#1210).
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
