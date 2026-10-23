"""Self-contained smoke for the boot glue (packages/rxf/boot).

Runs boot/run_smoke.sh once and asserts every seat's boot proved the magic:
the serial line says `RXFOK` and the wrap.h result line. Self-contained on
purpose: it imports nothing from this tests package and touches nothing but the
standalone boot/ directory. Skips when the toolchain or QEMU is absent.

    pytest tests/test_boot_glue.py -v
"""

import pathlib
import shutil
import subprocess

import pytest

BOOT = pathlib.Path(__file__).resolve().parent.parent / "boot"
TOOLS = [
    "make",
    "clang",
    "as",
    "ld",
    "wasm-ld",
    "node",
    "qemu-system-x86_64",
    "qemu-system-aarch64",
]
SEATS = ["x86_64-bios", "aarch64-virt", "wasm32-node", "x86_64-uefi"]


@pytest.fixture(scope="module")
def smoke():
    if not BOOT.is_dir():
        pytest.skip(f"no boot directory at {BOOT}")
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip("boot smoke tools missing: " + ", ".join(missing))
    return subprocess.run(
        ["sh", "run_smoke.sh"], cwd=BOOT, capture_output=True, text=True, timeout=600
    )


@pytest.mark.parametrize("seat", SEATS)
def test_boot_glue_seat(smoke, seat):
    lines = [line for line in smoke.stdout.splitlines() if line.startswith(seat + ":")]
    assert lines, f"{seat} produced no smoke line:\n{smoke.stdout}\n{smoke.stderr}"
    line = lines[0]
    assert "PASS" in line, f"{seat} did not pass:\n{smoke.stdout}\n{smoke.stderr}"
    assert "RXFOK" in line, f"{seat} passed without the magic on its serial:\n{line}"


def test_boot_glue_overall(smoke):
    assert "boot smoke: PASS" in smoke.stdout, f"smoke failed:\n{smoke.stdout}\n{smoke.stderr}"
