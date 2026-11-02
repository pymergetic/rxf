"""The composed wrappers run in the image with no machine body of their own (docs/32 5.6).

A typed word wrapper -- ``bit_and_int16_t`` and the rest of the migration -- is
one call of its ``uint64_t`` word kernel with the operand widths its Signature
carries ("the operand specs of the call carry the widths", docs/29; the result
store truncates). The round trip this file holds to:

* the starter's Function carries no machine code and no blob -- the one binary
  residue of the image is the intrinsic core (``native/graph.h``'s leaves);
* the program the image carries for it (its one ``Bytecode`` ``Code``) runs on
  the interpreter the starter ships;
* the words it answers are the word kernel's, taken at the width of the result
  the Signature names.
"""

from __future__ import annotations

import struct

import pytest

from pymergetic.rxftk.core import tree

from . import rxf_native
from .snippets import STARTER

OK = 0
# (function, first operand, second operand, width of the result in bytes, answer)
CASES = [
    ("bitwise.bit_and_int16_t", 0x01FF, 0x0101, 2, 0x0101),
    ("bitwise.bit_and_int64_t", -(1) & 0xFFFFFFFFFFFFFFFF, 0xFF, 8, 0xFF),
    ("bitwise.bit_or_int8_t", 0x0F, 0xF0, 1, 0xFF),
    ("bitwise.bit_not_uint32_t", 0x00FF00FF, 0, 4, 0xFF00FF00),
    ("bitwise.bit_not_int8_t", 0x0F, 0, 1, 0xF0),
    ("wrapping.add_int16_t", 0x7FFF, 1, 2, 0x8000),
    ("wrapping.subtract_uint8_t", 0, 1, 1, 0xFF),
    ("wrapping.multiply_int32_t", 0x7FFFFFFF, 2, 4, 0xFFFFFFFE),
    ("wrapping.multiply_int64_t", 0x7FFFFFFFFFFFFFFF, 2, 8, 0xFFFFFFFFFFFFFFFE),
    ("wrapping.subtract_int64_t", 0, 1, 8, 0xFFFFFFFFFFFFFFFF),
]


def load_image():
    return tree.load(STARTER)


def function_of(image, path: str) -> int:
    found = image.resolve(f"pymergetic.rxf.numeric.{path}")
    assert found is not None, path
    return found


def codes_of(image, function: int) -> list[tuple[int, dict[str, str]]]:
    return [
        (child, dict(image.fields(child)))
        for child in image.children(function)
        if image.type_name(child) == "Code"
    ]


@pytest.mark.parametrize(("path", "a", "b", "width", "expected"), CASES)
def test_a_composed_wrapper_runs_with_no_machine_body(path, a, b, width, expected):
    """The migrated wrapper answers what its word kernel answers at the width."""
    image = load_image()
    function = function_of(image, path)
    values = dict(image.fields(function))
    assert values["implementation"].endswith(".Semantic")
    codes = codes_of(image, function)
    assert len(codes) == 1, f"{path}: {len(codes)} Code objects, not one program"
    _, program = codes[0]
    assert program["format"].endswith(".Bytecode"), program["format"]
    assert not program.get("runtime_target"), "a program names no target"
    blobs = [c for c in image.children(function) if image.type_name(c) == "CodeBlob"]
    assert not blobs, f"{path}: {len(blobs)} machine bodies in a delta"
    body = image.resolve(values["body"])
    assert body is not None and image.type_name(body) == "Call"
    signature = image.resolve(values["signature"])
    assert signature is not None and image.type_name(signature) == "Signature"
    argc = int(dict(image.fields(signature))["parameter_count"])
    operands = struct.pack(f"<{argc}Q", *(a, b)[:argc])
    with rxf_native.Memory(1 << 16) as memory:
        status, value = rxf_native.run(
            bytes.fromhex(program["data"]), operands, memory
        )
    assert status == OK, f"{path}: refused with status {status}"
    mask = (1 << (8 * width)) - 1
    assert value & mask == expected, f"{path}({a:#x}, {b:#x}) = {value & mask:#x}"


def test_the_word_kernels_keep_the_one_binary_residue():
    """The twins the wrappers compose over are the intrinsics: machine code stays."""
    image = load_image()
    for path in (
        "bitwise.bit_and_uint64_t",
        "bitwise.bit_or_uint64_t",
        "bitwise.bit_not_uint64_t",
        "wrapping.add_uint64_t",
        "wrapping.subtract_uint64_t",
        "wrapping.multiply_uint64_t",
    ):
        function = function_of(image, path)
        values = dict(image.fields(function))
        assert values["implementation"].endswith(".Native")
        formats = [v.get("format", "").rsplit(".", 1)[-1] for _, v in codes_of(image, function)]
        assert "Machine" in formats and "Wasm" in formats, f"{path}: {formats}"
