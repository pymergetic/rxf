# 38 - The one language, draft 0

Status: **draft for the owner to cut**. Companion of
[37](37-THE-GRAPH-AND-ITS-ONE-LANGUAGE.md). Nothing here is implemented. Items marked
**DECIDE** need the owner; each has a recommendation.

The language is the text form of the object graph (37, decision 2): one vocabulary, written once.
This note fixes that vocabulary, the text form, and the intrinsic set the first interpreter has to
implement.

## 1. What the graph already has

The starter carries a graph vocabulary (`SemanticGraph`, `SemanticBlock`, `SemanticOperation`,
`SemanticValue`, `kinds.Terminator`, `kinds.Effects`). Measured on 2026-11-02:

- `Terminator`: `Branch`, `Goto`, `Loop`, `Refuse`, `Return`, `SwitchTag`. **Kept.**
- `Effects`: `Allocate`, `Host`, `Read`, `Time`, `Write`. **Kept.**
- `ValueKind`: `Bound`, `Literal`, `Object`. **Kept.**
- `Opcode`: about 35 operations at the level of the standard library (`Allocate`, `AppendBytes`,
  `Compare`, `Hash`, `FormatInteger`, `Utf8Validate`, `IterAdvance`, ...). **Not kept as the base
  set**: they are programs, not CPU operations. They are rewritten as composed functions over the
  intrinsics of section 4.
- Typed refusals (`Overflow`, `DivisionByZero`, `OutOfRange`, `ShiftCount`, ...). **Kept** as the
  one error model (see 3.3).

## 2. Values and types

- Integers of fixed width: `u8 u16 u32 u64` and the signed `i8 i16 i32 i64`. Arithmetic wraps,
  unless a refusing form is asked for.
- `handle`: a reference to an object. The only reference a program can hold.
- `addr`: a raw machine address. It exists only inside an `unsafe` block and only the intrinsics
  take it.
- Aggregates are `struct` types with C layout (offset, size and alignment are fields of the Type
  object), fixed-size arrays, and `enum` (a named set of integer values).
- **DECIDE 1: floating point.** Recommendation: not in draft 0; the numeric floor is integers.
- **DECIDE 2: `bool`.** Recommendation: no separate type; a comparison yields `u8` 0 or 1.

## 3. Text form

### 3.1 Shape

- Statements and expressions in the C family; **blocks by braces**, one statement per line, so the
  canonical printer is trivial and diff-friendly.
- **Names are paths**: `a.b.c` is the node `c` under `b` under `a`. A module is a node; `import` does
  not exist, a name either resolves in the tree or is refused.
- A type with functions under it is written as one block (`type T { fields...; fn name(...) {...} }`);
  that is the same tree shape the image has.
- **DECIDE 3: top-level execution.** In the HolyC manner a top-level statement runs when read, so the
  language is its own shell. Recommendation: yes, in the REPL only; in a module file a top-level
  statement is a refusal.
- Canonical form: one way to print every graph, layout included. A text that does not equal the
  canonical print of its own graph is accepted but never stored as written.

### 3.2 Statements

`let`, assignment, `if`/`else`, `while`, `switch` on an integer or an enum, `break`, `continue`,
`return`, `refuse <code>`, call. These map one to one onto blocks and the terminators of section 1
(`Branch`, `Goto`, `Loop`, `SwitchTag`, `Return`, `Refuse`).

### 3.3 Errors

A function returns a status and a value (the status-out ABI of 29 and 18). `refuse <code>` ends the
function with that status. There are no exceptions. A caller writes `try f(x)` to propagate a refusal
or handles the status in a `switch`.

### 3.4 Effects

A function declares its effects (`allocate`, `host`, `read`, `write`, `time`); the declaration is
checked against the body. A pure function has none.

## 4. The intrinsics

The set the interpreter implements first, and a backend implements later. They mirror the table in
[29](29-THE-SEED-COMPILER.md) and add the handful that touch objects.

| Group | Operations |
|---|---|
| Arithmetic | `add sub neg mul` (wrapping), `divu remu divs rems` (refusing on zero) |
| Bits | `and or xor not`, `shl shr sar` (a count of 64 or more yields 0, or sign for `sar`) |
| Compare | `eq ne ltu leu gtu geu lts les gts ges` |
| Width | `zext sext trunc` |
| Memory (unsafe) | `load8 load16 load32 load64`, `store8 store16 store32 store64`, `copy`, `fill` |
| Objects | `resolve` (path to handle), `get`/`set` a field of a handle, `allocate`, `release`, `pin`, `unpin` |
| Calls | `call` a function handle, `tailcall` |
| Platform | `out` (one byte to the character output), `fence`, `clock` |

That is 50 or so operations by name and about 20 by meaning, since the sized variants are one
operation with a width.

**DECIDE 4: signed arithmetic.** Recommendation: keep `divs rems lts les gts ges sar` and the
sign-extending conversions as intrinsics (they are not expressible without branches), everything
else signed is composed.

## 5. What the first interpreter and reader must do

- **Reader** (Python over the C library): text to objects in the arena, and the canonical print back.
- **Interpreter** (C, in the library): executes the graph objects directly, one case per intrinsic.
- The test of the pair is the round trip (text, objects, text, byte-equal) and a small program that
  runs (a loop, a recursion, an object field read and written).

## 6. Open

- The grammar in full (a page of EBNF once decisions 1 to 4 are made).
- The module system's visibility rules (all names public in draft 0).
- Integer literal forms and string literals (strings are `u8` arrays in draft 0).
- Concurrency (the run-cell machine of [20](20-EXECUTION-POLICY.md)) is not part of draft 0.
