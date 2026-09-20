# Semantic authority

The RXF object graph is the authoritative program. Types, modules, functions, signatures, calls, values, state, capabilities, target bindings, native Code, ownership, and provenance are ordinary typed objects in one durable identity space.

Source text and JSON are authoring or presentation forms. Native executable envelopes are loading forms. Neither supersedes the object graph.

## What authority means

A conforming reader can recover program structure from RXF without a separate source tree. A conforming writer changes the program by producing a validated successor graph. Generated source views may aid humans and tools, but formatting, comments, and source-file organization are not required to reconstruct semantic identity.

Every authoritative relationship is explicit:

- parent links derive module and object names;
- TYPE and FIELD objects define persisted layouts;
- Function, Signature, Parameter, Result, Call, Argument, and Value objects define behavior;
- typed references define graph edges;
- Code and ABI objects define target implementations;
- capability objects define external requirements; and
- provenance objects record derivation.

There is no parallel authoritative path table, source manifest, symbol registry, or target-specific object model.

## Validation boundary

Shape validation proves that records can be decoded. Semantic validation additionally proves graph invariants: IDs are unique, required references resolve, types agree, layouts fit, ownership is ordinary and acyclic where required, call graphs are coherent, ABI locations are valid, target Code is compatible, and retained objects form a closed image.

Invalid changes produce typed refusals before activation. Validation is deterministic and has no authority to consult ambient files, process symbols, network services, or an implementation-specific runtime.

## Views and round trips

Canonical JSON is a reversible inspection and authoring representation of the supported object model. Binary RXF is the authoritative compact representation. A JSON → RXF → JSON round trip preserves semantic content; a native envelope → RXF extraction recovers the embedded authoritative image.

A generated source-like view is intentionally non-authoritative. It may choose syntax and layout for a human or model while mapping edits back to object IDs and expected generations.

## Implementations

The Python package in this repository is the current reference toolchain and independent checker. Native runtime components implement selected operations needed by packaged images. Other runtimes may implement the same format and contracts without adopting Python, Metal, MicroPython, Wasm, or any particular operating system.
