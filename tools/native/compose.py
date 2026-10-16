"""The composer: a semantic graph of the image as the program of ``graph_run``.

A semantic Function is a ``SemanticGraph`` of blocks, operations and values. For
every target the same native code runs it: the interpreter ``graph.run`` of
``native/graph.c``, on a program that this module makes of the graph. The
program does not depend on the target; the leaves it calls (the native
Functions of the starter) are found by id in the function table of the runtime
at run time, so the target changes only the interpreter and the leaves.

``native/graph.h`` defines the program format and the ids of the leaves; this
module reads its numbers from there.

    values   slots of a frame, in the order of the graph; a literal or an
             object (a durable binding) is an operand and has no slot
    blocks   laid out with the entry first; every jump is a word index
    edges    the copies of the arguments of a block, done in parallel

The import lane's provenance (docs/22) never reaches this module: a run's origin record, its
citations and its lifted comments are Doc objects -- editorial placements that make zero
runtime refs -- and the programs a graph composes read the same words with or without them
(``tests/test_provenance.py`` pins that). The composer composes code and claims no facts about
it; if this module ever gains a face that records facts, that face takes the same tier and
floor the importer's claim does.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import graph_header

from pymergetic.rxftk.core.object import Image

NONE = (1 << 64) - 1
STATUS_TYPE = "uint32_t"
CALLS = frozenset(
    {
        "BeginPrivate", "Allocate", "Compare", "Copy", "Hash", "Lookup", "Read",
        "Search", "Utf8Validate", "CallCallback", "Write", "StoreField", "MoveSlot",
        "AppendBytes", "FormatInteger", "Validate", "Publish", "Rollback", "Cleanup",
        "Pin", "Unpin", "Borrow", "ReleaseBorrow",
    }
)  # fmt: skip
MOVES = frozenset(
    {
        "ReturnValue", "CanonicalOrder", "Utf8Boundary", "IterInit", "IterProject",
        "EnrichRefusal",
    }
)  # fmt: skip
FRAME_VALUES = frozenset({"Parameter", "OperationResult", "Bound"})


class ComposeError(Exception):
    pass


def word(text: str | None) -> str:
    """The last segment of a written path."""
    return (text or "").rsplit(".", 1)[-1]


@dataclass(frozen=True)
class Operand:
    kind: int
    a: int
    b: int

    def words(self) -> list[int]:
        return [self.kind, self.a, self.b]


@dataclass
class Value:
    handle: int
    kind: str
    width: int
    slot: int | None = None
    literal: int = 0
    object_id: int = 0


@dataclass
class Layout:
    """The numbers of the graph header, by the names the header gives them."""

    numbers: Mapping[str, int] = field(default_factory=graph_header.numbers)
    leaves: Mapping[str, int] = field(default_factory=graph_header.leaves)

    def __getitem__(self, name: str) -> int:
        return self.numbers[f"RXF_{name}"]


class Composer:
    def __init__(self, image: Image, layout: Layout | None = None) -> None:
        self.image = image
        self.layout = layout or Layout()
        self._fields: dict[int, dict[str, str]] = {}
        self._variants: dict[int, int] | None = None

    # ---- reading the image ----------------------------------------------------------------------

    def fields(self, handle: int) -> dict[str, str]:
        if handle not in self._fields:
            self._fields[handle] = dict(self.image.fields(handle))
        return self._fields[handle]

    def target(self, path: str | None) -> int:
        found = self.image.resolve(path) if path else None
        if found is None:
            raise ComposeError(f"{path!r} does not resolve")
        return found

    def children(self, handle: int, type_name: str) -> list[int]:
        return [
            c
            for c in self.image.children(handle)
            if self.image.type_name(c) == type_name
        ]

    def references(self, handle: int, role: str) -> list[tuple[int, int]]:
        """The targets of a role as (index, target), in the order they are stored."""
        found: list[tuple[int, int]] = []
        for target, via, index, _, _ in self.image.get(handle)["refs"]:
            if via is not None and word(self.image.path(via)) == role:
                found.append((index, target))
        return found

    def size_of(self, type_path: str) -> int:
        return int(self.fields(self.target(type_path)).get("size", "0"))

    def number(self, handle: int, name: str) -> int:
        return int(self.fields(handle).get(name, "0"))

    def enumerator(self, path: str) -> int:
        return self.number(self.target(path), "value")

    def variants(self) -> dict[int, int]:
        """The tag of the variant each payload field belongs to."""
        if self._variants is None:
            self._variants = {}
            for handle in range(self.image.handle_count):
                if (
                    self.image.live(handle)
                    and self.image.type_name(handle) == "TaggedVariant"
                ):
                    for _, payload in self.references(handle, "PayloadField"):
                        self._variants[payload] = self.number(handle, "tag_value")
        return self._variants

    # ---- values and operands --------------------------------------------------------------------

    def value(self, handle: int) -> Value:
        values = self.fields(handle)
        kind = word(values.get("kind"))
        width = self.size_of(values["value_type"]) if values.get("value_type") else 0
        found = Value(handle, kind, width)
        if kind == "Literal":
            data = bytes.fromhex(values.get("data", ""))[:8]
            found.literal = int.from_bytes(data, "little")
            found.width = int(values.get("literal_size", "8"))
        elif kind == "Object":
            found.object_id = self.number(handle, "object_id")
        return found

    def operand(self, found: Value) -> Operand:
        if found.kind in FRAME_VALUES:
            if found.slot is None:
                raise ComposeError(f"{self.image.path(found.handle)} has no slot")
            return Operand(self.layout["OPERAND_SLOT"], found.slot, found.width)
        if found.kind == "Literal":
            return Operand(self.layout["OPERAND_LITERAL"], found.literal, found.width)
        if found.kind == "Object":
            return Operand(self.layout["OPERAND_OBJECT"], found.object_id, found.width)
        raise ComposeError(f"a value of kind {found.kind} cannot be an operand")

    # ---- a graph --------------------------------------------------------------------------------

    def leaf_id(self, callee: str) -> int:
        parts = callee.split(".")
        for option in (f"{parts[-2]}_{parts[-1]}", parts[-1]):
            if option in self.layout.leaves:
                return self.layout.leaves[option]
        raise ComposeError(f"{callee} is not a leaf of the interpreter")

    def callee_program(self, callee: str) -> int | None:
        """The Function of a callee that is a program of its own (the call card names
        its words by object id), else None -- a callee None here is a leaf.

        A Function is a program when a SemanticGraph answers to it (the shape the
        importer writes: ``<Function implementation="Semantic"><SemanticGraph
        function="..."/></Function>``) or when it carries a body of its own. What
        resolves first is the callee's own path; a leaf keeps its leaf (the leaf
        table answers before any name search); a bare name that is no leaf is the
        one program function of that name -- two answer it is a refusal, not a guess.
        """
        found = self.image.resolve(callee) if callee else None
        if found is not None and self.is_program(found):
            return found
        try:
            self.leaf_id(callee)
            return None  # the leaf table answers this callee
        except ComposeError:
            pass
        name = callee.rsplit(".", 1)[-1]
        if not hasattr(self, "_programs_of"):
            self._programs_of = {
                self.fields(graph).get("function")
                for graph in range(self.image.handle_count)
                if self.image.live(graph)
                and self.image.type_name(graph) == "SemanticGraph"
            }
        matches = [
            handle
            for handle in range(self.image.handle_count)
            if self.image.live(handle)
            and self.image.type_name(handle) == "Function"
            and word(self.image.path(handle)) == name
            and self.is_program(handle)
        ]
        if len(matches) > 1:
            raise ComposeError(
                f"{callee}: {len(matches)} functions of the image answer that name"
            )
        return matches[0] if matches else None

    def is_program(self, handle: int) -> bool:
        """Whether this Function is a program of its own (a call card names its words)."""
        if self.image.type_name(handle) != "Function":
            return False
        values = self.fields(handle)
        if word(values.get("implementation")) != "Semantic":
            return False
        if values.get("body") is not None:
            return True
        if not hasattr(self, "_programs_of"):
            self._programs_of = {
                self.fields(graph).get("function")
                for graph in range(self.image.handle_count)
                if self.image.live(graph)
                and self.image.type_name(graph) == "SemanticGraph"
            }
        return self.image.path(handle) in self._programs_of

    def program(self, graph: int) -> bytes:
        """The program of one SemanticGraph."""
        plan = Plan(self, graph)
        return plan.encode()


class Plan:
    """One graph: its values placed in a frame, its blocks laid out, its words written."""

    def __init__(self, composer: Composer, graph: int) -> None:
        self.c = composer
        self.graph = graph
        self.name = composer.image.path(graph)
        self.values: dict[int, Value] = {}
        self.blocks = self.ordered_blocks()
        self.place_values()

    def ordered_blocks(self) -> list[int]:
        c = self.c
        entry = c.target(c.fields(self.graph).get("entry_block"))
        blocks = c.children(self.graph, "SemanticBlock")
        if entry not in blocks:
            raise ComposeError(f"{self.name}: the entry block is not a block of it")
        rest = sorted(
            (b for b in blocks if b != entry),
            key=lambda b: (c.number(b, "index"), c.image.path(b)),
        )
        return [entry, *rest]

    def value(self, handle: int) -> Value:
        if handle not in self.values:
            self.values[handle] = self.c.value(handle)
        return self.values[handle]

    def place_values(self) -> None:
        """A slot of its own for every parameter, block parameter and result."""
        c = self.c
        handles: list[int] = [t for _, t in c.references(self.graph, "ParameterValue")]
        for block in self.blocks:
            handles += self.block_parameters(block)
            for op in self.operations(block):
                handles += [t for _, t in c.references(op, "ResultValue")]
                status = c.fields(op).get("status_value")
                if status:
                    handles.append(c.target(status))
        offset = 0
        for handle in handles:
            found = self.value(handle)
            if found.kind not in FRAME_VALUES:
                raise ComposeError(
                    f"{c.image.path(handle)} is a {found.kind}, not a frame value"
                )
            if found.slot is not None:
                continue
            found.slot = offset
            offset += max(found.width, 1) + -max(found.width, 1) % 8
        self.frame = max(offset, 8)
        if self.frame > c.layout["GRAPH_CELL_BYTES"]:
            # the run cell is the budget (native/graph.h): the frames of the active call
            # chain are variable-length (base, size) links of one RXF_GRAPH_CELL_BYTES
            # cell, so a frame is composed only where a cell can hold it (the fixed
            # 1024-byte wall of RXF_GRAPH_FRAME is gone -- docs/20, the wall casualties)
            raise ComposeError(
                f"{self.name}: a frame of {self.frame} bytes is too large"
            )

    def block_parameters(self, block: int) -> list[int]:
        """The values a block takes: the ones it names, else the bound values it owns."""
        c = self.c
        wanted = c.number(block, "parameter_type_count")
        named = [t for _, t in sorted(c.references(block, "BlockParameterValue"))]
        if len(named) == wanted:
            return named
        path = c.image.path(block)
        owned = [
            v
            for holder in (self.graph, block)
            for v in c.children(holder, "SemanticValue")
            if word(c.fields(v).get("kind")) == "Bound"
            and c.fields(v).get("owner_block") == path
        ]
        if len(owned) == wanted:
            return sorted(owned, key=c.image.path)
        raise ComposeError(
            f"{c.image.path(block)}: {wanted} parameters, {len(named)} named"
        )

    def operations(self, block: int) -> list[int]:
        c = self.c
        return sorted(
            c.children(block, "SemanticOperation"),
            key=lambda op: (c.number(op, "index"), c.image.path(op)),
        )

    # ---- operands of operations -----------------------------------------------------------------

    def inputs(self, op: int) -> list[Operand]:
        c = self.c
        fields = c.fields(op)
        found: dict[int, int] = {}
        if fields.get("target_object"):
            found[0] = c.target(fields["target_object"])
        for index, target in c.references(op, "InputValue"):
            found[index] = target
        count = c.number(op, "input_value_count")
        if sorted(found) != list(range(count)):
            raise ComposeError(
                f"{c.image.path(op)}: {count} inputs, found {sorted(found)}"
            )
        return [c.operand(self.value(found[i])) for i in range(count)]

    def results(self, op: int) -> list[Value]:
        return [self.value(t) for _, t in sorted(self.c.references(op, "ResultValue"))]

    def status(self, op: int) -> int:
        status = self.c.fields(op).get("status_value")
        if not status:
            return NONE
        found = self.value(self.c.target(status))
        assert found.slot is not None
        return found.slot

    # ---- instructions ---------------------------------------------------------------------------

    def instruction(self, op: int) -> list[int]:
        c = self.c
        layout = c.layout
        fields = c.fields(op)
        opcode = word(fields.get("opcode"))
        label = c.image.path(op)
        inputs = self.inputs(op)
        results = self.results(op)

        def result() -> Value:
            if not results or results[0].slot is None:
                raise ComposeError(f"{label}: {opcode} has no result")
            return results[0]

        if opcode in CALLS:
            callee = fields.get("callee", "")
            out = (results[0].slot, results[0].width) if results else (NONE, 0)
            assert out[0] is not None
            program = c.callee_program(callee)
            if program is not None:
                # the call card: the callee is a program of its own, named by the
                # object id of its words (graph.h's RXF_GRAPH_CALL shape)
                words = [
                    layout["OP_CALL"], layout["GRAPH_CALL"], len(inputs),
                    out[0], out[1], self.status(op),
                    layout["OPERAND_OBJECT"], object_id(c.image, program), 0,
                ]  # fmt: skip
            else:
                words = [
                    layout["OP_CALL"],
                    c.leaf_id(callee),
                    len(inputs),
                    out[0],
                    out[1],
                    self.status(op),
                ]
            for operand in inputs:
                words += operand.words()
            return words
        if opcode in MOVES:
            if opcode == "EnrichRefusal":
                self.check_enrichment(op)
            return [
                layout["OP_MOVE"],
                result().slot or 0,
                result().width,
                *inputs[0].words(),
            ]
        if opcode in ("ReadTag", "ProjectPayload"):
            offset = c.number(c.target(fields["target_field"]), "offset")
            return [
                layout["OP_FIELD"], result().slot or 0, result().width,
                *inputs[0].words(), offset, result().width,
            ]  # fmt: skip
        if opcode in ("ConstructOption", "ConstructResult"):
            return self.construct(op, inputs[0], result())
        if opcode == "IterCondition":
            if len(inputs) == 1:
                return [
                    layout["OP_MOVE"],
                    result().slot or 0,
                    result().width,
                    *inputs[0].words(),
                ]
            if len(inputs) == 2:
                return [
                    layout["OP_LESS"], result().slot or 0,
                    *inputs[0].words(), *inputs[1].words(),
                ]  # fmt: skip
            raise ComposeError(
                f"{label}: an iterator condition of {len(inputs)} inputs"
            )
        if opcode == "IterAdvance":
            return [
                layout["OP_INCREMENT"],
                result().slot or 0,
                result().width,
                *inputs[0].words(),
            ]
        if opcode == "MapRefusal":
            return self.map_refusal(op, inputs[0], result())
        raise ComposeError(f"{label}: {opcode} is not an operation of the composer")

    def construct(self, op: int, source: Operand, result: Value) -> list[int]:
        c = self.c
        payload = c.target(c.fields(op)["target_field"])
        tags = c.variants()
        if payload not in tags:
            raise ComposeError(f"{c.image.path(payload)} belongs to no TaggedVariant")
        owner = c.image.get(payload)["parent"]
        tag = c.image.resolve(f"{c.image.path(owner)}.tag")
        tag_offset = c.number(tag, "offset") if tag is not None else 0
        size = c.size_of(c.fields(payload)["value_type"])
        return [
            c.layout["OP_CONSTRUCT"], result.slot or 0, result.width, tag_offset,
            tags[payload], *source.words(), c.number(payload, "offset"), size,
        ]  # fmt: skip

    def map_refusal(self, op: int, source: Operand, result: Value) -> list[int]:
        c = self.c
        fields = c.fields(op)
        mapping = c.target(fields.get("transform_authority"))
        pairs: list[int] = []
        for entry in c.children(mapping, "RefusalMappingEntry"):
            values = c.fields(entry)
            pairs += [
                c.enumerator(values["source_status"]),
                c.enumerator(values["destination_status"]),
            ]
        offset = c.number(c.target(fields["target_field"]), "offset")
        return [
            c.layout["OP_MAP"], result.slot or 0, result.width, *source.words(),
            offset, len(pairs) // 2, *pairs,
        ]  # fmt: skip

    def check_enrichment(self, op: int) -> None:
        c = self.c
        authority = c.fields(op).get("transform_authority")
        if not authority:
            return
        for entry in c.children(c.target(authority), "RefusalEnrichmentEntry"):
            values = c.fields(entry)
            if values.get("source_status") != values.get("destination_status"):
                raise ComposeError(
                    f"{c.image.path(op)}: an enrichment that changes the status needs a constructor"
                )

    # ---- blocks and edges -----------------------------------------------------------------------

    def edges(self, block: int) -> list[tuple[int, list[tuple[Operand, int, int]]]]:
        """The successors of a block as (block, copies of (source, slot, width))."""
        c = self.c
        fields = c.fields(block)
        successors = [t for _, t in c.references(block, "Successor")]
        words = struct.unpack(
            f"<{len(bytes.fromhex(fields.get('data', ''))) // 8}Q",
            bytes.fromhex(fields.get("data", "")),
        )
        cases = c.number(block, "case_count")
        counts = list(words[1 + cases :])
        if len(counts) != len(successors):
            raise ComposeError(
                f"{c.image.path(block)}: argument counts and successors differ"
            )
        explicit: dict[int, dict[int, int]] = {}
        for index, target in c.references(block, "SuccessorArgument"):
            explicit.setdefault(index // 64, {})[index % 64] = target
        condition = fields.get("condition_value")
        found: list[tuple[int, list[tuple[Operand, int, int]]]] = []
        for edge, target in enumerate(successors):
            given = explicit.get(edge, {})
            arguments = [given[i] for i in sorted(given)]
            if len(arguments) < counts[edge]:
                if not condition or len(arguments) + 1 != counts[edge]:
                    raise ComposeError(
                        f"{c.image.path(block)}: edge {edge} lacks arguments"
                    )
                arguments.append(c.target(condition))
            parameters = self.block_parameters(target)
            if len(arguments) != len(parameters):
                raise ComposeError(
                    f"{c.image.path(block)}: edge {edge} passes {len(arguments)} values "
                    f"to {len(parameters)} parameters"
                )
            copies: list[tuple[Operand, int, int]] = []
            for argument, parameter in zip(arguments, parameters, strict=True):
                destination = self.value(parameter)
                assert destination.slot is not None
                source = self.value(argument)
                if source.kind != "Literal" and source.width != destination.width:
                    raise ComposeError(
                        f"{c.image.path(block)}: edge {edge} passes {source.width} bytes "
                        f"to a parameter of {destination.width}"
                    )
                copies.append((c.operand(source), destination.slot, destination.width))
            found.append((target, copies))
        return found

    def encode_edge(
        self, edge: tuple[int, list[tuple[Operand, int, int]]], pcs: dict[int, int]
    ) -> list[int]:
        target, copies = edge
        words = [pcs[target], len(copies)]
        for source, slot, width in copies:
            words += [*source.words(), slot, width]
        return words

    def terminator(self, block: int, pcs: dict[int, int]) -> list[int]:
        c = self.c
        layout = c.layout
        fields = c.fields(block)
        kind = word(fields.get("terminator"))
        label = c.image.path(block)

        def operand(path: str | None) -> tuple[Value, Operand]:
            if not path:
                raise ComposeError(f"{label}: a {kind} without its value")
            found = self.value(c.target(path))
            return found, c.operand(found)

        if kind in ("Return", "Refuse"):
            found, source = operand(fields.get("terminator_value"))
            if kind == "Refuse":
                return [layout["OP_REFUSE"], *source.words()]
            return [layout["OP_RETURN"], *source.words(), found.width]
        edges = self.edges(block)
        if kind == "Goto":
            return [layout["OP_GOTO"], *self.encode_edge(edges[0], pcs)]
        _, source = operand(fields.get("condition_value"))
        if kind in ("Branch", "Loop"):
            status = (
                c.fields(c.target(fields["condition_value"])).get("value_type")
                == STATUS_TYPE
            )
            mode = layout["MODE_ZERO"] if status else layout["MODE_NONZERO"]
            if kind == "Loop" and status:
                raise ComposeError(f"{label}: a loop condition is not a bool")
            return [
                layout["OP_BRANCH"], *source.words(), mode,
                *self.encode_edge(edges[0], pcs), *self.encode_edge(edges[1], pcs),
            ]  # fmt: skip
        if kind == "SwitchTag":
            cases = c.number(block, "case_count")
            data = bytes.fromhex(fields.get("data", ""))
            tags = list(struct.unpack(f"<{cases}Q", data[8 : 8 + 8 * cases]))
            words = [layout["OP_SWITCH"], *source.words(), cases, *tags]
            for edge in edges:
                words += self.encode_edge(edge, pcs)
            return words
        raise ComposeError(f"{label}: terminator {kind} is not supported")

    def block_words(self, block: int, pcs: dict[int, int]) -> list[int]:
        words: list[int] = []
        for op in self.operations(block):
            words += self.instruction(op)
        return words + self.terminator(block, pcs)

    # ---- the program ----------------------------------------------------------------------------

    def parameters(self) -> list[Value]:
        return [
            self.value(t)
            for _, t in sorted(self.c.references(self.graph, "ParameterValue"))
        ]

    def encode(self) -> bytes:
        layout = self.c.layout
        parameters = self.parameters()
        argument_words = sum((p.width + 7) // 8 for p in parameters)
        table: list[int] = []
        for found in parameters:
            assert found.slot is not None
            table += [found.slot, found.width]
        header_words = 5 + len(table)
        pcs = {block: 0 for block in self.blocks}
        for _ in range(2):  # the lengths do not depend on the addresses
            at = header_words
            for block in self.blocks:
                pcs[block] = at
                at += len(self.block_words(block, pcs))
        words = [
            layout["GRAPH_MAGIC"], self.frame, len(parameters), argument_words, pcs[self.blocks[0]],
            *table,
        ]  # fmt: skip
        for block in self.blocks:
            words += self.block_words(block, pcs)
        return struct.pack(f"<{len(words)}Q", *words)


def object_id(
    image: Image, handle: int, fields: Mapping[str, str] | None = None
) -> int:
    """The id under which the runtime finds an object: no delta holds a handle.

    The ``object_id`` of a semantic object, else the first 8 bytes of the SHA-256 of the path.
    """
    if image.type_name(handle) == "SemanticObject":
        values = fields if fields is not None else dict(image.fields(handle))
        if "object_id" in values:
            return int(values["object_id"])
    return int.from_bytes(
        hashlib.sha256(image.path(handle).encode()).digest()[:8], "little"
    )


class Expression:
    """A function whose body is a call: every call to a native is an instruction.

    A callee that is itself such a function is inlined, so the program calls only leaves.
    A bound value (a data object of a module) is moved from its object into a slot; the
    status of every call is checked, and a refusal ends the run with it.
    """

    def __init__(self, composer: Composer, function: int) -> None:
        self.c = composer
        self.function = function
        self.frame = 0
        self.words: list[list[int]] = []  # the instructions; edges name the end by -1
        self.results: dict[tuple[int, ...], Operand] = {}
        self.status = self.allocate(4)

    def allocate(self, width: int) -> int:
        slot = self.frame
        self.frame += (width + 7) // 8 * 8
        return slot

    def parameters(self, function: int) -> list[Value]:
        found = []
        for child in self.c.children(function, "ValueReference"):
            values = self.c.fields(child)
            if word(values.get("kind")) == "Parameter":
                found.append((self.c.target(values["target"]), child))
        found.sort(key=lambda item: self.index_of(item[0]))
        return [self.c.value(child) for _, child in found]

    def index_of(self, handle: int) -> int:
        name = self.c.image.path(handle).rsplit(".", 1)[-1]
        return int(name[name.index("[") + 1 : -1])

    def sized(self, found: Value) -> Value:
        """A value reference has the size of its type, whatever ``size`` says."""
        values = self.c.fields(found.handle)
        if values.get("value_type"):
            found.width = self.c.size_of(values["value_type"])
        return found

    def operand(
        self, reference: int, inner: Mapping[int, Operand], stack: tuple[int, ...]
    ) -> Operand:
        """The operand of a value reference: a parameter, a bound object or another call."""
        c, layout = self.c, self.c.layout
        found = self.sized(c.value(reference))
        values = c.fields(reference)
        target = c.target(values["target"])
        if found.kind == "Parameter":
            return inner[target]
        if found.kind == "Bound":
            slot = self.allocate(found.width)
            object_ = Operand(
                layout["OPERAND_OBJECT"], object_id(c.image, target), found.width
            )
            self.words.append([layout["OP_MOVE"], slot, found.width, *object_.words()])
            return Operand(layout["OPERAND_SLOT"], slot, found.width)
        if found.kind == "OperationResult":
            return self.call(target, inner, stack)
        raise ComposeError(f"{c.image.path(reference)}: a value of kind {found.kind}")

    def writes(self, function: int) -> bool:
        """Whether the ABI of a native function hands its result back through an output location."""
        c = self.c
        return any(
            word(c.fields(place).get("value_role")) == "Output"
            for abi in c.children(function, "ABISignature")
            for place in c.children(abi, "ABIValueLocation")
        )

    def call(
        self, call: int, inner: Mapping[int, Operand], stack: tuple[int, ...]
    ) -> Operand:
        c, layout = self.c, self.c.layout
        key = (*stack, call)
        if key in self.results:
            return self.results[key]
        values = c.fields(call)
        callee = c.target(values["callee"])
        arguments = sorted(
            c.children(call, "Argument"),
            key=lambda a: self.index_of(c.target(c.fields(a)["parameter"])),
        )
        given = [
            self.operand(c.target(c.fields(a)["value"]), inner, stack)
            for a in arguments
        ]
        implementation = word(c.fields(callee).get("implementation"))
        if implementation == "Semantic":
            body = c.target(c.fields(callee)["body"])
            if c.image.type_name(body) != "Call":
                # the call card: the callee has a graph of its own -- a program the
                # call names by the object id of its words, not an inlined body
                return self.card(callee, given)
            formal = {v.handle: v for v in self.parameters(callee)}
            names = {
                self.c.target(c.fields(v.handle)["target"]): given[i]
                for i, v in enumerate(formal.values())
            }
            result = self.call(body, names, (*stack, call))
        else:
            signature = c.target(c.fields(callee)["signature"])
            results = c.children(signature, "Result")
            width = (
                c.size_of(c.fields(results[0])["value_type"])
                if results and self.writes(callee)
                else 0
            )
            out = self.allocate(width) if width else NONE
            leaf = c.leaf_id(c.image.path(callee))
            words = [layout["OP_CALL"], leaf, len(given), out, width, self.status]
            for operand in given:
                words += operand.words()
            self.words.append(words)
            self.words.append(
                [
                    layout["OP_BRANCH"],
                    layout["OPERAND_SLOT"],
                    self.status,
                    4,
                    layout["MODE_ZERO"],
                    -2,
                    0,
                    -1,
                    0,
                ]
            )
            result = Operand(
                layout["OPERAND_SLOT"], out if width else self.status, width or 4
            )
        self.results[key] = result
        return result

    def card(self, callee: int, given: list[Operand]) -> Operand:
        """A call of a program: the call card of ``graph.h`` -- the callee's words named
        by their object id, its parameters the caller's words, its Return the out word
        of the call. The status is checked the way a leaf call's is."""
        c, layout = self.c, self.c.layout
        signature = c.fields(callee).get("signature")
        results = c.children(c.target(signature), "Result") if signature else []
        width = c.size_of(c.fields(results[0])["value_type"]) if results else 8
        out = self.allocate(width)
        words = [
            layout["OP_CALL"], layout["GRAPH_CALL"], len(given), out, width,
            self.status, layout["OPERAND_OBJECT"], object_id(c.image, callee), 0,
        ]  # fmt: skip
        for operand in given:
            words += operand.words()
        self.words.append(words)
        self.words.append(
            [
                layout["OP_BRANCH"],
                layout["OPERAND_SLOT"],
                self.status,
                4,
                layout["MODE_ZERO"],
                -2,
                0,
                -1,
                0,
            ]
        )
        return Operand(layout["OPERAND_SLOT"], out, width)

    def program(self) -> bytes:
        c, layout = self.c, self.c.layout
        function = self.c.fields(self.function)
        body = c.target(function["body"])
        parameters = self.parameters(self.function)
        table: list[int] = []
        inner: dict[int, Operand] = {}
        for found in parameters:
            self.sized(found)
            slot = self.allocate(found.width)
            table += [slot, found.width]
            inner[c.target(c.fields(found.handle)["target"])] = Operand(
                layout["OPERAND_SLOT"], slot, found.width
            )
        result = self.call(body, inner, ())
        self.words.append([layout["OP_RETURN"], *result.words(), result.b])
        header = 5 + len(table)
        # lay out: a branch jumps to the next instruction (-2) or to the refusal (-1)
        at = header
        starts = []
        for words in self.words:
            starts.append(at)
            at += len(words)
        refusal = at
        flat: list[int] = []
        for index, words in enumerate(self.words):
            following = starts[index] + len(words)
            if words[0] == layout["OP_BRANCH"]:
                words = [
                    following if w == -2 else refusal if w == -1 else w for w in words
                ]
            flat += words
        flat += [layout["OP_REFUSE"], layout["OPERAND_SLOT"], self.status, 4]
        words = [
            layout["GRAPH_MAGIC"],
            self.frame,
            len(parameters),
            sum((p.width + 7) // 8 for p in parameters),
            header,
            *table,
            *flat,
        ]
        return struct.pack(f"<{len(words)}Q", *words)


def programs(image: Image, layout: Layout | None = None) -> dict[str, bytes]:
    """The program of every semantic function of the image, by the path of the function."""
    composer = Composer(image, layout)
    found: dict[str, bytes] = {}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "SemanticGraph":
            continue
        function = composer.fields(handle).get("function")
        if not function:
            raise ComposeError(f"{image.path(handle)} belongs to no function")
        try:
            found[function] = composer.program(handle)
        except (ComposeError, KeyError) as error:
            raise ComposeError(f"{image.path(handle)}: {error}") from error
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Function":
            continue
        values = composer.fields(handle)
        if word(values.get("implementation")) != "Semantic" or not values.get("body"):
            continue
        if image.path(handle) in found:
            continue  # a graph: its body is a call to itself, for readers that have no graphs
        if image.type_name(composer.target(values["body"])) != "Call":
            continue
        try:
            found[image.path(handle)] = Expression(composer, handle).program()
        except (ComposeError, KeyError) as error:
            raise ComposeError(f"{image.path(handle)}: {error}") from error
    return dict(sorted(found.items()))


def describe(program: bytes) -> Sequence[int]:
    """The words of a program."""
    return struct.unpack(f"<{len(program) // 8}Q", program)


def program_objects(image: Image, programs: Mapping[str, bytes]) -> dict[int, bytes]:
    """The programs as an object table carries them: every program's words at the id
    its call card names -- the object id of its Function (the same number the callee's
    operand holds), so a run resolves the callee's words by the handle the call gives.
    """
    found: dict[int, bytes] = {}
    for path, words in programs.items():
        handle = image.resolve(path)
        if handle is None:
            raise ComposeError(f"{path}: no function of the image answers that path")
        found[object_id(image, handle)] = words
    return dict(sorted(found.items()))