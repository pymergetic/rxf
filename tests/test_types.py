"""Canonical primitive validation, self-description, and wire tests."""

import math
import struct
from typing import Annotated

import pytest
from pydantic import Field, ValidationError

from pymergetic.rxf.expand import COUNTER
from pymergetic.rxf.model.module import derived_fqns
from pymergetic.rxf.output.base import Struct
from pymergetic.rxf.output.types import (
    Integer,
    RXFObject,
    float32_t,
    float64_t,
    int8_t,
    int16_t,
    int32_t,
    int64_t,
    uint8_t,
    uint16_t,
    uint32_t,
    uint64_t,
)
from pymergetic.rxf.schema import PRIMS
from pymergetic.rxf.ty.builtins import (
    BOOL_TYPE,
    BUILTINS,
    F32_TYPE,
    F64_TYPE,
    I8_TYPE,
    I16_TYPE,
    I32_TYPE,
    I64_TYPE,
    PRIMITIVES_MODULE_ID,
    U8_TYPE,
    U16_TYPE,
    U32_TYPE,
    U64_TYPE,
    VOID_TYPE,
)
from pymergetic.rxf.ty.objects import TypeFlags, TypeForm
from pymergetic.rxf.ty.table import TypeTable

INTEGER_CASES: tuple[tuple[type[Integer], int, int], ...] = (
    (uint8_t, 0, 2**8 - 1),
    (uint16_t, 0, 2**16 - 1),
    (uint32_t, 0, 2**32 - 1),
    (uint64_t, 0, 2**64 - 1),
    (int8_t, -(2**7), 2**7 - 1),
    (int16_t, -(2**15), 2**15 - 1),
    (int32_t, -(2**31), 2**31 - 1),
    (int64_t, -(2**63), 2**63 - 1),
)


@pytest.mark.parametrize(("primitive", "lo", "hi"), INTEGER_CASES)
def test_integer_bounds(primitive: type[Integer], lo: int, hi: int) -> None:
    assert issubclass(primitive, RXFObject)
    assert primitive._validate(lo) == lo
    assert primitive._validate(hi) == hi
    with pytest.raises(ValueError, match="out of range"):
        primitive._validate(lo - 1)
    with pytest.raises(ValueError, match="out of range"):
        primitive._validate(hi + 1)
    with pytest.raises(TypeError, match="expected int"):
        primitive._validate(True)


class NumericWire(Struct):
    __align__ = 1
    __pad_after__ = False

    signed: Annotated[int, int16_t]
    single: Annotated[float, float32_t]
    double: Annotated[float, float64_t]


def test_ieee_float_validation_and_wire_roundtrip() -> None:
    value = NumericWire(signed=-1234, single=1.25, double=-math.pi)
    expected = struct.pack("<hfd", -1234, 1.25, -math.pi)
    assert value.to_wire() == expected
    assert NumericWire.from_wire(expected) == value
    assert NumericWire.body_size() == 14
    assert NumericWire.padding_size() == 0
    assert NumericWire.wire_size() == 14


class AlignedWire(Struct):
    value: Annotated[int, uint32_t]
    tag: bytes = Field(max_length=1)


def test_struct_size_geometry_and_full_bounds_check() -> None:
    assert AlignedWire.body_size() == 5
    assert AlignedWire.padding_size() == 3
    assert AlignedWire.wire_size() == 8
    with pytest.raises(ValueError, match="AlignedWire wire bytes are out of bounds"):
        AlignedWire.from_wire(b"\0" * 7)


@pytest.mark.parametrize("primitive", (float32_t, float64_t))
@pytest.mark.parametrize("bad", (True, "1.0", math.inf, -math.inf, math.nan))
def test_float_rejects_non_numeric_and_non_finite(primitive, bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        primitive._validate(bad)


def test_float32_rejects_overflow_and_canonicalizes_precision() -> None:
    with pytest.raises(ValueError, match="out of range"):
        float32_t._validate(1e39)
    assert float32_t._validate(0.1) == struct.unpack("<f", struct.pack("<f", 0.1))[0]


def test_struct_validation_rejects_bool_for_numeric_fields() -> None:
    with pytest.raises((TypeError, ValidationError)):
        NumericWire(signed=True, single=1, double=1)
    with pytest.raises((TypeError, ValidationError)):
        NumericWire(signed=1, single=True, double=1)


def test_canonical_builtin_family_and_schema_parity() -> None:
    expected = (
        (VOID_TYPE, "void", TypeForm.VOID, 0, 1, TypeFlags.NONE),
        (BOOL_TYPE, "bool", TypeForm.BOOL, 1, 1, TypeFlags.NONE),
        (U8_TYPE, "uint8_t", TypeForm.UINT, 1, 1, TypeFlags.NONE),
        (U16_TYPE, "uint16_t", TypeForm.UINT, 2, 2, TypeFlags.NONE),
        (U32_TYPE, "uint32_t", TypeForm.UINT, 4, 4, TypeFlags.NONE),
        (U64_TYPE, "uint64_t", TypeForm.UINT, 8, 8, TypeFlags.NONE),
        (I8_TYPE, "int8_t", TypeForm.SINT, 1, 1, TypeFlags.SIGNED),
        (I16_TYPE, "int16_t", TypeForm.SINT, 2, 2, TypeFlags.SIGNED),
        (I32_TYPE, "int32_t", TypeForm.SINT, 4, 4, TypeFlags.SIGNED),
        (I64_TYPE, "int64_t", TypeForm.SINT, 8, 8, TypeFlags.SIGNED),
        (F32_TYPE, "float", TypeForm.FLOAT, 4, 4, TypeFlags.NONE),
        (F64_TYPE, "double", TypeForm.FLOAT, 8, 8, TypeFlags.NONE),
    )
    specs = {spec.id: spec for spec in BUILTINS}
    assert [
        (p.id.value, p.name, p.size, p.align, p.signed) for p in PRIMS.values()
    ] == [
        (id_, name, size, align, bool(flags & TypeFlags.SIGNED))
        for id_, name, _form, size, align, flags in expected
    ]
    assert [
        (
            id_,
            specs[id_].name,
            specs[id_].form,
            specs[id_].size,
            specs[id_].align,
            specs[id_].flags,
        )
        for id_, *_ in expected
    ] == list(expected)


def test_all_primitives_are_self_described_and_globally_indexed() -> None:
    container = COUNTER.build()
    table = TypeTable.from_container(container)
    fqns = derived_fqns(container.nodes)
    primitive_ids = {
        spec.id for spec in BUILTINS if spec.id in {p.id.value for p in PRIMS.values()}
    }
    assert primitive_ids <= table.types.keys()
    assert primitive_ids <= table.locations.keys()
    for type_id in primitive_ids:
        assert fqns[type_id] == f"pymergetic.rxf.primitives.{table.types[type_id].name}"
        node = container.node_by_id(type_id)
        assert node is not None and node.parent == PRIMITIVES_MODULE_ID
