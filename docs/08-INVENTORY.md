# Current implementation inventory

This document records what the RXF repository implements. It is not a dependency inventory for another runtime.

## Format and object model

- RXF v5 header and section metadata with uint64 sizes, limits, IDs, and offsets.
- One global `HeapImage`, deterministic 48-byte cell headers, aligned payloads, and copying compaction.
- Typed modules, types, fields, functions, signatures, calls, values, references, traits, conformances, capabilities, targets, ABI metadata, Code, and provenance.
- Canonical JSON conversion, binary packing/unpacking, integrity checks, annotated dumps, and memory-layout analysis.

## Semantics and native execution

- Semantic Function graphs with explicit result/refusal behavior.
- Static template, trait, conformance, and specialization objects.
- Native Function/Code binding for x86-64 SysV and AArch64 AAPCS64.
- Target-independent graph checks and optimization passes.
- Runtime object/code tables and relocation application without an RXF VM or interpreter.

## Executable packaging

- Static Linux ELF64 artifacts for x86-64 and AArch64.
- UEFI PE/COFF applications.
- Legacy BIOS disk images carrying the x86-64 RXF payload.
- Recovery of the complete authoritative RXF image from each supported envelope.

## Tooling

- Programmatic face and command-line operations for minting, inspection, replay, certification, compilation, planning, and executable builds.
- Multi-image FastAPI inspector with sandboxed directory mounts and detailed object, memory, target, ABI, and native Code views.
- Deterministic starter image and generated native corpus.
- Tests covering round trips, invariants, native compilation, packaging, extraction, and available QEMU execution lanes.

## Explicit non-requirements

RXF has no required Metal runtime, MicroPython runtime, browser runtime, Wasm engine, source-language compiler, VM, or ambient syscall provider. Integrations can satisfy explicit capability contracts without becoming part of the RXF format.
