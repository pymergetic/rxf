"""Whole-graph static specialization proofs."""

from copy import deepcopy

import pytest

from pymergetic.rxf.expand import counter_template
from pymergetic.rxf.model.specialization import specialize_static
from pymergetic.rxf.model.templates_traits import (
    PAIR_GENERIC_FIRST_ID,
    PAIR_GENERIC_SECOND_ID,
    PAIR_TEMPLATE_ID,
)
from pymergetic.rxf.ty.builtins import U16_TYPE, U32_TYPE


def test_static_specialization_clones_graph_and_reuses_identity() -> None:
    container = counter_template(include_lowered=False).build()
    prior = next(node for node in container.nodes if node.type_id == 57)
    container.nodes = [
        node
        for node in container.nodes
        if node.id != prior.id and node.parent != prior.id
    ]
    before = len(container.nodes)
    bindings = {PAIR_GENERIC_FIRST_ID: U32_TYPE, PAIR_GENERIC_SECOND_ID: U16_TYPE}
    created = specialize_static(container, PAIR_TEMPLATE_ID, bindings)
    assert not created.reused and len(container.nodes) > before
    clones = [
        node
        for node in container.nodes
        if node.attrs.get("specialization_provenance") == str(created.specialization_id)
    ]
    assert clones
    assert all(ref.target not in bindings for node in clones for ref in node.refs)
    again = specialize_static(
        container, PAIR_TEMPLATE_ID, dict(reversed(tuple(bindings.items())))
    )
    assert again.reused and again.specialization_id == created.specialization_id
    assert again.function_id == created.function_id
    assert again.created_ids == ()


def test_static_specialization_is_atomic_on_open_or_invalid_binding() -> None:
    container = counter_template(include_lowered=False).build()
    before = deepcopy(container.to_dict())
    with pytest.raises(ValueError, match="exactly cover"):
        specialize_static(
            container, PAIR_TEMPLATE_ID, {PAIR_GENERIC_FIRST_ID: U32_TYPE}
        )
    assert container.to_dict() == before
    with pytest.raises(ValueError, match="bound TYPE"):
        specialize_static(
            container,
            PAIR_TEMPLATE_ID,
            {PAIR_GENERIC_FIRST_ID: U32_TYPE, PAIR_GENERIC_SECOND_ID: 0xFFFF},
        )
    assert container.to_dict() == before


def test_static_specialization_rejects_unsatisfied_trait_bound() -> None:
    container = counter_template(include_lowered=False).build()
    with pytest.raises(ValueError, match="unsatisfied bound"):
        specialize_static(
            container,
            PAIR_TEMPLATE_ID,
            {PAIR_GENERIC_FIRST_ID: U32_TYPE, PAIR_GENERIC_SECOND_ID: U16_TYPE},
            bounds={PAIR_GENERIC_FIRST_ID: (0xFFFF,)},
        )
