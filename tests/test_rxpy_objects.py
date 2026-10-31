"""The Python strand's simple classes (docs/23 1 and 4.3): an instance is a cell with fields.

The mapping table of docs/23 says a class is a Type and an instance an object cell. The graph
machine has exactly one operand that reaches a cell, ``OBJECT``: it names the cell by its durable
id, a constant of the program (``native/graph.h``) -- there is no operand that resolves a handle
held in a slot, and no allocation leaf a program can call. So the honest slice is this one:

* an instance is a module-level ``NAME = Class(constants)``: one named cell whose words are the
  fields ``__init__`` sets, in the order it first sets them (``__init__`` runs at compile time over
  the constants -- the import-time run the reference makes);
* a method is compiled once per instance with ``self`` bound to that cell: ``a__bump`` is the
  Function of ``Counter.bump`` on ``a``, ``self.n`` is a load at the cell's address plus the field's
  offset, ``self.n += x`` the load, the leaf, the store, ``self.bump(k)`` a call card of the same
  instance's method (a tuple return is the out cells of docs/23 4.1);
* the receiver is always a cell known at compile time: an object held in a name, a parameter, or
  passed to a function has no cell the graph can resolve, and making an instance at run time needs
  an allocation and a handle operand the machine does not have -- both refused by name.

What is pinned: the lowering (layout, one graph per method per instance, the cell as an Object
value), the semantics differentially against a real MicroPython (docs/23 decision 10) -- calls in
order on both sides, methods called directly (``m.a.bump``) and through functions, state walking
across instances and across modules -- and the refusals by name and line.

Seats (all-platforms): the host runner (``tests/host/runner.c``); a toolchain lane. No guest
interpreter on the image yet, so no guest seat is claimed.
"""

import struct
import subprocess
from pathlib import Path

import pytest
import rximport
import rxpy

from pymergetic.rxftk.core import tree

from . import rxpy_run
from .mp_reference import micropythons, seat_of
from .snippets import STARTER

CORPUS = Path(__file__).parent / "rxpy_corpus" / "objects"
DRIVER = Path(__file__).parent / "rxpy_mp_import.py"
OK = 0
MPYS = micropythons()
NAMES = ("obj_counter", "obj_user")
GRID = (0, 1, -1, 2, 5, -3, 7)

# each sequence is one stream of calls on one image (the instances walk), checked against one
# MicroPython process that makes the same calls in the same order
COUNTERS = [
    ("obj_counter.run_a", (2,)),
    ("obj_counter.run_a", (-1,)),
    ("obj_counter.run_b", (3,)),
    ("obj_counter.peek_both", ()),
    ("obj_counter.twice_a", (4,)),
    ("obj_counter.pair_a", ()),
    ("obj_counter.poke_a", (-100,)),
    ("obj_counter.peek_both", ()),
    ("obj_counter.poke_a", (30,)),
    *(("obj_counter.limited", (k,)) for k in (3, 3, 3, 3, 3, 3, 3, -5)),
    ("obj_counter.pair_a", ()),
    ("obj_counter.climb", (40,)),
    ("obj_counter.climb", (10,)),
    ("obj_counter.step_of_b", ()),
    ("obj_counter.peek_both", ()),
    # a method called directly on its instance: the Function a__bump / b__peek / a__both
    ("obj_counter.a.bump", (1,)),
    ("obj_counter.b.bump", (-2,)),
    ("obj_counter.a.peek", ()),
    ("obj_counter.b.peek", ()),
    ("obj_counter.a.both", ()),  # a tuple through the out cells
    ("obj_counter.b.both", ()),
    ("obj_counter.a.reset", (9,)),
    ("obj_counter.a.twice", (1,)),
    ("obj_counter.a.over", (30,)),
    ("obj_counter.a.both", ()),
]
FLAGS = [
    *(("obj_counter.flip", ()) for _ in range(5)),
    ("obj_counter.flag_on", ()),
    ("obj_counter.f.toggle", ()),
    ("obj_counter.f.is_on", ()),
    *(("obj_counter.plain", (x,)) for x in GRID),
    *(("obj_counter.p.plus", (x,)) for x in GRID),
    ("obj_counter.p.three", ()),
]
ACROSS = [
    ("obj_user.drive", (1,)),
    ("obj_user.drive", (2,)),
    ("obj_user.through_name", (3,)),
    ("obj_counter.peek_both", ()),
    ("obj_user.drive", (-4,)),
    ("obj_user.bound", ()),
    ("obj_user.through_name", (-1,)),
    ("obj_counter.pair_a", ()),
]
SEQUENCES = {"counters": COUNTERS, "flags": FLAGS, "across": ACROSS}


@pytest.fixture(scope="module")
def image():
    scratch = tree.load(STARTER)
    for name in NAMES:
        rxpy.import_py_file(scratch, CORPUS / f"{name}.py", module=name)
    assert scratch.check() == []  # the deltas link: an instance's cell is the module's own
    return scratch


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
    return [line if line.startswith("!") else tuple(int(w) for w in line.split()) for line in answered]


def run_case(run, entry: str, words: tuple[int, ...]) -> tuple[int, ...]:
    module, name = rxpy_run.split_entry(NAMES, entry)
    status, value, outs = run.call(module, name, words)
    assert status == OK, f"{entry}{words}: the run refused ({status})"
    return tuple(outs) if outs else (value,)


# ---- the differential --------------------------------------------------------------------------

@pytest.mark.parametrize("sequence", sorted(SEQUENCES))
def test_a_stream_of_calls_walks_the_instances_as_micropython_does(image, mpy, sequence):
    cases = SEQUENCES[sequence]
    wanted = mp_answers(mpy, cases)
    with rxpy_run.running(image, NAMES) as run:
        got = [run_case(run, entry, words) for entry, words in cases]
    assert got == wanted, [
        (case, g, w) for case, g, w in zip(cases, got, wanted, strict=True) if g != w
    ]


def test_the_streams_are_not_vacuous():
    """every method of every class is on some path of the streams, and a tuple comes back"""
    entries = {entry for cases in SEQUENCES.values() for entry, _ in cases}
    for method in ("bump", "peek", "both", "twice", "reset", "over"):
        assert f"obj_counter.a.{method}" in entries, method
    assert {"obj_counter.f.toggle", "obj_counter.f.is_on", "obj_counter.p.plus"} <= entries


# ---- the lowering ------------------------------------------------------------------------------

def test_the_layout_is_the_order_init_first_sets_the_fields(image):
    src = rxpy.parse(
        (CORPUS / "obj_counter.py").read_text(), "obj_counter", rxpy.Resolver(image)
    )
    assert src.classes["Counter"].fields == ["n", "step", "calls"]
    assert src.classes["Flag"].fields == ["on"]
    assert src.classes["Plain"].fields == []
    assert src.instances["a"].words == [10, 3, 0]
    assert src.instances["b"].words == [0, 5, 0]
    assert src.instances["f"].words == [1]  # True is the word 1


def test_an_instance_is_one_cell_of_its_fields_words(image):
    cell = image.resolve("obj_counter.a")
    assert cell is not None and image.type_name(cell) == "FixedArray"
    assert bytes(image.get(cell)["payload"]) == struct.pack("<3Q", 10, 3, 0)
    assert bytes(image.get(image.resolve("obj_counter.b"))["payload"]) == struct.pack("<3Q", 0, 5, 0)
    one = image.resolve("obj_counter.f")  # one field: a word cell like any other
    assert one is not None and image.type_name(one) == "uint64_t"


def test_a_method_is_one_graph_per_instance_over_that_instances_cell(image):
    resolver = rxpy.Resolver(image)
    for instance in ("a", "b"):
        for method in ("bump", "peek", "both", "twice", "reset", "over"):
            assert image.resolve(f"obj_counter.{instance}__{method}") is not None
    both = resolver.signature_of("obj_counter", "a__both")
    assert (both.params, both.outs) == (0, 2)  # self is not a word of the call
    bump = resolver.signature_of("obj_counter", "a__bump")
    assert (bump.params, bump.outs) == (1, 0)
    graphs = rxpy.graphs_of(
        (CORPUS / "obj_counter.py").read_text(), "obj_counter", resolver=resolver
    )
    for instance in ("a", "b"):
        cells = {
            v.object_id for v in graphs[f"{instance}__bump"].values
            if v.kind == "Object" and not v.foreign
        }
        assert cells == {rximport.object_id_of(f"obj_counter.{instance}")}


def test_a_field_is_a_load_at_the_cells_address_plus_the_offset(image):
    graphs = rxpy.graphs_of(
        (CORPUS / "obj_counter.py").read_text(), "obj_counter", resolver=rxpy.Resolver(image)
    )
    literals = {v.literal for v in graphs["a__bump"].values if v.kind == "Literal"}
    assert {8, 16} <= literals  # step at 8, calls at 16; n is the cell's own address
    cards = [
        op.callee for block in graphs["a__twice"].blocks for op in block.ops
        if op.opcode == "CallCallback" and op.callee == "obj_counter.a__bump"
    ]
    assert len(cards) == 2  # self.bump(k) twice: a call card of the same instance's method


def test_the_importer_calls_a_function_not_the_instance(image):
    graphs = rxpy.graphs_of(
        (CORPUS / "obj_user.py").read_text(), "obj_user", resolver=rxpy.Resolver(image)
    )
    cards = {
        op.callee for g in graphs.values() for b in g.blocks for op in b.ops
        if op.opcode == "CallCallback"
    }
    assert {"obj_counter.run_a", "obj_counter.peek_both", "obj_counter.run_b"} <= cards


# ---- the refusals, by name ---------------------------------------------------------------------

INIT_X = "class A:\n    def __init__(self):\n        self.x = 1\n"
REFUSALS = [
    ("class A(B):\n    pass\n", "no inheritance"),
    ("class A(metaclass=M):\n    pass\n", "no inheritance"),
    ("@decorate\nclass A:\n    pass\n", "decorator on class A"),
    ("class A:\n    x = 1\n", "a class attribute or a nested class"),
    ("class A:\n    class B:\n        pass\n", "a class attribute or a nested class"),
    ("class A:\n    @staticmethod\n    def f(x):\n        return x\n", "staticmethod"),
    ("class A:\n    def __repr__(self):\n        return 1\n", "the special method"),
    ("class A:\n    def f():\n        return 1\n", "takes no self"),
    ("class A:\n    def f(self):\n        return 1\n    def f(self):\n        return 2\n", "defined twice"),
    ("class A:\n    def __init__(self, a):\n        print(a)\n", "does more than set"),
    ("class A:\n    def __init__(self, a):\n        if a:\n            self.x = a\n", "does more than set"),
    ("class A:\n    def __init__(self, a=1):\n        self.x = a\na = A()\n", "takes a default"),
    ("class A:\n    def __init__(self, a):\n        self.x = a\nz = A(1, 2)\n", "takes 1 values"),
    ("class A:\n    def __init__(self, a):\n        self.x = a\nz = A(a=1)\n", "keyword or starred"),
    ("def f(n):\n    return n\nclass A:\n    def __init__(self, a):\n        self.x = a\nz = A(f(1))\n", "not an integer constant"),
    ("class A:\n    def __init__(self, a):\n        self.x = a\ndef f(n):\n    z = A(n)\n    return n\n", "a call of class A inside a def"),
    (INIT_X + "    def m(self):\n        return self.y\na = A()\n", "no field y"),
    (INIT_X + "    def m(self):\n        self.y = 2\n        return 1\na = A()\n", "no field y"),
    (INIT_X + "    def m(self):\n        return self\na = A()\n", "is an instance, not a word"),
    (INIT_X + "    def m(self):\n        self = 1\n        return 1\na = A()\n", "rebinds self"),
    (INIT_X + "    def m(self, k=1):\n        return k\na = A()\n", "takes a default"),
    (INIT_X + "    def m(self):\n        return 1\na = A()\ndef f(n):\n    return a.m\n", "a method, not a field"),
    (INIT_X + "a = A()\ndef f(n):\n    return a.x(1)\n", "a field, not a method"),
    (INIT_X + "a = A()\ndef f(n):\n    return a.nope(1)\n", "no such method"),
    (INIT_X + "a = A()\ndef f(n):\n    return a\n", "is an instance, not a word"),
    (INIT_X + "    def m(self):\n        return 1\na = A()\ndef f(n):\n    return A.m(a)\n", "not an imported module or an instance"),
    (INIT_X + "a = A()\ndef f(a):\n    return a.x\n", "not an imported module or an instance"),
    (INIT_X + "a = A()\nb = a\n", "not an integer constant"),
    (INIT_X + "a = A()\na = A()\n", "is bound twice"),
    ("class A:\n    pass\ndef A(x):\n    return x\n", "is bound twice"),
]


@pytest.mark.parametrize(("source", "words"), REFUSALS, ids=[f"{n:02d}" for n in range(len(REFUSALS))])
def test_what_an_instance_cannot_be_is_refused_by_name(source, words):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(source, "m")
    assert words.lower() in str(problem.value).lower(), (
        f"the refusal does not name the shape ({words}): {problem.value}"
    )


def test_a_class_with_no_instance_compiles_to_nothing_by_design():
    """a class is a layout and methods; the graphs exist per instance, so a class nothing
    instantiates adds no Function (and checks no method body: a method is checked when an
    instance asks for its graph)"""
    graphs = rxpy.graphs_of(
        "class A:\n    def m(self):\n        for i in self:\n            pass\n"
        "def f(x):\n    return x\n",
        "m",
    )
    assert set(graphs) == {"f"}
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(
            "class A:\n    def m(self):\n        for i in self:\n            pass\n        return 1\n"
            "a = A()\n",
            "m",
        )
    assert "for loop" in str(problem.value)
