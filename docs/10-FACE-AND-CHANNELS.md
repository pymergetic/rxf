# Operations and adapters

RXF defines semantic operations; implementations expose them through adapters. No transport is the authoritative program interface.

## Core operations

A minimal implementation supports operations equivalent to:

- `get(id)` — return the typed object and generation;
- `children(id)` — enumerate semantic children in canonical order;
- `references(id)` — enumerate typed outgoing and incoming edges;
- `resolve(path)` — derive an object ID by walking parent/name relationships;
- `view(id, kind)` — produce a non-authoritative projection;
- `stage(base_generation, mutations)` — construct a candidate successor;
- `check(candidate)` — return success or typed refusals;
- `emit(candidate, target)` — produce canonical RXF or a supported envelope; and
- `extract(envelope)` — recover and validate the authoritative RXF image.

Concrete APIs may combine or rename operations, but they must preserve identity, generation checks, validation, and atomic refusal behavior.

## Mutation discipline

A mutation identifies existing objects by ID and expected generation. Creation uses new durable IDs. Deletion is represented through lifecycle state and successor construction. The implementation validates reference closure, types, layouts, capabilities, and target bindings before accepting output.

Partial semantic writes are never authoritative. A failed stage, check, compile, link, or emit operation leaves the source image unchanged.

## Adapters

The CLI, Python face, HTTP inspector, model protocol, and optional filesystem view are peers. Each adapter translates transport values into core operations and translates typed results back to its client. Adapters may authenticate, rate-limit, or restrict operations, but may not invent hidden object semantics.

Read-only adapters expose no mutation operation. A deployment may provide several adapters or none at all.

## Capability boundary

Execution reaches the outside world only through explicit imports and capability requirements. Host, firmware, browser, or other providers bind those requirements for a selected target. Their internal APIs are not RXF operations and their implementation terminology does not enter the format.
