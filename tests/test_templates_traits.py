"""Templates, supertraits, numeric contracts, and callable lifecycle proofs."""

from copy import deepcopy

from pymergetic.rxf.checker import check
from pymergetic.rxf.expand import COUNTER, counter_template
from pymergetic.rxf.model.execution import FunctionImplementation
from pymergetic.rxf.model.generics import (
    GenericArgument,
    GenericParameter,
    Specialization,
    Template,
    specialization_digest,
    specialization_id,
    specialize_function,
)
from pymergetic.rxf.model.numeric import (
    FLOAT_TYPES,
    INTEGER_TYPES,
    TRAIT_OPERATIONS,
)
from pymergetic.rxf.model.templates_traits import (
    PAIR_GENERIC_FIRST_ID,
    PAIR_GENERIC_SECOND_ID,
    PAIR_TEMPLATE_ID,
)
from pymergetic.rxf.model.traits import (
    AssociatedType,
    Conformance,
    ImplementationBinding,
    Trait,
    TraitRequirement,
    resolve_conformance,
    resolve_requirement,
)
from pymergetic.rxf.ops.lowering import derived_id
from pymergetic.rxf.ty.builtins import (
    ASSOCIATED_TYPE_TYPE,
    CONFORMANCE_TYPE,
    GENERIC_ARGUMENT_TYPE,
    GENERIC_PARAMETER_TYPE,
    IMPLEMENTATION_BINDING_TYPE,
    SPECIALIZATION_TYPE,
    TEMPLATE_TYPE,
    TRAIT_REQUIREMENT_TYPE,
    TRAIT_TYPE,
    U16_TYPE,
    U32_TYPE,
)

DECODERS = {
    GENERIC_PARAMETER_TYPE: GenericParameter.from_node,
    GENERIC_ARGUMENT_TYPE: GenericArgument.from_node,
    TEMPLATE_TYPE: Template.from_node,
    TRAIT_TYPE: Trait.from_node,
    TRAIT_REQUIREMENT_TYPE: TraitRequirement.from_node,
    ASSOCIATED_TYPE_TYPE: AssociatedType.from_node,
    CONFORMANCE_TYPE: Conformance.from_node,
    IMPLEMENTATION_BINDING_TYPE: ImplementationBinding.from_node,
    SPECIALIZATION_TYPE: Specialization.from_node,
}


def test_all_new_payloads_roundtrip_and_are_self_described() -> None:
    container = COUNTER.build()
    assert check(container) == []
    for node in container.nodes:
        decoder = DECODERS.get(node.type_id)
        if decoder is not None:
            assert decoder(node).to_node().data == node.data
    assert set(DECODERS) <= {
        node.id for node in container.nodes if node.kind.name == "TYPE"
    }


def test_specialization_is_canonical_for_reversed_binding_input() -> None:
    parameter_ids = (PAIR_GENERIC_FIRST_ID, PAIR_GENERIC_SECOND_ID)
    forward = ((PAIR_GENERIC_FIRST_ID, U32_TYPE), (PAIR_GENERIC_SECOND_ID, U16_TYPE))
    reverse = tuple(reversed(forward))
    first = specialization_digest(PAIR_TEMPLATE_ID, 1, forward, parameter_ids)
    second = specialization_digest(PAIR_TEMPLATE_ID, 1, reverse, parameter_ids)
    assert first == second
    assert specialization_id(first) >> 60 == 0xF

    base = counter_template(include_lowered=False).build()
    existing = next(node for node in base.nodes if node.type_id == SPECIALIZATION_TYPE)
    base.nodes.remove(existing)
    base.nodes[:] = [node for node in base.nodes if node.parent != existing.id]
    created_a = specialize_function(
        base,
        PAIR_TEMPLATE_ID,
        dict(forward),
        function_id=9000,
        signature_id=751,
        name="forward",
    )
    created_b = specialize_function(
        base,
        PAIR_TEMPLATE_ID,
        dict(reverse),
        function_id=9001,
        signature_id=751,
        name="reverse",
    )
    assert created_a[0].id == created_b[0].id
    assert (
        Specialization.from_node(created_a[0]).digest
        == Specialization.from_node(created_b[0]).digest
    )
    assert [
        GenericArgument.from_node(node).parameter_id for node in created_a[2]
    ] == list(parameter_ids)
    assert [
        GenericArgument.from_node(node).parameter_id for node in created_b[2]
    ] == list(parameter_ids)


def test_lifecycle_and_call_target_rules() -> None:
    broken = counter_template(include_lowered=False).build()
    function = next(
        node for node in broken.nodes if node.name == "checked_add_uint32_t"
    )
    payload = bytearray(function.data)
    payload[:4] = int(FunctionImplementation.COMPOSED).to_bytes(4, "little")
    function.data = bytes(payload)
    assert any("requires a Call body" in error for error in check(broken))

    declared = next(
        node for node in broken.nodes if node.name == "less_than_u32_contract"
    )
    call = next(node for node in broken.nodes if node.type_id == 17)
    call.refs[0].target = declared.id
    assert any("actually callable" in error for error in check(broken))


def test_supertrait_and_inherited_coverage() -> None:
    container = counter_template(include_lowered=False).build()
    ordered = next(
        Trait.from_node(node)
        for node in container.nodes
        if node.type_id == TRAIT_TYPE and node.name == "Ordered"
    )
    equal = next(
        Trait.from_node(node)
        for node in container.nodes
        if node.type_id == TRAIT_TYPE and node.name == "Equal"
    )
    assert ordered.supertrait_ids == (equal.id,)
    conformance = resolve_conformance(container, ordered.id, U32_TYPE)
    inherited_requirement = equal.requirement_ids[0]
    assert (
        resolve_requirement(container, conformance.id, inherited_requirement).name
        == "equal_uint32_t"
    )

    cyclic = deepcopy(container)
    equal_node = cyclic.node_by_id(equal.id)
    assert equal_node is not None
    record = Trait.from_node(equal_node)
    replacement = Trait(
        record.id,
        record.name,
        record.parent,
        record.requirement_ids,
        record.associated_type_ids,
        (ordered.id,),
        record.flags,
    ).to_node()
    equal_node.data, equal_node.refs = replacement.data, replacement.refs
    assert any("supertrait cycle" in error for error in check(cyclic))


def test_numeric_corpus_exact_function_and_conformance_coverage() -> None:
    container = counter_template(include_lowered=False).build()
    function_names = {node.name for node in container.nodes if node.type_id == 14}
    for type_name in INTEGER_TYPES.values():
        for opcodes in TRAIT_OPERATIONS.values():
            for opcode in opcodes:
                if opcode in {
                    "checked_negate",
                    "checked_absolute",
                } and type_name.startswith("uint"):
                    continue
                assert f"{opcode}_{type_name}" in function_names
    for type_name in FLOAT_TYPES.values():
        for name in (
            "equal",
            "less",
            "checked_add",
            "checked_subtract",
            "checked_multiply",
            "divide",
            "checked_negate",
            "checked_absolute",
            "minimum",
            "maximum",
        ):
            assert f"{name}_{type_name}" in function_names
    assert all(
        node.name.endswith(tuple(INTEGER_TYPES.values()) + tuple(FLOAT_TYPES.values()))
        or node.name.startswith("convert_")
        for node in container.nodes
        if node.type_id == 14 and node.parent == 74
    )


def test_control_derived_ids_are_deterministic_uint64_namespace() -> None:
    assert derived_id(123, "test") == derived_id(123, "test")
    assert derived_id(123, "test") >> 60 == 0xE
    assert derived_id(123, "test") != derived_id(123, "body")
