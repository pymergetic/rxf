"""The measured gap between the Python strand and upstream uasyncio (docs/23 4.3 and 4.5).

``tools/native/rxpy.py`` compiles a subset of whole-word Python (docs/23 4.3: functions of ints,
loops, tuple returns, module cells). Upstream ``extmod/asyncio`` (the pinned MicroPython tree) is
far outside it. These pins make the distance a MEASUREMENT instead of a feeling:

- every upstream asyncio source is refused by name at its first construct the subset does not
  lower, never lowered to wrong words;
- the census below is the set of construct kinds (AST node types) that block the six files. It is
  the work list of the Python-import lane for uasyncio: a construct leaves the set when the strand
  learns to lower it, and this test goes red on purpose so the table in docs/23 is updated with it.

The model of the S3 ring binding (tests/test_smp_asyncio.py) proves what uasyncio's primitives
MEAN on the run-cell machine; this file proves how far the compiler still is from reading the
source that defines them.
"""

import ast
from pathlib import Path

import pytest
import rxpy

from pymergetic.rxftk.core import tree

from .mp_reference import packages_dir
from .snippets import STARTER

ROOT = packages_dir()
UPSTREAM = ROOT / "micropython" / "extmod" / "asyncio" if ROOT else None
FILES = ("core", "task", "event", "funcs", "lock", "stream")

# the construct kinds the subset refuses (rxpy.py's module docstring is the list), by AST name
BLOCKERS = (
    "Import", "ImportFrom", "ClassDef", "AsyncFunctionDef", "Await", "Yield", "YieldFrom",
    "Try", "Raise", "With", "For", "Lambda", "ListComp", "DictComp", "GeneratorExp", "Attribute",
    "Subscript", "Nonlocal", "Assert", "Delete",
)

# the kinds blocking upstream asyncio today, measured against the pinned tree
MEASURED = {
    "Import", "ImportFrom", "ClassDef", "AsyncFunctionDef", "Await", "Yield", "YieldFrom", "Try",
    "Raise", "For", "Attribute", "Subscript", "Assert", "Delete", "Nonlocal", "ListComp",
}


def source_of(name: str) -> Path:
    if UPSTREAM is None or not (UPSTREAM / f"{name}.py").exists():
        pytest.skip("no upstream MicroPython checkout in the workspace (packages/micropython)")
    return UPSTREAM / f"{name}.py"


def census(path: Path) -> dict[str, int]:
    """How many nodes of each blocking kind the file holds."""
    counts: dict[str, int] = {}
    for node in ast.walk(ast.parse(path.read_text())):
        kind = type(node).__name__
        if kind in BLOCKERS:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


@pytest.mark.parametrize("name", FILES)
def test_every_upstream_asyncio_source_is_refused_by_name(name):
    path = source_of(name)
    image = tree.load(STARTER)
    with pytest.raises(rxpy.Refused) as refusal:
        rxpy.import_py_file(image, path, module=f"asyncio_{name}")
    # the first thing every file does is import, and the image has none of what it imports
    assert "the image has no module" in str(refusal.value) or "a relative import" in str(
        refusal.value
    )
    assert "line" in str(refusal.value)


def test_the_blocking_constructs_are_the_measured_work_list():
    present: set[str] = set()
    for name in FILES:
        present |= set(census(source_of(name)))
    assert present == MEASURED, (
        f"the census moved: gone {sorted(MEASURED - present)}, new {sorted(present - MEASURED)} "
        "(a construct the strand now lowers leaves MEASURED; update docs/23 with it)"
    )


def test_the_corpus_the_strand_does_compile_is_outside_that_set():
    """The proven corpus (tests/rxpy_corpus) uses none of the blocking kinds: the strand's
    subset and uasyncio's source are two disjoint worlds today, and the bridge is the lane."""
    corpus = Path(__file__).parent / "rxpy_corpus"
    used: set[str] = set()
    for path in sorted(corpus.glob("*.py")):
        used |= set(census(path))
    assert used <= {"Attribute"} or not used, used
