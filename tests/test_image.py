"""Functions of the linked starter run on x86_64, aarch64 and wasm32 and as the reference."""

import shutil

import link
import pytest

from pymergetic.rxftk.core import tree

from . import graph_world as world
from . import image_world as run
from .snippets import STARTER

MAIN = "pymergetic.rxf.application.main"
PICKS = [
    "pymergetic.rxf.algorithms.clamp",
    "pymergetic.rxf.algorithms.fold",
    "pymergetic.rxf.algorithms.binary_search",
    "pymergetic.rxf.foundation.heap.pin",
    "pymergetic.rxf.foundation.heap.release_borrow",
    "pymergetic.rxf.stdlib.flow.map_success",
    "pymergetic.rxf.stdlib.text.string_find",
    "pymergetic.rxf.stdlib.collections.hash_map_get",
    "pymergetic.rxf.algorithms.copy",
    "pymergetic.rxf.algorithms.compare",
    "pymergetic.rxf.algorithms.fill",
    MAIN,
]


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def code(image):
    return world.code_of(image)


def linked(image, machine):
    return link.link(image, machine, run.BASE)


@pytest.fixture(scope="module")
def runs(image):
    return run.runs(image, linked(image, "x86_64"), PICKS)


@pytest.fixture(scope="module")
def reference(image, runs):
    on = linked(image, "x86_64")
    return [run.referenced(on, image, r) for r in runs]


def test_the_linked_image_has_the_arena_in_it_byte_for_byte(image):
    on = linked(image, "x86_64")
    arena = image.to_bytes()
    start = on.data.index(arena[:192])
    assert on.data[start : start + len(arena)] == arena


def test_the_image_is_the_same_for_every_machine_but_its_function_slots(image):
    a, b, c = (linked(image, m) for m in ("x86_64", "aarch64", "wasm32"))
    assert len(a.data) == len(b.data) == len(c.data)
    assert a.table == b.table == c.table
    for x in (b, c):
        assert [x.data[s:e] for s, e in x.compared()] == [
            a.data[s:e] for s, e in a.compared()
        ]
    assert a.data[a.table[0] : a.table[1]] != b.data[b.table[0] : b.table[1]]


def test_the_runs_do_something(reference):
    assert 0 in {r.status for r in reference}
    assert any(any(r.out) for r in reference)


def test_x86_64_runs_what_the_reference_runs(image, runs, reference):
    on = linked(image, "x86_64")
    assert [run.emulated("x86_64", on, image, r) for r in runs] == reference


def test_aarch64_runs_what_the_reference_runs(image, runs, reference):
    on = linked(image, "aarch64")
    assert [run.emulated("aarch64", on, image, r) for r in runs] == reference


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wasm32_runs_what_the_reference_runs(image, code, runs, reference):
    assert run.in_node(code, linked(image, "wasm32"), runs) == reference


def test_the_main_of_the_starter_runs_and_returns_the_total(runs, reference):
    """main calls checkout_total, which reads six objects of the image and calls four leaves."""
    totals = [r for run, r in zip(runs, reference, strict=True) if run.function == MAIN]
    assert len(totals) == 3
    assert {(r.status, int.from_bytes(r.out[:4], "little")) for r in totals} == {
        (0, 570)
    }
