"""Build the native snippets from their Seed sources, one function per machine.

Every snippet is a ``.rxfs`` ``fn`` of ``native/``, compiled by the seed CLI
(``native/seed/seed.c``) to the object ABI of its machine. The starter holds
those bytes as ``Code`` objects, except the four large programs
(``compile.program``, ``live.pump``, ``graph.run``, ``tiny.compile``): their
bytes are built and not stored (``programs.py``, Wave 4b step 2). This module
is the one build path: no C compiler is in it (docs/31 step 5, "builders =
Seed only"). The families below name the snippets by the module of the starter
they land in.

    python tools/native/native_build.py OUTDIR      # writes OUTDIR/<machine>.json
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
import seed_build
from native_symbols import EXPECTED as NUMERIC

ROOT = HERE.parents[1]
NATIVE = ROOT / "native"

# The machines of the starter, and the seed CLI's target word for each.
MACHINES = dict(seed_build.TARGET)
# What CompilerProvenance records as ``compiler_major``: the seed recipe's major.
SEED_MAJOR = "1"

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


def build() -> dict[str, dict[str, str]]:
    """Return ``{machine: {name: hex}}`` for every snippet, from its Seed source."""
    return {
        machine: {name: seed_build.code(machine, name) for name in seed_build.keys()}
        for machine in MACHINES
    }


def sources() -> list[Path]:
    """Every file the recipe reads: the Seed sources, the seed compiler, and the
    drivers that point its CLI at the right ``fn``."""
    drivers = (
        "native_build.py",
        "seed_build.py",
        "graph_build.py",
        "compile_build.py",
        "live_build.py",
        "native_symbols.py",
    )
    return [
        NATIVE / "seed" / "seed.c",
        *sorted(NATIVE.rglob("*.rxfs")),
        *(HERE / driver for driver in drivers),
    ]


def recipe(machine: str) -> dict[str, object]:
    """Everything that turns the Seed sources into the bytes of one machine: the
    seed CLI's words per function (its ``fn`` and the shape of its operands)."""
    return {
        "compiler": "seed",
        "version": SEED_MAJOR,
        "target": MACHINES[machine],
        "functions": {
            name: [seed_build.fn_of(name, machine), *seed_build.shape_of(name)]
            for name in seed_build.keys()
        },
    }


def recipe_digest(machine: str) -> str:
    """SHA-256 of the recipe and the sources it names: what ``CompilerProvenance``
    records, so a changed source or shape moves the pin."""
    digest = hashlib.sha256()
    digest.update(
        json.dumps(recipe(machine), sort_keys=True, separators=(",", ":")).encode()
    )
    for path in sources():
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for machine, functions in build().items():
        payload = {
            "machine": machine,
            "target": MACHINES[machine],
            "compiler": "seed",
            "recipe": recipe(machine),
            "functions": functions,
        }
        (out / f"{machine}.json").write_text(
            json.dumps(payload, sort_keys=True, indent=1) + "\n"
        )


if __name__ == "__main__":
    main()
