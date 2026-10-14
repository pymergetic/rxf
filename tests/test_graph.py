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
from . import rxf_sim
from .snippets import ARITHMETIC, STARTER, arithmetic, boundary_arguments

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


def words(run, width: int, count: int) -> list[int]:
    """The words the run returned, at the front of its out buffer."""
    return [
        int.from_bytes(run.out[at : at + width], "little")
        for at in range(0, width * count, width)
    ]


def test_the_arithmetic_programs_compute_what_python_computes(cases, reference):
    """The multiply, divide and remainder leaves of native/graph.h in a program against the
    arithmetic Python computes: the signed division truncates toward zero."""
    runs = {c.name: r for c, r in zip(cases, reference, strict=True)}
    for name, width, want in (
        ("products 7 6", 8, [42, 42]),
        ("products 3 5", 8, [15, 15]),
        ("products high 1", 8, [(1 << 63) + 1, (1 << 63) + 1]),
        ("quotients 100 7", 8, [14, 2, 14, 2]),
        ("quotients -7 2", 8, [(1 << 63) - 4, 1, programs.MASK64 - 2, programs.MASK64]),
        ("quotients min 1", 8, [1 << 63, 0, 1 << 63, 0]),
        ("word quotients 100 7", 4, [14, 2, 2]),
        ("word quotients -7 2", 4, [0xFFFFFFFD, 0xFFFFFFFF, 1]),
    ):
        assert runs[name].status == 0, name
        assert words(runs[name], width, len(want)) == want, name


def test_the_with_overflow_composition_reaches_the_flags_python_computes(cases, reference):
    """llvm.uadd/usub.with.overflow as a wrapping operation and its carry or borrow, and
    llvm.sadd/ssub.with.overflow as the sign compare of the operands and the result: the four
    flags the program returns are the four Python computes (native/graph.h, the contract the
    importer lowers to)."""
    runs = {c.name: r for c, r in zip(cases, reference, strict=True)}
    for name, a, b in (
        ("overflow flags 5 3", 5, 3),
        ("overflow flags wrap add", programs.MASK64, 1),
        ("overflow flags sadd", (1 << 63) - 1, 1),
        ("overflow flags smul", 1 << 63, programs.MASK64),
        ("overflow flags ssub", 1 << 63, 1),
    ):
        assert runs[name].status == 0, name
        a_sign, b_sign = (a >> 63) & 1, (b >> 63) & 1
        sum_sign, diff_sign = ((a + b) >> 63) & 1, ((a - b) >> 63) & 1
        want = [
            int((a + b) % (1 << 64) < a),
            int(a < b),
            int(a_sign == b_sign and sum_sign != a_sign),
            int(a_sign != b_sign and diff_sign != a_sign),
        ]
        assert words(runs[name], 8, 4) == want, name


def test_the_sim_of_an_arithmetic_leaf_computes_what_its_snippet_computes():
    """The arithmetic leaves tests/rxf_sim.py writes in Python against the contract of their C
    bodies (tests/snippets.py), which tests/test_native_emulated.py holds to real C."""
    different = []
    for leaf in ARITHMETIC:
        run = rxf_sim.LEAF_RUN[graph_header.leaves()[leaf]]
        for a, b in boundary_arguments(leaf):
            have = run([a & programs.MASK64, b & programs.MASK64], None)
            want = arithmetic(leaf, a, b)
            if have != want:
                different.append((leaf, (a, b), want, have))
    assert different[:5] == []


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
