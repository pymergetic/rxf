"""Build the graph interpreter (``native/graph.rxfs``) with the seed compiler (docs/29).

``native/graph.rxfs`` is the ONE defining source of the interpreter: the faithful
port of ``native/graph.c`` in the Seed ``fn`` dialect (line-by-line, the helpers
macros, the state of ``rxf_graph`` the state of ``graph_run``). This helper is the
one build path: it runs the seed CLI (``native/seed/seed.c``) over that file and
returns the code of ``graph_run`` per machine -- the object-ABI body of
``f(params..., out) -> status``, here spelled ``!pHqqp``:
``run(context, program: handle, args, argc, out)``. Every exit is a ``refuse``
(a status, no store through ``out``): the run's value is written through the
``out`` parameter by OP_RETURN, exactly as the C writes it.

    python tools/native/graph_build.py            # hex of the three machines
    python tools/native/graph_build.py --json     # {machine: hex}

The wasm32 body's ``call_indirect`` names a leaf's function type by the position of
the leaf's signature in the type section of whoever composes the snippets: the
composer (``tests/wasm.py module_of``) puts one type per leaf in the order of the
leaf ids, so ``call_types()`` is that list, one entry per leaf, and the ``@`` argv
must be it -- the shapes of ``seed_build.call_type_letters()`` are the same indices.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import graph_header
import seed_build
import seed_cache

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "native" / "graph.rxfs"
SELF = "!pHqqp"  # run(context, program: handle, args, argc, out): 5 C arguments / 7 words
STOREW = 0  # the run stores through `out` itself; every exit is `refuse <status>`

# The C parameters of every leaf of native/graph.h, one letter each: the table of
# ``seed_build.LEAF_LETTERS`` (the one spelling of a leaf's call), asserted against
# the tests' own table by tests/test_graph_build.py.
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
    """The interpreter source, and that the build table covers the leaves it names."""
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
    """The hex of the object-ABI code of ``graph_run`` on ``machine``."""
    argv = [
        str(seed_build.seed_bin()),
        "codegen",
        seed_build.TARGET[machine],
        "object",
        str(STOREW),
        SELF,
        *(f"@{letters}" for letters in call_types()),
    ]
    return seed_cache.run(argv, source())


def build() -> dict[str, str]:
    """``{machine: hex}`` of ``graph_run`` for every machine of ``native_build``."""
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
