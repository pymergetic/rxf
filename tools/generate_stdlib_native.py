"""Generate exact relocation-free stdlib foundation leaf manifests."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import extract_native

MEMORY_SYMBOLS = {
    "memory_copy",
    "memory_move",
    "memory_fill",
    "memory_zero",
    "memory_compare",
    "load_u32",
    "store_u32",
    "utf8_validate",
    "hash_bytes",
}
RUNTIME_SYMBOLS = {
    "hash_u64",
    "runtime_hash_map_lookup",
    "runtime_hash_set_lookup",
    "runtime_begin_private",
    "runtime_resolve",
    "runtime_resolve_typed",
    "runtime_allocate_prepare",
    "runtime_publish",
    "runtime_rollback",
    "runtime_release",
    "runtime_borrow",
    "runtime_release_borrow",
    "runtime_pin",
    "runtime_unpin",
    "runtime_read",
    "runtime_write",
    "runtime_reserve",
    "runtime_append",
    "runtime_format_u32",
    "runtime_search",
    "runtime_cleanup",
}
SYMBOLS = MEMORY_SYMBOLS | RUNTIME_SYMBOLS
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
extract_native.EXPECTED = SYMBOLS
results = {}
for machine, target in (
    ("x86_64", "x86_64-unknown-linux-gnu"),
    ("aarch64", "aarch64-none-elf"),
):
    with tempfile.TemporaryDirectory() as directory:
        machine_results = {}
        for stem, expected in (
            ("stdlib_memory", MEMORY_SYMBOLS),
            ("stdlib_runtime", RUNTIME_SYMBOLS),
        ):
            obj = Path(directory) / f"{stem}.o"
            subprocess.run(
                [
                    "clang-18",
                    "-target",
                    target,
                    *FLAGS,
                    "-c",
                    str(ROOT / f"native/{stem}.c"),
                    "-o",
                    str(obj),
                ],
                check=True,
            )
            extract_native.EXPECTED = expected
            machine_results.update(extract_native.extract(obj, machine))
        results[machine] = machine_results
    payload = {
        "schema": 2,
        "machine": machine,
        "target": target,
        "compiler": "clang-18.1.3",
        "functions": results[machine],
    }
    (ROOT / "generated" / f"stdlib_native_{machine}.json").write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )
module = ['"""Generated exact stdlib native bytes; do not edit."""\n']
for variable, machine in (("STDLIB_X86_64", "x86_64"), ("STDLIB_AARCH64", "aarch64")):
    module.append(f"{variable} = {{")
    for name, record in sorted(results[machine].items()):
        module.append(f'    "{name}": bytes.fromhex(')
        module.append(f'        "{record["hex"]}"')
        module.append("    ),")
    module.append("}\n")
(ROOT / "src/pymergetic/rxf/generated_stdlib_native.py").write_text("\n".join(module))
