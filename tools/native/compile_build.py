"""Build the compiler (``native/compile.rxfs``) with the seed compiler (docs/29).

``native/compile.rxfs`` is the ONE defining source of the in-image compiler: the
faithful port of ``native/compile.c`` in the Seed ``fn`` dialect (line-by-line, the
encoder helpers macros, the state of ``rxf_cc`` the state of ``compile_program``,
``host()`` the seat's ``#if`` seam). This helper is the one build path: it runs the
seed CLI (``native/seed/seed.c``) over that file and returns the code of
``compile_program`` per machine -- the object-ABI body of
``f(params..., out) -> status``, here spelled ``!pp``:
``compile_program(context, request)``. The request is the struct of
``native/compile.h`` (program, program_size, out, out_capacity, work,
work_capacity, out_size, at); the run writes out_size and at through it and every
exit is a ``refuse <status>``.

    python tools/native/compile_build.py            # hex of the three machines
    python tools/native/compile_build.py --json     # {machine: hex}

The wasm32 body's ``call_indirect`` names a leaf's function type by the position of
the leaf's signature in the type section of whoever composes the snippets: the
composer (``tests/wasm.py module_of``) puts one type per leaf in the order of the
leaf ids, so ``call_types()`` is that list, one entry per leaf, and the ``@`` argv
must be it (the same list ``graph_build`` passes).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import graph_header
import seed_build

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "native" / "compile.rxfs"
SELF = "!pp"  # compile_program(context, request): 2 C arguments / 2 words
STOREW = 0  # the run writes out_size/at through the request; every exit is `refuse <status>`

# The C parameters of every leaf of native/graph.h, one letter each: the table of
# ``seed_build.LEAF_LETTERS`` (the one spelling of a leaf's call), asserted against
# the tests' own table by tests/test_compile_build.py.
LETTERS = seed_build.LEAF_LETTERS


def call_types() -> list[str]:
    """The call types of the codegen's ``@`` list: ``seed_build.call_type_letters``,
    the letters of every leaf in the order of its id (one entry per leaf, the order
    the composer's type section has)."""
    found: list[str] = []
    for name, _ in sorted(graph_header.leaves().items(), key=lambda item: item[1]):
        found.append(LETTERS[name])
    assert found == seed_build.call_type_letters()
    return found


def source() -> str:
    """The compiler source, and that the build table covers the leaves it names."""
    text = SOURCE.read_text()
    missing = set(graph_header.leaves()) - set(LETTERS)
    if missing:
        raise RuntimeError(f"no call letters for {sorted(missing)}")
    return text


def call_shapes(text: str) -> list[str]:
    """The ``call_<shape>`` conventions the source uses, in order."""
    seen: list[str] = []
    for shape in re.findall(r"call_([A-Za-z]+)\(", text):
        if shape not in seen:
            seen.append(shape)
    return seen


def code(machine: str) -> str:
    """The hex of the object-ABI code of ``compile_program`` on ``machine``."""
    argv = [
        str(seed_build.seed_bin()),
        "codegen",
        seed_build.TARGET[machine],
        "object",
        str(STOREW),
        SELF,
        *(f"@{letters}" for letters in call_types()),
    ]
    proc = subprocess.run(
        argv, input=source(), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def build() -> dict[str, str]:
    """``{machine: hex}`` of ``compile_program`` for every machine of ``native_build``."""
    return {machine: code(machine) for machine in seed_build.TARGET}


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--json":
        print(json.dumps(build(), sort_keys=True))
        return
    if len(sys.argv) != 1:
        raise SystemExit(__doc__)
    for machine, data in build().items():
        print(machine, len(data) // 2)


if __name__ == "__main__":
    main()
