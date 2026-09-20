"""Deterministic normalization of composed Function/Call/Argument/Value graphs."""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from pymergetic.rxf.execution.decode import decode_function, decode_signature
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import CallRole, FunctionImplementation, ValueKind
from pymergetic.rxf.ty.builtins import (
    ARGUMENT_TYPE,
    CALL_TYPE,
    FUNCTION_TYPE,
    PARAMETER_TYPE,
    SIGNATURE_TYPE,
    VALUE_TYPE,
)


class CompileError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedValue:
    id: int
    type_id: int
    kind: ValueKind
    source_id: int
    literal: bytes = b""


@dataclass(frozen=True)
class NormalizedCall:
    id: int
    function_id: int
    result_type: int
    arguments: tuple[NormalizedValue, ...]


@dataclass(frozen=True)
class NormalizedFunction:
    id: int
    signature_id: int
    return_type: int
    parameter_ids: tuple[int, ...]
    calls: tuple[NormalizedCall, ...]
    terminal_call_id: int
    source_ids: tuple[int, ...]
    digest: bytes


def _refs(node, role: CallRole) -> tuple[int, ...]:
    return tuple(r.target for r in node.refs if r.to_off == int(role))


def _value(container: Container, value_id: int) -> NormalizedValue:
    node = container.node_by_id(value_id)
    if node is None or node.type_id != VALUE_TYPE or len(node.data) < 24:
        raise CompileError(f"Value {value_id} is missing or malformed")
    type_id, kind_raw, reserved, size = struct.unpack("<QIIQ", node.data[:24])
    raw = node.data[24:]
    if reserved or len(raw) != size:
        raise CompileError(f"Value {value_id} has invalid extent")
    try:
        kind = ValueKind(kind_raw)
    except ValueError as error:
        raise CompileError(f"Value {value_id} has invalid kind") from error
    if kind in (ValueKind.PARAMETER, ValueKind.RESULT, ValueKind.OBJECT):
        if size != 8:
            raise CompileError(f"Value {value_id} reference must be uint64")
        source = int.from_bytes(raw, "little")
        return NormalizedValue(value_id, type_id, kind, source)
    return NormalizedValue(value_id, type_id, kind, 0, raw)


def normalize(container: Container, function_id: int) -> NormalizedFunction:
    by_id = {n.id: n for n in container.nodes}
    function = by_id.get(function_id)
    if function is None or function.type_id != FUNCTION_TYPE:
        raise CompileError(f"object {function_id} is not a Function")
    record = decode_function(function)
    if record.implementation != FunctionImplementation.COMPOSED:
        raise CompileError(f"Function {function_id} is not composed")
    signature = by_id.get(record.signature_id)
    if signature is None or signature.type_id != SIGNATURE_TYPE:
        raise CompileError(f"Function {function_id} Signature is missing")
    sig = decode_signature(signature)
    parameter_ids = _refs(signature, CallRole.PARAMETER)
    if len(parameter_ids) != sig.parameter_count:
        raise CompileError(f"Signature {signature.id} parameter count disagreement")
    parameter_types: dict[int, int] = {}
    for index, parameter_id in enumerate(parameter_ids):
        p = by_id.get(parameter_id)
        if p is None or p.type_id != PARAMETER_TYPE or len(p.data) != 24:
            raise CompileError(f"Parameter {parameter_id} is malformed")
        value_type, actual_index, lazy, reserved = struct.unpack("<2Q2I", p.data)
        if reserved or lazy or actual_index != index:
            raise CompileError(
                f"Parameter {parameter_id} is not an eager canonical parameter"
            )
        parameter_types[parameter_id] = value_type
    visiting: set[int] = set()
    done: set[int] = set()
    ordered: list[NormalizedCall] = []
    source_ids = {function.id, signature.id, *parameter_ids}

    def visit(call_id: int) -> None:
        if call_id in visiting:
            raise CompileError(f"call dependency cycle at {call_id}")
        if call_id in done:
            return
        call = by_id.get(call_id)
        if call is None or call.type_id != CALL_TYPE or len(call.data) != 24:
            raise CompileError(f"Call {call_id} is missing or malformed")
        visiting.add(call_id)
        source_ids.add(call_id)
        callee, count, result_type = struct.unpack("<3Q", call.data)
        if _refs(call, CallRole.CALLEE) != (callee,):
            raise CompileError(f"Call {call_id} callee disagreement")
        callee_node = by_id.get(callee)
        if callee_node is None or callee_node.type_id != FUNCTION_TYPE:
            raise CompileError(f"Call {call_id} callee is not Function")
        callee_record = decode_function(callee_node)
        callee_sig_node = by_id.get(callee_record.signature_id)
        if callee_sig_node is None:
            raise CompileError(f"Call {call_id} callee Signature missing")
        callee_sig = decode_signature(callee_sig_node)
        callee_params = _refs(callee_sig_node, CallRole.PARAMETER)
        arg_ids = _refs(call, CallRole.ARGUMENT)
        if len(arg_ids) != count or count != len(callee_params):
            raise CompileError(f"Call {call_id} argument count mismatch")
        args = []
        for position, arg_id in enumerate(arg_ids):
            arg = by_id.get(arg_id)
            if arg is None or arg.type_id != ARGUMENT_TYPE or len(arg.data) != 16:
                raise CompileError(f"Argument {arg_id} is malformed")
            parameter_id, value_id = struct.unpack("<2Q", arg.data)
            source_ids.add(arg_id)
            if parameter_id != callee_params[position]:
                raise CompileError(f"Argument {arg_id} parameter/order mismatch")
            value = _value(container, value_id)
            source_ids.add(value_id)
            pnode = by_id[parameter_id]
            expected_type = struct.unpack_from("<Q", pnode.data)[0]
            if value.type_id != expected_type:
                raise CompileError(f"Argument {arg_id} type mismatch")
            if value.kind == ValueKind.RESULT:
                visit(value.source_id)
            elif (
                value.kind == ValueKind.PARAMETER
                and value.source_id not in parameter_types
            ):
                raise CompileError(f"Value {value.id} references foreign Parameter")
            args.append(value)
        if result_type != callee_sig.return_type:
            raise CompileError(f"Call {call_id} result type mismatch")
        ordered.append(NormalizedCall(call_id, callee, result_type, tuple(args)))
        visiting.remove(call_id)
        done.add(call_id)

    visit(record.body_id)
    canonical = bytearray(
        struct.pack("<3Q", function_id, record.signature_id, record.body_id)
    )
    for call in ordered:
        canonical += struct.pack("<3Q", call.id, call.function_id, call.result_type)
        for value in call.arguments:
            canonical += (
                struct.pack(
                    "<4Q", value.id, value.type_id, int(value.kind), value.source_id
                )
                + value.literal
            )
    return NormalizedFunction(
        function_id,
        record.signature_id,
        sig.return_type,
        parameter_ids,
        tuple(ordered),
        record.body_id,
        tuple(sorted(source_ids)),
        hashlib.sha256(canonical).digest(),
    )
