# The native snippets: a multi-platform binary

The starter is a fat binary. Every native function is a small unit that is complete in itself: its machine code (or wasm body) for each target, with the ABI that says how to call it. Nothing links them; whatever composes them only calls. Once the set is complete, everything else in the image (semantic graphs) is composed from calls to it.

## Targets

A target is a `RuntimeTarget` object (architecture, ABI, environment, feature set).

| Target | Architecture | Environment | ABI | Code |
|---|---|---|---|---|
| `x86_64_linux_sysv` | x86_64 | Linux | System V, status out | machine |
| `x86_64_uefi_sysv` | x86_64 | UEFI | System V, status out | machine |
| `x86_64_bios_sysv` | x86_64 | BIOS | System V, status out | machine (Stage 2 runs it in long mode) |
| `aarch64_linux_aapcs64` | aarch64 | Linux | AAPCS64, status out | machine |
| `aarch64_uefi_aapcs64` | aarch64 | UEFI | AAPCS64, status out | machine |
| `wasm32_wasm_c` | wasm32 | wasm | wasm C, status out | wasm function body |

All 321 native functions (286 numeric, 30 stdlib foundation and runtime leaves, the interpreter `graph.run`, and the four of the live layer: `live.pump`, `uart.pump`, `compile.program`, `safe.point`) have a `Code` and an `ABISignature` for every target. `rxf binary check` proves it. The 58 semantic functions carry one more `Code` each, a program (see [Composed functions](#composed-functions)); `pymergetic.rxf.foundation.tiny.compile` is a 59th Function of that format whose program is the compiler of `native/tiny` (made by `rxfc.py`, not composed from a graph, [19](19-SELF-EDITING-PATH.md)).

The ABI of a target is the calling convention of the snippets, not of the firmware: on UEFI the snippets are System V and AAPCS64 like on Linux, and whatever enters the firmware (the PE/COFF entry, which is Microsoft x64 on x86_64) converts once at the border. A snippet never calls into firmware, so it never sees that convention.

## Calling convention

**Status out.** A native function returns a `uint32_t` status (`0` ok, otherwise a refusal code). Its result goes to an output pointer that follows the arguments. The `ABISignature` lists the `ABIValueLocation`s: arguments, an optional hidden runtime context, the output, and the status.

**wasm.** A wasm snippet is the body of one function (locals, expression, `end`) and stands alone under one convention:

- memory 0 is the linear memory, imported by whoever composes the snippets,
- global 0 is the shadow-stack pointer, a mutable i32,
- table 0 is the function table, imported too; only the interpreter uses it (`call_indirect`), the leaves do not,
- a snippet calls no function directly and has no data segment.

The function type follows from the ABI: an `Integer` of 8 bytes is `i64`, other integers and every pointer is `i32`, `Float` of 4 or 8 bytes is `f32` or `f64`, the status is the `i32` result. Locations of the wasm ABI sit in the register bank `Local`; their index is the parameter index. A struct passed by value (`Stack` on x86_64) is passed by address (`Pointer`). A 64-bit address that the C function carries in a `uint64_t` stays 64 bits wide.

## Where the bytes come from

`native/*.c` is the source of truth (formatted with `clang-format-18`, `.clang-format`), in C11, freestanding: `basics.c` (numeric), `conversions.c` (the 90 conversions), `stdlib_memory.c`, `stdlib_runtime.c`, `graph.c` (the interpreter). `tools/native/native_build.py` compiles each for x86_64 and aarch64 with clang 18.1.3 (`-O2 -ffreestanding -fno-pic ...`) and cuts the functions out of the object files (`extract_native.py` refuses anything with relocations). For wasm32 it compiles and links once with wasm-ld and cuts the bodies out of the module (`extract_wasm.py` refuses anything outside the convention above).

`tools/native/sync.py` writes what follows from that into the starter: the BIOS `Code` and `ABISignature` (the x86_64 bytes under the BIOS target) and the wasm32 ones. It reads the C function types of the wasm snippets and refuses a snippet whose parameters do not match its x86_64 ABI. `sync.py --check` says whether the starter is what the sources produce; the tests run it.

```
python tools/native/sync.py            # rewrite the derived objects
python tools/native/sync.py --check    # exit 1 when the starter differs
python tools/native/native_build.py OUTDIR    # the bytes per machine as JSON
```

Every `Code` names the `CompilerProvenance` of its machine (`clang_x86_64`, `clang_aarch64`, `clang_wasm32` in the module `targets`). Its `recipe_digest` is the SHA-256 of the recipe as canonical JSON (compiler, version, target triple, flags, linking), which `native_build.recipe()` returns, so the digest can be recomputed and the recipe read.

The x86_64 and aarch64 `Code` objects hold exactly what `native_build.py` makes of the C. `wasm32` was added with a source change: a 64-bit multiply that overflows calls `__multi3` on wasm32, which a snippet cannot do, so `basics.h` has `RXF_MUL_OVERFLOW`, which does it from 32-bit halves on wasm only. The other machines keep the compiler builtin and their bytes did not change.

## Composed functions

A semantic function is a graph of calls and structure. It is composed once into a **program**, and every target runs that program with its own copy of one interpreter, `rxf_graph_run` (`native/graph.c`, the native Function `pymergetic.rxf.foundation.graph.run`: 9.5 KB on x86_64, 8.1 KB on aarch64, 11.3 KB on wasm32). The program is target-independent data, so there is nothing to compose again per target; the leaves it calls are the native functions above.

- **Program.** A `Code` of format `Bytecode` on the function, named `bytecode`, without a `runtime_target`, with a reference of role `Interpreter` to `graph.run`. 58 programs, 82,456 bytes in all (392 to 3,152 bytes each). The words, the operand kinds, the 12 opcodes (`CALL`, `MOVE`, `MAP`, `FIELD`, `CONSTRUCT`, `LESS`, `INCREMENT`, `GOTO`, `BRANCH`, `SWITCH`, `RETURN`, `REFUSE`) and the ids of the 37 leaves are defined in `native/graph.h`, the one place; `tools/native/graph_header.py` reads them from there. Every operation that calls nothing (`ReturnValue`, `ReadTag`, `ConstructOption`, ...) is an instruction.
- **Values** live by value in an 8-byte-aligned frame of at most 1,024 bytes on the interpreter's stack. A block edge copies its arguments as one parallel copy, so a swap is two copies. An `Object` operand is the payload of the durable object, found in the object table of the runtime context.
- **Leaves** are found by id in the function table of the context (`rxf_native_slot`: id, generation, address). On wasm32 the address is the index in table 0 and the call is `call_indirect`; the type index of each `call_indirect` in the interpreter is rewritten to the *call type* of the leaf (the wasm type of the leaves in the order of their ids), and a host puts those types first in its type section. A leaf that is not in the table is refused as `UNBOUND`.
- **Refusals** of the interpreter itself: the program handle is not live (`UNBOUND`), stale (`STALE_GENERATION`) or not usable (`OUT_OF_RANGE`), the magic is wrong (`TYPE_MISMATCH`), the program is malformed or a slot, edge or operand is out of range (`OUT_OF_RANGE`), an object it names is not in the table (`UNBOUND`). A leaf's refusal ends the run with that status unless the graph branches on it.
- **Composer.** `tools/native/compose.py` reads the semantic graphs of the image (values, operations ordered by `index`, blocks, edges with their arguments, the status branch) and writes the program. `tools/native/graph_sync.py` makes the interpreter Function from the template of an existing native function and writes the `Code` of each graph; `sync.py` runs both, so `sync.py --check` also says whether the programs are what the graphs compose to. A graph whose call is not one of the 28 leaves is refused (`ComposeError`).
- **Functions that are a call.** A semantic function without a graph, whose body is a `Call` (`application.main`, `library.checkout_total`, `algorithms.compare/copy/fill`), is composed by `compose.Expression`: parameters get frame slots, a bound value (`Bound`: a data object of a module) is a `MOVE` from an `Object` operand, each `Call` is a `CALL` followed by a `BRANCH` on its status, and a callee that is itself such a function is inlined, so a program calls only leaves. A native callee has a result slot only if its ABI has an `Output` location (`memory.copy` and `fill` have none: the result of the call is its status). The leaves for them are 22 to 28: `checked_add/subtract/multiply_uint32_t`, `divide_uint32_t`, `memory_copy/compare/fill`.
- **BIOS and UEFI.** The interpreter is x86_64 machine code like every other snippet, so BIOS runs the Linux bytes and UEFI the same bytes under its target.

## The code the builder fills in

`foundation.compile:program`, `foundation.live:pump`, `foundation.graph:run`, `foundation.tiny:compile` and the 286 numeric leaves are derived data, and derived data is built, not stored (Wave 4b steps 2 and 3). The starter holds their source (`native/compile.rxfs`, `live.rxfs`, `graph.rxfs`, `tiny.rxfs`, and the one short `fn` per variant of `native/numeric/*.rxfs` and `native/float/*.rxfs`), the recipe and the pin -- the `byte_count`, and for a native body its `content_digest` in its `CodeBlob` -- and not the bytes. A program's recipe is its Seed `fn`; a numeric leaf's recipe is its `NumericContract` composed (`tools/native/numeric.py`: the operation, the policy, the width, the signedness and the source and destination types are all the recipe needs), so there is no second spec beside the contract to drift from. `tools/native/programs.py` is the one fill: `strip` takes the bytes out when `sync.py` writes the tree, `filled` fills a copy of a loaded image from the recipes (the Seed CLI over the sources, `rxfc` for the compiler as a program), and `fields_of` is the same fill for a reader. `link.py` fills as it links: the binary a seat runs is complete, the seed a delta carries is source, and the image a caller holds keeps its digest. `tests/test_program_sources.py` pins that the recipes build what the pin names; `tests/test_tiny.py` pins the same from the runtime side on every machine: the compiler as a program writes what the compiler of the image writes.

The two forms have two digests, and each is honest: the digest of the seed (source and pins) is the identity a feed and a voice sign, and the digest of the filled image is the state a running machine holds. A delta is checked against the image its receiver holds, so a delta of a live session is built on the filled image (`tests/snippets.py: running_image`), and a feed delta of the starter is built on the seed.

## The linked image

`tools/native/link.py` makes the image that runs: nothing is built when it is loaded. It is the memory itself, at a fixed base, in this order: the runtime context (144 bytes), the transaction journal, the function slots (24 bytes, one per leaf, the address of its machine code in the arena on x86_64 and aarch64, the table index on wasm32), the object table (80 bytes per durable object, **sorted by id**, so the runtime finds an entry by binary search), the arena (`Image.to_bytes()`, byte for byte), a heap and a scratch area. The id of an object is its `object_id` or the first 8 bytes of the SHA-256 of its path (`compose.object_id`); its value is the payload after the fixed prefix of a variable type. A program is found by the handle (entry id of the `bytecode` `Code`, generation 1, offset 0). Only the function slots differ between machines. `tests/test_image.py` runs functions of it, including `application.main` (570, status 0), on x86_64, aarch64 (emulator), wasm32 (node) and in the reference, and compares result, output and the digest of the whole image after the run (not the slots and the scratch).

## What is proven

- `rxf binary check STARTER` ([cli](../src/pymergetic/rxftk/binary/cli.py)): every native function has one `Code` per target in the right format, whose `byte_count` is the length of its bytes, whose digest and signature are the function's, which is tied to the `ABISignature` of that target and names a `CompilerProvenance`; `location_count` and `parameter_count` match the children; every function a semantic function calls is complete for every target. It also lists the operations that call nothing (`ReturnValue`, `ReadTag`, `ConstructOption`, the iterator operations, ...): those are structure that the composer makes, not a snippet.
- `tests/test_native.py`: the starter is exactly what the sources compile to -- the stored bytes and the pins of the built ones (a rebuild changes no byte; needs clang 18.1.3, `ld.lld-18`, `wasm-ld-18` for the C twins), the provenance digests are those of the recipes, the C is formatted.
- `tests/test_native_wasm.py`: the wasm32 and the x86_64 snippet of each numeric function return the same status and output on the same inputs (wasm in node, x86_64 as machine code in the test process).
- `tests/test_native_emulated.py`: the same for aarch64, with x86_64 and aarch64 both run in an emulator ([unicorn](https://www.unicorn-engine.org/)) under their own calling convention, so it does not depend on the host. UEFI and BIOS bytes are the Linux bytes.
- `tests/test_compose.py`: the programs in the starter are what the graphs compose to; the 37 leaf ids of `graph.h` are the `Leaf` references of `graph.run`, each a native function; a call that is no leaf is found.
- `tests/test_graph.py`: the interpreter on three machines against a second interpreter written in Python from the header (`tests/graph_reference.py`, calling the x86_64 leaves). The 58 programs run with three argument sets each, plus programs written with a small assembler (`tests/graph_programs.py`) that run every opcode, every shape of leaf (every leaf but the four that only `main` reaches return 0 at least once; `tests/test_image.py` runs `main`) and every refusal of the interpreter, plus a program that is called by a handle that does not reach it. Status, output and the whole memory are equal on x86_64 and aarch64 (emulator) and wasm32 (node). This found an uninitialized pointer in the interpreter.
- `rxf binary check` also covers every composed function: one `Code` of format `Bytecode` without a target, `byte_count` the length of the data in whole words, digest and signature the function's, and an `Interpreter` that is complete for every target.
- `tests/test_native_stdlib.py`: the 30 stdlib leaves, which take pointers, 64-bit addresses and 24-byte handles by value, run one script (`tests/scenario.py`: a transaction with allocate, write, append, publish, rollback, cleanup, borrows, pins, search, hash lookups, memory and text, refusals included) on x86_64, aarch64 and wasm32. After every call the status and the whole memory image are equal across the three machines. A changed byte in a snippet shows up as a difference.

## Leaves for words and code

Leaves 32 to 37 are numeric Functions that a program needs to take words apart and put them together: `wrapping_subtract`, `bit_and`, `bit_or`, `shift_left`, `shift_right` and `equal` on `uint64_t` (shape `QQO`; the shifts are `QWO`, a 64-bit value and a 32-bit count). A shift by 64 or more gives 0. Reading and writing memory at an address that a program computed needs no leaf of its own: the address of an object is a number (`wrapping_add` with 0 on an `Object` operand), and a slot of eight bytes given as a pointer to `memory_copy` or `memory_fill` is that address, so `memory_copy` is the indirect load and store (`tests/graph_programs.py`: `indirect`, `bits`; run by the reference, the interpreter and the compiled code on three machines).

## The compiler leaf

`pymergetic.rxf.foundation.compile.program` (leaf 31, `native/compile.c`) turns the program of a function into machine code on the machine it runs on: `status f(context, args, argc, out)`, with no relocation, so it runs where it is put. It compiles every instruction of the interpreter: `CALL` (leaves with scalar operands and leaves that take a 24-byte handle, which each ABI passes its own way: on the stack on x86_64, by pointer to a copy on aarch64 and wasm32), `MOVE`, `MAP`, `FIELD`, `CONSTRUCT`, `LESS`, `INCREMENT`, `GOTO`, `BRANCH`, `SWITCH`, `RETURN`, `REFUSE`, edges with their staged copies, and loops. Two passes (measure, then write) give the size that the caller needs, and the request carries a `work` region for the offset tables. What it cannot compile it refuses with `UNSUPPORTED` and the word it stopped at: a jump to a word that is not an instruction, a slot that is not aligned to the width of a value that is compared or counted, an odd width. `tests/test_compile_cases.py` runs the 174 argument sets of the 58 programs of the starter and the 28 synthetic programs (every opcode, every leaf shape, loops, switches, programs the interpreter refuses) twice from the same memory, interpreted and compiled, on x86_64 and aarch64 in an emulator and on wasm32 in node: status, value and the whole memory are equal. `main` and `checkout_total` read objects that only a linked image has; `tests/test_compile.py` compiles and runs them there, and compiles all 58 programs. The same code is made on every run. A test emulator has to flush its translation cache when code is written over code that ran (`tests/emulator.py`); the first version of this test passed only by accident because of that.

## Not covered

- Compiled code serves the channel and can be broken at every edge that goes back (leaf 38 `safe_point`, [19](19-SELF-EDITING-PATH.md)), one call per iteration; it holds the addresses of its moment, so when the pump changed the arena under it the code answers `STALE_GENERATION` and the program is compiled again. The interpreter reads its program again and simply goes on. The compiler of the earlier format (aarch64 only, 5,000 lines of Python) is in the git history and was not carried over.
- A BIOS-specific leaf would need its own source; today BIOS uses the Linux x86_64 bytes.
