"""Generic lowering from persisted SemanticGraph SSA to target-independent native plans."""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass

from pymergetic.rxf.compiler.ir import TypeRef, VirtualValue
from pymergetic.rxf.compiler.normalize import CompileError
from pymergetic.rxf.compiler.semantic import (
    SemanticIRFunction,
    SemanticIROperation,
    SemanticValueKind,
    decode_strict_abi,
)
from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.contracts import ABIRole, ABIValueLocation
from pymergetic.rxf.model.stdlib import (
    CanonicalOrderAuthority,
    CanonicalOrderPolicy,
    HashAuthority,
    RefusalDefaultPolicy,
    RefusalTransformRole,
    TaggedVariant,
)
from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode, TerminatorKind
from pymergetic.rxf.ty.builtins import BOOL_TYPE, FIELD_TYPE, U32_TYPE
from pymergetic.rxf.ty.objects import FieldObject, TypeObject


@dataclass(frozen=True)
class TargetCallABI:
    x86_64: tuple[ABIValueLocation, ...]
    aarch64: tuple[ABIValueLocation, ...]


@dataclass(frozen=True)
class LoweredSemanticOperation:
    source: SemanticIROperation
    call_abi: TargetCallABI | None


@dataclass(frozen=True)
class RefusalEnrichmentPlan:
    operation_id: int
    authority_id: int
    source_status: int
    destination_status: int
    payload_offsets: tuple[int, ...]
    preserve_identity: bool


@dataclass(frozen=True)
class RefusalMappingPlan:
    operation_id: int
    authority_id: int
    entries: tuple[tuple[int, int], ...]
    payload_offset: int
    default_policy: RefusalDefaultPolicy


@dataclass(frozen=True)
class SpecializationBinding:
    value_id: int
    type_id: int
    literal: bytes


@dataclass(frozen=True)
class OptimizationReport:
    blocks_before: int
    blocks_after: int
    operations_before: int
    operations_after: int
    folded_operations: tuple[int, ...] = ()
    removed_operations: tuple[int, ...] = ()
    removed_blocks: tuple[int, ...] = ()
    removed_values: int = 0
    retained_effects: int = 0


@dataclass(frozen=True)
class SemanticProgram:
    function_id: int
    signature_id: int
    parameters: tuple[VirtualValue, ...]
    result_type: TypeRef
    graph: SemanticIRFunction
    operations: tuple[LoweredSemanticOperation, ...]
    x86_entry_abi: tuple[ABIValueLocation, ...]
    arm_entry_abi: tuple[ABIValueLocation, ...]
    semantic_digest: bytes
    binding_function_ids: tuple[int, ...] = ()
    binding_object_ids: tuple[int, ...] = ()
    fields: tuple[FieldObject, ...] = ()
    canonical_orders: tuple[CanonicalOrderAuthority, ...] = ()
    tagged_variants: tuple[TaggedVariant, ...] = ()
    refusal_mappings: tuple[RefusalMappingPlan, ...] = ()
    refusal_enrichments: tuple[RefusalEnrichmentPlan, ...] = ()
    specialization_bindings: tuple[SpecializationBinding, ...] = ()
    optimization_report: OptimizationReport | None = None

    def operation(self, operation_id: int) -> LoweredSemanticOperation:
        found = tuple(op for op in self.operations if op.source.id == operation_id)
        if len(found) != 1:
            raise CompileError(
                f"Function {self.function_id} operation {operation_id} lowering is missing"
            )
        return found[0]


Handler = Callable[[Container, SemanticIRFunction, SemanticIROperation], None]
_HANDLERS: dict[SemanticOpcode, Handler] = {}


def handles(*opcodes: SemanticOpcode):
    def register(handler: Handler) -> Handler:
        for opcode in opcodes:
            if opcode in _HANDLERS:
                raise RuntimeError(f"duplicate semantic handler {opcode.name}")
            _HANDLERS[opcode] = handler
        return handler

    return register


@handles(*tuple(SemanticOpcode))
def _typed_operation(
    container: Container, graph: SemanticIRFunction, op: SemanticIROperation
) -> None:
    """Validate persisted authorities shared by all opcode-specific lowering."""
    if op.target_field_id:
        node = container.node_by_id(op.target_field_id)
        if node is None or node.type_id != FIELD_TYPE:
            raise CompileError(
                f"Function {graph.function_id} SemanticOperation {op.id} field {op.target_field_id} is unresolved"
            )
        try:
            field = FieldObject.from_payload(
                id=node.id, name=node.name, payload=node.data
            )
        except ValueError as error:
            raise CompileError(
                f"SemanticOperation {op.id} field {op.target_field_id} is malformed: {error}"
            ) from error
        owner = container.node_by_id(field.owner_type)
        if owner is None:
            raise CompileError(
                f"SemanticOperation {op.id} field {field.id} owner Type {field.owner_type} is unresolved"
            )
        descriptor = TypeObject.from_payload(
            id=owner.id, name=owner.name, payload=owner.data
        )
        if field.offset >= descriptor.size:
            raise CompileError(
                f"SemanticOperation {op.id} field {field.id} offset {field.offset} exceeds Type {field.owner_type}"
            )
    if (
        op.opcode in {SemanticOpcode.MAP_REFUSAL, SemanticOpcode.ENRICH_REFUSAL}
        and not op.transform_authority_id
    ):
        raise CompileError(
            f"SemanticOperation {op.id} {op.opcode.name} has no transform authority"
        )
    if op.opcode == SemanticOpcode.LOOKUP and not op.transform_authority_id:
        raise CompileError(f"SemanticOperation {op.id} LOOKUP has no hash authority")
    if (
        op.opcode in {SemanticOpcode.HASH, SemanticOpcode.LOOKUP}
        and op.transform_authority_id
    ):
        node = container.node_by_id(op.transform_authority_id)
        if node is None:
            raise CompileError(
                f"SemanticOperation {op.id} hash authority {op.transform_authority_id} is unresolved"
            )
        try:
            authority = HashAuthority.from_node(node)
        except (TypeError, ValueError) as error:
            raise CompileError(
                f"SemanticOperation {op.id} hash authority {op.transform_authority_id} is malformed: {error}"
            ) from error
        payload = tuple(value for value in op.results if value != op.status)
        if op.opcode == SemanticOpcode.HASH:
            valid = (
                op.callee_id == authority.hash_function
                and len(op.inputs) == 2
                and op.inputs[0].type.id == authority.input_type
                and op.inputs[1].type.id == authority.output_type
                and len(payload) == 1
                and payload[0].type.id == authority.output_type
            )
        else:
            valid = (
                op.callee_id
                in {authority.map_lookup_function, authority.set_lookup_function}
                and len(op.inputs) == 4
                and op.inputs[2].type.id == authority.output_type
                and op.inputs[3].type.id == authority.input_type
                and len(payload) == 1
                and payload[0].type.id
                == (
                    0xDD_D6E5_05AA_259D_9A
                    if op.callee_id == authority.map_lookup_function
                    else 0xDD_D6E5_05AA_259D_99
                )
            )
        if not valid:
            raise CompileError(
                f"Function {graph.function_id} SemanticOperation {op.id} {op.opcode.name} "
                f"does not match HashAuthority {authority.id}"
            )
    if op.opcode == SemanticOpcode.CALL_CALLBACK and not (
        op.callee_id
        or any(value.kind == SemanticValueKind.FUNCTION for value in op.inputs)
    ):
        raise CompileError(f"SemanticOperation {op.id} callback binding is unresolved")


def lower_semantic_program(
    container: Container, graph: SemanticIRFunction
) -> SemanticProgram:
    """Lower one normalized graph without consulting Function names."""
    function = container.node_by_id(graph.function_id)
    if function is None:
        raise CompileError(f"Function {graph.function_id} is missing")
    lowered = []
    functions = set()
    objects = set()
    fields: dict[int, FieldObject] = {}
    canonical_orders: dict[int, CanonicalOrderAuthority] = {}
    tagged_variants: dict[int, TaggedVariant] = {}
    refusal_mappings: dict[int, RefusalMappingPlan] = {}
    refusal_enrichments: dict[int, RefusalEnrichmentPlan] = {}
    for block in graph.blocks:
        if block.terminator == TerminatorKind.BRANCH:
            condition = block.condition
            if condition is None or condition.type.id not in {BOOL_TYPE, U32_TYPE}:
                value_id = 0 if condition is None else condition.id
                raise CompileError(
                    f"Function {graph.function_id} block {block.id} condition value {value_id} is not bool/status"
                )
            if condition.type.id == U32_TYPE:
                owner = next(
                    (op for op in block.operations if op.status == condition), None
                )
                if (
                    owner is None
                    or not block.operations
                    or block.operations[-1] != owner
                ):
                    raise CompileError(
                        f"Function {graph.function_id} block {block.id} status value {condition.id} lacks immediate call provenance"
                    )
        for op in block.operations:
            handler = _HANDLERS.get(op.opcode)
            if handler is None:
                raise CompileError(
                    f"Function {graph.function_id} SemanticOperation {op.id} unsupported opcode {op.opcode.name}"
                )
            handler(container, graph, op)
            if op.opcode == SemanticOpcode.ENRICH_REFUSAL:
                authority = container.node_by_id(op.transform_authority_id)
                if authority is None or len(authority.data) != 16:
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal enrichment {op.transform_authority_id} is unresolved or malformed"
                    )
                preserve, field_count, context_count, reserved = struct.unpack(
                    "<4I", authority.data
                )
                roles = {
                    role: tuple(
                        ref.target for ref in authority.refs if ref.to_off == int(role)
                    )
                    for role in RefusalTransformRole
                }
                if (
                    preserve != 1
                    or reserved
                    or context_count
                    or roles[RefusalTransformRole.CONTEXT_VALUE]
                    or roles[RefusalTransformRole.PAYLOAD_CONSTRUCTOR]
                    or len(roles[RefusalTransformRole.SOURCE_VARIANT]) != 1
                    or len(roles[RefusalTransformRole.DESTINATION_VARIANT]) != 1
                    or len(roles[RefusalTransformRole.PAYLOAD_FIELD]) != field_count
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal enrichment {authority.id} unsupported layout/transform"
                    )
                variants = []
                for role in (
                    RefusalTransformRole.SOURCE_VARIANT,
                    RefusalTransformRole.DESTINATION_VARIANT,
                ):
                    variant_id = roles[role][0]
                    variant = container.node_by_id(variant_id)
                    if variant is None or len(variant.data) != 8:
                        raise CompileError(
                            f"SemanticOperation {op.id} refusal variant {variant_id} is unresolved or malformed"
                        )
                    variants.append(struct.unpack("<Q", variant.data)[0])
                offsets = []
                for field_id in roles[RefusalTransformRole.PAYLOAD_FIELD]:
                    field_node = container.node_by_id(field_id)
                    if field_node is None:
                        raise CompileError(
                            f"SemanticOperation {op.id} enrichment field {field_id} is unresolved"
                        )
                    field = FieldObject.from_payload(
                        id=field_node.id, name=field_node.name, payload=field_node.data
                    )
                    if (
                        field.owner_type != op.inputs[0].type.id
                        or field.value_type != U32_TYPE
                    ):
                        raise CompileError(
                            f"SemanticOperation {op.id} enrichment field {field_id} type mismatch"
                        )
                    fields[field.id] = field
                    offsets.append(field.offset)
                if (
                    len(op.inputs) != 1
                    or len(op.results) != 1
                    or op.inputs[0].type != op.results[0].type
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal enrichment input/output type mismatch"
                    )
                refusal_enrichments[op.id] = RefusalEnrichmentPlan(
                    op.id, authority.id, variants[0], variants[1], tuple(offsets), True
                )
            if op.opcode == SemanticOpcode.MAP_REFUSAL:
                authority_node = container.node_by_id(op.transform_authority_id)
                if authority_node is None or len(authority_node.data) != 16:
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal mapping {op.transform_authority_id} is unresolved or malformed"
                    )
                policy_value, entry_count, reserved0, reserved1 = struct.unpack(
                    "<4I", authority_node.data
                )
                try:
                    policy = RefusalDefaultPolicy(policy_value)
                except ValueError as error:
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal mapping {authority_node.id} default policy is invalid"
                    ) from error
                if (
                    policy != RefusalDefaultPolicy.PROPAGATE_UNMATCHED
                    or reserved0
                    or reserved1
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal mapping {authority_node.id} unsupported default policy/layout"
                    )
                entry_ids = tuple(
                    ref.target
                    for ref in authority_node.refs
                    if ref.to_off == int(RefusalTransformRole.ENTRY)
                )
                if len(entry_ids) != entry_count:
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal mapping {authority_node.id} entry count mismatch"
                    )
                entries = []
                sources = set()
                for entry_id in entry_ids:
                    entry = container.node_by_id(entry_id)
                    if entry is None or len(entry.data) != 16:
                        raise CompileError(
                            f"SemanticOperation {op.id} refusal mapping entry {entry_id} is unresolved or malformed"
                        )
                    source, destination, reserved = struct.unpack("<IIQ", entry.data)
                    if source in sources:
                        raise CompileError(
                            f"SemanticOperation {op.id} refusal mapping {authority_node.id} duplicate source status {source}"
                        )
                    sources.add(source)
                    transforms = tuple(
                        ref
                        for ref in entry.refs
                        if ref.to_off == int(RefusalTransformRole.PAYLOAD_TRANSFORM)
                    )
                    variants = tuple(
                        ref.target
                        for ref in entry.refs
                        if ref.to_off
                        in {
                            int(RefusalTransformRole.SOURCE_VARIANT),
                            int(RefusalTransformRole.DESTINATION_VARIANT),
                        }
                    )
                    if reserved or transforms or len(variants) != 2:
                        raise CompileError(
                            f"SemanticOperation {op.id} refusal mapping entry {entry_id} variant/payload transform is unsupported"
                        )
                    variant_statuses = []
                    for variant_id in variants:
                        variant = container.node_by_id(variant_id)
                        if variant is None or len(variant.data) != 8:
                            raise CompileError(
                                f"SemanticOperation {op.id} refusal variant {variant_id} is unresolved or malformed"
                            )
                        variant_statuses.append(struct.unpack("<Q", variant.data)[0])
                    if variant_statuses != [source, destination]:
                        raise CompileError(
                            f"SemanticOperation {op.id} refusal mapping entry {entry_id} variant/status mismatch"
                        )
                    entries.append((source, destination))
                field = next(
                    (
                        field
                        for field in fields.values()
                        if field.id == op.target_field_id
                    ),
                    None,
                )
                if field is None:
                    field_node = container.node_by_id(op.target_field_id)
                    if field_node is None:
                        raise CompileError(
                            f"SemanticOperation {op.id} payload field {op.target_field_id} is unresolved"
                        )
                    field = FieldObject.from_payload(
                        id=field_node.id, name=field_node.name, payload=field_node.data
                    )
                    fields[field.id] = field
                if (
                    len(op.inputs) != 1
                    or len(op.results) != 1
                    or op.inputs[0].type != op.results[0].type
                    or field.owner_type != op.inputs[0].type.id
                    or field.value_type != U32_TYPE
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} refusal mapping {authority_node.id} payload field/type mismatch"
                    )
                refusal_mappings[op.id] = RefusalMappingPlan(
                    op.id, authority_node.id, tuple(entries), field.offset, policy
                )
            if op.opcode == SemanticOpcode.CANONICAL_ORDER:
                node = container.node_by_id(op.transform_authority_id)
                if node is None:
                    raise CompileError(
                        f"SemanticOperation {op.id} CANONICAL_ORDER authority "
                        f"{op.transform_authority_id} is unresolved"
                    )
                try:
                    authority = CanonicalOrderAuthority.from_node(node)
                except ValueError as error:
                    raise CompileError(
                        f"SemanticOperation {op.id} CANONICAL_ORDER authority "
                        f"{op.transform_authority_id} is malformed: {error}"
                    ) from error
                if (
                    authority.policy != CanonicalOrderPolicy.ASCENDING_SLOT_INDEX
                    or not authority.representation_preserving
                    or len(op.inputs) != 1
                    or len(op.results) != 1
                    or op.inputs[0].type.id != authority.input_type
                    or op.results[0].type.id != authority.output_type
                    or op.inputs[0].type != op.results[0].type
                ):
                    raise CompileError(
                        f"SemanticOperation {op.id} CANONICAL_ORDER authority "
                        f"{authority.id} policy/layout/type is unsupported"
                    )
                canonical_orders[authority.id] = authority
            if op.opcode in {
                SemanticOpcode.CONSTRUCT_OPTION,
                SemanticOpcode.CONSTRUCT_RESULT,
            }:
                matches = []
                for candidate in container.nodes:
                    try:
                        variant = TaggedVariant.from_node(candidate)
                    except (ValueError, TypeError):
                        continue
                    if variant.payload_field_id == op.target_field_id:
                        matches.append(variant)
                if len(matches) != 1:
                    raise CompileError(
                        f"SemanticOperation {op.id} tagged field {op.target_field_id} "
                        f"has {len(matches)} variant authorities"
                    )
                tagged_variants[matches[0].id] = matches[0]
            if op.target_field_id:
                node = container.node_by_id(op.target_field_id)
                assert node is not None
                fields[op.target_field_id] = FieldObject.from_payload(
                    id=node.id, name=node.name, payload=node.data
                )
            abi = None
            if op.callee_id:
                _, x86 = decode_strict_abi(container, op.callee_id, 1)
                _, arm = decode_strict_abi(container, op.callee_id, 2)
                for architecture, locations in (("SYSV_X86_64", x86), ("AAPCS64", arm)):
                    outputs = tuple(
                        location
                        for location in locations
                        if location.role in {ABIRole.TRANSIENT_OUTPUT, ABIRole.SRET}
                    )
                    results = tuple(value for value in op.results if value != op.status)
                    if len(outputs) != len(results):
                        raise CompileError(
                            f"Function {graph.function_id} SemanticGraph {graph.graph_id} "
                            f"SemanticOperation {op.id} {op.opcode.name} callee {op.callee_id} "
                            f"{architecture} defines result values {[value.id for value in results]} "
                            f"but ABI output locations {[location.id for location in outputs]}"
                        )
                abi = TargetCallABI(x86, arm)
                functions.add(op.callee_id)
            lowered.append(LoweredSemanticOperation(op, abi))
            objects.update(
                value.source_id
                for value in (*op.inputs, *op.results)
                if value.kind == SemanticValueKind.OBJECT
            )
    _, xentry = decode_strict_abi(container, graph.function_id, 1)
    _, aentry = decode_strict_abi(container, graph.function_id, 2)
    parameters = tuple(
        VirtualValue(value.id, value.type, value.source_id, graph.entry_block_id)
        for value in graph.parameters
    )
    return SemanticProgram(
        graph.function_id,
        decode_function(function).signature_id,
        parameters,
        graph.output_type,
        graph,
        tuple(lowered),
        xentry,
        aentry,
        graph.semantic_digest,
        tuple(sorted(functions)),
        tuple(sorted(objects)),
        tuple(fields[key] for key in sorted(fields)),
        tuple(canonical_orders[key] for key in sorted(canonical_orders)),
        tuple(tagged_variants[key] for key in sorted(tagged_variants)),
        tuple(refusal_mappings[key] for key in sorted(refusal_mappings)),
        tuple(refusal_enrichments[key] for key in sorted(refusal_enrichments)),
    )
