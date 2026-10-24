"""The BIOS image and the UEFI application booted, and the line they report.

The proof of a boot is the machine: qemu-system-x86_64 runs the disk image from its
boot sector and the EFI application under OVMF. Each seat reports `rxf result <status>
<out>` on its channel (COM1 for firmware) -- the line pinned byte for byte against the
interpreter run of the same entry (tests/image_world.py) -- and answers the coarse byte
on the magic port 0xf4, which QEMU's isa-debug-exit turns into the exit of the machine
(33 for a run that answered 0). When QEMU or OVMF is missing the tests skip and say
why; no test pretends a boot happened.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.binary import builder, package
from pymergetic.rxftk.core import tree

from . import image_world as run
from .snippets import STARTER

MAIN = "pymergetic.rxf.application.main"
ANSWER = 33  # RXF_WRAP_MAGIC_OK on port 0xf4, as isa-debug-exit reports it
# OVMF can need more than 120s to reach the wrapper when the machine runs the
# differential seats in parallel (observed under load ~29 on 20 cores). Keep the
# ceiling generous enough to be load-proof but low enough to still catch a hang.
TIMEOUT = 300
# The block the seat allocates is the fixed room of the memory map (boot/README.md
# Appendix A): package.BASE 0x04000000 upward, tens of megabytes of starter now
# that the image carries the seed. The firmware keeps its own rooms above that, so
# the machine must be sized past the block -- 128M is not enough for the UEFI seat
# to get its pages at the map (the BIOS seat runs before any firmware heap exists).
RAM = "512M"

QEMU = shutil.which("qemu-system-x86_64")
OVMF_CODE = next(
    (path for path in (
        Path("/usr/share/OVMF/OVMF_CODE_4M.fd"),
        Path("/usr/share/ovmf/OVMF_CODE.fd"),
        Path("/usr/share/edk2/x64/OVMF_CODE.4m.fd"),
    ) if path.exists()),
    None,
)
OVMF_VARS = next(
    (path for path in (
        Path("/usr/share/OVMF/OVMF_VARS_4M.fd"),
        Path("/usr/share/ovmf/OVMF_VARS.fd"),
        Path("/usr/share/edk2/x64/OVMF_VARS.4m.fd"),
    ) if path.exists()),
    None,
)

needs_qemu = pytest.mark.skipif(QEMU is None, reason="needs qemu-system-x86_64")
needs_ovmf = pytest.mark.skipif(
    OVMF_CODE is None or OVMF_VARS is None, reason="needs OVMF firmware"
)
pytestmark = [needs_qemu]


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def expected(image) -> bytes:
    """The report line the interpreter of the image answers for the same entry."""
    linked = package.link_module().link(image, "x86_64", package.BASE)
    return run.report_line(image, linked, MAIN)


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory, image):
    out = tmp_path_factory.mktemp("dist")
    return {
        target: Path(builder.build(target, image, out)["artifact"])
        for target in ("bios", "uefi")
    }


def boot(command: list[str], serial: Path) -> tuple[int, bytes]:
    """One machine run: its exit and what the serial line carried.

    KVM first, TCG second: the guest is our own kernel and runs the same either
    way, but TCG boots it at interpretation speed. On a host without usable KVM
    (or a foreign target like aarch64 on x86) QEMU falls back to the next
    ``-accel`` in the list by itself.
    """
    done = subprocess.run(
        command + ["-accel", "kvm", "-accel", "tcg",
                   "-display", "none", "-no-reboot",
                   "-device", "isa-debug-exit,iobase=0xf4,iosize=0x04",
                   "-serial", f"file:{serial}"],
        capture_output=True,
        timeout=TIMEOUT,
        check=False,
    )
    return done.returncode, serial.read_bytes() if serial.exists() else b""


def test_the_bios_image_boots_and_reports_the_interpreter_result(tmp_path, artifacts, expected):
    assert QEMU is not None
    answer, serial = boot(
        [QEMU, "-m", RAM, "-drive", f"format=raw,file={artifacts['bios']}"],
        tmp_path / "serial.log",
    )
    assert answer == ANSWER
    assert serial == expected


@needs_ovmf
def test_the_uefi_application_boots_and_reports_the_interpreter_result(
    tmp_path, artifacts, expected
):
    assert QEMU is not None and OVMF_CODE is not None and OVMF_VARS is not None
    efi = tmp_path / "esp" / "EFI" / "BOOT"
    efi.mkdir(parents=True)
    (efi / "BOOTX64.EFI").write_bytes(artifacts["uefi"].read_bytes())
    vars_at = tmp_path / "OVMF_VARS.fd"
    vars_at.write_bytes(OVMF_VARS.read_bytes())
    answer, serial = boot(
        [
            QEMU,
            "-machine", "q35",
            "-m", RAM,
            "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
            "-drive", f"if=pflash,format=raw,file={vars_at}",
            "-drive", f"format=raw,file=fat:rw:{tmp_path / 'esp'}",
        ],
        tmp_path / "serial.log",
    )
    assert answer == ANSWER
    # the seat writes the line twice: once to the console of the firmware and once to
    # COM1; every line it wrote is the one the interpreter answers
    lines = [line + b"\n" for line in serial.splitlines() if line.startswith(b"rxf result")]
    assert lines
    assert set(lines) == {expected}
