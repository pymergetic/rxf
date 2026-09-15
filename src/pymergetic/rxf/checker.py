"""Semantic coherence checks for self-describing callable RXF graphs."""

from pymergetic.rxf.execution.decode import (
    decode_code,
    decode_function,
    decode_import,
    decode_relocation,
    decode_signature,
)
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import (
    CallRole,
    FunctionImplementation,
    semantic_digest,
)
from pymergetic.rxf.model.generics import (
    GenericArgument,
    GenericParameter,
    GenericRole,
    Specialization,
    specialization_digest,
    specialization_id,
)
from pymergetic.rxf.model.generics import (
    Template as GenericTemplate,
)
from pymergetic.rxf.model.module import ModuleObject, derived_fqns
from pymergetic.rxf.model.target import (
    ABIObject,
    ArchitectureObject,
    EnvironmentObject,
    FeatureObject,
    FeatureSetObject,
    RuntimeTargetObject,
)
from pymergetic.rxf.model.traits import (
    AssociatedType,
    Conformance,
    ImplementationBinding,
    ImplementationBindingKind,
    Trait,
    TraitRequirement,
    TraitRole,
    function_is_callable,
)
from pymergetic.rxf.ops.lowering import lower
from pymergetic.rxf.schema import NODE_INVALID, NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    ABI_TYPE,
    ARCHITECTURE_TYPE,
    ARGUMENT_TYPE,
    ASSOCIATED_TYPE_TYPE,
    CALL_TYPE,
    CFG_BLOCK_TYPE,
    CFG_OP_TYPE,
    CODE_TYPE,
    CONFORMANCE_TYPE,
    ENVIRONMENT_TYPE,
    FEATURE_SET_TYPE,
    FEATURE_TYPE,
    FIELD_TYPE,
    FUNCTION_TYPE,
    GENERIC_ARGUMENT_TYPE,
    GENERIC_PARAMETER_TYPE,
    IMPLEMENTATION_BINDING_TYPE,
    IMPORT_TYPE,
    MODULE_TYPE,
    PARAMETER_TYPE,
    RELOCATION_TYPE,
    RUNTIME_TARGET_TYPE,
    SIGNATURE_TYPE,
    SPECIALIZATION_TYPE,
    TARGET_TYPE,
    TEMPLATE_TYPE,
    TRAIT_REQUIREMENT_TYPE,
    TRAIT_TYPE,
    TYPE_TYPE,
    VALUE_TYPE,
)
from pymergetic.rxf.ty.table import TypeTable


def _targets(node, role: CallRole) -> list[int]:
    return [ref.target for ref in node.refs if ref.to_off == int(role)]


def _role_targets(node, role: int) -> tuple[int, ...]:
    return tuple(ref.target for ref in node.refs if ref.to_off == role)


def _signature_shape(signature, by_id) -> tuple[int, tuple[int, ...]] | None:
    import struct

    try:
        record = decode_signature(signature)
    except ValueError:
        return None
    parameters = _role_targets(signature, int(CallRole.PARAMETER))
    if len(parameters) != record.parameter_count:
        return None
    value_types = []
    for parameter_id in parameters:
        parameter = by_id.get(parameter_id)
        if (
            parameter is None
            or parameter.type_id != PARAMETER_TYPE
            or len(parameter.data) != 24
        ):
            return None
        value_type, index, _lazy, reserved = struct.unpack("<2Q2I", parameter.data)
        if reserved or index != len(value_types):
            return None
        value_types.append(value_type)
    return record.return_type, tuple(value_types)


def check(container: Container) -> list[str]:
    errors: list[str] = []
    by_id = {}
    for node in container.nodes:
        if node.id in by_id:
            errors.append(f"duplicate node id {node.id}")
        by_id[node.id] = node
    try:
        type_table = TypeTable.from_container(container)
    except (KeyError, TypeError, ValueError) as error:
        errors.append(str(error))
        type_table = TypeTable()
    if TYPE_TYPE not in type_table.types:
        errors.append("bootstrap Type metatype is missing")
    elif by_id[TYPE_TYPE].type_id != TYPE_TYPE:
        errors.append("bootstrap Type metatype must type itself")
    if FIELD_TYPE not in type_table.types:
        errors.append("bootstrap Field type is missing")

    if container.header.entry_node:
        entry = by_id.get(container.header.entry_node)
        if entry is None:
            errors.append(
                f"header entry object {container.header.entry_node} is missing"
            )
        elif entry.type_id == FUNCTION_TYPE:
            try:
                if not function_is_callable(container, entry):
                    errors.append(f"header entry Function {entry.id} is not executable")
            except ValueError as error:
                errors.append(f"header entry Function {entry.id}: {error}")

    roots = [node for node in container.nodes if node.kind == NodeKind.ROOT]
    if len(roots) != 1:
        errors.append(f"expected exactly one ROOT node, found {len(roots)}")
    root = roots[0] if len(roots) == 1 else None
    if root is not None and root.id != 0:
        errors.append(f"ROOT node must have canonical id 0, found {root.id}")
    if root is not None and root.parent != NODE_INVALID:
        errors.append("ROOT node parent must be NODE_INVALID")

    valid_parent = {}
    for node in container.nodes:
        if node.kind != NodeKind.ROOT:
            if node.parent == NODE_INVALID:
                errors.append(f"node {node.id}: non-root parent is NODE_INVALID")
            elif node.parent == node.id:
                errors.append(f"node {node.id}: node cannot be its own parent")
            elif node.parent not in by_id:
                errors.append(f"node {node.id}: parent {node.parent} is missing")
            else:
                valid_parent[node.id] = node.parent

    if root is not None:
        reachable = {root.id}
        changed = True
        while changed:
            changed = False
            for node_id, parent_id in valid_parent.items():
                if parent_id in reachable and node_id not in reachable:
                    reachable.add(node_id)
                    changed = True
        for node in container.nodes:
            if node.id not in reachable and not (
                node.kind != NodeKind.ROOT
                and (node.parent == NODE_INVALID or node.parent not in by_id)
            ):
                errors.append(
                    f"node {node.id}: not reachable from ROOT via parent links"
                )

    for node in container.nodes:
        if node.type_id and node.type_id not in type_table.types:
            errors.append(f"node {node.id}: type object {node.type_id} is missing")
        if node.kind == NodeKind.TYPE and node.type_id != TYPE_TYPE:
            errors.append(f"TYPE object {node.id} must be typed by Type")
        if node.kind == NodeKind.FIELD and node.type_id != FIELD_TYPE:
            errors.append(f"FIELD object {node.id} must be typed by Field")
        if node.kind == NodeKind.MODULE:
            if node.type_id != MODULE_TYPE:
                errors.append(f"MODULE object {node.id} must be typed by Module")
            parent = by_id.get(node.parent)
            if parent is not None and parent.kind not in (
                NodeKind.ROOT,
                NodeKind.MODULE,
            ):
                errors.append(f"MODULE object {node.id} parent must be ROOT or MODULE")
            try:
                ModuleObject.from_payload(
                    id=node.id, name=node.name, parent=node.parent, payload=node.data
                )
            except ValueError as error:
                errors.append(str(error))
        if node.kind != NodeKind.ROOT and (not node.name or "." in node.name):
            errors.append(f"node {node.id}: name must be one non-empty path component")
        if "fqn" in node.attrs:
            errors.append(f"node {node.id}: FQN must be derived, not persisted")
        try:
            node.state.pack()
        except ValueError as error:
            errors.append(f"node {node.id}: {error}")
        if node.owner != NODE_INVALID and node.owner not in by_id:
            errors.append(f"node {node.id}: owner {node.owner} is missing")
        for reference in node.refs:
            if reference.target not in by_id:
                errors.append(
                    f"node {node.id}: ref to unknown target {reference.target}"
                )

    try:
        derived_fqns(container.nodes)
    except ValueError as error:
        errors.append(str(error))

    target_decoders = {
        ARCHITECTURE_TYPE: ArchitectureObject.from_node,
        ABI_TYPE: ABIObject.from_node,
        ENVIRONMENT_TYPE: EnvironmentObject.from_node,
        FEATURE_TYPE: FeatureObject.from_node,
        FEATURE_SET_TYPE: FeatureSetObject.from_node,
        RUNTIME_TARGET_TYPE: RuntimeTargetObject.from_node,
    }
    for node in container.nodes:
        decoder = target_decoders.get(node.type_id)
        if decoder is not None:
            try:
                decoder(node)
            except ValueError as error:
                errors.append(str(error))

    functions = [node for node in container.nodes if node.type_id == FUNCTION_TYPE]
    for function in functions:
        try:
            record = decode_function(function)
        except ValueError as error:
            errors.append(str(error))
            continue
        signature = by_id.get(record.signature_id)
        if signature is None or signature.type_id != SIGNATURE_TYPE:
            errors.append(
                f"Function {function.id} has invalid Signature {record.signature_id}"
            )
        expected = semantic_digest(
            function.name, record.signature_id, record.effects, record.semantic_version
        )
        if container.header.version < 5 and record.semantic_digest != expected:
            errors.append(f"Function {function.id} semantic digest is invalid")
        if container.header.version >= 5:
            numeric_refs = _targets(function, CallRole.NUMERIC_CONTRACT)
            if (
                function.parent == 74
                and record.implementation == FunctionImplementation.CODE_BACKED
                and len(numeric_refs) != 1
            ):
                errors.append(
                    f"Function {function.id} requires exactly one NumericContract"
                )
        children = [node for node in container.nodes if node.parent == function.id]
        implementations = [node for node in children if node.type_id == CODE_TYPE]
        implementation_refs = _targets(function, CallRole.IMPLEMENTATION)
        if sorted(implementation_refs) != sorted(node.id for node in implementations):
            errors.append(
                f"Function {function.id} implementation refs/children disagree"
            )
        if (
            record.implementation == FunctionImplementation.CODE_BACKED
            and not implementations
        ):
            errors.append(
                f"code-backed Function {function.id} has no Code implementation"
            )
        if record.implementation == FunctionImplementation.COMPOSED:
            body = by_id.get(record.body_id)
            if not record.body_id or body is None or body.type_id != CALL_TYPE:
                errors.append(f"composed Function {function.id} requires a Call body")
        elif record.body_id:
            errors.append(f"non-composed Function {function.id} must not have a body")
        if record.implementation == FunctionImplementation.IMPORTED:
            imported = [
                node
                for node in container.nodes
                if node.type_id == IMPORT_TYPE
                and any(ref.target == function.id for ref in node.refs)
            ]
            if len(imported) != 1:
                errors.append(
                    f"imported Function {function.id} requires exactly one bound Import"
                )
        if (
            record.implementation == FunctionImplementation.INTRINSIC
            and record.intrinsic.name == "NONE"
        ):
            errors.append(
                f"intrinsic Function {function.id} requires intrinsic metadata"
            )
        if (
            record.implementation != FunctionImplementation.INTRINSIC
            and record.intrinsic.name != "NONE"
        ):
            errors.append(
                f"Function {function.id} intrinsic metadata requires INTRINSIC implementation"
            )

    for call in (node for node in container.nodes if node.type_id == CALL_TYPE):
        import struct

        if len(call.data) != 24:
            errors.append(f"Call {call.id} payload must be 24 bytes")
            continue
        payload_callee, payload_count, _result_type = struct.unpack("<3Q", call.data)
        callees = _targets(call, CallRole.CALLEE)
        arguments = _targets(call, CallRole.ARGUMENT)
        if callees != [payload_callee] or len(arguments) != payload_count:
            errors.append(f"Call {call.id} payload/ref disagreement")
        if (
            len(callees) != 1
            or by_id.get(callees[0], None) is None
            or by_id[callees[0]].type_id != FUNCTION_TYPE
        ):
            errors.append(f"Call {call.id} must target exactly one Function")
        if any(
            ref.target in by_id and by_id[ref.target].type_id == CODE_TYPE
            for ref in call.refs
        ):
            errors.append(f"Call {call.id} must not target Code directly")
        if (
            len(callees) == 1
            and callees[0] in by_id
            and by_id[callees[0]].type_id == FUNCTION_TYPE
        ):
            try:
                if not function_is_callable(container, by_id[callees[0]]):
                    errors.append(
                        f"Call {call.id} must target an actually callable Function"
                    )
            except ValueError:
                pass
        if (
            len(callees) == 1
            and callees[0] in by_id
            and by_id[callees[0]].type_id == FUNCTION_TYPE
        ):
            callee_record = decode_function(by_id[callees[0]])
            signature = by_id.get(callee_record.signature_id)
            shape = (
                _signature_shape(signature, by_id) if signature is not None else None
            )
            if shape is not None:
                result_type, parameter_types = shape
                if _result_type and _result_type != result_type:
                    errors.append(
                        f"Call {call.id} result type does not match Signature"
                    )
                if len(arguments) != len(parameter_types):
                    errors.append(
                        f"Call {call.id} argument count does not match Signature"
                    )
                for position, argument_id in enumerate(
                    arguments[: len(parameter_types)]
                ):
                    argument = by_id.get(argument_id)
                    if argument is None:
                        continue
                    value_id = argument_id
                    if argument.type_id == ARGUMENT_TYPE and len(argument.data) == 16:
                        parameter_id, value_id = struct.unpack("<2Q", argument.data)
                        expected_parameter = _role_targets(
                            signature, int(CallRole.PARAMETER)
                        )[position]
                        if parameter_id != expected_parameter:
                            errors.append(
                                f"Call {call.id} Argument {argument.id} parameter/order mismatch"
                            )
                    value = by_id.get(value_id)
                    if (
                        value is not None
                        and value.type_id == VALUE_TYPE
                        and len(value.data) >= 24
                    ):
                        value_type = struct.unpack("<Q", value.data[:8])[0]
                        if value_type != parameter_types[position]:
                            errors.append(
                                f"Call {call.id} Argument {argument_id} value type mismatch"
                            )
        for argument_id in _targets(call, CallRole.ARGUMENT):
            argument = by_id.get(argument_id)
            if argument is not None and argument.type_id not in (
                ABI_TYPE,
                ARCHITECTURE_TYPE,
                ARGUMENT_TYPE,
                FUNCTION_TYPE,
                VALUE_TYPE,
            ):
                errors.append(f"Call {call.id} argument {argument_id} is invalid")

    for code in (node for node in container.nodes if node.type_id == CODE_TYPE):
        try:
            record = decode_code(code)
        except ValueError as error:
            errors.append(str(error))
            continue
        owner = by_id.get(record.owner_function)
        target = by_id.get(record.target_id)
        if owner is None or owner.type_id != FUNCTION_TYPE or code.parent != owner.id:
            errors.append(f"Code {code.id} must belong to exactly one Function")
        if target is None or target.type_id not in (TARGET_TYPE, RUNTIME_TARGET_TYPE):
            errors.append(
                f"Code {code.id} has invalid RuntimeTarget {record.target_id}"
            )
        if owner is not None and owner.type_id == FUNCTION_TYPE:
            function_record = decode_function(owner)
            if (
                record.signature_id,
                record.effects,
                record.semantic_version,
                record.semantic_digest,
            ) != (
                function_record.signature_id,
                function_record.effects,
                function_record.semantic_version,
                function_record.semantic_digest,
            ):
                errors.append(f"Code {code.id} Function contract disagreement")
        signature_refs = _targets(code, CallRole.SIGNATURE_CONTRACT)
        if signature_refs != [record.signature_id]:
            errors.append(f"Code {code.id} signature payload/ref disagreement")
        owner_impls = (
            _targets(owner, CallRole.IMPLEMENTATION) if owner is not None else []
        )
        if code.id not in owner_impls:
            errors.append(
                f"Code {code.id} owner/ref implementation agreement is invalid"
            )
        imports = _targets(code, CallRole.IMPORT)
        relocations = _targets(code, CallRole.RELOCATION)
        for import_id in imports:
            imported_node = by_id.get(import_id)
            if imported_node is None or imported_node.type_id != IMPORT_TYPE:
                errors.append(f"Code {code.id} Import {import_id} is invalid")
                continue
            try:
                decode_import(imported_node)
            except ValueError as error:
                errors.append(str(error))
        for relocation_id in relocations:
            relocation_node = by_id.get(relocation_id)
            if relocation_node is None or relocation_node.type_id != RELOCATION_TYPE:
                errors.append(f"Code {code.id} Relocation {relocation_id} is invalid")
                continue
            try:
                relocation = decode_relocation(relocation_node)
                width = relocation.patch_width
                if (
                    relocation.offset > len(record.raw_bytes)
                    or width > len(record.raw_bytes) - relocation.offset
                ):
                    errors.append(
                        f"Code {code.id} Relocation {relocation_id} is out of byte bounds"
                    )
                if (
                    relocation.target_id not in imports
                    and relocation.target_id not in by_id
                ):
                    errors.append(
                        f"Code {code.id} Relocation {relocation_id} target is missing"
                    )
            except ValueError as error:
                errors.append(str(error))

    for imported in (node for node in container.nodes if node.type_id == IMPORT_TYPE):
        callees = [ref.target for ref in imported.refs if ref.kind == RefKind.IMPORT]
        if (
            len(callees) != 1
            or by_id.get(callees[0], None) is None
            or by_id[callees[0]].type_id != FUNCTION_TYPE
        ):
            errors.append(f"Import {imported.id} must reference one Function")

    generic_parameters = {}
    for node in (
        item for item in container.nodes if item.type_id == GENERIC_PARAMETER_TYPE
    ):
        try:
            generic_parameters[node.id] = GenericParameter.from_node(node)
        except ValueError as error:
            errors.append(str(error))

    templates = {}
    for node in (item for item in container.nodes if item.type_id == TEMPLATE_TYPE):
        try:
            record = GenericTemplate.from_node(node)
            templates[node.id] = record
            expected = (record.function_id,)
            if _role_targets(node, int(GenericRole.SPECIALIZED_FUNCTION)) != expected:
                errors.append(f"Template {node.id} Function payload/ref disagreement")
            indices = [
                generic_parameters[value].index
                for value in record.parameter_ids
                if value in generic_parameters
            ]
            if len(indices) != len(record.parameter_ids) or indices != list(
                range(len(indices))
            ):
                errors.append(
                    f"Template {node.id} generic parameters must be contiguous"
                )
            if len(set(record.parameter_ids)) != len(record.parameter_ids):
                errors.append(f"Template {node.id} has duplicate generic parameters")
        except ValueError as error:
            errors.append(str(error))

    arguments = {}
    for node in (
        item for item in container.nodes if item.type_id == GENERIC_ARGUMENT_TYPE
    ):
        try:
            record = GenericArgument.from_node(node)
            arguments[node.id] = record
            if _role_targets(node, int(GenericRole.PARAMETER)) != (
                record.parameter_id,
            ):
                errors.append(
                    f"GenericArgument {node.id} parameter payload/ref disagreement"
                )
            if _role_targets(node, int(GenericRole.BOUND_TYPE)) != (record.bound_type,):
                errors.append(
                    f"GenericArgument {node.id} bound type payload/ref disagreement"
                )
            if record.bound_type not in type_table.types:
                errors.append(f"GenericArgument {node.id} bound TYPE is missing")
        except ValueError as error:
            errors.append(str(error))

    seen_specialization_ids = {}
    for node in (
        item for item in container.nodes if item.type_id == SPECIALIZATION_TYPE
    ):
        try:
            record = Specialization.from_node(node)
            template = templates.get(record.template_id)
            bound = [
                arguments[value] for value in record.argument_ids if value in arguments
            ]
            if template is None or len(bound) != len(record.argument_ids):
                errors.append(
                    f"Specialization {node.id} has incomplete template arguments"
                )
                continue
            if [item.parameter_id for item in bound] != list(template.parameter_ids):
                errors.append(
                    f"Specialization {node.id} arguments do not exactly cover parameters"
                )
            bindings = tuple((item.parameter_id, item.bound_type) for item in bound)
            expected_digest = specialization_digest(
                record.template_id, record.template_version, bindings
            )
            if (
                record.template_version != template.semantic_version
                or record.digest != expected_digest
            ):
                errors.append(f"Specialization {node.id} digest is invalid")
            if node.id != specialization_id(expected_digest):
                errors.append(f"Specialization {node.id} ID is not digest-derived")
            previous = seen_specialization_ids.setdefault(node.id, record.digest)
            if previous != record.digest:
                errors.append(f"Specialization {node.id} has a digest collision")
            function = by_id.get(record.function_id)
            if function is None or function.type_id != FUNCTION_TYPE:
                errors.append(f"Specialization {node.id} concrete Function is missing")
            elif _role_targets(node, int(GenericRole.SPECIALIZED_FUNCTION)) != (
                record.function_id,
            ):
                errors.append(
                    f"Specialization {node.id} Function payload/ref disagreement"
                )
        except ValueError as error:
            errors.append(str(error))

    traits = {}
    for node in (item for item in container.nodes if item.type_id == TRAIT_TYPE):
        try:
            record = Trait.from_node(node)
            traits[node.id] = record
            if len(set(record.requirement_ids)) != len(record.requirement_ids):
                errors.append(f"Trait {node.id} has duplicate requirements")
        except ValueError as error:
            errors.append(str(error))

    requirements = {}
    for node in (
        item for item in container.nodes if item.type_id == TRAIT_REQUIREMENT_TYPE
    ):
        try:
            record = TraitRequirement.from_node(node)
            requirements[node.id] = record
            if _role_targets(node, int(TraitRole.DECLARATION)) != (record.function_id,):
                errors.append(
                    f"TraitRequirement {node.id} declaration payload/ref disagreement"
                )
        except ValueError as error:
            errors.append(str(error))

    associated_types = {}
    for node in (
        item for item in container.nodes if item.type_id == ASSOCIATED_TYPE_TYPE
    ):
        try:
            associated_types[node.id] = AssociatedType.from_node(node)
        except ValueError as error:
            errors.append(str(error))

    bindings = {}
    for node in (
        item for item in container.nodes if item.type_id == IMPLEMENTATION_BINDING_TYPE
    ):
        try:
            record = ImplementationBinding.from_node(node)
            bindings[node.id] = record
            target_role = (
                TraitRole.BOUND_TYPE
                if record.kind == ImplementationBindingKind.ASSOCIATED_TYPE
                else TraitRole.IMPLEMENTATION
            )
            if _role_targets(node, int(TraitRole.REQUIREMENT)) != (
                record.requirement_id,
            ) or _role_targets(node, int(target_role)) != (record.implementation_id,):
                errors.append(
                    f"ImplementationBinding {node.id} payload/ref disagreement"
                )
        except ValueError as error:
            errors.append(str(error))

    trait_closure: dict[int, tuple[set[int], set[int]]] = {}
    visiting_traits: set[int] = set()

    def inherited_contract(trait_id: int) -> tuple[set[int], set[int]]:
        if trait_id in trait_closure:
            return trait_closure[trait_id]
        if trait_id in visiting_traits:
            errors.append(f"Trait {trait_id} has a supertrait cycle")
            return set(), set()
        trait = traits.get(trait_id)
        if trait is None:
            return set(), set()
        visiting_traits.add(trait_id)
        required = set(trait.requirement_ids)
        associated = set(trait.associated_type_ids)
        for supertrait_id in trait.supertrait_ids:
            if supertrait_id not in traits:
                errors.append(f"Trait {trait_id} supertrait {supertrait_id} is missing")
                continue
            super_required, super_associated = inherited_contract(supertrait_id)
            required.update(super_required)
            associated.update(super_associated)
        visiting_traits.remove(trait_id)
        trait_closure[trait_id] = required, associated
        return required, associated

    for trait_id in traits:
        inherited_contract(trait_id)

    seen_conformances = set()
    for node in (item for item in container.nodes if item.type_id == CONFORMANCE_TYPE):
        try:
            record = Conformance.from_node(node)
            key = (record.trait_id, record.conforming_type)
            if key in seen_conformances:
                errors.append(
                    f"duplicate conformance for trait {record.trait_id} and type {record.conforming_type}"
                )
            seen_conformances.add(key)
            trait = traits.get(record.trait_id)
            concrete = [
                bindings[value] for value in record.binding_ids if value in bindings
            ]
            if trait is None or len(concrete) != len(record.binding_ids):
                errors.append(f"Conformance {node.id} has invalid bindings")
                continue
            keys = [(item.kind, item.requirement_id) for item in concrete]
            if len(set(keys)) != len(keys):
                errors.append(f"Conformance {node.id} has duplicate bindings")
            actual_requirements = {
                item.requirement_id
                for item in concrete
                if item.kind == ImplementationBindingKind.REQUIREMENT
            }
            expected_requirements, expected_associated = inherited_contract(trait.id)
            if actual_requirements != expected_requirements:
                errors.append(
                    f"Conformance {node.id} requirement coverage is not exact"
                )
            actual_associated = {
                item.requirement_id
                for item in concrete
                if item.kind == ImplementationBindingKind.ASSOCIATED_TYPE
            }
            if actual_associated != expected_associated:
                errors.append(
                    f"Conformance {node.id} associated type coverage is not exact"
                )
            for item in concrete:
                if item.kind != ImplementationBindingKind.REQUIREMENT:
                    if item.implementation_id not in type_table.types:
                        errors.append(
                            f"Conformance {node.id} associated binding TYPE is missing"
                        )
                    continue
                requirement = requirements.get(item.requirement_id)
                declaration = (
                    by_id.get(requirement.function_id) if requirement else None
                )
                implementation = by_id.get(item.implementation_id)
                if (
                    declaration is None
                    or implementation is None
                    or implementation.type_id != FUNCTION_TYPE
                ):
                    errors.append(
                        f"Conformance {node.id} requirement implementation is missing"
                    )
                    continue
                declaration_record = decode_function(declaration)
                implementation_record = decode_function(implementation)
                declared_signature = by_id.get(declaration_record.signature_id)
                concrete_signature = by_id.get(implementation_record.signature_id)
                declared_shape = (
                    _signature_shape(declared_signature, by_id)
                    if declared_signature is not None
                    else None
                )
                concrete_shape = (
                    _signature_shape(concrete_signature, by_id)
                    if concrete_signature is not None
                    else None
                )
                associated_substitutions = {
                    bound.requirement_id: bound.implementation_id
                    for bound in concrete
                    if bound.kind == ImplementationBindingKind.ASSOCIATED_TYPE
                }
                if declared_shape is not None:
                    declared_shape = (
                        associated_substitutions.get(
                            declared_shape[0], declared_shape[0]
                        ),
                        tuple(
                            associated_substitutions.get(value, value)
                            for value in declared_shape[1]
                        ),
                    )
                if declared_shape is None or declared_shape != concrete_shape:
                    errors.append(
                        f"Conformance {node.id} requirement signature mismatch"
                    )
                if not function_is_callable(container, implementation):
                    errors.append(
                        f"Conformance {node.id} binds requirement to non-callable declaration"
                    )
        except ValueError as error:
            errors.append(str(error))

    semantic = [node for node in container.nodes if not node.attrs.get("derived")]
    derived = sorted(
        (
            {key: value for key, value in node.to_dict().items() if key != "state"}
            for node in container.nodes
            if node.attrs.get("derived")
        ),
        key=lambda item: item["id"],
    )
    if derived:
        try:
            expected = sorted(
                (
                    {
                        key: value
                        for key, value in node.to_dict().items()
                        if key != "state"
                    }
                    for node in lower(
                        Container(
                            header=container.header, heap=container.heap, nodes=semantic
                        )
                    ).nodes
                ),
                key=lambda item: item["id"],
            )
        except ValueError as error:
            errors.append(f"lowering failed: {error}")
            expected = []
        if derived != expected:
            errors.append("derived CFG objects are incomplete or non-deterministic")
        if any(
            node.type_id not in (CFG_BLOCK_TYPE, CFG_OP_TYPE)
            for node in container.nodes
            if node.attrs.get("derived")
        ):
            errors.append("derived execution object has an invalid TYPE")
    return errors
