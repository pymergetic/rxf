# RXF v5 native basics

RXF v5 persists ordered `Result`, `RefusalSet`/`RefusalVariant`, `NumericContract`, and target-specific `ABISignature` objects. Checked leaves uniformly return a `uint32_t` status and write through the transient final output pointer only on status zero. Codes 1–6 are overflow, division by zero, invalid shift count, non-finite, inexact, and out-of-range.

The Basic corpus is authored in `native/basics.c`/`.h`, compiled by pinned Clang 18 recipes, and extracted from ELF64 ET_REL objects by `tools/extract_native.py`. Generated manifests contain bytes and provenance only. No interpreter, VM, PXF1, bytecode, opcode dispatcher, browser, Wasm, or loader is part of this layer.

Signed integer division truncates toward zero; remainder has the dividend's sign. Checked shifts refuse counts at least the width and signed right shift is arithmetic. Float contracts accept finite values/results only, refuse NaN/infinity, and preserve signed zero. Directed conversions are exact and range checked; binary narrowing follows round-to-nearest-even and refuses an inexact result.

The starter computes `125 * 4 - 50 + 25`, multiplies by 20, divides by 100, and adds tax for a total of 570. It includes the full corpus and native Code for x86-64 SysV and AArch64 AAPCS64. Loading and browser/Wasm execution remain deliberately deferred.

## Composed compiler and movable binding slots

Stages 1–3 add a typed compiler pipeline in `pymergetic.rxf.compiler`. It normalizes a composed Function from its terminal Call through `ValueKind.RESULT` use-def edges, rejects cycles and signature/type disagreement, and emits status-explicit typed IR. Calls write private stack temporaries, test the `uint32_t` status after every leaf, converge on one refusal return, and copy the final value to caller output only after total success.

The generated composed ABI is `uint32_t (RuntimeContext *, semantic arguments..., result *)`; existing native leaves retain `uint32_t (semantic arguments..., result *)` and call lowering performs the adaptation. Function calls resolve indirectly through sorted Function slots. Code, heap Object, and imported Capability slots are copy-on-write snapshots keyed by durable `uint64` IDs, with publication version and per-slot generation. Addresses and payload objects are transient and never persisted; object payloads are resolved again at each call.

The target artifact includes source IDs, semantic digest, ABI frame slots, calls, objects, fixups, and exact text bytes. x86-64 SysV output uses deterministic stack-only temporaries and W^X publication. AArch64 AAPCS64 output is exact instruction bytes with typed slot and branch fixup metadata. `rxf compile <path> <function-id>` exposes the compiled inspector view. This compiler currently accepts the scalar, eager, single-result composed subset used by starter `main` and `checkout_total`; lazy control flow and floating composed calls are refused rather than interpreted.
