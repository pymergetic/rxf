# Self-description

The core knows four layouts: the arena header, the object cell header, `Type`, and `Field` (see [ground layout](03-ALLOCATOR-STATE.md)). Every other payload is read through the `Type` object of its object and the `Field` objects below that type. Nothing else in the core knows a payload layout, so one generic reader prints, converts, checks, and diffs every object.

Code: `src/pymergetic/rxftk/core/meta/`. Nothing here is part of the binary format; it is derived from the image on demand.

## Type and Field

```
Type  (48 bytes): descriptor_tag u32, form u32, size u64, inner_type REF?, return_type REF?,
                  align u32, flags u32, field_count u32, node_kind u32
Field (48 bytes): descriptor_tag u32, flags u32, owner_type REF, value_type REF,
                  offset u64, count u64, via REF?
```

- Forms (`Form`): `Meta, Void, Bool, Uint, Sint, Float, Bytes, Struct, Union, Enum, Array, Ref, Function, Control, Expression, Execution, Reserved16, Opaque`.
- Type flags (`TypeFlags`): `Variable` (the payload may be longer than `size`; the tail is raw bytes), `Signed`, `SelfDescribing`, `Bitset` (an enumeration whose enumerators are bits).
- Field flags (`FieldFlags`): `Mutable`, `Optional`, `Repeated`, **`Ref`**, and the two that declare a relation instead of a value: **`Children`** and **`Inverse`** (below).
- `Type.node_kind` is the kind of the objects of the type (`MODULE`, `FN`, `ALIAS`, ...; unset means `DATA`).
- These words are objects of the ground: `NodeKind`, `RefKind`, `Binding`, `OwnerKind`, `Mobility`, `Form`, `TypeFlags`, and `FieldFlags` are `Type` objects of form `Enum`, each with `Enumerator` children (the type `Enumerator` has one field, `value`). The fields `Type.form`, `Type.flags`, `Type.node_kind`, and `Field.flags` are typed by them, so the same generic reader that prints a `Point` prints a `Type`. The table of names is C++ (`core/kinds/`) and the Python enums are made from it. An enumerator is an object like any other and is written by its path (`Form.Struct`, `@kinds.AbiKind.Aapcs64`); there is no bare word in the text.
- **What sits at the root.** The root holds what must exist before the first module can: the ground, and in the starter the two object kinds of the module system with the enumerations of their fields: `Module` (`ModuleCategory`, `ModuleFlags`) and `Alias` (`AliasFlags`). `Module` is the type of every module, so it cannot live inside one, and an alias is written in every module, so it belongs to none. Everything else is a module's. Types are found by name, not by place, so the place of a type does not change how it is written.
- **Declared relations.** A `Field` flagged `Children` or `Inverse` holds no value and takes no place in the payload; it says what a list *is*, once for the type, not once per object. `Children`: the children of the value type below an object are the list this field names (`ABISignature.locations`, `Signature.parameters`, `Function.implementations`). `Inverse`: the objects of the value type that point at this object through `via` (a reference Field of the value type, or a Role) are the list (`SemanticBlock.predecessors` through the role `Successor`). `Image.members(handle)` returns each declared list in order; there is no other way to name a list. A type has at most one `Children` field for a child type, so the role of a child follows from the schema and not from the name of the child. The order of a list is the order of siblings ([15](15-DELTAS-AND-XML.md)). `via` is set on an `Inverse` field and on no other.
- A `REF` field is one `uint64` that holds a handle. It is the **only** place a handle may sit in a payload. Export writes it as a path, import resolves the path, compaction rewrites it, and the link check follows it. `OPTIONAL` means it may be unset (`NONE`, all ones). A `uint64` that is not flagged is a number.
- `Field.count` defaults to 1. `owner_type` is the parent; `field_count` is the number of `Field` children; both are derived, never written.
- Children of a `Type` are `Field` objects and nothing else.

## Layout of a payload

A type is turned into **leaves**: unsigned, signed, bool, float, hex, enumeration, or reference values with an offset and a size. Nested structs become dotted names (`a.b`), arrays `name.i`, an array of bytes one hex leaf. A leaf is written as an attribute, left out when it equals its default.

- **Raw** types have no leaf list: unions, types without fields, and types whose fields overlap. The whole payload is one `_data` hex value.
- **Variable** types have leaves for the fixed prefix and a `_data` tail for the rest.
- Padding between leaves must be zero; a payload that does not fit its type is a link error.

## What the link check proves

`Image.check()` returns `(path, message)` pairs, empty when the image links:

- every parent, type, reference, and reference field resolves to a live object; mandatory ones are set,
- each payload has the size its type says (exactly, or at least for variable types),
- each `Type` has a valid form and alignment, as many `Field` children as it declares, fields that do not overlap, stay inside the size, and are aligned; a reference field is one `uint64`,
- no two `Type` objects share a name; names of types and fields are identifiers,
- a reference does not repeat a reference field of the same object with the same target,
- a declared relation has a value type that is a `Type`; a `Children` field is the only one of its child type in its type; an `Inverse` field has a `via` that is a reference `Field` of its value type or a `Role`; `via` is set on no other field,
- the entry is a live object,
- the ground equals the bootstrap object for object,
- every word of the format (kind, owner, mobility, reference kind and binding) has a name, or the object could not be written,
- every `Alias` is used by a path of its module, or carries `Reexport` ([15](15-DELTAS-AND-XML.md); checked only when nothing else is wrong).

## Why it is shaped like this

An image is checked against its own description instead of per-type decoders: every payload type that has instances has `Field` objects, and a field says which `uint64` is a reference (`REFERENCE`). The starter carries all of its types this way.
