"""The checks and the pass of the semantic graph (`tools/native/graphpass.py`, docs/05).

A graph that composes is not yet a graph that is true. ``check`` holds one graph to the
invariants docs/05 names -- the entry is first, every reference resolves, a block edge is
whole, types agree at a merge -- and every failure is a typed refusal carrying where it
stands. One broken graph per failure kind is pinned below.

The one pass is unreachable-block elimination: the blocks no edge reaches carry no run of
the program. What it must not change is what the program computes, so the pin is the
interpreter: the composed words before and after the pass answer the same status and the
same word on every argument set of the corpus.
"""

from __future__ import annotations

import dataclasses
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "native"))

import graphpass
import rxemit
import rxfgraph

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rxf_sim

MASK = (1 << 64) - 1


def graph_of(source: str):
    graph = rxfgraph.graph_of(source, "t")
    assert graphpass.problems(graph) == []  # what the front end makes is honest
    return graph


def with_blocks(graph: rxemit.LGraph, **by_name: rxemit.LBlock) -> rxemit.LGraph:
    """The graph with some block replaced (named by the last word of its path)."""
    blocks = [
        by_name.get(block.path.rsplit(".", 1)[-1], block) for block in graph.blocks
    ]
    return dataclasses.replace(graph, blocks=blocks)


DIAMOND = "program p(a) { var x; x = a + 1; if (x) { return 1; } else { return 2; } return 3; }"


# ---- the checks: one broken graph per failure kind ---------------------------------------------


def test_a_sound_graph_has_no_problem():
    for source in (
        DIAMOND,
        "program p(a) { var x; x = a + 1; return x; }",
        "program p(x) { switch (x) { case 1: { return 10; } default: { return 30; } } }",
        "program p(i, j) { while (i) { i = j; } return i; }",
    ):
        assert graphpass.problems(graph_of(source)) == [], source


def test_the_entry_is_first():
    graph = graph_of(DIAMOND)
    broken = dataclasses.replace(
        graph,
        blocks=[
            dataclasses.replace(graph.blocks[0], index=1),
            dataclasses.replace(graph.blocks[1], index=0),
            *graph.blocks[2:],
        ],
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.BadEntry)
    assert problem.where == graph.path


def test_every_value_a_graph_reads_is_a_value_of_it():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry,
                ops=[
                    dataclasses.replace(
                        entry.ops[0],
                        inputs=[("value", f"{graph.graph}.no_such_value")],
                        input_types=[graph.params[0].type_path],
                    )
                ],
            )
        },
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.DanglingValue)
    assert "no_such_value" in problem.what
    assert problem.where.endswith(".op0")


def test_the_literal_a_graph_reads_is_a_value_of_it():
    graph = graph_of("program p() { var x; x = 7; return x; }")
    broken = dataclasses.replace(
        graph, values=[v for v in graph.values if v.kind != "Literal"]
    )
    found = graphpass.problems(broken)
    assert any(isinstance(p, graphpass.DanglingValue) and "7" in p.what for p in found), found


def test_every_jump_lands_on_a_block_of_the_graph():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry,
                successors=[
                    (f"{graph.graph}.no_such_block", []),
                    (entry.successors[1][0], []),
                ],
            )
        },
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.DanglingBlock)
    assert "no_such_block" in problem.what


def test_an_edge_is_whole():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry,
                successors=[
                    (entry.successors[0][0], [("value", graph.params[0].path)]),
                    (entry.successors[1][0], []),
                ],
            )
        },
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.BadEdge)
    assert "1 values into 0 parameters" in problem.what


def test_the_values_of_an_edge_agree_with_the_parameters_they_land_in():
    graph = graph_of(DIAMOND)
    entry, join = graph.blocks[0], graph.blocks[3]
    wide = dataclasses.replace(graph.params[0], path=f"{join.path}.wide", width=16)
    broken = with_blocks(
        graph,
        **{
            join.path.rsplit(".", 1)[-1]: dataclasses.replace(join, params=[wide]),
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry,
                successors=[
                    (entry.successors[0][0], []),
                    (entry.successors[1][0], [("value", graph.params[0].path)]),
                ],
            ),
        },
    )
    problems = [p for p in graphpass.problems(broken) if isinstance(p, graphpass.EdgeType)]
    assert problems, graphpass.problems(broken)
    assert "16 bytes" in problems[0].what


def test_a_block_ends_in_the_shape_its_terminator_names():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{entry.path.rsplit(".", 1)[-1]: dataclasses.replace(entry, terminator="Goto")}
    )
    found = [p for p in graphpass.problems(broken) if isinstance(p, graphpass.BadTerminator)]
    assert found, graphpass.problems(broken)
    assert found[0].where.endswith(".entry")


def test_a_switch_tag_carries_one_tag_per_case():
    graph = graph_of(
        "program p(x) { switch (x) { case 1: { return 10; } default: { return 30; } } }"
    )
    broken = with_blocks(graph, entry=dataclasses.replace(graph.blocks[0], case_tags=[]))
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.BadTerminator)
    assert "tags" in problem.what and "successors" in problem.what


def test_an_operation_has_the_shape_its_opcode_names():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry, ops=[dataclasses.replace(entry.ops[0], opcode="Nonsense")]
            )
        },
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.BadOperation)
    assert "Nonsense" in problem.what


def test_a_call_names_its_callee():
    graph = graph_of("program p(a, b) { return a + b; }")
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry, ops=[dataclasses.replace(entry.ops[0], callee=None)]
            )
        },
    )
    [problem] = graphpass.problems(broken)
    assert isinstance(problem, graphpass.BadOperation)
    assert "callee" in problem.what


def test_check_raises_the_first_refusal_of_the_graph():
    graph = graph_of(DIAMOND)
    entry = graph.blocks[0]
    broken = with_blocks(
        graph,
        **{
            entry.path.rsplit(".", 1)[-1]: dataclasses.replace(
                entry,
                ops=[
                    dataclasses.replace(
                        entry.ops[0],
                        inputs=[("value", f"{graph.graph}.gone")],
                        input_types=[graph.params[0].type_path],
                    )
                ],
            )
        },
    )
    with pytest.raises(graphpass.DanglingValue):
        graphpass.check(broken)


# ---- the pass ---------------------------------------------------------------------------------


def test_the_pass_drops_the_blocks_no_edge_reaches():
    graph = graph_of("program p(a) { return a; }")  # the end answers 0 and nothing reaches it
    assert len(graph.blocks) == 2
    pruned = graphpass.eliminate_unreachable_blocks(graph)
    assert [block.path for block in pruned.blocks] == [graph.blocks[0].path]
    twice = graphpass.eliminate_unreachable_blocks(pruned)
    assert [block.path for block in twice.blocks] == [block.path for block in pruned.blocks]
    assert graphpass.problems(pruned) == []
    assert not [v for v in pruned.values if v.kind == "Literal" and v.literal == 0]


def test_the_pass_keeps_the_blocks_an_edge_reaches():
    graph = graph_of(DIAMOND)
    pruned = graphpass.eliminate_unreachable_blocks(graph)
    kept = [block.path.rsplit(".", 1)[-1] for block in pruned.blocks]
    # the jump of the then, the join and the end are all after a return: none is reached
    assert kept == ["entry", "L1", "L2"]


RUNS = [
    ("program p(a) { return a; }", (0,)),
    ("program p(a) { return a; }", (7,)),
    ("program p(a) { return a; }", (MASK,)),
    ("program p(a, b) { return a + b; }", (1, 2)),
    ("program p(a, b) { return a + b; }", (MASK, MASK)),
    ("program p(a) { if (a) { return 1; } else { return 2; } return 3; }", (0,)),
    ("program p(a) { if (a) { return 1; } else { return 2; } return 3; }", (5,)),
    ("program p(a) { if (a) { refuse(9); } return 2; }", (0,)),
    ("program p(a) { if (a) { refuse(9); } return 2; }", (1,)),
    ("program p(i, j) { while (i) { i = j; } return i; }", (3, 0)),
    ("program p(i, j) { while (i) { i = j; } return i; }", (0, 9)),
    ("program p(x) { switch (x) { case 1: { return 10; } case 2, 3: { return 20; } default: { return 30; } } }", (1,)),
    ("program p(x) { switch (x) { case 1: { return 10; } case 2, 3: { return 20; } default: { return 30; } } }", (3,)),
    ("program p(x) { switch (x) { case 1: { return 10; } case 2, 3: { return 20; } default: { return 30; } } }", (9,)),
    ("program p(i) { var x; x = 0; switch (i) { case 1: { x = 1; } case 2, 3: { x = 2; } default: { x = 3; } } while (i < 10) { i = i + 1; } return i; }", (0,)),
    ("program p(i) { var x; x = 0; switch (i) { case 1: { x = 1; } case 2, 3: { x = 2; } default: { x = 3; } } while (i < 10) { i = i + 1; } return i; }", (2,)),
    ("program p(i) { var n; n = 0; switch (i) { case 1: { n = 1; } default: { n = 2; } } while (n) { if (n < 3) { n = n + 1; continue; } break; } return n; }", (1,)),
    ("program p(i) { var n; n = 0; switch (i) { case 1: { n = 1; } default: { n = 2; } } while (n) { if (n < 3) { n = n + 1; continue; } break; } return n; }", (7,)),
]


def run(source: str, args: tuple[int, ...], graph=None) -> tuple[int, int]:
    words = rxfgraph.words_for(graph if graph is not None else rxfgraph.graph_of(source, "t"))
    return rxf_sim.run(words, struct.pack(f"<{len(args)}Q", *args), rxf_sim.Memory())


@pytest.mark.parametrize("source,args", RUNS)
def test_the_pass_preserves_what_the_words_compute(source, args):
    graph = rxfgraph.graph_of(source, "t")
    pruned = graphpass.eliminate_unreachable_blocks(graph)
    assert graphpass.problems(pruned) == []
    assert run(source, args, pruned) == run(source, args)
