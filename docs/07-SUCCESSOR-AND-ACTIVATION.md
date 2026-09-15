# Successor construction and activation

A successor is built by non-mutating copying compaction. The current runtime graph remains live while retained objects are copied in deterministic ID order to a new global heap image. Transient and tombstone objects are excluded. Mandatory links to excluded objects make construction fail before activation.

Durable IDs, TypeIds, semantic parents, owners, generations, payload bytes, and mobility survive the copy. Emitted provenance becomes `IMAGE`, disposition `RETAIN`, and cleanliness `CLEAN`. Pinned source objects may be copied; the old address remains pinned and untouched.

Activation validates the complete v3 container, committed and known bounds, all 48-byte headers, node/cell state agreement, type and code indexes, sections, and mandatory references. Only then may the successor replace the current generation. Failure leaves the source generation unchanged.
