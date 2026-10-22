# The seed compiler: an image that is `.rxfs` only

This is the successor to [18](18-NATIVE-SNIPPETS.md). Doc 18 describes the starter as it is today: a
fat binary of 321 native snippets whose bytes a host tool (`clang` via `tools/native/native_build.py`)
cuts out of C objects, plus 58 semantic functions composed into `Bytecode` programs that one
interpreter, `graph.run`, executes. That design keeps opaque machine code in the object world — a
native leaf is a `Code` of `Machine` bytes with nothing behind it but a hex blob, and a semantic
function is a `Bytecode` program that no person wrote.

This document fixes that. The object world holds **`.rxfs` source** and nothing else. There is one
native program in the whole image — a minimal **seed compiler** that turns `.rxfs` into machine code.
Everything above it (the stdlib, the compilers, the semantic functions) is `.rxfs`, and the seed
compiles it. There is no interpreter and no `Bytecode`; a program is run as the machine code the seed
made from its source, so what the image executes is always what its source says.

The rule this whole document exists to make true:

> Nothing is in the object world except `.rxfs` source. Everything native is the one seed that
> compiles it. There is no second, compiled copy of anything a person can read as source.

## The shape, in one table

| Layer | Today (doc 18) | Target |
|---|---|---|
| Numeric stdlib (286) | `native/basics.c`, `conversions.c` → `Machine`/`Wasm` leaves | `.rxfs` source, seed-compiled |
| Stdlib memory + runtime (30) | `stdlib_memory.c`, `stdlib_runtime.c` → leaves | `.rxfs` source (the few irreducible ops are seed intrinsics) |
| The compiler | `native/compile.c` (seed) **and** `native/tiny/*.rxfs` (self-hosted) — two of them | **one** seed (native) + `native/tiny` (`.rxfs`), and the seed is only a bootstrap |
| The front end | `native/front/*.rxfs` (`.rxfs`→words) + `rxfc.py` (host) | `native/front` (`.rxfs`), seed-compiled |
| The interpreter | `native/graph.c` — `graph.run`, runs `Bytecode` | **gone.** No interpretation; everything is compiled |
| Semantic functions (58) | graphs → `compose.py` → `Bytecode` program | `.rxfs` source + its graph, [two views of one body](#the-semantic-function-two-views-of-one-body) |
| The seed | *does not exist as a concept* — `compile.c` is "a compiler leaf" | **the one native program.** Compiles `.rxfs`→machine |
| Object world payload | `Machine`/`Wasm`/`Bytecode` blobs | `.rxfs` source text, and only that |

The 321 "native functions" stop being native. They become `.rxfs` functions the seed compiles, and the
only thing that is machine code and not source is the seed itself.

## The seed contract

The seed is one native program. Its whole job:

```
compile(.rxfs source, target, ABI) -> machine code for one function
```

- **Input.** `.rxfs` source text (the language of `tools/native/rxfc.py`: statements over 64-bit
  variables — `var`, `if`/`else`, `while`, `switch`, `call`, `return`, `refuse`, `load8..64`,
  `store8..64`, `put`, `copy`, `fill`, and the operators). The seed parses it; no host tool is in the
  path.
- **Output.** The machine code of one function with the status-out calling convention below, for one
  `RuntimeTarget`. No relocations — a function runs where it is placed ([18, Calling convention](18-NATIVE-SNIPPETS.md#calling-convention)).
- **Intrinsic lowering.** The seed lowers operators to **native instructions**, not to calls into a
  table of native leaves. This is the crux: because `x + y` becomes a real `ADD` and not a `CALL
  wrapping_add`, the arithmetic primitives can themselves be written in `.rxfs` (below).
- **Failure.** What it cannot compile it refuses with `UNSUPPORTED` and the word it stopped at,
  exactly as `compile.program` does today.

The seed is the trust base ("trusting trust"). It is small and it is the only place a reader has to
trust without a source in the object world. Everything it compiles is `.rxfs` a reader can audit.

## The intrinsic set

The intrinsics must reproduce the **exact** semantics of the leaves they replace — this is the whole
game, because the stdlib is rewritten to call them and the existing test suite (`test_native_wasm`,
`test_native_emulated`, `test_compile_cases`) must still pass unchanged. The naive "map `<<` to `SHL`"
is wrong: see the shift row.

| `.rxfs` | Exact semantics | Lowered to (x86_64 shown) |
|---|---|---|
| `a + b` | wrapping add, `uint64` (mod 2⁶⁴) | `mov t,a; add t,b` |
| `a - b` | wrapping subtract | `mov t,a; sub t,b` |
| `-a` | wrapping negate | `neg t` |
| `a * b` | wrapping multiply (see note) | `mov t,a; mul t,b` — or `imul`; overflow folded mod 2⁶⁴ |
| `a & b` / `a \| b` | bit and / or | `and` / `or` |
| `~a` | bit not | `not` |
| `a << n`, `a >> n` | shift; **`n ≥ 64` gives 0** (`basics.c:207`) | `mov t,n; cmp t,64; ... ; shl/shr` **guarded** — raw `shl` masks `n` to 63 and would be wrong |
| `a == b`, `a != b` | equal / not equal, `uint64` | `cmp; sete`/`setne; movzx` |
| `a < b`, `<=`, `>`, `>=` | **unsigned** compare | `cmp; setb`/`setbe`/`seta`/`setae; movzx` |
| `!a` | logical not (result 0 or 1) | `test t,t; setz` |
| `load8/16/32/64(addr)` | **zero-extending** load | `movzx`/`mov` |
| `store8/16/32/64(addr,v)` | store, low `n` bytes | `mov` |
| `put(p, v, n)` | `n` bytes at `*p`; `p` advances by `n` | `mov`/`rep movsb` |
| `copy(dst, src, n)` | memcpy; **define overlap** (today's `memory_copy` is `memmove`-like) | `rep movsb` (or `memmove` loop) |
| `fill(dst, byte, n)` | memset | `rep stosb` |
| `if`/`else`, `while`, `break`, `continue` | control flow | `jmp`/`jcc` |
| `switch` | dense or sparse | compare chain, or jump table |
| `return v` / `refuse s` | status-out return | write output, `mov eax,s; ret` |

**Multiply note.** Today `.rxfs` only folds `* K` for a compile-time power of two into a shift
(`rxfc.py` line 503) and otherwise calls the `wrapping_multiply_*` leaves. To make those leaves
`.rxfs`, the seed needs a general `a * b` intrinsic (wrapping). The wrapping / checked
(`checked_multiply_*`) and saturating (`saturating_*`) variants stay `.rxfs` on top of it: the
overflow test is a compare against `MAXIMUM`/`0`, expressible in `.rxfs`. `RXF_MUL_OVERFLOW`
(`basics.h:48`) — the wasm-only 32-bit-halves trick from doc 18 — is *inside the seed's `mul` now*,
so it disappears from the stdlib and the wasm32 case is just the seed's wasm backend lowering `mul`.

The point of listing these is that **the intrinsic set is a semantic contract, not a mnemonic map.**
The seed's backend is where the wrapping/zero-extension/shift-guard/unsigned-compare behaviour lives;
the stdlib above it is plain `.rxfs` that assumes exactly this.

## The ABI

Unchanged from [18](18-NATIVE-SNIPPETS.md#calling-convention), and reused verbatim: a function is
`status f(context, args, argc, out)`, status out in `uint32_t`, the result through an output pointer
that follows the arguments, declared by an `ABISignature` of `ABIValueLocation`s (arguments, optional
hidden runtime context, output, status). The seed emits code conforming to the `ABISignature` of the
target; a `.rxfs` function compiled by the seed is therefore indistinguishable at the boundary from a
snippet `native_build.py` cuts out of C today — the two are free to coexist during migration. On
wasm32 the same rules hold (memory 0, shadow-stack global 0, `i32` status), except the function table
exists only where an indirect call is genuinely needed.

## The semantic function: two views of one body

**Decided: option C.** A semantic function is one body seen two ways, kept in sync:

- **The graph** — values, operations, blocks, edges — is the object-world structure: what the tooling
  reads, what the semantic model is composed from, what survives an edit round-trip. It is what makes
  the function *integrated in the object world* rather than a dead blob.
- **The `.rxfs` source** is the same function as a program a person can read, and it is what the seed
  compiles to machine code.

The two agree by construction, exactly the way `rxfc.py` and `native/front` agree byte-for-byte today
([19](19-SELF-EDITING-PATH.md)): a builder that changes one regenerates or verifies the other, and a
test asserts the two describe the same function (compose the graph to `.rxfs`, compare to the stored
`.rxfs`, byte for byte — or run both and require identical results on the whole corpus). The graph is
the authority for *structure*; the `.rxfs` is the authority for *what runs*; nothing executes that a
reader cannot also see as `.rxfs`.

This is the most faithful option and the most work: the composer (`native/compose`, `.rxfs`) becomes a
graph→`.rxfs` writer instead of a graph→`Bytecode` writer, and there is a consistency check between the
two views. That cost buys the invariant that no function in the object world is ever only compiled
bytes — there is always a readable `.rxfs` body and a structural graph behind it.

## The stdlib, rewritten as `.rxfs`

The 321 leaves become `.rxfs` functions the seed compiles:

- **286 numeric** (`basics.c`, `conversions.c`): arithmetic, bitwise, compares, the option/saturating
  wrappers, the conversions. All are expressions over the intrinsics — `wrapping_add_uint64_t(a,b)` is
  `return a + b;`, `checked_add_uint32_t` is an add plus a compare, `zero_extend_uint32_t` is a load.
  These compile to a handful of instructions each, matching what `clang -O2` produced.
- **30 stdlib memory + runtime** (`stdlib_memory.c`, `stdlib_runtime.c`): the ones that are pure data
  movement (`memory_copy/compare/fill`) are the `copy`/`fill` intrinsics; the ones that need a real
  primitive the language cannot express (the hash, the arena bookkeeping that touches a runtime
  context) become seed intrinsics too.

**Decided: the muscle is intrinsics.** Hash, any lock-free compare-and-swap, and every other
irreducible primitive live in the seed's codegen as intrinsics. The seed is the **only** native code in
the image — there is no native leaf beside it. Making the seed slightly larger to hold the muscle is
the right trade: "seed only" stays literally true, and everything a person can read is `.rxfs`.

## Bootstrap order

The seed is the trust base; everything else is compiled from source. The order:

1. **The seed** exists as native code (the one thing not in the object world as source). For a host
   build it is `clang`-compiled once; for a bare machine it is the firmware's one trusted program.
2. **The seed compiles the `.rxfs` compilers.** `native/tiny` (the codegen, `.rxfs`) and `native/front`
   (the parser, `.rxfs`) become machine code. From here the image can compile `.rxfs` itself.
3. **The seed compiles the stdlib** — the 321 primitives, now `.rxfs` — into machine code.
4. **The seed compiles the semantic functions** (`.rxfs`) into machine code.
5. **The fixpoint.** `native/tiny` (the self-hosted compiler) compiles the whole set, including
   itself. The bytes it produces for a function must equal the bytes the seed produced for the same
   source. That equality *is* the trusting-trust proof: the self-hosted compiler and the seed agree,
   so the seed is not hiding anything. `tests/test_native.py` gains this as the fixpoint check.

Step 5 is the acceptance gate for the entire project: seed-compiled bytes == tiny-compiled bytes, for
every function, on every target. If they differ, the seed and the self-hosted compiler disagree and one
of them is wrong.

## Targets

Today `compile.program` compiles "on the machine it runs on", and `native_build.py` cross-compiles the
C with `clang` for x86_64, aarch64 and wasm32. The seed replaces the `clang` step.

**Decided: (a) cross-seed.** The seed carries a codegen backend per `RuntimeTarget` (x86_64, aarch64,
wasm32) and emits for all of them from one run; the fat binary is built by running the seed over the
`.rxfs` sources once per target. The same seed binary also runs self-targeted in-image for self-editing
([19](19-SELF-EDITING-PATH.md)). The code-generating core is shared; only the instruction selection per
target differs. This is what replaces `native_build.py` + `clang` as the way the image's bytes are made.

## The object world

`.rxfd` (the XML delta, [15](15-DELTAS-AND-XML.md)) and the image hold `.rxfs` **source text** for every
function — the stdlib, the compilers, the semantic functions. A `Code` whose `format` is the `.rxfs`
source, not `Machine`/`Wasm`/`Bytecode`. The compiled machine code is a **derived artifact**: produced
by the seed when the image is built or self-edited, never authored, never the thing the object world
claims a function *is*. The seed-compiled bytes can be cached in the image for speed (as a build
product with a `recipe_digest` pointing at the source digest), but the authority is always the
`.rxfs` source, and `sync.py --check` proves the cached bytes are what the source compiles to.

This is what "integration in the object world" means: the function is a readable `.rxfs` program that
is part of the world and can be inspected, edited and recompiled in place ([19](19-SELF-EDITING-PATH.md)),
not a dead machine-code blob with a hex body. For a semantic function this is the `.rxfs` view of
[the two-view body](#the-semantic-function-two-views-of-one-body) above; its graph is the other view.

## Migration and proof

Phased, each phase proven before the next (this is the `s1`–`s6` plan):

1. **s1 — the seed.** Build the seed compiler: `.rxfs`→machine, intrinsic lowering, status-out ABI,
   target(s). Prove it reproduces `compile.c`'s bytes on a real function.
2. **s2 — the stdlib in `.rxfs`.** Re-author the 321 primitives as `.rxfs`, seed-compiled. Prove
   behavioral equivalence: `test_native_wasm`, `test_native_emulated`, `test_native_stdlib` pass with
   the seed-compiled code where the C leaves were.
3. **s3 — no interpreter.** Compile semantic functions to machine directly; delete `graph.c`, the
   `Bytecode` format, `graph.run`. `test_graph` becomes "the compiled code runs the programs".
4. **s4 — the front end and composer in-image.** `native/front` and `native/compose` run as `.rxfs`
   the seed compiles; the image compiles `.rxfs` end to end.
5. **s5 — the fixpoint.** The seed is the only native thing; `native/tiny` compiles everything and its
   bytes equal the seed's. `test_native.py` asserts the fixpoint.
6. **s6 — the object world.** Rebuild the starter so every `Code` is `.rxfs` source (semantic
   functions as the graph + `.rxfs` two views of one body), regenerate digests, and prove **every
   seat** — `ruf binary check`, `sync.py --check`, the corpus and
   fixpoint tests, on host C, unix µPy, emcc, firmware BIOS+UEFI,    and the upywm wasmmod seat
   ([the seat map](../../REPO_LAYOUT.md)). One phase wired on every seat, never one seat at a time.

## Decisions (locked)

1. **Semantic-function representation — C.** The graph and the `.rxfs` are two synced views of one
   body: the graph is the object-world structure, the `.rxfs` is what the seed compiles. The composer
   writes `.rxfs` and a check keeps the two views identical. Nothing is ever only compiled bytes.
2. **The muscle — intrinsics.** Hash, compare-and-swap, and every other irreducible primitive are seed
   intrinsics. The seed is the **only** native code in the image; there is no second native leaf beside
   it. Size is not a reason to break "seed only".
3. **Targets — cross-seed (a).** One seed with a codegen backend per `RuntimeTarget` (x86_64, aarch64,
   wasm32), emitting all of them per run to build the fat binary; the same seed runs self-targeted
   in-image for self-editing ([19](19-SELF-EDITING-PATH.md)).
4. **The multiply / language — general wrapping `*`.** `.rxfs` `*` is a general wrapping multiply (a
   seed intrinsic), with a constant power of two still folded to a shift. This is the language.

These four are locked. This document is the contract; s1 (the seed compiler) starts from it, and when
the code and this disagree, this is what we fix first.
