"""The compilers leave a HANDLE operand to the interpreter (docs/23 4.9).

``compile.program`` (``native/compile.rxfs``) bakes the payload address of an OBJECT operand into
the machine code at compile time; a handle in a slot cannot be baked -- it is resolved at each use,
and a cell may move between two uses. So every program that holds a HANDLE operand is answered
RXF_REFUSE_UNSUPPORTED (16, "left to the interpreter"), never 6 and never code, on x86_64, aarch64
and wasm32. The programs are the rows of ``tests/test_graph_handle.py`` (every position the
operand can stand in); the same programs are what the interpreter of the same machine runs there.
"""

from __future__ import annotations

import shutil

import graph_header
import pytest

from . import graph_world as world
from .test_graph_handle import LEAVES, PROBES

UNSUPPORTED = graph_header.numbers()["RXF_REFUSE_UNSUPPORTED"]


@pytest.fixture(scope="module")
def cases() -> list[world.Case]:
    return [p.case() for p in PROBES]


@pytest.fixture(scope="module")
def compiler_code() -> world.Code:
    # a call card compiles to a call of the run leaf: it is in the table of the machine
    return world.seed_code([*LEAVES, "compile_program", "graph_run"])


@pytest.mark.parametrize("machine", ["x86_64", "aarch64"])
def test_the_compiler_leaves_a_handle_program_to_the_interpreter(
    machine, compiler_code, cases
):
    """The compiler bakes object addresses at compile time, which a handle in a slot cannot be:
    every program above is answered RXF_REFUSE_UNSUPPORTED (16), never 6 and never code."""
    found = {
        case.name: (have.status, have.outcome)
        for probe, case in zip(PROBES, cases, strict=True)
        if probe.holds
        for have in [world.compiled(machine, compiler_code, case)]
    }
    assert found
    assert {name: status for name, (status, _) in found.items()} == dict.fromkeys(
        found, UNSUPPORTED
    )
    assert all(outcome is None for _, outcome in found.values())


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_wasm32_compiler_leaves_a_handle_program_to_the_interpreter(
    compiler_code, cases
):
    held = [case for probe, case in zip(PROBES, cases, strict=True) if probe.holds]
    found = world.compiled_in_node(compiler_code, held)
    assert [c.status for c in found] == [UNSUPPORTED] * len(held)
    assert all(c.outcome is None for c in found)
