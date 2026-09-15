# Templates, traits, and static dispatch

RXF models generics and traits as ordinary typed DATA objects. The first coherent stage is intentionally static: it records generic parameters, concrete TYPE arguments, templates, traits, requirements, associated types, conformances, implementation bindings, and deterministic specializations. Dynamic witness tables and runtime type erasure are deferred.

## Canonical objects

TYPE IDs `49..57` are `GenericParameter`, `GenericArgument`, `Template`, `Trait`, `TraitRequirement`, `AssociatedType`, `Conformance`, `ImplementationBinding`, and `Specialization`. Their payload IDs and references are uint64; enum, count, and flag words are uint32. Every payload relationship is mirrored by a typed `RefDef` role. The types live under `pymergetic.rxf.model.generics` and `pymergetic.rxf.model.traits`; public conformances and specializations live under `pymergetic.rxf.traits` and `pymergetic.rxf.templates`.

A generic parameter list is contiguous by index. A specialization supplies exactly one argument for every parameter, in parameter order. The canonical SHA-256 input is the little-endian tuple `(template_id:uint64, template_version:uint32, binding_count:uint32)` followed by ordered `(parameter_id:uint64, bound_type:uint64)` pairs. The specialization ID is the low 60 digest bits under reserved high nibble `0xF`; an occupied ID with different bytes is a collision and is refused.

## Traits and static dispatch

A Trait owns requirement and associated-type declarations. A requirement points to an `ABSTRACT` Function: it carries an honest callable signature but is not itself a call target. A Conformance is unique for `(trait, concrete TYPE)`, covers every requirement and associated type exactly once, and binds each function requirement to a concrete Function with the same signature.

Static resolution is:

```text
(trait, concrete TYPE) -> unique Conformance
(Conformance, TraitRequirement) -> concrete Function
Call -> concrete Function
```

A Call never targets an abstract requirement declaration, a Template, a Conformance, an ImplementationBinding, or Code. Operator syntax such as `a == b` is only front-end sugar for resolving `Equal.equal` and emitting a Call to the selected concrete Function.

## Composition, not inheritance

Struct/class-like composition uses fields whose value types are other TYPE objects, plus Trait conformances for behavior. This stage adds no class inheritance, subtype parent chain, implicit method lookup, or object-layout inheritance. Reuse is explicit composition, generic specialization, and static trait binding.

## Current corpus and deferred work

The minimal `Equal`/`Ordered` and deterministic-specialization proof is retained, while the current corpus now covers every fixed-width integer primitive plus `float` and `double` as described in the following Numeric section. Only the full N×N conversion matrix and advanced dynamic generic features remain deferred.

## Numeric and conversion corpus

The numeric corpus defines `Equal`, `Ordered`, `CheckedAdd`, `WrappingAdd`, `SaturatingAdd`, `CheckedSubtract`, `CheckedMultiply`, `Divide`, `Remainder`, `Bitwise`, `Shift`, and `Negate`. `Ordered` explicitly extends `Equal`; conformances flatten inherited requirement and associated-`Self` coverage, which the checker validates exactly.

Integer defaults are checked. Wrapping and saturating behavior require their explicitly named traits. Division and remainder refuse division by zero and signed `INT_MIN / -1`; shifts refuse counts greater than or equal to the operand width. Signed negate and absolute refuse the minimum value when it cannot be represented. Floating operations are finite contracts: non-finite input or result refuses. Float/double include equality, ordering, checked finite arithmetic, negate, absolute, minimum, and maximum.

Concrete primitive Functions are ordinary typed `CODE_BACKED` Functions. Their operation and arithmetic policy are persisted in NumericContract objects and implemented by target-specific `NATIVE` Code extracted from the authored C corpus.

Representative conversions cover integer widening, checked narrowing, checked signed/unsigned crossings, float-to-double, checked double-to-float, and checked integer/float crossings. The complete source/destination conversion matrix remains deferred.

## Function lifecycle

`ABSTRACT` is a trait/template requirement declaration and is non-callable. `DECLARED` is a concrete contract without implementation and is non-callable. `COMPOSED` requires a valid Call body. `CODE_BACKED` requires Code. `IMPORTED` is callable only with one bound Import capability. `INTRINSIC` is reserved for explicitly modeled control semantics and requires a non-`NONE` `FunctionIntrinsic`. Calls and static requirement resolution accept only actually callable Functions.
