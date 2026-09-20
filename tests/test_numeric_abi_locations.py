from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.model.contracts import (
    ABIRegisterBank,
    ABIRole,
    ABIValueLocation,
    ContractRole,
    verify_abi_locations,
)
from pymergetic.rxf.ty.builtins import (
    ABI_SIGNATURE_TYPE,
    FUNCTION_TYPE,
    NUMERIC_MODULE_ID,
)


def _records(container, name):
    function = next(
        node
        for node in container.nodes
        if node.type_id == FUNCTION_TYPE and node.name == name
    )
    return [
        node
        for node in container.nodes
        if node.type_id == ABI_SIGNATURE_TYPE and node.parent == function.id
    ]


def test_all_numeric_native_abis_have_strict_locations():
    container = starter_template().build()
    functions = {
        node.id
        for node in container.nodes
        if node.type_id == FUNCTION_TYPE and node.parent == NUMERIC_MODULE_ID
    }
    records = [
        node
        for node in container.nodes
        if node.type_id == ABI_SIGNATURE_TYPE and node.parent in functions
    ]
    assert len(records) == 572
    assert all(verify_abi_locations(container, record) == [] for record in records)


def test_callback_numeric_abi_geometry_is_exact():
    container = starter_template().build()
    expected = {
        "less_uint64_t": (2, 1),
        "wrapping_add_uint64_t": (2, 8),
        "bit_not_uint64_t": (1, 8),
    }
    for name, (arity, output_width) in expected.items():
        records = _records(container, name)
        assert len(records) == 2
        for record in records:
            locations = [
                ABIValueLocation.from_node(container.node_by_id(ref.target))  # pyright: ignore[reportArgumentType]
                for ref in record.refs
                if ref.to_off == int(ContractRole.ABI_LOCATION)
            ]
            assert [value.role for value in locations] == [
                ABIRole.SEMANTIC_ARGUMENT
            ] * arity + [ABIRole.TRANSIENT_OUTPUT, ABIRole.STATUS_RETURN]
            assert [value.register_indices for value in locations[:-1]] == [
                (index,) for index in range(arity + 1)
            ]
            assert (
                locations[-2].width == output_width
                and locations[-1].register_bank == ABIRegisterBank.RETURN
            )
            assert not any(value.role == ABIRole.HIDDEN_CONTEXT for value in locations)
