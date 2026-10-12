# Successor construction and activation

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

A successor is built by non-mutating copying compaction. The current runtime graph remains live while retained objects are copied in canonical path order into a new arena. Transient and tombstone objects are excluded. Mandatory links to excluded objects make construction fail before activation.

Paths (names and parents), types, owner kinds and owner references, generations, payload bytes, and mobility survive the copy. Handles are assigned densely in path order and may differ from the source; references are rewritten to the new handles. Emitted provenance becomes `IMAGE`, disposition `RETAIN`, and cleanliness `CLEAN`. Pinned source objects may be copied; the old address remains pinned and untouched.

Activation validates the complete arena: the header, committed and known bounds, the index chunks, every 64-byte cell header and its flags, name uniqueness under each parent, and mandatory references. Only then may the successor replace the current generation. Failure leaves the source generation unchanged.
