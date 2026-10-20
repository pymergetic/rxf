"""The native snippets of the starter as data the equivalence tests run.

Each snippet is read from the starter: its scalar arguments, its output type and
its code for every target. The values are the edge and random inputs the tests
feed to all of them.
"""

import ctypes
import mmap
import random
import xml.etree.ElementTree as ET
from pathlib import Path

import sync

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"

RXF = "{urn:pymergetic:rxf}"
INTEGERS = {
    "uint8_t": (8, False),
    "uint16_t": (16, False),
    "uint32_t": (32, False),
    "uint64_t": (64, False),
    "int8_t": (8, True),
    "int16_t": (16, True),
    "int32_t": (32, True),
    "int64_t": (64, True),
}
FLOATS = {"float": 4, "double": 8}
C_TYPES = {
    "uint8_t": ctypes.c_uint8,
    "uint16_t": ctypes.c_uint16,
    "uint32_t": ctypes.c_uint32,
    "uint64_t": ctypes.c_uint64,
    "int8_t": ctypes.c_int8,
    "int16_t": ctypes.c_int16,
    "int32_t": ctypes.c_int32,
    "int64_t": ctypes.c_int64,
    "float": ctypes.c_float,
    "double": ctypes.c_double,
    "bool": ctypes.c_uint8,
}
SIZES = {
    **{k: v[0] // 8 for k, v in INTEGERS.items()},
    "float": 4,
    "double": 8,
    "bool": 1,
}


def values(type_name: str, rng: random.Random) -> list[int | float]:
    if type_name in INTEGERS:
        bits, signed = INTEGERS[type_name]
        low, high = (
            (-(1 << (bits - 1)), (1 << (bits - 1)) - 1)
            if signed
            else (0, (1 << bits) - 1)
        )
        edge = {
            0,
            1,
            2,
            3,
            7,
            8,
            31,
            32,
            63,
            64,
            low,
            high,
            high - 1,
            low + 1,
            -1,
            100,
            200,
        }
        picks: list[int | float] = [v for v in edge if low <= v <= high]
        picks += [rng.randint(low, high) for _ in range(14)]
        return picks
    pool = [
        0.0,
        -0.0,
        1.0,
        -1.0,
        0.5,
        3.25,
        1e30,
        -1e30,
        1e-30,
        float("inf"),
        float("-inf"),
        float("nan"),
    ]
    return pool + [rng.uniform(-1000, 1000) for _ in range(8)]


# ---- the contract of the arithmetic leaves of native/graph.h -----------------------------------

# The refusals of native/basics.h that its arithmetic returns.
OVERFLOW = 1
DIVISION_BY_ZERO = 2

# The byte an output buffer is filled with before a snippet runs: what a leaf that refuses
# leaves behind, visible in the differential tests.
OUTPUT_FILL = 0xAA

# The snippets behind the multiply, divide and remainder leaves of native/graph.h, by leaf name
# (each is rxf_<name> of native/basics.c, in its module of starter/pymergetic/rxf/numeric/).
ARITHMETIC = {
    "wrapping_multiply_uint64_t": "pymergetic/rxf/numeric/wrapping:multiply_uint64_t",
    "checked_multiply_uint64_t": "pymergetic/rxf/numeric/checked:multiply_uint64_t",
    "checked_multiply_int64_t": "pymergetic/rxf/numeric/checked:multiply_int64_t",
    "divide_uint64_t": "pymergetic/rxf/numeric/division:divide_uint64_t",
    "remainder_uint64_t": "pymergetic/rxf/numeric/division:remainder_uint64_t",
    "divide_int64_t": "pymergetic/rxf/numeric/division:divide_int64_t",
    "remainder_int64_t": "pymergetic/rxf/numeric/division:remainder_int64_t",
    "remainder_uint32_t": "pymergetic/rxf/numeric/division:remainder_uint32_t",
    "divide_int32_t": "pymergetic/rxf/numeric/division:divide_int32_t",
    "remainder_int32_t": "pymergetic/rxf/numeric/division:remainder_int32_t",
}


def width_signed(leaf: str) -> tuple[int, bool]:
    """The width and kind the name of an arithmetic leaf names."""
    if leaf.endswith("uint64_t"):
        return 64, False
    if leaf.endswith("int64_t"):
        return 64, True
    if leaf.endswith("uint32_t"):
        return 32, False
    return 32, True


def arithmetic(leaf: str, a: int, b: int) -> tuple[int, int]:
    """(status, value) of one arithmetic leaf's C (native/basics.c), in Python.

    This is the contract the leaf comment of native/graph.h promises: a division by zero
    refuses DIVISION_BY_ZERO; the signed forms refuse OVERFLOW at INT_MIN / -1 (both the
    division and the remainder, which is written with it); the checked multiplies refuse
    OVERFLOW where the product leaves the type; the wrapping multiply wraps. The C divides
    and takes the remainder truncating toward zero.
    """
    width, signed = width_signed(leaf)
    mask = (1 << width) - 1
    low = -(1 << (width - 1)) if signed else 0
    high = (1 << (width - 1)) - 1 if signed else mask

    def view(value: int) -> int:
        value &= mask
        return value - (1 << width) if signed and value > high else value

    a, b = view(a), view(b)
    if leaf.startswith("wrapping_multiply"):
        return 0, (a * b) & mask
    if leaf.startswith("checked_multiply"):
        product = a * b
        return (OVERFLOW, 0) if product < low or product > high else (0, product & mask)
    if b == 0:
        return DIVISION_BY_ZERO, 0
    if signed and a == low and b == -1:
        return OVERFLOW, 0
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    if leaf.startswith("divide"):
        return 0, quotient & mask
    return 0, (a - quotient * b) & mask


# The snippets behind the checked and saturating leaves of the integer traits, by
# ``<mode>_<op>_<type>`` name (each is rxf_<name> of native/basics.c, in starter/
# pymergetic/rxf/numeric/). Every integer type for the two-operand ops; negate and
# absolute are checked-only and signed-only (the C defines them in RXF_DEFINE_SIGNED).
CHECKED_SATURATING = {
    f"{mode}_{op}_{kind}": f"pymergetic/rxf/numeric/{mode}:{op}_{kind}"
    for mode in ("checked", "saturating")
    for op in ("add", "subtract", "multiply")
    for kind in INTEGERS
} | {
    f"checked_{op}_{kind}": f"pymergetic/rxf/numeric/checked:{op}_{kind}"
    for op in ("negate", "absolute")
    for kind in INTEGERS
    if INTEGERS[kind][1]
}


def _checked_kind(leaf: str) -> str:
    """The type a checked/saturating leaf names, unambiguously (``_uint8_t`` never
    matches ``_int8_t``: the underscore is part of the match)."""
    for kind in INTEGERS:
        if leaf.endswith("_" + kind):
            return kind
    raise KeyError(f"{leaf}: no integer type")


def checked_saturating(leaf: str, a: int, b: int = 0) -> tuple[int, int]:
    """(status, value) of one checked/saturating leaf's C (native/basics.c), in Python.

    The contract: a checked op refuses OVERFLOW (writes nothing) where the true result
    leaves the type; a saturating op always succeeds, clamping to the type's MINIMUM or
    MAXIMUM (the C clamps to MAXIMUM on an unsigned overflow and by the sign of the
    second operand -- MINIMUM when subtracting a positive, etc.). The result is the two's
    complement bits of the type.
    """
    bits, signed = INTEGERS[_checked_kind(leaf)]
    mask = (1 << bits) - 1
    low = -(1 << (bits - 1)) if signed else 0
    high = (1 << (bits - 1)) - 1 if signed else mask

    def view(value: int) -> int:
        value &= mask
        return value - (1 << bits) if signed and value > high else value

    a, b = view(a), view(b)
    op = leaf.split("_")[1]
    if op in ("negate", "absolute"):
        # checked-only: MIN has no representable positive counterpart.
        if a == low:
            return OVERFLOW, 0
        value = -a if op == "negate" else abs(a)
        return 0, (value & mask)
    result = {"add": a + b, "subtract": a - b, "multiply": a * b}[op]
    size = bits // 8
    if result < low or result > high:
        if leaf.startswith("checked"):
            return OVERFLOW, 0
        result = high if result > high else low
    return 0, (result & mask)


def checked_saturating_seen(leaf: str, a: int, b: int = 0) -> tuple[int, str]:
    """The contract of one checked/saturating leaf as a run of its snippet sees it."""
    status, value = checked_saturating(leaf, a, b)
    size = SIZES[_checked_kind(leaf)]
    written = (
        value.to_bytes(size, "little").hex()
        if status == 0
        else bytes([OUTPUT_FILL] * size).hex()
    )
    return status, written


def checked_saturating_arguments(leaf: str) -> list[tuple[int, int]]:
    """The edge pairs every checked/saturating leaf is held to: the extremes where an
    add, subtract or multiply crosses the type's bound in either direction."""
    kind = _checked_kind(leaf)
    bits, signed = INTEGERS[kind]
    top = 1 << (bits - 1)
    low = -top if signed else 0
    high = top - 1 if signed else 2 * top - 1
    pairs = [
        (0, 0),
        (0, 1),
        (1, 1),
        (7, 2),
        (high, 1),  # high + 1 and high * 1 cross the top
        (high, 2),  # the multiply leaves the type
        (high, high),
        (low, 1),
        (low, low),
    ]
    if signed:
        pairs += [
            (-1, -1),
            (-1, high),
            (low, -1),  # MIN * -1 cannot be represented
            (low, -2),
            (high, -1),
            (low, high),
            (high, low),
        ]
    else:
        pairs += [(top, 2), (top, top), (0, high), (1, high)]
    return pairs


def arithmetic_seen(leaf: str, a: int, b: int) -> tuple[int, str]:
    """The contract of one leaf as a run of its snippet sees it: the status and the output
    bytes. A leaf that refuses writes nothing, so the output stays the fill of the buffer."""
    status, value = arithmetic(leaf, a, b)
    size = width_signed(leaf)[0] // 8
    written = (
        value.to_bytes(size, "little").hex()
        if status == 0
        else bytes([OUTPUT_FILL] * size).hex()
    )
    return status, written


def boundary_arguments(leaf: str) -> list[tuple[int, int]]:
    """The edge pairs every arithmetic leaf is held to: 0, 1, -1, the extremes, wrap-around."""
    width, signed = width_signed(leaf)
    top = 1 << (width - 1)
    low = -top if signed else 0
    high = top - 1 if signed else 2 * top - 1
    pairs = [
        (0, 0),
        (0, 7),
        (7, 0),  # a division by zero refuses
        (1, 1),
        (7, 2),
        (low, 1),
        (low, low),
        (high, 1),
        (high, 2),  # a checked multiply refuses where the product leaves the type
        (high, high),
    ]
    if signed:
        pairs += [
            (-1, -1),
            (-1, high),
            (low, -1),  # INT_MIN / -1 (and INT_MIN % -1) refuses OVERFLOW
            (high, -1),
            (7, -2),  # the C truncates toward zero
            (-7, 2),
        ]
    else:
        pairs += [(top, 2), (top, top)]
    return pairs


class Machine:
    """x86_64 machine code from the starter as callable functions."""

    def __init__(self) -> None:
        self.maps: list[mmap.mmap] = []

    def function(self, code: bytes, result, args):
        page = mmap.mmap(
            -1,
            max(len(code), 1),
            prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC,
        )
        page.write(code)
        self.maps.append(page)
        address = ctypes.addressof(ctypes.c_char.from_buffer(page))
        return ctypes.CFUNCTYPE(result, *args)(address)


def snippets() -> list[dict]:
    """The numeric snippets of the starter: scalar arguments and one output."""
    found = []
    for path in sorted(STARTER.rglob("*.rxfd")):
        parts = path.relative_to(STARTER).with_suffix("").parts
        for function in ET.parse(path).getroot().iter("Function"):
            codes = {c.get("name"): c for c in function if c.tag == "Code"}
            if "wasm32" not in codes:
                continue
            signature = next(
                s
                for s in function
                if s.tag == "ABISignature" and s.get("name") == "wasm_c_status_out"
            )
            locations = sync.order(
                [loc for loc in signature if loc.tag == "ABIValueLocation"]
            )
            arguments = [
                loc
                for loc in locations
                if sync.word(loc.get("value_role")) == "Argument"
            ]
            outputs = [
                loc for loc in locations if sync.word(loc.get("value_role")) == "Output"
            ]
            if len(outputs) != 1 or any(
                sync.value_class_of(loc) == "Pointer" for loc in arguments
            ):
                continue
            kinds = []
            for loc in arguments:
                association = word_after(loc.get("association"))
                parameter = next(
                    p
                    for p in function.findall("Signature/*")
                    if p.get("name") == association
                )
                kinds.append(parameter.get("value_type"))
            found.append(
                {
                    "name": f"{'/'.join(parts)}:{function.get('name')}",
                    "kinds": kinds,
                    "out": word(outputs[0].get("pointee_type")),
                    "wasm": codes["wasm32"].get(sync.DATA),
                    "x86": codes["x86_64_linux"].get(sync.DATA),
                    "aarch64": codes["aarch64_linux"].get(sync.DATA),
                    "aarch64_uefi": codes["aarch64_uefi"].get(sync.DATA),
                    "uefi": codes["x86_64_uefi"].get(sync.DATA),
                    "bios": codes["x86_64_bios"].get(sync.DATA),
                    "params": [sync.wasm_type_of(loc) for loc in locations],
                }
            )
    return found


def word(value: str | None) -> str:
    return (value or "").rsplit(".", 1)[-1]


def word_after(value: str | None) -> str:
    return word(value)
