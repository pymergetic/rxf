# Capabilities and toolchain stages 9–15

RXF v5 persists typed `CapabilityRequirement` object IDs, semantic versions, rights, effects, target/environment constraints, optional policy bytes, refusal-set links, and semantic digests. Provider manifests and provider addresses are transient authoring/preflight input and are never serialized. The canonical corpus declares console logging; file open/read/write/stat/list/close; monotonic and wall clocks and timers; random fill; network resolve/connect/listen/send/receive/close; and environment/platform inspection. No provider implementation or syscall is hidden in RXF.

`BootPlan` is deterministic planning data only. It selects Code, Imports, capability requirements, and relocations; it does not map pages or transfer control. `prune_for_target` creates a nonmutating target-specific Container retaining bootstrap TYPE/FIELD objects and the transitive parent/type/reference closure.

Static specialization clones the complete Function-owned graph, substitutes generic types through signatures, parameters, results, values, arguments, calls, nested templates and trait/conformance bindings, and records canonical SHA-256 specialization provenance. Repeated identical requests reuse the existing specialization; incomplete bindings, missing types, collisions, open generic references, and unsatisfied trait bounds refuse atomically.

The inspector exposes capability inventory, BootPlan, reachability reasons, typed reflection, migration planning, specialization provenance through object details, and canonical JSON downloads. These are tooling boundaries, not runtime serialization APIs.

`tools/generate_all.py` stages deterministic generators and writes `generated/provenance.json` with source, generator, artifact, compiler, target, and recipe data. `--check` fails on stale output and restores the working tree snapshot it inspected. `rxf certify-lane` treats missing Clang 18, Node, or QEMU AArch64 tooling as certification failures.

External fills remain explicit work outside this package: host/firmware/browser capability providers and platform boot page mapping/control transfer. RXF includes no VM, interpreter, bytecode engine, Wasm/browser runtime, fake provider, or hidden syscall path.
