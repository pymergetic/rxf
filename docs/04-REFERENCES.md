# References and binding

> Status: this describes RXFB 6 (C++ core; see [the operating-system path](17-OPERATING-SYSTEM-PATH.md)).

RXF references preserve semantic identity without persisting process addresses. A reference names a source object, a target object, a role, a kind, a binding policy, and its position in a list. Object identities and offsets are always uint64.

The **role is an object**: an instance of the field-less type `Role` at `pymergetic.rxf.roles.<area>.<name>`. A reference stores its handle (24-byte record: target, role, kind, binding, index), so no role is a numeric code, and in a delta the role is named by path. The `index` is the position of the reference in a list of the same role (`operand` 0, 1, 2); a name that is an element of a list is written `name[n]`. See [deltas and their XML form](15-DELTAS-AND-XML.md).

## Durable references

A durable reference is an edge in the RXF object graph. It may represent a type relationship, parentage, ownership, a call, an argument, a result, data use, an import, an entry point, a trait requirement, or another schema-defined role. The role determines which source and target kinds are valid.

Mandatory references must resolve. Optional references may use the invalid-handle sentinel where their schema permits it. Readers refuse missing targets, invalid roles, incompatible object kinds, out-of-range sub-offsets, and duplicate singleton roles.

References never contain:

- a native address;
- a host symbol-table pointer;
- a table index disguised as identity; or
- an implicit lookup against the process that opened the image.

## Native binding

A `Function` expresses semantic behavior. A target-specific `Code` object provides one native implementation. `RuntimeTarget`, `ABISignature`, and `ABIValueLocation` objects describe selection and calling convention. Binding selects compatible Code by explicit target and ABI metadata.

Native fixups are derived packaging work. The linker resolves Function and object handles against the selected executable layout and applies architecture-specific relocations. The persisted semantic edge remains a handle edge inside an arena (a path edge in a delta) even when a native envelope contains a relocated address.

## Imports and capabilities

An `Import` names an explicit external contract. A `CapabilityRequirement` describes the required service, version, rights, effects, and target constraints. Provider addresses are transient runtime input; they are not durable RXF authority.

A build or activation refuses an unresolved mandatory import, ambiguous provider, ABI disagreement, missing capability, or unsupported relocation. It does not fall back to ambient process symbols or hidden syscalls.

## Movement and recovery

Movable objects may change physical offsets during deterministic copying compaction while keeping their paths; compaction rewrites handles. Relocatable native Code is fixed up for its selected envelope. Pinned objects retain the placement required by their contract. Because semantic links are handle-based inside the image and path-based outside it, extraction of an RXF image from ELF, PE/COFF, or BIOS packaging recovers the same object graph rather than envelope addresses.
