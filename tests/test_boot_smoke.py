"""The boot smoke of ``boot/`` as part of the suite (docs/31 §5, boot/README.md).

``boot/run_smoke.sh`` builds every seat of the boot glue and boots it: the
x86_64 BIOS sector, the aarch64 virt kernel, the wasm32 module under node and
the x86_64 UEFI application under OVMF. Each seat's serial line must say what
the image's program answered (``RXFOK`` and the ``rxf result`` line) and the
coarse exit must agree. The proof lives in the script -- the seat commands are
its language, not this file's -- so the suite runs the script and holds it to
its word.

A seat whose tool is missing the script skips loudly and names the tool. The
test skips (and says so) only when no seat could run here at all; no test
pretends a boot happened.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SMOKE = ROOT / "boot" / "run_smoke.sh"
SEATS = ("x86_64-bios", "aarch64-virt", "wasm32-node", "x86_64-uefi")
VERDICT = re.compile(rf"^({'|'.join(SEATS)}): (PASS|SKIP|FAIL)", re.MULTILINE)


def test_the_boot_smoke_boots_every_seat_this_machine_can_run():
    if shutil.which("make") is None:
        pytest.skip("needs make to build the seats")
    done = subprocess.run(
        ["sh", str(SMOKE)],
        cwd=ROOT / "boot",
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    out = done.stdout + done.stderr
    verdicts = dict(VERDICT.findall(out))
    passed = {seat for seat, verdict in verdicts.items() if verdict == "PASS"}
    skipped = {seat for seat, verdict in verdicts.items() if verdict == "SKIP"}
    failed = {seat for seat, verdict in verdicts.items() if verdict == "FAIL"}
    if not passed and skipped:
        pytest.skip(f"no seat could run here: {', '.join(sorted(skipped))}\n{out}")
    assert not failed, f"the seats {sorted(failed)} failed:\n{out}"
    assert done.returncode == 0, out
    assert passed == set(SEATS) - skipped, f"a seat proved nothing:\n{out}"
