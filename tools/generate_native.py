#!/usr/bin/env python3
"""Rebuild both deterministic RXF native manifests with pinned Clang 18.1.3."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
CLANG = "clang-18"
PINNED = "18.1.3"
FLAGS = (
    "-std=c11",
    "-O2",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-stack-protector",
    "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables",
    "-ffunction-sections",
    "-fdata-sections",
    "-fno-ident",
    "-fno-addrsig",
    "-fno-pic",
    "-fno-jump-tables",
)
RECIPE = " ".join(FLAGS)
version_output = subprocess.run(
    [CLANG, "--version"], check=True, text=True, capture_output=True
).stdout
match = re.search(r"clang version (\d+\.\d+\.\d+)", version_output)
if match is None or match.group(1) != PINNED:
    raise RuntimeError(f"expected clang {PINNED}, got {version_output.splitlines()[0]}")
subprocess.run(
    [sys.executable, str(ROOT / "tools/generate_conversions.py")], check=True
)
for machine, target, filename in (
    ("x86_64", "x86_64-unknown-linux-gnu", "native_x86_64.json"),
    ("aarch64", "aarch64-none-elf", "native_aarch64.json"),
):
    with tempfile.TemporaryDirectory() as directory:
        objects = []
        for source in ("basics.c", "conversions.c"):
            obj = Path(directory) / f"{source}.o"
            subprocess.run(
                [
                    CLANG,
                    "-target",
                    target,
                    *FLAGS,
                    "-c",
                    str(ROOT / "native" / source),
                    "-o",
                    str(obj),
                ],
                check=True,
            )
            objects.append(obj)
        combined = Path(directory) / "all.o"
        subprocess.run(
            ["ld.lld-18", "-r", "--unique", *map(str, objects), "-o", str(combined)],
            check=True,
        )
        temporary = Path(directory) / filename
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/extract_native.py"),
                machine,
                str(combined),
                str(temporary),
            ],
            check=True,
        )
        payload = json.loads(temporary.read_text())
        payload.update(compiler=f"clang-{PINNED}", recipe=RECIPE, target=target)
        (ROOT / "generated" / filename).write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        )
