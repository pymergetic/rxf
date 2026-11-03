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

The markers are a **closed set of three**: `const` (read only), `static` (bound to the class, not to
each instance) and `weak` (a reference that does not keep its object alive, section 5).

**Nesting.** Everything is a node, so everything may be nested: a class in a class, a function in a
function, a class in a function, a function in a class. A nested definition is a child node of the one
that contains it, and its path says so (`Echo.Stats`, `Echo.handle.span`).

A nested definition is a **node, not a closure**: it may use the names of the nodes around it (types,
functions, class and module variables), but it does not capture the local variables of the activation
around it. What it needs from there it is given as an argument.

## 5. Objects, variables and lifetime

**Everything is an object, numbers included.** A number is an immutable object; its identity cannot
be seen, so a typed slot (`uint32_t`) may hold it inline without an id. Whether it is stored inline
or as a cell is a matter of representation, not of the language.

**A variable is a reference.** A module variable, a class variable, a member, a parameter and a local
are all slots that hold a reference to an object (an object id), as in Python. Assignment copies the
reference, never the object. A global is a variable on the root node.

**There is no function stack, and a call is an instance.** A function is an object like any other
(section 7), and calling it creates an **instance** of it, exactly as calling a class does. The
variables the body declares, its parameters and its result are the **fields** of that instance. The
instance is allocated by the **caller** and assigned to the **active run**, which is itself only an
object id. The **run** (what threads and async became, [20](20-EXECUTION-POLICY.md)) is the origin of
all memory, and everything a run creates lives in the run. Because nothing belongs to a function,
`return p` passes the reference and nothing is moved or copied; the instance of the call is freed by
its reference count like any other object, and the result lives on if the caller holds it.

The run has a **quota** of memory; a call that cannot be allocated within it is refused with
`OutOfMemory`.

A program can name runs where it must cross their boundary: `run.current` is the run it is in and
`run.spawn(f, args)` starts another run that executes `f(args)` and returns a reference to it. The
names are a proposal; the need is agreed.

```
runs.7                 a Run
  main                 the first call; the variables the caller allocated for it
    p                  a Packet
    make               a call; the variables main allocated for it
      p                the Packet that make builds (still alive after make returns, if referenced)
```

**Lifetime is by reference count.** An object lives while something refers to it; the last release
frees it. Definitions (modules, classes, functions) are nodes of the tree and live as long as the tree.

**Cycles** are broken with `weak`: a `weak` member or variable refers to an object without keeping it
alive, and using it after the object has gone is a `Dangling` refusal. A parent that refers to its
child strongly and is referred to by it `weak` is the usual shape.

**Reference counts are atomic**, since runs share objects.

**A reference to a dead object is a refusal, not undefined behaviour.** The id carries a generation
that no longer matches (`Dangling`). This is checked at run time. When an image is edited (a delta is
applied), the image already refuses a change that would leave a reference to an object that is gone,
so the same rule holds at the moment objects are created.

**`copy(p)` is shallow, as in Python.** It makes a new object and returns a reference to it; the slots
of the new object refer to the same objects as the slots of the original, so references stay as they
are. The new object has a weak link to the object it was copied from (at the root of the copy only),
visible as long as the original exists. A deep copy is a separate, explicit operation (its name is
open).

## 6. Names

- A **module variable** is bound to its module, a **class variable** to its class.
- **Everything of the system lives under `pymergetic.rxf`, including the simplest types.** The language
  does not define them: it **assumes they exist**, and the genesis of an image creates them before the
  first line of any program is read.
- The base types keep their C names: `uint8_t`, `uint16_t`, `uint32_t`, `uint64_t`, `int8_t`, ... They
  are grouped under `pymergetic.rxf`; the sample uses `pymergetic.rxf.types` for them and
  `pymergetic.rxf.templates` for the generic ones (`Option`, `list`, `fn`). The grouping is a proposal
  (section 11).
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

### Recursion

A function **carries what is known about its own recursion**, as data of its object:

- **Derived** from the graph, which names the callee of every call: whether the function is
  recursive, **directly** or **indirectly** (through others), and whether its recursive calls are in
  **tail position**.
- **Declared** by the program: whether recursion is allowed at all, and to what **depth**.

Recursion is an **error unless declared**. A function that is derived to be recursive and is not
declared so is refused when it is defined (also when an image is edited).

The declaration is what lets the machine choose how a call is stored, instead of one instance per call
always:

| Declared / derived | What a call costs |
|---|---|
| not recursive | **one instance per function and run, reused**: a call allocates nothing |
| tail recursion | the call **reuses its own instance**: the parameters are overwritten and the function jumps; nothing grows |
| recursion to depth N | **one allocation** of N instances, taken from the run once |
| unbounded | one instance per call from the run; the quota ends it (`OutOfMemory`) |

A recursion that exceeds its declared depth is refused. Whether the recursion of a function that is
not a tail call can be **flattened** as well (for instance when what is combined is associative, so
that an accumulator replaces the pending work) is not decided; tail calls are the certain case.

Proposed spelling, a clause after the signature: `recursion none`, `recursion tail`,
`recursion 64`, `recursion unbounded`.

## 8. Generics

`<>` is the one form: `Option<Packet>`, `fn<uint16_t(Packet)>`. An instantiation is an object of its
own; the same arguments are the same object.

## 9. Layout and typing

The type comes first everywhere (`uint32_t sum = 0`, `def uint16_t checksum(Packet self)`); blocks are
formed by indentation.

## 10. Direction: workflows first

The owner's example: a run has to parse a million elements. The caller fills a block of 1000, the
blocks are worked off in parallel, and at the end the results are put together again. The sketch is
[`samples/parse_blocks.rx`](samples/parse_blocks.rx).

**A run per block is far too heavy**, and a run per element is absurd: a run is a cell with a budget
of its own, and it exists to be an independent, isolated line of execution (a connection, a task that
lives for a while). Parallel work on **data** does not need that. What it needs is **work that is
available and known somewhere, and carriers that take it**. The machine already works like this: a run
is data in a ring, and any carrier that is free reads it and goes on
([20](20-EXECUTION-POLICY.md)). The change is that the unit on the belt is a **light item** (a
function and its block), not a run.

The proposed form is a **map over a collection** that the system spreads over the free carriers:

- the collection is cut into blocks (`blocks(source, 1000)`);
- the carriers take the next block by **increasing one shared cursor** (one atomic add), so there is no
  queue to manage and nothing waits for a producer;
- every block has its own **result slot**, indexed by the block, so the results are in order without a
  lock and without a merge step in the middle;
- the function runs on the carrier's own run, and a function that cannot recurse keeps **one reused
  instance per run** (section 7), so a block costs no allocation for the call itself.

Whether a function may be spread this way is **derived** from the graph: it must not write an object
that it did not make itself (its effects say what it writes). That is the same fact the lock rules of
section 11 use.

**Runs stay** for what is really independent and long-lived, and for crossing their boundary
(`run.current`, `run.spawn`, section 5).

A second shape is a **pipeline** with a bounded belt between stages, for work that is produced over
time instead of lying in a collection (the ready ring is the machine's belt).

Open: what a failing block does to the others (cancel or finish), how a `map` takes its share of the
caller's quota, and whether `map` is a library function (proposed) or a construct such as
`parallel for`.

## 11. Open

1. **Locks, derived.** The owner wants locks derived automatically, by simple rules, without a large
   cost. Proposed rules:
   - an action locks **the objects it writes**, nothing else; reads do not lock (the id carries a
     generation, a read that meets a changed one is repeated);
   - **the parent comes in without a rule of its own**: adding, removing or replacing a child writes
     the parent's list of children, so the parent is in the write set; a plain write to a child does
     not write the parent, so the parent stays free;
   - an invariant that spans a parent and a child is kept by an action that **writes both**, so both are
     locked; there is no separate dependency declaration;
   - locks are taken as the action reaches them. If one would break the order by object id, it is only
     *tried*; when that fails, the action releases what it holds and starts again. Nothing waits in a
     cycle, so there is no deadlock.
   Open: what an **action** is. Proposed: a single store is atomic by itself, and a block
   (`with atomic:`) makes several stores one action.
2. **The syntax and the default of the recursion clause**, and whether non-tail recursion may be
   flattened.
3. **The name of the deep copy.**
4. Not yet discussed: operators and literals, control flow, error handling, enums and constants,
   strings and arrays, the intrinsics and the interpreter
   ([37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md)).
