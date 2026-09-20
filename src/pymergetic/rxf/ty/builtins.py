"""Canonical built-in TYPE/FIELD object corpus."""

from __future__ import annotations

from dataclasses import dataclass

from pymergetic.rxf.model.module import (
    ModuleCategory,
    ModuleFlags,
    ModuleObject,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.objects import FieldObject, TypeFlags, TypeForm, TypeObject

TYPE_TYPE = 1
FIELD_TYPE = 2
VOID_TYPE = 3
BOOL_TYPE = 4
U8_TYPE = 5
U16_TYPE = 6
U32_TYPE = 7
U64_TYPE = 8
I32_TYPE = 9
F64_TYPE = 10
BYTES_TYPE = 11
REF_TYPE = 12
OWNER_TYPE = 13
FUNCTION_TYPE = 14
SIGNATURE_TYPE = 15
PARAMETER_TYPE = 16
CALL_TYPE = 17
ARGUMENT_TYPE = 18
VALUE_TYPE = 19
CODE_TYPE = 20
TARGET_TYPE = 21
IMPORT_TYPE = 22
RELOCATION_TYPE = 23
CFG_BLOCK_TYPE = 24
CFG_OP_TYPE = 25
ARRAY_TYPE = 26
MODULE_TYPE = 27
I8_TYPE = 28
I16_TYPE = 29
I64_TYPE = 30
F32_TYPE = 31
BOOTSTRAP_OWNER_ID = 32
TYPE_FORM_FIELD = 33
TYPE_SIZE_FIELD = 34
TYPE_ALIGN_FIELD = 35
TYPE_INNER_FIELD = 36
TYPE_RETURN_FIELD = 37
TYPE_FLAGS_FIELD = 38
TYPE_FIELD_COUNT_FIELD = 39
TYPE_TAG_FIELD = 40
FIELD_OWNER_FIELD = 41
FIELD_VALUE_TYPE_FIELD = 42
FIELD_OFFSET_FIELD = 43
FIELD_COUNT_FIELD = 44
FIELD_FLAGS_FIELD = 45
FIELD_TAG_FIELD = 46
MODULE_CATEGORY_FIELD = 47
MODULE_FLAGS_FIELD = 48
GENERIC_PARAMETER_TYPE = 49
GENERIC_ARGUMENT_TYPE = 50
TEMPLATE_TYPE = 51
TRAIT_TYPE = 52
TRAIT_REQUIREMENT_TYPE = 53
ASSOCIATED_TYPE_TYPE = 54
CONFORMANCE_TYPE = 55
IMPLEMENTATION_BINDING_TYPE = 56
SPECIALIZATION_TYPE = 57
ARCHITECTURE_TYPE = 59
ABI_TYPE = 60
ENVIRONMENT_TYPE = 61
FEATURE_TYPE = 62
FEATURE_SET_TYPE = 63
PYMERGETIC_MODULE_ID = 64
RXF_MODULE_ID = 65
PRIMITIVES_MODULE_ID = 66
MODEL_MODULE_ID = 67
EXECUTION_MODULE_ID = 68
BASIC_MODULE_ID = 69
GENERICS_MODULE_ID = 70
TRAITS_MODEL_MODULE_ID = 71
TRAITS_MODULE_ID = 72
TEMPLATES_MODULE_ID = 73
NUMERIC_MODULE_ID = 74
RUNTIME_TARGET_TYPE = 75
TARGETS_MODULE_ID = 76
RESULT_TYPE = 151
REFUSAL_SET_TYPE = 152
REFUSAL_VARIANT_TYPE = 153
NUMERIC_CONTRACT_TYPE = 154
ABI_SIGNATURE_TYPE = 155
COMPILER_PROVENANCE_TYPE = 156
ABI_VALUE_LOCATION_TYPE = 177
CAPABILITY_REQUIREMENT_TYPE = 167
CAPABILITY_KIND_FIELD = 168
CAPABILITY_VERSION_FIELD = 169
CAPABILITY_RIGHTS_FIELD = 170
CAPABILITY_EFFECTS_FIELD = 171
CAPABILITY_TARGET_COUNT_FIELD = 172
CAPABILITY_ENVIRONMENT_COUNT_FIELD = 173
CAPABILITY_POLICY_SIZE_FIELD = 174
CAPABILITY_DIGEST_FIELD = 175
CAPABILITY_REFUSAL_SET_FIELD = 176
GENERIC_PARAMETER_INDEX_FIELD = 80
GENERIC_PARAMETER_KIND_FIELD = 81
GENERIC_PARAMETER_FLAGS_FIELD = 82
GENERIC_ARGUMENT_PARAMETER_FIELD = 83
GENERIC_ARGUMENT_BOUND_TYPE_FIELD = 84
GENERIC_ARGUMENT_KIND_FIELD = 85
TEMPLATE_FUNCTION_FIELD = 86
TEMPLATE_VERSION_FIELD = 87
TEMPLATE_PARAMETER_COUNT_FIELD = 88
TEMPLATE_FLAGS_FIELD = 89
TRAIT_REQUIREMENT_COUNT_FIELD = 90
TRAIT_ASSOCIATED_COUNT_FIELD = 91
TRAIT_FLAGS_FIELD = 92
REQUIREMENT_FUNCTION_FIELD = 93
REQUIREMENT_INDEX_FIELD = 94
REQUIREMENT_KIND_FIELD = 95
ASSOCIATED_INDEX_FIELD = 96
ASSOCIATED_FLAGS_FIELD = 97
CONFORMANCE_TRAIT_FIELD = 98
CONFORMANCE_TYPE_FIELD = 99
CONFORMANCE_BINDING_COUNT_FIELD = 100
CONFORMANCE_FLAGS_FIELD = 101
BINDING_REQUIREMENT_FIELD = 102
BINDING_IMPLEMENTATION_FIELD = 103
BINDING_KIND_FIELD = 104
SPECIALIZATION_TEMPLATE_FIELD = 105
SPECIALIZATION_VERSION_FIELD = 106
SPECIALIZATION_ARGUMENT_COUNT_FIELD = 107
SPECIALIZATION_FUNCTION_FIELD = 108
SPECIALIZATION_FLAGS_FIELD = 109
SPECIALIZATION_DIGEST_FIELD = 110
TRAIT_SUPERTRAIT_COUNT_FIELD = 111
ARCHITECTURE_KIND_FIELD = 118
ARCHITECTURE_WORD_BITS_FIELD = 119
ARCHITECTURE_ENDIANNESS_FIELD = 120
ARCHITECTURE_FLAGS_FIELD = 121
ABI_KIND_FIELD = 122
ABI_VERSION_FIELD = 123
ABI_CALLING_CONVENTION_FIELD = 124
ABI_FLAGS_FIELD = 125
ABI_ARCHITECTURE_FIELD = 126
ENVIRONMENT_KIND_FIELD = 127
ENVIRONMENT_VERSION_FIELD = 128
ENVIRONMENT_FLAGS_FIELD = 129
FEATURE_KIND_FIELD = 130
FEATURE_VERSION_FIELD = 131
FEATURE_FLAGS_FIELD = 132
FEATURE_ARCHITECTURE_FIELD = 133
FEATURE_SET_COUNT_FIELD = 134
FEATURE_SET_FLAGS_FIELD = 135
RUNTIME_TARGET_ARCHITECTURE_FIELD = 136
RUNTIME_TARGET_ABI_FIELD = 137
RUNTIME_TARGET_ENVIRONMENT_FIELD = 138
RUNTIME_TARGET_FEATURE_SET_FIELD = 139
RUNTIME_TARGET_FLAGS_FIELD = 140
CODE_OWNER_FUNCTION_FIELD = 141
CODE_RUNTIME_TARGET_FIELD = 142
CODE_FORMAT_FIELD = 143
CODE_FLAGS_FIELD = 144
CODE_ENTRY_OFFSET_FIELD = 145
CODE_BYTE_COUNT_FIELD = 146
CODE_SIGNATURE_FIELD = 147
CODE_EFFECTS_FIELD = 148
CODE_SEMANTIC_VERSION_FIELD = 149
CODE_SEMANTIC_DIGEST_FIELD = 150
RESULT_VALUE_TYPE_FIELD = 157
RESULT_INDEX_FIELD = 158
REFUSAL_CODE_FIELD = 159
REFUSAL_SET_COUNT_FIELD = 160
NUMERIC_OPERATION_FIELD = 161
NUMERIC_POLICY_FIELD = 162
ABI_SIGNATURE_TARGET_FIELD = 163
ABI_SIGNATURE_STATUS_FIELD = 164
ABI_SIGNATURE_OUTPUT_FIELD = 165
COMPILER_VERSION_FIELD = 166
FIRST_USER_ID = 256


@dataclass(frozen=True)
class BuiltinSpec:
    id: int
    name: str
    form: TypeForm
    size: int = 0
    align: int = 1
    flags: TypeFlags = TypeFlags.NONE


BUILTINS = (
    BuiltinSpec(TYPE_TYPE, "Type", TypeForm.META, 48, 8, TypeFlags.SELF_DESCRIBING),
    BuiltinSpec(FIELD_TYPE, "Field", TypeForm.STRUCT, 48, 8),
    BuiltinSpec(VOID_TYPE, "void", TypeForm.VOID),
    BuiltinSpec(BOOL_TYPE, "bool", TypeForm.BOOL, 1, 1),
    BuiltinSpec(U8_TYPE, "uint8_t", TypeForm.UINT, 1, 1),
    BuiltinSpec(U16_TYPE, "uint16_t", TypeForm.UINT, 2, 2),
    BuiltinSpec(U32_TYPE, "uint32_t", TypeForm.UINT, 4, 4),
    BuiltinSpec(U64_TYPE, "uint64_t", TypeForm.UINT, 8, 8),
    BuiltinSpec(I32_TYPE, "int32_t", TypeForm.SINT, 4, 4, TypeFlags.SIGNED),
    BuiltinSpec(F64_TYPE, "double", TypeForm.FLOAT, 8, 8),
    BuiltinSpec(BYTES_TYPE, "Bytes", TypeForm.BYTES, flags=TypeFlags.VARIABLE_SIZE),
    BuiltinSpec(REF_TYPE, "Ref", TypeForm.REF, 24, 8),
    BuiltinSpec(OWNER_TYPE, "Owner", TypeForm.STRUCT, 16, 8),
    BuiltinSpec(FUNCTION_TYPE, "Function", TypeForm.STRUCT, 72, 8),
    BuiltinSpec(SIGNATURE_TYPE, "Signature", TypeForm.FUNCTION, 32, 8),
    BuiltinSpec(PARAMETER_TYPE, "Parameter", TypeForm.STRUCT, 24, 8),
    BuiltinSpec(CALL_TYPE, "Call", TypeForm.EXPRESSION, 24, 8),
    BuiltinSpec(ARGUMENT_TYPE, "Argument", TypeForm.STRUCT, 16, 8),
    BuiltinSpec(
        VALUE_TYPE, "Value", TypeForm.EXPRESSION, flags=TypeFlags.VARIABLE_SIZE
    ),
    BuiltinSpec(CODE_TYPE, "Code", TypeForm.OPAQUE, flags=TypeFlags.VARIABLE_SIZE),
    BuiltinSpec(TARGET_TYPE, "Target", TypeForm.STRUCT, 24, 4),
    BuiltinSpec(IMPORT_TYPE, "Import", TypeForm.STRUCT, 16, 8),
    BuiltinSpec(RELOCATION_TYPE, "Relocation", TypeForm.STRUCT, 32, 8),
    BuiltinSpec(CFG_BLOCK_TYPE, "CfgBlock", TypeForm.EXECUTION),
    BuiltinSpec(CFG_OP_TYPE, "CfgOp", TypeForm.EXECUTION),
    BuiltinSpec(ARRAY_TYPE, "Array", TypeForm.ARRAY, flags=TypeFlags.VARIABLE_SIZE),
    BuiltinSpec(
        MODULE_TYPE, "Module", TypeForm.STRUCT, 8, 4, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(I8_TYPE, "int8_t", TypeForm.SINT, 1, 1, TypeFlags.SIGNED),
    BuiltinSpec(I16_TYPE, "int16_t", TypeForm.SINT, 2, 2, TypeFlags.SIGNED),
    BuiltinSpec(I64_TYPE, "int64_t", TypeForm.SINT, 8, 8, TypeFlags.SIGNED),
    BuiltinSpec(F32_TYPE, "float", TypeForm.FLOAT, 4, 4),
    BuiltinSpec(
        GENERIC_PARAMETER_TYPE,
        "GenericParameter",
        TypeForm.STRUCT,
        16,
        4,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        GENERIC_ARGUMENT_TYPE,
        "GenericArgument",
        TypeForm.STRUCT,
        24,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        TEMPLATE_TYPE, "Template", TypeForm.STRUCT, 24, 8, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(TRAIT_TYPE, "Trait", TypeForm.STRUCT, 16, 4, TypeFlags.SELF_DESCRIBING),
    BuiltinSpec(
        TRAIT_REQUIREMENT_TYPE,
        "TraitRequirement",
        TypeForm.STRUCT,
        24,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        ASSOCIATED_TYPE_TYPE,
        "AssociatedType",
        TypeForm.STRUCT,
        16,
        4,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        CONFORMANCE_TYPE,
        "Conformance",
        TypeForm.STRUCT,
        32,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        IMPLEMENTATION_BINDING_TYPE,
        "ImplementationBinding",
        TypeForm.STRUCT,
        24,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        SPECIALIZATION_TYPE,
        "Specialization",
        TypeForm.STRUCT,
        64,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        ARCHITECTURE_TYPE,
        "Architecture",
        TypeForm.STRUCT,
        16,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(ABI_TYPE, "ABI", TypeForm.STRUCT, 24, 8, TypeFlags.SELF_DESCRIBING),
    BuiltinSpec(
        ENVIRONMENT_TYPE,
        "Environment",
        TypeForm.STRUCT,
        16,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        FEATURE_TYPE, "Feature", TypeForm.STRUCT, 24, 8, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(
        FEATURE_SET_TYPE, "FeatureSet", TypeForm.STRUCT, 8, 4, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(
        RUNTIME_TARGET_TYPE,
        "RuntimeTarget",
        TypeForm.STRUCT,
        40,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        RESULT_TYPE, "Result", TypeForm.STRUCT, 16, 8, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(
        REFUSAL_SET_TYPE, "RefusalSet", TypeForm.STRUCT, 8, 4, TypeFlags.SELF_DESCRIBING
    ),
    BuiltinSpec(
        REFUSAL_VARIANT_TYPE,
        "RefusalVariant",
        TypeForm.STRUCT,
        8,
        4,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        NUMERIC_CONTRACT_TYPE,
        "NumericContract",
        TypeForm.STRUCT,
        48,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        ABI_SIGNATURE_TYPE,
        "ABISignature",
        TypeForm.STRUCT,
        48,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        COMPILER_PROVENANCE_TYPE,
        "CompilerProvenance",
        TypeForm.STRUCT,
        16,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        ABI_VALUE_LOCATION_TYPE,
        "ABIValueLocation",
        TypeForm.STRUCT,
        80,
        8,
        TypeFlags.SELF_DESCRIBING,
    ),
    BuiltinSpec(
        CAPABILITY_REQUIREMENT_TYPE,
        "CapabilityRequirement",
        TypeForm.STRUCT,
        72,
        8,
        TypeFlags.SELF_DESCRIBING | TypeFlags.VARIABLE_SIZE,
    ),
)


def builtin_nodes() -> list[NodeDef]:
    modules = (
        ModuleObject(PYMERGETIC_MODULE_ID, "pymergetic", 0, flags=ModuleFlags.PUBLIC),
        ModuleObject(
            RXF_MODULE_ID, "rxf", PYMERGETIC_MODULE_ID, flags=ModuleFlags.PUBLIC
        ),
        ModuleObject(PRIMITIVES_MODULE_ID, "primitives", RXF_MODULE_ID),
        ModuleObject(MODEL_MODULE_ID, "model", RXF_MODULE_ID, ModuleCategory.MODEL),
        ModuleObject(
            EXECUTION_MODULE_ID, "execution", RXF_MODULE_ID, ModuleCategory.EXECUTION
        ),
        ModuleObject(
            BASIC_MODULE_ID, "basic", EXECUTION_MODULE_ID, ModuleCategory.EXECUTION
        ),
        ModuleObject(
            GENERICS_MODULE_ID, "generics", MODEL_MODULE_ID, ModuleCategory.MODEL
        ),
        ModuleObject(
            TRAITS_MODEL_MODULE_ID, "traits", MODEL_MODULE_ID, ModuleCategory.MODEL
        ),
        ModuleObject(TRAITS_MODULE_ID, "traits", RXF_MODULE_ID, ModuleCategory.MODEL),
        ModuleObject(
            TEMPLATES_MODULE_ID, "templates", RXF_MODULE_ID, ModuleCategory.MODEL
        ),
        ModuleObject(
            NUMERIC_MODULE_ID, "numeric", RXF_MODULE_ID, ModuleCategory.EXECUTION
        ),
        ModuleObject(
            TARGETS_MODULE_ID, "targets", RXF_MODULE_ID, ModuleCategory.EXECUTION
        ),
    )
    nodes = [
        NodeDef(
            id=module.id,
            name=module.name,
            kind=NodeKind.MODULE,
            parent=module.parent,
            type_id=MODULE_TYPE,
            data=module.to_payload(),
        )
        for module in modules
    ]
    primitive_types = {
        VOID_TYPE,
        BOOL_TYPE,
        U8_TYPE,
        U16_TYPE,
        U32_TYPE,
        U64_TYPE,
        I8_TYPE,
        I16_TYPE,
        I32_TYPE,
        I64_TYPE,
        F32_TYPE,
        F64_TYPE,
    }
    generic_types = {
        GENERIC_PARAMETER_TYPE,
        GENERIC_ARGUMENT_TYPE,
        TEMPLATE_TYPE,
        SPECIALIZATION_TYPE,
    }
    trait_types = {
        TRAIT_TYPE,
        TRAIT_REQUIREMENT_TYPE,
        ASSOCIATED_TYPE_TYPE,
        CONFORMANCE_TYPE,
        IMPLEMENTATION_BINDING_TYPE,
    }
    execution_types = {
        FUNCTION_TYPE,
        SIGNATURE_TYPE,
        PARAMETER_TYPE,
        CALL_TYPE,
        ARGUMENT_TYPE,
        VALUE_TYPE,
        CODE_TYPE,
        TARGET_TYPE,
        IMPORT_TYPE,
        RELOCATION_TYPE,
        CFG_BLOCK_TYPE,
        CFG_OP_TYPE,
        ARCHITECTURE_TYPE,
        ABI_TYPE,
        ENVIRONMENT_TYPE,
        FEATURE_TYPE,
        FEATURE_SET_TYPE,
        RUNTIME_TARGET_TYPE,
        RESULT_TYPE,
        REFUSAL_SET_TYPE,
        REFUSAL_VARIANT_TYPE,
        NUMERIC_CONTRACT_TYPE,
        ABI_SIGNATURE_TYPE,
        COMPILER_PROVENANCE_TYPE,
        CAPABILITY_REQUIREMENT_TYPE,
    }
    for spec in BUILTINS:
        field_count = (
            8
            if spec.id == TYPE_TYPE
            else 6
            if spec.id == FIELD_TYPE
            else 2
            if spec.id == MODULE_TYPE
            else {
                CODE_TYPE: 10,
                ARCHITECTURE_TYPE: 4,
                ABI_TYPE: 5,
                ENVIRONMENT_TYPE: 3,
                FEATURE_TYPE: 4,
                FEATURE_SET_TYPE: 2,
                RUNTIME_TARGET_TYPE: 5,
                GENERIC_PARAMETER_TYPE: 3,
                GENERIC_ARGUMENT_TYPE: 3,
                TEMPLATE_TYPE: 4,
                TRAIT_TYPE: 4,
                TRAIT_REQUIREMENT_TYPE: 3,
                ASSOCIATED_TYPE_TYPE: 2,
                CONFORMANCE_TYPE: 4,
                IMPLEMENTATION_BINDING_TYPE: 3,
                SPECIALIZATION_TYPE: 6,
                CAPABILITY_REQUIREMENT_TYPE: 9,
            }.get(spec.id, 0)
        )
        descriptor = TypeObject(
            id=spec.id,
            name=spec.name,
            form=spec.form,
            size=spec.size,
            align=spec.align,
            flags=spec.flags,
            field_count=field_count,
        )
        parent = (
            PRIMITIVES_MODULE_ID
            if spec.id in primitive_types
            else GENERICS_MODULE_ID
            if spec.id in generic_types
            else TRAITS_MODEL_MODULE_ID
            if spec.id in trait_types
            else EXECUTION_MODULE_ID
            if spec.id in execution_types
            else MODEL_MODULE_ID
        )
        nodes.append(
            NodeDef(
                id=spec.id,
                name=spec.name,
                kind=NodeKind.TYPE,
                parent=parent,
                type_id=TYPE_TYPE,
                data=descriptor.to_payload(),
            )
        )
    bootstrap_fields = (
        FieldObject(TYPE_TAG_FIELD, "descriptor_tag", TYPE_TYPE, U32_TYPE, 0),
        FieldObject(TYPE_FORM_FIELD, "form", TYPE_TYPE, U32_TYPE, 4),
        FieldObject(TYPE_SIZE_FIELD, "size", TYPE_TYPE, U64_TYPE, 8),
        FieldObject(TYPE_ALIGN_FIELD, "align", TYPE_TYPE, U32_TYPE, 16),
        FieldObject(TYPE_INNER_FIELD, "inner_type", TYPE_TYPE, U64_TYPE, 24),
        FieldObject(TYPE_RETURN_FIELD, "return_type", TYPE_TYPE, U64_TYPE, 32),
        FieldObject(TYPE_FLAGS_FIELD, "flags", TYPE_TYPE, U32_TYPE, 40),
        FieldObject(TYPE_FIELD_COUNT_FIELD, "field_count", TYPE_TYPE, U32_TYPE, 44),
        FieldObject(FIELD_TAG_FIELD, "descriptor_tag", FIELD_TYPE, U32_TYPE, 0),
        FieldObject(FIELD_OWNER_FIELD, "owner_type", FIELD_TYPE, U64_TYPE, 8),
        FieldObject(FIELD_VALUE_TYPE_FIELD, "value_type", FIELD_TYPE, U64_TYPE, 16),
        FieldObject(FIELD_OFFSET_FIELD, "offset", FIELD_TYPE, U64_TYPE, 24),
        FieldObject(FIELD_COUNT_FIELD, "count", FIELD_TYPE, U64_TYPE, 32),
        FieldObject(FIELD_FLAGS_FIELD, "flags", FIELD_TYPE, U32_TYPE, 40),
        FieldObject(MODULE_CATEGORY_FIELD, "category", MODULE_TYPE, U32_TYPE, 0),
        FieldObject(MODULE_FLAGS_FIELD, "flags", MODULE_TYPE, U32_TYPE, 4),
        FieldObject(
            GENERIC_PARAMETER_INDEX_FIELD, "index", GENERIC_PARAMETER_TYPE, U32_TYPE, 0
        ),
        FieldObject(
            GENERIC_PARAMETER_KIND_FIELD, "kind", GENERIC_PARAMETER_TYPE, U32_TYPE, 4
        ),
        FieldObject(
            GENERIC_PARAMETER_FLAGS_FIELD, "flags", GENERIC_PARAMETER_TYPE, U32_TYPE, 8
        ),
        FieldObject(
            GENERIC_ARGUMENT_PARAMETER_FIELD,
            "parameter",
            GENERIC_ARGUMENT_TYPE,
            U64_TYPE,
            0,
        ),
        FieldObject(
            GENERIC_ARGUMENT_BOUND_TYPE_FIELD,
            "bound_type",
            GENERIC_ARGUMENT_TYPE,
            U64_TYPE,
            8,
        ),
        FieldObject(
            GENERIC_ARGUMENT_KIND_FIELD, "kind", GENERIC_ARGUMENT_TYPE, U32_TYPE, 16
        ),
        FieldObject(TEMPLATE_FUNCTION_FIELD, "function", TEMPLATE_TYPE, U64_TYPE, 0),
        FieldObject(TEMPLATE_VERSION_FIELD, "version", TEMPLATE_TYPE, U32_TYPE, 8),
        FieldObject(
            TEMPLATE_PARAMETER_COUNT_FIELD,
            "parameter_count",
            TEMPLATE_TYPE,
            U32_TYPE,
            12,
        ),
        FieldObject(TEMPLATE_FLAGS_FIELD, "flags", TEMPLATE_TYPE, U32_TYPE, 16),
        FieldObject(
            TRAIT_REQUIREMENT_COUNT_FIELD, "requirement_count", TRAIT_TYPE, U32_TYPE, 0
        ),
        FieldObject(
            TRAIT_ASSOCIATED_COUNT_FIELD,
            "associated_type_count",
            TRAIT_TYPE,
            U32_TYPE,
            4,
        ),
        FieldObject(
            TRAIT_SUPERTRAIT_COUNT_FIELD, "supertrait_count", TRAIT_TYPE, U32_TYPE, 8
        ),
        FieldObject(TRAIT_FLAGS_FIELD, "flags", TRAIT_TYPE, U32_TYPE, 12),
        FieldObject(
            REQUIREMENT_FUNCTION_FIELD, "function", TRAIT_REQUIREMENT_TYPE, U64_TYPE, 0
        ),
        FieldObject(
            REQUIREMENT_INDEX_FIELD, "index", TRAIT_REQUIREMENT_TYPE, U32_TYPE, 8
        ),
        FieldObject(
            REQUIREMENT_KIND_FIELD, "kind", TRAIT_REQUIREMENT_TYPE, U32_TYPE, 12
        ),
        FieldObject(ASSOCIATED_INDEX_FIELD, "index", ASSOCIATED_TYPE_TYPE, U32_TYPE, 0),
        FieldObject(ASSOCIATED_FLAGS_FIELD, "flags", ASSOCIATED_TYPE_TYPE, U32_TYPE, 4),
        FieldObject(CONFORMANCE_TRAIT_FIELD, "trait", CONFORMANCE_TYPE, U64_TYPE, 0),
        FieldObject(
            CONFORMANCE_TYPE_FIELD, "conforming_type", CONFORMANCE_TYPE, U64_TYPE, 8
        ),
        FieldObject(
            CONFORMANCE_BINDING_COUNT_FIELD,
            "binding_count",
            CONFORMANCE_TYPE,
            U32_TYPE,
            16,
        ),
        FieldObject(CONFORMANCE_FLAGS_FIELD, "flags", CONFORMANCE_TYPE, U32_TYPE, 20),
        FieldObject(
            BINDING_REQUIREMENT_FIELD,
            "requirement",
            IMPLEMENTATION_BINDING_TYPE,
            U64_TYPE,
            0,
        ),
        FieldObject(
            BINDING_IMPLEMENTATION_FIELD,
            "implementation",
            IMPLEMENTATION_BINDING_TYPE,
            U64_TYPE,
            8,
        ),
        FieldObject(
            BINDING_KIND_FIELD, "kind", IMPLEMENTATION_BINDING_TYPE, U32_TYPE, 16
        ),
        FieldObject(
            SPECIALIZATION_TEMPLATE_FIELD, "template", SPECIALIZATION_TYPE, U64_TYPE, 0
        ),
        FieldObject(
            SPECIALIZATION_VERSION_FIELD,
            "template_version",
            SPECIALIZATION_TYPE,
            U32_TYPE,
            8,
        ),
        FieldObject(
            SPECIALIZATION_ARGUMENT_COUNT_FIELD,
            "argument_count",
            SPECIALIZATION_TYPE,
            U32_TYPE,
            12,
        ),
        FieldObject(
            SPECIALIZATION_FUNCTION_FIELD, "function", SPECIALIZATION_TYPE, U64_TYPE, 16
        ),
        FieldObject(
            SPECIALIZATION_FLAGS_FIELD, "flags", SPECIALIZATION_TYPE, U32_TYPE, 24
        ),
        FieldObject(
            SPECIALIZATION_DIGEST_FIELD,
            "digest",
            SPECIALIZATION_TYPE,
            U8_TYPE,
            32,
            count=32,
        ),
        FieldObject(ARCHITECTURE_KIND_FIELD, "kind", ARCHITECTURE_TYPE, U32_TYPE, 0),
        FieldObject(
            ARCHITECTURE_WORD_BITS_FIELD, "word_bits", ARCHITECTURE_TYPE, U32_TYPE, 4
        ),
        FieldObject(
            ARCHITECTURE_ENDIANNESS_FIELD, "endianness", ARCHITECTURE_TYPE, U32_TYPE, 8
        ),
        FieldObject(ARCHITECTURE_FLAGS_FIELD, "flags", ARCHITECTURE_TYPE, U32_TYPE, 12),
        FieldObject(ABI_KIND_FIELD, "kind", ABI_TYPE, U32_TYPE, 0),
        FieldObject(ABI_VERSION_FIELD, "version", ABI_TYPE, U32_TYPE, 4),
        FieldObject(
            ABI_CALLING_CONVENTION_FIELD, "calling_convention", ABI_TYPE, U32_TYPE, 8
        ),
        FieldObject(ABI_FLAGS_FIELD, "flags", ABI_TYPE, U32_TYPE, 12),
        FieldObject(ABI_ARCHITECTURE_FIELD, "architecture", ABI_TYPE, U64_TYPE, 16),
        FieldObject(ENVIRONMENT_KIND_FIELD, "kind", ENVIRONMENT_TYPE, U32_TYPE, 0),
        FieldObject(
            ENVIRONMENT_VERSION_FIELD, "version", ENVIRONMENT_TYPE, U32_TYPE, 4
        ),
        FieldObject(ENVIRONMENT_FLAGS_FIELD, "flags", ENVIRONMENT_TYPE, U32_TYPE, 8),
        FieldObject(FEATURE_KIND_FIELD, "kind", FEATURE_TYPE, U32_TYPE, 0),
        FieldObject(FEATURE_VERSION_FIELD, "version", FEATURE_TYPE, U32_TYPE, 4),
        FieldObject(FEATURE_FLAGS_FIELD, "flags", FEATURE_TYPE, U32_TYPE, 8),
        FieldObject(
            FEATURE_ARCHITECTURE_FIELD, "architecture", FEATURE_TYPE, U64_TYPE, 16
        ),
        FieldObject(FEATURE_SET_COUNT_FIELD, "count", FEATURE_SET_TYPE, U32_TYPE, 0),
        FieldObject(FEATURE_SET_FLAGS_FIELD, "flags", FEATURE_SET_TYPE, U32_TYPE, 4),
        FieldObject(
            RUNTIME_TARGET_ARCHITECTURE_FIELD,
            "architecture",
            RUNTIME_TARGET_TYPE,
            U64_TYPE,
            0,
        ),
        FieldObject(RUNTIME_TARGET_ABI_FIELD, "abi", RUNTIME_TARGET_TYPE, U64_TYPE, 8),
        FieldObject(
            RUNTIME_TARGET_ENVIRONMENT_FIELD,
            "environment",
            RUNTIME_TARGET_TYPE,
            U64_TYPE,
            16,
        ),
        FieldObject(
            RUNTIME_TARGET_FEATURE_SET_FIELD,
            "feature_set",
            RUNTIME_TARGET_TYPE,
            U64_TYPE,
            24,
        ),
        FieldObject(
            RUNTIME_TARGET_FLAGS_FIELD, "flags", RUNTIME_TARGET_TYPE, U32_TYPE, 32
        ),
        FieldObject(
            CODE_OWNER_FUNCTION_FIELD, "owner_function", CODE_TYPE, U64_TYPE, 0
        ),
        FieldObject(
            CODE_RUNTIME_TARGET_FIELD, "runtime_target", CODE_TYPE, U64_TYPE, 8
        ),
        FieldObject(CODE_SIGNATURE_FIELD, "signature", CODE_TYPE, U64_TYPE, 16),
        FieldObject(CODE_FORMAT_FIELD, "format", CODE_TYPE, U32_TYPE, 24),
        FieldObject(CODE_EFFECTS_FIELD, "effects", CODE_TYPE, U32_TYPE, 28),
        FieldObject(
            CODE_SEMANTIC_VERSION_FIELD, "semantic_version", CODE_TYPE, U32_TYPE, 32
        ),
        FieldObject(CODE_FLAGS_FIELD, "flags", CODE_TYPE, U32_TYPE, 36),
        FieldObject(CODE_ENTRY_OFFSET_FIELD, "entry_offset", CODE_TYPE, U64_TYPE, 40),
        FieldObject(CODE_BYTE_COUNT_FIELD, "byte_count", CODE_TYPE, U64_TYPE, 48),
        FieldObject(
            CODE_SEMANTIC_DIGEST_FIELD,
            "semantic_digest",
            CODE_TYPE,
            U8_TYPE,
            56,
            count=32,
        ),
        FieldObject(
            CAPABILITY_KIND_FIELD, "kind", CAPABILITY_REQUIREMENT_TYPE, U32_TYPE, 0
        ),
        FieldObject(
            CAPABILITY_VERSION_FIELD,
            "version",
            CAPABILITY_REQUIREMENT_TYPE,
            U32_TYPE,
            4,
        ),
        FieldObject(
            CAPABILITY_RIGHTS_FIELD, "rights", CAPABILITY_REQUIREMENT_TYPE, U32_TYPE, 8
        ),
        FieldObject(
            CAPABILITY_EFFECTS_FIELD,
            "effects",
            CAPABILITY_REQUIREMENT_TYPE,
            U32_TYPE,
            12,
        ),
        FieldObject(
            CAPABILITY_TARGET_COUNT_FIELD,
            "target_count",
            CAPABILITY_REQUIREMENT_TYPE,
            U32_TYPE,
            16,
        ),
        FieldObject(
            CAPABILITY_ENVIRONMENT_COUNT_FIELD,
            "environment_count",
            CAPABILITY_REQUIREMENT_TYPE,
            U32_TYPE,
            20,
        ),
        FieldObject(
            CAPABILITY_POLICY_SIZE_FIELD,
            "policy_size",
            CAPABILITY_REQUIREMENT_TYPE,
            U32_TYPE,
            24,
        ),
        FieldObject(
            CAPABILITY_DIGEST_FIELD,
            "semantic_digest",
            CAPABILITY_REQUIREMENT_TYPE,
            U8_TYPE,
            28,
            count=32,
        ),
        FieldObject(
            CAPABILITY_REFUSAL_SET_FIELD,
            "refusal_set",
            CAPABILITY_REQUIREMENT_TYPE,
            U64_TYPE,
            64,
        ),
    )
    nodes.extend(
        NodeDef(
            id=f.id,
            name=f.name,
            kind=NodeKind.FIELD,
            parent=f.owner_type,
            type_id=FIELD_TYPE,
            data=f.to_payload(),
        )
        for f in bootstrap_fields
    )
    return nodes
