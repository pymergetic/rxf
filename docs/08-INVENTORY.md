# Current implementation inventory

This document records what the RXF repository implements, and what it does not yet. It is not a dependency inventory for another runtime. The v5 Python stack (compiler, native Code binder, executable builders, old inspector) is not in the tree; its last state is the git tag `legacy-v5-python`.

## The core (RXFB 6)

`pymergetic.rxf.core` is the compiled package. Its C++ modules sit in subdirectories of `src/pymergetic/rxf/core/` (one namespace each, `pymergetic::rxf::core::<module>`), and each module with a Python face has a submodule of the same name, so the C++ structure *is* the Python structure. The extension is the `__init__` of the package; `core/*.pyi` are generated stubs (`python -m nanobind.stubgen -m pymergetic.rxf.core -r -O src/pymergetic/rxf`, checked by `tests/test_core_module.py`).

- `ground.h`: the C layout (arena header, cell header, reference, `Type`, `Field`), pinned for four targets. `core.arena` holds its constants.
- `kinds/`: the words of the format, once: `NodeKind`, `RefKind`, `Binding`, `OwnerKind`, `Mobility`, `Form`, `TypeFlags`, `FieldFlags` as name tables over the values of `ground.h`. The bootstrap, the XML, and the Python enums (`core.kinds`) are made from them.
- `arena/`: the freestanding arena (no exceptions, RTTI, or library; the object file needs only `memcpy` and `memset`): index directory, cell stream, block cells, dead cells, growth inside a reserved limit, verification.
- `object/`: the host `Image` (`core.object`): bootstrap (99 handles: the ground types, the enumerations of the format with their enumerators), paths, unique names (UTF-8 text), children index, compaction (canonical, copying, payload handles and roles rewritten), clone and swap.
- `hash/`: SHA-256 (`core.hash`).
- `meta/`: self-description: `Types`, payload codec, `referrers`, link `check`, ground check ([16](16-SELF-DESCRIPTION.md)); adds `check`, `fields`, `layout`, `compact`, `digest` to `Image`.
- `delta/`: operations, atomic path-addressed replay, canonical export, export of one module ([15](15-DELTAS-AND-XML.md)); adds `empty`, `apply`, `apply_all`, `export`, `delta_digest` to `Image`.
- `xml/`: reads and writes the delta text on [pugixml](https://pugixml.org) (vendored in `third_party/`); no Python face.
- `tree/`: an image as one file per module, printed and parsed on several threads (`core.tree`).
- `bindings.hpp`, `module.cpp`: the shared exceptions and the module entry; each module's own binding is `<module>/bindings.cpp`.

## Python (orchestration only)

- `source.py`: where an image comes from and goes to (tree, delta, bytes).
- `cli.py`: the one `rxf` entry point; the commands are in the `cli.py` next to what they operate on: `binary/cli.py`, `core/object/cli.py`, `core/delta/cli.py`, `server/cli.py`.
- `binary/`: `rxf binary check`, the starter as a multi-platform binary ([18](18-NATIVE-SNIPPETS.md)).
- `server/`: the read-only inspector (FastAPI): `inspector.py` (views), `app.py` (routes), `templates/inspector.html`.

## Data

- `starter/`: the default image, one XML file per module, laid out like Python modules (`__init__.rxfd` for a module with sub-modules): 58 files, 18,673 objects, 3.11 MB as an image. It is the source of truth. The enumerations of its data are in `pymergetic.rxf.kinds`.

## Native

- `native/*.c`: the C sources of the 317 native functions (the numeric and stdlib snippets and the graph interpreter); `tools/native/`: `native_build.py` (clang and wasm-ld), `extract_native.py`, `extract_wasm.py`, `sync.py` (writes the BIOS and wasm32 objects, the interpreter and the programs into the starter), `compose.py` (semantic graph to program), `graph_sync.py`, `graph_header.py`. See [18](18-NATIVE-SNIPPETS.md).

## Tests and tools

- `test_arena`, `test_ground_layout`, `test_arena_freestanding`, `test_core_module`: layout and arena.
- `test_delta`, `test_roles`, `test_kinds`, `test_tree`, `test_starter_delta`: deltas, roles, enumerations and aliases, the module tree, the starter.
- `test_journal`: the undo log of the arena: random edits of an image, rolled back, give the same bytes; a full log refuses; a refused delta changes nothing.
- `test_hardening`, `test_fuzz`: hostile input; `tools/sanitize.py` runs the core tests under AddressSanitizer and UBSan.
- `test_cli`, `test_inspector`: the commands and the inspector.
- `test_binary`, `test_native`: the binary check and the starter against the C sources and their recipes.
- `test_compose`, `test_graph`: the programs of the 58 semantic functions are what the graphs and calls compose to, and the interpreter runs them (and hand-written ones) identically on x86_64, aarch64 and wasm32 and as a Python reference.
- `test_image`: the starter linked as one memory image (`link.py`): functions of it, `application.main` included (570), run identically on the three machines and in the reference.
- `test_native_wasm`, `test_native_emulated`, `test_native_stdlib`: the same inputs on x86_64, aarch64 and wasm32 (emulator and node), the 286 numeric snippets and the 30 stdlib leaves with a full memory comparison. They need `unicorn` (the `test` extra) and node.

- `test_rxfc`, `test_tiny`: the language `.rxfs` (`tools/native/rxfc.py`) and the compiler written in it (`native/tiny`): the same code as leaf 31 on three machines, on the whole corpus (`test_tiny_corpus`), and the fixpoint (interpreter, machine code, machine code again) with 3.14 and 3.12 alike.
- `test_live*`, `test_safe_points`, `test_self_edit`, `test_compile`: the live layer ([19](19-SELF-EDITING-PATH.md)): frames, reads, delta, journal, compaction, safe points and break (also in compiled loops), UART (eight devices) and shared-memory links, the first self-edit, and the compiled code of leaf 31, each on x86_64 and aarch64 (emulator) and wasm32 (node) where it touches native code.

## Not implemented

From what was planned, built since: semantic-graph checks and passes (`tools/native/rxfgraph.py`, `graphpass.py`, `tests/test_rxfgraph.py`), the compiler as a semantic graph (composed to programs, byte-equal to `rxfc.py` on the corpus), executable packaging (ELF, PE/COFF, BIOS, UEFI; `src/pymergetic/rxf/binary/`, `tools/make_bootable.py`, QEMU boots in `tests/test_boot_qemu.py`), and certification (Ed25519 over statements, M-of-N quorum, activation gates the build: `src/pymergetic/rxf/certify/`, `tests/test_certify*.py`, `tests/test_binary_activation.py`).

What remains out: resource limits of the trust policy (the statement has a slot, enforcement is not written), and measured boot in the firmware itself (the UEFI path verifies the signature of the embedded ELF and nothing measures the firmware).

## Explicit non-requirements

RXF has no required Metal runtime, MicroPython runtime, browser runtime, Wasm engine, source-language compiler, VM, or ambient syscall provider. Integrations can satisfy explicit capability contracts without becoming part of the RXF format.
