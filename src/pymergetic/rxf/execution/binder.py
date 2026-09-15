"""Deterministic, atomic native target binding and preflight diagnostics."""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pymergetic.rxf.execution.decode import (
    decode_code,
    decode_function,
    decode_import,
    decode_relocation,
)
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import (
    CallRole,
    CodeFormat,
    FunctionImplementation,
    ValueKind,
)
from pymergetic.rxf.model.target import (
    ABIObject,
    ArchitectureObject,
    FeatureObject,
    FeatureSetObject,
    RuntimeTargetObject,
)
from pymergetic.rxf.ty.builtins import (
    ARGUMENT_TYPE,
    CALL_TYPE,
    CODE_TYPE,
    FUNCTION_TYPE,
    IMPORT_TYPE,
    RUNTIME_TARGET_TYPE,
    TARGET_TYPE,
    VALUE_TYPE,
)


class RefusalCode(StrEnum):
    INVALID_ENTRY = "invalid_entry"
    INVALID_TARGET = "invalid_target"
    UNIMPLEMENTED = "unimplemented"
    INCOMPATIBLE = "incompatible"
    AMBIGUOUS = "ambiguous"
    MALFORMED = "malformed"
    MISSING_CAPABILITY = "missing_capability"


@dataclass(frozen=True)
class Diagnostic:
    code: RefusalCode
    object_id: int
    message: str
    related_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class Candidate:
    function_id: int
    code_id: int
    target_id: int
    compatible: bool
    rank: tuple[int, int, int] | None
    reason: str | None


@dataclass(frozen=True)
class BoundFunction:
    function_id: int
    target_id: int
    code_id: int
    rank: tuple[int, int, int]


@dataclass(frozen=True)
class BoundImport:
    import_id: int
    function_id: int
    capability: object


@dataclass(frozen=True)
class RelocationPatch:
    relocation_id: int
    code_id: int
    offset: int
    width: int
    target_id: int
    addend: int


@dataclass(frozen=True)
class BindingPlan:
    entry_function: int
    target_id: int
    functions: tuple[BoundFunction, ...]
    imports: tuple[BoundImport, ...]
    patches: tuple[RelocationPatch, ...]
    candidates: tuple[Candidate, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    @property
    def bindings(self) -> tuple[BoundFunction, ...]:
        return self.functions


Binding = BoundFunction


def _refs(node, role: CallRole) -> tuple[int, ...]:
    return tuple(r.target for r in node.refs if r.to_off == int(role))


def _target(container: Container, target_id: int):
    node = container.node_by_id(target_id)
    if node is None:
        raise ValueError(f"object {target_id} is not a RuntimeTarget")
    if node.type_id == TARGET_TYPE:
        if len(node.data) != 24:
            raise ValueError(f"legacy Target {target_id} payload is invalid")
        arch, env, abi, bits, endian, features = struct.unpack("<6I", node.data)
        return (
            arch,
            abi,
            env,
            bits,
            endian,
            frozenset(i for i in range(32) if features & (1 << i)),
        )
    if node.type_id != RUNTIME_TARGET_TYPE:
        raise ValueError(f"object {target_id} is not a RuntimeTarget")
    target = RuntimeTargetObject.from_node(node)
    arch_node = container.node_by_id(target.architecture_id)
    abi_node = container.node_by_id(target.abi_id)
    fs_node = container.node_by_id(target.feature_set_id)
    if arch_node is None or abi_node is None or fs_node is None:
        raise ValueError(f"RuntimeTarget {target_id} relationship is missing")
    arch = ArchitectureObject.from_node(arch_node)
    abi = ABIObject.from_node(abi_node)
    features = FeatureSetObject.from_node(fs_node)
    if abi.architecture_id != arch.id:
        raise ValueError(f"RuntimeTarget {target_id} ABI/Architecture mismatch")
    for feature_id in features.feature_ids:
        feature_node = container.node_by_id(feature_id)
        if (
            feature_node is None
            or FeatureObject.from_node(feature_node).architecture_id != arch.id
        ):
            raise ValueError(f"RuntimeTarget {target_id} Feature architecture mismatch")
    return (
        arch.id,
        abi.id,
        target.environment_id,
        arch.word_bits,
        int(arch.endianness),
        frozenset(features.feature_ids),
    )


def candidate_details(
    container: Container, function_id: int, target_id: int
) -> tuple[Candidate, ...]:
    try:
        active = _target(container, target_id)
    except ValueError as error:
        return (Candidate(function_id, 0, target_id, False, None, str(error)),)
    result = []
    for node in sorted(container.nodes, key=lambda n: n.id):
        if node.type_id != CODE_TYPE:
            continue
        try:
            code = decode_code(node)
        except ValueError as error:
            result.append(Candidate(function_id, node.id, 0, False, None, str(error)))
            continue
        if code.owner_function != function_id:
            continue
        if code.format != CodeFormat.NATIVE:
            result.append(
                Candidate(
                    function_id,
                    node.id,
                    code.target_id,
                    False,
                    None,
                    "format is not NATIVE",
                )
            )
            continue
        try:
            candidate = _target(container, code.target_id)
        except ValueError as error:
            result.append(
                Candidate(function_id, node.id, code.target_id, False, None, str(error))
            )
            continue
        if candidate[:5] != active[:5]:
            reason = "architecture, ABI, environment, width, or endianness mismatch"
            result.append(
                Candidate(function_id, node.id, code.target_id, False, None, reason)
            )
            continue
        if not candidate[5] <= active[5]:
            result.append(
                Candidate(
                    function_id,
                    node.id,
                    code.target_id,
                    False,
                    None,
                    "required feature set is not a subset of active features",
                )
            )
            continue
        exact = int(candidate[5] != active[5])
        rank = (exact, len(active[5] - candidate[5]), -len(candidate[5]))
        result.append(Candidate(function_id, node.id, code.target_id, True, rank, None))
    return tuple(result)


def candidates(
    container: Container, function_id: int, target_id: int
) -> tuple[BoundFunction, ...]:
    return tuple(
        BoundFunction(c.function_id, target_id, c.code_id, c.rank)
        for c in sorted(
            candidate_details(container, function_id, target_id),
            key=lambda c: (c.rank or (99, 99, 99), c.code_id),
        )
        if c.compatible and c.rank is not None
    )


def bind(container: Container, function_id: int, target_id: int) -> BoundFunction:
    function = container.node_by_id(function_id)
    if function is None or function.type_id != FUNCTION_TYPE:
        raise ValueError(f"object {function_id} is not a Function")
    ranked = candidates(container, function_id, target_id)
    if not ranked:
        raise ValueError(
            f"Function {function_id} has no compatible native Code for RuntimeTarget {target_id}"
        )
    best = tuple(c for c in ranked if c.rank == ranked[0].rank)
    if len(best) != 1:
        raise ValueError(
            f"Function {function_id} native Code selection is ambiguous: {[c.code_id for c in best]}"
        )
    return best[0]


def _call_functions(container: Container, call_id: int, visited: set[int]) -> set[int]:
    if call_id in visited:
        return set()
    visited.add(call_id)
    call = container.node_by_id(call_id)
    if call is None or call.type_id != CALL_TYPE:
        raise ValueError(f"Function body {call_id} is not a Call")
    if len(call.data) != 24:
        raise ValueError(f"Call {call.id} payload is invalid")
    callee, count, _ = struct.unpack("<3Q", call.data)
    callee_refs = _refs(call, CallRole.CALLEE)
    args = _refs(call, CallRole.ARGUMENT)
    if callee_refs != (callee,) or len(args) != count:
        raise ValueError(f"Call {call.id} payload/ref disagreement")
    found = {callee}
    for arg_id in args:
        arg = container.node_by_id(arg_id)
        if arg is None:
            raise ValueError(f"Call {call.id} Argument {arg_id} is missing")
        value_id = arg_id
        if arg.type_id == ARGUMENT_TYPE:
            if len(arg.data) != 16:
                raise ValueError(f"Argument {arg.id} payload is invalid")
            _, value_id = struct.unpack("<2Q", arg.data)
        value = container.node_by_id(value_id)
        if value is None:
            continue
        if value.type_id == FUNCTION_TYPE:
            found.add(value.id)
        elif value.type_id == CALL_TYPE:
            found.update(_call_functions(container, value.id, visited))
        elif value.type_id == VALUE_TYPE and len(value.data) >= 24:
            value_type, kind, reserved, size = struct.unpack("<QIIQ", value.data[:24])
            raw = value.data[24:]
            if reserved or len(raw) != size:
                raise ValueError(f"Value {value.id} payload is invalid")
            value_kind = ValueKind(kind)
            if (
                value_kind == ValueKind.OBJECT
                and value_type == FUNCTION_TYPE
                and size == 8
            ):
                found.add(int.from_bytes(raw, "little"))
            elif value_kind == ValueKind.RESULT and size == 8:
                found.update(
                    _call_functions(container, int.from_bytes(raw, "little"), visited)
                )
    return found


def reachable_terminal_functions(container: Container, entry_function: int) -> set[int]:
    terminals = set()
    visited = set()
    pending = [entry_function]
    while pending:
        function_id = pending.pop()
        if function_id in visited:
            continue
        visited.add(function_id)
        function = container.node_by_id(function_id)
        if function is None or function.type_id != FUNCTION_TYPE:
            raise ValueError(f"call target {function_id} is not a Function")
        record = decode_function(function)
        if record.implementation in (
            FunctionImplementation.CODE_BACKED,
            FunctionImplementation.IMPORTED,
            FunctionImplementation.DECLARED,
        ):
            terminals.add(function_id)
            continue
        if record.implementation == FunctionImplementation.COMPOSED:
            pending.extend(_call_functions(container, record.body_id, set()))
        elif record.implementation == FunctionImplementation.INTRINSIC:
            continue
        else:
            raise ValueError(f"Function {function_id} is not callable")
    return terminals


def preflight(
    container: Container,
    entry_function: int,
    target_id: int,
    capabilities: Mapping[int, object] | None = None,
) -> BindingPlan:
    capabilities = {} if capabilities is None else capabilities
    diagnostics = []
    bound = []
    imports = []
    patches = []
    all_candidates = []
    try:
        _target(container, target_id)
        terminals = reachable_terminal_functions(container, entry_function)
    except ValueError as error:
        return BindingPlan(
            entry_function,
            target_id,
            (),
            (),
            (),
            (),
            (Diagnostic(RefusalCode.INVALID_ENTRY, entry_function, str(error)),),
        )
    for function_id in sorted(terminals):
        function = container.node_by_id(function_id)
        assert function is not None
        record = decode_function(function)
        if record.implementation == FunctionImplementation.DECLARED:
            diagnostics.append(
                Diagnostic(
                    RefusalCode.UNIMPLEMENTED,
                    function_id,
                    "Function contract is DECLARED and has no native implementation",
                )
            )
            continue
        if record.implementation == FunctionImplementation.IMPORTED:
            import_nodes = [
                n
                for n in container.nodes
                if n.type_id == IMPORT_TYPE
                and decode_import(n).function_id == function_id
            ]
            if len(import_nodes) != 1:
                diagnostics.append(
                    Diagnostic(
                        RefusalCode.MALFORMED,
                        function_id,
                        "imported Function requires exactly one Import",
                    )
                )
                continue
            imp = decode_import(import_nodes[0])
            capability = capabilities.get(function_id)
            if capability is None and not imp.optional:
                diagnostics.append(
                    Diagnostic(
                        RefusalCode.MISSING_CAPABILITY,
                        import_nodes[0].id,
                        "mandatory capability is absent",
                        (function_id,),
                    )
                )
                continue
            if capability is not None:
                imports.append(BoundImport(import_nodes[0].id, function_id, capability))
            continue
        details = candidate_details(container, function_id, target_id)
        all_candidates.extend(details)
        try:
            selected = bind(container, function_id, target_id)
            bound.append(selected)
        except ValueError as error:
            diagnostics.append(
                Diagnostic(
                    RefusalCode.AMBIGUOUS
                    if "ambiguous" in str(error)
                    else RefusalCode.INCOMPATIBLE,
                    function_id,
                    str(error),
                    tuple(c.code_id for c in details),
                )
            )
    if not diagnostics:
        for selected in bound:
            code_node = container.node_by_id(selected.code_id)
            assert code_node is not None
            for relocation_id in _refs(code_node, CallRole.RELOCATION):
                relocation_node = container.node_by_id(relocation_id)
                assert relocation_node is not None
                relocation = decode_relocation(relocation_node)
                patches.append(
                    RelocationPatch(
                        relocation_id,
                        selected.code_id,
                        relocation.offset,
                        relocation.patch_width,
                        relocation.target_id,
                        relocation.addend,
                    )
                )
    return BindingPlan(
        entry_function,
        target_id,
        tuple(bound) if not diagnostics else (),
        tuple(imports) if not diagnostics else (),
        tuple(patches) if not diagnostics else (),
        tuple(all_candidates),
        tuple(diagnostics),
    )
