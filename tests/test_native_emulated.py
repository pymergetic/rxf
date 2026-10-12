"""The x86_64 and aarch64 snippets of the starter compute the same.

Both are run as machine code in an emulator (unicorn), so the proof does not
depend on the host: the same inputs go in under each calling convention
(System V and AAPCS64), and status and output bytes must be equal. UEFI and BIOS
use the bytes of the Linux target; that is checked here too.
"""

import ctypes
import platform
import random
import struct

import pytest

from .emulator import Emulator, Integer, Real
from .snippets import C_TYPES, INTEGERS, SIZES, Machine, snippets, values

BUFFER = 0x20000
PAGE = 0x1000
OUTPUT_FILL = 0xAA
CASES = 24


def scalar(kind: str, value: float) -> Integer | Real:
    """An argument as a register holds it: integers extended, reals as their bits."""
    if kind == "float":
        return Real(4, int.from_bytes(struct.pack("<f", value), "little"))
    if kind == "double":
        return Real(8, int.from_bytes(struct.pack("<d", value), "little"))
    return Integer(C_TYPES[kind](int(value)).value & (2**64 - 1))


def call(machine: str, code: bytes, kinds: list[str], arguments, out: int):
    """Run one snippet under the calling convention of the machine."""
    emulator = Emulator(machine)
    emulator.map(BUFFER, PAGE)
    emulator.write(BUFFER, bytes([OUTPUT_FILL]) * 64)
    address = emulator.load(code)
    given = [scalar(kind, value) for kind, value in zip(kinds, arguments, strict=True)]
    # the output pointer follows the arguments; reals do not take integer slots
    status = emulator.call(address, [*given, Integer(BUFFER)])
    return status, emulator.read(BUFFER, out).hex()


def inputs(snippet: dict, rng: random.Random) -> list[list[int | float]]:
    sets = [values(kind, rng) for kind in snippet["kinds"]]
    return [[rng.choice(options) for options in sets] for _ in range(CASES)]


def test_an_aarch64_snippet_computes_what_the_x86_64_snippet_computes():
    rng = random.Random(11)
    different = []
    for snippet in snippets():
        size = SIZES[snippet["out"]]
        for arguments in inputs(snippet, rng):
            want = call(
                "x86_64",
                bytes.fromhex(snippet["x86"]),
                snippet["kinds"],
                arguments,
                size,
            )
            have = call(
                "aarch64",
                bytes.fromhex(snippet["aarch64"]),
                snippet["kinds"],
                arguments,
                size,
            )
            if want != have:
                different.append((snippet["name"], arguments, want, have))
    assert different[:5] == []


def test_the_firmware_targets_hold_the_bytes_of_the_linux_targets():
    for snippet in snippets():
        assert snippet["uefi"] == snippet["x86"] == snippet["bios"], snippet["name"]
        assert snippet["aarch64_uefi"] == snippet["aarch64"], snippet["name"]


@pytest.mark.skipif(platform.machine() != "x86_64", reason="needs an x86_64 host")
def test_the_emulator_agrees_with_the_host_on_x86_64():
    rng = random.Random(5)
    host = Machine()
    for snippet in snippets()[::7]:
        code = bytes.fromhex(snippet["x86"])
        native = host.function(
            code,
            ctypes.c_uint32,
            [C_TYPES[kind] for kind in snippet["kinds"]] + [ctypes.c_void_p],
        )
        size = SIZES[snippet["out"]]
        for arguments in inputs(snippet, rng)[:6]:
            buffer = (ctypes.c_uint8 * 64)(*([OUTPUT_FILL] * 64))
            status = native(
                *[C_TYPES[k](a).value for k, a in zip(snippet["kinds"], arguments)],
                ctypes.cast(buffer, ctypes.c_void_p),
            )
            want = (status, bytes(buffer[:size]).hex())
            assert call("x86_64", code, snippet["kinds"], arguments, size) == want


def test_the_integer_kinds_are_the_ones_the_snippets_use():
    used = {kind for snippet in snippets() for kind in snippet["kinds"]}
    assert used <= set(INTEGERS) | {"float", "double", "bool"}
