"""Stages 1-3 composed compiler, movable slots, and native image proofs."""

from __future__ import annotations

import random
from copy import deepcopy

import pytest

from pymergetic.rxf.compiler import (
    Architecture,
    CompileError,
    RuntimeContext,
    compile_function,
    compile_targets,
    emit,
    normalize,
)
from pymergetic.rxf.compiler.native import LinkError, _validate_fixups
from pymergetic.rxf.expand import (
    STARTER_CHECKOUT_FUNCTION_ID,
    STARTER_MAIN_FUNCTION_ID,
    starter_template,
)


def test_starter_normalizes_independent_of_node_order():
    original = starter_template().build()
    shuffled = deepcopy(original)
    random.Random(57).shuffle(shuffled.nodes)
    assert normalize(original, STARTER_CHECKOUT_FUNCTION_ID) == normalize(
        shuffled, STARTER_CHECKOUT_FUNCTION_ID
    )
    assert (
        compile_targets(original, STARTER_CHECKOUT_FUNCTION_ID).inspect()
        == compile_targets(shuffled, STARTER_CHECKOUT_FUNCTION_ID).inspect()
    )


def test_checkout_and_main_have_status_explicit_ir_and_both_targets():
    container = starter_template().build()
    checkout = compile_function(container, STARTER_CHECKOUT_FUNCTION_ID)
    main = compile_function(container, STARTER_MAIN_FUNCTION_ID)
    assert sum(len(block.operations) for block in checkout.blocks) == 6
    assert sum(len(block.operations) for block in main.blocks) == 1
    for ir in (checkout, main):
        images = {arch: emit(ir, arch) for arch in Architecture}
        assert images[Architecture.X86_64].text
        assert images[Architecture.AARCH64].text.endswith(bytes.fromhex("c0035fd6"))
        assert all(image.fixups for image in images.values())
        assert all(
            (slot.offset + slot.width) <= 256
            for slot in images[Architecture.X86_64].frame
        )


def test_slots_move_code_and_object_without_changing_durable_ids():
    context = RuntimeContext.empty()
    context.codes.publish({9: object()})
    context.objects.publish({17: bytearray(b"old")})
    old_code = context.codes.resolve(9)
    old_object = context.objects.resolve(17)
    new_code_payload = object()
    new_object_payload = bytearray(b"new")
    context.codes.update(9, new_code_payload)
    context.objects.update(17, new_object_payload)
    assert context.codes.resolve(9).payload is new_code_payload
    assert context.objects.resolve(17).payload is new_object_payload
    assert context.codes.resolve(9).generation == old_code.generation + 1
    assert context.objects.resolve(17).generation == old_object.generation + 1


def test_corrupt_fixup_refuses():
    image = compile_targets(
        starter_template().build(), STARTER_MAIN_FUNCTION_ID
    ).images[0]
    broken = [deepcopy(image.fixups[0])]
    object.__setattr__(broken[0], "offset", len(image.text))
    with pytest.raises(LinkError, match="invalid fixup"):
        _validate_fixups(bytearray(image.text), broken)


def test_result_cycle_refuses():
    container = starter_template().build()
    graph = normalize(container, STARTER_CHECKOUT_FUNCTION_ID)
    terminal_call = container.node_by_id(graph.calls[-1].id)
    assert terminal_call is not None
    argument_id = next(ref.target for ref in terminal_call.refs if ref.to_off == 203)
    argument = container.node_by_id(argument_id)
    assert argument is not None
    value = container.node_by_id(int.from_bytes(argument.data[8:16], "little"))
    assert value is not None
    value.data = (
        value.data[:8]
        + (3).to_bytes(4, "little")
        + bytes(4)
        + (8).to_bytes(8, "little")
        + graph.calls[-1].id.to_bytes(8, "little")
    )
    with pytest.raises(CompileError, match="cycle"):
        normalize(container, STARTER_CHECKOUT_FUNCTION_ID)
