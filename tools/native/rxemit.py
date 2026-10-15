"""The delta emitter: the one vocabulary every front-end strand writes its graphs with.

A module arrives at this emitter as the semantic model below -- graphs of blocks,
operations and values (the model ``Lowering`` of ``rximport`` fills from the textual LLVM
IR, the same model a Python->RXF compiler fills from Python) -- and leaves as one delta
document, the XML vocabulary the image applies. One emitter for every strand
(one-defining-lang): no second writer of the delta exists, so the vocabulary cannot
drift between the strands.

The names the delta is spelled in live here with the writers: the kind, role and
refusal names (``KINDS``, ``SEMANTIC``, ``REFUSAL_SET``) and the type names (``U64``,
``U32``, ``WIDE``) are the vocabulary, and a strand that writes the delta writes them
verbatim. ``rximport`` re-exports every name of this module, so the importer's old home
still answers each one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Literal

# ---- the vocabulary the delta is spelled in ----------------------------------------------------

KINDS = "pymergetic.rxf.kinds"
SEMANTIC = "pymergetic.rxf.roles.semantic"
REFUSAL_SET = "pymergetic.rxf.foundation.StdlibRefusals"
U64 = "uint64_t"
U32 = "uint32_t"
# the wide cell the delta spells: 16 bytes (an addressable local, a struct cell)
WIDE = "pymergetic.rxf.stdlib.types.FixedArrayU8Of16"


class ParseError(Exception):
    """Input that is not of the subset this toolchain takes: IR text a parser of
    ``rximport`` cannot read, or a model value the emitter cannot write."""


# ---- the semantic model the emitter writes -----------------------------------------------------


@dataclass
class Global:
    """A module-level global: a named data object of the import unit.

    The delta emits one named cell per global (the C name in the names block, the initial
    payload as its data), and every graph that reads or writes it carries one value of
    kind Object with this global's ``object_id``: the handle of the arena cell the run
    resolves to the cell's payload address. Loads and stores of the global lower to flat
    loads and stores at that address (the memory leaves), so the cell is exactly the C
    static storage the differential's native binary keeps."""

    name: str  # the C name (the cell is ``module.name``)
    type: str  # the LLVM type of the global
    init: bytes | None  # the initial payload; None for a declaration without bytes
    constant: bool = False  # LLVM ``constant`` (a data root: a message, a table)

    @property
    def cell(self) -> str:
        """The cell form of the delta: a scalar is its typed cell, bytes are a wide cell."""
        if len(self.init or b"") <= 8:
            return {1: "uint8_t", 2: "uint16_t", 4: "uint32_t", 8: "uint64_t"}.get(
                len(self.init or b""), "FixedArray"
            )
        return "FixedArray"


@dataclass
class LValue:
    path: str
    kind: str  # Parameter | Bound | OperationResult | Literal | Object
    owner_block: str | None = None
    owner_op: str | None = None
    literal: int | None = None
    width: int = 8  # bytes in the frame: 16 for a wide slot (an address-taken local)
    type_path: str = U64
    object_id: int | None = None  # an Object value: the id of the cell it resolves to
    payload: bytes | None = None  # an Object value: the cell's initial bytes (its data)


# an operand of the model: the path of a value, or a constant that a Literal object stands for
type ValueOperand = tuple[Literal["value"], str]
type MOperand = ValueOperand | tuple[Literal["literal"], int]


@dataclass
class LOp:
    path: str
    index: int
    opcode: str  # CallCallback | ReturnValue | ...
    inputs: list[MOperand]
    input_types: list[str]
    result: LValue | None
    callee: str | None = None  # the path of the leaf Function, for a CallCallback


@dataclass
class LBlock:
    path: str
    index: int
    params: list[LValue] = field(default_factory=list)
    ops: list[LOp] = field(default_factory=list)
    terminator: str = "Goto"
    terminator_value: MOperand | None = None
    condition: MOperand | None = None
    successors: list[tuple[str, list[MOperand]]] = field(default_factory=list)  # (block, arguments)
    case_tags: list[int] = field(default_factory=list)


@dataclass
class LGraph:
    name: str
    module: str
    path: str  # the Function
    graph: str  # the SemanticGraph
    signature: str
    params: list[LValue]
    blocks: list[LBlock]
    results: list[MOperand]  # Return terminators only: what the function answers
    values: list[LValue]  # every value, the Literal objects included
    locals: dict[str, LValue]  # the wide slots an alloca took (name -> value)
    globals: dict[str, "Global"] = field(default_factory=dict)  # the module's data objects


# ---- the delta the emitter writes ------------------------------------------------------------


def _ref(role: str, target: str, index: int | None = None, kind: str = "RefKind.Data") -> str:
    at = f' index="{index}"' if index else ""
    return f'<rxf:ref role="{SEMANTIC}.{role}"{at} kind="{kind}" target="{target}" />'


def _attrs(pairs: list[tuple[str, object]]) -> str:
    return "".join(f' {name}="{value}"' for name, value in pairs if value is not None)


def _data(words: list[int]) -> str:
    return struct.pack(f"<{len(words)}Q", *words).hex()


def _cells(graphs: list[LGraph]) -> list[str]:
    """The named data objects of the import unit: one cell per global the graphs use. The
    cell is the C static storage the global is (a scalar its typed cell, a wide struct one
    cell whose data is the struct bytes), and every graph that touches it carries one
    Object value that names the cell by ``object_id`` and holds the same bytes. An out
    cell (the pointer/out position of docs/23 4.1) is named after the body and parameter
    whose handle it is, and is one cell across the graphs that carry it (one object id)."""
    out: list[str] = []
    seen: set[int] = set()
    taken: set[str] = set()
    for graph in graphs:
        for value in graph.values:
            if value.kind != "Object" or value.object_id is None:
                continue
            if value.object_id in seen:
                continue
            seen.add(value.object_id)
            suffix = value.path.rsplit(".", 1)[-1]
            if suffix.startswith("global_"):
                name = suffix[len("global_") :]
                cell = graph.globals.get(f"@{name}")
                form = cell.cell if cell is not None else "FixedArray"
            else:  # an out cell: named for the body and the parameter it is the handle of
                name = suffix
                form = "FixedArray"
            if name in taken:
                name = f"{name}_{len(taken)}"  # two distinct cells, one name: numbered
            taken.add(name)
            payload = (value.payload or b"").hex()
            out.append(f"<{form}" + _attrs([("name", name), ("rxf:data", payload)]) + " />")
    return out


def emit_delta(graphs: list[LGraph], module: str = "imported") -> str:
    """The graphs as one delta: the cells of the module's globals first, then a Module of
    Functions, each with its Signature and graph."""
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<delta xmlns:rxf="urn:pymergetic:rxf">',
        f'  <Module name="{module}" category="ModuleCategory.Library">',
    ]
    for cell in _cells(graphs):
        out.append("    " + cell)
    for graph in graphs:
        out += ["    " + line for line in _function(graph)]
    out += ["  </Module>", "</delta>"]
    return "\n".join(out)


def _operand_path(graph: LGraph, operand: MOperand) -> str:
    if operand[0] == "value":
        return str(operand[1])
    for value in graph.values:
        if value.kind == "Literal" and value.literal == operand[1]:
            return value.path
    raise AssertionError(f"{graph.name}: the literal {operand[1]} was never made")


def _function(graph: LGraph) -> list[str]:
    name = graph.name
    entry = graph.blocks[0].path
    out = [
        "<Function"
        + _attrs(
            [
                ("name", name),
                ("implementation", f"{KINDS}.FunctionImplementation.Semantic"),
                ("layer", f"{KINDS}.FunctionLayer.Library"),
                ("semantic_version", "1"),
                ("signature", graph.signature),
            ]
        )
        + ">"
    ]
    out.append(
        "  <SemanticGraph"
        + _attrs(
            [
                ("name", f"{name}_graph"),
                ("function", graph.path),
                ("entry_block", entry),
                ("refusal_set", REFUSAL_SET),
                ("output_type", U64),
                ("block_count", len(graph.blocks)),
                ("parameter_count", len(graph.params)),
                ("result_count", len(graph.results)),
            ]
        )
        + ">"
    )
    for index, parameter in enumerate(graph.params):
        out.append("    " + _ref("ParameterValue", parameter.path, index))
    for index, result in enumerate(graph.results):
        out.append("    " + _ref("GraphResultValue", _operand_path(graph, result), index))
    for block in graph.blocks:
        out += ["    " + line for line in _block(graph, block)]
    for index, parameter in enumerate(graph.params):
        out.append("    " + _value(graph, parameter, source=f"{graph.signature}.argument[{index}]"))
    for value in graph.values:
        if value.kind in ("Literal", "Object"):
            out.append("    " + _value(graph, value))
    out.append("  </SemanticGraph>")
    out.append(
        "  <Signature"
        + _attrs(
            [
                ("name", "signature"),
                ("return_type", U64),
                ("parameter_count", len(graph.params)),
                ("result_count", len(graph.results)),
                ("refusal_set", REFUSAL_SET),
            ]
        )
        + ">"
    )
    for index, parameter in enumerate(graph.params):
        out.append(
            "    <Parameter"
            + _attrs(
                [("name", f"argument[{index}]"), ("value_type", U64), ("index", index)]
            )
            + " />"
        )
    for index, result in enumerate(graph.results):
        width = next(
            (
                value.width
                for value in graph.values
                if result[0] == "value" and value.path == result[1]
            ),
            8,
        )
        value_type = WIDE if width > 8 else U64
        out.append(f'    <Result name="value{index}" value_type="{value_type}" />')
    out.append("  </Signature>")
    out.append("</Function>")
    return out


def _block(graph: LGraph, block: LBlock) -> list[str]:
    name = block.path.rsplit(".", 1)[-1]
    out = [
        "<SemanticBlock"
        + _attrs(
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
                    _operand_path(graph, block.condition) if block.condition else None,
                ),
                (
                    "terminator_value",
                    _operand_path(graph, block.terminator_value)
                    if block.terminator_value
                    else None,
                ),
                ("rxf:data", _data([0, *block.case_tags, *[len(args) for _, args in block.successors]])),
            ]
        )
        + ">"
    ]
    for edge, (successor, arguments) in enumerate(block.successors):
        out.append("    " + _ref("Successor", successor, edge))
        for position, argument in enumerate(arguments):
            out.append(
                "    "
                + _ref(
                    "SuccessorArgument",
                    _operand_path(graph, argument),
                    edge * 64 + position,
                )
            )
    for index, parameter in enumerate(block.params):
        out.append("    " + _ref("BlockParameterValue", parameter.path, index))
    for op in block.ops:
        out += ["    " + line for line in _operation(graph, op)]
    for parameter in block.params:
        out.append("    " + _value(graph, parameter))
    out.append("</SemanticBlock>")
    return out


def _operation(graph: LGraph, op: LOp) -> list[str]:
    name = op.path.rsplit(".", 1)[-1]
    out = [
        "<SemanticOperation"
        + _attrs(
            [
                ("name", name),
                ("opcode", f"{KINDS}.Opcode.{op.opcode}"),
                ("index", op.index),
                ("input_type_count", len(op.inputs)),
                ("result_type_count", "1" if op.result else None),
                ("input_value_count", len(op.inputs)),
                ("result_value_count", "1" if op.result else None),
                ("refusal_set", REFUSAL_SET),
                ("callee", op.callee),
            ]
        )
        + ">"
    ]
    for index, type_path in enumerate(op.input_types):
        out.append("    " + _ref("InputType", type_path, index, kind="RefKind.Type"))
    if op.result is not None:
        out.append(f'    {_ref("ResultType", op.result.type_path, kind="RefKind.Type")}')
    for index, argument in enumerate(op.inputs):
        out.append("    " + _ref("InputValue", _operand_path(graph, argument), index))
    if op.result is not None:
        out.append("    " + _ref("ResultValue", op.result.path))
        out.append("    " + _value(graph, op.result))
    out.append("</SemanticOperation>")
    return out


def _value(graph: LGraph, value: LValue, source: str | None = None) -> str:
    name = value.path.rsplit(".", 1)[-1]
    data = None
    tag = "SemanticValue"
    literal_size: object | None = None
    extra: list[tuple[str, object]] = []
    if value.kind == "Literal":
        if value.literal is None:
            raise ParseError(f"{value.path}: a Literal without its constant")
        data = struct.pack("<Q", value.literal).hex()
        literal_size = "8"
    elif value.kind == "Object":
        # a global's cell: the handle of a named data object of the import unit. The data
        # is the cell's initial payload (the run lays the cell out before the run, exactly
        # like a request struct): the value names the object and carries its bytes
        if value.object_id is None or value.payload is None:
            raise ParseError(f"{value.path}: an Object value without its cell")
        tag = "SemanticObject"
        data = value.payload.hex()
        literal_size = len(value.payload)
        extra = [("object_id", value.object_id)]
    return (
        "<"
        + tag
        + _attrs(
            [
                ("name", name),
                ("value_type", value.type_path),
                ("kind", f"{KINDS}.ValueKind.{value.kind}"),
                ("literal_size", literal_size),
                *extra,
                ("owner_function", graph.path),
                ("owner_block", value.owner_block),
                ("owner_operation", value.owner_op),
                ("source", source),
                ("rxf:data", data),
            ]
        )
        + " />"
    )
