# 27 — Editing: one mutation is one delta

The object world has one writer: a delta. There is no other way to change an image. A
transaction is one delta, undo is its inverse, history is the delta chain ([15](15-DELTAS-AND-XML.md)).
Everything below is the face of that one rule.

> Status: design agreed 2026-10-16, not yet implemented. The mount lane lands it.

## The set

Structure (cells and their place):

```
image.create(type, parent, name) -> handle   # a cell; size and align come from the Type
image.rename(target, name)                   # name is an identifier; the schema checks it
image.move(target, new_parent)               # the place is the sibling order (docs/15)
image.delete(target)                         # refusals: dangling refs, declared children
```

Values (leaves and references — the only two kinds of value there is):

```
image.put(target, "a.b.2.field", value)      # a leaf; width, sign and enum come from the Field
image.ref(target, field, to)                 # the ONLY place a handle may be written
image.unset(target, field)                   # OPTIONAL fields only
```

Transaction and check (the conscience):

```
with image.edit() as e:                      # collects ops
    ...
delta = e.commit()                           # ONE .rxfd: all of it or none of it
image.check()                                # the link check IS the edit validator
image.apply(delta)                           # the existing face (docs/15)
```

Reading (what editing needs to see):

```
image.get(target, "a.b.c") -> value          # a leaf, through the generic reader
image.members(target)                        # the declared lists, in order (docs/16)
image.path(handle) / image.type_of(target)   # where it stands, what it is
```

## Rules

- Derived is never written: `owner_type`, `field_count`, and `Children`/`Inverse` fields take
  no value and no place. `put` on one is a refusal, not a warning.
- Every refusal is one of the link check's codes ([16](16-SELF-DESCRIPTION.md)). There is no
  second error catalogue: a refused edit is a failed check, said before the delta exists.
- Paths name things, handles identify them. Enumerators are written by path (`Form.Struct`),
  like in the text.
- The width comes from the field, not from Python: `put` takes a number; overflow against
  `Type.form`/`size` is a refusal.
- No query language (walking `members` is the query), no second store, no undo stack inside
  the image (undo lives as an inverse delta beside it).

## One set, every seat

The set is defined once, in the object world. Every seat just reads it:

- the host C++ face (`pm_image_edit_*`; `edit_init`/`edit_deinit`, `create`/`destroy` for the
  transaction objects),
- the mounted Python face: the generated classes carry these verbs as methods — `alloc.size = 4096`
  is `put`, `alloc.kind.ref(x)` is `ref`, `image.edit().commit()` stays the one transaction,
- the microcontroller seat: the same card, its own interpreter ([23](23-MICROPYTHON-EXPANSION.md)),
- the text form (`.rxfd`) is the same ops written down.

Defined in rxf once, therefore available in python, therefore the same in upy. There is nothing
that could drift: the only implementation is the one in the image's language; the rest is display.
