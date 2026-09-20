# Development roadmap

The current RXF foundation proves the format, typed object world, native target binding, executable packaging, and recovery model. The remaining roadmap is organized around independent, measurable capabilities.

## 1. Agent mutation protocol

Expose bounded object neighborhoods, typed mutation proposals, expected generations, validation results, and structured refusals. Prove that every accepted mutation produces a deterministic successor and every rejected mutation leaves the source unchanged.

## 2. Incremental compilation and linking

Compute affected semantic and native closures from changed object IDs. Recompile and relink only the required Functions, Code, tables, and fixups while preserving unaffected IDs and provenance.

## 3. Runtime and standard library

Complete core values, memory operations, structures, collections, text, algorithms, serialization, control flow, refusal composition, heap services, and explicit capability calls. Every durable facility remains represented by typed RXF objects.

## 4. Debugging and observability

Add source-like projections, object-aware stack and call traces, profiling, mutation history, executable-layout correlation, and deterministic failure capsules without introducing a second semantic authority.

## 5. Security and trust

Define capability policy, signature and trust metadata, resource bounds, verifier profiles, reproducible certification, and adversarial mutation tests. Provider state and addresses remain transient.

## 6. Portability

Expand native architectures, ABIs, and executable envelopes through explicit `RuntimeTarget` and `Code` objects. External environments implement capability providers; no provider is privileged by the format.

## 7. Evaluation

Compare model performance on identical tasks through conventional source repositories and RXF object mutation. Measure tokens, latency, failed attempts, repair loops, changed closure size, regressions, cross-target correctness, and retained understanding across sessions.

A roadmap item is complete only when its invariants are tested, its output is inspectable, and its result can be recovered from the produced RXF or executable artifact.
