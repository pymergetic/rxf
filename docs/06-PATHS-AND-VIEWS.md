# Paths and views

The same normalized path keys, uint64 node IDs, selectors, and global layout records serve both an external inspection of an unstarted RXF image and an internal view of a running instance. Host and target filesystem protocols may differ; RXF identity does not.

## Paths are derived

A path is the chain of names from root object `0`. A lookup walks components through parent/child relationships. RXF stores no second path-to-ID identity table.

Aliases and mounted views are ordinary objects. Their semantic payload uses uint64 object IDs:

```c
typedef struct pm_rxf_path_v3 {
    uint64_t target;       /* object reached by this alias */
    uint64_t view;         /* VIEW object; UINT64_MAX means stored bytes */
    uint32_t selector;     /* whole object, field, code body, metadata */
    uint16_t read_policy;  /* STORED_BYTES or GENERATED_VIEW */
    uint16_t write_policy; /* NONE, RAW_OBJECT, or SEMANTIC_PATCH */
    uint32_t base_policy;  /* required generation binding */
} pm_rxf_path_v3_t;
```

Counts, policies, and selectors are classifications and may remain 32-bit. Object identities are always uint64.

## Namespace

```text
/                              root object
/sections/<name>               globally addressed image span
/types/<type>                  TYPE object
/types/<type>/<field>          FIELD object
/modules/<dotted>/...          cards, functions, exports, and limits
/objects/<id>                  object by durable uint64 ID
/objects/<id>/<field>          field selected through its TYPE layout
/code/<function-id>/<target>   one Code implementation
/heap                          global heap geometry and cell stream
/tools/<name>                  contained capability
/views/source/<gen>/<node>     generated source view
/views/semantic/<node>         generated semantic view
/journal/<seq>                 activation record
```

Reading an object field resolves its ID, finds the global cell offset, applies the TYPE field offset, and reads the payload. No storage domain, arena object, or per-domain region participates.

## One path, two directions

Outside the image, an inspector parses tables and the global heap without starting the program. Inside a running instance, the same IDs and parent links address live objects. The address mapping differs, but the semantic identity and path do not.

Image bytes occupy the serialized prefix measured by `image_size`. Runtime writes are authorized only inside `committed_size`, never merely because the limit is unknown. A view may display initial image bytes or current runtime bytes while retaining the same object ID and global offset vocabulary.

## Existing external precedents

Metal currently has several unrelated path surfaces. They are implementation precedents, not RXF semantic storage:

- `pymergetic.metal.fs` uses an external Metal memory arena and a FAT fallback.
- `pymergetic.metal.workspace` writes source paths into that filesystem.
- `pymergetic.metal.inspect` maps source/build routes to embedded records.
- the wasmmod pack source API supports both host-file and loaded-pack access.

Those current systems may use arenas internally. RXF v5 does not model those arenas as objects and does not inherit their identity spaces.

## Required faces

1. Tree lookup and child enumeration over uint64 IDs.
2. A view registry whose VIEW objects are in the ordinary ID space.
3. External and internal adapters that preserve the same normalized path behavior.
4. Writable views that bind an expected generation before mutation.
5. Inspector and MicroPython integrations that consume the same graph rather than constructing parallel indexes.

## Dotted FQNs

The inspector's title, details, API, and search use the dotted FQN derived from semantic parent links. Tree rows remain compact and show the leaf component. Modules make the parent tree itself the namespace hierarchy; there is no stored `fqn` attribute. Missing parents and cycles are errors, not partial names.
