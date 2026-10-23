"""The seed driver: which `.rxfs` fn a snippet key is, and how to compile it.

A key maps to (source file, fn name, shape argv, result store width); the driver
points the seed CLI at that one fn and turns its hex into the snippet's bytes.
The programs live in native/**/*.rxfs as real Seed `fn` sources -- this module
holds no source text. If a change needs logic, it is a `.rxfs` macro or fn; if it
needs Python string assembly, it is in the wrong file (`.cursor/rules/
source-in-its-lang.mdc`).

A `.rxfs` fn is statements over its parameters, ending in `return` (stores the
value through `out`, status 0) or `refuse` (a RXF_REFUSE_* status, no store). The
shape argv is the seed's per-operand spelling of the key's C operands
(``4s`` = sign-extended from 4 bytes; ``!pHq`` = the letters form) and the width
of the result store -- the C ABI of the key (docs/29), not its program.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import seed_cache

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1]
NATIVE = PACKAGE / "native"
SEED_C = NATIVE / "seed" / "seed.c"

# The seed's target word per machine of ``native_build.MACHINES``.
TARGET = {"x86_64": "x64", "aarch64": "arm64", "wasm32": "wasm"}

_seed_bin = None


def seed_bin() -> Path:
    """Path to the compiled seed codegen CLI, one binary per ``seed.c``.

    The path is content-addressed (hash of ``seed.c``), not a tempdir: the cache
    keys of ``seed_cache`` include this argv, so a new ``seed.c`` is a new path,
    is a new key -- and every worker on the machine shares the one binary.
    """
    global _seed_bin
    if _seed_bin is None or not _seed_bin.exists():
        digest = hashlib.sha256(SEED_C.read_bytes()).hexdigest()[:16]
        out = seed_cache.CACHE / "bin" / f"seed-{digest}"
        if not out.exists():
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
            subprocess.run(
                ["cc", "-O2", str(SEED_C), "-o", str(tmp)],
                check=True, capture_output=True, text=True,
            )
            tmp.replace(out)
        _seed_bin = out
    return _seed_bin


# key -> (source file, fn name, shape argv, result store width).
SOURCES: dict[str, tuple[str, str, str, int]] = {
    # native/numeric/wrapping.rxfs -- wrapping C arithmetic in the unsigned type
    "wrapping_add_int16_t": ("native/numeric/wrapping.rxfs", "wrapping_add_int16_t", "2u 2u", 2),
    "wrapping_add_int32_t": ("native/numeric/wrapping.rxfs", "wrapping_add_int32_t", "4u 4u", 4),
    "wrapping_add_int64_t": ("native/numeric/wrapping.rxfs", "wrapping_add_int64_t", "8u 8u", 8),
    "wrapping_add_int8_t": ("native/numeric/wrapping.rxfs", "wrapping_add_int8_t", "1u 1u", 1),
    "wrapping_add_uint16_t": ("native/numeric/wrapping.rxfs", "wrapping_add_uint16_t", "2u 2u", 2),
    "wrapping_add_uint32_t": ("native/numeric/wrapping.rxfs", "wrapping_add_uint32_t", "4u 4u", 4),
    "wrapping_add_uint64_t": ("native/numeric/wrapping.rxfs", "wrapping_add_uint64_t", "8u 8u", 8),
    "wrapping_add_uint8_t": ("native/numeric/wrapping.rxfs", "wrapping_add_uint8_t", "1u 1u", 1),
    "wrapping_multiply_int16_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_int16_t", "2u 2u", 2),
    "wrapping_multiply_int32_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_int32_t", "4u 4u", 4),
    "wrapping_multiply_int64_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_int64_t", "8u 8u", 8),
    "wrapping_multiply_int8_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_int8_t", "1u 1u", 1),
    "wrapping_multiply_uint16_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_uint16_t", "2u 2u", 2),
    "wrapping_multiply_uint32_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_uint32_t", "4u 4u", 4),
    "wrapping_multiply_uint64_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_uint64_t", "8u 8u", 8),
    "wrapping_multiply_uint8_t": ("native/numeric/wrapping.rxfs", "wrapping_multiply_uint8_t", "1u 1u", 1),
    "wrapping_subtract_int16_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_int16_t", "2u 2u", 2),
    "wrapping_subtract_int32_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_int32_t", "4u 4u", 4),
    "wrapping_subtract_int64_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_int64_t", "8u 8u", 8),
    "wrapping_subtract_int8_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_int8_t", "1u 1u", 1),
    "wrapping_subtract_uint16_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_uint16_t", "2u 2u", 2),
    "wrapping_subtract_uint32_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_uint32_t", "4u 4u", 4),
    "wrapping_subtract_uint64_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_uint64_t", "8u 8u", 8),
    "wrapping_subtract_uint8_t": ("native/numeric/wrapping.rxfs", "wrapping_subtract_uint8_t", "1u 1u", 1),

    # native/numeric/bitwise.rxfs -- bitwise not/and/or/xor
    "bit_and_int16_t": ("native/numeric/bitwise.rxfs", "bit_and_int16_t", "2u 2u", 2),
    "bit_and_int32_t": ("native/numeric/bitwise.rxfs", "bit_and_int32_t", "4u 4u", 4),
    "bit_and_int64_t": ("native/numeric/bitwise.rxfs", "bit_and_int64_t", "8u 8u", 8),
    "bit_and_int8_t": ("native/numeric/bitwise.rxfs", "bit_and_int8_t", "1u 1u", 1),
    "bit_and_uint16_t": ("native/numeric/bitwise.rxfs", "bit_and_uint16_t", "2u 2u", 2),
    "bit_and_uint32_t": ("native/numeric/bitwise.rxfs", "bit_and_uint32_t", "4u 4u", 4),
    "bit_and_uint64_t": ("native/numeric/bitwise.rxfs", "bit_and_uint64_t", "8u 8u", 8),
    "bit_and_uint8_t": ("native/numeric/bitwise.rxfs", "bit_and_uint8_t", "1u 1u", 1),
    "bit_not_int16_t": ("native/numeric/bitwise.rxfs", "bit_not_int16_t", "2u", 2),
    "bit_not_int32_t": ("native/numeric/bitwise.rxfs", "bit_not_int32_t", "4u", 4),
    "bit_not_int64_t": ("native/numeric/bitwise.rxfs", "bit_not_int64_t", "8u", 8),
    "bit_not_int8_t": ("native/numeric/bitwise.rxfs", "bit_not_int8_t", "1u", 1),
    "bit_not_uint16_t": ("native/numeric/bitwise.rxfs", "bit_not_uint16_t", "2u", 2),
    "bit_not_uint32_t": ("native/numeric/bitwise.rxfs", "bit_not_uint32_t", "4u", 4),
    "bit_not_uint64_t": ("native/numeric/bitwise.rxfs", "bit_not_uint64_t", "8u", 8),
    "bit_not_uint8_t": ("native/numeric/bitwise.rxfs", "bit_not_uint8_t", "1u", 1),
    "bit_or_int16_t": ("native/numeric/bitwise.rxfs", "bit_or_int16_t", "2u 2u", 2),
    "bit_or_int32_t": ("native/numeric/bitwise.rxfs", "bit_or_int32_t", "4u 4u", 4),
    "bit_or_int64_t": ("native/numeric/bitwise.rxfs", "bit_or_int64_t", "8u 8u", 8),
    "bit_or_int8_t": ("native/numeric/bitwise.rxfs", "bit_or_int8_t", "1u 1u", 1),
    "bit_or_uint16_t": ("native/numeric/bitwise.rxfs", "bit_or_uint16_t", "2u 2u", 2),
    "bit_or_uint32_t": ("native/numeric/bitwise.rxfs", "bit_or_uint32_t", "4u 4u", 4),
    "bit_or_uint64_t": ("native/numeric/bitwise.rxfs", "bit_or_uint64_t", "8u 8u", 8),
    "bit_or_uint8_t": ("native/numeric/bitwise.rxfs", "bit_or_uint8_t", "1u 1u", 1),
    "bit_xor_int16_t": ("native/numeric/bitwise.rxfs", "bit_xor_int16_t", "2u 2u", 2),
    "bit_xor_int32_t": ("native/numeric/bitwise.rxfs", "bit_xor_int32_t", "4u 4u", 4),
    "bit_xor_int64_t": ("native/numeric/bitwise.rxfs", "bit_xor_int64_t", "8u 8u", 8),
    "bit_xor_int8_t": ("native/numeric/bitwise.rxfs", "bit_xor_int8_t", "1u 1u", 1),
    "bit_xor_uint16_t": ("native/numeric/bitwise.rxfs", "bit_xor_uint16_t", "2u 2u", 2),
    "bit_xor_uint32_t": ("native/numeric/bitwise.rxfs", "bit_xor_uint32_t", "4u 4u", 4),
    "bit_xor_uint64_t": ("native/numeric/bitwise.rxfs", "bit_xor_uint64_t", "8u 8u", 8),
    "bit_xor_uint8_t": ("native/numeric/bitwise.rxfs", "bit_xor_uint8_t", "1u 1u", 1),

    # native/numeric/shifts.rxfs -- shifts, refusing an out-of-range count
    "shift_left_int16_t": ("native/numeric/shifts.rxfs", "shift_left_int16_t", "2s 4u", 2),
    "shift_left_int32_t": ("native/numeric/shifts.rxfs", "shift_left_int32_t", "4s 4u", 4),
    "shift_left_int64_t": ("native/numeric/shifts.rxfs", "shift_left_int64_t", "8s 4u", 8),
    "shift_left_int8_t": ("native/numeric/shifts.rxfs", "shift_left_int8_t", "1s 4u", 1),
    "shift_left_uint16_t": ("native/numeric/shifts.rxfs", "shift_left_uint16_t", "2u 4u", 2),
    "shift_left_uint32_t": ("native/numeric/shifts.rxfs", "shift_left_uint32_t", "4u 4u", 4),
    "shift_left_uint64_t": ("native/numeric/shifts.rxfs", "shift_left_uint64_t", "8u 4u", 8),
    "shift_left_uint8_t": ("native/numeric/shifts.rxfs", "shift_left_uint8_t", "1u 4u", 1),
    "shift_right_int16_t": ("native/numeric/shifts.rxfs", "shift_right_int16_t", "2s 4u", 2),
    "shift_right_int32_t": ("native/numeric/shifts.rxfs", "shift_right_int32_t", "4s 4u", 4),
    "shift_right_int64_t": ("native/numeric/shifts.rxfs", "shift_right_int64_t", "8s 4u", 8),
    "shift_right_int8_t": ("native/numeric/shifts.rxfs", "shift_right_int8_t", "1s 4u", 1),
    "shift_right_uint16_t": ("native/numeric/shifts.rxfs", "shift_right_uint16_t", "2u 4u", 2),
    "shift_right_uint32_t": ("native/numeric/shifts.rxfs", "shift_right_uint32_t", "4u 4u", 4),
    "shift_right_uint64_t": ("native/numeric/shifts.rxfs", "shift_right_uint64_t", "8u 4u", 8),
    "shift_right_uint8_t": ("native/numeric/shifts.rxfs", "shift_right_uint8_t", "1u 4u", 1),

    # native/numeric/compare.rxfs -- equal/less and min/max
    "equal_int16_t": ("native/numeric/compare.rxfs", "equal_int16_t", "2u 2u", 1),
    "equal_int32_t": ("native/numeric/compare.rxfs", "equal_int32_t", "4u 4u", 1),
    "equal_int64_t": ("native/numeric/compare.rxfs", "equal_int64_t", "8u 8u", 1),
    "equal_int8_t": ("native/numeric/compare.rxfs", "equal_int8_t", "1u 1u", 1),
    "equal_uint16_t": ("native/numeric/compare.rxfs", "equal_uint16_t", "2u 2u", 1),
    "equal_uint32_t": ("native/numeric/compare.rxfs", "equal_uint32_t", "4u 4u", 1),
    "equal_uint64_t": ("native/numeric/compare.rxfs", "equal_uint64_t", "8u 8u", 1),
    "equal_uint8_t": ("native/numeric/compare.rxfs", "equal_uint8_t", "1u 1u", 1),
    "less_int16_t": ("native/numeric/compare.rxfs", "less_int16_t", "2s 2s", 1),
    "less_int32_t": ("native/numeric/compare.rxfs", "less_int32_t", "4s 4s", 1),
    "less_int64_t": ("native/numeric/compare.rxfs", "less_int64_t", "8s 8s", 1),
    "less_int8_t": ("native/numeric/compare.rxfs", "less_int8_t", "1s 1s", 1),
    "less_uint16_t": ("native/numeric/compare.rxfs", "less_uint16_t", "2u 2u", 1),
    "less_uint32_t": ("native/numeric/compare.rxfs", "less_uint32_t", "4u 4u", 1),
    "less_uint64_t": ("native/numeric/compare.rxfs", "less_uint64_t", "8u 8u", 1),
    "less_uint8_t": ("native/numeric/compare.rxfs", "less_uint8_t", "1u 1u", 1),
    "maximum_int16_t": ("native/numeric/compare.rxfs", "maximum_int16_t", "2s 2s", 2),
    "maximum_int32_t": ("native/numeric/compare.rxfs", "maximum_int32_t", "4s 4s", 4),
    "maximum_int64_t": ("native/numeric/compare.rxfs", "maximum_int64_t", "8s 8s", 8),
    "maximum_int8_t": ("native/numeric/compare.rxfs", "maximum_int8_t", "1s 1s", 1),
    "maximum_uint16_t": ("native/numeric/compare.rxfs", "maximum_uint16_t", "2u 2u", 2),
    "maximum_uint32_t": ("native/numeric/compare.rxfs", "maximum_uint32_t", "4u 4u", 4),
    "maximum_uint64_t": ("native/numeric/compare.rxfs", "maximum_uint64_t", "8u 8u", 8),
    "maximum_uint8_t": ("native/numeric/compare.rxfs", "maximum_uint8_t", "1u 1u", 1),
    "minimum_int16_t": ("native/numeric/compare.rxfs", "minimum_int16_t", "2s 2s", 2),
    "minimum_int32_t": ("native/numeric/compare.rxfs", "minimum_int32_t", "4s 4s", 4),
    "minimum_int64_t": ("native/numeric/compare.rxfs", "minimum_int64_t", "8s 8s", 8),
    "minimum_int8_t": ("native/numeric/compare.rxfs", "minimum_int8_t", "1s 1s", 1),
    "minimum_uint16_t": ("native/numeric/compare.rxfs", "minimum_uint16_t", "2u 2u", 2),
    "minimum_uint32_t": ("native/numeric/compare.rxfs", "minimum_uint32_t", "4u 4u", 4),
    "minimum_uint64_t": ("native/numeric/compare.rxfs", "minimum_uint64_t", "8u 8u", 8),
    "minimum_uint8_t": ("native/numeric/compare.rxfs", "minimum_uint8_t", "1u 1u", 1),

    # native/numeric/checked.rxfs -- checked negate/absolute/add/subtract/multiply
    "checked_absolute_int16_t": ("native/numeric/checked.rxfs", "checked_absolute_int16_t", "2s", 2),
    "checked_absolute_int32_t": ("native/numeric/checked.rxfs", "checked_absolute_int32_t", "4s", 4),
    "checked_absolute_int64_t": ("native/numeric/checked.rxfs", "checked_absolute_int64_t", "8s", 8),
    "checked_absolute_int8_t": ("native/numeric/checked.rxfs", "checked_absolute_int8_t", "1s", 1),
    "checked_add_int16_t": ("native/numeric/checked.rxfs", "checked_add_int16_t", "2s 2s", 2),
    "checked_add_int32_t": ("native/numeric/checked.rxfs", "checked_add_int32_t", "4s 4s", 4),
    "checked_add_int64_t": ("native/numeric/checked.rxfs", "checked_add_int64_t", "8s 8s", 8),
    "checked_add_int8_t": ("native/numeric/checked.rxfs", "checked_add_int8_t", "1s 1s", 1),
    "checked_add_uint16_t": ("native/numeric/checked.rxfs", "checked_add_uint16_t", "2u 2u", 2),
    "checked_add_uint32_t": ("native/numeric/checked.rxfs", "checked_add_uint32_t", "4u 4u", 4),
    "checked_add_uint64_t": ("native/numeric/checked.rxfs", "checked_add_uint64_t", "8u 8u", 8),
    "checked_add_uint8_t": ("native/numeric/checked.rxfs", "checked_add_uint8_t", "1u 1u", 1),
    "checked_multiply_int16_t": ("native/numeric/checked.rxfs", "checked_multiply_int16_t", "2s 2s", 2),
    "checked_multiply_int32_t": ("native/numeric/checked.rxfs", "checked_multiply_int32_t", "4s 4s", 4),
    "checked_multiply_int64_t": ("native/numeric/checked.rxfs", "checked_multiply_int64_t", "8s 8s", 8),
    "checked_multiply_int8_t": ("native/numeric/checked.rxfs", "checked_multiply_int8_t", "1s 1s", 1),
    "checked_multiply_uint16_t": ("native/numeric/checked.rxfs", "checked_multiply_uint16_t", "2u 2u", 2),
    "checked_multiply_uint32_t": ("native/numeric/checked.rxfs", "checked_multiply_uint32_t", "4u 4u", 4),
    "checked_multiply_uint64_t": ("native/numeric/checked.rxfs", "checked_multiply_uint64_t", "8u 8u", 8),
    "checked_multiply_uint8_t": ("native/numeric/checked.rxfs", "checked_multiply_uint8_t", "1u 1u", 1),
    "checked_negate_int16_t": ("native/numeric/checked.rxfs", "checked_negate_int16_t", "2s", 2),
    "checked_negate_int32_t": ("native/numeric/checked.rxfs", "checked_negate_int32_t", "4s", 4),
    "checked_negate_int64_t": ("native/numeric/checked.rxfs", "checked_negate_int64_t", "8s", 8),
    "checked_negate_int8_t": ("native/numeric/checked.rxfs", "checked_negate_int8_t", "1s", 1),
    "checked_subtract_int16_t": ("native/numeric/checked.rxfs", "checked_subtract_int16_t", "2s 2s", 2),
    "checked_subtract_int32_t": ("native/numeric/checked.rxfs", "checked_subtract_int32_t", "4s 4s", 4),
    "checked_subtract_int64_t": ("native/numeric/checked.rxfs", "checked_subtract_int64_t", "8s 8s", 8),
    "checked_subtract_int8_t": ("native/numeric/checked.rxfs", "checked_subtract_int8_t", "1s 1s", 1),
    "checked_subtract_uint16_t": ("native/numeric/checked.rxfs", "checked_subtract_uint16_t", "2u 2u", 2),
    "checked_subtract_uint32_t": ("native/numeric/checked.rxfs", "checked_subtract_uint32_t", "4u 4u", 4),
    "checked_subtract_uint64_t": ("native/numeric/checked.rxfs", "checked_subtract_uint64_t", "8u 8u", 8),
    "checked_subtract_uint8_t": ("native/numeric/checked.rxfs", "checked_subtract_uint8_t", "1u 1u", 1),

    # native/numeric/saturating.rxfs -- saturating add/subtract/multiply
    "saturating_add_int16_t": ("native/numeric/saturating.rxfs", "saturating_add_int16_t", "2s 2s", 2),
    "saturating_add_int32_t": ("native/numeric/saturating.rxfs", "saturating_add_int32_t", "4s 4s", 4),
    "saturating_add_int64_t": ("native/numeric/saturating.rxfs", "saturating_add_int64_t", "8s 8s", 8),
    "saturating_add_int8_t": ("native/numeric/saturating.rxfs", "saturating_add_int8_t", "1s 1s", 1),
    "saturating_add_uint16_t": ("native/numeric/saturating.rxfs", "saturating_add_uint16_t", "2u 2u", 2),
    "saturating_add_uint32_t": ("native/numeric/saturating.rxfs", "saturating_add_uint32_t", "4u 4u", 4),
    "saturating_add_uint64_t": ("native/numeric/saturating.rxfs", "saturating_add_uint64_t", "8u 8u", 8),
    "saturating_add_uint8_t": ("native/numeric/saturating.rxfs", "saturating_add_uint8_t", "1u 1u", 1),
    "saturating_multiply_int16_t": ("native/numeric/saturating.rxfs", "saturating_multiply_int16_t", "2s 2s", 2),
    "saturating_multiply_int32_t": ("native/numeric/saturating.rxfs", "saturating_multiply_int32_t", "4s 4s", 4),
    "saturating_multiply_int64_t": ("native/numeric/saturating.rxfs", "saturating_multiply_int64_t", "8s 8s", 8),
    "saturating_multiply_int8_t": ("native/numeric/saturating.rxfs", "saturating_multiply_int8_t", "1s 1s", 1),
    "saturating_multiply_uint16_t": ("native/numeric/saturating.rxfs", "saturating_multiply_uint16_t", "2u 2u", 2),
    "saturating_multiply_uint32_t": ("native/numeric/saturating.rxfs", "saturating_multiply_uint32_t", "4u 4u", 4),
    "saturating_multiply_uint64_t": ("native/numeric/saturating.rxfs", "saturating_multiply_uint64_t", "8u 8u", 8),
    "saturating_multiply_uint8_t": ("native/numeric/saturating.rxfs", "saturating_multiply_uint8_t", "1u 1u", 1),
    "saturating_subtract_int16_t": ("native/numeric/saturating.rxfs", "saturating_subtract_int16_t", "2s 2s", 2),
    "saturating_subtract_int32_t": ("native/numeric/saturating.rxfs", "saturating_subtract_int32_t", "4s 4s", 4),
    "saturating_subtract_int64_t": ("native/numeric/saturating.rxfs", "saturating_subtract_int64_t", "8s 8s", 8),
    "saturating_subtract_int8_t": ("native/numeric/saturating.rxfs", "saturating_subtract_int8_t", "1s 1s", 1),
    "saturating_subtract_uint16_t": ("native/numeric/saturating.rxfs", "saturating_subtract_uint16_t", "2u 2u", 2),
    "saturating_subtract_uint32_t": ("native/numeric/saturating.rxfs", "saturating_subtract_uint32_t", "4u 4u", 4),
    "saturating_subtract_uint64_t": ("native/numeric/saturating.rxfs", "saturating_subtract_uint64_t", "8u 8u", 8),
    "saturating_subtract_uint8_t": ("native/numeric/saturating.rxfs", "saturating_subtract_uint8_t", "1u 1u", 1),

    # native/numeric/divrem.rxfs -- divide/remainder, refusing division by zero
    "divide_int16_t": ("native/numeric/divrem.rxfs", "divide_int16_t", "2s 2s", 2),
    "divide_int32_t": ("native/numeric/divrem.rxfs", "divide_int32_t", "4s 4s", 4),
    "divide_int64_t": ("native/numeric/divrem.rxfs", "divide_int64_t", "8s 8s", 8),
    "divide_int8_t": ("native/numeric/divrem.rxfs", "divide_int8_t", "1s 1s", 1),
    "divide_uint16_t": ("native/numeric/divrem.rxfs", "divide_uint16_t", "2u 2u", 2),
    "divide_uint32_t": ("native/numeric/divrem.rxfs", "divide_uint32_t", "4u 4u", 4),
    "divide_uint64_t": ("native/numeric/divrem.rxfs", "divide_uint64_t", "8u 8u", 8),
    "divide_uint8_t": ("native/numeric/divrem.rxfs", "divide_uint8_t", "1u 1u", 1),
    "remainder_int16_t": ("native/numeric/divrem.rxfs", "remainder_int16_t", "2s 2s", 2),
    "remainder_int32_t": ("native/numeric/divrem.rxfs", "remainder_int32_t", "4s 4s", 4),
    "remainder_int64_t": ("native/numeric/divrem.rxfs", "remainder_int64_t", "8s 8s", 8),
    "remainder_int8_t": ("native/numeric/divrem.rxfs", "remainder_int8_t", "1s 1s", 1),
    "remainder_uint16_t": ("native/numeric/divrem.rxfs", "remainder_uint16_t", "2u 2u", 2),
    "remainder_uint32_t": ("native/numeric/divrem.rxfs", "remainder_uint32_t", "4u 4u", 4),
    "remainder_uint64_t": ("native/numeric/divrem.rxfs", "remainder_uint64_t", "8u 8u", 8),
    "remainder_uint8_t": ("native/numeric/divrem.rxfs", "remainder_uint8_t", "1u 1u", 1),

    # native/numeric/convert.rxfs -- integer <-> integer conversions
    "convert_int16_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_int32_t", "2s", 4),
    "convert_int16_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_int64_t", "2s", 8),
    "convert_int16_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_int8_t", "2s", 1),
    "convert_int16_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_uint16_t", "2s", 2),
    "convert_int16_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_uint32_t", "2s", 4),
    "convert_int16_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_uint64_t", "2s", 8),
    "convert_int16_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_int16_t_to_uint8_t", "2s", 1),
    "convert_int32_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_int16_t", "4s", 2),
    "convert_int32_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_int64_t", "4s", 8),
    "convert_int32_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_int8_t", "4s", 1),
    "convert_int32_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_uint16_t", "4s", 2),
    "convert_int32_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_uint32_t", "4s", 4),
    "convert_int32_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_uint64_t", "4s", 8),
    "convert_int32_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_int32_t_to_uint8_t", "4s", 1),
    "convert_int64_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_int16_t", "8s", 2),
    "convert_int64_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_int32_t", "8s", 4),
    "convert_int64_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_int8_t", "8s", 1),
    "convert_int64_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_uint16_t", "8s", 2),
    "convert_int64_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_uint32_t", "8s", 4),
    "convert_int64_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_uint64_t", "8s", 8),
    "convert_int64_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_int64_t_to_uint8_t", "8s", 1),
    "convert_int8_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_int16_t", "1s", 2),
    "convert_int8_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_int32_t", "1s", 4),
    "convert_int8_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_int64_t", "1s", 8),
    "convert_int8_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_uint16_t", "1s", 2),
    "convert_int8_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_uint32_t", "1s", 4),
    "convert_int8_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_uint64_t", "1s", 8),
    "convert_int8_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_int8_t_to_uint8_t", "1s", 1),
    "convert_uint16_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_int16_t", "2u", 2),
    "convert_uint16_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_int32_t", "2u", 4),
    "convert_uint16_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_int64_t", "2u", 8),
    "convert_uint16_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_int8_t", "2u", 1),
    "convert_uint16_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_uint32_t", "2u", 4),
    "convert_uint16_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_uint64_t", "2u", 8),
    "convert_uint16_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_uint16_t_to_uint8_t", "2u", 1),
    "convert_uint32_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_int16_t", "4u", 2),
    "convert_uint32_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_int32_t", "4u", 4),
    "convert_uint32_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_int64_t", "4u", 8),
    "convert_uint32_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_int8_t", "4u", 1),
    "convert_uint32_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_uint16_t", "4u", 2),
    "convert_uint32_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_uint64_t", "4u", 8),
    "convert_uint32_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_uint32_t_to_uint8_t", "4u", 1),
    "convert_uint64_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_int16_t", "8u", 2),
    "convert_uint64_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_int32_t", "8u", 4),
    "convert_uint64_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_int64_t", "8u", 8),
    "convert_uint64_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_int8_t", "8u", 1),
    "convert_uint64_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_uint16_t", "8u", 2),
    "convert_uint64_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_uint32_t", "8u", 4),
    "convert_uint64_t_to_uint8_t": ("native/numeric/convert.rxfs", "convert_uint64_t_to_uint8_t", "8u", 1),
    "convert_uint8_t_to_int16_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_int16_t", "1u", 2),
    "convert_uint8_t_to_int32_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_int32_t", "1u", 4),
    "convert_uint8_t_to_int64_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_int64_t", "1u", 8),
    "convert_uint8_t_to_int8_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_int8_t", "1u", 1),
    "convert_uint8_t_to_uint16_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_uint16_t", "1u", 2),
    "convert_uint8_t_to_uint32_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_uint32_t", "1u", 4),
    "convert_uint8_t_to_uint64_t": ("native/numeric/convert.rxfs", "convert_uint8_t_to_uint64_t", "1u", 8),

    # native/float/float.rxfs -- float/double arithmetic, rounding, isnormal
    "checked_absolute_double": ("native/float/float.rxfs", "checked_absolute_double", "8f", 8),
    "checked_absolute_float": ("native/float/float.rxfs", "checked_absolute_float", "4f", 4),
    "checked_add_double": ("native/float/float.rxfs", "checked_add_double", "8f 8f", 8),
    "checked_add_float": ("native/float/float.rxfs", "checked_add_float", "4f 4f", 4),
    "checked_multiply_double": ("native/float/float.rxfs", "checked_multiply_double", "8f 8f", 8),
    "checked_multiply_float": ("native/float/float.rxfs", "checked_multiply_float", "4f 4f", 4),
    "checked_negate_double": ("native/float/float.rxfs", "checked_negate_double", "8f", 8),
    "checked_negate_float": ("native/float/float.rxfs", "checked_negate_float", "4f", 4),
    "checked_subtract_double": ("native/float/float.rxfs", "checked_subtract_double", "8f 8f", 8),
    "checked_subtract_float": ("native/float/float.rxfs", "checked_subtract_float", "4f 4f", 4),
    "divide_double": ("native/float/float.rxfs", "divide_double", "8f 8f", 8),
    "divide_float": ("native/float/float.rxfs", "divide_float", "4f 4f", 4),
    "equal_double": ("native/float/float.rxfs", "equal_double", "8f 8f", 1),
    "equal_float": ("native/float/float.rxfs", "equal_float", "4f 4f", 1),
    "less_double": ("native/float/float.rxfs", "less_double", "8f 8f", 1),
    "less_float": ("native/float/float.rxfs", "less_float", "4f 4f", 1),
    "maximum_double": ("native/float/float.rxfs", "maximum_double", "8f 8f", 8),
    "maximum_float": ("native/float/float.rxfs", "maximum_float", "4f 4f", 4),
    "minimum_double": ("native/float/float.rxfs", "minimum_double", "8f 8f", 8),
    "minimum_float": ("native/float/float.rxfs", "minimum_float", "4f 4f", 4),

    # native/float/convert.rxfs -- float/double <-> integer conversions
    "convert_double_to_float": ("native/float/convert.rxfs", "convert_double_to_float", "8f", 4),
    "convert_double_to_int16_t": ("native/float/convert.rxfs", "convert_double_to_int16_t", "8f", 2),
    "convert_double_to_int32_t": ("native/float/convert.rxfs", "convert_double_to_int32_t", "8f", 4),
    "convert_double_to_int64_t": ("native/float/convert.rxfs", "convert_double_to_int64_t", "8f", 8),
    "convert_double_to_int8_t": ("native/float/convert.rxfs", "convert_double_to_int8_t", "8f", 1),
    "convert_double_to_uint16_t": ("native/float/convert.rxfs", "convert_double_to_uint16_t", "8f", 2),
    "convert_double_to_uint32_t": ("native/float/convert.rxfs", "convert_double_to_uint32_t", "8f", 4),
    "convert_double_to_uint64_t": ("native/float/convert.rxfs", "convert_double_to_uint64_t", "8f", 8),
    "convert_double_to_uint8_t": ("native/float/convert.rxfs", "convert_double_to_uint8_t", "8f", 1),
    "convert_float_to_double": ("native/float/convert.rxfs", "convert_float_to_double", "4f", 8),
    "convert_float_to_int16_t": ("native/float/convert.rxfs", "convert_float_to_int16_t", "4f", 2),
    "convert_float_to_int32_t": ("native/float/convert.rxfs", "convert_float_to_int32_t", "4f", 4),
    "convert_float_to_int64_t": ("native/float/convert.rxfs", "convert_float_to_int64_t", "4f", 8),
    "convert_float_to_int8_t": ("native/float/convert.rxfs", "convert_float_to_int8_t", "4f", 1),
    "convert_float_to_uint16_t": ("native/float/convert.rxfs", "convert_float_to_uint16_t", "4f", 2),
    "convert_float_to_uint32_t": ("native/float/convert.rxfs", "convert_float_to_uint32_t", "4f", 4),
    "convert_float_to_uint64_t": ("native/float/convert.rxfs", "convert_float_to_uint64_t", "4f", 8),
    "convert_float_to_uint8_t": ("native/float/convert.rxfs", "convert_float_to_uint8_t", "4f", 1),
    "convert_int16_t_to_double": ("native/float/convert.rxfs", "convert_int16_t_to_double", "2s", 8),
    "convert_int16_t_to_float": ("native/float/convert.rxfs", "convert_int16_t_to_float", "2s", 4),
    "convert_int32_t_to_double": ("native/float/convert.rxfs", "convert_int32_t_to_double", "4s", 8),
    "convert_int32_t_to_float": ("native/float/convert.rxfs", "convert_int32_t_to_float", "4s", 4),
    "convert_int64_t_to_double": ("native/float/convert.rxfs", "convert_int64_t_to_double", "8s", 8),
    "convert_int64_t_to_float": ("native/float/convert.rxfs", "convert_int64_t_to_float", "8s", 4),
    "convert_int8_t_to_double": ("native/float/convert.rxfs", "convert_int8_t_to_double", "1s", 8),
    "convert_int8_t_to_float": ("native/float/convert.rxfs", "convert_int8_t_to_float", "1s", 4),
    "convert_uint16_t_to_double": ("native/float/convert.rxfs", "convert_uint16_t_to_double", "2u", 8),
    "convert_uint16_t_to_float": ("native/float/convert.rxfs", "convert_uint16_t_to_float", "2u", 4),
    "convert_uint32_t_to_double": ("native/float/convert.rxfs", "convert_uint32_t_to_double", "4u", 8),
    "convert_uint32_t_to_float": ("native/float/convert.rxfs", "convert_uint32_t_to_float", "4u", 4),
    "convert_uint64_t_to_double": ("native/float/convert.rxfs", "convert_uint64_t_to_double", "8u", 8),
    "convert_uint64_t_to_float": ("native/float/convert.rxfs", "convert_uint64_t_to_float", "8u", 4),
    "convert_uint8_t_to_double": ("native/float/convert.rxfs", "convert_uint8_t_to_double", "1u", 8),
    "convert_uint8_t_to_float": ("native/float/convert.rxfs", "convert_uint8_t_to_float", "1u", 4),

    # native/memory/memory.rxfs -- memory copy/move/fill/compare and UTF-8 validate
    "hash_bytes": ("native/memory/memory.rxfs", "hash_bytes", "8p 8u 8u", 8),
    "hash_u64": ("native/memory/memory.rxfs", "hash_u64", "8u 8u", 8),
    "load_u32": ("native/memory/memory.rxfs", "load_u32", "8p 8u 8u 4u 4u", 4),
    "memory_compare": ("native/memory/memory.rxfs", "memory_compare", "8p 8u 8p 8u 8u", 4),
    "memory_copy": ("native/memory/memory.rxfs", "memory_copy", "8p 8u 8p 8u 8u", 0),
    "memory_fill": ("native/memory/memory.rxfs", "memory_fill", "8p 8u 1u 8u", 0),
    "memory_move": ("native/memory/memory.rxfs", "memory_move", "8p 8u 8p 8u 8u", 0),
    "memory_zero": ("native/memory/memory.rxfs", "memory_zero", "8p 8u 8u", 0),
    "store_u32": ("native/memory/memory.rxfs", "store_u32", "8p 8u 8u 4u 4u 4u", 0),
    "utf8_validate": ("native/memory/memory.rxfs", "utf8_validate", "8p 8u", 8),

    # native/runtime/runtime.rxfs -- the runtime family
    "runtime_allocate_prepare": ("native/runtime/runtime.rxfs", "runtime_allocate_prepare", "!pqqqqqpp", 0),
    "runtime_append": ("native/runtime/runtime.rxfs", "runtime_append", "!pHpqq", 0),
    "runtime_begin_private": ("native/runtime/runtime.rxfs", "runtime_begin_private", "!pHqp", 0),
    "runtime_borrow": ("native/runtime/runtime.rxfs", "runtime_borrow", "!pHq", 0),
    "runtime_cleanup": ("native/runtime/runtime.rxfs", "runtime_cleanup", "!pp", 0),
    "runtime_format_u32": ("native/runtime/runtime.rxfs", "runtime_format_u32", "!pHpd", 0),
    "runtime_hash_map_lookup": ("native/runtime/runtime.rxfs", "runtime_hash_map_lookup", "!pHpqqp", 0),
    "runtime_hash_set_lookup": ("native/runtime/runtime.rxfs", "runtime_hash_set_lookup", "!pHpqqp", 0),
    "runtime_pin": ("native/runtime/runtime.rxfs", "runtime_pin", "!pH", 0),
    "runtime_publish": ("native/runtime/runtime.rxfs", "runtime_publish", "!pp", 0),
    "runtime_read": ("native/runtime/runtime.rxfs", "runtime_read", "!pHpqqp", 0),
    "runtime_release": ("native/runtime/runtime.rxfs", "runtime_release", "!pH", 0),
    "runtime_release_borrow": ("native/runtime/runtime.rxfs", "runtime_release_borrow", "!pHq", 0),
    "runtime_reserve": ("native/runtime/runtime.rxfs", "runtime_reserve", "!pHpq", 0),
    "runtime_resolve": ("native/runtime/runtime.rxfs", "runtime_resolve", "!pHqp", 0),
    "runtime_resolve_typed": ("native/runtime/runtime.rxfs", "runtime_resolve_typed", "!pHpqqp", 0),
    "runtime_rollback": ("native/runtime/runtime.rxfs", "runtime_rollback", "!pp", 0),
    "runtime_search": ("native/runtime/runtime.rxfs", "runtime_search", "!pHqHqpp", 0),
    "runtime_unpin": ("native/runtime/runtime.rxfs", "runtime_unpin", "!pH", 0),
    "runtime_write": ("native/runtime/runtime.rxfs", "runtime_write", "!pHpqqq", 0),

    # native/safe/safe.rxfs -- safe_point
    "safe_point": ("native/safe/safe.rxfs", "safe_point", "!pp", 0),

    # native/uart/uart.rxfs -- uart_pump, one fn per machine
    "uart_pump": ("native/uart/uart.rxfs", "uart_pump", "!pp", 0),
}


def fn_of(key: str, machine: str) -> str:
    """The `.rxfs` fn a key compiles: the row's fn, except uart_pump, which has one
    fn per machine (the seed lowers its port calls per target). Each program source
    has its one fn, named for its key."""
    if key == "uart_pump":
        return f"uart_pump_{TARGET[machine]}"
    if key in PROGRAMS:
        return key
    return SOURCES[key][1]


# The three programs whose shapes their own builders hold (the interpreter of
# native/graph.rxfs, the compiler of native/compile.rxfs, the pump of
# native/live.rxfs); a key is one of these or a row of SOURCES.
PROGRAMS = ("graph_run", "compile_program", "live_pump")

# The `.rxfs` file each program source lives in; with the row's source this is the
# build history the Source objects of the image carry (docs/31 §3).
PROGRAM_SOURCE = {
    "graph_run": "native/graph.rxfs",
    "compile_program": "native/compile.rxfs",
    "live_pump": "native/live.rxfs",
}


def source_of(key: str) -> str:
    """The `.rxfs` file a key is compiled from: the row's source, or a program's."""
    if key in SOURCES:
        return SOURCES[key][0]
    return PROGRAM_SOURCE[key]


def is_seed(key: str) -> bool:
    """True if this key is a snippet the seed compiles (every key is)."""
    return key in SOURCES or key in PROGRAMS


def shape_of(key: str) -> tuple[str, int]:
    """``(shape, store width)`` of a key: its C operands as the seed spells them."""
    if key in SOURCES:
        return SOURCES[key][2], SOURCES[key][3]
    if key == "graph_run":
        import graph_build

        return graph_build.SELF, graph_build.STOREW
    if key == "compile_program":
        import compile_build

        return compile_build.SELF, compile_build.STOREW
    if key == "live_pump":
        import live_build

        return live_build.SELF, live_build.STOREW
    raise KeyError(key)


def keys() -> list[str]:
    """Every snippet key, sorted: what `native_build.build()` compiles."""
    return sorted([*SOURCES, *PROGRAMS])


# The wasm32 value type a C operand is passed as: a real pointer is an i32 address,
# an object handle (its 24 bytes) goes by address, a u64 (or a 64-bit address it
# carries) is i64, the narrower integers are i32, the reals f32/f64.
TOKEN_WASM = {
    "1u": "i32", "1s": "i32", "2u": "i32", "2s": "i32",
    "4u": "i32", "4s": "i32", "8u": "i64", "8s": "i64",
    "8p": "i32", "4f": "f32", "8f": "f64",
}  # fmt: skip
LETTER_WASM = {"p": "i32", "H": "i32", "q": "i64", "d": "i32", "w": "i32", "b": "i32"}


def wasm_params(key: str) -> tuple[str, ...]:
    """The wasm32 value types the C function of a key takes.

    The derivation is from the key's shape -- the C ABI of the key as the seed
    spells it (docs/29), the one place it is written down. A shape spelled in
    operands takes the store of its result after them; the letters form spells its
    parameters whole, an out pointer among them. The result is always a status
    (``i32``), written through the out parameter.
    """
    shape, storew = shape_of(key)
    if shape.startswith("!"):
        return tuple(LETTER_WASM[letter] for letter in shape[1:])
    params = [TOKEN_WASM[token] for token in shape.split()]
    if storew:
        params.append("i32")
    return tuple(params)


def code(machine: str, key: str) -> str:
    """The hex of the object-ABI code for ``key`` on ``machine``."""
    if key == "graph_run":
        # native/graph.rxfs (docs/29): the graph interpreter itself, the same seed
        # on every machine -- this is where its bytes come from.
        import graph_build

        return graph_build.code(machine)
    if key == "compile_program":
        # native/compile.rxfs: the compiler that writes a program's machine code.
        import compile_build

        return compile_build.code(machine)
    if key == "live_pump":
        # native/live.rxfs: the pump that serves the frames of a channel.
        import live_build

        return live_build.code(machine)
    source, fn, shape, storew = SOURCES[key]
    argv = [str(seed_bin()), "codegen", TARGET[machine], "object", str(storew),
            "#" + fn_of(key, machine)]
    if shape.startswith("!"):
        argv.append(shape)
        argv.extend(f"@{letters}" for letters in call_type_letters())
    else:
        argv.extend(shape.split())
    return seed_cache.run(argv, (PACKAGE / source).read_text())


def compile_body(machine: str, body: str, params: str, specs: str, storew: int) -> str:
    """The hex of one `.rxfs` body compiled to the object-ABI code of ``machine``."""
    source = f"fn f({params}) {{ {body} }}\n"
    argv = [str(seed_bin()), "codegen", TARGET[machine], "object", str(storew), "!" + specs,
            *(f"@{letters}" for letters in call_type_letters())]
    return seed_cache.run(argv, source)


def wasm_body(key: str) -> bytes:
    """The wasm32 function body (local declarations + expression) for a key."""
    return bytes.fromhex(code("wasm32", key))


# The C parameters of every leaf of native/graph.h, one letter each -- the same strings
# tests/scenario.py SPEC and tests/graph_world.py NUMERIC write, and the ones
# native/graph.c casts the operands to. `p` pointer or context, `q` u64, `d` u32, `w`
# u32, `b` u8, `H` object handle (its three words). A call type of the codegen is this
# spelling: `call_pHpqqp` takes (context, handle, u64, u64, out).
LEAF_LETTERS = {
    "wrapping_add_uint64_t": "qqp",
    "less_uint64_t": "qqp",
    "bit_not_uint64_t": "qp",
    "hash_u64": "qqp",
    "runtime_hash_map_lookup": "pHpqqp",
    "runtime_hash_set_lookup": "pHpqqp",
    "runtime_begin_private": "pHqp",
    "runtime_resolve": "pHqp",
    "runtime_allocate_prepare": "pqqqqqpp",
    "runtime_publish": "pp",
    "runtime_rollback": "pp",
    "runtime_borrow": "pHq",
    "runtime_release_borrow": "pHq",
    "runtime_pin": "pH",
    "runtime_unpin": "pH",
    "runtime_read": "pHpqqp",
    "runtime_write": "pHpqqq",
    "runtime_append": "pHpqq",
    "runtime_format_u32": "pHpd",
    "runtime_search": "pHqHqpp",
    "runtime_cleanup": "pp",
    "checked_add_uint32_t": "ddp",
    "checked_subtract_uint32_t": "ddp",
    "checked_multiply_uint32_t": "ddp",
    "divide_uint32_t": "ddp",
    "memory_copy": "pqpqq",
    "memory_compare": "pqpqqp",
    "memory_fill": "pqbq",
    "live_pump": "pp",
    "uart_pump": "pp",
    "compile_program": "pp",
    "wrapping_subtract_uint64_t": "qqp",
    "bit_and_uint64_t": "qqp",
    "bit_or_uint64_t": "qqp",
    "shift_left_uint64_t": "qdp",
    "shift_right_uint64_t": "qdp",
    "equal_uint64_t": "qqp",
    "safe_point": "pp",
    "wrapping_multiply_uint64_t": "qqp",
    "divide_uint64_t": "qqp",
    "remainder_uint64_t": "qqp",
    "divide_int64_t": "qqp",
    "remainder_int64_t": "qqp",
    "checked_multiply_uint64_t": "qqp",
    "checked_multiply_int64_t": "qqp",
    "remainder_uint32_t": "ddp",
    "divide_int32_t": "ddp",
    "remainder_int32_t": "ddp",
    "graph_run": "pHqqp",
}


def call_type_letters() -> list[str]:
    """The call types a codegen run must be given: the letters of the leaves in the
    order of their ids, ONE ENTRY PER LEAF. A composed module's type section has one
    type per leaf in this order (tests/wasm.py module_of), so a `call_indirect` names
    its type by the position of the leaf that has these letters; the codegen takes
    the first entry that matches the call's shape. A test asserts this table and
    tests' letters() agree (tests/test_graph_build.py)."""
    import graph_header

    return [
        LEAF_LETTERS[name]
        for name, _ in sorted(graph_header.leaves().items(), key=lambda item: item[1])
    ]
