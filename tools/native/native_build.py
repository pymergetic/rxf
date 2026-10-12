"""Compile the native leaf snippets with pinned Clang and list their bytes.

The C sources live in ``native/``. Each function is compiled once per machine
(``x86_64``, ``aarch64``), is relocation-free, and is cut out of the object
file by ``extract_native``. The starter holds these bytes as ``Code`` objects.

    python tools/native/native_build.py OUTDIR      # writes OUTDIR/<machine>.json
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

HERE = Path(__file__).resolve().parent
import extract_native
import extract_wasm
import graph_header
from native_symbols import EXPECTED as NUMERIC

ROOT = HERE.parents[1]
NATIVE = ROOT / "native"
CLANG = "clang-18"
LINKER = "ld.lld-18"
WASM_LINKER = "wasm-ld-18"
PINNED = "18.1.3"
MACHINES = {
    "x86_64": "x86_64-unknown-linux-gnu",
    "aarch64": "aarch64-none-elf",
    "wasm32": "wasm32-unknown-unknown",
}
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
MEMORY = {
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
RUNTIME = {
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
GRAPH = {"graph_run"}
LIVE = {"live_pump"}
UART = {"uart_pump"}
SAFE = {"safe_point"}
COMPILE = {"compile_program"}
# Which sources make which symbols.
UNITS: tuple[tuple[tuple[str, ...], set[str]], ...] = (
    (("basics.c", "conversions.c"), set(NUMERIC)),
    (("stdlib_memory.c",), MEMORY),
    (("stdlib_runtime.c",), RUNTIME),
    (("graph.c",), GRAPH),
    (("live.cpp",), LIVE),
    (("uart.c",), UART),
    (("safe.c",), SAFE),
    (("compile.c",), COMPILE),
)
# The C++ of the core (src/) as one native leaf: smaller code, no SIMD (a constant pool would be a
# relocation), nothing that needs a runtime.
CPP_FLAGS = (
    "-std=c++20",
    "-Os",
    "-fno-exceptions",
    "-fno-rtti",
    "-fno-vectorize",
    "-fno-slp-vectorize",
)
CPP_INCLUDE = ROOT / "src"
GENERAL_REGISTERS = {"x86_64", "aarch64"}  # no vector registers, so no constant pool


def flags_for(source: str, machine: str) -> list[str]:
    """The compiler flags of one source file on one machine."""
    if not source.endswith(".cpp"):
        return list(FLAGS)
    flags = [f for f in FLAGS if f not in ("-std=c11", "-O2")]
    flags += CPP_FLAGS
    if machine in GENERAL_REGISTERS:
        flags.append("-mgeneral-regs-only")
    flags += ["-I", str(CPP_INCLUDE)]
    return flags


def recipe(machine: str) -> dict[str, object]:
    """Everything that turns the C sources into the bytes of one machine."""
    linking = (
        [
            WASM_LINKER,
            "--no-entry",
            "--import-memory",
            "--import-table",
            "--emit-relocs",
            "--export=rxf_<name>",
        ]
        if machine == "wasm32"
        else [LINKER, "-r", "--unique"]
    )
    return {
        "compiler": CLANG,
        "version": PINNED,
        "target": MACHINES[machine],
        "flags": list(FLAGS),
        "cpp_flags": [
            *CPP_FLAGS,
            *(["-mgeneral-regs-only"] if machine in GENERAL_REGISTERS else []),
        ],
        "link": linking,
    }


def recipe_digest(machine: str) -> str:
    """SHA-256 of the recipe as canonical JSON: what ``CompilerProvenance`` records."""
    text = json.dumps(recipe(machine), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def clang_version() -> str:
    out = subprocess.run(
        [CLANG, "--version"], check=True, text=True, capture_output=True
    ).stdout
    match = re.search(r"clang version (\d+\.\d+\.\d+)", out)
    if match is None:
        raise RuntimeError(f"cannot read the version of {CLANG}")
    return match.group(1)


def compile_unit(
    sources: tuple[str, ...], target: str, machine: str, work: Path, expected: set[str]
) -> Mapping[str, Mapping[str, object]]:
    objects: list[Path] = []
    for source in sources:
        obj = work / f"{source}.o"
        subprocess.run(
            [
                CLANG,
                "-target",
                target,
                *flags_for(source, machine),
                "-c",
                str(NATIVE / source),
                "-o",
                str(obj),
            ],
            check=True,
        )
        objects.append(obj)
    combined: Path
    if len(objects) == 1:
        combined = objects[0]
    else:
        combined = work / f"{sources[0]}.all.o"
        subprocess.run(
            [LINKER, "-r", "--unique", *map(str, objects), "-o", str(combined)],
            check=True,
        )
    return extract_native.extract(combined, machine, expected)


def build_wasm(work: Path) -> dict[str, extract_wasm.Function]:
    """Compile every unit for wasm32, link them once, and cut the bodies out."""
    target = MACHINES["wasm32"]
    objects: list[Path] = []
    for sources, _ in UNITS:
        for source in sources:
            obj = work / f"{source}.wasm.o"
            subprocess.run(
                [
                    CLANG,
                    "-target",
                    target,
                    *flags_for(source, "wasm32"),
                    "-c",
                    str(NATIVE / source),
                    "-o",
                    str(obj),
                ],
                check=True,
            )
            problems = extract_wasm.check_object(obj)
            if problems:
                raise RuntimeError(
                    f"{source} does not stand alone on wasm32: {problems}"
                )
            objects.append(obj)
    names = {name for _, expected in UNITS for name in expected}
    linked = work / "snippets.wasm"
    subprocess.run(
        [
            WASM_LINKER,
            "--no-entry",
            "--import-memory",
            "--import-table",
            "--emit-relocs",
            *(f"--export=rxf_{name}" for name in sorted(names)),
            *map(str, objects),
            "-o",
            str(linked),
        ],
        check=True,
    )
    by_id = sorted(graph_header.leaves().items(), key=lambda leaf: leaf[1])
    found = extract_wasm.read_module(
        linked,
        {f"rxf_{name}" for name in names},
        tuple(f"rxf_{name}" for name, _ in by_id),
    )
    return {name[4:]: function for name, function in found.items()}


def build_wasm_functions() -> dict[str, extract_wasm.Function]:
    """The wasm32 snippets with their function types."""
    with tempfile.TemporaryDirectory() as directory:
        return build_wasm(Path(directory))


def build() -> dict[str, dict[str, str]]:
    """Return ``{machine: {name: hex}}`` for every snippet."""
    version = clang_version()
    if version != PINNED:
        raise RuntimeError(f"expected clang {PINNED}, got {version}")
    result: dict[str, dict[str, str]] = {}
    for machine, target in MACHINES.items():
        if machine == "wasm32":
            continue
        functions: dict[str, str] = {}
        with tempfile.TemporaryDirectory() as directory:
            for sources, expected in UNITS:
                found = compile_unit(
                    sources, target, machine, Path(directory), expected
                )
                for name, record in found.items():
                    if name in functions:
                        raise RuntimeError(f"{name} is defined twice")
                    functions[name] = str(record["hex"])
        result[machine] = dict(sorted(functions.items()))
    result["wasm32"] = {
        name: function.body.hex()
        for name, function in sorted(build_wasm_functions().items())
    }
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for machine, functions in build().items():
        payload = {
            "machine": machine,
            "target": MACHINES[machine],
            "compiler": f"clang-{PINNED}",
            "recipe": " ".join(FLAGS),
            "functions": functions,
        }
        (out / f"{machine}.json").write_text(
            json.dumps(payload, sort_keys=True, indent=1) + "\n"
        )


if __name__ == "__main__":
    main()
