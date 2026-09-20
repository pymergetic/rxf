"""Execute exact manifest bytes attached to stdlib Code objects."""

import ctypes
import mmap
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pymergetic.rxf.execution.decode import decode_code
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.generated_stdlib_native import STDLIB_AARCH64, STDLIB_X86_64
from pymergetic.rxf.model.stdlib_corpus import NATIVE_SYMBOLS
from pymergetic.rxf.model.target import (
    AARCH64_LINUX_TARGET_ID,
    AARCH64_UEFI_TARGET_ID,
    X86_64_LINUX_TARGET_ID,
)

ROOT = Path(__file__).parents[1]


class _NativeFunction(Protocol):
    def __call__(self, *args: object) -> int: ...


@dataclass(frozen=True)
class _ExecutableFunction:
    function: _NativeFunction
    memory: mmap.mmap

    def __call__(self, *args: object) -> int:
        return self.function(*args)


def _exact_function(raw, args, output) -> _ExecutableFunction:
    memory = mmap.mmap(
        -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
    )
    memory.write(raw)
    function = ctypes.CFUNCTYPE(ctypes.c_uint32, *args, ctypes.POINTER(output))(
        ctypes.addressof(ctypes.c_char.from_buffer(memory))
    )
    return _ExecutableFunction(function, memory)


def test_starter_code_bytes_match_both_manifests_and_x86_executes_exact_hash():
    container = starter_template().build()
    by_name = {
        node.name: node
        for node in container.nodes
        if node.type_id == 14 and node.name in NATIVE_SYMBOLS
    }
    used = {
        X86_64_LINUX_TARGET_ID: set(),
        AARCH64_UEFI_TARGET_ID: set(),
        AARCH64_LINUX_TARGET_ID: set(),
    }
    for function_name, symbol in NATIVE_SYMBOLS.items():
        function = by_name[function_name]
        code_nodes = [
            node
            for node in container.nodes
            if node.parent == function.id and node.kind.name == "CODE"
        ]
        code = [decode_code(node) for node in code_nodes]
        assert len(code) == 3
        for item in code:
            expected = (
                STDLIB_X86_64
                if item.target_id == X86_64_LINUX_TARGET_ID
                else STDLIB_AARCH64
            )
            assert item.raw_bytes == expected[symbol]
            used[item.target_id].add(symbol)
        by_target = {item.target_id: item for item in code}
        assert (
            by_target[AARCH64_UEFI_TARGET_ID].raw_bytes
            == by_target[AARCH64_LINUX_TARGET_ID].raw_bytes
        )
        assert (
            by_target[AARCH64_UEFI_TARGET_ID].semantic_digest
            == by_target[AARCH64_LINUX_TARGET_ID].semantic_digest
        )
        code_ids = {decode_code(node).target_id: node.id for node in code_nodes}
        assert code_ids[AARCH64_UEFI_TARGET_ID] != code_ids[AARCH64_LINUX_TARGET_ID]
    assert used[X86_64_LINUX_TARGET_ID] == set(NATIVE_SYMBOLS.values())
    assert used[AARCH64_UEFI_TARGET_ID] == set(NATIVE_SYMBOLS.values())
    assert used[AARCH64_LINUX_TARGET_ID] == set(NATIVE_SYMBOLS.values())
    payload = (ctypes.c_uint8 * 3)(1, 2, 3)
    out = ctypes.c_uint64(99)
    fn = _exact_function(
        STDLIB_X86_64["hash_bytes"],
        (ctypes.POINTER(ctypes.c_uint8), ctypes.c_uint64, ctypes.c_uint64),
        ctypes.c_uint64,
    )
    assert fn(payload, 3, 0, ctypes.byref(out)) == 0
    assert out.value == 15035938162879559083


def test_qemu_system_certifies_exact_aarch64_stdlib_bytes(tmp_path):
    qemu = shutil.which("qemu-system-aarch64")
    assert qemu is not None, "qemu-system-aarch64 is required for stdlib certification"
    subprocess.run(
        ["python3", str(ROOT / "tools/generate_stdlib_qemu_blobs.py")], check=True
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
            str(ROOT / "native/stdlib_qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    for source, output in (
        (ROOT / "native/qemu_start.S", "s.o"),
        (ROOT / "generated/stdlib_qemu_aarch64_blobs.S", "b.o"),
    ):
        subprocess.run(
            [
                "clang-18",
                "-target",
                "aarch64-none-elf",
                "-c",
                str(source),
                "-o",
                str(tmp_path / output),
            ],
            check=True,
        )
    elf = tmp_path / "stdlib.elf"
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
    assert (
        result.returncode == 0
        and "RXF-STDLIB-QEMU-PASS" in result.stdout + result.stderr
    )
