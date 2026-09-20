"""Typed compiler-semantic CFG records for authored stdlib bodies.

The records are source graph objects, not VM instructions. Opcodes identify lowering
concepts; all durable operands are object/type/function IDs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha256

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import Effect, FunctionImplementation
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    BOOL_TYPE,
    CALL_TYPE,
    FUNCTION_TYPE,
    U32_TYPE,
)


class SemanticOpcode(IntEnum):
    BEGIN_PRIVATE = 1
    RESOLVE_HANDLE = 2
    ALLOCATE = 3
    COPY = 4
    WRITE = 5
    READ = 6
    VALIDATE = 7
    PUBLISH = 8
    ROLLBACK = 9
    CLEANUP = 10
    READ_TAG = 11
    PROJECT_PAYLOAD = 12
    CONSTRUCT_OPTION = 13
    CONSTRUCT_RESULT = 14
    ITER_INIT = 15
    ITER_CONDITION = 16
    ITER_PROJECT = 17
    ITER_ADVANCE = 18
    CALL_CALLBACK = 19
    ACCUMULATE = 20
    COMPARE = 21
    SORT_INSERT = 22
    HASH = 23
    EQUAL = 24
    UTF8_VALIDATE = 25
    UTF8_BOUNDARY = 26
    FORMAT_INTEGER = 27
    APPEND_BYTES = 28
    SEARCH = 29
    MAP_REFUSAL = 30
    ENRICH_REFUSAL = 31
    RETURN_VALUE = 32
    RETURN_REFUSAL = 33
    LOAD_FIELD = 34
    STORE_FIELD = 35
    CHECK_BOUNDS = 36
    CHECK_GENERATION = 37
    BORROW = 38
    RELEASE_BORROW = 39
    PIN = 40
    UNPIN = 41
    MOVE_SLOT = 42
    CANONICAL_ORDER = 43
    LOOKUP = 44


class SemanticValueKind(IntEnum):
    PARAMETER = 1
    LITERAL = 2
    OP_RESULT = 3
    BLOCK_PARAMETER = 4
    OBJECT = 5
    FUNCTION = 6


class TerminatorKind(IntEnum):
    GOTO = 1
    BRANCH = 2
    SWITCH_TAG = 3
    LOOP = 4
    RETURN = 5
    REFUSE = 6


class SemanticRole:
    FUNCTION = 340
    ENTRY = 341
    BLOCK = 342
    PREDECESSOR = 343
    SUCCESSOR = 344
    CONDITION = 345
    OPERATION = 346
    INPUT_TYPE_BASE = 400
    RESULT_TYPE_BASE = 500
    CALLEE = 600
    TARGET_OBJECT = 601
    TARGET_FIELD = 602
    REFUSAL_SET = 603
    TRANSFORM_AUTHORITY = 604
    INPUT_VALUE_BASE = 700
    RESULT_VALUE_BASE = 800
    PARAMETER_VALUE_BASE = 900
    GRAPH_RESULT_VALUE_BASE = 1000
    BLOCK_PARAMETER_VALUE_BASE = 1100
    TERMINATOR_VALUE = 1200
    SUCCESSOR_ARGUMENT_BASE = 1300
    DEFINITION = 1400
    OWNER_FUNCTION = 1401
    OWNER_BLOCK = 1402
    OWNER_OPERATION = 1403


@dataclass(frozen=True)
class SemanticValue:
    id: int
    name: str
    parent: int
    type_id: int
    kind: SemanticValueKind
    owner_function_id: int
    owner_block_id: int = 0
    owner_operation_id: int = 0
    result_index: int = 0
    source_id: int = 0
    literal: bytes = b""

    def to_node(self, record_type: int) -> NodeDef:
        data = (
            struct.pack(
                "<Q4I4Q",
                self.type_id,
                int(self.kind),
                self.result_index,
                len(self.literal),
                0,
                self.owner_function_id,
                self.owner_block_id,
                self.owner_operation_id,
                self.source_id,
            )
            + self.literal
        )
        refs = [
            RefDef(self.id, self.type_id, RefKind.TYPE, to_off=SemanticRole.DEFINITION),
            RefDef(
                self.id,
                self.owner_function_id,
                RefKind.DATA,
                to_off=SemanticRole.OWNER_FUNCTION,
            ),
        ]
        if self.owner_block_id:
            refs.append(
                RefDef(
                    self.id,
                    self.owner_block_id,
                    RefKind.DATA,
                    to_off=SemanticRole.OWNER_BLOCK,
                )
            )
        if self.owner_operation_id:
            refs.append(
                RefDef(
                    self.id,
                    self.owner_operation_id,
                    RefKind.DATA,
                    to_off=SemanticRole.OWNER_OPERATION,
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> SemanticValue:
        if len(node.data) < 56:
            raise ValueError(f"SemanticValue {node.id} payload is truncated")
        type_id, kind, index, size, reserved, function, block, operation, source = (
            struct.unpack("<Q4I4Q", node.data[:56])
        )
        literal = node.data[56:]
        if reserved or len(literal) != size:
            raise ValueError(f"SemanticValue {node.id} payload extent is invalid")
        return cls(
            node.id,
            node.name,
            node.parent,
            type_id,
            SemanticValueKind(kind),
            function,
            block,
            operation,
            index,
            source,
            literal,
        )


@dataclass(frozen=True)
class SemanticOperation:
    id: int
    name: str
    parent: int
    opcode: SemanticOpcode
    index: int
    input_types: tuple[int, ...]
    result_types: tuple[int, ...]
    input_value_ids: tuple[int, ...]
    result_value_ids: tuple[int, ...]
    status_value_id: int = 0
    effects: Effect = Effect.NONE
    refusal_set_id: int = 0
    callee_id: int = 0
    target_object_id: int = 0
    target_field_id: int = 0
    transform_authority_id: int = 0

    def to_node(self, record_type: int) -> NodeDef:
        data = struct.pack(
            "<8I6Q",
            int(self.opcode),
            self.index,
            len(self.input_types),
            len(self.result_types),
            int(self.effects),
            len(self.input_value_ids),
            len(self.result_value_ids),
            0,
            self.refusal_set_id,
            self.callee_id,
            self.status_value_id,
            self.target_object_id,
            self.target_field_id,
            self.transform_authority_id,
        )
        refs = [
            RefDef(
                self.id, value, RefKind.TYPE, to_off=SemanticRole.INPUT_TYPE_BASE + i
            )
            for i, value in enumerate(self.input_types)
        ]
        refs += [
            RefDef(
                self.id, value, RefKind.TYPE, to_off=SemanticRole.RESULT_TYPE_BASE + i
            )
            for i, value in enumerate(self.result_types)
        ]
        refs += [
            RefDef(
                self.id, value, RefKind.DATA, to_off=SemanticRole.INPUT_VALUE_BASE + i
            )
            for i, value in enumerate(self.input_value_ids)
        ]
        refs += [
            RefDef(
                self.id, value, RefKind.DATA, to_off=SemanticRole.RESULT_VALUE_BASE + i
            )
            for i, value in enumerate(self.result_value_ids)
        ]
        if self.status_value_id:
            refs.append(
                RefDef(
                    self.id,
                    self.status_value_id,
                    RefKind.DATA,
                    to_off=SemanticRole.TERMINATOR_VALUE,
                )
            )
        if self.callee_id:
            refs.append(
                RefDef(
                    self.id, self.callee_id, RefKind.CALL, to_off=SemanticRole.CALLEE
                )
            )
        if self.target_object_id:
            refs.append(
                RefDef(
                    self.id,
                    self.target_object_id,
                    RefKind.DATA,
                    to_off=SemanticRole.TARGET_OBJECT,
                )
            )
        if self.target_field_id:
            refs.append(
                RefDef(
                    self.id,
                    self.target_field_id,
                    RefKind.DATA,
                    to_off=SemanticRole.TARGET_FIELD,
                )
            )
        if self.transform_authority_id:
            refs.append(
                RefDef(
                    self.id,
                    self.transform_authority_id,
                    RefKind.DATA,
                    to_off=SemanticRole.TRANSFORM_AUTHORITY,
                )
            )
        if self.refusal_set_id:
            refs.append(
                RefDef(
                    self.id,
                    self.refusal_set_id,
                    RefKind.DATA,
                    to_off=SemanticRole.REFUSAL_SET,
                )
            )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> SemanticOperation:
        if len(node.data) != 80:
            raise ValueError(f"SemanticOperation {node.id} payload must be 80 bytes")
        (
            opcode,
            index,
            ni,
            nr,
            effects,
            nvi,
            nvr,
            r2,
            refusal,
            callee,
            status,
            target,
            field,
            authority,
        ) = struct.unpack("<8I6Q", node.data)
        if r2:
            raise ValueError(f"SemanticOperation {node.id} reserved words are nonzero")
        inputs = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.INPUT_TYPE_BASE
            <= ref.to_off
            < SemanticRole.RESULT_TYPE_BASE
        )
        results = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.RESULT_TYPE_BASE <= ref.to_off < SemanticRole.CALLEE
        )
        input_values = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.INPUT_VALUE_BASE
            <= ref.to_off
            < SemanticRole.RESULT_VALUE_BASE
        )
        result_values = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.RESULT_VALUE_BASE
            <= ref.to_off
            < SemanticRole.PARAMETER_VALUE_BASE
        )
        if (
            len(inputs) != ni
            or len(results) != nr
            or len(input_values) != nvi
            or len(result_values) != nvr
        ):
            raise ValueError(
                f"SemanticOperation {node.id} type/value count disagreement"
            )
        return cls(
            node.id,
            node.name,
            node.parent,
            SemanticOpcode(opcode),
            index,
            inputs,
            results,
            input_values,
            result_values,
            status,
            Effect(effects),
            refusal,
            callee,
            target,
            field,
            authority,
        )


@dataclass(frozen=True)
class SemanticBlock:
    id: int
    name: str
    parent: int
    index: int
    terminator: TerminatorKind
    operation_ids: tuple[int, ...]
    predecessor_ids: tuple[int, ...]
    successor_ids: tuple[int, ...]
    parameter_types: tuple[int, ...] = ()
    parameter_value_ids: tuple[int, ...] = ()
    condition_value_id: int = 0
    terminator_value_id: int = 0
    successor_arguments: tuple[tuple[int, ...], ...] = ()
    case_tags: tuple[int, ...] = ()

    @property
    def condition_operation_id(self) -> int:
        """RXF v5 compiler compatibility; conditions now identify SSA values."""
        return 0

    def to_node(self, record_type: int) -> NodeDef:
        if len(self.successor_arguments) != len(self.successor_ids):
            raise ValueError("successor argument map count mismatch")
        counts = tuple(len(v) for v in self.successor_arguments)
        data = struct.pack(
            "<8I3Q",
            self.index,
            int(self.terminator),
            len(self.operation_ids),
            len(self.predecessor_ids),
            len(self.successor_ids),
            len(self.parameter_types),
            len(self.case_tags),
            len(counts),
            self.condition_value_id,
            self.terminator_value_id,
            0,
        )
        data += b"".join(struct.pack("<Q", v) for v in (*self.case_tags, *counts))
        refs = [
            RefDef(self.id, v, RefKind.DATA, to_off=SemanticRole.OPERATION)
            for v in self.operation_ids
        ]
        refs += [
            RefDef(self.id, v, RefKind.DATA, to_off=SemanticRole.PREDECESSOR)
            for v in self.predecessor_ids
        ]
        refs += [
            RefDef(self.id, v, RefKind.DATA, to_off=SemanticRole.SUCCESSOR)
            for v in self.successor_ids
        ]
        refs += [
            RefDef(self.id, v, RefKind.TYPE, to_off=SemanticRole.INPUT_TYPE_BASE + i)
            for i, v in enumerate(self.parameter_types)
        ]
        refs += [
            RefDef(
                self.id,
                v,
                RefKind.DATA,
                to_off=SemanticRole.BLOCK_PARAMETER_VALUE_BASE + i,
            )
            for i, v in enumerate(self.parameter_value_ids)
        ]
        if self.condition_value_id:
            refs.append(
                RefDef(
                    self.id,
                    self.condition_value_id,
                    RefKind.DATA,
                    to_off=SemanticRole.CONDITION,
                )
            )
        if self.terminator_value_id:
            refs.append(
                RefDef(
                    self.id,
                    self.terminator_value_id,
                    RefKind.DATA,
                    to_off=SemanticRole.TERMINATOR_VALUE,
                )
            )
        for edge, args in enumerate(self.successor_arguments):
            refs += [
                RefDef(
                    self.id,
                    v,
                    RefKind.DATA,
                    to_off=SemanticRole.SUCCESSOR_ARGUMENT_BASE + edge * 64 + i,
                )
                for i, v in enumerate(args)
            ]
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> SemanticBlock:
        if len(node.data) < 56 or (len(node.data) - 56) % 8:
            raise ValueError(f"SemanticBlock {node.id} payload extent is invalid")
        (
            index,
            term,
            no,
            np,
            ns,
            nparams,
            ncases,
            ncounts,
            condition,
            terminal,
            reserved,
        ) = struct.unpack("<8I3Q", node.data[:56])
        if reserved or ncounts != ns:
            raise ValueError(f"SemanticBlock {node.id} successor map disagreement")
        words = (
            struct.unpack(f"<{ncases + ncounts}Q", node.data[56:])
            if ncases + ncounts
            else ()
        )
        cases, counts = tuple(words[:ncases]), tuple(words[ncases:])

        def role(v: int):
            return tuple(r.target for r in node.refs if r.to_off == v)

        ops, preds, succs = (
            role(SemanticRole.OPERATION),
            role(SemanticRole.PREDECESSOR),
            role(SemanticRole.SUCCESSOR),
        )
        types = tuple(
            r.target
            for r in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.INPUT_TYPE_BASE <= r.to_off < SemanticRole.RESULT_TYPE_BASE
        )
        values = tuple(
            r.target
            for r in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.BLOCK_PARAMETER_VALUE_BASE
            <= r.to_off
            < SemanticRole.TERMINATOR_VALUE
        )
        edge_args = tuple(
            tuple(
                r.target
                for r in sorted(node.refs, key=lambda r: r.to_off)
                if SemanticRole.SUCCESSOR_ARGUMENT_BASE + i * 64
                <= r.to_off
                < SemanticRole.SUCCESSOR_ARGUMENT_BASE + (i + 1) * 64
            )
            for i in range(ns)
        )
        if (len(ops), len(preds), len(succs), len(types), len(values)) != (
            no,
            np,
            ns,
            nparams,
            nparams,
        ) or tuple(map(len, edge_args)) != counts:
            raise ValueError(f"SemanticBlock {node.id} count disagreement")
        return cls(
            node.id,
            node.name,
            node.parent,
            index,
            TerminatorKind(term),
            ops,
            preds,
            succs,
            types,
            values,
            condition,
            terminal,
            edge_args,
            cases,
        )


@dataclass(frozen=True)
class SemanticGraph:
    id: int
    name: str
    parent: int
    function_id: int
    entry_block_id: int
    block_ids: tuple[int, ...]
    refusal_set_id: int
    effects: Effect
    output_type: int
    parameter_value_ids: tuple[int, ...] = ()
    result_value_ids: tuple[int, ...] = ()

    def to_node(self, record_type: int) -> NodeDef:
        data = struct.pack(
            "<4Q4I",
            self.function_id,
            self.entry_block_id,
            self.refusal_set_id,
            self.output_type,
            len(self.block_ids),
            int(self.effects),
            len(self.parameter_value_ids),
            len(self.result_value_ids),
        )
        refs = [
            RefDef(
                self.id, self.function_id, RefKind.DATA, to_off=SemanticRole.FUNCTION
            ),
            RefDef(
                self.id, self.entry_block_id, RefKind.DATA, to_off=SemanticRole.ENTRY
            ),
        ]
        refs += [
            RefDef(self.id, v, RefKind.DATA, to_off=SemanticRole.BLOCK)
            for v in self.block_ids
        ]
        refs += [
            RefDef(
                self.id, v, RefKind.DATA, to_off=SemanticRole.PARAMETER_VALUE_BASE + i
            )
            for i, v in enumerate(self.parameter_value_ids)
        ]
        refs += [
            RefDef(
                self.id,
                v,
                RefKind.DATA,
                to_off=SemanticRole.GRAPH_RESULT_VALUE_BASE + i,
            )
            for i, v in enumerate(self.result_value_ids)
        ]
        refs.append(
            RefDef(
                self.id,
                self.refusal_set_id,
                RefKind.DATA,
                to_off=SemanticRole.REFUSAL_SET,
            )
        )
        return NodeDef(
            self.id,
            self.name,
            NodeKind.DATA,
            self.parent,
            type_id=record_type,
            data=data,
            refs=refs,
        )

    @classmethod
    def from_node(cls, node: NodeDef) -> SemanticGraph:
        if len(node.data) != 48:
            raise ValueError(f"SemanticGraph {node.id} payload must be 48 bytes")
        function, entry, refusal, output, count, effects, np, nr = struct.unpack(
            "<4Q4I", node.data
        )
        blocks = tuple(
            ref.target for ref in node.refs if ref.to_off == SemanticRole.BLOCK
        )
        parameters = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.PARAMETER_VALUE_BASE
            <= ref.to_off
            < SemanticRole.GRAPH_RESULT_VALUE_BASE
        )
        results = tuple(
            ref.target
            for ref in sorted(node.refs, key=lambda r: r.to_off)
            if SemanticRole.GRAPH_RESULT_VALUE_BASE
            <= ref.to_off
            < SemanticRole.BLOCK_PARAMETER_VALUE_BASE
        )
        if len(blocks) != count or len(parameters) != np or len(results) != nr:
            raise ValueError(f"SemanticGraph {node.id} count disagreement")
        return cls(
            node.id,
            node.name,
            node.parent,
            function,
            entry,
            blocks,
            refusal,
            Effect(effects),
            output,
            parameters,
            results,
        )

    def semantic_digest(self, container: Container) -> bytes:
        by = {n.id: n for n in container.nodes}

        def encoded(node: NodeDef) -> bytes:
            refs = sorted(
                (int(ref.kind), int(ref.binding), ref.target, ref.to_off, ref.source)
                for ref in node.refs
            )
            return (
                node.id.to_bytes(8, "little")
                + node.data
                + b"".join(struct.pack("<IIQQQ", *ref) for ref in refs)
            )

        payload = bytearray(encoded(by[self.id]))
        value_ids = {*self.parameter_value_ids, *self.result_value_ids}
        for bid in self.block_ids:
            block = SemanticBlock.from_node(by[bid])
            payload += encoded(by[bid])
            value_ids.update(block.parameter_value_ids)
            for args in block.successor_arguments:
                value_ids.update(args)
            if block.condition_value_id:
                value_ids.add(block.condition_value_id)
            if block.terminator_value_id:
                value_ids.add(block.terminator_value_id)
            for oid in block.operation_ids:
                op = SemanticOperation.from_node(by[oid])
                payload += encoded(by[oid])
                value_ids.update((*op.input_value_ids, *op.result_value_ids))
                if op.status_value_id:
                    value_ids.add(op.status_value_id)
        for value_id in sorted(value_ids):
            payload += encoded(by[value_id])
        return sha256(payload).digest()


def verify_semantic_graphs(
    container: Container,
    graph_type: int,
    block_type: int,
    operation_type: int,
    value_type: int = 41009,
) -> list[str]:
    errors = []
    by_id = {n.id: n for n in container.nodes}
    graphs = []
    for n in container.nodes:
        try:
            if n.type_id == graph_type:
                graphs.append(SemanticGraph.from_node(n))
            elif n.type_id == block_type:
                SemanticBlock.from_node(n)
            elif n.type_id == operation_type:
                SemanticOperation.from_node(n)
            elif n.type_id == value_type:
                SemanticValue.from_node(n)
        except ValueError as e:
            errors.append(str(e))
    for graph in graphs:
        fn = by_id.get(graph.function_id)
        if (
            fn is None
            or fn.type_id != FUNCTION_TYPE
            or decode_function(fn).implementation != FunctionImplementation.COMPOSED
        ):
            errors.append(f"SemanticGraph {graph.id} Function is not composed")
            continue
        if (
            decode_function(fn).body_id == 0
            or by_id.get(decode_function(fn).body_id) is None
            or by_id[decode_function(fn).body_id].type_id != CALL_TYPE
        ):
            errors.append(f"SemanticGraph {graph.id} ordinary body Call is missing")
        blocks = []
        for bid in graph.block_ids:
            n = by_id.get(bid)
            if n is None or n.type_id != block_type:
                errors.append(f"SemanticGraph {graph.id} block {bid} is missing")
                continue
            blocks.append(SemanticBlock.from_node(n))
        ids = {b.id for b in blocks}
        if graph.entry_block_id not in ids:
            errors.append(f"SemanticGraph {graph.id} entry block is missing")
        if sorted(b.index for b in blocks) != list(range(len(blocks))):
            errors.append(f"SemanticGraph {graph.id} block indices are not contiguous")
        for block in blocks:
            if any(
                v not in ids for v in (*block.predecessor_ids, *block.successor_ids)
            ):
                errors.append(f"SemanticBlock {block.id} edge leaves graph")
            for succ in block.successor_ids:
                target = next((x for x in blocks if x.id == succ), None)
                if target and block.id not in target.predecessor_ids:
                    errors.append(
                        f"SemanticBlock {block.id} successor/predecessor disagreement"
                    )
            ops = []
            for oid in block.operation_ids:
                n = by_id.get(oid)
                if n is None or n.type_id != operation_type:
                    errors.append(
                        f"SemanticBlock {block.id} operation {oid} is missing"
                    )
                    continue
                op = SemanticOperation.from_node(n)
                ops.append(op)
                if op.parent != block.id:
                    errors.append(f"SemanticOperation {op.id} belongs to wrong block")
                if (
                    op.opcode in {SemanticOpcode.HASH, SemanticOpcode.LOOKUP}
                    and op.transform_authority_id
                ):
                    from pymergetic.rxf.model.stdlib import HashAuthority

                    try:
                        authority_node = by_id[op.transform_authority_id]
                        authority = HashAuthority.from_node(authority_node)
                        expected = (
                            authority.hash_function
                            if op.opcode == SemanticOpcode.HASH
                            else authority.map_lookup_function
                            if op.result_types
                            and op.result_types[0] == 0xDD_D6E5_05AA_259D_9A
                            else authority.set_lookup_function
                        )
                        if op.callee_id != expected:
                            errors.append(
                                f"SemanticOperation {op.id} hash authority callee mismatch"
                            )
                        if op.opcode == SemanticOpcode.HASH and op.input_types != (
                            authority.input_type,
                            authority.input_type,
                        ):
                            errors.append(
                                f"SemanticOperation {op.id} HASH authority type mismatch"
                            )
                    except (KeyError, ValueError):
                        errors.append(
                            f"SemanticOperation {op.id} hash authority is invalid"
                        )
                if op.opcode == SemanticOpcode.CANONICAL_ORDER:
                    from pymergetic.rxf.model.stdlib import CanonicalOrderAuthority

                    authority_node = by_id.get(op.transform_authority_id)
                    authority = None
                    if authority_node is not None:
                        try:
                            authority = CanonicalOrderAuthority.from_node(
                                authority_node
                            )
                        except ValueError:
                            pass
                    if authority is None:
                        errors.append(
                            f"SemanticOperation {op.id} CANONICAL_ORDER authority is missing"
                        )
                    elif op.input_types != (
                        authority.input_type,
                    ) or op.result_types != (authority.output_type,):
                        errors.append(
                            f"SemanticOperation {op.id} CANONICAL_ORDER authority type mismatch"
                        )
                    elif (
                        authority.representation_preserving
                        and authority.input_type != authority.output_type
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} CANONICAL_ORDER representation policy is invalid"
                        )
                if any(
                    t not in {x.id for x in container.nodes if x.kind == NodeKind.TYPE}
                    for t in (*op.input_types, *op.result_types)
                ):
                    errors.append(f"SemanticOperation {op.id} type is missing")
                if op.opcode in {
                    SemanticOpcode.READ_TAG,
                    SemanticOpcode.PROJECT_PAYLOAD,
                    SemanticOpcode.CONSTRUCT_OPTION,
                    SemanticOpcode.CONSTRUCT_RESULT,
                }:
                    from pymergetic.rxf.ty.builtins import FIELD_TYPE
                    from pymergetic.rxf.ty.objects import (
                        FieldObject,
                        TypeForm,
                        TypeObject,
                    )

                    tagged_type = (
                        op.input_types[0]
                        if op.opcode
                        in {SemanticOpcode.READ_TAG, SemanticOpcode.PROJECT_PAYLOAD}
                        and op.input_types
                        else op.result_types[0]
                        if op.result_types
                        else 0
                    )
                    tagged_node = by_id.get(tagged_type)
                    tagged = None
                    if tagged_node is not None and tagged_node.kind == NodeKind.TYPE:
                        try:
                            tagged = TypeObject.from_payload(
                                id=tagged_node.id,
                                name=tagged_node.name,
                                payload=tagged_node.data,
                            )
                        except ValueError:
                            pass
                    if tagged is None or tagged.form != TypeForm.UNION:
                        errors.append(
                            f"SemanticOperation {op.id} tagged value is not a union Type"
                        )
                    field_node = by_id.get(op.target_field_id)
                    field = None
                    if field_node is not None and field_node.type_id == FIELD_TYPE:
                        try:
                            field = FieldObject.from_payload(
                                id=field_node.id,
                                name=field_node.name,
                                payload=field_node.data,
                            )
                        except ValueError:
                            pass
                    if field is None or field.owner_type != tagged_type:
                        errors.append(
                            f"SemanticOperation {op.id} tagged field is missing or belongs to another Type"
                        )
                    elif op.opcode == SemanticOpcode.READ_TAG and (
                        field.offset != 0
                        or field.value_type != U32_TYPE
                        or op.result_types[0] != U32_TYPE
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} tag field/type is invalid"
                        )
                    elif (
                        op.opcode == SemanticOpcode.PROJECT_PAYLOAD
                        and op.result_types
                        and field.value_type != op.result_types[0]
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} payload projection type disagrees with field"
                        )
            if [o.index for o in ops] != list(range(len(ops))):
                errors.append(
                    f"SemanticBlock {block.id} operation indices are not ordered"
                )
            if (
                block.terminator in (TerminatorKind.RETURN, TerminatorKind.REFUSE)
                and block.successor_ids
            ):
                errors.append(f"SemanticBlock {block.id} terminal block has successors")
            if (
                block.terminator == TerminatorKind.BRANCH
                and len(block.successor_ids) != 2
            ):
                errors.append(f"SemanticBlock {block.id} branch is not exhaustive")
            if block.terminator == TerminatorKind.BRANCH:
                condition_value = next(
                    (
                        SemanticValue.from_node(candidate)
                        for candidate in container.nodes
                        if candidate.type_id == value_type
                        and candidate.id == block.condition_value_id
                    ),
                    None,
                )
                if condition_value is None or condition_value.type_id not in {
                    BOOL_TYPE,
                    U32_TYPE,
                }:
                    errors.append(
                        f"SemanticBlock {block.id} BRANCH requires bool or uint32 status"
                    )
                elif condition_value.type_id == U32_TYPE:
                    owner_node = by_id.get(condition_value.owner_operation_id)
                    owner = (
                        SemanticOperation.from_node(owner_node)
                        if owner_node is not None
                        and owner_node.type_id == operation_type
                        else None
                    )
                    if (
                        owner is None
                        or owner.status_value_id != condition_value.id
                        or not block.operation_ids
                        or block.operation_ids[-1] != owner.id
                    ):
                        errors.append(
                            f"SemanticBlock {block.id} status branch is not an immediate call status"
                        )
            if block.terminator == TerminatorKind.SWITCH_TAG and (
                len(block.successor_ids) != len(block.case_tags) + 1
            ):
                errors.append(f"SemanticBlock {block.id} switch lacks default")
        opcodes = {
            SemanticOperation.from_node(by_id[oid]).opcode
            for b in blocks
            for oid in b.operation_ids
            if by_id.get(oid) is not None
        }
        if (
            SemanticOpcode.PUBLISH in opcodes
            and not {
                SemanticOpcode.VALIDATE,
                SemanticOpcode.ROLLBACK,
                SemanticOpcode.CLEANUP,
            }
            <= opcodes
        ):
            errors.append(f"SemanticGraph {graph.id} transaction control is incomplete")
        if SemanticOpcode.BEGIN_PRIVATE in opcodes:
            begin = next(
                (
                    op
                    for b in blocks
                    for oid in b.operation_ids
                    for op in (SemanticOperation.from_node(by_id[oid]),)
                    if op.opcode == SemanticOpcode.BEGIN_PRIVATE
                ),
                None,
            )
            rollback_blocks = [
                b
                for b in blocks
                if any(
                    SemanticOperation.from_node(by_id[oid]).opcode
                    == SemanticOpcode.ROLLBACK
                    for oid in b.operation_ids
                )
            ]
            cleanup_blocks = [
                b
                for b in blocks
                if any(
                    SemanticOperation.from_node(by_id[oid]).opcode
                    == SemanticOpcode.CLEANUP
                    for oid in b.operation_ids
                )
            ]
            if begin is None or not rollback_blocks or not cleanup_blocks:
                errors.append(
                    f"SemanticGraph {graph.id} private transaction terminal chain is incomplete"
                )
            else:
                transaction_id = next(
                    (
                        value
                        for value, typ in zip(
                            begin.result_value_ids, begin.result_types
                        )
                        if typ != U32_TYPE
                    ),
                    0,
                )
                terminal_ops = [
                    operation
                    for block in (*rollback_blocks, *cleanup_blocks)
                    for oid in block.operation_ids
                    for operation in (SemanticOperation.from_node(by_id[oid]),)
                    if operation.opcode
                    in {SemanticOpcode.ROLLBACK, SemanticOpcode.CLEANUP}
                ]
                if not transaction_id or any(
                    transaction_id not in op.input_value_ids for op in terminal_ops
                ):
                    errors.append(
                        f"SemanticGraph {graph.id} substitutes private transaction value"
                    )
                rollback_id, cleanup_id = rollback_blocks[0].id, cleanup_blocks[0].id
                next(b for b in blocks if begin.id in b.operation_ids)
                for block in blocks:
                    block_ops = [
                        SemanticOperation.from_node(by_id[oid])
                        for oid in block.operation_ids
                    ]
                    opcode = block_ops[-1].opcode if block_ops else None
                    if (
                        block.terminator == TerminatorKind.BRANCH
                        and len(block.successor_ids) == 2
                    ):
                        failure = block.successor_ids[1]
                        if opcode == SemanticOpcode.BEGIN_PRIVATE and failure in {
                            rollback_id,
                            cleanup_id,
                        }:
                            errors.append(
                                f"SemanticGraph {graph.id} cleans failed uninitialized transaction"
                            )
                        elif (
                            block_ops
                            and block_ops[-1].status_value_id
                            and opcode
                            not in {
                                SemanticOpcode.BEGIN_PRIVATE,
                                SemanticOpcode.ROLLBACK,
                                SemanticOpcode.CLEANUP,
                            }
                            and failure != rollback_id
                        ):
                            errors.append(
                                f"SemanticGraph {graph.id} post-begin failure bypasses rollback"
                            )
                        elif (
                            opcode == SemanticOpcode.ROLLBACK and failure != cleanup_id
                        ):
                            errors.append(
                                f"SemanticGraph {graph.id} rollback failure bypasses cleanup"
                            )
        if SemanticOpcode.READ_TAG in opcodes and not any(
            b.terminator == TerminatorKind.SWITCH_TAG for b in blocks
        ):
            errors.append(f"SemanticGraph {graph.id} tag read lacks switch")
        if (
            SemanticOpcode.ITER_INIT in opcodes
            and not {
                SemanticOpcode.ITER_CONDITION,
                SemanticOpcode.ITER_PROJECT,
                SemanticOpcode.ITER_ADVANCE,
            }
            <= opcodes
        ):
            errors.append(f"SemanticGraph {graph.id} iterator loop is incomplete")
        if SemanticOpcode.ITER_INIT in opcodes and graph.name != "stable_sort_graph":
            headers = [b for b in blocks if b.terminator == TerminatorKind.LOOP]
            if len(headers) != 1:
                errors.append(f"SemanticGraph {graph.id} iterator header is missing")
            else:
                header = headers[0]
                header_ops = [
                    SemanticOperation.from_node(by_id[oid])
                    for oid in header.operation_ids
                ]
                conditions = [
                    op
                    for op in header_ops
                    if op.opcode == SemanticOpcode.ITER_CONDITION
                ]
                if len(header.parameter_value_ids) < 2:
                    errors.append(f"SemanticGraph {graph.id} iterator bound is missing")
                if len(conditions) != 1 or header.condition_value_id not in (
                    conditions[0].result_value_ids if conditions else ()
                ):
                    errors.append(
                        f"SemanticGraph {graph.id} iterator condition is not recomputed in header"
                    )
                # Status-split loop backedges are checked by generic phi validation.
        values = {
            n.id: SemanticValue.from_node(n)
            for n in container.nodes
            if n.type_id == value_type
            and SemanticValue.from_node(n).owner_function_id == graph.function_id
        }
        function_record = decode_function(fn)
        signature = by_id.get(function_record.signature_id)
        signature_parameters = (
            tuple(ref.target for ref in signature.refs if ref.to_off == 204)
            if signature
            else ()
        )
        signature_results = (
            tuple(ref.target for ref in signature.refs if ref.to_off == 218)
            if signature
            else ()
        )
        if len(graph.parameter_value_ids) != len(signature_parameters):
            errors.append(
                f"SemanticGraph {graph.id} Function parameter mapping mismatch"
            )
        if not graph.result_value_ids:
            errors.append(f"SemanticGraph {graph.id} has no result values")
        for value_id in (*graph.parameter_value_ids, *graph.result_value_ids):
            if value_id not in values:
                errors.append(f"SemanticGraph {graph.id} value {value_id} is missing")
        definitions: dict[int, int] = {}
        op_position = {
            oid: (b.index, i) for b in blocks for i, oid in enumerate(b.operation_ids)
        }
        uses = 0
        if len(values) != len(set(values)):
            errors.append(f"SemanticGraph {graph.id} repeats value definition")
        for value in values.values():
            if value.owner_function_id != graph.function_id:
                errors.append(f"SemanticValue {value.id} belongs to wrong Function")
            if (
                value.kind == SemanticValueKind.PARAMETER
                and value.source_id not in signature_parameters
            ):
                errors.append(
                    f"SemanticValue {value.id} does not map a Signature Parameter"
                )
            if (
                value.id in graph.result_value_ids
                and value.source_id not in signature_results
            ):
                errors.append(f"SemanticValue {value.id} does not map a ResultSlot")
            if value.kind == SemanticValueKind.OP_RESULT:
                definitions[value.id] = value.owner_operation_id
                opnode = by_id.get(value.owner_operation_id)
                if (
                    opnode is None
                    or opnode.type_id != operation_type
                    or value.id
                    not in (
                        *SemanticOperation.from_node(opnode).result_value_ids,
                        SemanticOperation.from_node(opnode).status_value_id,
                    )
                ):
                    errors.append(f"SemanticValue {value.id} result backref is missing")
            elif (
                value.kind == SemanticValueKind.PARAMETER
                and value.id not in graph.parameter_value_ids
            ):
                errors.append(
                    f"SemanticValue {value.id} disconnected Function parameter"
                )
            elif value.kind == SemanticValueKind.LITERAL and not value.literal:
                errors.append(f"SemanticValue {value.id} literal is empty")
        for b in blocks:
            if len(b.parameter_types) != len(b.parameter_value_ids):
                errors.append(f"SemanticBlock {b.id} parameter value mismatch")
            for edge, target_id in enumerate(b.successor_ids):
                target = next((x for x in blocks if x.id == target_id), None)
                args = (
                    b.successor_arguments[edge]
                    if edge < len(b.successor_arguments)
                    else ()
                )
                if target and len(args) != len(target.parameter_value_ids):
                    errors.append(f"SemanticBlock {b.id} phi argument mismatch")
                for arg, param in zip(
                    args, target.parameter_value_ids if target else ()
                ):
                    if (
                        arg not in values
                        or param not in values
                        or values[arg].type_id != values[param].type_id
                    ):
                        errors.append(f"SemanticBlock {b.id} phi type mismatch")
                    uses += 1
            for oid in b.operation_ids:
                op = SemanticOperation.from_node(by_id[oid])
                if len(op.input_types) != len(op.input_value_ids) or len(
                    op.result_types
                ) != len(op.result_value_ids):
                    errors.append(f"SemanticOperation {op.id} typed arity lacks values")
                for position, (typ, vid) in enumerate(
                    zip(op.input_types, op.input_value_ids)
                ):
                    value = values.get(vid)
                    if value is None:
                        errors.append(
                            f"SemanticOperation {op.id} input value {vid} is missing"
                        )
                        continue
                    if value.type_id != typ:
                        errors.append(f"SemanticOperation {op.id} input type mismatch")
                    if (
                        typ == 12
                        and value.kind == SemanticValueKind.LITERAL
                        and value.literal == bytes(24)
                        and not value.source_id
                    ):
                        errors.append(f"SemanticOperation {op.id} required Ref is null")
                    if (
                        op.opcode == SemanticOpcode.ALLOCATE
                        and position in {1, 2, 3}
                        and value.kind == SemanticValueKind.LITERAL
                    ):
                        number = int.from_bytes(value.literal, "little")
                        if (position == 2 and not number) or (
                            position == 3 and (not number or number & (number - 1))
                        ):
                            errors.append(
                                f"SemanticOperation {op.id} allocation size/alignment is invalid"
                            )
                    if (
                        value.kind == SemanticValueKind.OP_RESULT
                        and op_position.get(value.owner_operation_id, (999, 999))
                        >= op_position.get(op.id, (999, 999))
                        and value.owner_block_id == b.id
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} uses value before definition"
                        )
                    uses += 1
                if op.callee_id:
                    status = values.get(op.status_value_id)
                    if (
                        b.terminator != TerminatorKind.BRANCH
                        or b.condition_value_id != op.status_value_id
                        or b.operation_ids[-1] != op.id
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} call status is unchecked"
                        )
                    if (
                        status is None
                        or status.type_id != 7
                        or status.owner_operation_id != op.id
                        or status.result_index
                        != len(
                            [
                                value
                                for value in op.result_value_ids
                                if value != op.status_value_id
                            ]
                        )
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} call status is missing or not uint32"
                        )
                elif op.status_value_id:
                    errors.append(
                        f"SemanticOperation {op.id} compiler-only status is unexpected"
                    )
                for index, (typ, vid) in enumerate(
                    zip(op.result_types, op.result_value_ids)
                ):
                    value = values.get(vid)
                    if (
                        value is None
                        or value.type_id != typ
                        or value.owner_operation_id != op.id
                        or value.result_index != index
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} result backref/type mismatch"
                        )
                operational = {
                    SemanticOpcode.RESOLVE_HANDLE,
                    SemanticOpcode.ALLOCATE,
                    SemanticOpcode.COPY,
                    SemanticOpcode.WRITE,
                    SemanticOpcode.READ,
                    SemanticOpcode.VALIDATE,
                    SemanticOpcode.PUBLISH,
                    SemanticOpcode.ROLLBACK,
                    SemanticOpcode.CLEANUP,
                    SemanticOpcode.UTF8_VALIDATE,
                    SemanticOpcode.FORMAT_INTEGER,
                    SemanticOpcode.APPEND_BYTES,
                    SemanticOpcode.SEARCH,
                    SemanticOpcode.HASH,
                    SemanticOpcode.LOOKUP,
                    SemanticOpcode.COMPARE,
                    SemanticOpcode.BORROW,
                    SemanticOpcode.RELEASE_BORROW,
                    SemanticOpcode.PIN,
                    SemanticOpcode.UNPIN,
                    SemanticOpcode.MOVE_SLOT,
                    SemanticOpcode.CALL_CALLBACK,
                }
                transaction_type = 41015
                for value_id in op.input_value_ids:
                    value = values.get(value_id)
                    if (
                        value is not None
                        and value.type_id == transaction_type
                        and value.kind == SemanticValueKind.LITERAL
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} transaction pointer input {value.id} is a persisted literal"
                        )
                if op.opcode in operational:
                    callee = by_id.get(op.callee_id)
                    if (
                        not op.callee_id
                        or callee is None
                        or callee.type_id != FUNCTION_TYPE
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} operational callee is missing"
                        )
                if op.opcode == SemanticOpcode.CALL_CALLBACK:
                    functions = [
                        values[v]
                        for v in op.input_value_ids
                        if values.get(v)
                        and values[v].kind == SemanticValueKind.FUNCTION
                    ]
                    if bool(op.callee_id) == bool(functions):
                        errors.append(
                            f"SemanticOperation {op.id} callback binding is ambiguous"
                        )
                    if functions and (
                        op.input_value_ids[0] != functions[0].id
                        or functions[0].type_id != FUNCTION_TYPE
                        or not functions[0].source_id
                    ):
                        errors.append(
                            f"SemanticOperation {op.id} callback metadata is incomplete"
                        )
                object_ops = {
                    SemanticOpcode.ALLOCATE,
                    SemanticOpcode.COPY,
                    SemanticOpcode.WRITE,
                    SemanticOpcode.READ,
                    SemanticOpcode.PUBLISH,
                    SemanticOpcode.ROLLBACK,
                    SemanticOpcode.CLEANUP,
                    SemanticOpcode.BORROW,
                    SemanticOpcode.RELEASE_BORROW,
                    SemanticOpcode.PIN,
                    SemanticOpcode.UNPIN,
                    SemanticOpcode.MOVE_SLOT,
                    SemanticOpcode.LOAD_FIELD,
                    SemanticOpcode.STORE_FIELD,
                }
                if op.opcode in object_ops and (
                    not op.target_object_id or op.target_object_id not in values
                ):
                    errors.append(
                        f"SemanticOperation {op.id} target object value is missing"
                    )
                if (
                    op.opcode in {SemanticOpcode.LOAD_FIELD, SemanticOpcode.STORE_FIELD}
                    and not op.target_field_id
                ):
                    errors.append(f"SemanticOperation {op.id} target Field is zero")
            if b.terminator == TerminatorKind.REFUSE and (
                not b.terminator_value_id
                or b.terminator_value_id not in values
                or values[b.terminator_value_id].type_id != 7
            ):
                errors.append(f"SemanticBlock {b.id} REFUSE requires uint32 status")
            for vid in (b.condition_value_id, b.terminator_value_id):
                if vid and vid not in values:
                    errors.append(f"SemanticBlock {b.id} terminator value is missing")
                uses += bool(vid)
        if any(
            v.kind == SemanticValueKind.OP_RESULT
            and v.id
            not in {
                x
                for b in blocks
                for oid in b.operation_ids
                for x in (
                    *SemanticOperation.from_node(by_id[oid]).result_value_ids,
                    SemanticOperation.from_node(by_id[oid]).status_value_id,
                )
            }
            for v in values.values()
        ):
            errors.append(f"SemanticGraph {graph.id} has disconnected result")
    return errors


# Compatibility names are intentionally removed from starter generation.
SemanticBody = SemanticGraph
verify_semantic_bodies = verify_semantic_graphs
