# Deltas and their XML form

A **delta** is a list of operations on an image, addressed by path. It is the storage and exchange format of RXFB 6: an image is exactly the result of applying one delta to the empty image, and the canonical delta of an image reproduces it. The text form is XML. Handles never appear in it.

Code: `src/pymergetic/rxftk/core/delta/` (operations, replay, export), `src/pymergetic/rxftk/core/xml/` (parser and writer), `src/pymergetic/rxftk/core/meta/` (fields by name, see [self-description](16-SELF-DESCRIPTION.md)).

## Operations

| Operation | Effect |
|---|---|
| `Create` | A new object of a type, below a parent, with a name, field values, optional references. |
| `Set` | New field values, references, mobility, owner kind, or generation for an existing object. |
| `Delete` | Removes an object and everything below it. |
| `Entry` | Names the entry Function of the image (an empty path clears it). |

## Syntax

```xml
<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Role name="Peer"/>
  <Point name="a" x="1" next="b"/>
  <Point name="b" x="2">
    <rxf:ref role="Peer" index="2" kind="RefKind.Data" target="a"/>
  </Point>
  <rxf:set path="b" x="9"/>
  <rxf:delete path="old.thing"/>
  <rxf:entry path="a"/>
</delta>
```

- The **element name is the type name** (the name of a `Type` object, unique in the image). The attribute `name` is the object's name; the path is the path of the parent plus `.` plus the name.
- **Nested elements are children.** A top-level element is a child of the root unless `rxf:parent="path"` names another parent. The writer nests whenever the parent is the previous open element.
- **Attributes without a prefix are fields**, by the name of the `Field` object. A field holds a number (decimal; signed for signed types), `true`/`false`, a float, a hex string for byte arrays, an **enumerator name** for a field whose type is an enumeration, or, for a `REF` field, the **path** of the target. A value that equals the default (zero, unset reference) is left out.
- **A word is the path of an object, never a bare word or a number.** An enumeration value is its `Enumerator` object, written like every other name: by path, shortened by an alias of the module. `form="Form.Struct"`, `kind="RefKind.Call"`, `layer="@kinds.FunctionLayer.Library"`. A bitset names its enumeration once, on the first value: `flags="TypeFlags.Variable|Signed"`, `effects="@kinds.Effects.Write|Allocate"`. The words of the format (`NodeKind`, `RefKind`, `Binding`, `OwnerKind`, `Mobility`, `Form`, `TypeFlags`, `FieldFlags`, `DocKind`, `DocStatus`, `DocCodec`, `DocRole`) are enumeration Types of the ground, so their paths sit at the root and have no module (`RefKind.Data`). They exist once, as tables in the C++ module `core/kinds/`; the bootstrap, the XML, and the Python enums of `pymergetic.rxftk.core.kinds` are all made from them. The words of the *data* are `Type` objects of form `Enum` whose children are `Enumerator` objects (name and value); a `Type` flagged `Bitset` is a bitset. A number, a bare word, or a path that is not an `Enumerator` of the field's enumeration is refused. Only counts, sizes, indices, offsets, widths, and alignments stay numeric.
- **Spelling.** Modules, fields, and functions are `snake_case`; Types, enumerations, enumerators, and Role objects are `CamelCase` (`AbiValueFlags`, `Writes`, `AbiLocation`). The one name `role` is a Role object; a field that holds some other kind of role is called after what it is (`value_role`).
- **Everything the format itself says lives in the namespace `urn:pymergetic:rxf`**, written with the prefix `rxf`. No field, type, or object can collide with it, and no name starts with an underscore. Elements: `rxf:ref`, `rxf:set`, `rxf:delete`, `rxf:entry`, `rxf:doc` (the text of a doc: see [Documentation](#documentation-docs22)). Attributes of an object: `rxf:parent`, `rxf:index`, `rxf:kind` (`MODULE`, `FN`, `CODE`, `DATA`, ...; omitted when the type implies it: a `Type` has `node_kind`, the kind of its objects, so a `Module` is a `MODULE` without saying so, and `DATA` is the default), `rxf:owner`, `rxf:mobility`, `rxf:generation`, and `rxf:data` (the hex of the bytes a type does not describe: the whole payload of a union, a field-less type, or the tail of a variable type). `rxf:set` takes `path` plus the same `rxf:` attributes and fields, and `rxf:refs="none"` or `rxf:refs="data"` to drop references.
- Only `name` and `path` are reserved as field names, and `delta` as a type name.
- `<rxf:ref role="path" index="n" kind="RefKind.Call|Data|Entry|Type|Import|Implementation|Owner|Doc" binding="Binding.Optional" target="path"/>` is an explicit reference of the object (24 bytes in the cell). Reference *fields* in a payload are attributes; `rxf:ref` carries the role. `RefKind.Doc` is an editorial doc placement: see [Documentation](#documentation-docs22). See below.
- Parsing is done by [pugixml](https://pugixml.org) (vendored in `third_party/pugixml`): comments, the XML declaration, the five entities and character references. Not parsed: DTD, CDATA. Nesting is limited to 256 levels. Errors carry the line.

## Aliases

Paths are long (`pymergetic.rxf.roles.call.callee`). A module may open with `Alias` objects, which behave like Python imports: `<Alias name="call" target="pymergetic.rxf.roles.call"/>` makes `@call.callee` mean that path inside the module (and below it). A path that starts with `@` is an alias path: the first segment names an `Alias` child of the nearest enclosing `Module`, the rest follows the target. The `@` marks it, so a lookup never confuses it with a name (no name starts with `@`). An alias target is always absolute, and an alias is an object like any other (type `Alias`, node kind `ALIAS`, one `REF` field `target`, and the bitset `flags` of `AliasFlags`).

The image holds the absolute path; the **writer** chooses the alias: the longest alias of the module that is a prefix of the path, used only when it is shorter, so the same image always gives the same text. The aliases come first in a module.

An alias must be **used**: some path of an object in its module (a reference field, a reference, or the role of a reference) must be written through it by that rule. The link check reports an alias that is not as a problem, so an alias is never dead text. An alias that is meant as a name of the module whether or not a path uses it carries `flags="AliasFlags.Reexport"` and is exempt. The image does not store who uses an alias (a use is a path, resolved on load); it is computed from the rule (`Image.alias_uses()`), and the inspector shows it both ways: the alias lists the objects written through it, and an object lists its aliases.

The starter's aliases are the modules that the paths of a module go through: one alias for each Module that the module refers into at least twice, named like that module, so a path reads `@alias.symbol` (`@numeric.overflow`, `@contract.refusal_variant`). There is no catch-all alias for the namespace.

## Roles and lists

A **role** says what a reference is *for* (`signature`, `body`, `successor`, `operand_type`). A role is an object: an instance of the field-less type `Role` at `pymergetic.rxf.roles.<area>.<name>`, so the text names it by path like everything else, and a numeric code appears nowhere. The cell stores the role's handle; compaction keeps it, and deleting a role while a reference uses it is refused.

**One edge, said once.** A reference *field* is already a reference: `signature="..."` needs no `<rxf:ref role=Signature>` beside it, and the link check refuses one that repeats a field. A list of children is the tree, and the schema says which list it is: `ABISignature` has a `Field` `locations` flagged `Children` (value type `ABIValueLocation`), so its `ABIValueLocation` children are its `locations`; no object lists them again. The other direction of an edge is computed and declared the same way: `SemanticBlock.predecessors` is a `Field` flagged `Inverse` whose `via` is the role `Successor`, so the starter stores `Successor` and never `Predecessor`. `Image.members(handle)` returns these lists, and the inspector shows them under the name of the field. What stays an `<rxf:ref>` is a relation that has no field and is not such a list: a list of types, a provenance, or a subset of the children where the type of the children does not say which (`SemanticBlock` has parameter values *and* other values below it).

**Order among siblings.** The children of an object are ordered by name, with `[n]` read as a number (`argument[2]` before `argument[10]`). When a list is in another order, each member says its place: `rxf:index="n"` on the object (omitted when 0), a 32-bit `index` in the cell. A type that has its own field called `index` (`SemanticBlock`, `Parameter`, `SemanticOperation`, ...) says it there and does not need the attribute. The members of a type below an object, in this order, are the list (`Inspector.members`). `rxf:set path="..." rxf:index="n"` changes it.

A reference that is the n-th of a list that is not made of children carries `index` (omitted when 0): `<rxf:ref role="pymergetic.rxf.roles.semantic.InputType" index="1" .../>`. The same holds for names: the n-th element of a list is called `name[n]` (`argument[0]`, `operand[1]`, `block[2]`), with the number in brackets, never `name_n`. The brackets are allowed in a name and are the only place a digit run means "position".

## Documentation (docs/22)

Documentation is a graph of recorded facts **inside the image**, not a comment that rots in a file. A doc is an object of the built-in `Doc` type: three words (`kind`, `status`, `codec`) and its text in the tail of the payload. `<rxf:doc>` writes one, **nested in the object that holds it**; its content is the text, and its `role` says why it is placed there.

```xml
<Thing name="guide">
  <rxf:doc name="intro" role="DocRole.Narrates">This explains the guide &amp; its scope.</rxf:doc>
  <rxf:doc name="plan" kind="DocKind.Todo" role="DocRole.Narrates" codec="DocCodec.Raw">Expand the guide.</rxf:doc>
</Thing>
```

- The placement is one edge — `RefKind.Doc` with a `DocRole` (`Narrates`, `Attaches`, `References`; a topic link between docs carries `Parent` or `Related`) — written on the child, said once on the parent. The same doc placed from elsewhere is an ordinary `<rxf:ref role="DocRole.References" kind="RefKind.Doc" target="guide.intro"/>`; one payload sits behind many placements.
- `RefKind.Doc` is **editorial, never a runtime reference**: the edge has no runtime slot (its index is 0) and a doc placement does not move data. A doc edge that claims a slot is refused at replay.
- `kind` is `DocKind` (`Narrates` | `Topic` | `Todo`); `status` is the state word of a todo (`Open` | `Resolved`), changed like every fact: `<rxf:set path="guide.plan" status="DocStatus.Resolved"/>`.
- The `codec` flag (`DocCodec.Lz4` | `DocCodec.Raw`) says how the tail stores the text: compressed where it pays, raw where it does not. The text face decompresses on demand, so both states read alike. **A doc with an empty body is not written in `DocCodec.Raw`** — the import refuses it.
- **A citation is a Doc whose text is its locator** — a record of `key=value` lines: `path=...`, `lines=first-last` (1-based), and an optional `sha256=...`. The 24-byte reference carries `role`/`index`/`kind`/`binding` and **no locator payload**; the locator rides in the citing Doc's text tail (the seam, decided in [docs/22](22-DOCUMENTATION-AND-INTENT.md#the-locator-seam-the-citing-docs-text-tail-decided)), so a citation round-trips through this text form like any other doc and the permalink it stands for is derived from it, never stored. The provenance facts of an import (its origin record, its source payload, its hash records) are ordinary Doc objects and round-trip the same way.
- The text is the content of the element, so a docstring **round-trips**: `export` writes it, `apply` reads it back, and the digest does not move. Comments survive the text form.

## Module tree

One file is not how a program is kept. `pymergetic.rxftk.core.tree` (C++, `core/tree/`) writes an image as **one delta per module, laid out like Python modules**: the path of the file is the path of the module, a module with sub-modules is a package (`__init__.rxfd` in its folder), and one without is a plain file.

```
starter/
  __init__.rxfd                         what sits at the root (the types, the entry)
  pymergetic/__init__.rxfd              module pymergetic (it has sub-modules)
  pymergetic/rxf/algorithms.rxfd        module pymergetic.rxf.algorithms
  pymergetic/rxf/foundation/__init__.rxfd   module pymergetic.rxf.foundation
  pymergetic/rxf/foundation/heap.rxfd       module pymergetic.rxf.foundation.heap
  pymergetic/rxf/roles/call.rxfd        module pymergetic.rxf.roles.call
```

A file **does not name its parent**: the path says where its module sits. A file defines its `Module` and everything below it that is not a module, and nothing else; the loader refuses a file that starts with something else, defines anything outside its module, or is a second file for the same module. **Every** `Module` object, wherever it sits, is a file of its own (`Image.export(module=path)`; `""` is the root). The files are independent texts, so the tree is **written and read on several threads** without the GIL: the image is only read while printing, and each file is parsed into its own operations before one atomic replay applies them all, the root `__init__.rxfd` last because it names the entry. `threads=0` is one thread per core.

Writing is canonical: the same image gives the same files, whatever the thread count, and a rewrite replaces only `.rxfd` files. A module name that could leave the directory (`/`, `\`) or is called `__init__` is refused before anything is written. The interface is `tree.files(image)`, `tree.write(image, directory, overwrite=False)`, and `tree.load(directory)`; the module's kind is the C++ enum, never a number in Python.

## Replay

`Image.apply(text)` is atomic: the delta is applied in place under a transaction (an undo log, [19](19-SELF-EDITING-PATH.md)), linked, checked, and then committed. On any error the transaction is rolled back and the image is unchanged, byte for byte.

- A run of consecutive `Create` operations is a **set**: the order inside it does not matter. A create that waits for its parent, its type, or the `Field` objects of its type is retried until the run settles.
- `Set`, `Delete`, and `Entry` are **barriers**: they see everything before them.
- References may point forward (to an object created later in the delta). When the delta ends, every one must resolve.
- `Delete` is recursive and **refused while anything outside the subtree still uses it** (a parent, a type, a reference, a reference field, the entry). The message lists the users.
- The **ground** (root, `Type`, `Field`, `uint32_t`, `uint64_t`, `Enumerator`, `Doc`, the enumerations of the format (`NodeKind`, `Form`, `TypeFlags`, `FieldFlags`, `DocKind`, `DocStatus`, `DocCodec`, `DocRole`, ...) with their enumerators, and the `Field` objects of `Type`, `Field`, `Enumerator`, and `Doc`) cannot be created below, changed, or deleted.
- Derived fields are never written and always recomputed: `Type.descriptor_tag`, `Type.field_count`, `Field.descriptor_tag`, `Field.owner_type` (the parent).
- At the end, the whole image is checked (`Image.check()`): dead handles, payloads that do not fit their type, malformed `Type` and `Field` objects, duplicate type names, the ground against the bootstrap.

## Canonical form and digests

`Image.export()` writes every non-ground object once, in path pre-order (children by name), then the entry. Attributes are in field order. The text is therefore a function of the image's content alone: handles, creation order, and free slots do not show.

- `Image.digest()`: SHA-256 of the **compacted** bytes. Compaction is copying, in path order, and idempotent. Two images with equal content have equal digests whatever their history.
- `Image.delta_digest()`: SHA-256 of the canonical text. A module's version is the delta digest of its subtree.
- `export` then `apply` onto an empty image gives an image with the same digest. The test suite proves it for the starter image (15,059 objects).

## Limits

- Names are UTF-8 text without control characters and without `.`. The name of a `Type` or a `Field` must also be an identifier (letters, digits, `_`, `-`, `.`; not starting with a digit), since it becomes an element or attribute name; a `Field` may not be called `name` or `path`, and neither a `Type` nor a `Field` may start with `_`. An object name may carry a list index in brackets (`argument[0]`).
- One `Field` is at most 2^31 in offset, count, and element size; a type is split into at most 65,536 values. A type larger than the limit of the image has no objects.

## Commands

`rxf` is one [Python Fire](https://github.com/google/python-fire) entry point (`pymergetic.rxftk.cli`). **A command lives next to what it operates on, and the command path is the module path**: the public functions of `core/object/cli.py` are `rxf core object <function>`, those of `server/cli.py` are `rxf server <function>`. Every `cli.py` below the package is found by its path, so a new one is a new group and there is no list to keep. `rxf`, or a group name, lists the commands below it.

```sh
rxf core object info   starter               # objects, entry, digests, problems (JSON)
rxf core object check  starter               # link check; exit status 1 on problems
rxf core object digest starter.rxfb          # image digest and delta digest
rxf core object save   starter starter.rxfb  # .rxfd: one delta, .rxfb: bytes, else a module tree
rxf core delta show    starter --module=pymergetic.rxf.roles   # canonical XML of one module
rxf core delta apply   starter patch.rxfd more.rxfd --to=out.rxfb # atomic replay of deltas
rxf server browse      starter --port=8420   # read-only inspector (needs the `server` extra)
```

A SOURCE is a directory (a module tree), a `.rxfd` file (a delta on the empty image), or anything else (RXFB 6 bytes in canonical form). Exit status: 0 done, 1 the image has problems, 2 the input could not be used.
