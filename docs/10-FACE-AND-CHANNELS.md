# Operations and adapters

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

RXF defines semantic operations; implementations expose them through adapters. No transport is the authoritative program interface.

## Core operations

A minimal implementation supports operations equivalent to:

- `get(path)` — return the typed object and generation;
- `children(path)` — enumerate semantic children in canonical order;
- `references(path)` — enumerate typed outgoing and incoming edges;
- `resolve(path)` — derive the local handle by walking parent/name relationships;
- `view(path, kind)` — produce a non-authoritative projection;
- `stage(base, delta)` — construct a candidate successor;
- `check(candidate)` — return success or typed refusals;
- `emit(candidate, target)` — produce canonical RXF or a supported envelope; and
- `extract(envelope)` — recover and validate the authoritative RXF image.

Concrete APIs may combine or rename operations, but they must preserve path identity, base checks, validation, and atomic refusal behavior.

## Mutation discipline

A mutation is a delta. It identifies existing objects by path and names the digest of the base it was made against; a runtime handle additionally carries the generation of its target. Creation names the new path and needs no ID. Deletion is represented through lifecycle state and successor construction. The implementation validates reference closure, types, layouts, capabilities, and target bindings before accepting output.

Partial semantic writes are never authoritative. A failed stage, check, compile, link, or emit operation leaves the source image unchanged.

## Adapters

The CLI, Python face, HTTP inspector, model protocol, and optional filesystem view are peers. Each adapter translates transport values into core operations and translates typed results back to its client. Adapters may authenticate, rate-limit, or restrict operations, but may not invent hidden object semantics.

Read-only adapters expose no mutation operation. A deployment may provide several adapters or none at all.

## Capability boundary

Execution reaches the outside world only through explicit imports and capability requirements. Host, firmware, browser, or other providers bind those requirements for a selected target. Their internal APIs are not RXF operations and their implementation terminology does not enter the format.
