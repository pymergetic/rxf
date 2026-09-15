#!/usr/bin/env python3
"""Generate assembly byte functions from the checked AArch64 manifest."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
SYMBOLS = (
    "checked_add_int32_t",
    "divide_int32_t",
    "remainder_int32_t",
    "shift_left_int32_t",
    "shift_right_int32_t",
    "checked_add_float",
    "minimum_float",
    "convert_double_to_int32_t",
    "convert_uint64_t_to_double",
)
manifest = json.loads((ROOT / "generated/native_aarch64.json").read_text())["functions"]
lines = ['.section .text.blobs,"ax"']
for name in SYMBOLS:
    raw = bytes.fromhex(manifest[name]["hex"])
    lines += [
        ".balign 16",
        f".global blob_{name}",
        f".type blob_{name}, %function",
        f"blob_{name}:",
    ]
    for offset in range(0, len(raw), 16):
        lines.append(
            "    .byte " + ",".join(f"0x{x:02x}" for x in raw[offset : offset + 16])
        )
    lines.append(f".size blob_{name}, .-blob_{name}")
(ROOT / "generated/qemu_aarch64_blobs.S").write_text("\n".join(lines) + "\n")
