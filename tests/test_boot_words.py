"""The None word on the boot entry: an absent argument binds it (native/graph.h).

``boot/image_stub/entry.c``'s run binds the words the arguments do not reach to
``RXF_WORD_NONE`` -- the face the interpreter's run entry has (docs/23 4.9 step 10). The
glue passes one argument word (``world_v7.h``'s ``RXF_BOOT_ARGC``) and the program below
declares two: the second parameter is its ``= None`` default and the seat must answer the
None word, not int 0. The entry is shared by the seats (tests/test_boot_handle.py), so
the wasm32 seat under node proves the code the other seats compile; the control that the
same seat still answers the smoke's program ``RXFOK`` is that file's own test.
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
WORD_NONE = N["RXF_WORD_NONE"]
TOOLS = ("make", "clang", "wasm-ld", "node")


def default_program() -> bytes:
    """The smoke's program with a second parameter the glue's one argument word does not
    reach: the answer is the second parameter -- the word the entry bound for it."""
    slot = N["RXF_OPERAND_SLOT"]
    return struct.pack(
        "<20Q",
        N["RXF_GRAPH_MAGIC"],
        64,  # frame bytes
        2,  # parameters
        2,  # argument words (the glue passes one: the second is its `= None` default)
        9,  # entry word (9 = 5 + two parameter pairs)
        0,
        8,  # parameter 0 lives at frame slot 0: the glue's argument word
        8,
        8,  # parameter 1 lives at frame slot 8: the default
        N["RXF_OP_MOVE"],
        16,
        8,
        slot,
        8,
        8,  # frame[16] = frame[8], the defaulted parameter
        N["RXF_OP_RETURN"],
        slot,
        16,
        8,
        8,  # answer frame[16..24)
    )


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


def test_the_seat_binds_the_none_word_to_an_argument_it_does_not_take(
    seat, tmp_path: Path
):
    done = run_seat(tmp_path, default_program())
    # the run itself succeeded (status 0); the shim blesses one answer word (RXFOK)
    # and reports any other as the failure line it is named in
    assert done.returncode != 0, done.stdout
    assert f"status=0 out=0x{WORD_NONE:x}" in done.stderr, done.stderr
