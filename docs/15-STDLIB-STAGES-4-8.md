# Standard library semantic corpus (stages 4–8)

The starter image exposes `pymergetic.rxf.foundation`, `pymergetic.rxf.stdlib`, and
`pymergetic.rxf.algorithms`. Durable references use the canonical 24-byte handle
`{object_id:uint64, generation:uint64, offset:uint64}`. `Slice`, `MutSlice`, and
`StringView` append checked uint64 `start` and `length`; resolved pointers are transient
and valid only during a pin or borrow no-move period.

`ResultSlot` is the Python name for the existing Signature output descriptor. Its RXF v5
wire TYPE ID and payload remain unchanged, and `ResultObject` remains a compatibility
alias. `TypedResult<T,E>` is a separate value-level tagged sum and does not replace
Signature `ResultSlot`/`RefusalSet` authority.

## Executed foundations

Nine foundation Functions have exact x86-64 Linux and AArch64 UEFI Code objects,
ABISignatures, compiler provenance, and checked-in deterministic manifests: bounded
copy, overlap-safe move, fill, zero, compare, aligned endian-aware uint32 load/store,
strict UTF-8 validation, and byte hashing. Tests mmap the exact x86 bytes. AArch64 exact
bytes are attached and extractor-validated; the raw-byte QEMU execution test skips when
its required harness/runtime is unavailable.

The Python global-heap reference model proves generation validation, aligned atomic
allocate/publish, release, compaction directory updates, pinning, one-mutable-or-many-
immutable borrows, prepare/copy/publish resize, and state-unchanged OOM. It is a semantic
reference, not an RXF interpreter.

## Hash collection storage authority

`HashMap` and `HashSet` persist the same 56-byte header: `bucket_storage:Ref` at offset 0, then uint64 `capacity`, `count`, `tombstone_count`, and `generation` at offsets 24, 32, 40, and 48. `HashMapBucket` is 48 bytes (`state:u32` at 0 with explicit padding through 7, `stored_hash:u64` at 8, `key:u64` at 16, `value:Ref` at 24). `HashSetBucket` is 24 bytes with the same state/hash/key prefix. Bucket state is a fixed-width u32 enum: EMPTY=0, OCCUPIED=1, TOMBSTONE=2; key/hash/value are valid only for OCCUPIED buckets.

`HashStorageAuthority` version 1 uses minimum capacity 8, power-of-two capacities, linear probing `(hash + probe_index) & (capacity - 1)`, termination at EMPTY or after `capacity` probes, maximum occupied load 3/4, tombstone rebuild threshold 1/4, and generation increment on mutation. Collection and bucket objects use runtime object-slot visibility: LIVE objects are generally visible; PENDING objects are visible only with the matching owner transaction. The hash-algorithm reference is explicitly unresolved in this model-only slice.

## Authored semantic graphs

All 64 stdlib APIs are terminal Code-backed leaves or composed Functions with ordinary
body Calls. The 52 non-alias composed APIs carry typed `SemanticGraph`, `SemanticBlock`,
and `SemanticOperation` objects. Operations have enum lowering semantics, ordered typed
inputs/results, owner blocks, effects, refusal sets, and explicit target/callee IDs. CFGs
encode reciprocal predecessor/successor edges, branch/switch/loop terminators, tagged
cases plus defaults, transaction success/refusal paths, rollback, cleanup, and publish.
The six former generic marker Functions are absent; no composed call graph reaches one.

Every graph now persists SSA `SemanticValue` objects for Function parameters, literals,
operation results, and block parameters. Operations carry ordered input/result value IDs
beside their types. Blocks carry condition/return/refusal values and per-successor phi
arguments. The independent verifier checks Signature/ResultSlot mapping, operation result
backrefs, ownership, same-block dominance, type agreement, phi arity/types, required
callback/object/field identities, literal payloads, and disconnected definitions. Canonical
semantic digests cover graph/block/operation/value payloads, references, literals, and edge
arguments. The starter contains 533 semantic values, 633 value uses, 134 block parameters,
and 188 predecessor-to-block phi bindings across 53 graphs.
The twelve generic model constructors are explicitly abstract Template requirements;
there are no DECLARED stdlib APIs. Reference models concretely prove
iterator invalidation, canonical map ordering/serialization, stable sort and comparator-
refusal output preservation, UTF-8 scalar boundaries, and malformed UTF-8 rejection.
Every refusal-aware contract promises output unchanged on refusal.

Core type checks reject duplicate field names, overlaps, misalignment, uint64 extent
or array multiplication overflow, inline variable-sized fields, malformed tagged unions,
and noncanonical Ref geometry. Core specializations derive stable concrete IDs and
layouts from template/type/const arguments; repeated Option<u32> is stable and
FixedArray<u8,16> differs from FixedArray<u8,32>.


## Semantic receipt application

`pymergetic.rxf.receipt.build_receipt` is a deterministic composed workflow over
`heap_allocate`, `vector_push`, `format_append`, `string_find`, and `cleanup`. Its
reference result is the UTF-8 byte string `Receipt total: 570\n`. The graph contains the literal bytes `Receipt total: ` and the integer `570`, then
explicit allocate, vector write, byte append, integer formatting, UTF-8 validation,
search, validate/publish, rollback, and cleanup operations. It remains authored semantic source and is compiled through the same verified CFG pipeline.

The corpus includes seven deterministic concrete TYPE specializations: Option<u32>,
FixedArray<u8,16>, FixedArray<u8,32>, Tuple<u32,u32>, Vector<u8>, Vector<u32>, and
Vector<String>. The compiler normalizes, optimizes, emits, and links all 53 SemanticGraphs for both x86-64 and AArch64. Transactional, tagged, loop, callback, refusal-remap, canonical-order, and cleanup control paths are covered by the generic CFG emitter.

## Native runtime ABI

The transient native runtime context is a target-independent 144-byte uint64-only ABI.
Offsets are: version 0, size 8, Function slots 16/24, Object entries 32/40, Capability
slots 48/56, heap base/committed/limit/frontier 64/72/80/88, transaction journal
96/104/112, and cleanup stack 120/128/136. Object entries are 80 bytes containing
`id,generation,type_id,payload,size,capacity,state,read_borrows,pin_count,owner_transaction`; payload and slot addresses are
transient and never persisted in RXF.

Sixteen relocation-free runtime leaves cover resolve, allocate prepare, publish, rollback,
release, borrowing, pinning, checked read/write, reserve/append, decimal u32 formatting,
byte search, and cleanup. Each has dual-target Code/ABI/provenance objects. Operational
SemanticOperations carry concrete Function IDs; compiler-only graph operations remain
callee-free.

Operational call operands are generated from each persisted callee Signature rather than
an opcode/name arity table. The leading RuntimeContext `Ref` parameter is hidden from the
artifact; all remaining ordered Parameters have matching SemanticValues, and operation
results map to ResultSlots. The compiler public classifier accepts all 53 graphs and all
159 concrete operational calls. Compiler-only operations are transaction-region metadata,
tag projection/construction, iterator/loop/phi mechanics, accumulation/sort metadata,
UTF-8 boundary metadata, refusal mapping/enrichment, canonical ordering, and return/refuse.

Required Ref operands now use canonical nonzero durable ObjectHandles with explicit OBJECT
source IDs. Allocation size/alignment literals are nonzero (`64/8` for receipt), and the
receipt prefix/newline are immutable DATA objects 90007/90008 whose handles flow directly
to append/validation operations. The executable classifier reports zero null Ref operands
across all 53 graphs.

`runtime_allocate_prepare` now has a canonical 24-byte `Ref`/ObjectHandle ResultSlot and
all three allocation operations produce width-24 results. The receipt graph uses one
transaction handle (90089) for allocate, publish, rollback, and cleanup; one pending handle
(90101) for writes/appends/format/validation; formatter arguments are u32 570, pending
handle, capacity 48; prefix and newline appends consume objects 90007 and 90008 in order.

## Typed ABI locations

Each native ABISignature now references an ordered sequence of ABIValueLocation objects.
Locations persist semantic Parameter/ResultSlot association, role, passing mode, class,
width/alignment, aggregate chunk count, register bank/indices or stack policy, pointee
TYPE, hidden/mutable/output-only flags, and target ABI. The sequence explicitly inserts
hidden RuntimeContext, semantic arguments, transient output storage, and uint32 status
return. A 24-byte ObjectHandle is represented as three integer chunks when resolved as an
argument; aggregate output is indirect storage of width 24/alignment 8. Legacy class-only
ABISignatures remain inspectable, while strict location validation rejects them for
execution.

### Typed transactional object runtime ABI

The transient object directory uses an 80-byte, 8-byte-aligned entry: `id@0`,
`generation@8`, `type_id@16`, `payload@24`, `size@32`, `capacity@40`,
`state@48`, `read_borrows@56`, `pin_count@64`, and
`owner_transaction@72`. `type_id` is durable authority; object identity is
never used to infer a type.

The 144-byte runtime context retains its existing layout and exposes a
caller-owned transient journal through `journal_count@96`,
`journal_capacity@104`, and `journal@112`. Each 72-byte journal member records
transaction identity, object ID, and the prior generation/type/payload/size/
capacity/state/owner fields. No journal address is persisted in RXF and the
runtime performs no hidden allocation. Membership lookup scans bounded journal
capacity deterministically by transaction identity and object ID.

A 48-byte transaction contains `root_object_id@0`, `old_frontier@8`,
`identity@16`, `member_count@24`, `active@32`, and `reserved@40`. Begin creates
an empty active transaction; allocation atomically reserves a journal member
before making the object PENDING. Typed access accepts LIVE entries or PENDING
entries owned by the active transaction and present in its journal. Publish
validates every member before making all members LIVE; rollback restores all
members and the frontier. Either terminal action clears membership and makes a
second terminal action refuse. Statuses 12, 13, 14, and 15 mean stale
generation, unbound/visibility/member mismatch, type mismatch, and journal full.

### U64 hash and typed lookup authority

`HashAuthority` 89990 fixes hash version 1 to FNV-1a 64 over the eight
little-endian bytes of a `uint64`, starting from explicit seed
14695981039346656037 and wrapping multiplication modulo 2^64. It binds native
Functions 61478 (`hash_u64`), 61496 (`runtime_hash_map_lookup`), and 61523
(`runtime_hash_set_lookup`). `HashStorageAuthority` 60079 references this
algorithm authority.

The lookup leaves resolve both the 56-byte collection header and its bucket
storage through typed runtime access. They validate power-of-two capacity,
counts, multiplication and extent bounds, then linearly probe by
`(hash + probe) & (capacity - 1)`. Tombstones continue, EMPTY terminates a miss,
and a full cycle also returns canonical None. Map hits return canonical
`Option<Ref>` and set hits return canonical `Option<uint64>`; refusal leaves the
output unchanged. Both LIVE and matching-owner journal-member PENDING objects
use the same typed visibility contract.
