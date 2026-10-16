"""The graph target of the ``.rxfs`` front end: source to a SemanticGraph, not to words.

Today ``tools/native/rxfc.py`` reads a ``.rxfs`` program and writes the words of the
interpreter's program format directly. This module keeps that compiler exactly as it is
and takes a second thing out of the same parse: the *semantic graph* of the program --
blocks, operations and values as ordinary image objects (the kind ``tools/native/rxemit.py``
writes), the same kind the corpus programs of the starter are. The existing composer
(``tools/native/compose.py``) turns such a graph back into program words, so the pin of
``tests/test_rxfgraph.py`` holds: the composer's words over this graph are the compiler's
own words, byte for byte.

The parse is ``rxfc``'s, not a copy of it. ``Maker`` runs unmodified; a recording subclass
notes the frame slots it allocates (the values of the graph) and refuses by name the
constructs whose words this graph shape cannot be:

- ``load8|16|32|64``, ``store8|16|32|64``, ``put``, ``copy``, ``fill``, ``proc``, ``call``,
  ``ret``: every one of them moves memory with a leaf call that takes no out slot (memory
  is moved by ``memory_copy`` and ``memory_fill``; the stack of ways back is built from
  those). The compiler writes the out word ``(NONE, 8)`` of such a call where the composer
  writes ``(NONE, 0)``, so no graph composes those words byte for byte. One of the two
  writers is wrong about the format (a seam named in the report); until that is answered,
  the memory of a program is outside this target;
- a label the code falls into (an ``if`` or ``else`` arm that does not end in a jump of
  its own, or a loop header the code before it falls into): the words let one instruction
  run into the next, while a graph block always ends in a terminator. Nothing composes
  the fall-through -- the join would need a jump the words do not have -- so it is refused
  by name, with the construct whose label it is. (A block that ends in a jump of its own
  is fine: every ``switch`` arm ends in one, and so does every ``while`` body.)
- a jump into the entry block of a program with a frame: the entry holds the frame (its
  parameters are what the composer places), so a jump into it would have to copy every
  value of the frame, and the words jump with no copies;
- a program of no frame at all (no parameter, no ``var``, no temporary): the composer
  places a frame of at least 8 bytes where the compiler writes 0. One of the two writers
  is wrong about the format (a seam named in the report); the graph itself is fine, only
  the words over it are refused.

What remains is a real language: parameters, ``var``, assignment, every operator and
literal of the expressions (each one its leaf call), ``if``/``else`` arms that end in a
return, a refusal or a jump, ``while`` with ``break`` and ``continue`` where the loop is
entered by jumps alone, ``switch``, ``return`` and ``refuse``, with ``include``,
``const``, ``macro`` and ``fn`` expanded first. Every program of that subset becomes a
``SemanticGraph`` an editor can read, rewrite and hand to the composer.

The shape one program takes here: the frame of the program is the parameters of the entry
block (compose places them where the compiler's own slot allocator placed them), but they
are the *owned* bound values of the entry, not named parameters -- the named form is one
edge, one place (``core/meta/types.cpp``), and a terminator that reads a frame value would
name the value twice. Compose's ``block_parameters`` reads both forms ("the ones it names,
else the bound values it owns"); this target writes the second.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import compose
import graph_header
import rxemit
import rxfc

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image

N = graph_header.numbers()
LEAF = graph_header.leaves()
BY_ID = {identifier: name for name, identifier in LEAF.items()}
NONE = N["RXF_GRAPH_NONE"]
U64 = rxemit.U64
U32 = rxemit.U32
WIDE = rxemit.WIDE
KINDS = "pymergetic.rxf.kinds"
MASK = (1 << 64) - 1
REFUSALS = "pymergetic.rxf.foundation.StdlibRefusals"
OP = {name: N[f"RXF_OP_{name}"] for name in (
    "CALL", "MOVE", "GOTO", "BRANCH", "SWITCH", "RETURN", "REFUSE",
)}  # fmt: skip
TERMINATORS = {OP["GOTO"], OP["BRANCH"], OP["RETURN"], OP["REFUSE"], OP["SWITCH"]}
SHIFTS = frozenset({"shift_left_uint64_t", "shift_right_uint64_t"})

# the constructs that have no byte-identical graph shape, refused by name
MEMORY_WORDS = frozenset({
    "load8", "load16", "load32", "load64",
    "store8", "store16", "store32", "store64", "put", "copy", "fill",
})  # fmt: skip
PROC_WORDS = frozenset({"proc", "call", "ret"})

FALL_THROUGH = (
    "a graph block ends in a terminator and the words have no jump here: the code "
    "falls into the label"
)
MEMORY_WHY = (
    "a memory move is a call with no out slot: the compiler writes the out word "
    "(NONE, 8) and the composer writes (NONE, 0), so the words cannot match"
)
PROC_WHY = "the stack of ways back is built from the memory moves this target refuses"
ENTRY_WHY = (
    "the entry holds the frame, so a jump into it would have to copy every value "
    "of it: the words jump with no copies"
)


class Refused(Exception):
    """A construct of the source is outside the graph target, named with where it stands."""

    def __init__(self, construct: str, where: str, why: str) -> None:
        self.construct = construct
        self.where = where
        self.why = why
        super().__init__(f"{where}: {construct} is refused here ({why})")


# ---- the parse, recorded ----------------------------------------------------------------------


class Recording(rxfc.Maker):
    """``rxfc.Maker`` as it is, with the frame slots it allocates noted in order.

    The parse, the expansion of macros and functions, and every word the compiler writes
    are the compiler's own. What this adds is the list of slots the frame is built from
    (the values of the graph) and the refusals above.
    """

    def __init__(self, source: str, path: str = "<source>") -> None:
        super().__init__(source, path)
        self.slot_log: list[tuple[int, int]] = []
        self.label_of: dict[str, str] = {}
        self.blame = "the program"

    def where(self) -> str:
        token = self.peek() if self.tokens else None
        line = token.line if token else 0
        return f"{(token.file if token else '') or self.path}:{line}"

    def new_label(self) -> str:
        """A label of the compiler, noted with the construct that made it."""
        found = super().new_label()
        self.label_of[found] = self.blame
        return found

    def _blaming(self, blame: str, run) -> None:
        was, self.blame = self.blame, blame
        try:
            run()
        finally:
            self.blame = was

    def if_statement(self) -> None:
        self._blaming("an if", super().if_statement)

    def while_statement(self) -> None:
        self._blaming("a while", super().while_statement)

    def switch_statement(self) -> None:
        self._blaming("a switch", super().switch_statement)

    def slot(self, width: int) -> int:
        found = super().slot(width)
        self.slot_log.append((found, width))
        return found

    def statement(self) -> None:
        token = self.peek()
        word = token.text if token else ""
        if word in MEMORY_WORDS or word in PROC_WORDS:
            raise Refused(
                word, self.where(), MEMORY_WHY if word in MEMORY_WORDS else PROC_WHY
            )
        super().statement()

    def load(self, size: int, address: tuple[str, int]) -> tuple[str, int]:
        raise Refused(f"load{size * 8}", self.where(), MEMORY_WHY)

    def store(self, size: int, address: tuple[str, int], value: tuple[str, int]) -> None:
        raise Refused(f"store{size * 8}", self.where(), MEMORY_WHY)

    def call(self, leaf: str, operands: list, out: int | None) -> None:
        if leaf in ("memory_copy", "memory_fill") and out is None:
            raise Refused("a memory move", self.where(), MEMORY_WHY)
        super().call(leaf, operands, out)

    def call_statement(self) -> None:
        raise Refused("call", self.where(), PROC_WHY)

    def proc(self) -> None:
        raise Refused("proc", self.where(), PROC_WHY)


@dataclass
class Parsed:
    """One parse: the compiler's program, and the slots its frame was built from."""

    name: str
    words: list  # the instruction words (ints and rxfc.Label), without the header
    starts: list[int]
    labels: dict[str, int]
    params: list[tuple[int, int]]
    frame: int
    slots: list[tuple[int, int]]  # every slot the frame holds, in allocation order
    names: dict[str, int]  # the name each variable was given
    label_of: dict[str, str]  # the construct that made each label (for a refusal)


def parse(source: str, path: str = "<source>") -> Parsed:
    """The program the source says, with the slots and names of its frame."""
    maker = Recording(source, path)
    made = maker.parse()
    return Parsed(
        maker.program_name,
        made.words,
        made.starts,
        dict(made.labels),
        list(made.params),
        made.frame,
        list(maker.slot_log),
        dict(maker.variables),
        dict(maker.label_of),
    )


# ---- the blocks of the stream -----------------------------------------------------------------


@dataclass
class _Block:
    """A run of instructions the stream bounds: one label in, one terminator out."""

    name: str
    ops: list[list] = field(default_factory=list)
    terminator: list | None = None


def _is_terminator(instruction: list) -> bool:
    return bool(instruction) and instruction[0] in TERMINATORS


def _blocks_of(p: Parsed) -> tuple[list[_Block], dict[str, _Block]]:
    """The instruction stream as blocks. Every label a jump lands on starts a block and
    every terminator ends one: code that falls into a label has no graph shape."""
    labels_at: dict[int, list[str]] = {}
    for label, at in p.labels.items():
        labels_at.setdefault(at, []).append(label)
    blocks: list[_Block] = []
    by_label: dict[str, _Block] = {}
    current: _Block | None = None
    ended = True  # the first instruction starts a block
    for index, start in enumerate(p.starts):
        stop = p.starts[index + 1] if index + 1 < len(p.starts) else len(p.words)
        instruction = list(p.words[start:stop])
        named = labels_at.get(start, [])
        if current is None or ended or named:
            if named and not ended:
                raise Refused(
                    f"{p.label_of.get(named[0], 'a label')} label {named[0]}",
                    f"word {start} of {p.name}",
                    FALL_THROUGH,
                )
            name = min(named) if named else f"b{len(blocks)}"
            current = _Block(name)
            blocks.append(current)
            for label in named:
                by_label[label] = current
        if _is_terminator(instruction):
            current.terminator = instruction
            ended = True
        else:
            current.ops.append(instruction)
            ended = False
    if not blocks:
        raise Refused("a program", p.name, "it holds no instruction")
    if not ended:
        raise Refused(
            "a block", f"{p.name}:{blocks[-1].name}", "it does not end in a terminator"
        )
    blocks[0].name = "entry"
    by_label["__entry__"] = blocks[0]
    for label in p.labels:
        if label not in by_label:
            raise Refused(f"label {label}", p.name, "nothing was placed at it")
    return blocks, by_label


# ---- the graph -------------------------------------------------------------------------------


class GraphOf:
    """One program of the source as a SemanticGraph of ``rxemit``."""

    def __init__(self, p: Parsed, module: str = "rxf") -> None:
        self.p = p
        self.module = module
        self.name = p.name
        self.path = f"{module}.{self.name}"
        self.graph = f"{self.path}.{self.name}_graph"
        self.signature = f"{self.path}.signature"
        self.values: dict[int, rxemit.LValue] = {}
        self.literals: dict[int, rxemit.LValue] = {}
        self.slots: list[rxemit.LValue] = []
        self.params: list[rxemit.LValue] = []
        self.leaves: set[str] = set()

    @property
    def every(self) -> list[rxemit.LValue]:
        """Every value of the graph: the parameters, then the frame, then the literals."""
        return [*self.params, *self.slots, *self.literals.values()]

    # -- values

    def value(self, slot: int, width: int = 8) -> rxemit.LValue:
        found = self.values.get(slot)
        if found is None:
            raise Refused(
                "a slot", self.name, f"the words read slot {slot}, which was never made"
            )
        if found.width != width:
            raise Refused(
                "a slot", self.name, f"slot {slot} is read as {width} of {found.width} bytes"
            )
        return found

    def literal(self, constant: int) -> rxemit.LValue:
        constant &= MASK
        found = self.literals.get(constant)
        if found is None:
            found = rxemit.LValue(
                f"{self.graph}.lit{constant}", "Literal", literal=constant, width=8
            )
            self.literals[constant] = found
        return found

    def operand(self, words: list, at: int) -> rxemit.MOperand:
        kind, first, width = words[at : at + 3]
        if kind == N["RXF_OPERAND_LITERAL"]:
            self.literal(first)
            return ("literal", first & MASK)
        if kind == N["RXF_OPERAND_SLOT"]:
            self.value(first, width)
            return ("value", self.values[first].path)
        raise Refused("an operand", self.name, f"kind {kind} is not a slot or a literal")

    def make_values(self) -> None:
        """The frame: the parameters first, then every other slot the compiler allocated,
        in allocation order. A slot of the frame is a value the entry block owns -- the
        frame value the composer places where the compiler's own allocator placed it."""
        taken = set()
        for index, (slot, width) in enumerate(self.p.params):
            name = self.names_by_slot.get(slot, f"arg{index}")
            made = rxemit.LValue(
                f"{self.graph}.{name}", "Parameter", width=width, type_path=U64
            )
            self.values[slot] = made
            self.params.append(made)
            taken.add(slot)
        for at, width in self.p.slots:
            if at in taken:
                continue
            made = rxemit.LValue(
                f"{self.graph}.entry.s{at:04d}",
                "Bound",
                owner_block=f"{self.graph}.entry",
                width=width,
                type_path=U64 if width <= 8 else WIDE,
            )
            self.values[at] = made
            self.slots.append(made)
            taken.add(at)

    @property
    def names_by_slot(self) -> dict[int, str]:
        return {slot: name for name, slot in self.p.names.items()}

    # -- operations and blocks

    def operation(self, block: str, index: int, words: list) -> rxemit.LOp:
        path = f"{block}.op{index}"
        opcode = words[0]
        if opcode == OP["CALL"]:
            leaf = BY_ID.get(words[1])
            if leaf is None:
                raise Refused(
                    "a call", self.name, f"leaf {words[1]} is not a leaf of the interpreter"
                )
            count = words[2]
            out, out_width, status = words[3 : 6]
            if out == NONE or status != NONE or out_width != 8:
                raise Refused(
                    f"the call of {leaf}",
                    self.name,
                    "its out word is not one result of eight bytes",
                )
            self.leaves.add(leaf)
            inputs = [self.operand(words, 6 + 3 * i) for i in range(count)]
            second = U32 if leaf in SHIFTS else U64
            return rxemit.LOp(
                path,
                index,
                "CallCallback",
                inputs,
                [U64, second][: len(inputs)],
                self.value(out),
                f"{self.module}.{leaf}",
            )
        if opcode == OP["MOVE"]:
            target, width = words[1:3]
            source = self.operand(words, 3)
            return rxemit.LOp(
                path, index, "ReturnValue", [source], [U64], self.value(target, width)
            )
        raise Refused("an instruction", self.name, f"opcode {opcode} has no graph shape")

    def terminator_of(
        self,
        path: str,
        index: int,
        block: _Block,
        ops: list[rxemit.LOp],
        by_label: dict[str, _Block],
        params: list[rxemit.LValue],
    ) -> rxemit.LBlock:
        words = block.terminator or []
        where = f"{self.name}:{block.name}"

        def edge(at: int) -> tuple[str, list]:
            label, copies = words[at], words[at + 1]
            if copies:
                raise Refused("an edge", where, "the words copy values into the successor")
            name = label.name if isinstance(label, rxfc.Label) else f"b{label}"
            found = by_label.get(name)
            if found is None:
                raise Refused("an edge", where, f"{name} is not a block of the program")
            if found is by_label.get("__entry__") and self.slots:
                raise Refused(
                    f"{self.p.label_of.get(name, 'a jump')} label {name}",
                    where,
                    ENTRY_WHY,
                )
            return (f"{self.graph}.{found.name}", [])

        if not words:
            raise Refused("a block", where, "it does not end in a terminator")
        if words[0] == OP["RETURN"]:
            return rxemit.LBlock(path, index, params, ops, "Return", self.operand(words, 1))
        if words[0] == OP["REFUSE"]:
            return rxemit.LBlock(path, index, params, ops, "Refuse", self.operand(words, 1))
        if words[0] == OP["GOTO"]:
            return rxemit.LBlock(path, index, params, ops, "Goto", successors=[edge(1)])
        if words[0] == OP["BRANCH"]:
            condition = self.operand(words, 1)
            return rxemit.LBlock(
                path, index, params, ops, "Branch",
                condition=condition, successors=[edge(5), edge(7)],
            )  # fmt: skip
        if words[0] == OP["SWITCH"]:
            value = self.operand(words, 1)
            cases = words[4]
            tags = list(words[5 : 5 + cases])
            after = 5 + cases
            successors = [edge(after + 2 * i) for i in range(cases + 1)]
            return rxemit.LBlock(
                path, index, params, ops, "SwitchTag",
                condition=value, successors=successors, case_tags=tags,
            )  # fmt: skip
        raise Refused("a block", where, "it does not end in a terminator")

    def build(self) -> rxemit.LGraph:
        self.make_values()
        blocks, by_label = _blocks_of(self.p)
        made: list[rxemit.LBlock] = []
        for index, block in enumerate(blocks):
            path = f"{self.graph}.{block.name}"
            ops = [self.operation(path, at, words) for at, words in enumerate(block.ops)]
            made.append(
                self.terminator_of(
                    path, index, block, ops, by_label, self.slots if index == 0 else []
                )
            )
        return rxemit.LGraph(
            self.name, self.module, self.path, self.graph, self.signature,
            self.params, made, [], self.every, {},
        )  # fmt: skip


# ---- the delta (the shape of this target: the frame is the bound values the entry owns)


def emit_delta(graph: rxemit.LGraph, module: str | None = None) -> str:
    """One graph as one delta of the image: the graph, and one small Function per leaf it
    calls (the callee of a call is a reference and must resolve)."""
    module = module or graph.module
    leaves = sorted(
        {
            op.callee.rsplit(".", 1)[-1]
            for block in graph.blocks
            for op in block.ops
            if op.callee
        }
    )
    out = [
        '<delta xmlns:rxf="urn:pymergetic:rxf">',
        f'  <Module name="{module}" category="ModuleCategory.Library">',
        "    "
        + "<Function"
        + rxemit._attrs(
            [
                ("name", graph.name),
                ("implementation", f"{KINDS}.FunctionImplementation.Semantic"),
                ("layer", f"{KINDS}.FunctionLayer.Library"),
                ("semantic_version", "1"),
                ("signature", graph.signature),
            ]
        )
        + ">",
    ]
    out += ["      " + line for line in _graph(graph)]
    out.append("      " + _signature(graph))
    out.append("    </Function>")
    for leaf in leaves:
        out.append(
            f'    <Function name="{leaf}"'
            f' implementation="{KINDS}.FunctionImplementation.Native"'
            f' layer="{KINDS}.FunctionLayer.Core" semantic_version="1"'
            f' signature="{module}.{leaf}.signature">'
        )
        out.append(
            f'      <Signature name="signature" return_type="{U64}"'
            ' parameter_count="0" result_count="0"'
            f' refusal_set="{REFUSALS}" />'
        )
        out.append("    </Function>")
    out += ["  </Module>", "</delta>"]
    return "\n".join(out)


def _graph(graph: rxemit.LGraph) -> list[str]:
    entry = graph.blocks[0].path
    out = [
        "<SemanticGraph"
        + rxemit._attrs(
            [
                ("name", f"{graph.name}_graph"),
                ("function", graph.path),
                ("entry_block", entry),
                ("refusal_set", REFUSALS),
                ("output_type", U64),
                ("block_count", len(graph.blocks)),
                ("parameter_count", len(graph.params)),
                ("result_count", len(graph.results) or None),
            ]
        )
        + ">"
    ]
    for index, parameter in enumerate(graph.params):
        out.append("    " + rxemit._ref("ParameterValue", parameter.path, index))
    for index, result in enumerate(graph.results):
        out.append(
            "    " + rxemit._ref("GraphResultValue", rxemit._operand_path(graph, result), index)
        )
    for block in graph.blocks:
        out += ["    " + line for line in _block(graph, block)]
    for index, parameter in enumerate(graph.params):
        out.append(
            "    "
            + rxemit._value(
                graph, parameter, source=f"{graph.signature}.argument[{index}]"
            )
        )
    for value in graph.values:
        if value.kind in ("Literal", "Object"):
            out.append("    " + rxemit._value(graph, value))
    out.append("</SemanticGraph>")
    return out


def _block(graph: rxemit.LGraph, block: rxemit.LBlock) -> list[str]:
    name = block.path.rsplit(".", 1)[-1]
    out = [
        "<SemanticBlock"
        + rxemit._attrs(
            [
                ("name", name),
                ("index", block.index),
                ("terminator", f"{KINDS}.Terminator.{block.terminator}"),
                ("operation_count", len(block.ops) or None),
                ("parameter_type_count", len(block.params) or None),
                ("successor_count", len(block.successors) or None),
                ("successor_map_count", len(block.successors) or None),
                ("case_count", len(block.case_tags) or None),
                (
                    "condition_value",
                    rxemit._operand_path(graph, block.condition) if block.condition else None,
                ),
                (
                    "terminator_value",
                    rxemit._operand_path(graph, block.terminator_value)
                    if block.terminator_value
                    else None,
                ),
                (
                    "rxf:data",
                    rxemit._data(
                        [0, *block.case_tags, *[len(args) for _, args in block.successors]]
                    ),
                ),
            ]
        )
        + ">"
    ]
    for edge, (successor, arguments) in enumerate(block.successors):
        out.append("    " + rxemit._ref("Successor", successor, edge))
        for position, argument in enumerate(arguments):
            out.append(
                "    "
                + rxemit._ref(
                    "SuccessorArgument",
                    rxemit._operand_path(graph, argument),
                    edge * 64 + position,
                )
            )
    for op in block.ops:
        out += ["    " + line for line in _operation(graph, op)]
    for parameter in block.params:
        out.append("    " + rxemit._value(graph, parameter))
    out.append("</SemanticBlock>")
    return out


def _operation(graph: rxemit.LGraph, op: rxemit.LOp) -> list[str]:
    if op.status_value is not None and op.status_value.type_path != U32:
        raise Refused(
            "a status value", op.path, f"it is typed {op.status_value.type_path}, not {U32}"
        )
    out = [
        "<SemanticOperation"
        + rxemit._attrs(
            [
                ("name", op.path.rsplit(".", 1)[-1]),
                ("opcode", f"{KINDS}.Opcode.{op.opcode}"),
                ("index", op.index),
                ("input_type_count", len(op.inputs)),
                ("result_type_count", "1" if op.result else None),
                ("input_value_count", len(op.inputs)),
                ("result_value_count", "1" if op.result else None),
                ("refusal_set", REFUSALS),
                ("callee", op.callee or None),
                (
                    "status_value",
                    op.status_value.path if op.status_value is not None else None,
                ),
            ]
        )
        + ">"
    ]
    for index, type_path in enumerate(op.input_types):
        out.append("    " + rxemit._ref("InputType", type_path, index, kind="RefKind.Type"))
    if op.result is not None:
        out.append(f'    {rxemit._ref("ResultType", op.result.type_path, kind="RefKind.Type")}')
    for index, argument in enumerate(op.inputs):
        out.append(
            "    " + rxemit._ref("InputValue", rxemit._operand_path(graph, argument), index)
        )
    if op.result is not None:
        out.append("    " + rxemit._ref("ResultValue", op.result.path))
    out.append("</SemanticOperation>")
    return out


def _signature(graph: rxemit.LGraph) -> str:
    out = [
        "<Signature"
        + rxemit._attrs(
            [
                ("name", "signature"),
                ("return_type", U64),
                ("parameter_count", len(graph.params)),
                ("result_count", len(graph.results) or None),
                ("refusal_set", REFUSALS),
            ]
        )
        + ">"
    ]
    for index, parameter in enumerate(graph.params):
        out.append(
            "    <Parameter"
            + rxemit._attrs(
                [("name", f"argument[{index}]"), ("value_type", U64), ("index", index)]
            )
            + " />"
        )
    out.append("</Signature>")
    return "\n      ".join(out)


# ---- the entry points --------------------------------------------------------------------------


def graph_of(source: str, path: str = "<source>", module: str = "rxf") -> rxemit.LGraph:
    """The SemanticGraph of the program the source says."""
    return GraphOf(parse(source, path), module).build()


def delta_of(source: str, path: str = "<source>", module: str = "rxf") -> str:
    """The graph of the source as one delta, ready for ``Image.apply``."""
    return emit_delta(graph_of(source, path, module), module)


def image_of(
    source: str, path: str = "<source>", module: str = "rxf", image: Image | None = None
) -> tuple[Image, rxemit.LGraph]:
    """The graph applied to an image (the starter when none is given): the ordinary image
    data the composer takes."""
    graph = graph_of(source, path, module)
    if image is None:
        image = tree.load(starter())
    image.apply(emit_delta(graph, module))
    return image, graph


def words_for(graph: rxemit.LGraph, module: str = "rxf", image: Image | None = None) -> bytes:
    """The composer's words over one graph: what ``compose`` makes of it."""
    frame = [value for value in graph.values if value.kind != "Literal"]
    if not frame:
        raise Refused(
            "a program of no frame",
            graph.name,
            "the composer places a frame of at least 8 bytes where the compiler "
            "writes 0 for a program with no value",
        )
    if image is None:
        image = tree.load(starter())
    image.apply(emit_delta(graph, module))
    problems = image.check()
    if problems:
        raise Refused("the graph", graph.name, f"the image refuses it: {problems[0]}")
    return compose.programs(image)[f"{module}.{graph.name}"]


def words_of(
    source: str, path: str = "<source>", module: str = "rxf", image: Image | None = None
) -> bytes:
    """The composer's words over the graph of the source: the pin against
    ``rxfc.compile_source``."""
    return words_for(graph_of(source, path, module), module, image)


def compose_source(source: str, path: str = "<source>", module: str = "rxf") -> bytes:
    """The words over the graph: the pin against ``rxfc.compile_source``."""
    return words_of(source, path, module)


def starter() -> Path:
    """The starter image of the tree."""
    return Path(__file__).resolve().parents[2] / "starter"
