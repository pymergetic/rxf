"""The gaps that sit inside the Python strand's subset (docs/23 4.3 and 7): five closures.

No machine change: each of these is a lowering the existing words and cells already allow.

* **default parameters and keyword arguments**: a default is the constant the def folded when it ran;
  a call that leaves a parameter out passes that word, a keyword argument binds by the
  parameter's name; the same for methods and for ``__init__`` (the fields it sets);
* **None**: it is the one value word ``RXF_WORD_NONE`` (docs/23 4.9 step 10) -- ``None`` lowers to
  it wherever a word goes, ``is`` / ``is not None`` is word identity with it, and a ``= None``
  default folds to the very word a caller passes explicitly (the lowerer stages that word in the
  call's operands: a call card is never short).  ``is`` between two words stays refused by name.
  The ABSENCE of an answer is separate: a def whose returns are all bare or ``return None``, or
  that falls off its end, is a procedure -- its Signature has no Result, the image says so, and
  its callers call it as a statement;
* **tables**: a tuple, a list or bytes at module level is one named data cell (a FixedArray); an index
  is normalized (negative counts from the end) and checked, ``len`` is a constant, a list may be
  stored through an index; strings stay refused by name;
* **every cell is in the image**: a module-level binding no def reads or writes is emitted, so it
  can be imported, and a module that is only cells exists;
* **a class nothing instantiates** has its methods checked over a probe instance.

Differentially against a real MicroPython (docs/23 decision 10): the corpus
``tests/rxpy_corpus/subset`` is IMPORTED by the reference and compiled into one image; every pure
case answers the same, three call streams walk the same state (a procedure answers ``None``
there and nothing here), and outside the domain the sides are pinned one-sidedly (a table index
past the length is IndexError in the reference and a refused run here).

Seats (all-platforms): the host runner (``tests/host/runner.c``); a toolchain lane. No guest
interpreter on the image yet, so no guest seat is claimed.
"""

import struct
import subprocess
from pathlib import Path

import graph_header
import pytest
import rxpy

from pymergetic.rxftk.core import tree

from . import rxpy_run
from .mp_reference import micropythons, seat_of
from .snippets import STARTER

CORPUS = Path(__file__).parent / "rxpy_corpus" / "subset"
DRIVER = Path(__file__).parent / "rxpy_mp_import.py"
MPYS = micropythons()
OUT_OF_RANGE = graph_header.numbers()["RXF_REFUSE_OUT_OF_RANGE"]
NONE_WORD = graph_header.numbers()["RXF_WORD_NONE"]  # the value word of None (docs/23 4.9 step 10)

# the corpus, in the order an import needs it
NAMES = (
    "sub_defaults", "sub_procs", "sub_procs_user", "sub_tables", "sub_cells", "sub_cells_user",
)
GRID = (0, 1, -1, 2, 5, -3, 7)

PURE = [
    *(("sub_defaults.short_calls", (x,)) for x in GRID),
    *(("sub_defaults.keyword_calls", (x,)) for x in GRID),
    *(("sub_defaults.mixed", (x,)) for x in GRID),
    *(("sub_defaults.add", (a, b)) for a in (0, 1, -4) for b in (0, 3, -2)),
    *(("sub_defaults.scale", (x, k, off)) for x in (0, 2, -3) for k in (1, 2) for off in (0, 10)),
    *(("sub_tables.prime", (i,)) for i in range(-6, 6)),
    *(("sub_tables.signed", (i,)) for i in range(-3, 3)),
    *(("sub_tables.mask", (i,)) for i in range(-8, 8)),
    *(("sub_tables.high", (i,)) for i in range(-3, 3)),  # bytes above 127 stay unsigned words
    *(("sub_tables.tail", (i,)) for i in range(9)),
    ("sub_tables.sizes", ()),
    *(("sub_tables.total", (n,)) for n in range(7)),
    ("sub_cells_user.both", ()),
    ("sub_cells_user.snap", ()),
    ("sub_cells_user.spare", ()),
]

DEFAULTS = [
    ("sub_defaults.snapshot", ()),
    ("sub_defaults.run", (3,)),
    ("sub_defaults.run", (-1,)),
    ("sub_defaults.snapshot", ()),
    ("sub_defaults.fresh.bump", (4,)),  # a method called directly: the Function fresh__bump
    ("sub_defaults.both.bump", (2,)),
    ("sub_defaults.snapshot", ()),
]
PROCS = [
    ("sub_procs.read", ()),
    ("sub_procs.tick", (3,)),  # a procedure answers None: no word
    ("sub_procs.read", ()),
    ("sub_procs.note", (9,)),
    ("sub_procs.drive", (5,)),
    ("sub_procs.drive", (1,)),
    ("sub_procs.reset", ()),
    ("sub_procs.read", ()),
    ("sub_procs.gauge", (4,)),
    ("sub_procs.g.raise_by", (2,)),  # a procedure method, called directly
    ("sub_procs.g.lower", (1,)),
    ("sub_procs.g.lower", (1000,)),  # returns early: still None
    ("sub_procs.g.read", ()),
    ("sub_procs_user.go", (3,)),  # a procedure of another module, called as a statement
    ("sub_procs.read", ()),
]
TABLES = [
    ("sub_tables.sum_slots", ()),
    ("sub_tables.put", (1, 99)),
    ("sub_tables.put", (-1, 7)),  # a negative index stores through the end
    ("sub_tables.grow", (0, 5)),
    ("sub_tables.grow", (-2, -4)),
    *(("sub_tables.slot", (i,)) for i in range(-4, 4)),
    ("sub_tables.sum_slots", ()),
]
SEQUENCES = {"defaults": DEFAULTS, "procs": PROCS, "tables": TABLES}

# outside the domain: the reference raises, the run refuses -- each side pinned to what it does
OUT = [
    ("sub_tables.prime", (6,)),
    ("sub_tables.prime", (-7,)),
    ("sub_tables.mask", (8,)),
    ("sub_tables.mask", (-9,)),
    ("sub_tables.high", (3,)),
    ("sub_tables.signed", (3,)),
    ("sub_tables.slot", (4,)),
    ("sub_tables.slot", (-5,)),
    ("sub_tables.put", (4, 1)),
    ("sub_tables.grow", (-5, 1)),
    ("sub_tables.tail", (9,)),
]


@pytest.fixture(scope="module")
def image():
    scratch = tree.load(STARTER)
    for name in NAMES:
        rxpy.import_py_file(scratch, CORPUS / f"{name}.py", module=name)
    assert scratch.check() == []  # the deltas link: every cell is written once, by its owner
    return scratch


@pytest.fixture(scope="module")
def run(image):
    with rxpy_run.running(image, NAMES) as made:
        yield made


@pytest.fixture(scope="module", params=MPYS or [None], ids=lambda p: seat_of(p) if p else "no-mpy")
def mpy(request) -> str:
    if request.param is None:
        pytest.skip(
            "no MicroPython unix binary in the workspace (set RXPY_MPY): the differential pins "
            "need the reference really run; the shape and refusal pins still stand"
        )
    return request.param


def mp_answers(mpy: str, cases: list[tuple[str, tuple[int, ...]]]) -> list[object]:
    """One run of the reference: the corpus IMPORTED, the cases called in order in one process."""
    lines = [" ".join([entry, *(str(word) for word in words)]) for entry, words in cases]
    made = subprocess.run(
        [mpy, str(DRIVER), str(CORPUS), *NAMES],
        input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=300, check=False,
    )
    assert made.returncode == 0, f"{mpy}: the reference did not run: {made.stderr}"
    answered = [line for line in made.stdout.splitlines() if line.strip()]
    assert len(answered) == len(cases), f"{len(cases)} cases, {len(answered)} answers: {made.stdout!r}"
    return [rxpy_run.parse_answer(line) for line in answered]


def run_case(run, entry: str, words: tuple[int, ...]) -> tuple[int | str, ...]:
    module, name = rxpy_run.split_entry(NAMES, entry)
    return rxpy_run.answer(run, module, name, words)


@pytest.fixture(scope="module")
def pure_reference(mpy):
    return dict(zip(map(repr, PURE), mp_answers(mpy, PURE), strict=True))


# ---- the differential --------------------------------------------------------------------------

@pytest.mark.parametrize(("entry", "words"), PURE, ids=[f"{e}{list(w)}" for e, w in PURE])
def test_in_the_subset_the_run_answers_what_micropython_answers(run, pure_reference, entry, words):
    assert run_case(run, entry, words) == pure_reference[repr((entry, words))]


@pytest.mark.parametrize("sequence", sorted(SEQUENCES))
def test_a_stream_of_calls_walks_the_state_as_micropython_does(image, mpy, sequence):
    cases = SEQUENCES[sequence]
    wanted = mp_answers(mpy, cases)
    with rxpy_run.running(image, NAMES) as walk:
        got = [run_case(walk, entry, words) for entry, words in cases]
    assert got == wanted, [
        (case, g, w) for case, g, w in zip(cases, got, wanted, strict=True) if g != w
    ]


def test_the_streams_are_not_vacuous():
    """a procedure answers None on some path of the streams, and the reference's answers vary"""
    entries = {entry for cases in SEQUENCES.values() for entry, _ in cases}
    assert {"sub_procs.tick", "sub_procs.g.raise_by", "sub_procs_user.go"} <= entries
    assert {"sub_defaults.fresh.bump", "sub_tables.put", "sub_tables.grow"} <= entries


def test_outside_the_domain_the_reference_raises_and_the_run_refuses(run, mpy):
    wanted = mp_answers(mpy, OUT)
    assert set(wanted) == {"!IndexError"}
    for entry, words in OUT:
        module, name = rxpy_run.split_entry(NAMES, entry)
        status, _, _ = run.call(module, name, words)
        assert status == OUT_OF_RANGE, (entry, words, status)


# ---- (a) defaults ------------------------------------------------------------------------------

def test_a_signature_carries_the_names_and_the_defaults(image):
    src = rxpy.parse(
        (CORPUS / "sub_defaults.py").read_text(), "sub_defaults", rxpy.Resolver(image)
    )
    scale = src.signatures["scale"]
    assert scale.names == ("x", "k", "off")
    assert scale.defaults == (2, 10)  # off = LIMIT: the cell's word when the def ran
    assert src.signatures["fresh__bump"].names == ("k",)  # self is not a parameter of the call
    assert src.signatures["fresh__bump"].defaults == (1,)
    assert src.instances["fresh"].words == [0, 1]
    assert src.instances["other"].words == [5, 1]
    assert src.instances["fast"].words == [0, 7]
    assert src.instances["both"].words == [2, 3]


def test_a_call_that_leaves_a_parameter_out_passes_the_default_word():
    graphs = rxpy.graphs_of(
        "def g(a, b=3):\n    return a + b\ndef f(x):\n    return g(x)\n", "m"
    )
    literals = {v.literal for v in graphs["f"].values if v.kind == "Literal"}
    assert 3 in literals  # the card passes two words: x and the folded default


# ---- (b) None / procedures ---------------------------------------------------------------------

def test_a_procedure_has_a_signature_with_no_result_and_a_word_function_has_one(image):
    resolver = rxpy.Resolver(image)
    for name in ("tick", "reset", "note"):
        assert resolver.signature_of("sub_procs", name).procedure, name
    assert resolver.signature_of("sub_procs", "g__raise_by").procedure
    assert resolver.signature_of("sub_procs", "g__lower").procedure
    for name in ("read", "drive", "gauge"):
        assert not resolver.signature_of("sub_procs", name).procedure, name
    assert not resolver.signature_of("sub_defaults", "add").procedure


def test_a_procedure_of_another_module_is_called_as_a_statement_and_refused_as_a_value(image):
    resolver = rxpy.Resolver(image)
    ok = rxpy.graphs_of(
        "import sub_procs\ndef f(x):\n    sub_procs.tick(x)\n    return x\n", "m", resolver=resolver
    )
    assert "sub_procs.tick" in {
        op.callee for block in ok["f"].blocks for op in block.ops if op.opcode == "CallCallback"
    }
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(
            "import sub_procs\ndef f(x):\n    return sub_procs.tick(x)\n", "m", resolver=resolver
        )
    assert "answers None" in str(problem.value)


def test_none_lowers_to_the_none_word_and_is_is_word_identity(served_image):
    """The value word of ``None`` (``RXF_WORD_NONE``, docs/23 4.9 step 10): ``None`` lowers to
    it wherever a word goes, ``is`` / ``is not None`` is word identity with it (word equality on
    the one singleton a word carries), and a ``= None`` default folds to the very word a caller
    passes explicitly -- the lowerer stages that word in the call's operands, because a call card
    is never short."""
    graphs = rxpy.graphs_of(
        "def g(a, b=None):\n    return a\n"
        "def f(x):\n    y = None\n    return (g(x) is None) + (x is not None) + (y is not None)\n",
        "m", resolver=rxpy.Resolver(served_image),
    )
    words = [v.literal for v in graphs["f"].values + graphs["g"].values if v.kind == "Literal"]
    assert words.count(NONE_WORD) == 1  # one None literal: every use reads the same word
    staged = [
        operand
        for block in graphs["f"].blocks
        for op in block.ops
        if op.opcode == "CallCallback" and op.callee == "m.g"
        for operand in op.inputs
    ]
    assert ("literal", NONE_WORD) in staged  # the = None default, staged in the call's operands
    identity = [
        op
        for block in graphs["f"].blocks
        for op in block.ops
        if op.opcode == "CallCallback"
        and op.callee == "pymergetic.rxf.numeric.compare.equal_uint64_t"
    ]
    assert len(identity) == 3  # is None, is not None, y is not None: each a word equality
    assert all(("literal", NONE_WORD) in op.inputs for op in identity), [
        op.inputs for op in identity
    ]


# ---- (c) tables --------------------------------------------------------------------------------

def test_a_table_is_one_data_cell_padded_so_it_is_never_a_word_cell(image):
    primes = image.resolve("sub_tables.PRIMES")
    assert primes is not None and image.type_name(primes) == "FixedArray"
    assert bytes(image.get(primes)["payload"]) == struct.pack("<6Q", 2, 3, 5, 7, 11, 13)[:48]
    masks = image.get(image.resolve("sub_tables.MASKS"))["payload"]
    assert bytes(masks) == bytes([1, 2, 4, 8, 16, 32, 64, 128]).ljust(16, b"\0")  # a byte each
    slots = image.get(image.resolve("sub_tables.SLOTS"))["payload"]
    assert bytes(slots) == struct.pack("<4Q", 10, 20, 30, 40)
    # a one-element table would be 8 bytes: it is padded, so the importer cannot take it for a cell
    src = rxpy.parse("ONE = (5,)\nEMPTY = b''\n", "m")
    assert len(src.cell_bytes("ONE")) == 16 and len(src.cell_bytes("EMPTY")) == 16


def test_len_of_a_table_is_a_constant_and_an_index_is_one_checked_load():
    graphs = rxpy.graphs_of(
        "T = (4, 5, 6)\ndef n():\n    return len(T)\ndef at(i):\n    return T[i]\n", "m"
    )
    assert [op.callee for b in graphs["n"].blocks for op in b.ops if op.opcode == "CallCallback"] == []
    refusals = [b for b in graphs["at"].blocks if b.terminator == "Refuse"]
    assert len(refusals) == 1  # the bounds check: one Refuse path carrying the status


def test_a_table_of_another_module_is_not_a_cell_the_importer_can_read(image):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(
            "import sub_tables\ndef f(i):\n    return sub_tables.PRIMES\n", "m",
            resolver=rxpy.Resolver(image),
        )
    assert "is not a word cell" in str(problem.value)


# ---- (d) every cell is in the image ------------------------------------------------------------

def test_a_cell_no_def_touches_is_in_the_image_and_importable(image):
    spare = image.resolve("sub_defaults.SPARE")
    assert spare is not None and image.type_name(spare) == "uint64_t"
    assert bytes(image.get(spare)["payload"]) == (42).to_bytes(8, "little")
    # the importer's snapshot is that word
    assert bytes(image.get(image.resolve("sub_cells_user.SPARE"))["payload"]) == (42).to_bytes(8, "little")


def test_a_module_that_is_only_cells_exists_and_is_a_module(image):
    assert image.resolve_module("sub_cells") == "module"
    assert image.resolve("sub_cells.ALPHA") is not None
    assert image.resolve("sub_cells.BETA") is not None
    assert bytes(image.get(image.resolve("sub_cells.BETA"))["payload"]) == struct.pack("<q", -3)


def test_a_module_with_neither_cells_nor_defs_still_imports_nothing(tmp_path):
    only_a_class = tmp_path / "bare.py"
    only_a_class.write_text("class A:\n    pass\n")
    assert rxpy.import_py_file(tree.load(STARTER), only_a_class, module="bare") == {}


# ---- the refusals, by name ---------------------------------------------------------------------

def helper_image() -> object:
    scratch = tree.load(STARTER)
    for name in ("sub_defaults", "sub_procs"):
        rxpy.import_py_file(scratch, CORPUS / f"{name}.py", module=name)
    return scratch


REFUSALS = [
    # defaults and keywords
    ("def f(x, y=g(1)):\n    return x\n", "not an integer constant"),
    ("def f(x, y=[]):\n    return x\n", "not an integer constant"),
    ("def f(x, *, y=1):\n    return x\n", "keyword-only"),
    ("def f(x, **k):\n    return x\n", "**kwargs"),
    ("def g(a, b=1):\n    return a\ndef f(x):\n    return g()\n", "misses a"),
    ("def g(a, b=1):\n    return a\ndef f(x):\n    return g(1, 2, 3)\n", "takes 2 values"),
    ("def g(a, b=1):\n    return a\ndef f(x):\n    return g(1, a=2)\n", "more than one value for a"),
    ("def g(a, b=1):\n    return a\ndef f(x):\n    return g(1, c=2)\n", "no parameter of that name"),
    ("def g(a, b=1):\n    return a\ndef f(x):\n    return g(1, **x)\n", "**kwargs"),
    ("import sub_defaults\ndef f(x):\n    return sub_defaults.add(x)\n", "carries no defaults"),
    ("import sub_defaults\ndef f(x):\n    return sub_defaults.add(a=x, b=1)\n", "carries no parameter names"),
    # procedures and None
    ("def f(x):\n    return None\ndef g(x):\n    y = f(x)\n    return y\n", "answers None"),
    ("def f(x):\n    return None\ndef g(x):\n    return f(x) + 1\n", "answers None"),
    ("def f(x):\n    if x:\n        return 1\n    return None\n", "a word on one path and None"),
    ("def f(x):\n    if x:\n        return\n    return x\n", "a word on one path and None"),
    ("def f(x, y):\n    return x is y\n", "identity lowers only against None"),
    ("import sub_procs\ndef f(x):\n    return 1 + sub_procs.tick(x)\n", "answers None"),
    # tables
    ("T = (1, 2)\ndef f(i):\n    return T\n", "is a table, not a word"),
    ("T = (1, 2)\ndef f(i):\n    T[i] = 3\n    return 0\n", "immutable"),
    ("T = b'ab'\ndef f(i):\n    T[i] += 1\n    return 0\n", "immutable"),
    ("T = [1, 2]\ndef f(i):\n    return T[1:2]\n", "slice"),
    ("T = [1, 2]\ndef f(i):\n    return T.append(i)\n", "something that is not an imported module"),
    ("T = [1, [2]]\n", "not an integer constant"),
    ("T = (1, f)\ndef f():\n    return 1\n", "not an integer constant"),
    ("S = 'text'\n", "is a string"),
    ("T = (1, 2)\nT = (3, 4)\n", "is bound twice"),
    ("T = (1, 2)\ndef T(x):\n    return x\n", "is bound twice"),
    ("def f(a, i):\n    return a[i]\n", "not a table of this module"),
    ("T = (1, 2)\ndef f(T):\n    return T[0]\n", "not a table of this module"),
    ("T = (1, 2)\ndef f(i):\n    global T\n    T = 3\n    return 0\n", "names no module-level binding"),
]


@pytest.fixture(scope="module")
def served_image():
    return helper_image()


@pytest.mark.parametrize(("source", "words"), REFUSALS, ids=[f"{n:02d}" for n in range(len(REFUSALS))])
def test_what_the_closures_do_not_take_is_refused_by_name(served_image, source, words):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(source, "m", resolver=rxpy.Resolver(served_image))
    assert words.lower() in str(problem.value).lower(), (
        f"the refusal does not name the shape ({words}): {problem.value}"
    )
