"""The numeric leaves are composed from their NumericContract (Wave 4b step 3).

The 286 numeric leaves of the starter are fully specified by their
``NumericContract``: the operation, the policy, the width, the signedness and
the source and destination types say what the leaf means, and that is all its
code's recipe needs (docs/29). So the recipe is *derived, not stored*: this
module maps a contract to the one ``.rxfs`` ``fn`` and the shape of its
operands the Seed CLI compiles. ``seed_build.SOURCES`` keeps rows only for the
intrinsics -- hash, the transactional runtime leaves, the memory ops,
``safe_point``, ``uart_pump`` -- the irreducible primitives with no contract to
compose from.

    compose(contract) -> (source, fn, shape, store width)   # one leaf's recipe
    contracts()       -> {key: contract}                    # the corpus
    recipes()         -> {key: recipe}                      # it, composed

The corpus is the product ``native_symbols`` spells (the integer ops over the
integer types, the signed extras, the float ops, every conversion). The fn
heads stay one short ``fn`` per variant in their own ``.rxfs`` file and the
parameterization lives inside the language; nothing here writes source text --
the composition picks a file, a ``fn`` and an operand shape, and the seed
compiles it (`.cursor/rules/source-in-its-lang.mdc`).
"""

from __future__ import annotations

import functools
from typing import Mapping

import native_symbols

# The C types of the corpus: the width of a value, the seed's operand token for
# it, and whether it is signed (the shape spellings of the ABI, docs/29).
TYPES = {
    "uint8_t": (1, "1u", 0),
    "uint16_t": (2, "2u", 0),
    "uint32_t": (4, "4u", 0),
    "uint64_t": (8, "8u", 0),
    "int8_t": (1, "1s", 1),
    "int16_t": (2, "2s", 1),
    "int32_t": (4, "4s", 1),
    "int64_t": (8, "8s", 1),
    "float": (4, "4f", 1),
    "double": (8, "8f", 1),
}
FLOATS = frozenset(("float", "double"))

# Where the fn heads of the integer families live (one short fn per variant).
FAMILY = {
    "wrapping": "native/numeric/wrapping.rxfs",
    "checked": "native/numeric/checked.rxfs",
    "saturating": "native/numeric/saturating.rxfs",
    "divrem": "native/numeric/divrem.rxfs",
    "bitwise": "native/numeric/bitwise.rxfs",
    "shifts": "native/numeric/shifts.rxfs",
    "compare": "native/numeric/compare.rxfs",
    "convert": "native/numeric/convert.rxfs",
}
FLOAT_OPS_SOURCE = "native/float/float.rxfs"
FLOAT_CONVERT_SOURCE = "native/float/convert.rxfs"

# The NumericOperation of a leaf -> (the stem of its fn name, how many operands,
# how their tokens spell, where its result store comes from, the family of its
# integer fn head). ``raw`` is the type's own token; ``unsigned`` forces the
# unsigned spelling of an integer -- the wrapping and bitwise ops wrap in the
# unsigned type and ``equal`` compares bit patterns -- and ``shift`` spells the
# count as a four-byte word after the value.
OPERATIONS = {
    "Equal": ("equal", 2, "unsigned", "bool", "compare"),
    "Less": ("less", 2, "raw", "bool", "compare"),
    "Minimum": ("minimum", 2, "raw", "destination", "compare"),
    "Maximum": ("maximum", 2, "raw", "destination", "compare"),
    "CheckedAdd": ("checked_add", 2, "raw", "destination", "checked"),
    "CheckedSubtract": ("checked_subtract", 2, "raw", "destination", "checked"),
    "CheckedMultiply": ("checked_multiply", 2, "raw", "destination", "checked"),
    "CheckedNegate": ("checked_negate", 1, "raw", "destination", "checked"),
    "CheckedAbsolute": ("checked_absolute", 1, "raw", "destination", "checked"),
    "WrappingAdd": ("wrapping_add", 2, "unsigned", "destination", "wrapping"),
    "WrappingSubtract": (
        "wrapping_subtract", 2, "unsigned", "destination", "wrapping",
    ),
    "WrappingMultiply": (
        "wrapping_multiply", 2, "unsigned", "destination", "wrapping",
    ),
    "SaturatingAdd": ("saturating_add", 2, "raw", "destination", "saturating"),
    "SaturatingSubtract": (
        "saturating_subtract", 2, "raw", "destination", "saturating",
    ),
    "SaturatingMultiply": (
        "saturating_multiply", 2, "raw", "destination", "saturating",
    ),
    "Divide": ("divide", 2, "raw", "destination", "divrem"),
    "Remainder": ("remainder", 2, "raw", "destination", "divrem"),
    "BitNot": ("bit_not", 1, "unsigned", "destination", "bitwise"),
    "BitAnd": ("bit_and", 2, "unsigned", "destination", "bitwise"),
    "BitOr": ("bit_or", 2, "unsigned", "destination", "bitwise"),
    "BitXor": ("bit_xor", 2, "unsigned", "destination", "bitwise"),
    "ShiftLeft": ("shift_left", 2, "shift", "destination", "shifts"),
    "ShiftRight": ("shift_right", 2, "shift", "destination", "shifts"),
    "Convert": ("convert", 1, "raw", "destination", "convert"),
}

# The IEEE rounding/refusal contract of a float leaf, ascending by the value of
# the enumerator -- the spelling the image writes.
IEEE = "IeeeRounding|IeeeNan|IeeeInfinity|IeeeSignedZero"


def _enum(stem: str) -> str:
    """The NumericOperation enumerator of an operation stem: ``bit_not`` is
    ``BitNot``."""
    return "".join(word.capitalize() for word in stem.split("_"))


def _policy(operation: str, source: str) -> str:
    """The NumericPolicy of a leaf, by its operation and the kind of its source."""
    real = source in FLOATS
    if operation.startswith("Wrapping"):
        return "Wrap"
    if operation.startswith("Saturating"):
        return "Saturate"
    if operation == "Convert":
        return "RefuseOverflow|RefuseNonFinite|RefuseInexact|RefuseOutOfRange"
    if operation.startswith("Checked"):
        return f"RefuseOverflow|{IEEE}" if real else "RefuseOverflow"
    if operation == "Divide":
        base = "RefuseOverflow|RefuseDivisionByZero"
        return f"{base}|{IEEE}" if real else base
    if operation == "Remainder":
        return "RefuseOverflow|RefuseDivisionByZero|DividendSign"
    if operation in ("Equal", "Less", "Minimum", "Maximum"):
        return IEEE if real else ""
    if operation in ("BitNot", "BitAnd", "BitOr", "BitXor"):
        return ""  # bit patterns: no refusal and no rounding to promise
    if operation in ("ShiftLeft", "ShiftRight"):
        arithmetic = operation == "ShiftRight" and TYPES[source][2]
        return "RefuseOverflow|RefuseShiftCount" + (
            "|ArithmeticShift" if arithmetic else ""
        )
    raise KeyError(operation)


def compose(contract: Mapping[str, str]) -> tuple[str, str, str, int]:
    """The recipe of one leaf: ``(source file, fn, shape argv, store width)``.

    Everything the seed needs is derived from what the contract says. The width
    and the signedness it carries are checked against the source type, so a
    contract that lies about itself composes to nothing.
    """
    operation = str(contract["operation"]).rsplit(".", 1)[-1]
    source = str(contract["source_type"])
    destination = str(contract["destination_type"])
    stem, operands, tokens, result, family = OPERATIONS[operation]
    width, token, signed = TYPES[source]
    if int(contract.get("width") or width * 8) != width * 8:
        raise ValueError(f"{operation} {source}: width is {contract.get('width')}")
    if int(contract.get("signed") or 0) != signed:
        raise ValueError(f"{operation} {source}: signed is {contract.get('signed')}")
    if operation != "Convert" and source != destination:
        raise ValueError(f"{operation}: {source} to {destination} is not a leaf")
    storew = TYPES[destination][0] if result == "destination" else 1
    if operation == "Convert":
        fn = f"convert_{source}_to_{destination}"
        shape = token
        file = (
            FLOAT_CONVERT_SOURCE
            if source in FLOATS or destination in FLOATS
            else FAMILY["convert"]
        )
    else:
        fn = f"{stem}_{source}"
        if tokens == "shift":
            shape = f"{token} 4u"
        elif tokens == "unsigned" and source not in FLOATS:
            shape = " ".join([f"{width}u"] * operands)
        else:
            shape = " ".join([token] * operands)
        file = FLOAT_OPS_SOURCE if source in FLOATS else FAMILY[family]
    return file, fn, shape, storew


def _contract(stem: str, source: str, destination: str) -> dict[str, str]:
    """One corpus leaf as its NumericContract, spelled the way the image does."""
    operation = _enum(stem)
    return {
        "operation": operation,
        "policy": _policy(operation, source),
        "width": str(TYPES[source][0] * 8),  # the contract spells width in bits
        "signed": str(TYPES[source][2]),
        "source_type": source,
        "destination_type": destination,
    }


@functools.lru_cache(maxsize=None)
def contracts() -> dict[str, dict[str, str]]:
    """The corpus as contracts: the product ``native_symbols`` spells, keyed as
    the fn of each leaf is named."""
    out: dict[str, dict[str, str]] = {}
    for name in native_symbols.INTEGER:
        for stem in native_symbols.INTEGER_OPS:
            out[f"{stem}_{name}"] = _contract(stem, name, name)
    for name in native_symbols.SIGNED:
        for stem in ("checked_negate", "checked_absolute"):
            out[f"{stem}_{name}"] = _contract(stem, name, name)
    for name in native_symbols.TYPES[8:]:
        for stem in native_symbols.FLOAT_OPS:
            out[f"{stem}_{name}"] = _contract(stem, name, name)
    for source in native_symbols.TYPES:
        for destination in native_symbols.TYPES:
            if source != destination:
                out[f"convert_{source}_to_{destination}"] = _contract(
                    "convert", source, destination
                )
    return out


@functools.lru_cache(maxsize=None)
def recipes() -> dict[str, tuple[str, str, str, int]]:
    """The corpus composed: every numeric key's ``(source, fn, shape, storew)``."""
    return {key: compose(contract) for key, contract in contracts().items()}
