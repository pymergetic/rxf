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
    for path in sorted(STARTER.rglob("*.xml")):
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
