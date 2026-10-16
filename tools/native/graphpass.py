"""The checks and passes of the semantic graph (docs/05): what makes one graph honest,
and the one rewrite this lane proves.

A graph that composes is not yet a graph that is true. docs/05 draws the line between
shape validation (a record can be decoded) and semantic validation (the graph invariants
hold): required references resolve, types agree, and an invalid change produces a typed
refusal before anything runs. ``check`` is that line for one SemanticGraph -- the graph of
``tools/native/rxfgraph.py`` or any other of the same shape:

- the entry is first: the blocks are laid out with the entry at index 0 and the rest in
  order (the layout compose writes);
- every reference resolves: the values an operation reads and writes, the values a
  terminator reads, and the values an edge copies are values of the graph, and the blocks
  a jump lands on are blocks of the graph;
- a block edge is whole: an edge passes exactly the values its target takes;
- types agree at a merge: a value an edge copies is the width and type of the parameter
  it lands in (a literal is written into any word of a parameter);
- every terminator and operation has the shape its kind names.

Each failure is a typed refusal (``BadEntry``, ``DanglingValue``, ``DanglingBlock``,
``BadEdge``, ``EdgeType``, ``BadTerminator``, ``BadOperation``) carrying where it stands.

The one pass is the simplest rewrite that changes a program: unreachable-block
elimination. The blocks no edge reaches carry no run of the program (the entry is the only
way in), so a validated successor graph (docs/05) may drop them. ``tests/
test_graph_checks.py`` proves the drop preserves what the composed words compute: the
interpreter answers the same status and word on every argument set of the corpus.
"""

from __future__ import annotations

import dataclasses

import rxemit

U32 = rxemit.U32
TERMINATORS = {"Return", "Refuse", "Goto", "Branch", "SwitchTag"}
OPCODES = {"CallCallback", "ReturnValue"}


class Problem(Exception):
    """One graph invariant does not hold, named with where it stands."""

    def __init__(self, where: str, what: str) -> None:
        self.where = where
        self.what = what
        super().__init__(f"{where}: {what}")


class BadEntry(Problem):
    """The entry is not first: the blocks are not laid out from index 0 in order."""


class DanglingValue(Problem):
    """A value the graph reads is not a value of the graph."""


class DanglingBlock(Problem):
    """A jump lands on a block that is not a block of the graph."""


class BadEdge(Problem):
    """An edge is not whole: it passes a different number of values than its target takes."""


class EdgeType(Problem):
    """A value an edge copies disagrees with the parameter it lands in."""


class BadTerminator(Problem):
    """A block does not end in the shape its terminator kind names."""


class BadOperation(Problem):
    """An operation does not carry the shape its opcode names."""


# ---- the checks --------------------------------------------------------------------------------


def _operand(
    where: str, values: dict[str, rxemit.LValue], operand: rxemit.MOperand | None
) -> list[Problem]:
    """The value an operand names, or the refusal that it names nothing."""
    if operand is None:
        return []
    kind, first = operand[0], operand[1]
    if kind == "value":
        return [] if str(first) in values else [DanglingValue(where, f"{first} is no value")]
    if kind == "literal":
        found = [
            v for v in values.values() if v.kind == "Literal" and v.literal == first
        ]
        return [] if found else [DanglingValue(where, f"the literal {first} is no value")]
    return [DanglingValue(where, f"an operand of kind {kind!r} is no value")]


def problems(graph: rxemit.LGraph) -> list[Problem]:
    """Every graph invariant of one graph that does not hold, each with where it stands."""
    out: list[Problem] = []
    values = {v.path: v for v in graph.values}
    blocks = {b.path: b for b in graph.blocks}
    where = graph.path

    if not graph.blocks:
        return [BadEntry(where, "it has no block")]
    indexes = [b.index for b in graph.blocks]
    if indexes[0] != 0 or indexes != sorted(set(indexes)):
        out.append(BadEntry(where, f"the blocks are laid out at {indexes}, not from 0 in order"))

    for block in graph.blocks:
        at = block.path
        out += _operand(at, values, block.condition)
        out += _operand(at, values, block.terminator_value)
        for parameter in block.params:
            if parameter.path not in values:
                out.append(DanglingValue(at, f"{parameter.path} is no value"))
        shape = {
            "Goto": (False, False, 1, None),
            "Branch": (True, False, 2, None),
            "SwitchTag": (True, False, None, "cases"),
            "Return": (False, True, 0, None),
            "Refuse": (False, True, 0, None),
        }.get(block.terminator)
        if shape is None:
            out.append(BadTerminator(at, f"{block.terminator} is no terminator kind"))
        else:
            wants_condition, wants_value, count, cases = shape
            got_condition, got_value = block.condition is not None, (
                block.terminator_value is not None
            )
            if got_condition != wants_condition or got_value != wants_value:
                out.append(
                    BadTerminator(
                        at, f"a {block.terminator} does not take the value and condition it has"
                    )
                )
            if cases == "cases":
                if len(block.successors) != len(block.case_tags) + 1:
                    out.append(
                        BadTerminator(
                            at,
                            f"{len(block.case_tags)} tags and {len(block.successors)} successors",
                        )
                    )
            elif len(block.successors) != count:
                out.append(
                    BadTerminator(
                        at, f"a {block.terminator} has {len(block.successors)} successors"
                    )
                )
        for index, (successor, arguments) in enumerate(block.successors):
            edge = f"{at} edge {index}"
            target = blocks.get(successor)
            if target is None:
                out.append(DanglingBlock(edge, f"{successor} is no block"))
                continue
            for argument in arguments:
                out += _operand(edge, values, argument)
            if len(arguments) != len(target.params):
                out.append(
                    BadEdge(
                        edge,
                        f"{len(arguments)} values into {len(target.params)} parameters of "
                        f"{target.path}",
                    )
                )
                continue
            for argument, parameter in zip(arguments, target.params, strict=True):
                if argument[0] == "literal":
                    continue  # a literal is written into any word of a parameter
                found = values.get(str(argument[1]))
                if found is None:
                    continue  # already refused above
                if found.width != parameter.width or found.type_path != parameter.type_path:
                    out.append(
                        EdgeType(
                            edge,
                            f"{found.path} is {found.width} bytes of {found.type_path} into "
                            f"{parameter.path}, {parameter.width} bytes of {parameter.type_path}",
                        )
                    )
        for op in block.ops:
            out += _operation_problems(values, op)
    return out


def _operation_problems(
    values: dict[str, rxemit.LValue], op: rxemit.LOp
) -> list[Problem]:
    out: list[Problem] = []
    at = op.path
    if op.opcode not in OPCODES:
        out.append(BadOperation(at, f"{op.opcode} is no opcode of the graph"))
    if op.opcode == "CallCallback" and not op.callee:
        out.append(BadOperation(at, "a call names no callee"))
    if op.opcode == "ReturnValue" and len(op.inputs) != 1:
        out.append(BadOperation(at, f"a move takes one value, not {len(op.inputs)}"))
    if len(op.input_types) != len(op.inputs):
        out.append(
            BadOperation(
                at, f"{len(op.inputs)} inputs and {len(op.input_types)} input types"
            )
        )
    for argument in op.inputs:
        out += _operand(at, values, argument)
    if op.result is not None and op.result.path not in values:
        out.append(DanglingValue(at, f"{op.result.path} is no value"))
    if op.status_value is not None and op.status_value.path not in values:
        out.append(DanglingValue(at, f"{op.status_value.path} is no value"))
    if op.status_value is not None and op.status_value.type_path != U32:
        out.append(
            BadOperation(at, f"a status is typed {op.status_value.type_path}, not {U32}")
        )
    return out


def check(graph: rxemit.LGraph) -> None:
    """The first refusal of the graph, or nothing when it is sound."""
    found = problems(graph)
    if found:
        raise found[0]


# ---- the pass ----------------------------------------------------------------------------------


def reachable(graph: rxemit.LGraph) -> set[str]:
    """The blocks the entry reaches by its edges."""
    blocks = {b.path: b for b in graph.blocks}
    seen: set[str] = set()
    stack = [graph.blocks[0].path] if graph.blocks else []
    while stack:
        path = stack.pop()
        if path in seen or path not in blocks:
            continue
        seen.add(path)
        stack += [successor for successor, _ in blocks[path].successors]
    return seen


def used_values(graph: rxemit.LGraph, blocks: list[rxemit.LBlock]) -> set[str]:
    """The paths of every value the given blocks read or write."""
    values = {v.path: v for v in graph.values}
    used = {v.path for v in graph.values if v.kind == "Parameter"}  # the signature

    def use(operand: rxemit.MOperand) -> None:
        nonlocal used
        if operand[0] == "value":
            used.add(str(operand[1]))
        else:
            used |= {
                path
                for path, value in values.items()
                if value.kind == "Literal" and value.literal == operand[1]
            }

    for block in blocks:
        for operand in (block.condition, block.terminator_value):
            if operand is not None:
                use(operand)
        used |= {p.path for p in block.params}
        for _, arguments in block.successors:
            for argument in arguments:
                use(argument)
        for op in block.ops:
            for operand in op.inputs:
                use(operand)
            if op.result is not None:
                used.add(op.result.path)
            if op.status_value is not None:
                used.add(op.status_value.path)
    return used


def eliminate_unreachable_blocks(graph: rxemit.LGraph) -> rxemit.LGraph:
    """The validated successor graph (docs/05) without the blocks no edge reaches, and
    without the values only those blocks held: what the program computes does not change
    (``tests/test_graph_checks.py`` holds the composed words to it)."""
    keep = reachable(graph)
    blocks = [b for b in graph.blocks if b.path in keep]
    used = used_values(graph, blocks)
    values = [v for v in graph.values if v.path in used]
    kept = [
        dataclasses.replace(b, params=[p for p in b.params if p.path in used])
        if b.params
        else b
        for b in blocks
    ]
    return dataclasses.replace(graph, blocks=kept, values=values)
