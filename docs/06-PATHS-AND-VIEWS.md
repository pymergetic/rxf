# Paths and views

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

The identity of an object is its path: its name and its parent chain. A path is not stored as a string and is not a second identity system next to a numeric one. Handles are image-local slots and are never an identity.

## Derived names

The root is the object whose handle the arena header records. Every named non-root object stores one path component and a semantic parent. Names are unique under one parent. A dotted fully qualified name is derived by walking parents to root. A slash path is another rendering of the same chain.

Readers refuse malformed components, missing parents, and cycles. They do not persist a separate path-to-handle authority; derived lookups are built in memory at load.

## Object access

Core access is by path, which resolves to a local handle. Tools may expose:

- tree lookup and ordered child enumeration;
- typed object details and references;
- stored payload bytes;
- generated semantic, source-like, or diagnostic views;
- native Code bytes and ABI metadata; and
- heap and executable-layout projections.

A view names its source object by path and the digest of its base. Writable tooling stages a semantic change as a delta against that base, validates the complete successor, and only then emits or activates it. A stale generation is refused rather than silently redirected.

## Adapters

CLI commands, Python APIs, HTTP inspectors, filesystem mounts, and model-facing protocols are adapters over the same object model. No adapter gains semantic authority. Transport syntax may decode a request, but identity, validation, mutation policy, and refusal behavior remain RXF concerns.

RXF does not require a filesystem, web server, browser, MicroPython runtime, or particular capability provider. Implementations may offer any of these while preserving the same paths and semantics.

## Native envelopes

ELF, PE/COFF, and BIOS artifacts map the complete RXF image as a validated segment or extent. Envelope symbols and virtual addresses are projections used for loading. Inspection and recovery operate on the embedded RXF image and therefore expose the same object paths as the raw artifact.
