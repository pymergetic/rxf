# Code, foundation functions, and library functions

RXF separates portable function identity from replaceable executable bytes. The
important boundary is not “function versus instruction”; it is **Function versus
Code**:

- a **Function object** defines what may be called: its identity, signature, effects,
  semantic version, and implementation form;
- a **Code object** is one terminal implementation of exactly one Function for one
  target. It carries target metadata and raw executable bytes;
- a **Call object always targets a Function object**. It never selects or directly
  calls a Code object. The binder selects Code for the active target.

This keeps the architecture-dependent surface small. Most of the artifact is a graph
of portable Functions. Only the lowest foundation Functions require target Code.

## The three execution layers

```text
library functions                   pleasant application API
  strings, arrays, maps, JSON,
  streams, tasks, protocols
          |
          | Call -> Function
          v
basic functions                     portable mechanisms
  buffer growth, object traversal,
  comparison, dispatch, allocation,
  if/while/switch composition
          |
          | Call -> Function
          v
foundation functions                smallest platform boundary
  object resolve, memory pages,
  atomics, clock, raw I/O, boot exit
          |
          | binder selects implementation
          v
Code object                          terminal target implementation
  target metadata + imports +
  relocations + raw executable bytes
```

The layers are architectural constraints, not separate identity spaces. Every item is
an ordinary object with a node ID.

### Code layer

A Code object is the end of RXF semantic decomposition. RXF understands its metadata,
imports, sections, and ownership, but does not interpret its native instruction bytes
as further objects.

A Code object:

- belongs to exactly one Function;
- implements exactly one target tuple;
- contains raw bytes and the metadata needed to bind them;
- imports Functions or runtime capabilities by semantic identity, never by persisted
  process address;
- may be replaced only by another implementation of the same Function contract.

A Code object is not independently callable. An orphan executable Code object is
invalid. Arbitrary bytes may exist as a normal data object, but are not executable.

### Basic-function layer

Basic Functions are portable compositions built above the foundation. They call other
Functions and normally contain no architecture-specific bytes.

Examples include:

- checked object field access;
- buffer growth and slicing;
- array iteration;
- generic equality and ordering;
- allocator policy above raw page acquisition;
- `if`, `while`, and `switch` as ordinary functions taking lazy Function arguments.

`then`, `else`, `case`, and `do` are not types. They are argument roles. Branches and
loop bodies are normal Function objects passed lazily.

### Library-function layer

Library Functions form the useful API exposed to applications. They are implemented
from basic Functions, can call other library Functions, and remain portable unless
they deliberately reach an imported capability.

Examples include strings, lists, maps, JSON, files, HTTP, scheduling, logging, and the
path/view API. Moving the RXF to another architecture does not duplicate this layer.

## Function implementation forms

There is one Function object model with four implementation forms:

```text
ABSTRACT
    A non-callable declaration used by a TraitRequirement.

COMPOSED
    The Function body consists of Calls to Functions.

CODE_BACKED
    The Function is a terminal foundation leaf and owns one or more Code objects.

IMPORTED
    The bootloader or runtime supplies the implementation as a capability.
```

An optional intrinsic marker may permit optimized lowering, but it is not a second
semantic authority. A composed fallback or a defining semantic contract must remain.

The central call rule is always:

```text
Call -> Function -> selected Code
```

Never:

```text
Call -> x86_64 Code
```

## Proposed object hierarchy

This is an object hierarchy, not necessarily a source-directory hierarchy:

```text
/root
  /types
    Type
    Field
    Function
    Signature
    Parameter
    Call
    Argument
    Value
    Array
    Code
    Target
    Import
    Relocation
    Section

  /targets
    x86_64-linux-sysv
    aarch64-uefi

  /functions
    /foundation
      object.resolve
        code.x86_64
        code.aarch64
        code.wasm32
      memory.page_acquire
        code.x86_64-linux
        code.aarch64-uefi
      io.raw_write
        code.x86_64-linux
        code.aarch64-uefi
      clock.monotonic
        code.x86_64-linux
        code.aarch64-uefi

    /basic
      memory.copy
      object.read_field
      object.write_field
      array.append
      control.if
      control.while
      control.switch

    /lib
      string.concat
      list.map
      json.parse
      stream.write
      log.info

  /application
    main
    handle_request
```

The corresponding external-toolchain source layout should keep model, validation,
binding, and authored corpus separate:

```text
src/pymergetic/rxf/
  model/
    function.py              Function, Signature, Parameter, Call, Argument
    code.py                  Code, Target, Import, Relocation, code sections
  output/
    function.py              fixed binary payload models
    code.py                  target/code binary payload models
    code_table.py            derived Function+Target -> Code location index
  execution/
    checker.py               call, ownership, signature, and layer checks
    binder.py                active-target Code selection
    target.py                target compatibility and feature ranking
  lower/
    calls.py                 composed Functions -> executable call graph
    control.py               if/while/switch optimization
  corpus/
    foundation.py            declarations of required foundation Functions
    basic.py                 portable basic Function graph
    library.py               portable standard-library Function graph
```

The corpus files mint ordinary objects. They are not another runtime implementation
and do not become hidden authority outside the RXF.

## Object examples

The examples below show semantic JSON. Numeric IDs are illustrative.

### A target object

```json
{
  "id": 1000,
  "name": "x86_64-linux-sysv",
  "type": 80,
  "parent": 100,
  "attrs": {
    "architecture": "x86_64",
    "environment": "linux",
    "abi": "sysv",
    "endianness": "little",
    "word_bits": 64,
    "features": ["baseline"]
  }
}
```

`Target` should eventually be a typed payload rather than an unrestricted attribute
object; the example focuses on the relationships.

### A code-backed foundation Function

```json
{
  "id": 2000,
  "name": "object.resolve",
  "type": 70,
  "parent": 200,
  "attrs": {
    "implementation": "CODE_BACKED",
    "semantic_version": 1,
    "semantic_digest": "sha256:..."
  },
  "refs": [
    {"role": "signature", "target": 2100},
    {"role": "implementation", "target": 2200},
    {"role": "implementation", "target": 2201}
  ]
}
```

Its x86-64 implementation is an ordinary child object:

```json
{
  "id": 2200,
  "name": "object.resolve.x86_64",
  "type": 81,
  "parent": 2000,
  "data": "554889e5...",
  "attrs": {
    "format": "NATIVE",
    "entry_offset": 0,
    "semantic_digest": "sha256:..."
  },
  "refs": [
    {"role": "owner_function", "target": 2000},
    {"role": "target", "target": 1000}
  ]
}
```

An ARM implementation is a sibling, not another Function:

```json
{
  "id": 2201,
  "name": "object.resolve.aarch64",
  "type": 81,
  "parent": 2000,
  "data": "fd7bbfa9...",
  "refs": [
    {"role": "owner_function", "target": 2000},
    {"role": "target", "target": 1001}
  ]
}
```

### A composed basic Function

`array.append` calls Functions; it does not know which Code implementation the binder
will select for a foundation leaf:

```json
{
  "id": 3000,
  "name": "array.append",
  "type": 70,
  "parent": 300,
  "attrs": {"implementation": "COMPOSED"},
  "refs": [
    {"role": "signature", "target": 3100},
    {"role": "body", "target": 3200}
  ]
}
```

```json
{
  "id": 3201,
  "name": "grow_if_required",
  "type": 73,
  "parent": 3200,
  "refs": [
    {"role": "callee", "target": 3300},
    {"role": "argument.capacity", "target": 3210},
    {"role": "argument.grow", "target": 3211},
    {"role": "argument.keep", "target": 3212}
  ]
}
```

Here `3300` is the normal Function object `control.if`. `3211` and `3212` are normal
Functions used as lazy branches.

### `if`, `while`, and `switch`

Control is expressed through ordinary calls:

```text
control.if(condition, then_function, else_function)
control.while(condition_function, body_function)
control.switch(selector, cases, default_function)
```

A switch case array has ordinary values pairing a match value with a Function:

```json
{
  "id": 3400,
  "name": "request_dispatch",
  "type": 73,
  "refs": [
    {"role": "callee", "target": 3410},
    {"role": "argument.selector", "target": 3420},
    {"role": "argument.cases", "target": 3430},
    {"role": "argument.default", "target": 3440}
  ]
}
```

Conceptually, `3430` contains:

```text
[
  (GET, handle_get),
  (POST, handle_post),
  (DELETE, handle_delete)
]
```

The optimizer may lower this to comparisons, a jump table, binary search, or hashed
dispatch. The authored call remains the same.

### A library Function

```json
{
  "id": 4000,
  "name": "json.write",
  "type": 70,
  "parent": 400,
  "attrs": {"implementation": "COMPOSED"},
  "refs": [
    {"role": "signature", "target": 4100},
    {"role": "body", "target": 4200}
  ]
}
```

Its body may call `string.encode_utf8`, `buffer.append`, and `stream.write`. Only the
bottom dependency—perhaps `io.raw_write`—requires target-specific Code.

## Multi-architecture Code registry

A fat RXF may contain several Code objects per code-backed Function. The registry is
a derived lookup index, not a second definition:

```text
(function ID, target ID) -> Code object location
```

The Code object remains authoritative. The index only accelerates selection.

For a selected target, the binder ranks candidates:

1. exact architecture, environment, ABI, and feature match;
2. architecture baseline implementation;
3. imported runtime capability supplied to preflight;
4. no compatible implementation: typed refusal.

A deployment build can retain one implementation. A fat distribution can retain
several. Removing unrelated target Code must not change portable Function IDs or
semantics.

## Boot envelopes

A canonical RXF payload does not need to masquerade simultaneously as BIOS, PE/COFF,
ELF, or any other envelope. Packaging should be:

```text
platform-specific boot envelope
  + unchanged canonical RXF payload
```

The envelope locates the RXF, provides the initial capability set, chooses a Target,
binds the entry Function, and transfers control. BIOS, UEFI, hosted Unix, and firmware envelopes can carry the same fat RXF payload.

## Required invariants

The checker must enforce:

1. Every executable Code object has exactly one owner Function.
2. A Function owns zero or more Code objects; Code never owns a Function.
3. Every Call targets a Function, never Code.
4. A composed Function body contains calls to Functions and no target addresses.
5. A code-backed Function has at least one Code object or a declared imported fallback.
6. Code target metadata is compatible with its sections, relocations, and ABI.
7. Code and Function semantic digest, signature, and effect contract agree.
8. Persisted references are object IDs/handles, never native addresses.
9. Foundation Code imports only permitted foundation Functions or capabilities.
10. Library and application Functions cannot become dependencies of the foundation
    layer.
11. A selected target has a compatible implementation for every reachable terminal
    Function, or loading refuses before execution.
12. Replacing target Code leaves the owning Function's semantic identity unchanged.

## Design consequence

A multi-platform RXF should not duplicate the whole application per architecture. It
shares the object graph, state, types, composed basic Functions, and library Functions.
Only the deliberately small foundation Code set is repeated:

```text
shared:     application + library + basic functions + state + types
per target: foundation Code objects + boot envelope
```

That is the portability boundary: a small replaceable executable foundation under a
large portable function library.

## Implemented toolchain model

The external toolchain now implements this boundary under `src/pymergetic/rxf/`:

- `model/execution.py` defines typed Function, Signature, Parameter, Call, Argument,
  Value, Target, Code, Import, and Relocation object builders;
- `execution/decode.py` reads their fixed payload records;
- `execution/binder.py` selects Code through a Function and Target;
- `output/code_table.py` defines the derived Function+Target-to-Code cell index;
- `ops/lowering.py` lowers ordinary Call objects, using a callee Function's optional
  intrinsic marker for control-flow optimization;
- the inspector's Function view shows layers, implementation forms, intrinsics, and
  terminal target Code.

The current counter fixture uses normal Function and Call objects. It has no `If`,
`Then`, `Else`, `While`, or `Do` object types. `control.while` is a Function and its
condition and body are lazy Function arguments.

## Modules do not add an execution layer

The canonical `pymergetic.rxf.execution` and `pymergetic.rxf.execution.basic` Modules organize the existing Function Types and basic Functions. Application Functions and state live beneath `pymergetic.rxf.application.<application>`. A Module neither executes nor owns its children and does not alter Function/Code binding, lifecycle, or heap placement. Function-owned Signature, Call, and Code objects remain semantic children of their Function.


## Trait dispatch remains concrete

Trait requirements are ABSTRACT Function declarations and cannot be Call targets. Static resolution chooses a unique Conformance and matching ImplementationBinding, then emits the ordinary `Call -> concrete Function` edge. This does not introduce a fourth execution layer or permit calls through requirement declarations.

## Portable primitive Code and control intrinsics

Primitive numeric operations are typed `CODE_BACKED` Functions with complete Result, refusal, NumericContract, ABISignature, and extracted machine-byte contracts for both native targets.

Control Functions are `sequence`, `if`, `while`, `switch`, `return`, `break`, `continue`, and `refuse`. Their implementation form is `INTRINSIC`; `then`, `else`, and `do` remain lazy parameters, never object types. Lowering derives uint64 IDs in reserved `0xE...` space from SHA-256 of source Call ID and role, refuses collisions, and emits deterministic CFG objects for every form.


## Loader status

Binding and preflight validate a transient atomic plan only. Mapping executable pages, applying relocation patches, and transferring control are future loader work; this package provides no `run` operation.
