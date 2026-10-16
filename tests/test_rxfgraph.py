"""The `.rxf` front end as a graph target (`tools/native/rxfgraph.py`): every construct of
the source becomes the semantic graph the composer takes, and the composer's words over
that graph are held byte-identical to the compiler's own words.

The pin of the lane is `compose(graph(src)) == rxfc(src)`: the graph is not an
approximation of the program, it is the program in another form. What has no graph shape
is refused BY NAME with the construct it stands in -- the memory words (a memory move is a
call with no out slot, and the composer writes the out width of such a call as 0 where the
compiler writes 8), `proc`/`call`/`ret` (the stack of ways back is built from those memory
moves), a label the code falls into (a graph block ends in a terminator and the words have
no jump there), and a jump into the entry block of a program with a frame (the words jump
with no copies and the entry holds every value of the frame).

Each construct below pins the graph shape it maps to, and the corpus pins the words.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "native"))

import rxfc
import rxfgraph


def graph_of(source: str):
    return rxfgraph.graph_of(source, "t")


def calls_of(source: str, path: str = "t") -> list[tuple[str, list[str], str]]:
    """(callee, its operands as words, its result) of every call of the graph.

    Values are named where they stand in the graph (the arguments ``a`` and ``b``, the
    frame ``s0000`` and up); a literal is ``lit`` and its constant.
    """
    graph = rxfgraph.graph_of(source, path)
    found = []
    for block in graph.blocks:
        for op in block.ops:
            operands = []
            for kind, value in op.inputs:
                operands.append(
                    f"lit {value}" if kind == "literal" else short(graph, str(value))
                )
            result = short(graph, op.result.path) if op.result else ""
            if not op.callee:
                continue
            found.append((op.callee.rsplit(".", 1)[-1], operands, result))
    return found


def short(graph, path: str) -> str:
    return path.rsplit(".", 1)[-1]


def blocks_of(source: str) -> list[tuple[str, str, list[str], list[str]]]:
    """(name, terminator, the callees of its operations, its successors) of every block."""
    graph = rxfgraph.graph_of(source, "t")
    return [
        (
            block.path.rsplit(".", 1)[-1],
            block.terminator,
            [op.callee.rsplit(".", 1)[-1] for op in block.ops if op.callee],
            [target[0].rsplit(".", 1)[-1] for target in block.successors],
        )
        for block in graph.blocks
    ]


def words_match(source: str) -> bool:
    """The composer's words over the graph equal the compiler's own words."""
    return rxfgraph.words_of(source, "t") == rxfc.compile_source(source, "t")


# ---- the constructs, one by one ----------------------------------------------------------------


def test_a_leaf_call_is_an_operation_of_one_block():
    graph = graph_of("program p(a, b) { return a + b; }")
    assert (graph.name, graph.path, graph.graph) == (
        "p",
        "rxf.p",
        "rxf.p.p_graph",
    )
    assert [value.path for value in graph.params] == [
        "rxf.p.p_graph.a",
        "rxf.p.p_graph.b",
    ]
    assert [op.opcode for op in graph.blocks[0].ops] == ["CallCallback"]


def test_every_operator_is_its_leaf_call():
    for source, calls in [
        ("program p(a, b) { return a + b; }", [("wrapping_add_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a - b; }", [("wrapping_subtract_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a & b; }", [("bit_and_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a | b; }", [("bit_or_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a << b; }", [("shift_left_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a >> b; }", [("shift_right_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a == b; }", [("equal_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a < b; }", [("less_uint64_t", ["a", "b"], "s0016")]),
        ("program p(a, b) { return a > b; }", [("less_uint64_t", ["b", "a"], "s0016")]),
        # a truth is a word: what is not a leaf is the question of the truth again
        ("program p(a, b) { return a != b; }", [
            ("equal_uint64_t", ["a", "b"], "s0016"),
            ("equal_uint64_t", ["s0016", "lit 0"], "s0016"),
        ]),
        ("program p(a, b) { return a <= b; }", [
            ("less_uint64_t", ["b", "a"], "s0016"),
            ("equal_uint64_t", ["s0016", "lit 0"], "s0016"),
        ]),
        ("program p(a, b) { return a >= b; }", [
            ("less_uint64_t", ["a", "b"], "s0016"),
            ("equal_uint64_t", ["s0016", "lit 0"], "s0016"),
        ]),
    ]:
        assert calls_of(source) == calls, source


def test_the_forms_without_a_leaf_are_their_lowering():
    with pytest.raises(rxfc.Error, match="power of two"):
        rxfgraph.graph_of("program p(a, b) { return a * b; }", "t")  # no leaf for it
    assert calls_of("program p(a) { return -a; }") == [
        ("wrapping_subtract_uint64_t", ["lit 0", "a"], "s0008")  # minus is 0 - a
    ]
    assert calls_of("program p(a) { return ~a; }") == [
        ("wrapping_subtract_uint64_t", ["lit 18446744073709551615", "a"], "s0008")
    ]
    assert calls_of("program p(a) { return !a; }") == [
        ("equal_uint64_t", ["a", "lit 0"], "s0008")
    ]
    assert calls_of("program p(a) { return a * 4; }") == [
        ("shift_left_uint64_t", ["a", "lit 2"], "s0008")
    ]


def test_a_number_known_at_compile_time_is_a_literal_of_no_call():
    assert calls_of("program p() { return 7; }") == []
    graph = graph_of("program p() { return 7; }")
    literals = [v for v in graph.values if v.kind == "Literal"]
    assert [(v.literal, v.width) for v in literals] == [(7, 8), (0, 8)]


def test_a_literal_is_a_value_of_the_graph():
    graph = graph_of("program p(a) { return a + (2 + 3); }")
    literals = [v for v in graph.values if v.kind == "Literal"]
    assert {v.literal for v in literals} == {5, 0}  # the folded five and the end's zero
    assert graph.blocks[0].ops[0].inputs[1] == ("literal", 5)


def test_the_frame_is_the_values_the_entry_owns():
    graph = graph_of("program p(a) { var x; x = a + 1; return x; }")
    entry = graph.blocks[0]
    assert [v.path for v in graph.params] == ["rxf.p.p_graph.a"]  # the arguments
    assert [v.path for v in entry.params] == [
        "rxf.p.p_graph.entry.s0008",  # x
        "rxf.p.p_graph.entry.s0016",  # the temporary of a + 1
    ]


def test_an_assignment_writes_the_out_of_the_call_that_computes_it():
    assert calls_of("program p(a) { var x; x = a + 1; return x; }") == [
        ("wrapping_add_uint64_t", ["a", "lit 1"], "s0008")
    ]


def test_a_move_is_a_return_value_operation():
    graph = graph_of("program p(a) { var x; x = a; return x; }")
    move = graph.blocks[0].ops[0]
    assert (move.opcode, move.inputs) == ("ReturnValue", [("value", "rxf.p.p_graph.a")])
    assert move.result is not None and move.result.path == "rxf.p.p_graph.entry.s0008"


def test_a_return_and_a_refuse_are_terminators():
    for source, terminator in [
        ("program p(a) { return a; }", "Return"),
        ("program p() { refuse(7); }", "Refuse"),
    ]:
        graph = graph_of(source)
        assert graph.blocks[-2].terminator == terminator, source
        assert graph.blocks[-1].terminator == "Return"  # the end of a program answers 0


# ---- the control forms ------------------------------------------------------------------------


def test_an_if_is_a_branch_and_an_else_is_a_jump_to_the_join():
    assert blocks_of("program p(a) { if (a) { return 1; } return 2; }") == [
        ("entry", "Branch", [], ["L1", "L2"]),
        ("L1", "Return", [], []),
        ("L2", "Return", [], []),
        ("b3", "Return", [], []),  # the end of a program answers 0
    ]


def test_an_if_else_is_two_edges_and_a_join():
    assert blocks_of("program p(a) { if (a) { return 1; } else { return 2; } return 3; }") == [
        ("entry", "Branch", [], ["L1", "L2"]),
        ("L1", "Return", [], []),
        ("b2", "Goto", [], ["L3"]),  # the jump of the then to the join
        ("L2", "Return", [], []),
        ("L3", "Return", [], []),
        ("b5", "Return", [], []),
    ]


def test_an_else_if_chain_is_blocks_of_their_own():
    source = (
        "program p(a) { if (a == 1) { return 10; } else if (a == 2) "
        "{ return 20; } else { return 30; } }"
    )
    blocks = blocks_of(source)
    assert blocks[0] == ("entry", "Branch", ["equal_uint64_t"], ["L1", "L2"])
    assert [name for name, *_ in blocks][1:] == [
        "L1",  # the first then
        "b2",  # its jump to the join
        "L2",  # the head of the else if
        "L4",  # its then
        "b5",  # its jump to the join
        "L5",  # the else
        "L3",  # the join (the end of the program)
    ]


def test_a_while_is_a_branch_and_a_jump_back():
    source = "program p(i, j) { while (i) { i = j; } return i; }"
    blocks = blocks_of(source)
    assert blocks[0] == ("entry", "Branch", [], ["L2", "L3"])  # the head is the entry
    assert ("L2", "Goto", [], ["entry"]) in blocks  # the jump back
    assert ("L3", "Return", [], []) in blocks


def test_a_while_after_a_terminator_heads_a_block_of_its_own():
    source = "program p(i, j) { while (i) { i = j; } while (j) { j = i; } return j; }"
    blocks = blocks_of(source)
    assert ("L3", "Branch", [], ["L5", "L6"]) in blocks  # the second head
    assert ("L5", "Goto", [], ["L3"]) in blocks  # and its jump back
    assert words_match(source)


def test_break_and_continue_are_jumps_of_the_loop():
    source = (
        "program p(i) { var n; n = 0; switch (i) { case 1: { n = 1; } "
        "default: { n = 2; } } while (n) { if (n < 3) { n = n + 1; continue; } "
        "break; } return n; }"
    )
    blocks = blocks_of(source)
    assert ("L7", "Goto", ["wrapping_add_uint64_t"], ["L2"]) in blocks  # continue
    assert ("L8", "Goto", [], ["L6"]) in blocks  # break to the end
    assert ("b7", "Goto", [], ["L2"]) in blocks  # the loop's own jump back
    assert words_match(source)


def test_a_switch_is_a_switch_tag_with_one_edge_per_tag_and_the_default():
    source = (
        "program p(x) { switch (x) { case 1: { return 10; } "
        "case 2, 3: { return 20; } default: { return 30; } } }"
    )
    blocks = blocks_of(source)
    assert blocks[0][:2] == ("entry", "SwitchTag")
    assert blocks[0][3] == ["L3", "L4", "L4", "L1"]  # two tags of the second case
    graph = graph_of(source)
    assert graph.blocks[0].case_tags == [1, 2, 3]


def test_a_macro_a_fn_and_a_const_are_expanded_first():
    source = (
        "macro bump(x, by) { x = x + by; } "
        "fn add3(a) = a + 3; "
        "const SEVEN = 7; "
        "program p(q) { var r; r = 0; bump(r, q); return add3(r) + SEVEN; }"
    )
    assert calls_of(source) == [
        ("wrapping_add_uint64_t", ["s0008", "q"], "s0008"),  # bump(r, q), expanded
        ("wrapping_add_uint64_t", ["s0008", "lit 3"], "s0016"),  # add3(r), folded in
        ("wrapping_add_uint64_t", ["s0016", "lit 7"], "s0016"),  # the const (a freed temp)
    ]
    assert words_match(source)


def test_an_include_is_the_tokens_of_the_other_file(tmp_path):
    other = tmp_path / "other.rxf"
    other.write_text("fn add3(a) = a + 3;\n")
    source = f'include "{other.name}";\nprogram p(q) {{ return add3(q); }}\n'
    assert calls_of(source, str(tmp_path / "t.rxf")) == [
        ("wrapping_add_uint64_t", ["q", "lit 3"], "s0008")
    ]  # the fn came from the other file
    here = str(tmp_path / "t.rxf")
    assert rxfgraph.words_of(source, here) == rxfc.compile_source(source, here)


# ---- the byte-identity pin --------------------------------------------------------------------


CORPUS = [
    # a program of every form that has a graph shape, held word for word
    "program p() { var x; x = 3007; return x; }",
    "program p(a) { var x; x = a + 1; return x; }",
    "program p(a, b) { return a + b; }",
    "program p(a) { return a * 4; }",
    "program p(a) { return -a; }",
    "program p(a) { return ~a; }",
    "program p(a) { return !a; }",
    "program p(a, b) { return a < b; }",
    "program p(a) { if (a) { return 1; } return 2; }",
    "program p(a) { if (a) { return 1; } else { return 2; } return 3; }",
    "program p(a) { if (a == 1) { return 10; } else if (a == 2) { return 20; } else { return 30; } }",
    "program p(a) { if (a) { refuse(9); } return 2; }",
    "program p(i, j) { while (i) { i = j; } return i; }",
    "program p(i, j) { while (i) { i = j; } while (j) { j = i; } return j; }",
    "program p(x) { switch (x) { case 1: { return 10; } case 2, 3: { return 20; } default: { return 30; } } }",
    "program p(i) { var x; x = 0; switch (i) { case 1: { x = 1; } case 2, 3: { x = 2; } default: { x = 3; } } while (i < 10) { i = i + 1; } return i; }",
    "program p(i) { var n; n = 0; switch (i) { case 1: { n = 1; } default: { n = 2; } } while (n) { if (n < 3) { n = n + 1; continue; } break; } return n; }",
    "macro bump(x, by) { x = x + by; } fn add3(a) = a + 3; const SEVEN = 7; program p(q) { var r; r = 0; bump(r, q); return add3(r) + SEVEN; }",
]


@pytest.mark.parametrize("source", CORPUS)
def test_the_composers_words_over_the_graph_are_the_compilers_words(source):
    assert words_match(source), source


SAMPLES = Path(__file__).resolve().parent / "rxfc"
TINY = Path(__file__).resolve().parents[1] / "native" / "tiny" / "tiny.rxf"


def test_the_corpus_of_the_compilers_is_refused_by_named_constructs():
    """Every source of the corpus is byte-identical or refused with the construct."""
    refused = {
        SAMPLES / "branches.rxf": "an if label",  # a join the code falls into
        SAMPLES / "procs.rxf": "call",  # the stack of ways back
        SAMPLES / "table.rxf": "store32",  # a memory move
        TINY: "load64",  # a memory move
    }
    for path, construct in refused.items():
        with pytest.raises(rxfgraph.Refused, match=construct) as caught:
            rxfgraph.words_of(path.read_text(), str(path))
        assert caught.value.construct, path  # the refusal names the construct
    # arith.rxf is the corpus source that composes byte-identically
    words = rxfgraph.words_of((SAMPLES / "arith.rxf").read_text(), str(SAMPLES / "arith.rxf"))
    assert words == rxfc.compile_file(SAMPLES / "arith.rxf")


# ---- the refusals, by name --------------------------------------------------------------------


def test_a_memory_word_is_refused_by_name():
    with pytest.raises(rxfgraph.Refused, match="load8") as caught:
        rxfgraph.graph_of("program p(a) { return load8(a); }", "t")
    assert caught.value.construct == "load8"
    with pytest.raises(rxfgraph.Refused, match="store8"):
        rxfgraph.graph_of("program p(a, b) { store8(a, b); return 0; }", "t")
    with pytest.raises(rxfgraph.Refused, match="copy"):
        rxfgraph.graph_of("program p(a, b, c) { copy(a, b, c); return 0; }", "t")


def test_a_proc_is_refused_by_name():
    with pytest.raises(rxfgraph.Refused, match="proc") as caught:
        rxfgraph.graph_of("program p() { return 0; } proc q { ret; }", "t")
    assert caught.value.construct == "proc"
    with pytest.raises(rxfgraph.Refused, match="call"):
        rxfgraph.graph_of("program p() { call q; } proc q { ret; }", "t")


def test_a_label_the_code_falls_into_is_refused_by_name():
    with pytest.raises(rxfgraph.Refused, match="an if label L3") as caught:
        rxfgraph.graph_of(
            "program p(a) { var x; x = 1; if (a) { x = 2; } else { x = 3; } return x; }",
            "t",
        )
    assert caught.value.construct == "an if label L3"
    with pytest.raises(rxfgraph.Refused, match="a while label L1"):
        rxfgraph.graph_of(
            "program p(i) { var x; x = 0; while (i) { x = i; } return x; }", "t"
        )


def test_a_jump_into_the_entry_of_a_framed_program_is_refused_by_name():
    with pytest.raises(rxfgraph.Refused, match="a while label L1") as caught:
        rxfgraph.graph_of("program p(a, b) { while (a < b) { a = a + 1; } return a; }", "t")
    assert "entry holds the frame" in caught.value.why


def test_a_program_of_no_frame_is_refused_where_the_words_disagree():
    with pytest.raises(rxfgraph.Refused, match="no frame") as caught:
        rxfgraph.words_of("program p() { return 3007; }", "t")
    assert "at least 8 bytes" in caught.value.why
