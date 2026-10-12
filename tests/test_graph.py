"""Composed functions: the interpreter on three machines against a second interpreter.

Every program of the starter runs with three sets of arguments in a context in
memory. The Python interpreter (``graph_reference``) runs it with the leaves as
x86_64 machine code; ``rxf_graph_run`` runs it as x86_64 and aarch64 machine
code in an emulator and as wasm32 in node. Status, output and the whole memory
must be equal.
"""

import shutil

import graph_header
import pytest

from pymergetic.rxf.core import tree

from . import graph_programs as programs
from . import graph_world as world
from .snippets import STARTER

# Only main reads the objects of a linked image to reach them; tests/test_image.py runs it.
READ_FROM_OBJECTS = {
    "checked_add_uint32_t",
    "checked_subtract_uint32_t",
    "checked_multiply_uint32_t",
    "divide_uint32_t",
}
# The pump of the live layer serves a channel; tests/test_live_native.py runs it.
# The compiler writes machine code for a program; tests/test_compile.py runs it.
NOT_IN_PROGRAMS = {"live_pump", "uart_pump", "compile_program", "safe_point"}


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def code(image):
    return world.code_of(image)


@pytest.fixture(scope="module")
def cases(image):
    return world.cases(image) + programs.synthetic()


@pytest.fixture(scope="module")
def runs(code, cases):
    return [world.referenced(code, case) for case in cases]


@pytest.fixture(scope="module")
def reference(runs):
    return [outcome for outcome, _ in runs]


def different(cases, want, have):
    return [
        (case.name, w.status, h.status, w.out == h.out, w.memory == h.memory)
        for case, w, h in zip(cases, want, have, strict=True)
        if w != h
    ]


def test_there_is_a_program_for_every_semantic_function(cases):
    assert len(cases) == 58 * 3 + len(programs.synthetic())


def test_the_programs_succeed_and_refuse_both(reference):
    statuses = {r.status for r in reference}
    assert 0 in statuses and len(statuses) >= 3, statuses


def test_the_runs_reach_every_leaf_and_the_leaves_both_succeed_and_refuse(runs):
    seen: dict[str, set[int]] = {}
    for _, calls in runs:
        for leaf, status in calls:
            seen.setdefault(leaf, set()).add(status)
    assert set(seen) == set(graph_header.leaves()) - READ_FROM_OBJECTS - NOT_IN_PROGRAMS
    assert all(0 in statuses for statuses in seen.values()), seen
    assert len({s for statuses in seen.values() for s in statuses}) >= 4


def test_a_program_reads_and_writes_memory_at_an_address_it_computed(cases, reference):
    (found,) = [
        r for c, r in zip(cases, reference, strict=True) if c.name == "indirect"
    ]
    assert found.status == 0
    # loaded = the first word of the object (its id), and the successor was stored behind it
    assert int.from_bytes(found.out[:8], "little") == programs.BINDING + 1


def test_the_numeric_leaves_of_a_program_compute_what_python_computes(cases, reference):
    runs = {c.name: r for c, r in zip(cases, reference, strict=True)}
    for name, n, count in (
        ("bits 0x1234 << 4", 0x1234, 4),
        ("bits 1", 1, 1),
        ("bits 0", 0, 0),
    ):
        want = ((n & 0xFF00) | (((n << count) & (2**64 - 1)) >> 8)) - 1 & (2**64 - 1)
        assert runs[name].status == 0
        assert int.from_bytes(runs[name].out[:8], "little") == want, name
        assert runs[name].out[8] == int(want == n), name
    # a shift by the whole width gives 0, not the count taken modulo 64
    assert runs["bits count 64"].status == 0
    assert int.from_bytes(runs["bits count 64"].out[:8], "little") == 2**64 - 1


def test_x86_64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("x86_64", code, case) for case in cases]
    assert different(cases, reference, have) == []


def test_aarch64_runs_what_the_reference_runs(code, cases, reference):
    have = [world.emulated("aarch64", code, case) for case in cases]
    assert different(cases, reference, have) == []


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_wasm32_runs_what_the_reference_runs(code, cases, reference):
    assert different(cases, reference, world.in_node(code, cases)) == []


def test_the_refused_programs_get_the_refusal_they_are_for(code):
    for case, status in programs.refusals():
        assert world.referenced(code, case)[0].status == status, case.name
        for machine in ("x86_64", "aarch64"):
            assert world.emulated(machine, code, case).status == status, (
                machine,
                case.name,
            )


def test_a_program_that_is_not_found_is_refused_by_every_machine(code):
    looked = programs.lookups()
    for case, status in looked:
        for machine in ("x86_64", "aarch64"):
            assert world.emulated(machine, code, case).status == status, (
                machine,
                case.handle,
            )
    if shutil.which("node"):
        found = world.in_node(code, [case for case, _ in looked])
        assert [o.status for o in found] == [status for _, status in looked]
