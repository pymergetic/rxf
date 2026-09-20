"""Direct OVMF/AAVMF certification of shared-starter UEFI images."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.executable import build_executable_image
from pymergetic.rxf.executable.pe import (
    MACHINE_AARCH64,
    MACHINE_X86_64,
    check_pe32_plus,
    extract_pe_rxf,
    format_pe32_plus,
    install_uefi_startup,
)
from pymergetic.rxf.executable.targets import resolve_executable_target
from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template

ROOT = Path(__file__).parents[1]
EXPECTED_CONSOLE = b"570\r\n"


def _required(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        pytest.fail(f"{name} is required for direct UEFI certification")
    return path


def _startup(work: Path, arch: str) -> bytes:
    obj = work / f"{arch}.o"
    linked = work / f"{arch}.elf"
    raw = work / f"{arch}.bin"
    triple = "x86_64-none-elf" if arch == "x86_64" else "aarch64-none-elf"
    subprocess.run(
        [
            _required("clang-18"),
            "-target",
            triple,
            "-ffreestanding",
            "-c",
            ROOT / f"native/executable/uefi/{arch}_start.S",
            "-o",
            obj,
        ],
        check=True,
    )
    source = obj
    if arch == "aarch64":
        subprocess.run(
            [_required("ld.lld-18"), "-Ttext=0", "--entry=efi_main", "-o", linked, obj],
            check=True,
        )
        source = linked
    subprocess.run(
        [_required("llvm-objcopy-18"), f"--dump-section=.text={raw}", source],
        check=True,
    )
    return raw.read_bytes()


def _efi(work: Path, target: int, arch: str, machine: int) -> Path:
    image = build_executable_image(
        starter_template().build(),
        STARTER_MAIN_FUNCTION_ID,
        resolve_executable_target(target),
    )
    startup = _startup(work, arch)
    bound = install_uefi_startup(image, startup)
    blob = format_pe32_plus(bound)
    assert blob == format_pe32_plus(bound)
    check_pe32_plus(blob, expected_machine=machine)
    graph = next(s for s in image.segments if s.kind.name == "RXF_IMAGE")
    assert extract_pe_rxf(blob) == graph.data
    assert len(graph.data) > 4 * 1024 * 1024
    efi = work / ("BOOTX64.EFI" if arch == "x86_64" else "BOOTAA64.EFI")
    efi.write_bytes(blob)
    report = subprocess.run(
        [
            _required("llvm-readobj-18"),
            "--file-headers",
            "--sections",
            "--coff-basereloc",
            efi,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "Subsystem: IMAGE_SUBSYSTEM_EFI_APPLICATION" in report
    assert "Type: DIR64" in report
    return efi


def _esp(work: Path, efi: Path) -> Path:
    esp = work / "esp.img"
    with esp.open("wb") as stream:
        stream.truncate(64 * 1024 * 1024)
    subprocess.run([_required("mkfs.vfat"), esp], check=True, capture_output=True)
    subprocess.run([_required("mmd"), "-i", esp, "::/EFI", "::/EFI/BOOT"], check=True)
    subprocess.run(
        [_required("mcopy"), "-i", esp, efi, f"::/EFI/BOOT/{efi.name}"], check=True
    )
    return esp


def _boot(
    command: list[os.PathLike[str] | str], timeout: float, allow_timeout: bool
) -> tuple[bytes, int]:
    process = subprocess.Popen(
        [str(item) for item in command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, 15)
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, 9)
            output, _ = process.communicate()
        if not allow_timeout:
            pytest.fail(f"QEMU did not terminate; output={output!r}")
    return output, process.returncode


def test_target824_boots_ovmf_with_exact_output_and_debug_exit(tmp_path: Path) -> None:
    efi = _efi(tmp_path, 824, "x86_64", MACHINE_X86_64)
    esp = _esp(tmp_path, efi)
    vars_copy = tmp_path / "OVMF_VARS_4M.fd"
    shutil.copyfile("/usr/share/OVMF/OVMF_VARS_4M.fd", vars_copy)
    output, code = _boot(
        [
            _required("qemu-system-x86_64"),
            "-machine",
            "q35",
            "-nodefaults",
            "-no-reboot",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-device",
            "isa-debug-exit,iobase=0x501,iosize=4",
            "-drive",
            "if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd",
            "-drive",
            f"if=pflash,format=raw,file={vars_copy}",
            "-drive",
            f"format=raw,file={esp}",
        ],
        30,
        False,
    )
    assert EXPECTED_CONSOLE in output
    assert output.count(EXPECTED_CONSOLE) == 1
    assert code == 33  # isa-debug-exit: (pass marker 16 << 1) | 1


def test_target818_boots_aavmf_with_exact_output_and_cleanup(tmp_path: Path) -> None:
    efi = _efi(tmp_path, 818, "aarch64", MACHINE_AARCH64)
    esp = _esp(tmp_path, efi)
    vars_copy = tmp_path / "AAVMF_VARS.fd"
    shutil.copyfile("/usr/share/AAVMF/AAVMF_VARS.fd", vars_copy)
    output, code = _boot(
        [
            _required("qemu-system-aarch64"),
            "-machine",
            "virt",
            "-cpu",
            "cortex-a57",
            "-nodefaults",
            "-no-reboot",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-semihosting-config",
            "enable=on,target=native",
            "-drive",
            "if=pflash,format=raw,readonly=on,file=/usr/share/AAVMF/AAVMF_CODE.fd",
            "-drive",
            f"if=pflash,format=raw,file={vars_copy}",
            "-drive",
            f"if=none,format=raw,file={esp},id=esp",
            "-device",
            "virtio-blk-device,drive=esp",
        ],
        30,
        True,
    )
    assert EXPECTED_CONSOLE in output
    assert output.count(EXPECTED_CONSOLE) == 1
    assert code == 16  # semihosting extended-exit pass marker
