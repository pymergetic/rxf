"""The Python -> RXF compiler strand: the subset, the mapping, and the differential.

What these pins hold (docs/23 4.3 and 6 decision 10: **MicroPython is the reference**):

* the grammar the strand takes and the shapes it refuses -- every refusal by name and
  line, never lowered to wrong words;
* the mapping: one def one ``Function`` of the image, a call one call card naming the
  callee's path (never an inline), a tuple return the out cells of docs/23 4.1 (one
  object id for the callee and every caller: what lands is the caller's, not a copy);
* the semantics, differentially: the same corpus source (``tests/rxpy_corpus/*.py``,
  plain Python) is executed by a **real MicroPython** -- the unix port of
  ``packages/micropython-wasmmod`` (and the upstream ``packages/micropython`` checkout
  where its binary exists), the same bytes this strand compiles -- on the same inputs.
  In-domain answers match the reference exactly.  Outside the domain both sides are
  pinned one-sidedly and exactly to what each really does (a wrapped word here, a
  bignum or a named exception there; a refusal status, never a wrong number).

A refusal of the run (``RXF_REFUSE_*``) is the run ending with the status.  The
statuses and the call-card budget are read from ``graph_header`` -- the landed run cell
is the machine's, not a table of this file."""

from __future__ import annotations

import hashlib
import os
import struct
import subprocess
from pathlib import Path

import compose
import graph_header
import pytest
import rxemit
import rximport
import rxpy

from pymergetic.rxftk.core import tree

from . import rxf_native
from .snippets import STARTER

CORPUS = Path(__file__).parent / "rxpy_corpus"
DRIVER = Path(__file__).parent / "rxpy_mp_answer.py"
U64 = (1 << 64) - 1
OK = 0

NUMBERS = graph_header.numbers()
DIVISION_BY_ZERO = NUMBERS["RXF_REFUSE_DIVISION_BY_ZERO"]
OVERFLOW = NUMBERS["RXF_REFUSE_OVERFLOW"]
SHIFT_COUNT = NUMBERS["RXF_REFUSE_SHIFT_COUNT"]
CALL_DEPTH = NUMBERS["RXF_REFUSE_CALL_DEPTH"]

# the corpus modules this differential runs (borrow is imported last, against chain)
MODULES = ("words", "compare", "branch", "loops", "pair", "cells", "chain")
KNOWN = {"double_it": "chain.double_it"}


# ---- the differential reference: MicroPython, really run --------------------------------------

def micropythons() -> list[str]:
    """The MicroPython unix binaries of the workspace: both seats where both exist.

    ``RXPY_MPY`` names one to pin against explicitly; without it the wasmmod seat is
    tried first, then the upstream checkout.  Nothing here is a golden table: where no
    binary exists the differential tests skip and say so, and only the shape and
    refusal pins run."""
    named = os.environ.get("RXPY_MPY")
    if named:
        return [named]
    root = Path(__file__).parents[2]  # .../os-sdk/packages
    found = []
    for seat in ("micropython-wasmmod", "micropython"):
        binary = root / seat / "ports" / "unix" / "build-standard" / "micropython"
        if binary.exists():
            found.append(str(binary))
    return found


MPYS = micropythons()


def seat_of(binary: str) -> str:
    """The seat a binary belongs to (its checkout's name), for the test's ids."""
    parts = Path(binary).parts
    return parts[parts.index("ports") - 1] if "ports" in parts else Path(binary).name


def mp_lines(mpy: str, modules: tuple[str, ...], cases: list[tuple[str, tuple[int, ...]]]):
    """One run of the reference: the corpus modules executed as they stand, the cases
    called in order in that one namespace, one answer line per case.

    A line is the answer words in decimal, or ``!ExceptionName`` where the call raised
    (the kinds MicroPython really raises: ZeroDivisionError, ValueError, ...)."""
    arguments = [mpy, str(DRIVER), *[str(CORPUS / f"{name}.py") for name in modules]]
    lines = [" ".join([entry, *(str(word) for word in words)]) for entry, words in cases]
    made = subprocess.run(
        arguments, input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=300,
        check=False,
    )
    assert made.returncode == 0, f"{mpy}: the reference did not run: {made.stderr}"
    answered = [line for line in made.stdout.splitlines() if line.strip()]
    assert len(answered) == len(cases), (
        f"{mpy}: {len(cases)} cases, {len(answered)} answers: {made.stdout!r} {made.stderr!r}"
    )
    out = []
    for line in answered:
        if line.startswith("!"):
            out.append(line)
        else:
            out.append(tuple(int(word) for word in line.split()))
    return out


def signed(word: int) -> int:
    return word if word < (1 << 63) else word - (1 << 64)


# ---- the cases ---------------------------------------------------------------------------------

# pairs and grids the in-domain answers must match the reference on: every intermediate
# stays a signed 64-bit word, every shift count is 0..63, every divisor is not zero
GRID = (0, 1, 2, 5, -1, -3, 7, -7, 100, -100)
PAIRS = [(a, b) for a in GRID for b in GRID]
MUL_PAIRS = [(a, b) for a in (0, 1, 2, 5, -1, -3, 1 << 20, -(1 << 20)) for b in (0, 1, 2, -3, 1 << 20)]
DIVIDENDS = (1, 5, -1, -3, 7, -7, 55, -55, 100, -100)
DIVISORS = (1, 2, 3, -2, 7, -7)
DIV_PAIRS = [(a, b) for a in DIVIDENDS for b in DIVISORS]
BIT_PAIRS = [(0, 0), (1, 2), (5, 3), (-1, 5), (-8, -2), (0xF0F0, 0x0FF0), (1 << 20, 1 << 10), (-(1 << 20), 5)]
SHIFT_PAIRS = [(a, n) for a in (0, 1, 2, 3, -1, -3, 1 << 10) for n in (0, 1, 5, 10, 42)]
WORD_PAIRS = [(a, n) for a in (0, 1, -1, -8, 3, -(1 << 20), (1 << 20) - 1) for n in (0, 1, 2, 6, 31, 32, 62, 63)]
COMPARE_PAIRS = [(a, b) for a in (0, 1, -1, 5, -5, 1 << 40, -(1 << 40)) for b in (0, 1, -1, 5, -5)]
CHAINED = [(a, b, c) for a in (0, 1, -1, 5) for b in (0, 1, -1, 5) for c in (0, 1, -1, 5) if (a, b, c) in ((0, 1, 2), (2, 1, 0), (1, 1, 1), (-5, 0, 5), (5, 0, -5), (-1, -1, 0), (0, -1, -1), (3, 3, 4), (4, 3, 3), (-2, -1, 0), (0, -1, -2), (1, 0, 1))]
CHAINED4 = [(0, 1, 2, 3), (3, 2, 1, 0), (1, 1, 1, 1), (-2, -1, 0, 1), (1, 0, -1, -2), (0, 2, 1, 3), (5, 5, 6, 6), (-6, -6, -5, -5)]
LOGIC_PAIRS = [(a, b) for a in (0, 1, 2, -1, 5) for b in (0, 1, 2, -1, 5)]


def cases_of(module: str, entry: str, taken) -> list[tuple[str, str, tuple[int, ...]]]:
    return [(module, entry, tuple(words)) for words in taken]


IN_CASES: list[tuple[str, str, tuple[int, ...]]] = [
    # the word arithmetic (words.py)
    *cases_of("words", "add", PAIRS),
    *cases_of("words", "sub", PAIRS),
    *cases_of("words", "mul", MUL_PAIRS),
    *cases_of("words", "floordiv", DIV_PAIRS),
    *cases_of("words", "mod", DIV_PAIRS),
    *cases_of("words", "band", BIT_PAIRS),
    *cases_of("words", "bor", BIT_PAIRS),
    *cases_of("words", "bxor", BIT_PAIRS),
    *cases_of("words", "shl", SHIFT_PAIRS),
    *cases_of("words", "shr", WORD_PAIRS),
    *cases_of("words", "neg", [(a,) for a in GRID]),
    *cases_of("words", "invert", [(a,) for a in GRID]),
    *cases_of("words", "calc", PAIRS),
    # comparisons, chained forms, and/or/not (compare.py)
    *cases_of("compare", "eq", COMPARE_PAIRS),
    *cases_of("compare", "ne", COMPARE_PAIRS),
    *cases_of("compare", "lt", COMPARE_PAIRS),
    *cases_of("compare", "le", COMPARE_PAIRS),
    *cases_of("compare", "gt", COMPARE_PAIRS),
    *cases_of("compare", "ge", COMPARE_PAIRS),
    *cases_of("compare", "chain3", CHAINED),
    *cases_of("compare", "chain4", CHAINED4),
    *cases_of("compare", "both", LOGIC_PAIRS),
    *cases_of("compare", "either", LOGIC_PAIRS),
    *cases_of("compare", "valued_and", LOGIC_PAIRS),
    *cases_of("compare", "valued_or", LOGIC_PAIRS),
    *cases_of("compare", "negate", [(a,) for a in (0, 1, 2, -1)]),
    # branching (branch.py)
    *cases_of("branch", "classify", [(a,) for a in GRID]),
    *cases_of("branch", "abs_of", [(a,) for a in GRID]),
    *cases_of("branch", "early", [(x, n) for x in (5, -5, 50, 1) for n in (0, 3, 30)]),
    *cases_of("branch", "signed_area", [(w, h) for w in (3, -3, 0) for h in (4, -4, 0)]),
    # loops: while, break, continue, the names carried (loops.py)
    *cases_of("loops", "sum_to", [(n,) for n in (0, 1, 2, 5, 10, 20)]),
    *cases_of("loops", "sum_even", [(n,) for n in (0, 1, 4, 9, 10)]),
    *cases_of("loops", "steps", [(x, limit) for x in (0, 1, 20, -20, 3) for limit in (10, 0, 1)]),
    *cases_of("loops", "gcd", [(a, b) for a in (12, -12, 17, 0, 7) for b in (18, 5, 0, -8)]),
    # the tuple return and its out cells (pair.py)
    *cases_of("pair", "main", [(x,) for x in (0, 1, 7, -3, 100, -(1 << 20))]),
    *cases_of("pair", "pair", [(x,) for x in (0, 1, 7, -3, 100, -(1 << 20))]),
    # calls and recursion inside the call-card budget (chain.py)
    *cases_of("chain", "add_one", [(x,) for x in GRID]),
    *cases_of("chain", "double_it", [(x,) for x in GRID]),
    *cases_of("chain", "fact", [(n,) for n in range(9)]),
    *cases_of("chain", "triangle", [(n,) for n in range(8)]),
    # a face of another import (borrow.py, against chain.double_it)
    *cases_of("borrow", "borrow", [(x,) for x in GRID]),
]

# Outside the domain both sides are pinned exactly, one-sidedly: what the run really
# does (a wrapped word or a refusal status) and what MicroPython really answers (a
# bignum or the exception it raises).  The sides differ here on purpose -- the pins are
# that each side is what it says it is, never a guess at the other.
OUT_CASES = [
    # wrapping arithmetic: the run's word vs the reference's bignum
    (("words", "add", (1 << 62, 1 << 62)), (OK, -(1 << 63)), 1 << 63),
    (("words", "sub", (-(1 << 62), (1 << 62) + 2)), (OK, (1 << 63) - 2), -(1 << 63) - 2),
    (("words", "mul", (1 << 62, 4)), (OK, 0), 1 << 64),
    (("words", "mul", ((1 << 40), (1 << 40))), (OK, 0), 1 << 80),
    (("words", "neg", (-(1 << 63),)), (OK, -(1 << 63)), 1 << 63),
    # division and remainder by zero: the guard's refusal vs ZeroDivisionError
    (("words", "floordiv", (1, 0)), (DIVISION_BY_ZERO, None), "!ZeroDivisionError"),
    (("words", "mod", (1, 0)), (DIVISION_BY_ZERO, None), "!ZeroDivisionError"),
    (("words", "floordiv", (-(1 << 63), 0)), (DIVISION_BY_ZERO, None), "!ZeroDivisionError"),
    # INT64_MIN with -1: the overflow guard vs the reference's answer
    (("words", "floordiv", (-(1 << 63), -1)), (OVERFLOW, None), 1 << 63),
    (("words", "mod", (-(1 << 63), -1)), (OVERFLOW, None), 0),
    # the shift count's domain vs the reference's shift or its own refusal
    (("words", "shl", (1, 64)), (SHIFT_COUNT, None), 1 << 64),
    (("words", "shl", (1, 70)), (SHIFT_COUNT, None), 1 << 70),
    (("words", "shl", (1, -1)), (SHIFT_COUNT, None), "!ValueError"),
    (("words", "shr", (-8, 64)), (SHIFT_COUNT, None), -1),
    # the call-card budget vs the reference's recursion
    (("chain", "triangle", (8,)), (CALL_DEPTH, None), 36),
    (("chain", "triangle", (20,)), (CALL_DEPTH, None), 210),
    # fact(8) is inside the budget (fact stops calling at 2: 8 frames counting the
    # entry); fact(9) is the first word past it
    (("chain", "fact", (9,)), (CALL_DEPTH, None), 362880),
]

# a sequence of calls on one image: the module's cell walks the same state the
# reference's namespace walks, case after case
CELL_SEQUENCE = [
    ("bump", (3,)),
    ("bump", (4,)),
    ("read", ()),
    ("bump_twice", (1,)),
    ("read", ()),
]
CELL_START = 5  # the initial word of cells.py's COUNT

REFUSALS = [
    ("def f(x):\n    def g(y):\n        return y\n    return g(x)\n", "nested def"),
    ("def f(x):\n    return (lambda y: y)(x)\n", "lambda"),
    ("def f(x):\n    return [y for y in x]\n", "comprehension"),
    ("class A:\n    pass\n", "class"),
    ("def f(x):\n    for i in x:\n        x = i\n    return x\n", "for loop"),
    ("import os\n", "import"),
    ("from os import path\n", "import"),
    ("def f(x):\n    try:\n        return x\n    except Exception:\n        return 0\n", "try/except"),
    ("def f(x):\n    raise ValueError()\n", "raise"),
    ("def f(x):\n    assert x\n    return x\n", "assert"),
    ("def f(x):\n    with x:\n        return 1\n", "with statement"),
    ("def f(x):\n    yield x\n", "generator"),
    ("async def f(x):\n    return x\n", "async def"),
    ("def f(x):\n    nonlocal x\n    return x\n", "nonlocal"),
    ("def f(x):\n    del x\n    return 0\n", "del"),
    ("def f(x):\n    y: int = x\n    return y\n", "annotated assignment"),
    ("def f(x):\n    return 1 if x else 2\n", "conditional expression"),
    ("def f(x):\n    return g(*x)\n", "starred"),
    ("def f(a, i):\n    return a[i]\n", "subscript"),
    ("def f(x):\n    return x.real\n", "attribute"),
    ("def f(x):\n    return [x]\n", "list"),
    ("def f(x):\n    return {x: x}\n", "dict"),
    ("def f(x):\n    return {x}\n", "set"),
    ("def f(x):\n    return f'{x}'\n", "f-string"),
    ("def f(x):\n    return (y := x)\n", "assignment expression"),
    ("def f(x):\n    match x:\n        case 1:\n            return 1\n    return 0\n", "match"),
    ("def f(x):\n    return 1.5\n", "as a value"),
    ("def f(x):\n    return x / 2\n", "true division"),
    ("def f(x):\n    return x ** 2\n", "power"),
    ("def f(x):\n    return x @ x\n", "the @ operator"),
    ("def f(x):\n    print(x)\n    return x\n", "print"),
    ("def f(x):\n    return len(x)\n", "a call of"),
    ("def f(x):\n    y = x = 1\n    return y\n", "chained assignment"),
    ("def f(x):\n    return\n", "bare return"),
    ("def f(x):\n    if x:\n        return 1, 2\n    return 3\n", "on one path"),
    ("def f(x):\n    return (x,)\n", "one-tuple"),
    ("def f(x):\n    return ()\n", "empty return"),
    ("def f(x):\n    a = [1]\n    return (x, *a)\n", "starred return"),
    ("def f(x):\n    while x:\n        x -= 1\n    else:\n        return 1\n    return 0\n", "else on a while"),
    ("@staticmethod\ndef f(x):\n    return x\n", "decorator"),
    ("def f(x=1):\n    return x\n", "default"),
    ("def f(*args):\n    return 1\n", "default, *args"),
    ("def f(x: int):\n    return x\n", "annotation"),
    ("def f(x):\n    return x\ndef f(x):\n    return x + 1\n", "defined twice"),
    ("def f(x):\n    y = x\n    if x:\n        y = x + 1\n    return z\n", "a name the subset cannot bind"),
    ("def f(x):\n    return y\n", "a name the subset cannot bind"),
    ("def f(x):\n    if x:\n        y = 1\n    return y\n", "bound on one path of a join"),
    ("def f(x):\n    while x:\n        y = 1\n        x -= 1\n    return y\n", "cannot see it bound on every path"),
    ("def f(x):\n    global NOPE\n    NOPE = x\n    return NOPE\n", "names no module-level binding"),
    ("def f(x):\n    y = x + 1\n", "falls off the end"),
    ("def f(x):\n    break\n    return x\n", "outside a loop"),
    ("def f(x):\n    continue\n    return x\n", "outside a loop"),
    ("def g(a, b):\n    return a + b\ndef f(x):\n    return g(x)\n", "takes"),
    ("def g(a, b):\n    return a + b\ndef f(x):\n    return g(x, 1, 2)\n", "takes"),
    ("def g(a, b):\n    return a + b\ndef f(x):\n    return g(x, b=1)\n", "keyword argument"),
    ("COUNT = 1\ndef f(x):\n    return COUNT(x)\n", "is a cell"),
    ("def f(x):\n    return 'hi'\n", "string as a value"),
    ("def f(x):\n    return 0x1_0000_0000_0000_0000\n", "does not fit"),
    ("X = f(1)\ndef f(x):\n    return x\n", "not an integer constant"),
    ("X = 'hi'\ndef f(x):\n    return x\n", "not an integer constant"),
    ("def f(x):\n    y = (x, x)\n    return x\n", "as a value"),
    ("print('hi')\n", "at module level"),
    ("if 1:\n    X = 2\n", "at module level"),
    ("X = 1\nX = 2\ndef f(x):\n    return x\n", "bound twice"),
    ("X = -0x8000_0000_0000_0001\ndef f(x):\n    return x\n", "does not fit"),
]


# ---- the image and the run --------------------------------------------------------------------

@pytest.fixture(scope="module")
def program():
    """The corpus imported into one image and called on the host runner (the ground
    truth seat of ``tests/host/runner.c``): the real interpreter of ``native/graph.c``,
    the out cells live, a call one run."""
    image = tree.load(STARTER)
    for name in MODULES:
        rxpy.import_py_file(image, CORPUS / f"{name}.py", module=name)
    mapping = rxpy.import_py_file(image, CORPUS / "borrow.py", module="borrow", known=KNOWN)
    assert mapping == {"borrow": "borrow.borrow"}
    assert image.check() == []  # the deltas link: the graph vocabulary is the emitter's
    programs = compose.programs(image)

    composer = compose.Composer(image)
    cells: dict[int, bytearray] = {}
    objects: dict[int, bytes | bytearray] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
            continue
        path = image.path(handle)
        if not path.startswith(tuple(f"{name}." for name in (*MODULES, "borrow"))):
            continue
        identity = int(values["object_id"])
        data = bytearray(bytes.fromhex(values.get("data", "")))
        cells[identity] = data
        objects[identity] = data
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "SemanticOperation":
            continue
        callee = composer.fields(handle).get("callee")
        target = image.resolve(callee) if callee else None
        if target is not None and image.path(target) in programs:
            objects[compose.object_id(image, target)] = programs[image.path(target)]

    memory = rxf_native.Memory(1 << 16)

    def call(module: str, entry: str, words: tuple[int, ...]):
        """One call: the answer word (and the out words of a tuple return)."""
        arity = rxpy.parse((CORPUS / f"{module}.py").read_text(), module).signatures[entry].outs
        given = [word & U64 for word in words]
        outs = []
        for index in range(arity):
            identity = rximport.object_id_of(f"{module}.{entry}.out_{index}")
            cell = cells[identity]
            cell[:] = bytes(len(cell))  # a clean out cell per case
            outs.append(cell)
            given += [identity, 24]  # the handle run-entry ARGS takes (identity, offset)
        status, value = rxf_native.run(
            programs[f"{module}.{entry}"],
            struct.pack(f"<{len(given)}Q", *given),
            memory,
            objects=objects,
        )
        answered = [signed(int.from_bytes(cell[24:32], "little", signed=True)) for cell in outs]
        return status, signed(value), answered

    def cell_of(module: str, name: str) -> bytearray:
        return cells[rximport.object_id_of(f"{module}.{name}")]

    yield type("Run", (), {"call": staticmethod(call), "cell_of": staticmethod(cell_of)})()
    memory.close()


@pytest.fixture(scope="module", params=MPYS or [None], ids=lambda p: seat_of(p) if p else "no-mpy")
def reference(request):
    """The reference answers, from a real MicroPython run of the same corpus source."""
    if request.param is None:
        pytest.skip(
            "no MicroPython unix binary in the workspace (set RXPY_MPY): the differential "
            "pins need the reference really run; the shape and refusal pins still stand"
        )
    answers: dict[tuple[str, str, tuple[int, ...]], object] = {}
    sequence: list[object] = []
    groups: dict[str, list[tuple[str, tuple[int, ...]]]] = {
        name: [] for name in (*MODULES, "borrow")
    }
    for module, entry, words in IN_CASES:
        groups.setdefault(module, []).append((entry, words))
    for (module, entry, words), _, _ in OUT_CASES:
        groups.setdefault(module, []).append((entry, words))
    groups["cells"] = list(CELL_SEQUENCE)
    modules_for = {
        "borrow": ("chain", "borrow"),
        "cells": ("cells",),
    }
    for module, cases in groups.items():
        if not cases:
            continue
        taken = mp_lines(request.param, modules_for.get(module, (module,)), cases)
        for case, line in zip(cases, taken, strict=True):
            answers[(module, case[0], case[1])] = line
            if module == "cells":
                sequence.append(line)  # the state walks: read twice, answered twice
    return type(
        "Reference", (), {"answers": answers, "sequence": sequence, "mpy": request.param}
    )()


def answered(reference, module: str, entry: str, words: tuple[int, ...]):
    return reference.answers[(module, entry, words)]


# ---- the grammar: what the subset refuses, by name ---------------------------------------------

@pytest.mark.parametrize(("source", "words"), REFUSALS, ids=[f"{n:02d}" for n in range(len(REFUSALS))])
def test_a_shape_outside_the_subset_is_refused_by_name(source: str, words: str):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(source, "m")
    assert words.lower() in str(problem.value).lower(), (
        f"the refusal does not name the shape ({words}): {problem.value}"
    )


def test_a_refusal_names_the_line():
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of("def f(x):\n    return x\n    for i in x:\n        x = i\n    return x\n", "m")
    assert "line 3" in str(problem.value)


def test_a_helper_outside_the_subset_is_skipped_unless_asked_for():
    """the promise of ``only``: a corpus file may carry helpers this pass cannot lower"""
    text = "def good(x):\n    return x + 1\ndef bad(x):\n    for i in x:\n        x = i\n    return x\n"
    found = rxpy.graphs_of(text, "m", only={"good"})
    assert set(found) == {"good"}
    with pytest.raises(rxpy.Refused):
        rxpy.graphs_of(text, "m", only={"bad"})


# ---- the mapping ------------------------------------------------------------------------------

def test_a_module_maps_to_one_graph_per_def():
    graphs = rxpy.graphs_of((CORPUS / "chain.py").read_text(), "chain")
    assert set(graphs) == {"add_one", "double_it", "fact", "triangle"}
    for name, graph in graphs.items():
        assert graph.path == f"chain.{name}"
        assert graph.module == "chain"
        assert graph.blocks  # blocks of operations, not a single shape


def test_a_call_is_a_call_card_naming_the_callee_never_an_inline():
    graphs = rxpy.graphs_of((CORPUS / "chain.py").read_text(), "chain")
    cards = [
        (op.callee, op.opcode)
        for block in graphs["double_it"].blocks
        for op in block.ops
    ]
    assert ("chain.add_one", "CallCallback") in cards
    # recursion through the card too: fact's card names fact's own Function
    recursive = [
        op.callee
        for block in graphs["fact"].blocks
        for op in block.ops
        if op.opcode == "CallCallback"
    ]
    assert "chain.fact" in recursive


def test_a_face_of_another_import_is_a_card_naming_that_path():
    """``known`` maps the name to the Function a prior import landed: the card names
    that path (borrow's call of chain.double_it), never a body of its own"""
    graphs = rxpy.graphs_of((CORPUS / "borrow.py").read_text(), "borrow", known=KNOWN)
    cards = [
        op.callee
        for block in graphs["borrow"].blocks
        for op in block.ops
        if op.opcode == "CallCallback"
    ]
    assert "chain.double_it" in cards


def test_the_out_cell_is_one_object_for_the_callee_and_every_caller():
    """docs/23 4.1: what lands is the caller's, not a copy -- one object id, one payload"""
    graphs = rxpy.graphs_of((CORPUS / "pair.py").read_text(), "pair")

    def out_cells(graph):
        return {
            value.path.rsplit(".", 1)[-1]: value
            for value in graph.values
            if value.kind == "Object" and "_out_" in value.path
        }

    callee = out_cells(graphs["pair"])
    caller = out_cells(graphs["main"])
    assert set(callee) == {"pair_pair_out_0", "pair_pair_out_1"}
    assert set(caller) == {"pair_pair_out_0", "pair_pair_out_1"}
    for name in callee:
        assert callee[name].object_id == caller[name].object_id, name
        identity = callee[name].object_id
        # the handle its three words over the payload (identity, generation, offset)
        assert callee[name].payload == struct.pack("<3Q", identity, 1, 24) + bytes(8)


def test_a_tuple_return_writes_the_out_cells_of_the_def():
    graphs = rxpy.graphs_of((CORPUS / "pair.py").read_text(), "pair")
    assert graphs["pair"].results  # one result value per return
    stores = [
        op
        for block in graphs["pair"].blocks
        for op in block.ops
        if op.opcode == "CallCallback" and op.callee == rxpy.leaf("wrapping_add_uint64_t")
    ]
    assert stores  # the out word = the cell's payload address + the handle's offset


def test_the_signature_counts_the_words_a_run_entry_takes():
    src = rxpy.parse((CORPUS / "pair.py").read_text(), "pair")
    assert src.signatures["pair"].params == 1
    assert src.signatures["pair"].outs == 2
    assert src.signatures["pair"].arguments == 5  # the word and two handles (identity, offset)
    assert src.signatures["main"].outs == 0
    assert src.signatures["main"].arguments == 1


def test_the_import_records_the_claim_and_the_delta_links():
    image = tree.load(STARTER)
    spec = "full,repository=https://example.org/repo.git,commit=" + "a" * 40 + ",license=MIT"
    made = rximport.Provenance.parse(spec)
    mapping = rxpy.import_py_file(image, CORPUS / "branch.py", module="branch", provenance=made)
    assert mapping == {name: f"branch.{name}" for name in ("classify", "abs_of", "early", "signed_area")}
    assert image.check() == []
    assert made.importer == "rxpy"  # the fact this strand stands on
    assert made.was_once_python is True  # docs/23 5's marker: this module was Python
    report = made.report()
    assert report.startswith("provenance: full\n")
    assert f"file: {CORPUS / 'branch.py'}" in report or "branch.py" in report


# ---- the differential: in-domain answers match MicroPython exactly -----------------------------

@pytest.mark.parametrize(
    ("module", "entry", "words"),
    IN_CASES,
    ids=[f"{m}.{e}{list(w)}" for m, e, w in IN_CASES],
)
def test_in_domain_the_run_answers_what_micropython_answers(program, reference, module, entry, words):
    want = answered(reference, module, entry, words)
    status, value, outs = program.call(module, entry, words)
    assert status == OK, f"{module}.{entry}{words}: the run refused ({status})"
    if outs:
        assert tuple(outs) == want, f"{module}.{entry}{words}: out words {outs}, reference {want}"
    else:
        assert value == want[0], f"{module}.{entry}{words}: answered {value}, reference {want}"


# ---- the differential: outside the domain, pinned one-sidedly ---------------------------------

@pytest.mark.parametrize(
    ("module", "entry", "words", "rxf", "reference_pin"),
    [(m, e, w, r, p) for (m, e, w), r, p in OUT_CASES],
    ids=[f"{m}.{e}{list(w)}" for (m, e, w), _, _ in OUT_CASES],
)
def test_out_of_domain_is_pinned_one_sidedly(
    program, reference, module, entry, words, rxf, reference_pin
):
    """The two sides differ here on purpose.  Each is pinned to what it really does:
    the run answers a wrapped word or refuses with a status (never a wrong number),
    MicroPython answers a bignum or raises (never a wrong number either)."""
    want_status, want_value = rxf
    status, value, _ = program.call(module, entry, words)
    assert status == want_status, f"{module}.{entry}{words}: status {status}, pinned {want_status}"
    if want_value is not None:
        assert value == want_value, f"{module}.{entry}{words}: answered {value}, pinned {want_value}"
    if reference_pin is not None:
        assert answered(reference, module, entry, words) == (
            reference_pin
            if isinstance(reference_pin, str)
            else (reference_pin,)
        ), f"{module}.{entry}{words}: the reference's own answer changed"


def test_the_reference_raises_where_the_run_refuses(reference):
    """the refusal kinds MicroPython really raises, named -- not a guess"""
    assert answered(reference, "words", "floordiv", (1, 0)) == "!ZeroDivisionError"
    assert answered(reference, "words", "shl", (1, -1)) == "!ValueError"


# ---- the module's state: one cell, walked case after case --------------------------------------

def test_a_sequence_of_calls_walks_the_module_cell_both_sides(program, reference):
    cell = program.cell_of("cells", "COUNT")
    cell[:] = (CELL_START & U64).to_bytes(len(cell), "little")  # the cell's initial word
    for (entry, words), want in zip(CELL_SEQUENCE, reference.sequence, strict=True):
        status, value, _ = program.call("cells", entry, words)
        assert status == OK, f"cells.{entry}{words}: the run refused ({status})"
        assert value == want[0], f"cells.{entry}{words}: answered {value}, reference {want}"


def test_the_cell_of_the_module_is_the_object_of_one_id():
    graphs = rxpy.graphs_of((CORPUS / "cells.py").read_text(), "cells")
    for graph in graphs.values():
        cells = [v for v in graph.values if v.kind == "Object" and v.path.endswith("global_COUNT")]
        for found in cells:
            assert found.object_id == rximport.object_id_of("cells.COUNT")
            assert found.payload == (CELL_START & U64).to_bytes(8, "little")


# ---- the seams: the status branch of a leaf, the marker, and the deltas' exact text ------------

# Every corpus delta pinned to its text (sha256 of the delta the emitter writes). The
# seams are additive: a delta that names none of the new wiring is byte-identical to the
# pre-seam corpus, text and digest. Two deltas flip to the new wiring on purpose and say
# so here, never blurred: words.py and loops.py divide (``//`` and ``%``) and their
# division calls now carry the leaf's own status as a status branch (``LOp.status_value``
# -- the starter's ``<op>_status``) where the guards pre-lowered the same checks by
# hand. The pre-seam digests are named beside them; the guard-block path stays where the
# leaf's own check is narrower than the contract (the shift count), so nothing else
# moved and no refusal flipped (OUT_CASES pins both eras' statuses).
DELTA_DIGESTS = {
    "borrow.py": "2db7686d18799d195bc15d984f9daa2fce9086062a120a7d6683bc2031cb8d30",
    "branch.py": "734d2afa1c473302e0c89c12b2e9e83ced2ce00d5054e74067d065ed4743c2e6",
    "cells.py": "f5591f167b0d557201de219df4ea5d08038ec5dc9f816553ed1bf21e2aa23a00",
    "chain.py": "7a2c7317f5f334d0343fc666d164861ac3054846c714a8543bf17adff85ce033",
    "compare.py": "5cd4a15a8d877df7fb1b533287e9110da6a59d053663bd226f3256876ab6510a",
    # flipped to the status branch (was e08e1dd83d5870848af94044db4b570676afb433abec115572c794d58f27680a)
    "loops.py": "59a43506ca958b30f91dd22b78e2446b07f1bd08b581c0c89012de66240ec304",
    "pair.py": "c87bc7d78b02ca880803f9fe82fb9ae6aa19d358ee6a5ec3ae1ebb07fabfc30d",
    # flipped to the status branch (was 3325828d099b12951bbf9b210d2b222440b6b33500628a4ea59a76836aa01503)
    "words.py": "f9b592b31cb7dad5134e6734fbefeb26e741f47d4ddcbfafba84df6dfec8c3d1",
}


def delta_of(module: str, known: dict[str, str] | None = None) -> str:
    """One corpus module's delta as the corpus carries it (the text the digests hash)."""
    source = CORPUS / f"{module}.py"
    graphs = rxpy.graphs_of(source.read_text(), module, known=known)
    return rxpy.emit_delta(list(graphs.values()), module)


def test_the_corpus_deltas_keep_their_exact_text():
    """The seams' hard rule: additive means byte-identical where the new wiring is not
    named. Every corpus delta keeps its exact text and digest; the two that flip are
    named in DELTA_DIGESTS and their wiring is pinned one call at a time below."""
    for source in sorted(CORPUS.glob("*.py")):
        known = KNOWN if source.stem == "borrow" else None
        digest = hashlib.sha256(delta_of(source.stem, known).encode()).hexdigest()
        assert digest == DELTA_DIGESTS[source.name], f"{source.name}: the delta text moved"


def test_a_division_wires_the_leafs_status_as_a_status_branch():
    """The status_value seam adopted where it is a clean win (divmod): the divide and
    remainder calls carry the leaf's own status as a U32 value (the starter's
    ``<op>_status`` slot), the call's block splits on it (the status is ``uint32_t``, so
    status 0 takes the first edge and the run goes on), and the refusal path ends in a
    Refuse carrying the same status word -- the leaf's own refusal, never a literal the
    guard spelled ahead of it. words.calc mixes this with the guarded shift (below): one
    module, both shapes, nothing rewritten."""
    calc = rxpy.graphs_of((CORPUS / "words.py").read_text(), "words")["calc"]
    wired = [
        op for block in calc.blocks for op in block.ops if op.status_value is not None
    ]
    assert len(wired) == 4  # two division sites, each the quotient and remainder leaf
    assert {op.callee for op in wired} == {
        "pymergetic.rxf.numeric.division.divide_int64_t",
        "pymergetic.rxf.numeric.division.remainder_int64_t",
    }
    by_path = {block.path: block for block in calc.blocks}
    for op in wired:
        status = op.status_value
        assert status is not None
        assert status.type_path == "uint32_t" and status.width == 4
        assert status.owner_op == op.path and status.owner_block in by_path
        block = by_path[status.owner_block]
        assert block.terminator == "Branch" and block.condition == ("value", status.path)
        [onward, refused] = [by_path[successor] for successor, _ in block.successors]
        assert block.successors == [(onward.path, []), (refused.path, [])]
        assert refused.terminator == "Refuse"
        assert refused.terminator_value == ("value", status.path)  # the leaf's own word
        assert onward.terminator != "Refuse"
    # the flip's exact width, in the emitted text: two division sites (``//`` and ``%``),
    # each lowering the two status-carrying leaves of floor semantics (quotient and
    # remainder) -- four status words, and nothing else
    text = rxpy.emit_delta(
        list(rxpy.graphs_of((CORPUS / "words.py").read_text(), "words", only={"calc"}).values()),
        "words",
    )
    assert text.count('status_value="') == 4
    for op in wired:
        assert op.status_value is not None
        assert f'status_value="{op.status_value.path}"' in text
        assert f'name="{op.status_value.path.rsplit(".", 1)[-1]}" value_type="uint32_t"' in text


def test_the_guard_blocks_stay_where_the_leaf_check_is_narrower():
    """The seam is additive, not a rewrite: a shift's count is guarded ahead of the call
    (a leaf takes a u32 count where the domain is a word -- its own check is narrower
    than the contract), those graphs move no byte, and no shift call carries a status
    word. The run's statuses are pinned where they always were (OUT_CASES: SHIFT_COUNT)."""
    words = rxpy.graphs_of((CORPUS / "words.py").read_text(), "words")
    for name in ("shl", "shr"):
        shifts = [
            op for block in words[name].blocks for op in block.ops
            if op.callee and "shift" in op.callee
        ]  # fmt: skip
        assert shifts, name
        assert all(op.status_value is None for op in shifts)
        assert any(block.terminator == "Refuse" for block in words[name].blocks)


def test_without_a_status_value_the_emission_is_todays_exactly():
    """None is today's behavior exactly: the field defaults to None on every op a
    lowering builds without naming it, and such an op serializes with no status word --
    the deltas of the corpus modules that do not divide are byte-identical to their
    pre-seam text (the digests above pin it), and carry no status_value at all."""
    plain = rxpy.graphs_of((CORPUS / "compare.py").read_text(), "compare")
    assert all(
        op.status_value is None
        for graph in plain.values()
        for block in graph.blocks
        for op in block.ops
    )
    assert 'status_value="' not in delta_of("compare")


def test_a_status_value_of_another_type_refuses_to_emit():
    """A status branch is read by its type (the composer takes a Branch on a ``uint32_t``
    value as MODE_ZERO): the emitter refuses to write a status word typed as anything
    else -- loudly, never a flag that composes as an ordinary condition."""
    graph = rxpy.graphs_of("def f(a, b):\n    return a // b\n", "m")["f"]
    op = next(
        op for block in graph.blocks for op in block.ops if op.status_value is not None
    )
    assert op.status_value is not None
    op.status_value.type_path = "uint64_t"  # the wrong type, on purpose
    with pytest.raises(rxemit.ParseError, match="a status value is typed"):
        rxemit.emit_delta([graph], "m")


def test_the_marker_was_once_python_rides_the_modules_provenance():
    """docs/23 5's marker: the provenance record carries the slot (was_once_python) and
    the Python strand sets it for every module it compiles -- whatever the tier, the
    report speaks it. The C strand leaves the slot False and its report unmarked
    (pinned in tests/test_import.py)."""
    image = tree.load(STARTER)
    made = rximport.Provenance.parse(
        "full,repository=https://example.org/repo.git,commit=" + "a" * 40 + ",license=MIT"
    )
    assert made.was_once_python is False  # the slot's default: nothing sets it for C
    rxpy.import_py_file(image, CORPUS / "branch.py", module="branch", provenance=made)
    assert made.was_once_python is True
    assert made.report().splitlines()[:2] == ["provenance: full", "marker: was once Python"]
