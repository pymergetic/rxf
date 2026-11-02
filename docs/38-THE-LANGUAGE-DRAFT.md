# 38 - The one language, draft 0

Status: **draft, built one element at a time with the owner**. Companion of
[37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md). Nothing here is implemented. The document holds what the
owner has fixed (sections 1 to 9) and, at the end, what is still open.

The sample is [`samples/echo.rx`](samples/echo.rx). The draft highlighting is `editors/rx-draft`
(hand-written while the grammar is open).

The language looks and behaves like Python (layout by indentation, `def`, `class`, `import`,
docstrings, `#` comments, variables that refer to objects) and is typed like C++ (the type before the
name, `<>` for generics, `const`, `static`).

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

`import net.slip` and `from pymergetic.rxf.types import uint32_t` bring a name into the namespace, as
in Python. In the object world an import is an **Import** object under the module that **refers** to
the node it names (one object per imported name). It does not copy anything.

## 4. Class and struct

Both are as in C++ and are the same thing here: **a collection of variables and functions**, with
optional **markers** on a member. `struct` marks a plain data collection by convention; `class` is
used when it has functions. They are dynamic: a member is looked up in the object, not fixed at
compile time.

In the object world a class or struct is a **Type** object; every variable is a **Field** under it
and every function is a **Function** under it.

The markers are a **closed set of two**: `const` (read only) and `static` (bound to the class, not to
each instance).

**Nesting.** Everything is a node, so everything may be nested: a class in a class, a function in a
function, a class in a function, a function in a class. A nested definition is a child node of the one
that contains it, and its path says so (`Echo.Stats`, `Echo.handle.span`).

A nested definition is a **node, not a closure**: it may use the names of the nodes around it (types,
functions, class and module variables), but it does not capture the local variables of the activation
around it. What it needs from there it is given as an argument.

## 5. Variables

A variable is a slot **allocated by its parent**: a member by the object, a module variable by the
module, a class variable (`static`) by the class, a global by the root node, a local by the activation
of the function that declares it. There is no global as a separate concept: a global is a variable on
the root node.

Where the storage physically lies is one of three places: the **run's frames** (locals), the
**global** part of the tree (variables of the root and of modules), or the **heap** (what a holding
parent keeps). A parameter may always be passed on from parent to parent; the object stays where its
parent put it.

The first parent is the **run** (what threads and async became, [20](20-EXECUTION-POLICY.md)). A run
owns its cell, and the activations of its calls are frames cut from that cell, the caller cutting the
frame of the callee (at most 16 open, together within the cell budget, 32768 bytes as shipped).

```
runs.7                 a Run
  main                 the activation of the first function: its variables are slots
    p                  a Packet
    make               the activation of a call, cut by its caller
      p                the Packet that make builds
```

## 6. Names

- A **module variable** is bound to its module, a **class variable** to its class.
- **Everything of the system lives under `pymergetic.rxf`, including the simplest types.** The language
  does not define them: it **assumes they exist**, and the genesis of an image creates them before the
  first line of any program is read.
- The base types keep their C names: `uint8_t`, `uint16_t`, `uint32_t`, `uint64_t`, `int8_t`, ... They
  are grouped under `pymergetic.rxf`; the sample uses `pymergetic.rxf.types` for them and
  `pymergetic.rxf.templates` for the generic ones (`Option`, `list`, `fn`). The grouping is a proposal
  (section 10).
- **Lookup of an unqualified name** goes from the inside out: the activation, then the members of each
  enclosing node outwards, then the module, then the root. A local of an enclosing function is not
  found (section 4). A path is always unambiguous (`pymergetic.rxf.types.uint32_t`).

## 7. Functions are variables

`fn<uint16_t(Packet)>` is a type like any other, so a function is a variable of such a type, and a
variable of that type can hold any function with that signature:

```
def uint16_t length_of(Packet p):
    return p.length

fn<uint16_t(Packet)> measure = length_of
```

`def` is the way to write a variable of a function type together with its body. In the object world
the function is a **Function** object at its place in the tree, and `measure` is a variable that
refers to it.

`self` is written out, as in Python, and it is typed like every other parameter: `def swap(Packet
self, ...)`. In the object world it is a **Parameter** of the function.

## 8. Generics

`<>` is the one form: `Option<Packet>`, `fn<uint16_t(Packet)>`. An instantiation is an object of its
own; the same arguments are the same object.

## 9. Layout and typing

The type comes first everywhere (`uint32_t sum = 0`, `def uint16_t checksum(Packet self)`); blocks are
formed by indentation.

## 10. Open (the owner's rule "variables are references" raised these)

The owner decided that variables are **references** (object ids), as in Python, and that `copy()`
returns a reference that carries a **link to the reference it was copied from**, visible as long as
the original exists. There is no `ref` marker and no pointer-like behaviour. What follows from that is
not yet fixed:

1. **Ownership against reference.** Every object has one parent, which owns it, and lives as long as
   that parent. A variable only refers. Using a reference to an object whose parent is gone is a
   refusal (the handle's generation no longer matches), not undefined behaviour. Open: is that
   run-time refusal enough, or must the language reject at definition time a store that outlives the
   object?
2. **What `return p` does** with an object that lives in the frame of the callee. Proposed: the object
   is **moved to the caller's frame** (its parent changes, its id stays valid), so nothing is copied.
3. **What storing into a holder does** (`self.last = p`, `self.kept.append(p)`): store only the
   reference, adopt the object (it becomes a child of the holder), or copy it. Proposed: a container
   that owns its elements adopts them; assigning a member stores the reference.
4. **Scalars.** Whether `uint32_t` values are copied by value (proposed) while instances of classes
   are objects that are referred to.
5. **What `copy()` copies.** The object and the children it owns (proposed), with every reference it
   holds copied as a reference; and the link to the origin is weak (it dangles when the origin goes).
6. **The grouping** of the base and generic types under `pymergetic.rxf`.
7. Not yet discussed: operators and literals, control flow, error handling, enums and constants,
   strings and arrays, how a run is started, the intrinsics and the interpreter
   ([37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md)).
