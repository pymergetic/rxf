# RXF v5 container

An RXF v5 file contains one extensible header prefix, node/reference/section/type/code tables, attributes and strings, followed by one global `HeapImage` blob. There are no semantic DOMAIN objects, domain definitions, region IDs, storage-domain fields, or per-domain heaps.

The persisted header starts with `magic`, `version`, `header_size`, and `header_capacity`. `header_size` is the byte size of the current known `BinaryHeader` schema, including its normal trailing alignment. `header_capacity` is the entire reserved file prefix before the node table. V5 reserves 256 bytes; bytes from `header_size` through `header_capacity` must be zero. Readers validate both values before using the capacity, require capacity to fit the file and satisfy header alignment, and reject metadata that overlaps the reserved prefix. A later format can consume reserve while preserving stable table placement; this first v5 reader accepts only its exact known header schema size.

Every persisted size, offset, count, identity, and limit is represented with an explicitly sized wire integer; sizes, offsets, and limits are `uint64_t` independent of the future loader target. The wire format never uses C `size_t`. A 32-bit future loader must range-check each persisted value before converting it to local `size_t` and refuse values that cannot be represented.

The header carries uint64 table counts/offsets and global heap geometry: `heap_off`, `image_size`, `committed_size`, `heap_limit`, and `frontier`, plus explicitly sized alignment fields. The image base is file offset zero, so `image_size` is exactly the serialized RXF byte length, including metadata and the stored heap prefix. The invariant is `frontier <= committed_size`, `image_size <= committed_size`, and, when known, `committed_size <= heap_limit`. `heap_limit == UINT64_MAX` means unknown only; unknown never authorizes reads, writes, or allocation beyond `committed_size`.

The cell stream is deterministic and each header is exactly 48 bytes:

```c
typedef struct pm_rxf_cell_v5 {
    uint64_t id;
    uint64_t type_id;
    uint64_t generation;
    uint64_t parent;
    uint64_t payload_size;
    uint32_t flags;
    uint32_t reserved;
} pm_rxf_cell_v5_t;
```

`reserved` must be zero. Cell payload follows immediately; the next header is aligned to the global heap alignment. Section offsets, `TypeLocation.cell_offset`, and `CodeLocation.cell_offset` are global offsets from the start of the heap image.

A reader validates all ranges, v5 header bootstrap and zero reserve, canonical non-overlapping table order, the committed frontier, cell/node identity, type, parent, generation, size and state agreement, unique TYPE locations, and mandatory references before exposing the image.
