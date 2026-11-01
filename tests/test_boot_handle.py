"""The boot interpreter refuses a HANDLE operand (docs/23 4.9, step 8).

``boot/image_stub/entry.c`` is a mini interpreter of the v7 slot table (MOVE, RETURN, REFUSE and the
CALL of one shape). It resolves no handle in a frame: an operand kind it does not know answers
``OUT_OF_RANGE`` (6) -- the stated contract until a seat resolves the operand against the superblock
directly (docs/23 4.9, "Open"). There is no code change in ``entry.c`` for this; the test pins the
answer on the wasm32 seat, the one that runs under node (the other seats share the entry), with the
control that the same seat answers the smoke's own program ``RXFOK``.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import boot_image
import graph_header
import pytest

pytestmark = [pytest.mark.nightly, pytest.mark.slow]

ROOT = Path(__file__).parents[1]
BOOT = ROOT / "boot"
N = graph_header.numbers()
OUT_OF_RANGE = 6
TOOLS = ("make", "clang", "wasm-ld", "node")


def handle_program() -> bytes:
    """``program()`` with its one MOVE reading through a HANDLE operand: frame[8] = *frame[0]."""
    words = list(struct.unpack("<18Q", boot_image.program()))
    assert words[7] == N["RXF_OP_MOVE"] and words[10] == N["RXF_OPERAND_SLOT"]
    words[10] = N["RXF_OPERAND_HANDLE"]
    return struct.pack("<18Q", *words)


def run_seat(tmp_path: Path, words: bytes) -> subprocess.CompletedProcess[str]:
    image = tmp_path / "image_wasm32.bin"
    boot_image.build(image, machine="wasm32", probe_words=words)
    return subprocess.run(
        [
            "node",
            "wasm_shim.js",
            "build/rxf_boot.wasm",
            str(image),
            "build/rxf_leaves.wasm",
        ],
        cwd=BOOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


@pytest.fixture(scope="module")
def seat() -> None:
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip(f"needs {', '.join(missing)}")
    subprocess.run(
        [
            "make",
            "--no-print-directory",
            "build/rxf_boot.wasm",
            "build/rxf_leaves.wasm",
        ],
        cwd=BOOT,
        check=True,
        capture_output=True,
    )


def test_the_seat_answers_the_smokes_program(seat, tmp_path: Path):
    done = run_seat(tmp_path, boot_image.program())
    assert done.returncode == 0, done.stderr


def test_the_seat_refuses_a_handle_operand_out_of_range(seat, tmp_path: Path):
    done = run_seat(tmp_path, handle_program())
    assert done.returncode != 0
    assert f"status={OUT_OF_RANGE}" in done.stderr, done.stderr
