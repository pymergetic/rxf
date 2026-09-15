"""Differential execution proofs for exact extracted x86-64 bytes."""

from __future__ import annotations

import ctypes
import math
import mmap
import random

from pymergetic.rxf.generated_native import AARCH64, X86_64
from pymergetic.rxf.model.numeric import numeric_nodes
from pymergetic.rxf.ty.builtins import CODE_TYPE, FUNCTION_TYPE, NUMERIC_CONTRACT_TYPE


def _fn(symbol, argtypes, outtype):
    raw = X86_64[symbol]
    mem = mmap.mmap(
        -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
    )
    mem.write(raw)
    address = ctypes.addressof(ctypes.c_char.from_buffer(mem))
    fn = ctypes.CFUNCTYPE(ctypes.c_uint32, *argtypes, ctypes.POINTER(outtype))(address)
    fn._mem = mem  # type: ignore[attr-defined]
    return fn


def test_manifests_have_identical_complete_symbol_coverage():
    assert set(X86_64) == set(AARCH64) and len(X86_64) == 286
    assert all(X86_64[n] and AARCH64[n] for n in X86_64)


def test_exhaustive_u8_checked_wrapping_saturating_and_output_unchanged():
    specs = {
        "checked_add": lambda a, b: (1, None) if a + b > 255 else (0, a + b),
        "wrapping_add": lambda a, b: (0, (a + b) & 255),
        "saturating_add": lambda a, b: (0, min(255, a + b)),
        "checked_subtract": lambda a, b: (1, None) if a < b else (0, a - b),
        "wrapping_subtract": lambda a, b: (0, (a - b) & 255),
        "saturating_subtract": lambda a, b: (0, max(0, a - b)),
        "checked_multiply": lambda a, b: (1, None) if a * b > 255 else (0, a * b),
        "wrapping_multiply": lambda a, b: (0, (a * b) & 255),
        "saturating_multiply": lambda a, b: (0, min(255, a * b)),
    }
    for op, oracle in specs.items():
        fn = _fn(f"{op}_uint8_t", (ctypes.c_uint8, ctypes.c_uint8), ctypes.c_uint8)
        for a in range(256):
            for b in range(256):
                out = ctypes.c_uint8(0xA5)
                status = fn(a, b, ctypes.byref(out))
                expected, value = oracle(a, b)
                assert status == expected
                assert out.value == (0xA5 if value is None else value)


def test_signed_division_remainder_and_shift_boundaries():
    div = _fn("divide_int32_t", (ctypes.c_int32, ctypes.c_int32), ctypes.c_int32)
    rem = _fn("remainder_int32_t", (ctypes.c_int32, ctypes.c_int32), ctypes.c_int32)
    shr = _fn("shift_right_int32_t", (ctypes.c_int32, ctypes.c_uint32), ctypes.c_int32)
    for a, b in [(-7, 3), (7, -3), (-7, -3), (7, 3)]:
        out = ctypes.c_int32()
        assert div(a, b, ctypes.byref(out)) == 0
        q = math.trunc(a / b)
        assert out.value == q
        assert rem(a, b, ctypes.byref(out)) == 0 and out.value == a - q * b
    out = ctypes.c_int32(77)
    assert shr(-4, 31, ctypes.byref(out)) == 0 and out.value == -1
    out.value = 77
    assert shr(-4, 32, ctypes.byref(out)) == 3 and out.value == 77


def test_wider_integer_properties():
    fn = _fn("checked_add_int64_t", (ctypes.c_int64, ctypes.c_int64), ctypes.c_int64)
    rng = random.Random(5)
    for _ in range(2000):
        a = rng.randrange(-(1 << 63), 1 << 63)
        b = rng.randrange(-(1 << 63), 1 << 63)
        out = ctypes.c_int64(123)
        status = fn(a, b, ctypes.byref(out))
        v = a + b
        if -(1 << 63) <= v < (1 << 63):
            assert status == 0 and out.value == v
        else:
            assert status == 1 and out.value == 123


def test_v5_corpus_contract_and_code_counts():
    nodes = numeric_nodes()
    assert sum(n.type_id == NUMERIC_CONTRACT_TYPE for n in nodes) == 286
    assert sum(n.type_id == CODE_TYPE for n in nodes) == 572
    assert sum(n.type_id == FUNCTION_TYPE for n in nodes) == 309


def test_checkout_oracle_is_570():
    unit_price, quantity, discount, shipping, tax_percent = 125, 4, 50, 25, 20
    subtotal = unit_price * quantity
    taxable = subtotal - discount + shipping
    tax = taxable * tax_percent // 100
    assert taxable + tax == 570
