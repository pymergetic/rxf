"""Class-based templates for the canonical callable RXF object graph."""

from copy import deepcopy
from dataclasses import dataclass, field

from pymergetic.rxf.model.capability_corpus import capability_nodes, toolchain_nodes
from pymergetic.rxf.model.container import Container, Header
from pymergetic.rxf.model.contracts import ResultObject
from pymergetic.rxf.model.execution import (
    ArgumentObject,
    CallObject,
    Effect,
    FunctionImplementation,
    FunctionIntrinsic,
    FunctionLayer,
    FunctionObject,
    ParameterObject,
    SignatureObject,
    ValueKind,
    ValueObject,
)
from pymergetic.rxf.model.module import ModuleCategory, ModuleObject
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.numeric import control_nodes, numeric_nodes
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.stdlib_corpus import stdlib_nodes
from pymergetic.rxf.model.target import native_target_nodes
from pymergetic.rxf.model.templates_traits import proving_nodes
from pymergetic.rxf.ops.lowering import with_lowered
from pymergetic.rxf.schema import NodeKind, RefKind
from pymergetic.rxf.ty.builtins import (
    BASIC_MODULE_ID,
    BOOL_TYPE,
    FIELD_TYPE,
    MODULE_TYPE,
    NUMERIC_MODULE_ID,
    RXF_MODULE_ID,
    TYPE_TYPE,
    U32_TYPE,
    builtin_nodes,
)
from pymergetic.rxf.ty.objects import FieldObject, TypeForm, TypeObject

COUNTER_STATE_TYPE = 256
COUNTER_VALUE_FIELD = 257
COUNTER_STEP_FIELD = 258
COUNTER_LIMIT_FIELD = 259
COUNTER_RESERVED_FIELD = 260
COUNTER_STATE_ID = 301
COUNTER_FUNCTION_ID = 400
COUNTER_SIGNATURE_ID = 401
WHILE_FUNCTION_ID = 410
WHILE_SIGNATURE_ID = 411
WHILE_CONDITION_PARAMETER_ID = 412
WHILE_BODY_PARAMETER_ID = 413
LESS_THAN_FUNCTION_ID = 420
LESS_THAN_SIGNATURE_ID = 421
ASSIGN_FUNCTION_ID = 430
ASSIGN_SIGNATURE_ID = 431
CONDITION_FUNCTION_ID = 440
CONDITION_SIGNATURE_ID = 441
BODY_FUNCTION_ID = 450
BODY_SIGNATURE_ID = 451
COUNTER_CALL_ID = 460
WHILE_CALL_ID = 461
LESS_THAN_CALL_ID = 462
ASSIGN_CALL_ID = 463
STATE_VALUE_ID = 470
LIMIT_VALUE_ID = 471
NEXT_VALUE_ID = 472
CONDITION_ARGUMENT_ID = 480
BODY_ARGUMENT_ID = 481
ASSIGN_TARGET_ARGUMENT_ID = 482
ASSIGN_VALUE_ARGUMENT_ID = 483
APPLICATION_MODULE_ID = 500
COUNTER_MODULE_ID = 501


@dataclass
class Template:
    entry: int = 0
    include_lowered: bool = True
    extra_nodes: list[NodeDef] = field(default_factory=list)
    omitted_builtin_module_ids: tuple[int, ...] = ()

    def build(self) -> Container:
        builtins = [
            node
            for node in builtin_nodes()
            if node.id not in self.omitted_builtin_module_ids
        ]
        container = Container(
            header=Header(entry_node=self.entry),
            nodes=builtins + deepcopy(self.extra_nodes),
        )
        return with_lowered(container) if self.include_lowered else container


def _module_node(module: ModuleObject) -> NodeDef:
    return NodeDef(
        id=module.id,
        name=module.name,
        kind=NodeKind.MODULE,
        parent=module.parent,
        type_id=MODULE_TYPE,
        data=module.to_payload(),
    )


def _field_node(descriptor: FieldObject) -> NodeDef:
    return NodeDef(
        id=descriptor.id,
        name=descriptor.name,
        kind=NodeKind.FIELD,
        parent=descriptor.owner_type,
        type_id=FIELD_TYPE,
        data=descriptor.to_payload(),
    )


def counter_template(*, include_lowered: bool = True) -> Template:
    numeric = numeric_nodes()
    numeric_functions = {node.name: node.id for node in numeric if node.type_id == 14}
    state_type = TypeObject(
        COUNTER_STATE_TYPE, "CounterState", TypeForm.STRUCT, 16, 4, field_count=4
    )
    type_nodes = [
        NodeDef(
            id=state_type.id,
            name=state_type.name,
            kind=NodeKind.TYPE,
            parent=COUNTER_MODULE_ID,
            type_id=TYPE_TYPE,
            data=state_type.to_payload(),
        )
    ]
    type_nodes.extend(
        _field_node(item)
        for item in (
            FieldObject(COUNTER_VALUE_FIELD, "value", state_type.id, U32_TYPE, 0),
            FieldObject(COUNTER_STEP_FIELD, "step", state_type.id, U32_TYPE, 4),
            FieldObject(COUNTER_LIMIT_FIELD, "limit", state_type.id, U32_TYPE, 8),
            FieldObject(
                COUNTER_RESERVED_FIELD, "reserved", state_type.id, U32_TYPE, 12
            ),
        )
    )
    nodes = [
        _module_node(
            ModuleObject(
                APPLICATION_MODULE_ID,
                "application",
                RXF_MODULE_ID,
                ModuleCategory.APPLICATION,
            )
        ),
        _module_node(
            ModuleObject(
                COUNTER_MODULE_ID,
                "counter",
                APPLICATION_MODULE_ID,
                ModuleCategory.APPLICATION,
            )
        ),
        NodeDef(
            id=0,
            name="root",
            kind=NodeKind.ROOT,
            refs=[RefDef(0, COUNTER_CALL_ID, RefKind.CALL)],
        ),
        NodeDef(
            id=COUNTER_STATE_ID,
            name="counter_state",
            kind=NodeKind.DATA,
            parent=COUNTER_MODULE_ID,
            type_id=COUNTER_STATE_TYPE,
            data=(0).to_bytes(4, "little")
            + (1).to_bytes(4, "little")
            + (10).to_bytes(4, "little")
            + b"\x00" * 4,
        ),
        SignatureObject(
            COUNTER_SIGNATURE_ID,
            "signature",
            COUNTER_FUNCTION_ID,
            U32_TYPE,
        ).to_node(),
        FunctionObject(
            COUNTER_FUNCTION_ID,
            "run",
            COUNTER_MODULE_ID,
            COUNTER_SIGNATURE_ID,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            body_id=WHILE_CALL_ID,
        ).to_node(),
        ParameterObject(
            WHILE_CONDITION_PARAMETER_ID,
            "condition",
            WHILE_SIGNATURE_ID,
            COUNTER_SIGNATURE_ID,
            0,
            lazy=True,
        ).to_node(),
        ParameterObject(
            WHILE_BODY_PARAMETER_ID,
            "body",
            WHILE_SIGNATURE_ID,
            COUNTER_SIGNATURE_ID,
            1,
            lazy=True,
        ).to_node(),
        SignatureObject(
            WHILE_SIGNATURE_ID,
            "signature",
            WHILE_FUNCTION_ID,
            U32_TYPE,
            (WHILE_CONDITION_PARAMETER_ID, WHILE_BODY_PARAMETER_ID),
        ).to_node(),
        FunctionObject(
            WHILE_FUNCTION_ID,
            "while",
            BASIC_MODULE_ID,
            WHILE_SIGNATURE_ID,
            FunctionImplementation.INTRINSIC,
            FunctionLayer.BASIC,
            intrinsic=FunctionIntrinsic.WHILE,
        ).to_node(),
        SignatureObject(
            LESS_THAN_SIGNATURE_ID,
            "signature",
            LESS_THAN_FUNCTION_ID,
            BOOL_TYPE,
        ).to_node(),
        FunctionObject(
            LESS_THAN_FUNCTION_ID,
            "less_than_u32_contract",
            BASIC_MODULE_ID,
            LESS_THAN_SIGNATURE_ID,
            FunctionImplementation.DECLARED,
            FunctionLayer.BASIC,
        ).to_node(),
        SignatureObject(
            ASSIGN_SIGNATURE_ID,
            "signature",
            ASSIGN_FUNCTION_ID,
            U32_TYPE,
        ).to_node(),
        FunctionObject(
            ASSIGN_FUNCTION_ID,
            "assign_u32_contract",
            BASIC_MODULE_ID,
            ASSIGN_SIGNATURE_ID,
            FunctionImplementation.DECLARED,
            FunctionLayer.BASIC,
            effects=Effect.WRITE_MEMORY,
        ).to_node(),
        SignatureObject(
            CONDITION_SIGNATURE_ID,
            "signature",
            CONDITION_FUNCTION_ID,
            U32_TYPE,
        ).to_node(),
        FunctionObject(
            CONDITION_FUNCTION_ID,
            "condition",
            COUNTER_FUNCTION_ID,
            CONDITION_SIGNATURE_ID,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            body_id=LESS_THAN_CALL_ID,
        ).to_node(),
        SignatureObject(
            BODY_SIGNATURE_ID,
            "signature",
            BODY_FUNCTION_ID,
            U32_TYPE,
        ).to_node(),
        FunctionObject(
            BODY_FUNCTION_ID,
            "body",
            COUNTER_FUNCTION_ID,
            BODY_SIGNATURE_ID,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            effects=Effect.WRITE_MEMORY,
            body_id=ASSIGN_CALL_ID,
        ).to_node(),
        ValueObject(
            STATE_VALUE_ID,
            "value",
            COUNTER_FUNCTION_ID,
            U32_TYPE,
            ValueKind.OBJECT,
            COUNTER_STATE_ID.to_bytes(8, "little"),
        ).to_node(),
        ValueObject(
            LIMIT_VALUE_ID,
            "limit",
            CONDITION_FUNCTION_ID,
            U32_TYPE,
            ValueKind.LITERAL,
            (10).to_bytes(4, "little"),
        ).to_node(),
        ValueObject(
            NEXT_VALUE_ID,
            "next",
            BODY_FUNCTION_ID,
            U32_TYPE,
            ValueKind.LITERAL,
            (1).to_bytes(4, "little"),
        ).to_node(),
        CallObject(
            LESS_THAN_CALL_ID,
            "compare_limit",
            CONDITION_FUNCTION_ID,
            numeric_functions["less_uint32_t"],
            (STATE_VALUE_ID, LIMIT_VALUE_ID),
            BOOL_TYPE,
        ).to_node(),
        CallObject(
            ASSIGN_CALL_ID,
            "increment_counter",
            BODY_FUNCTION_ID,
            numeric_functions["checked_add_uint32_t"],
            (STATE_VALUE_ID, NEXT_VALUE_ID),
            U32_TYPE,
        ).to_node(),
        ArgumentObject(
            CONDITION_ARGUMENT_ID,
            "condition",
            WHILE_CALL_ID,
            WHILE_CONDITION_PARAMETER_ID,
            CONDITION_FUNCTION_ID,
        ).to_node(),
        ArgumentObject(
            BODY_ARGUMENT_ID,
            "body",
            WHILE_CALL_ID,
            WHILE_BODY_PARAMETER_ID,
            BODY_FUNCTION_ID,
        ).to_node(),
        CallObject(
            WHILE_CALL_ID,
            "counter_loop",
            COUNTER_FUNCTION_ID,
            WHILE_FUNCTION_ID,
            (CONDITION_ARGUMENT_ID, BODY_ARGUMENT_ID),
            U32_TYPE,
        ).to_node(),
        CallObject(
            COUNTER_CALL_ID,
            "start",
            COUNTER_MODULE_ID,
            COUNTER_FUNCTION_ID,
            (),
            U32_TYPE,
        ).to_node(),
    ]
    return Template(
        include_lowered=include_lowered,
        extra_nodes=type_nodes
        + native_target_nodes(76)
        + numeric
        + control_nodes()
        + proving_nodes()
        + nodes,
    )


COUNTER = counter_template()

# Canonical useful checkout starter image.
STARTER_FOUNDATION_MODULE_ID = NUMERIC_MODULE_ID
STARTER_LIBRARY_MODULE_ID = 40000
STARTER_APPLICATION_MODULE_ID = 40001
STARTER_MAIN_FUNCTION_ID = 40010
STARTER_MAIN_SIGNATURE_ID = 40011
STARTER_CHECKOUT_FUNCTION_ID = 40020
STARTER_CHECKOUT_SIGNATURE_ID = 40021
STARTER_IDENTITY_FUNCTION_ID = 0
STARTER_NORMALIZE_FUNCTION_ID = STARTER_CHECKOUT_FUNCTION_ID
STARTER_X86_CODE_ID = 0
STARTER_AARCH64_CODE_ID = 0


def starter_template() -> Template:
    """One complete checkout application plus the entire native Basic corpus."""
    numeric = numeric_nodes()
    functions = {node.name: node for node in numeric if node.type_id == 14}
    signatures = {node.id: node for node in numeric if node.type_id == 15}

    def parameters(function_name: str) -> tuple[int, ...]:
        import struct

        from pymergetic.rxf.model.execution import CallRole

        fn = functions[function_name]
        signature_id = struct.unpack_from("<Q", fn.data, 16)[0]
        return tuple(
            r.target
            for r in signatures[signature_id].refs
            if r.to_off == int(CallRole.PARAMETER)
        )

    modules = [
        ModuleObject(
            STARTER_LIBRARY_MODULE_ID,
            "library",
            RXF_MODULE_ID,
            ModuleCategory.EXECUTION,
        ),
        ModuleObject(
            STARTER_APPLICATION_MODULE_ID,
            "application",
            RXF_MODULE_ID,
            ModuleCategory.APPLICATION,
        ),
    ]
    nodes = [
        *native_target_nodes(76),
        *numeric,
        *control_nodes(),
        *stdlib_nodes(),
        *capability_nodes(),
        *toolchain_nodes(),
        *(_module_node(m) for m in modules),
        NodeDef(id=0, name="root", kind=NodeKind.ROOT),
    ]
    next_id = 40100

    def alloc():
        nonlocal next_id
        value = next_id
        next_id += 1
        return value

    literals = {}
    for name, value in [
        ("unit_price", 125),
        ("quantity", 4),
        ("discount", 50),
        ("shipping", 25),
        ("tax_percent", 20),
        ("hundred", 100),
    ]:
        data_id, value_id = alloc(), alloc()
        nodes.append(
            NodeDef(
                data_id,
                name,
                NodeKind.DATA,
                STARTER_APPLICATION_MODULE_ID,
                type_id=U32_TYPE,
                data=value.to_bytes(4, "little"),
            )
        )
        nodes.append(
            ValueObject(
                value_id,
                name,
                STARTER_CHECKOUT_FUNCTION_ID,
                U32_TYPE,
                ValueKind.OBJECT,
                data_id.to_bytes(8, "little"),
            ).to_node()
        )
        literals[name] = value_id
    previous = None
    call_ids = []
    specs = [
        (
            "checked_multiply_uint32_t",
            "subtotal",
            literals["unit_price"],
            literals["quantity"],
        ),
        ("checked_subtract_uint32_t", "discounted", None, literals["discount"]),
        ("checked_add_uint32_t", "with_shipping", None, literals["shipping"]),
        ("checked_multiply_uint32_t", "tax_numerator", None, literals["tax_percent"]),
        ("divide_uint32_t", "tax", None, literals["hundred"]),
        ("checked_add_uint32_t", "total", None, None),
    ]
    taxable = None
    tax_value = None
    for index, (callee, name, left, right) in enumerate(specs):
        if index == 1 or index == 2:
            left = previous
        elif index == 3:
            taxable = previous
            left = previous
        elif index == 4:
            left = previous
        elif index == 5:
            left = taxable
            right = tax_value
        if left is None or right is None:
            raise AssertionError("checkout call arguments must be fully wired")
        call_id = alloc()
        call_ids.append(call_id)
        parameter_ids = parameters(callee)
        argument_ids = []
        for position, value_id in enumerate((left, right)):
            argument_id = alloc()
            nodes.append(
                ArgumentObject(
                    argument_id,
                    f"operand_{position}",
                    call_id,
                    parameter_ids[position],
                    value_id,
                ).to_node()
            )
            argument_ids.append(argument_id)
        nodes.append(
            CallObject(
                call_id,
                name,
                STARTER_CHECKOUT_FUNCTION_ID,
                functions[callee].id,
                tuple(argument_ids),
                U32_TYPE,
            ).to_node()
        )
        result_value = alloc()
        nodes.append(
            ValueObject(
                result_value,
                name,
                STARTER_CHECKOUT_FUNCTION_ID,
                U32_TYPE,
                ValueKind.RESULT,
                call_id.to_bytes(8, "little"),
            ).to_node()
        )
        previous = result_value
        if index == 4:
            tax_value = result_value
    checkout_result = alloc()
    nodes.append(
        ResultObject(
            checkout_result, "total", STARTER_CHECKOUT_SIGNATURE_ID, U32_TYPE, 0
        ).to_node()
    )
    nodes.append(
        SignatureObject(
            STARTER_CHECKOUT_SIGNATURE_ID,
            "signature",
            STARTER_CHECKOUT_FUNCTION_ID,
            U32_TYPE,
            (),
            (checkout_result,),
            0,
        ).to_node()
    )
    nodes.append(
        FunctionObject(
            STARTER_CHECKOUT_FUNCTION_ID,
            "checkout_total",
            STARTER_LIBRARY_MODULE_ID,
            STARTER_CHECKOUT_SIGNATURE_ID,
            FunctionImplementation.COMPOSED,
            FunctionLayer.LIBRARY,
            body_id=call_ids[-1],
        ).to_node()
    )
    main_result = alloc()
    nodes.append(
        ResultObject(
            main_result, "total", STARTER_MAIN_SIGNATURE_ID, U32_TYPE, 0
        ).to_node()
    )
    nodes.append(
        SignatureObject(
            STARTER_MAIN_SIGNATURE_ID,
            "signature",
            STARTER_MAIN_FUNCTION_ID,
            U32_TYPE,
            (),
            (main_result,),
            0,
        ).to_node()
    )
    main_call = alloc()
    nodes.append(
        CallObject(
            main_call,
            "checkout",
            STARTER_MAIN_FUNCTION_ID,
            STARTER_CHECKOUT_FUNCTION_ID,
            (),
            U32_TYPE,
        ).to_node()
    )
    nodes.append(
        FunctionObject(
            STARTER_MAIN_FUNCTION_ID,
            "main",
            STARTER_APPLICATION_MODULE_ID,
            STARTER_MAIN_SIGNATURE_ID,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            body_id=main_call,
        ).to_node()
    )
    return Template(
        entry=STARTER_MAIN_FUNCTION_ID, include_lowered=False, extra_nodes=nodes
    )


STARTER = starter_template()

# Reusable, executable native binding proof artifact. It is inspected/preflighted only.
NATIVE_ENTRY_FUNCTION_ID = 5000
NATIVE_LEAF_FUNCTION_ID = 5001
NATIVE_SIGNATURE_ID = 5002
NATIVE_BODY_CALL_ID = 5003
NATIVE_X86_CODE_ID = 5004
NATIVE_AARCH64_CODE_ID = 5005


def native_binding_template() -> Template:
    """Return a tiny composed graph with honest x86-64 and AArch64 leaves."""
    from pymergetic.rxf.model.execution import CodeFormat, CodeObject, semantic_digest
    from pymergetic.rxf.model.target import (
        AARCH64_RETURN_U32,
        AARCH64_UEFI_TARGET_ID,
        X86_64_LINUX_TARGET_ID,
        X86_64_RETURN_U32,
    )

    digest = semantic_digest("identity_native", NATIVE_SIGNATURE_ID, Effect.NONE, 1)
    return Template(
        entry=NATIVE_ENTRY_FUNCTION_ID,
        include_lowered=False,
        extra_nodes=[
            *native_target_nodes(76),
            NodeDef(id=0, name="root", kind=NodeKind.ROOT),
            SignatureObject(
                NATIVE_SIGNATURE_ID,
                "signature",
                NATIVE_LEAF_FUNCTION_ID,
                U32_TYPE,
            ).to_node(),
            FunctionObject(
                NATIVE_LEAF_FUNCTION_ID,
                "identity_native",
                BASIC_MODULE_ID,
                NATIVE_SIGNATURE_ID,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.FOUNDATION,
                implementation_ids=(NATIVE_X86_CODE_ID, NATIVE_AARCH64_CODE_ID),
            ).to_node(),
            CodeObject(
                NATIVE_X86_CODE_ID,
                "x86_64_code",
                NATIVE_LEAF_FUNCTION_ID,
                NATIVE_LEAF_FUNCTION_ID,
                X86_64_LINUX_TARGET_ID,
                CodeFormat.NATIVE,
                X86_64_RETURN_U32,
                NATIVE_SIGNATURE_ID,
                Effect.NONE,
                1,
                digest,
            ).to_node(),
            CodeObject(
                NATIVE_AARCH64_CODE_ID,
                "aarch64_code",
                NATIVE_LEAF_FUNCTION_ID,
                NATIVE_LEAF_FUNCTION_ID,
                AARCH64_UEFI_TARGET_ID,
                CodeFormat.NATIVE,
                AARCH64_RETURN_U32,
                NATIVE_SIGNATURE_ID,
                Effect.NONE,
                1,
                digest,
            ).to_node(),
            CallObject(
                NATIVE_BODY_CALL_ID,
                "native_body",
                NATIVE_ENTRY_FUNCTION_ID,
                NATIVE_LEAF_FUNCTION_ID,
            ).to_node(),
            FunctionObject(
                NATIVE_ENTRY_FUNCTION_ID,
                "native_entry",
                BASIC_MODULE_ID,
                NATIVE_SIGNATURE_ID,
                FunctionImplementation.COMPOSED,
                FunctionLayer.APPLICATION,
                body_id=NATIVE_BODY_CALL_ID,
            ).to_node(),
        ],
    )
