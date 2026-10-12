# Semantic authority

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

The RXF object graph is the authoritative program. Types, modules, functions, signatures, calls, values, state, capabilities, target bindings, native Code, ownership, and provenance are ordinary typed objects in one path space: every object is identified by its path, and handles are image-local.

Source text and JSON are authoring or presentation forms. Native executable envelopes are loading forms. Neither supersedes the object graph.

## What authority means

A conforming reader can recover program structure from RXF without a separate source tree. A conforming writer changes the program by producing a validated successor graph. Generated source views may aid humans and tools, but formatting, comments, and source-file organization are not required to reconstruct semantic identity.

Every authoritative relationship is explicit:

- parent links and names derive the path, which is the identity of an object;
- TYPE and FIELD objects define persisted layouts;
- Function, Signature, Parameter, Result, Call, Argument, and Value objects define behavior;
- typed references define graph edges;
- Code and ABI objects define target implementations;
- capability objects define external requirements; and
- provenance objects record derivation.

There is no parallel authoritative path table, source manifest, symbol registry, or target-specific object model. The path is the name chain itself, not a stored copy.

## Validation boundary

Shape validation proves that records can be decoded. Semantic validation additionally proves graph invariants: names are unique under each parent, required references resolve, types agree, layouts fit, ownership is ordinary and acyclic where required, call graphs are coherent, ABI locations are valid, target Code is compatible, and retained objects form a closed image.

Invalid changes produce typed refusals before activation. Validation is deterministic and has no authority to consult ambient files, process symbols, network services, or an implementation-specific runtime.

## Views and round trips

The delta (XML) is the reversible exchange and authoring representation of the object model. It carries paths and field values and no handles. Binary RXF is the authoritative compact representation. A delta → RXF → delta round trip returns the same canonical text; a native envelope → RXF extraction recovers the embedded authoritative image. The v5 Python stack in the tree reads and writes canonical JSON until the core replaces it.

A generated source-like view is intentionally non-authoritative. It may choose syntax and layout for a human or model while mapping edits back to object paths and the digest of the base they were made against.

## Implementations

The C++ core of this repository is the reference implementation, and Python orchestrates it. An independent checker is a separate implementation of the same format. Native runtime components implement selected operations needed by packaged images. Other runtimes may implement the same format and contracts without adopting Python, Metal, MicroPython, Wasm, or any particular operating system.
