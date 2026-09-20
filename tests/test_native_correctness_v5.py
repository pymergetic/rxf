"""Boundary, conversion, structure, and AArch64 execution proofs."""

import ctypes
import mmap
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from pymergetic.rxf.generated_native import AARCH64, X86_64
from pymergetic.rxf.model.numeric import numeric_nodes
from pymergetic.rxf.ty.builtins import FUNCTION_TYPE

ROOT = Path(__file__).parents[1]
TYPES = {
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
}


def fn(name, args, out):
    raw = X86_64[name]
    memory = mmap.mmap(
        -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
    )
    memory.write(raw)
    result = ctypes.CFUNCTYPE(ctypes.c_uint32, *args, ctypes.POINTER(out))(
        ctypes.addressof(ctypes.c_char.from_buffer(memory))
    )
    result.memory = memory  # type: ignore[attr-defined]
    return result


def test_signed_shift_division_and_remainder_edges():
    for name, t, width in (
        ("int8_t", ctypes.c_int8, 8),
        ("int16_t", ctypes.c_int16, 16),
        ("int32_t", ctypes.c_int32, 32),
        ("int64_t", ctypes.c_int64, 64),
    ):
        out = t(19)
        assert (
            fn(f"shift_left_{name}", (t, ctypes.c_uint32), t)(-1, 1, ctypes.byref(out))
            == 0
            and out.value == -2
        )
        assert (
            fn(f"shift_right_{name}", (t, ctypes.c_uint32), t)(-4, 1, ctypes.byref(out))
            == 0
            and out.value == -2
        )
        out.value = 19
        assert (
            fn(f"shift_left_{name}", (t, ctypes.c_uint32), t)(
                -1, width, ctypes.byref(out)
            )
            == 3
            and out.value == 19
        )
        minimum = -(1 << (width - 1))
        out.value = 19
        assert (
            fn(f"divide_{name}", (t, t), t)(minimum, -1, ctypes.byref(out)) == 1
            and out.value == 19
        )
        assert (
            fn(f"remainder_{name}", (t, t), t)(minimum, -1, ctypes.byref(out)) == 1
            and out.value == 19
        )


def test_float_nonfinite_and_signed_zero():
    for name, t, code, bits in (
        ("float", ctypes.c_float, "<I", 32),
        ("double", ctypes.c_double, "<Q", 64),
    ):
        for operation in ("equal", "less"):
            out = ctypes.c_uint8(165)
            assert (
                fn(f"{operation}_{name}", (t, t), ctypes.c_uint8)(
                    t(float("nan")), t(1), ctypes.byref(out)
                )
                == 4
                and out.value == 165
            )
        out = t(99)
        assert fn(f"minimum_{name}", (t, t), t)(
            t(-0.0), t(0.0), ctypes.byref(out)
        ) == 0 and struct.unpack(code, bytes(out))[0] == 1 << (bits - 1)
        assert (
            fn(f"maximum_{name}", (t, t), t)(t(-0.0), t(0.0), ctypes.byref(out)) == 0
            and struct.unpack(code, bytes(out))[0] == 0
        )
        out.value = 99
        assert (
            fn(f"minimum_{name}", (t, t), t)(t(float("inf")), t(0), ctypes.byref(out))
            == 4
            and out.value == 99
        )


def test_all_90_conversion_symbols_and_refusals():
    for source, st in TYPES.items():
        for destination, dt in TYPES.items():
            if source == destination:
                continue
            out = dt(7)
            assert (
                fn(f"convert_{source}_to_{destination}", (st,), dt)(
                    st(0), ctypes.byref(out)
                )
                == 0
                and out.value == 0
            )
    out = ctypes.c_int8(7)
    assert (
        fn("convert_uint16_t_to_int8_t", (ctypes.c_uint16,), ctypes.c_int8)(
            128, ctypes.byref(out)
        )
        == 6
        and out.value == 7
    )
    out32 = ctypes.c_int32(7)
    convert = fn("convert_double_to_int32_t", (ctypes.c_double,), ctypes.c_int32)
    assert convert(float("nan"), ctypes.byref(out32)) == 4 and out32.value == 7
    assert convert(1e100, ctypes.byref(out32)) == 6 and out32.value == 7
    assert convert(1.5, ctypes.byref(out32)) == 5 and out32.value == 7
    outd = ctypes.c_double(7)
    assert (
        fn("convert_uint64_t_to_double", (ctypes.c_uint64,), ctypes.c_double)(
            (1 << 53) + 1, ctypes.byref(outd)
        )
        == 5
        and outd.value == 7
    )


def test_manifest_contract_code_bijection():
    from pymergetic.rxf.execution.decode import decode_code, decode_function
    from pymergetic.rxf.model.execution import CallRole, FunctionImplementation

    nodes = numeric_nodes()
    by_id = {n.id: n for n in nodes}
    used = {817: set(), 818: set(), 823: set()}
    manifests = {817: X86_64, 818: AARCH64, 823: AARCH64}
    functions = [
        n
        for n in nodes
        if n.type_id == FUNCTION_TYPE
        and decode_function(n).implementation == FunctionImplementation.CODE_BACKED
    ]
    assert len(functions) == 286
    for function in functions:
        refs = lambda role, function=function: [
            r.target for r in function.refs if r.to_off == int(role)
        ]
        assert (
            len(refs(CallRole.NUMERIC_CONTRACT)) == 1
            and len(refs(CallRole.IMPLEMENTATION)) == 3
        )
        for code_id in refs(CallRole.IMPLEMENTATION):
            code = by_id[code_id]
            record = decode_code(code)
            used[record.target_id].add(function.name)
            assert record.raw_bytes == manifests[record.target_id][function.name]
            assert (
                len([r for r in code.refs if r.to_off == int(CallRole.ABI_SIGNATURE)])
                == 1
            )
    assert used[817] == set(X86_64)
    assert used[818] == used[823] == set(AARCH64)
    by_function_target = {
        (decode_code(node).owner_function, decode_code(node).target_id): (
            node.id,
            decode_code(node),
        )
        for node in nodes
        if node.kind.name == "CODE"
    }
    for function in functions:
        uefi_id, uefi = by_function_target[(function.id, 818)]
        linux_id, linux = by_function_target[(function.id, 823)]
        assert uefi.raw_bytes == linux.raw_bytes
        assert uefi.semantic_digest == linux.semantic_digest
        uefi_abi = next(
            ref.target
            for ref in by_id[uefi_id].refs
            if ref.to_off == int(CallRole.ABI_SIGNATURE)
        )
        linux_abi = next(
            ref.target
            for ref in by_id[linux_id].refs
            if ref.to_off == int(CallRole.ABI_SIGNATURE)
        )
        assert uefi_id != linux_id and uefi_abi != linux_abi


def test_qemu_executes_exact_aarch64_bytes(tmp_path):
    qemu = shutil.which("qemu-system-aarch64")
    if qemu is None:
        pytest.skip("qemu-system-aarch64 is unavailable")
    subprocess.run(
        [sys.executable, str(ROOT / "tools/generate_qemu_blobs.py")], check=True
    )
    flags = [
        "-target",
        "aarch64-none-elf",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
        "-fno-unwind-tables",
        "-fno-asynchronous-unwind-tables",
    ]
    subprocess.run(
        [
            "clang-18",
            *flags,
            "-O2",
            "-std=c11",
            "-c",
            str(ROOT / "native/qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    for source, out in (
        (ROOT / "native/qemu_start.S", "s.o"),
        (ROOT / "generated/qemu_aarch64_blobs.S", "b.o"),
    ):
        subprocess.run(
            [
                "clang-18",
                "-target",
                "aarch64-none-elf",
                "-c",
                str(source),
                "-o",
                str(tmp_path / out),
            ],
            check=True,
        )
    elf = tmp_path / "qemu.elf"
    subprocess.run(
        [
            "ld.lld-18",
            "-T",
            str(ROOT / "native/qemu.ld"),
            str(tmp_path / "s.o"),
            str(tmp_path / "h.o"),
            str(tmp_path / "b.o"),
            "-o",
            str(elf),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            qemu,
            "-M",
            "virt",
            "-cpu",
            "cortex-a57",
            "-nographic",
            "-semihosting-config",
            "enable=on,target=native",
            "-device",
            f"loader,file={elf}",
            "-device",
            "loader,addr=0x40200000,cpu-num=0",
        ],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0 and "RXF-QEMU-PASS" in result.stdout + result.stderr
