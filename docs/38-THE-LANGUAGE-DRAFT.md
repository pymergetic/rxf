# 38 - The one language, draft 0

Status: **draft for the owner to cut**. Companion of
[37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md). Nothing here is implemented. Items marked
**DECIDE** need the owner; each has a recommendation.

The sample is [`docs/samples/echo.rx`](samples/echo.rx). The draft highlighting is
`editors/rx-draft` (written by hand while the grammar is open; it becomes generated from the
language's own table when the grammar is fixed).

## 1. Principles

1. **Everything is an object.** The file is a module object, a class is a type object, a field is a
   field object, a function is a function object, a constant is a typed object, an enum member is an
   object. Only two things are not objects: the *local values* inside a function body (they are values
   of its graph) and the raw machine words an intrinsic touches.
2. **The text is a face of the objects.** What is stored is the objects. The text is printed from them
   in one canonical form and parsed back into the same objects. A text is never stored as written.
3. **It looks and behaves like Python**: layout by indentation, `def`, `class`, `import`, `for ... in`,
   `if/elif/else`, `#` comments, docstrings, modules as files, names as dotted paths.
4. **It is typed like C++**: static types, the type before the name (`u32 sum = 0`), `const`, fixed-width
   integers, struct layout as in C, generics by instantiation (`Option<Packet>`).
5. **Errors are refusals, not exceptions.** A function declares what it refuses; `try` propagates a
   refusal; there is no unwinding.
6. **Names are paths and are resolved when read.** `net.slip.send` is the function object at that path;
   the reader stores the reference to it, not the string.
7. **No raw pointers.** A value of a class type is a handle to an object. Raw addresses exist only
   inside `unsafe` and only the intrinsics take them.

## 2. What becomes what in the object world

| In the text | In the object world |
|---|---|
| a file, and its `module net.echo` line | a **Module** node at the path `net.echo`; the line is optional (the path is the file's place in the tree) and a mismatch is refused |
| the docstring | a **Doc** object attached to the module (and the same for a class or a function) |
| `import net.slip` | an **Import** object under the module that refers to the node `net.slip` |
| `from core import u8, Option` | one **Import** per name, each referring to the node it names |
| `import x as y` | an **Alias** object under the module |
| `const u16 MTU = 1500` | a named object `net.echo.MTU` of the type `u16`, value in its payload, flagged immutable |
| `enum Verdict:` | a **Type** object `net.echo.Verdict` of form Enum; each member is an **Enumerator** object with its value |
| `class Packet:` | a **Type** object `net.echo.Packet` of form Struct |
| a field line `u32 src` | a **Field** object under the type, with its type, and the offset and size by C layout |
| `u8[MTU]` | an array **Type** (element type and count); the same pair is the same object |
| `Option<Packet>` | an instantiated **Type**: a reference to `core.Option` and one to `Packet`; the same arguments are the same object |
| `def u16 checksum(const Packet self)` | a **Function** object `net.echo.Packet.checksum` under the type; `self` is its first **Parameter** |
| `def Verdict decide(...)` at module level | a **Function** object `net.echo.decide` under the module |
| the signature | the **Signature**: parameters, result type, and the refusal set |
| `refuses TooLong` | the refusal set of the signature; `TooLong` is a named object under the module (**DECIDE 3**) |
| the body of a function | its **graph**: blocks, operations, terminators |
| a local `u32 sum = 0` | a **value** of the graph, not an object |
| `Packet(1, 2, 0)` | an `allocate` of an object of type `Packet`, then the field stores; the result is a **handle** |
| `try decide(p)` | a call whose refusal edge leaves the function with the same refusal |
| a statement at the REPL | executed now and not stored; a *definition* at the REPL becomes the objects above |

Two consequences worth stating:

- An object that a program makes at run time (`Packet(1, 2, 0)`) and an object that a definition makes
  (`class Packet`) are the same kind of thing and live in the same arena.
- Renaming a module or a type does not break references, since they are handles; only the printed
  text changes.

## 3. The sample, read against the table

`docs/samples/echo.rx` has one module, one enum, one class with a field block and two methods, two
free functions and a REPL line. In the object world that is: 1 Module, 4 Import objects for the names
it takes from `core` plus one for `net.slip`, 1 constant, 1 enum Type with 2 Enumerators, 1 struct Type
with 4 Fields and 2 Functions, 2 more Functions, and one instantiation `Option<Packet>`.

## 4. DECIDE

1. **Type-first everywhere** (`def u16 checksum(const Packet self)`, `u32 sum = 0`) or the Python
   annotation form (`def checksum(self: Packet) -> u16`, `sum: u32 = 0`). The sample uses type-first
   because it reads as C++ typing; the annotation form reads as Python. Recommendation: type-first.
2. **Generic syntax.** `Option<Packet>` as in C++, or `Option[Packet]` as in Python's typing.
   Recommendation: angle brackets, since the type comes first and `[]` already means an array.
3. **How a refusal is declared.** By use (`refuse TooLong` creates the name `net.echo.TooLong`) or by an
   explicit line (`refusal TooLong`). Recommendation: explicit, so a typo is not a new refusal.
4. **Naming convention carries meaning in the highlighting only.** Types are CamelCase, constants are
   UPPER_CASE, everything else is lower_case. The compiler does not depend on it; the draft grammar
   uses it because it cannot resolve names.

## 5. After the principles

Only when 1 to 4 are settled does the next layer follow: the grammar in full, the intrinsics, the reader
and the interpreter of [37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md). The intrinsic set of the earlier
draft (arithmetic, bits, compare, width, memory in `unsafe`, object access, calls, platform) is kept as
the list the interpreter implements, but it is not part of the language's surface.
