"""The measured gap between the Python strand and upstream uasyncio (docs/23 4.3, 4.5 and 7).

``tools/native/rxpy.py`` compiles a subset of whole-word Python: functions of ints, loops, tuple
returns, module cells, imports of modules of the image, and simple classes whose instances are
named cells (docs/23 4.3, 4.6). Upstream ``extmod/asyncio`` (the pinned MicroPython tree) is far
outside it. These pins make the distance a MEASUREMENT instead of a feeling (``tests/rxpy_gap.py``
is the measurement; ``tests/rxpy_asyncio_gap.json`` the pinned answer):

- against the image as it is, every upstream asyncio source stops at its first import, and the
  refusal names the module the tree lacks (``time``, the package ``asyncio``): the module tree
  of the image has no guest stdlib yet;
- with every import served and the file's own defs bound where the file binds them (a call of a
  sibling def is a call of the file's function, so a def's refusal is a construct of the def), the
  strand's answer per top-level statement (a def, a class, a method tried as a def over a parameter
  ``self``) is the pinned table: which compile, and the first reason each is refused -- per def by
  name, the contract of the census;
- the census of construct kinds still blocking the six files is the work list of the
  Python-import lane for uasyncio. A construct leaves the set when the strand learns to lower it
  and these tests go red on purpose; ``PYTHONPATH=tools/native:. python -m tests.rxpy_gap
  --write`` regenerates the pin and writes the rows of the docs/23 table from the one measurement.

The model of the S3 ring binding (tests/test_smp_asyncio.py) proves what uasyncio's primitives
MEAN on the run-cell machine; this file proves how far the compiler still is from reading the
source that defines them.
"""

import ast
import json
from pathlib import Path

import pytest
import rxpy

from pymergetic.rxftk.core import tree

from . import rxpy_gap
from .snippets import STARTER

FILES = rxpy_gap.FILES

# the construct kinds the subset still refuses, by AST name (imports, classes and attributes are
# no longer here: the strand binds an import, lays out a class and reads the attributes of an
# imported module or a named instance; subscripts index the tables of the module -- what stays
# refused of them shows in the pinned table)
BLOCKERS = (
    "AsyncFunctionDef", "Await", "Yield", "YieldFrom", "Try", "Raise", "With", "For", "Lambda",
    "ListComp", "DictComp", "GeneratorExp", "Nonlocal", "Assert", "Delete",
)

# the kinds blocking upstream asyncio today, measured against the pinned tree
MEASURED = {
    "AsyncFunctionDef", "Await", "Yield", "YieldFrom", "Try", "Raise", "For",
    "Assert", "Delete", "Nonlocal", "ListComp",
}


def source_of(name: str) -> Path:
    root = rxpy_gap.upstream()
    if root is None:
        pytest.skip("no upstream MicroPython checkout in the workspace (packages/micropython)")
    return root / f"{name}.py"


def census(path: Path) -> dict[str, int]:
    """How many nodes of each blocking kind the file holds."""
    counts: dict[str, int] = {}
    for node in ast.walk(ast.parse(path.read_text())):
        kind = type(node).__name__
        if kind in BLOCKERS:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


@pytest.mark.parametrize("name", FILES)
def test_against_the_image_as_it_is_every_source_stops_at_its_first_import(name):
    path = source_of(name)
    image = tree.load(STARTER)
    with pytest.raises(rxpy.Refused) as refusal:
        rxpy.import_py_file(image, path, module=f"asyncio.{name}")
    message = str(refusal.value)
    # the first thing every file does is import, and the tree has none of what it imports
    assert "the image has no module" in message
    assert "line" in message


def test_the_measurement_is_the_pinned_one():
    """Per file: its imports, what compiles of its statements with the imports served, and the
    first reason for each refusal. Red when the strand learns (or loses) a construct; the
    regeneration is ``python -m tests.rxpy_gap --write`` and the docs/23 rows come from it."""
    source_of("core")
    assert rxpy_gap.measure() == json.loads(rxpy_gap.PINNED.read_text())


def test_the_docs_table_is_the_pinned_measurement():
    """docs/23 section 7 carries the generated rows, not a retelling: the block between the
    markers is exactly what ``python -m tests.rxpy_gap`` prints from the pinned JSON."""
    docs = (Path(__file__).parents[1] / "docs" / "23-MICROPYTHON-EXPANSION.md").read_text()
    begin = docs.index("<!-- rxpy_gap:begin")
    block = docs[docs.index("\n", begin) + 1 : docs.index("<!-- rxpy_gap:end -->")]
    pinned = json.loads(rxpy_gap.PINNED.read_text())
    assert block.rstrip("\n") == "\n".join(rxpy_gap.rows(pinned))


def test_the_blocking_constructs_are_the_measured_work_list():
    present: set[str] = set()
    for name in FILES:
        present |= set(census(source_of(name)))
    assert present == MEASURED, (
        f"the census moved: gone {sorted(MEASURED - present)}, new {sorted(present - MEASURED)} "
        "(a construct the strand now lowers leaves MEASURED; regenerate the pin and update docs/23)"
    )


def test_the_corpus_the_strand_does_compile_is_outside_that_set():
    """The proven corpus (tests/rxpy_corpus) uses none of the blocking kinds: the strand's
    subset and uasyncio's source are two disjoint worlds today, and the bridge is the lane."""
    corpus = Path(__file__).parent / "rxpy_corpus"
    used: set[str] = set()
    for path in sorted(corpus.rglob("*.py")):
        used |= set(census(path))
    assert not used, used
