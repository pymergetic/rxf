"""Generate authored assembly wrappers around exact AArch64 stdlib bytes."""

import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
manifest = json.loads((ROOT / "generated/stdlib_native_aarch64.json").read_text())[
    "functions"
]
lines = ['.section .text.stdlib_blobs,"ax"']
for name in sorted(manifest):
    raw = bytes.fromhex(manifest[name]["hex"])
    lines.extend(
        [
            ".balign 16",
            f".global stdlib_blob_{name}",
            f".type stdlib_blob_{name}, %function",
            f"stdlib_blob_{name}:",
        ]
    )
    for offset in range(0, len(raw), 16):
        lines.append(
            "    .byte " + ",".join(f"0x{x:02x}" for x in raw[offset : offset + 16])
        )
    lines.append(f".size stdlib_blob_{name}, .-stdlib_blob_{name}")
(ROOT / "generated/stdlib_qemu_aarch64_blobs.S").write_text("\n".join(lines) + "\n")
