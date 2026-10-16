# Authoring RXF

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

RXF supports human-readable and programmatic authoring without making either representation authoritative. Both routes produce the same typed models, semantic checks, and canonical binary image.

## Deltas

A delta (XML) represents modules, types, functions, calls, values, references, targets, Code metadata, capabilities, and other objects. The loader rejects duplicate paths and unknown fields. Elements are named after types and attributes after fields, objects are addressed by path, and numbers are text, so no 64-bit value loses precision. Canonical rendering uses UTF-8 and a fixed order. The v5 Python stack in the tree still authors in canonical JSON until the core replaces it.

Authoring conveniences such as comments, includes, or templates must expand before semantic validation and do not enter authoritative bytes unless represented as ordinary RXF objects.

## Python models

The `pymergetic.rxftk` API builds the same object model with typed classes. Python may compute repetitive structures, layouts, or generated content, but the resulting objects pass through the same checks as delta input. Python source is a toolchain input, not part of the RXF runtime contract.

## Types and layout

TYPE and FIELD objects record exact persisted size, alignment, field offsets, and primitive interpretation. Handles, sizes, and heap offsets use `uint64_t`. RXF does not persist native pointers, `size_t`, or architecture-dependent integer aliases.

The canonical primitive family is `void`, `bool`, exact-width signed and unsigned integers, IEEE-754 `float`, and IEEE-754 `double`. Target-specific native ABI details belong to ABI and Code metadata.

## Functions and generics

Functions own signatures and semantic graphs. Calls refer to Functions by path and to Values by their name inside the graph. Execution order follows explicit control/data-flow edges rather than source or serialization position.

Templates, traits, associated types, conformances, implementation bindings, and specializations are ordinary typed objects. Static resolution selects concrete Functions before native lowering; no runtime interpreter is introduced.

## Mutation

An authoring change names the digest of its base and the affected paths. The toolchain validates the complete candidate, derives affected compilation/link closures, and emits a deterministic successor. Failed authoring never partially modifies the source image.

## Generated views

Source-like, schema, dump, and layout views are projections for people and tools. They should carry object paths and provenance so edits can map back to authority, but their formatting and file organization are not semantic identity.
