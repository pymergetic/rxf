# Paths and views

RXF identity is object-based. Paths are derived human-facing views over parent links; they are not a second identity system.

## Derived names

Object `0` is the root. Every named non-root object stores one path component and an optional semantic parent. A dotted fully qualified name is derived by walking parents to root. A slash path is another rendering of the same chain.

Readers refuse malformed components, missing parents, and cycles. They do not persist a separate path-to-ID authority.

## Object access

Core access is by uint64 object ID. Tools may expose:

- tree lookup and ordered child enumeration;
- typed object details and references;
- stored payload bytes;
- generated semantic, source-like, or diagnostic views;
- native Code bytes and ABI metadata; and
- heap and executable-layout projections.

A view names its source object and generation. Writable tooling stages a semantic change against expected IDs and generations, validates the complete successor, and only then emits or activates it. A stale generation is refused rather than silently redirected.

## Adapters

CLI commands, Python APIs, HTTP inspectors, filesystem mounts, and model-facing protocols are adapters over the same object model. No adapter gains semantic authority. Transport syntax may decode a request, but identity, validation, mutation policy, and refusal behavior remain RXF concerns.

RXF does not require a filesystem, web server, browser, MicroPython runtime, or particular capability provider. Implementations may offer any of these while preserving the same IDs and semantics.

## Native envelopes

ELF, PE/COFF, and BIOS artifacts map the complete RXF image as a validated segment or extent. Envelope symbols and virtual addresses are projections used for loading. Inspection and recovery operate on the embedded RXF image and therefore expose the same object paths as the raw artifact.
