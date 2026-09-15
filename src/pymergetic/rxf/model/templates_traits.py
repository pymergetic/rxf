"""Focused two-parameter specialization proof atop the numeric trait corpus."""

from pymergetic.rxf.model.execution import (
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    ParameterObject,
    SignatureObject,
)
from pymergetic.rxf.model.generics import (
    GenericArgument,
    GenericParameter,
    Specialization,
    Template,
    specialization_digest,
    specialization_id,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.ty.builtins import TEMPLATES_MODULE_ID, U16_TYPE, U32_TYPE

PAIR_TEMPLATE_FUNCTION_ID = 740
PAIR_TEMPLATE_SIGNATURE_ID = 741
PAIR_GENERIC_FIRST_ID = 742
PAIR_GENERIC_SECOND_ID = 743
PAIR_TEMPLATE_ID = 744
PAIR_U32_U16_FUNCTION_ID = 750
PAIR_U32_U16_SIGNATURE_ID = 751
PAIR_U32_U16_PARAMETER_ID = 752
PAIR_U32_U16_CODE_ID = 754

# Compatibility names retained for callers of the first stage.
IDENTITY_TEMPLATE_FUNCTION_ID = PAIR_TEMPLATE_FUNCTION_ID
IDENTITY_TEMPLATE_SIGNATURE_ID = PAIR_TEMPLATE_SIGNATURE_ID
IDENTITY_GENERIC_PARAMETER_ID = PAIR_GENERIC_FIRST_ID
IDENTITY_TEMPLATE_ID = PAIR_TEMPLATE_ID
IDENTITY_U32_FUNCTION_ID = PAIR_U32_U16_FUNCTION_ID
IDENTITY_U32_SIGNATURE_ID = PAIR_U32_U16_SIGNATURE_ID


def proving_nodes() -> list[NodeDef]:
    nodes = [
        ParameterObject(
            755,
            "value",
            PAIR_TEMPLATE_SIGNATURE_ID,
            PAIR_GENERIC_FIRST_ID,
            0,
        ).to_node(),
        SignatureObject(
            PAIR_TEMPLATE_SIGNATURE_ID,
            "signature",
            PAIR_TEMPLATE_FUNCTION_ID,
            PAIR_GENERIC_FIRST_ID,
            (755,),
        ).to_node(),
        FunctionObject(
            PAIR_TEMPLATE_FUNCTION_ID,
            "project_first",
            TEMPLATES_MODULE_ID,
            PAIR_TEMPLATE_SIGNATURE_ID,
            FunctionImplementation.ABSTRACT,
            FunctionLayer.LIBRARY,
        ).to_node(),
        GenericParameter(PAIR_GENERIC_FIRST_ID, "T", PAIR_TEMPLATE_ID, 0).to_node(),
        GenericParameter(PAIR_GENERIC_SECOND_ID, "U", PAIR_TEMPLATE_ID, 1).to_node(),
        Template(
            PAIR_TEMPLATE_ID,
            "project_first_template",
            TEMPLATES_MODULE_ID,
            PAIR_TEMPLATE_FUNCTION_ID,
            (PAIR_GENERIC_FIRST_ID, PAIR_GENERIC_SECOND_ID),
        ).to_node(),
        ParameterObject(
            PAIR_U32_U16_PARAMETER_ID,
            "value",
            PAIR_U32_U16_SIGNATURE_ID,
            U32_TYPE,
            0,
        ).to_node(),
        SignatureObject(
            PAIR_U32_U16_SIGNATURE_ID,
            "signature",
            PAIR_U32_U16_FUNCTION_ID,
            U32_TYPE,
            (PAIR_U32_U16_PARAMETER_ID,),
        ).to_node(),
    ]
    digest = specialization_digest(
        PAIR_TEMPLATE_ID,
        1,
        ((PAIR_GENERIC_SECOND_ID, U16_TYPE), (PAIR_GENERIC_FIRST_ID, U32_TYPE)),
        (PAIR_GENERIC_FIRST_ID, PAIR_GENERIC_SECOND_ID),
    )
    specialization_node_id = specialization_id(digest)
    first_argument = specialization_node_id ^ 1
    second_argument = specialization_node_id ^ 2
    function = FunctionObject(
        PAIR_U32_U16_FUNCTION_ID,
        "project_first_uint32_t_uint16_t",
        TEMPLATES_MODULE_ID,
        PAIR_U32_U16_SIGNATURE_ID,
        FunctionImplementation.DECLARED,
        FunctionLayer.LIBRARY,
    ).to_node()
    nodes.extend(
        [
            GenericArgument(
                first_argument,
                "T_uint32_t",
                specialization_node_id,
                PAIR_GENERIC_FIRST_ID,
                U32_TYPE,
            ).to_node(),
            GenericArgument(
                second_argument,
                "U_uint16_t",
                specialization_node_id,
                PAIR_GENERIC_SECOND_ID,
                U16_TYPE,
            ).to_node(),
            function,
            Specialization(
                specialization_node_id,
                "project_first_uint32_t_uint16_t_specialization",
                TEMPLATES_MODULE_ID,
                PAIR_TEMPLATE_ID,
                1,
                PAIR_U32_U16_FUNCTION_ID,
                (first_argument, second_argument),
                digest,
            ).to_node(),
        ]
    )
    return nodes
