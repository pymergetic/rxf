"""Uniform Function calling and target Code binding."""

from pymergetic.rxf.checker import check
from pymergetic.rxf.execution import bind
from pymergetic.rxf.expand import counter_template
from pymergetic.rxf.model.execution import (
    CallObject,
    CodeFormat,
    CodeObject,
    Effect,
    Endianness,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    SignatureObject,
    TargetObject,
    semantic_digest,
)
from pymergetic.rxf.ops.lowering import with_lowered
from pymergetic.rxf.ty.builtins import U32_TYPE


def _with_code():
    container = counter_template(include_lowered=False).build()
    nodes = container.nodes
    nodes.extend(
        [
            SignatureObject(
                600,
                "foundation_signature",
                601,
                U32_TYPE,
            ).to_node(),
            FunctionObject(
                601,
                "foundation_test",
                0,
                600,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.FOUNDATION,
                implementation_ids=(611, 612),
            ).to_node(),
            TargetObject(
                602,
                "x86_64-linux",
                0,
                1,
                1,
                1,
                64,
                Endianness.LITTLE,
            ).to_node(),
            TargetObject(
                603,
                "aarch64-uefi",
                0,
                2,
                2,
                2,
                64,
                Endianness.LITTLE,
            ).to_node(),
            CodeObject(
                611,
                "x86",
                601,
                601,
                602,
                CodeFormat.NATIVE,
                b"\xc3",
                600,
                Effect.NONE,
                1,
                semantic_digest("foundation_test", 600, Effect.NONE, 1),
            ).to_node(),
            CodeObject(
                612,
                "arm",
                601,
                601,
                603,
                CodeFormat.NATIVE,
                b"\xc0\x03\x5f\xd6",
                600,
                Effect.NONE,
                1,
                semantic_digest("foundation_test", 600, Effect.NONE, 1),
            ).to_node(),
            CallObject(613, "call_foundation", 400, 601).to_node(),
        ]
    )
    return with_lowered(container)


def test_calls_are_uniform_and_binder_selects_code_by_target() -> None:
    container = _with_code()
    assert check(container) == []
    assert bind(container, 601, 602).code_id == 611
    assert bind(container, 601, 603).code_id == 612


def test_call_cannot_target_code_directly() -> None:
    container = _with_code()
    call = container.node_by_id(613)
    assert call is not None
    for reference in call.refs:
        if reference.target == 601:
            reference.target = 611
    assert any("exactly one Function" in error for error in check(container))


def test_semantic_payload_ids_roundtrip_above_uint32() -> None:
    import struct

    from pymergetic.rxf.execution.decode import decode_code, decode_function
    from pymergetic.rxf.model.execution import (
        ArgumentObject,
        ImportObject,
        ParameterObject,
        RelocationKind,
        RelocationObject,
        ValueKind,
        ValueObject,
    )

    high = 1 << 40
    signature = SignatureObject(
        high + 1, "wide_signature", high + 2, high + 3, (high + 4,)
    ).to_node()
    assert struct.unpack("<4Q", signature.data) == (high + 3, 1, 0, 0)
    assert signature.refs[0].target == high + 4

    parameter = ParameterObject(
        high + 4, "wide_parameter", high + 1, high + 3, high + 5
    ).to_node()
    assert struct.unpack("<2Q2I", parameter.data)[:2] == (high + 3, high + 5)

    function = FunctionObject(
        high + 2,
        "wide_function",
        0,
        high + 1,
        FunctionImplementation.CODE_BACKED,
        FunctionLayer.FOUNDATION,
        body_id=high + 6,
        implementation_ids=(high + 7,),
    ).to_node()
    decoded_function = decode_function(function)
    assert decoded_function.signature_id == high + 1
    assert decoded_function.body_id == high + 6

    call = CallObject(
        high + 8, "wide_call", high + 2, high + 2, (high + 9,), high + 3
    ).to_node()
    assert struct.unpack("<3Q", call.data) == (high + 2, 1, high + 3)
    assert {reference.target for reference in call.refs} == {high + 2, high + 9}

    argument = ArgumentObject(
        high + 9, "wide_argument", high + 8, high + 4, high + 10
    ).to_node()
    assert struct.unpack("<2Q", argument.data) == (high + 4, high + 10)

    value = ValueObject(
        high + 10,
        "wide_value",
        high + 2,
        high + 3,
        ValueKind.OBJECT,
        (high + 11).to_bytes(8, "little"),
    ).to_node()
    assert struct.unpack("<QIIQ", value.data[:24]) == (
        high + 3,
        ValueKind.OBJECT,
        0,
        8,
    )

    code = CodeObject(
        high + 7,
        "wide_code",
        high + 2,
        high + 2,
        high + 12,
        CodeFormat.NATIVE,
        b"\xc3",
        high + 1,
        Effect.NONE,
        1,
        semantic_digest("wide_function", high + 1, Effect.NONE, 1),
    ).to_node()
    decoded_code = decode_code(code)
    assert decoded_code.owner_function == high + 2
    assert decoded_code.target_id == high + 12

    imported = ImportObject(
        high + 13, "wide_import", 0, high + 2, optional=True
    ).to_node()
    assert struct.unpack("<QI4x", imported.data) == (high + 2, 1)

    relocation = RelocationObject(
        high + 14,
        "wide_relocation",
        high + 7,
        high + 15,
        RelocationKind.ABSOLUTE,
        high + 16,
        8,
        -3,
    ).to_node()
    assert struct.unpack("<QIIQq", relocation.data) == (
        high + 15,
        RelocationKind.ABSOLUTE,
        8,
        high + 16,
        -3,
    )
