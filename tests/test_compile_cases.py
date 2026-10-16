"""The compiler against the interpreter on every program there is (docs/19, WP12).

Every program of the starter (three sets of arguments each) and every program of
`graph_programs` (all the opcodes, every shape of leaf, loops, switches, edges with copies, and
the programs that the interpreter must refuse) is run twice from the same memory: by
`rxf_graph_run`, and as the code that `rxf_compile_program` writes for it, on x86_64 and aarch64
in an emulator and on wasm32 in node. The status, the value and the whole memory must be equal. A
program that is whole is compiled; one that the interpreter refuses is refused by the compiler too,
or compiled to code that refuses the same way.
"""

import shutil

import graph_header
import pytest

from pymergetic.rxftk.core import tree

from . import graph_programs as programs
from . import graph_world as world
from .snippets import STARTER

UNSUPPORTED = graph_header.numbers()["RXF_REFUSE_UNSUPPORTED"]
# What the compiler may say about a program that is not whole: what the interpreter says, or that
# it leaves the program to the interpreter.
REFUSALS = {6, 13, 14, UNSUPPORTED}  # out of range, unbound, type mismatch


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def code(image):
    return world.code_of(image)


@pytest.fixture(scope="module")
def cases(image):
    synthetic = programs.synthetic()
    return world.cases(image) + synthetic + world.channeled(synthetic)


def broken(case):
    return case.name.startswith("refuses ")


def check(cases, interpreted, compiled):
    for case, want, have in zip(cases, interpreted, compiled, strict=True):
        if have.outcome is None:
            # refused: a program that is not whole, or one the interpreter refuses too (main reads
            # objects that only a linked image has: tests/test_compile.py compiles it there)
            assert have.status in REFUSALS, (case.name, have.status, have.at)
            assert broken(case) or have.status == want.status, (
                case.name,
                have.status,
                have.at,
            )
            continue
        assert have.outcome.status == want.status, case.name
        assert have.outcome.out == want.out, case.name
        assert have.outcome.memory == want.memory, case.name


def test_nearly_every_program_is_compiled(cases, code):
    """What is not compiled is a broken program, or `main` and what it calls (objects of an image)."""
    left = []
    for case in cases:
        have = world.compiled("x86_64", code, case)
        if have.outcome is None and not broken(case):
            left.append(case.name.split("#")[0])
    assert sorted(set(left)) == [
        "pymergetic.rxf.application.main",
        "pymergetic.rxf.library.checkout_total",
    ]


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_compiled_code_does_what_the_interpreter_does(machine, cases, code):
    interpreted = [world.emulated(machine, code, case) for case in cases]
    compiled = [world.compiled(machine, code, case) for case in cases]
    check(cases, interpreted, compiled)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_compiled_code_does_what_the_interpreter_does_on_wasm32(cases, code):
    interpreted = world.in_node(code, cases)
    compiled = world.compiled_in_node(code, cases)
    check(cases, interpreted, compiled)
    left = {
        case.name.split("#")[0]
        for case, have in zip(cases, compiled, strict=True)
        if have.outcome is None and not broken(case)
    }
    assert left == {
        "pymergetic.rxf.application.main",
        "pymergetic.rxf.library.checkout_total",
    }


def test_a_program_that_is_refused_is_refused_the_same_when_it_is_compiled(code):
    for case, status in programs.refusals():
        have = world.compiled("x86_64", code, case)
        if have.outcome is not None:
            assert have.outcome.status == status, case.name
        else:
            assert have.status in REFUSALS, case.name
