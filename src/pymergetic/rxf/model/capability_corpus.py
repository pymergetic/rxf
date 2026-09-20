"""Canonical imported capability Function declarations."""

from __future__ import annotations

from dataclasses import dataclass

from pymergetic.rxf.model.capabilities import (
    CapabilityKind,
    CapabilityRequirement,
    CapabilityRights,
)
from pymergetic.rxf.model.contracts import RefusalSet, RefusalVariant, ResultObject
from pymergetic.rxf.model.execution import (
    Effect,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    ImportObject,
    ParameterObject,
    SignatureObject,
)
from pymergetic.rxf.model.module import ModuleCategory, ModuleObject
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.builtins import (
    BOOL_TYPE,
    BYTES_TYPE,
    FIELD_TYPE,
    MODULE_TYPE,
    RXF_MODULE_ID,
    TYPE_TYPE,
    U32_TYPE,
    U64_TYPE,
    VOID_TYPE,
)
from pymergetic.rxf.ty.objects import FieldObject, TypeFlags, TypeForm, TypeObject

CAPABILITY_MODULE_ID = 42000
CAPABILITY_REFUSAL_SET_ID = 42001
CAPABILITY_REFUSED_ID = 42002
CAPABILITY_UNAVAILABLE_ID = 42003
CAPABILITY_INVALID_ID = 42004
HANDLE_TYPE = 42005
FILE_STAT_TYPE = 42006
PLATFORM_INFO_TYPE = 42007
HANDLE_VALUE_FIELD = 42008
FILE_STAT_SIZE_FIELD = 42009
FILE_STAT_KIND_FIELD = 42010
PLATFORM_INFO_ARCH_FIELD = 42011
PLATFORM_INFO_ENVIRONMENT_FIELD = 42012
CAPABILITY_BASE = 42100


@dataclass(frozen=True)
class CapabilityDeclaration:
    name: str
    kind: CapabilityKind
    rights: CapabilityRights
    effects: Effect
    parameters: tuple[tuple[str, int], ...]
    return_type: int


DECLARATIONS = (
    CapabilityDeclaration(
        "console_log",
        CapabilityKind.CONSOLE,
        CapabilityRights.LOG,
        Effect.IO,
        (("message", BYTES_TYPE),),
        VOID_TYPE,
    ),
    CapabilityDeclaration(
        "file_open",
        CapabilityKind.FILE,
        CapabilityRights.OPEN,
        Effect.IO,
        (("path", BYTES_TYPE), ("flags", U32_TYPE)),
        HANDLE_TYPE,
    ),
    CapabilityDeclaration(
        "file_read",
        CapabilityKind.FILE,
        CapabilityRights.READ,
        Effect.IO | Effect.WRITE_MEMORY,
        (("handle", HANDLE_TYPE), ("buffer", BYTES_TYPE)),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "file_write",
        CapabilityKind.FILE,
        CapabilityRights.WRITE,
        Effect.IO | Effect.READ_MEMORY,
        (("handle", HANDLE_TYPE), ("buffer", BYTES_TYPE)),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "file_stat",
        CapabilityKind.FILE,
        CapabilityRights.STAT,
        Effect.IO,
        (("path", BYTES_TYPE),),
        FILE_STAT_TYPE,
    ),
    CapabilityDeclaration(
        "file_list",
        CapabilityKind.FILE,
        CapabilityRights.LIST,
        Effect.IO,
        (("path", BYTES_TYPE),),
        BYTES_TYPE,
    ),
    CapabilityDeclaration(
        "file_close",
        CapabilityKind.FILE,
        CapabilityRights.CLOSE,
        Effect.IO,
        (("handle", HANDLE_TYPE),),
        VOID_TYPE,
    ),
    CapabilityDeclaration(
        "clock_monotonic",
        CapabilityKind.CLOCK,
        CapabilityRights.MONOTONIC,
        Effect.CLOCK,
        (),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "clock_wall",
        CapabilityKind.CLOCK,
        CapabilityRights.WALL,
        Effect.CLOCK,
        (),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "timer_schedule",
        CapabilityKind.TIMER,
        CapabilityRights.SCHEDULE,
        Effect.CLOCK,
        (("deadline", U64_TYPE),),
        HANDLE_TYPE,
    ),
    CapabilityDeclaration(
        "timer_cancel",
        CapabilityKind.TIMER,
        CapabilityRights.CLOSE,
        Effect.CLOCK,
        (("timer", HANDLE_TYPE),),
        BOOL_TYPE,
    ),
    CapabilityDeclaration(
        "random_fill",
        CapabilityKind.ENTROPY,
        CapabilityRights.RANDOM,
        Effect.IO | Effect.WRITE_MEMORY,
        (("buffer", BYTES_TYPE),),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "network_resolve",
        CapabilityKind.NETWORK,
        CapabilityRights.RESOLVE,
        Effect.IO,
        (("host", BYTES_TYPE),),
        BYTES_TYPE,
    ),
    CapabilityDeclaration(
        "network_connect",
        CapabilityKind.NETWORK,
        CapabilityRights.CONNECT,
        Effect.IO,
        (("address", BYTES_TYPE),),
        HANDLE_TYPE,
    ),
    CapabilityDeclaration(
        "network_listen",
        CapabilityKind.NETWORK,
        CapabilityRights.LISTEN,
        Effect.IO,
        (("address", BYTES_TYPE),),
        HANDLE_TYPE,
    ),
    CapabilityDeclaration(
        "network_send",
        CapabilityKind.NETWORK,
        CapabilityRights.SEND,
        Effect.IO | Effect.READ_MEMORY,
        (("socket", HANDLE_TYPE), ("buffer", BYTES_TYPE)),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "network_receive",
        CapabilityKind.NETWORK,
        CapabilityRights.RECEIVE,
        Effect.IO | Effect.WRITE_MEMORY,
        (("socket", HANDLE_TYPE), ("buffer", BYTES_TYPE)),
        U64_TYPE,
    ),
    CapabilityDeclaration(
        "network_close",
        CapabilityKind.NETWORK,
        CapabilityRights.CLOSE,
        Effect.IO,
        (("socket", HANDLE_TYPE),),
        VOID_TYPE,
    ),
    CapabilityDeclaration(
        "environment_get",
        CapabilityKind.ENVIRONMENT,
        CapabilityRights.INSPECT,
        Effect.IO,
        (("name", BYTES_TYPE),),
        BYTES_TYPE,
    ),
    CapabilityDeclaration(
        "platform_info",
        CapabilityKind.PLATFORM,
        CapabilityRights.INSPECT,
        Effect.NONE,
        (),
        PLATFORM_INFO_TYPE,
    ),
)


def _module_node(module: ModuleObject) -> NodeDef:
    return NodeDef(
        module.id,
        module.name,
        NodeKind.MODULE,
        module.parent,
        type_id=MODULE_TYPE,
        data=module.to_payload(),
    )


def _type_node(value: TypeObject) -> NodeDef:
    return NodeDef(
        value.id,
        value.name,
        NodeKind.TYPE,
        CAPABILITY_MODULE_ID,
        type_id=TYPE_TYPE,
        data=value.to_payload(),
    )


def _field_node(value: FieldObject) -> NodeDef:
    return NodeDef(
        value.id,
        value.name,
        NodeKind.FIELD,
        value.owner_type,
        type_id=FIELD_TYPE,
        data=value.to_payload(),
    )


def capability_nodes() -> list[NodeDef]:
    nodes = [
        _module_node(
            ModuleObject(
                CAPABILITY_MODULE_ID,
                "capabilities",
                RXF_MODULE_ID,
                ModuleCategory.EXECUTION,
            )
        ),
        RefusalVariant(
            CAPABILITY_REFUSED_ID, "refused", CAPABILITY_MODULE_ID, 1
        ).to_node(),
        RefusalVariant(
            CAPABILITY_UNAVAILABLE_ID, "unavailable", CAPABILITY_MODULE_ID, 2
        ).to_node(),
        RefusalVariant(
            CAPABILITY_INVALID_ID, "invalid", CAPABILITY_MODULE_ID, 3
        ).to_node(),
        RefusalSet(
            CAPABILITY_REFUSAL_SET_ID,
            "CapabilityRefusals",
            CAPABILITY_MODULE_ID,
            (CAPABILITY_REFUSED_ID, CAPABILITY_UNAVAILABLE_ID, CAPABILITY_INVALID_ID),
        ).to_node(),
        _type_node(
            TypeObject(
                HANDLE_TYPE,
                "CapabilityHandle",
                TypeForm.STRUCT,
                8,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
                field_count=1,
            )
        ),
        _type_node(
            TypeObject(
                FILE_STAT_TYPE,
                "FileStat",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
                field_count=2,
            )
        ),
        _type_node(
            TypeObject(
                PLATFORM_INFO_TYPE,
                "PlatformInfo",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
                field_count=2,
            )
        ),
        _field_node(FieldObject(HANDLE_VALUE_FIELD, "value", HANDLE_TYPE, U64_TYPE, 0)),
        _field_node(
            FieldObject(FILE_STAT_SIZE_FIELD, "size", FILE_STAT_TYPE, U64_TYPE, 0)
        ),
        _field_node(
            FieldObject(FILE_STAT_KIND_FIELD, "kind", FILE_STAT_TYPE, U32_TYPE, 8)
        ),
        _field_node(
            FieldObject(
                PLATFORM_INFO_ARCH_FIELD,
                "architecture",
                PLATFORM_INFO_TYPE,
                U64_TYPE,
                0,
            )
        ),
        _field_node(
            FieldObject(
                PLATFORM_INFO_ENVIRONMENT_FIELD,
                "environment",
                PLATFORM_INFO_TYPE,
                U64_TYPE,
                8,
            )
        ),
    ]
    cursor = CAPABILITY_BASE
    for declaration in DECLARATIONS:
        function_id, signature_id, requirement_id, import_id = range(cursor, cursor + 4)
        cursor += 4
        parameter_ids = tuple(range(cursor, cursor + len(declaration.parameters)))
        cursor += len(parameter_ids)
        result_id = cursor
        cursor += 1
        nodes.extend(
            ParameterObject(
                parameter_id, name, signature_id, value_type, index
            ).to_node()
            for index, (parameter_id, (name, value_type)) in enumerate(
                zip(parameter_ids, declaration.parameters, strict=True)
            )
        )
        nodes.append(
            ResultObject(
                result_id, "result", signature_id, declaration.return_type, 0
            ).to_node()
        )
        nodes.append(
            SignatureObject(
                signature_id,
                "signature",
                function_id,
                declaration.return_type,
                parameter_ids,
                (result_id,),
                CAPABILITY_REFUSAL_SET_ID,
            ).to_node()
        )
        nodes.append(
            FunctionObject(
                function_id,
                declaration.name,
                CAPABILITY_MODULE_ID,
                signature_id,
                FunctionImplementation.IMPORTED,
                FunctionLayer.FOUNDATION,
                declaration.effects,
            ).to_node()
        )
        nodes.append(
            CapabilityRequirement(
                requirement_id,
                f"{declaration.name}_requirement",
                CAPABILITY_MODULE_ID,
                declaration.kind,
                1,
                declaration.rights,
                declaration.effects,
                CAPABILITY_REFUSAL_SET_ID,
            ).to_node()
        )
        nodes.append(
            ImportObject(
                import_id,
                f"{declaration.name}_import",
                CAPABILITY_MODULE_ID,
                function_id,
                requirement_id=requirement_id,
            ).to_node()
        )
    return nodes


def toolchain_nodes() -> list[NodeDef]:
    """Additive toolchain corpus hook for the later starter merge."""
    return []
