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

## 5. Variables and who allocates

A variable is a slot **allocated by its parent**: a member by the object, a module variable by the
module, a local by the activation of the function that declares it. Passing a variable on passes a
reference to the parent's slot. Nothing is allocated by the receiver, so ownership is never in
question.

The first parent is the **run** (what threads and async became, [20](20-EXECUTION-POLICY.md)). A run
owns its cell, and the activations of its calls are frames cut from that cell, the caller cutting the
frame of the callee (at most 16 open, together within the cell budget, 32768 bytes as shipped). In the
tree:

```
runs.7                 a Run
  main                 the activation of the first function: its variables are slots
    p                  a Packet
    make               the activation of a call, cut by its caller
      p                the Packet that make builds
```

Two consequences:

- **The result of a call is a slot of the caller.** The caller allocates it, the callee writes into
  it (the status-out ABI already works this way). A `return p` therefore writes the data of `p` into
  the caller's slot and never leaves a reference to a frame that is gone.
- **Only what a parent holds outlives the run.** An object that must survive its run is created in a
  parent that holds it (the module or another object), not in a frame. How a program says "this belongs
  to that parent" is open (section 9).

## 6. Where names live

- A **module variable** is bound to its module, a **class variable** to its class. Both are children of
  that node, like any other member.
- There is **no global as a separate concept**. A global is a variable on the **root node**; its name
  is its path from the root.
- **Everything of the system lives under `pymergetic.rxf`, including the simplest types** (the
  integers and the rest). The language does not define them: it **assumes they exist**, and the
  genesis of an image creates them before the first line of any program is read. Whatever a program
  needs up to that point is there by construction.

Compared with the tree as it stands, this moves the primitive types (`uint8_t`, `uint64_t`, ... are
types of the root node today, with an alias in the module that uses them) under `pymergetic.rxf`.

## 7. Functions are variables

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

## 8. Generics

`<>` is the one form: `Option<Packet>`, `fn<u16(Packet)>`. An instantiation is an object of its own;
the same arguments are the same object.

## 9. Open (nothing below is decided)

- **Instance variable or class variable.** A variable declared in a class body is a member of every
  instance (a Field). A variable that is bound to the class itself needs a way to be written; C++ says
  `static`, Python says "assigned in the class body". Open.
- **Name lookup order** for an unqualified name: the activation, then the enclosing class, then the
  module, then the root. Proposed, not agreed.
- **The spelling of the base types**: `u8 u16 u32 u64` as in the sample, or the C names the tree
  carries today (`uint8_t`, `uint64_t`).
- **Copy or alias.** Whether assignment copies (as in C++) with `ref` making a reference, which would
  make `ref` a qualifier of any variable and a parameter the place where the difference shows
  (`def swap(self, ref Packet p)` changes the original, `def f(Packet p)` gets a copy). Proposed, not
  agreed.
- **How a program says an object belongs to a holding parent.** Proposed:
  `ref Packet q = self.packets.add(Packet(1, 2, 0))`, with a holder type still to be defined.
- Enums, constants, error handling, `try`, loops and `match`: not yet.
- The markers beyond `ref` (and `const`), and whether their set is closed.
- The implicit or explicit `self` (the sample is explicit, as in Python).
- Operators, integer literals, strings other than docstrings.
- The intrinsics and the interpreter ([37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md)).
