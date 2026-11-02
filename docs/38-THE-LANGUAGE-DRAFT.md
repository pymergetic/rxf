# 38 - The one language, draft 0

Status: **draft, built one element at a time with the owner**. Companion of
[37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md). Nothing here is implemented. The document holds only
what the owner has fixed; everything else is listed as open at the end.

The sample is [`samples/echo.rx`](samples/echo.rx). The draft highlighting is `editors/rx-draft`
(hand-written while the grammar is open).

The language looks and behaves like Python (layout by indentation, `def`, `class`, `import`,
docstrings, `#` comments) and is typed like C++ (the type before the name, `<>` for generics).

## 1. Module

A module is a Python module or package: its place is its **path and file**. There is no `module`
line. In the object world it is a **Module** object at the matching parent and child position of the
tree. Whether it is a package (it has children) or a plain module is at most a flag, and the two are
unified: a module may hold submodules.

## 2. Docstring

A string at the start of a module, a class, a struct or a function is its docstring; a string right
after a variable declaration is the variable's docstring. In the object world each is a **Doc**
object attached to the thing it documents.

## 3. Import

`import net.slip` and `from core import u32, Option` bring a name into the namespace, as in Python.
In the object world an import is an **Import** object under the module that **refers** to the node it
names (one object per imported name). It does not copy anything.

## 4. Class and struct

Both are as in C++ and are the same thing here: **a collection of variables and functions**, with
optional **markers** on a member that say how it relates to other objects. `struct` marks a plain
data collection by convention; `class` is used when it has functions. They are dynamic: a member is
looked up in the object, not fixed at compile time.

In the object world a class or struct is a **Type** object; every variable is a **Field** under it
and every function is a **Function** under it.

The only marker in the sample is `ref`, which says the member refers to an object that someone else
holds instead of being part of this one.

## 5. Variables

A variable is a slot **allocated by its parent**: a member by the object, a local by the function
that declares it, a module variable by the module. Passing a variable on passes a reference to the
parent's slot. Nothing is allocated by the receiver, so ownership is never in question.

## 6. Functions are variables

`fn<u16(Packet)>` is a type like any other, so a function is a variable of such a type, and a
variable of that type can hold any function with that signature:

```
def u16 length_of(Packet p):
    return p.length

fn<u16(Packet)> measure = length_of
```

`def` is the way to write a variable of a function type together with its body. In the object world
the function is a **Function** object at its place in the tree, and `measure` is a variable that
refers to it.

## 7. Generics

`<>` is the one form: `Option<Packet>`, `fn<u16(Packet)>`. An instantiation is an object of its own;
the same arguments are the same object.

## 8. Open (nothing below is decided)

- Enums, constants, error handling, `try`, loops and `match`: not yet.
- The markers beyond `ref`, and whether their set is closed.
- The implicit or explicit `self` (the sample is explicit, as in Python).
- Operators, integer literals, strings other than docstrings.
- The intrinsics and the interpreter ([37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md)).
