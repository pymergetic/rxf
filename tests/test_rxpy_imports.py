"""The Python strand's imports (docs/23 4.3 and 4.6): ``import`` is a lookup of named cells.

The image IS the module tree: a module of the image is a ``Module`` cell, its functions and word
cells are named cells under it, and ``import a.b`` / ``from a import b`` resolve over them
(``Image.resolve_module``, the children of the Module, a Function's Signature). The compiler keeps
no registry of what it compiled before: ``rxpy.Resolver`` reads the image, so a module imported by
any strand in any run is importable by name. What these pins hold:

* the lowering: ``m.f(x)`` is a call card naming ``m.f`` (never an inline), ``m.NAME`` is a load
  of the cell of ``m`` (an Object value that names the cell and carries its bytes, no second cell
  is written), ``from m import NAME`` is this module's own cell holding a snapshot;
* the semantics, differentially (docs/23 decision 10: **MicroPython is the reference**): the same
  corpus modules (``tests/rxpy_corpus/imports``) are IMPORTED by a real MicroPython -- its import
  machinery, packages and relative imports included -- and compiled into one image; every function
  answers the same word, a sequence of calls walks the same cells (a live read sees a write, a
  ``from`` snapshot does not);
* the refusals, by name and line: a module the image does not have, a name it does not export, a
  submodule no import loaded, a function or a module used as a value, an import inside a def.

Seats (all-platforms): the host runner (``tests/host/runner.c``, the real interpreter of
``native/graph.c``) -- a toolchain lane. No guest interpreter is on the image yet, so the guest side
of ``mp_import_stat`` is not claimed here.
"""

import subprocess
from pathlib import Path

import pytest
import rximport
import rxpy

from pymergetic.rxftk.core import tree

from . import rxpy_run
from .mp_reference import micropythons, seat_of
from .snippets import STARTER

CORPUS = Path(__file__).parent / "rxpy_corpus" / "imports"
DRIVER = Path(__file__).parent / "rxpy_mp_import.py"
OK = 0
MPYS = micropythons()

# the modules of the corpus, in the order an import needs them: (dotted name, file)
MODULES = [
    ("imp_base", "imp_base.py"),
    ("imp_user", "imp_user.py"),
    ("pkgx.leaf", "pkgx/leaf.py"),
    ("pkgx.sibling", "pkgx/sibling.py"),
    ("imp_pkg", "imp_pkg.py"),
]
NAMES = tuple(name for name, _ in MODULES)
GRID = (0, 1, 2, 5, -1, -3, 100)

# the cases that read no state another case writes: each is checked on its own
PURE = [
    *(("imp_user.via_module", (x,)) for x in GRID),
    *(("imp_user.via_alias", (x,)) for x in GRID),
    *(("imp_user.via_from", (x,)) for x in GRID),
    *(("imp_user.shadowed", (x,)) for x in GRID),
    ("imp_user.snapshot_limit", ()),
    *(("imp_user.pair_use", (a, b)) for a in (17, -17, 0, 100) for b in (5, -5, 3)),
    *(("imp_pkg.dotted", (x,)) for x in GRID),
    *(("imp_pkg.through_sibling", (x,)) for x in GRID),
    *(("imp_pkg.from_package", (x,)) for x in GRID),
    *(("imp_pkg.imported_fn", (x,)) for x in GRID),
    ("imp_pkg.depth_of", ()),
    *(("pkgx.sibling.via_leaf", (x,)) for x in GRID),
    *(("pkgx.sibling.via_name", (x,)) for x in GRID),
    ("pkgx.sibling.deep", ()),
    ("pkgx.leaf.depth", ()),
    ("imp_base.limit", ()),
]

# one stream of calls on one image: the cell of imp_base walks, the snapshot of imp_user stays
SEQUENCE = [
    ("imp_user.live_count", ()),
    ("imp_user.snapshot_count", ()),
    ("imp_user.push", (5,)),  # a from-imported function writes imp_base's cell through global
    ("imp_user.live_count", ()),
    ("imp_user.snapshot_count", ()),  # still the word the cell had when the import ran: 7
    ("imp_user.push_module", (3,)),
    ("imp_user.live_count", ()),
    ("imp_user.write_through", (40,)),  # imp_base.COUNT = v from another module
    ("imp_user.live_count", ()),
    ("imp_user.add_through", (2,)),  # imp_base.COUNT += v
    ("imp_base.bump", (1,)),  # the owner sees what the importer wrote
    ("imp_user.loop_through", (0,)),
    ("imp_user.loop_through", (1,)),
    ("imp_user.loop_through", (4,)),
    ("imp_user.snapshot_count", ()),
    ("imp_user.live_count", ()),
]


def compiled():
    """The corpus imported into one image, in the order an import needs."""
    image = tree.load(STARTER)
    for name, file in MODULES:
        rxpy.import_py_file(image, CORPUS / file, module=name)
    assert image.check() == []  # the deltas link: an import wrote no cell it does not own
    return image


@pytest.fixture(scope="module")
def image():
    return compiled()


@pytest.fixture(scope="module")
def run(image):
    """The image's modules on the host runner, built once for the pure cases."""
    with rxpy_run.running(image, NAMES) as made:
        yield made


def mp_answers(mpy: str, cases: list[tuple[str, tuple[int, ...]]]) -> list[object]:
    """One run of the reference: the corpus IMPORTED by the real machinery, the cases called in
    order in that one process; a line is the answer words, or ``!ExceptionName``."""
    lines = [" ".join([entry, *(str(word) for word in words)]) for entry, words in cases]
    made = subprocess.run(
        [mpy, str(DRIVER), str(CORPUS), *NAMES],
        input="\n".join(lines) + "\n", capture_output=True, text=True, timeout=300, check=False,
    )
    assert made.returncode == 0, f"{mpy}: the reference did not run: {made.stderr}"
    answered = [line for line in made.stdout.splitlines() if line.strip()]
    assert len(answered) == len(cases), f"{len(cases)} cases, {len(answered)} answers: {made.stdout!r}"
    return [line if line.startswith("!") else tuple(int(w) for w in line.split()) for line in answered]


@pytest.fixture(scope="module", params=MPYS or [None], ids=lambda p: seat_of(p) if p else "no-mpy")
def mpy(request) -> str:
    if request.param is None:
        pytest.skip(
            "no MicroPython unix binary in the workspace (set RXPY_MPY): the differential pins "
            "need the reference really run; the shape and refusal pins still stand"
        )
    return request.param


@pytest.fixture(scope="module")
def pure_reference(mpy):
    return dict(zip(map(repr, PURE), mp_answers(mpy, PURE), strict=True))


def run_case(run, entry: str, words: tuple[int, ...]) -> tuple[int, ...]:
    module, _, name = entry.rpartition(".")
    status, value, outs = run.call(module, name, words)
    assert status == OK, f"{entry}{words}: the run refused ({status})"
    return tuple(outs) if outs else (value,)


# ---- the differential --------------------------------------------------------------------------

@pytest.mark.parametrize(("entry", "words"), PURE, ids=[f"{e}{list(w)}" for e, w in PURE])
def test_an_imported_name_answers_what_micropython_answers(run, pure_reference, entry, words):
    assert run_case(run, entry, words) == pure_reference[repr((entry, words))]


def test_a_sequence_of_calls_walks_the_cells_and_the_snapshot_stays(mpy):
    """The live cell of ``imp_base`` walks (written through ``global``, through ``m.NAME = v``
    and ``m.NAME += v`` from another module) and every module sees the same word; the
    ``from imp_base import COUNT`` snapshot is the word at import time, on both sides."""
    wanted = mp_answers(mpy, SEQUENCE)
    with rxpy_run.running(compiled(), NAMES) as walk:
        got = [run_case(walk, entry, words) for entry, words in SEQUENCE]
    assert got == wanted
    # the pins above are not vacuous: the cell moved, and the snapshot did not
    assert got[0] != got[3] and got[1] == got[4] == (7,)


# ---- the lowering ------------------------------------------------------------------------------

def cards_of(graph) -> list[str]:
    return [
        op.callee for block in graph.blocks for op in block.ops
        if op.opcode == "CallCallback" and op.callee
    ]


def test_a_call_through_an_import_is_a_card_naming_the_imported_path(image):
    graphs = rxpy.graphs_of(
        (CORPUS / "imp_user.py").read_text(), "imp_user", resolver=rxpy.Resolver(image),
    )
    assert "imp_base.twice" in cards_of(graphs["via_module"])
    assert "imp_base.twice" in cards_of(graphs["via_alias"])  # the alias names the same path
    assert "imp_base.twice" in cards_of(graphs["via_from"])
    assert "imp_base.bump" in cards_of(graphs["push"])
    # a dotted import reaches the nested Module's path
    pkg = rxpy.graphs_of((CORPUS / "imp_pkg.py").read_text(), "imp_pkg", resolver=rxpy.Resolver(image))
    assert "pkgx.leaf.inc" in cards_of(pkg["dotted"])
    assert "pkgx.sibling.via_name" in cards_of(pkg["through_sibling"])


def test_an_imported_cell_is_the_owners_object_and_no_second_cell_is_written(image):
    graphs = rxpy.graphs_of(
        (CORPUS / "imp_user.py").read_text(), "imp_user", resolver=rxpy.Resolver(image),
    )
    values = [v for v in graphs["live_count"].values if v.kind == "Object"]
    assert [v.foreign for v in values] == [True]
    # the id the run resolves is the id of imp_base's own cell, carrying the bytes it has now
    assert values[0].object_id == rximport.object_id_of("imp_base.COUNT")
    assert values[0].payload == (7).to_bytes(8, "little")
    assert image.resolve("imp_user.imp_base_COUNT") is None
    # the importer owns exactly one cell of its own: the snapshot FIRST_COUNT (and LIMIT)
    own = {
        image.path(child).rsplit(".", 1)[-1] for child in image.children(image.resolve("imp_user"))
        if image.type_name(child) == "uint64_t"
    }
    assert own == {"FIRST_COUNT", "LIMIT"}


def test_the_snapshot_is_the_word_the_cell_had_when_the_import_ran(image):
    snapshot = image.get(image.resolve("imp_user.FIRST_COUNT"))["payload"]
    assert bytes(snapshot) == (7).to_bytes(8, "little")


def test_a_dotted_module_is_nested_cells_of_the_tree(image):
    assert image.resolve_module("pkgx") == "package"
    assert image.module_children("pkgx") == ["leaf", "sibling"]
    assert image.resolve_module("pkgx.leaf") == "module"
    assert image.resolve("pkgx.leaf.inc") is not None


def test_the_resolver_reads_the_shape_of_a_function_off_the_image(image):
    resolver = rxpy.Resolver(image)
    divide = resolver.signature_of("imp_base", "divide")
    assert (divide.params, divide.outs) == (2, 2)  # two words in, an out cell per answer
    twice = resolver.signature_of("imp_base", "twice")
    assert (twice.params, twice.outs) == (1, 0)


def test_an_import_binds_no_new_function_into_the_modules_map(image):
    """the mapping of ``import_py_file`` is the module's own defs: imports add none"""
    scratch = tree.load(STARTER)
    rxpy.import_py_file(scratch, CORPUS / "imp_base.py", module="imp_base")
    mapping = rxpy.import_py_file(scratch, CORPUS / "imp_user.py", module="imp_user")
    assert "twice" not in mapping and "via_module" in mapping
    assert image.resolve("imp_user.twice") is None


# ---- the refusals, by name ---------------------------------------------------------------------

REFUSALS = [
    ("import nope\n", "the image has no module nope"),
    ("import imp_base.nope\n", "the image has no module imp_base.nope"),
    ("from imp_base import nothing\n", "cannot import name nothing"),
    ("from nope import x\n", "the image has no module nope"),
    ("from imp_base import *\n", "import *"),
    ("from . import x\n", "relative import"),
    ("import imp_base\ndef f(x):\n    return imp_base.nope(x)\n", "no such attribute"),
    ("import pkgx\ndef f(x):\n    return pkgx.leaf.inc(x)\n", "no import loaded"),
    ("import imp_base\ndef f(x):\n    return imp_base.twice\n", "is a function, not a word"),
    ("import imp_base\ndef f(x):\n    return imp_base\n", "an imported module, not a word"),
    ("import imp_base\ndef f(x):\n    return imp_base.twice(x, 1)\n", "takes 1 values"),
    ("import imp_base\ndef f(x):\n    return imp_base.COUNT(x)\n", "is a cell, not a function"),
    ("import imp_base\ndef f(x):\n    imp_base.twice = x\n    return x\n", "not a word cell"),
    ("def f(x):\n    return x.y\n", "something that is not an imported module"),
    ("def f(x):\n    return x.y(1)\n", "something that is not an imported module"),
    ("def f(x):\n    import imp_base\n    return x\n", "an import inside a def"),
    ("import imp_base\ndef imp_base(x):\n    return x\n", "is bound twice"),
    ("import imp_base\nfrom imp_base import twice as imp_base\n", "is bound twice"),
]


@pytest.fixture(scope="module")
def base_image():
    scratch = tree.load(STARTER)
    for name, file in MODULES[:1] + MODULES[2:3]:
        rxpy.import_py_file(scratch, CORPUS / file, module=name)
    return scratch


@pytest.mark.parametrize(("source", "words"), REFUSALS, ids=[f"{n:02d}" for n in range(len(REFUSALS))])
def test_an_import_the_image_cannot_serve_is_refused_by_name(base_image, source, words):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of(source, "m", resolver=rxpy.Resolver(base_image))
    assert words.lower() in str(problem.value).lower(), (
        f"the refusal does not name the shape ({words}): {problem.value}"
    )


def test_an_import_without_an_image_is_refused_not_guessed():
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of("import imp_base\ndef f(x):\n    return x\n", "m")
    assert "no image to look it up in" in str(problem.value)


def test_a_refusal_of_an_import_names_the_line(base_image):
    with pytest.raises(rxpy.Refused) as problem:
        rxpy.graphs_of("import imp_base\nimport nope\n", "m", resolver=rxpy.Resolver(base_image))
    assert "line 2" in str(problem.value)
