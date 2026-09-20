"""Deterministic stage 4-8 standard-library semantic corpus."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.generated_stdlib_native import STDLIB_AARCH64, STDLIB_X86_64
from pymergetic.rxf.model.contracts import (
    ABIClass,
    ABIKind,
    ABIPassingMode,
    ABIRegisterBank,
    ABIRole,
    ABISignature,
    ABIValueLocation,
    CompilerProvenance,
    RefusalSet,
    RefusalVariant,
    ResultSlot,
)
from pymergetic.rxf.model.execution import (
    ArgumentObject,
    CallObject,
    CodeFormat,
    CodeObject,
    Effect,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    ParameterObject,
    SignatureObject,
    ValueKind,
    ValueObject,
    semantic_digest,
)
from pymergetic.rxf.model.generics import GenericParameter, Template
from pymergetic.rxf.model.module import ModuleCategory, ModuleObject
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.numeric import numeric_nodes
from pymergetic.rxf.model.stdlib import (
    CanonicalOrderAuthority,
    CanonicalOrderPolicy,
    HashAuthority,
    HashStorageAuthority,
    ObjectHandle,
    RefusalDefaultPolicy,
    RefusalEnrichment,
    RefusalMapping,
    RefusalMappingEntry,
    StdlibFlags,
    StdlibRecord,
    StdlibRecordKind,
    TaggedVariant,
    specialize_value_type,
)
from pymergetic.rxf.model.stdlib_semantics import (
    SemanticBlock,
    SemanticGraph,
    SemanticOpcode,
    SemanticOperation,
    SemanticValue,
    SemanticValueKind,
    TerminatorKind,
)
from pymergetic.rxf.model.target import (
    AARCH64_LINUX_TARGET_ID,
    AARCH64_UEFI_TARGET_ID,
    X86_64_LINUX_TARGET_ID,
    X86_64_UEFI_TARGET_ID,
)
from pymergetic.rxf.model.traits import Conformance, Trait
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.builtins import (
    BOOL_TYPE,
    BYTES_TYPE,
    FIELD_TYPE,
    FUNCTION_TYPE,
    I32_TYPE,
    MODULE_TYPE,
    REF_TYPE,
    RXF_MODULE_ID,
    TYPE_TYPE,
    U8_TYPE,
    U32_TYPE,
    U64_TYPE,
    VOID_TYPE,
)
from pymergetic.rxf.ty.objects import (
    FieldFlags,
    FieldObject,
    TypeFlags,
    TypeForm,
    TypeObject,
)

STDLIB_FOUNDATION_MODULE_ID = 41000
STDLIB_BASIC_MODULE_ID = 41001
STDLIB_LIBRARY_MODULE_ID = 41002
STDLIB_RECORD_TYPE = 41003
STDLIB_REFUSAL_SET_ID = 41004
STDLIB_SEMANTIC_GRAPH_TYPE = 41006
STDLIB_SEMANTIC_BLOCK_TYPE = 41007
STDLIB_SEMANTIC_OPERATION_TYPE = 41008
STDLIB_SEMANTIC_VALUE_TYPE = 41009
STDLIB_TAGGED_VARIANT_TYPE = 41010
STDLIB_REFUSAL_MAPPING_TYPE = 41011
STDLIB_REFUSAL_MAPPING_ENTRY_TYPE = 41012
STDLIB_REFUSAL_ENRICHMENT_TYPE = 41013
STDLIB_CANONICAL_ORDER_AUTHORITY_TYPE = 41014
NATIVE_TRANSACTION_TYPE = 41015
STDLIB_HASH_STORAGE_AUTHORITY_TYPE = 41016
STDLIB_HASH_AUTHORITY_TYPE = 41020
HASH_BUCKET_STATE_TYPE = 41017
HASH_MAP_BUCKET_TYPE = 41018
HASH_SET_BUCKET_TYPE = 41019
OPTION_U32_TYPE = 0xDD_D6E5_05AA_259D_98
OPTION_U64_TYPE = 0xDD_D6E5_05AA_259D_99
OPTION_REF_TYPE = 0xDD_D6E5_05AA_259D_9A
TYPED_RESULT_U64_U32_TYPE = 0xDD_D6E5_05AA_259D_9B
STDLIB_HASH_AUTHORITY_ID = 89990
STDLIB_RECEIPT_MODULE_ID = 90000
STDLIB_RECEIPT_FUNCTION_ID = 90001
STDLIB_RECEIPT_EXPECTED_ID = 90002
STDLIB_RECEIPT_BYTES = b"Receipt total: 570\n"
STDLIB_AARCH64_LINUX_ABI_BASE = 30_000_000
STDLIB_AARCH64_LINUX_LOCATION_BASE = 31_000_000
STDLIB_AARCH64_LINUX_CODE_BASE = 32_000_000
STDLIB_X86_64_UEFI_ABI_BASE = 33_000_000
STDLIB_X86_64_UEFI_LOCATION_BASE = 34_000_000
STDLIB_X86_64_UEFI_CODE_BASE = 35_000_000

NATIVE_SIGNATURES = {
    "memory_copy": ((REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE, U64_TYPE), U64_TYPE),
    "memory_move": ((REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE, U64_TYPE), U64_TYPE),
    "memory_fill": ((REF_TYPE, U64_TYPE, U8_TYPE, U64_TYPE), U64_TYPE),
    "memory_zero": ((REF_TYPE, U64_TYPE, U64_TYPE), U64_TYPE),
    "memory_compare": ((REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE, U64_TYPE), I32_TYPE),
    "memory_load_u32": ((REF_TYPE, U64_TYPE, U64_TYPE, U32_TYPE, U32_TYPE), U32_TYPE),
    "memory_store_u32": (
        (REF_TYPE, U64_TYPE, U64_TYPE, U32_TYPE, U32_TYPE, U32_TYPE),
        U64_TYPE,
    ),
    "utf8_validate": ((REF_TYPE, U64_TYPE), U64_TYPE),
    "hash_bytes": ((REF_TYPE, U64_TYPE, U64_TYPE), U64_TYPE),
}
HASH_NATIVE_SIGNATURES = {
    "hash_u64": ((U64_TYPE, U64_TYPE), U64_TYPE),
    "runtime_hash_map_lookup": (
        (REF_TYPE, REF_TYPE, NATIVE_TRANSACTION_TYPE, U64_TYPE, U64_TYPE),
        OPTION_REF_TYPE,
    ),
    "runtime_hash_set_lookup": (
        (REF_TYPE, REF_TYPE, NATIVE_TRANSACTION_TYPE, U64_TYPE, U64_TYPE),
        OPTION_U64_TYPE,
    ),
}
RUNTIME_SIGNATURES = {
    "runtime_begin_private": ((REF_TYPE, REF_TYPE, U64_TYPE), NATIVE_TRANSACTION_TYPE),
    "runtime_resolve": ((REF_TYPE, REF_TYPE, U64_TYPE), U64_TYPE),
    "runtime_resolve_typed": (
        (REF_TYPE, REF_TYPE, REF_TYPE, U64_TYPE, U64_TYPE),
        U64_TYPE,
    ),
    "runtime_allocate_prepare": (
        (
            REF_TYPE,
            U64_TYPE,
            U64_TYPE,
            U64_TYPE,
            U64_TYPE,
            U64_TYPE,
            NATIVE_TRANSACTION_TYPE,
        ),
        REF_TYPE,
    ),
    "runtime_publish": ((REF_TYPE, NATIVE_TRANSACTION_TYPE), VOID_TYPE),
    "runtime_rollback": ((REF_TYPE, NATIVE_TRANSACTION_TYPE), VOID_TYPE),
    "runtime_release": ((REF_TYPE, REF_TYPE), VOID_TYPE),
    "runtime_borrow": ((REF_TYPE, REF_TYPE, U64_TYPE), VOID_TYPE),
    "runtime_release_borrow": ((REF_TYPE, REF_TYPE, U64_TYPE), VOID_TYPE),
    "runtime_pin": ((REF_TYPE, REF_TYPE), VOID_TYPE),
    "runtime_unpin": ((REF_TYPE, REF_TYPE), VOID_TYPE),
    "runtime_read": ((REF_TYPE, REF_TYPE, REF_TYPE, U64_TYPE, U64_TYPE), U64_TYPE),
    "runtime_write": (
        (REF_TYPE, REF_TYPE, REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE),
        VOID_TYPE,
    ),
    "runtime_reserve": ((REF_TYPE, REF_TYPE, REF_TYPE, U64_TYPE), VOID_TYPE),
    "runtime_append": ((REF_TYPE, REF_TYPE, REF_TYPE, REF_TYPE, U64_TYPE), VOID_TYPE),
    "runtime_format_u32": ((REF_TYPE, REF_TYPE, REF_TYPE, U32_TYPE), VOID_TYPE),
    "runtime_search": (
        (REF_TYPE, REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE, REF_TYPE),
        U64_TYPE,
    ),
    "runtime_cleanup": ((REF_TYPE, NATIVE_TRANSACTION_TYPE), VOID_TYPE),
}
RUNTIME_CONTEXT_FUNCTIONS = frozenset(
    {
        "runtime_hash_map_lookup",
        "runtime_hash_set_lookup",
        "runtime_begin_private",
        "runtime_resolve",
        "runtime_resolve_typed",
        "runtime_allocate_prepare",
        "runtime_publish",
        "runtime_rollback",
        "runtime_release",
        "runtime_borrow",
        "runtime_release_borrow",
        "runtime_pin",
        "runtime_unpin",
        "runtime_read",
        "runtime_write",
        "runtime_reserve",
        "runtime_append",
        "runtime_format_u32",
        "runtime_search",
        "runtime_cleanup",
    }
)
STATUS_ONLY_NATIVE_FUNCTIONS = frozenset(
    {
        "runtime_publish",
        "runtime_rollback",
        "runtime_cleanup",
        "runtime_write",
        "runtime_append",
        "runtime_format_u32",
        "runtime_pin",
        "runtime_unpin",
        "runtime_borrow",
        "runtime_release_borrow",
        "runtime_release",
        "runtime_reserve",
        "memory_copy",
        "memory_move",
        "memory_fill",
        "memory_zero",
        "memory_store_u32",
    }
)
NATIVE_OUTPUT_FUNCTIONS = frozenset(
    {
        "memory_compare",
        "memory_load_u32",
        "utf8_validate",
        "hash_bytes",
        "hash_u64",
        "runtime_hash_map_lookup",
        "runtime_hash_set_lookup",
        "runtime_begin_private",
        "runtime_resolve",
        "runtime_resolve_typed",
        "runtime_allocate_prepare",
        "runtime_read",
        "runtime_search",
    }
)
RUNTIME_HANDLE_PARAMETERS = {
    "runtime_hash_map_lookup": frozenset({1}),
    "runtime_hash_set_lookup": frozenset({1}),
    "runtime_resolve": frozenset({1}),
    "runtime_resolve_typed": frozenset({1}),
    "runtime_release": frozenset({1}),
    "runtime_borrow": frozenset({1}),
    "runtime_release_borrow": frozenset({1}),
    "runtime_pin": frozenset({1}),
    "runtime_unpin": frozenset({1}),
    "runtime_read": frozenset({1}),
    "runtime_write": frozenset({1}),
    "runtime_reserve": frozenset({1}),
    "runtime_append": frozenset({1}),
    "runtime_format_u32": frozenset({1}),
    "runtime_search": frozenset({1, 3}),
}
TRANSACTION_PARAMETERS = {
    "runtime_hash_map_lookup": frozenset({2}),
    "runtime_hash_set_lookup": frozenset({2}),
    "runtime_allocate_prepare": frozenset({6}),
    "runtime_resolve_typed": frozenset({2}),
    "runtime_read": frozenset({2}),
    "runtime_write": frozenset({2}),
    "runtime_reserve": frozenset({2}),
    "runtime_append": frozenset({2}),
    "runtime_format_u32": frozenset({2}),
    "runtime_search": frozenset({5}),
    "runtime_publish": frozenset({1}),
    "runtime_rollback": frozenset({1}),
    "runtime_cleanup": frozenset({1}),
}

NATIVE_SIGNATURES.update(HASH_NATIVE_SIGNATURES)
NATIVE_SIGNATURES.update(RUNTIME_SIGNATURES)
COMPOSED_SIGNATURES = {
    "array_get": ((REF_TYPE, U64_TYPE), OPTION_U64_TYPE),
    "hash_map_get": ((REF_TYPE, U64_TYPE, NATIVE_TRANSACTION_TYPE), OPTION_REF_TYPE),
    "hash_set_contains": (
        (REF_TYPE, U64_TYPE, NATIVE_TRANSACTION_TYPE),
        OPTION_U64_TYPE,
    ),
    "find": ((REF_TYPE, U64_TYPE), OPTION_U64_TYPE),
    "binary_search": ((REF_TYPE, U64_TYPE), OPTION_U64_TYPE),
    "propagate": (
        (TYPED_RESULT_U64_U32_TYPE, NATIVE_TRANSACTION_TYPE),
        TYPED_RESULT_U64_U32_TYPE,
    ),
    "map_success": (
        (TYPED_RESULT_U64_U32_TYPE, NATIVE_TRANSACTION_TYPE),
        TYPED_RESULT_U64_U32_TYPE,
    ),
    "map_refusal": (
        (TYPED_RESULT_U64_U32_TYPE, NATIVE_TRANSACTION_TYPE),
        TYPED_RESULT_U64_U32_TYPE,
    ),
    "catch_refusal": (
        (TYPED_RESULT_U64_U32_TYPE, NATIVE_TRANSACTION_TYPE),
        TYPED_RESULT_U64_U32_TYPE,
    ),
    "enrich_refusal": (
        (TYPED_RESULT_U64_U32_TYPE, NATIVE_TRANSACTION_TYPE),
        TYPED_RESULT_U64_U32_TYPE,
    ),
    "cleanup": ((NATIVE_TRANSACTION_TYPE,), U64_TYPE),
    "defer": ((NATIVE_TRANSACTION_TYPE,), U64_TYPE),
    "string_find": ((REF_TYPE, U64_TYPE, REF_TYPE, U64_TYPE), U64_TYPE),
    "stable_sort": ((REF_TYPE, U64_TYPE, FUNCTION_TYPE), REF_TYPE),
    "map": ((U64_TYPE, FUNCTION_TYPE), U64_TYPE),
    "filter": ((U64_TYPE, FUNCTION_TYPE), U64_TYPE),
    "fold": ((U64_TYPE, U64_TYPE, FUNCTION_TYPE), U64_TYPE),
}
COMPOSED_ALIASES = {
    "copy": "memory_copy",
    "fill": "memory_fill",
    "compare": "memory_compare",
}

NATIVE_SYMBOLS = {
    "memory_copy": "memory_copy",
    "memory_move": "memory_move",
    "memory_fill": "memory_fill",
    "memory_zero": "memory_zero",
    "memory_compare": "memory_compare",
    "memory_load_u32": "load_u32",
    "memory_store_u32": "store_u32",
    "utf8_validate": "utf8_validate",
    "hash_bytes": "hash_bytes",
    **{name: name for name in HASH_NATIVE_SIGNATURES},
    **{name: name for name in RUNTIME_SIGNATURES},
}

API_GROUPS = {
    StdlibRecordKind.HEAP_API: (
        "runtime_begin_private",
        "runtime_resolve",
        "runtime_resolve_typed",
        "runtime_allocate_prepare",
        "runtime_publish",
        "runtime_rollback",
        "runtime_release",
        "runtime_borrow",
        "runtime_release_borrow",
        "runtime_pin",
        "runtime_unpin",
        "runtime_read",
        "runtime_write",
        "runtime_reserve",
        "runtime_append",
        "runtime_format_u32",
        "runtime_search",
        "runtime_cleanup",
        "heap_allocate",
        "heap_resize",
        "heap_register_slot",
        "heap_update_slot",
        "heap_plan_compaction",
        "heap_apply_move_plan",
        "heap_pin",
        "heap_unpin",
        "heap_borrow",
        "heap_release_borrow",
        "heap_release",
    ),
    StdlibRecordKind.MEMORY_API: (
        "memory_load_u32",
        "memory_store_u32",
        "memory_copy",
        "memory_move",
        "memory_fill",
        "memory_compare",
        "memory_zero",
    ),
    StdlibRecordKind.COLLECTION_MODEL: (
        "hash_u64",
        "runtime_hash_map_lookup",
        "runtime_hash_set_lookup",
        "array_new",
        "array_get",
        "array_set",
        "vector_push",
        "vector_pop",
        "deque_push_front",
        "deque_push_back",
        "deque_pop_front",
        "hash_map_get",
        "hash_map_insert",
        "hash_map_remove",
        "hash_set_insert",
        "hash_set_contains",
        "collection_iter",
    ),
    StdlibRecordKind.TEXT_MODEL: (
        "bytes_slice",
        "byte_buffer_append",
        "utf8_validate",
        "string_from_bytes",
        "string_scalars",
        "string_concat",
        "string_compare",
        "string_find",
        "string_split",
        "format_append",
    ),
    StdlibRecordKind.ALGORITHM: (
        "compare",
        "clamp",
        "copy",
        "fill",
        "find",
        "count",
        "stable_sort",
        "binary_search",
        "map",
        "filter",
        "fold",
        "iterator_take",
        "iterator_skip",
        "iterator_zip",
        "hash_bytes",
    ),
    StdlibRecordKind.REFUSAL_COMBINATOR: (
        "propagate",
        "map_success",
        "map_refusal",
        "catch_refusal",
        "enrich_refusal",
        "cleanup",
        "defer",
    ),
}

TYPE_SPECS = (
    ("Option", TypeForm.UNION, 16, 8),
    ("TypedResult", TypeForm.UNION, 24, 8),
    ("Tuple0", TypeForm.STRUCT, 0, 1),
    ("Tuple1", TypeForm.STRUCT, 8, 8),
    ("Tuple2", TypeForm.STRUCT, 16, 8),
    ("Tuple3", TypeForm.STRUCT, 24, 8),
    ("Tuple4", TypeForm.STRUCT, 32, 8),
    ("FixedArray", TypeForm.ARRAY, 0, 1),
    ("Slice", TypeForm.STRUCT, 40, 8),
    ("MutSlice", TypeForm.STRUCT, 40, 8),
    ("MutRef", TypeForm.REF, 24, 8),
    ("Range", TypeForm.STRUCT, 24, 8),
    ("Iterator", TypeForm.STRUCT, 16, 8),
    ("IntoIterator", TypeForm.STRUCT, 16, 8),
    ("Callable", TypeForm.FUNCTION, 24, 8),
    ("Vector", TypeForm.STRUCT, 32, 8),
    ("Deque", TypeForm.STRUCT, 40, 8),
    ("HashMap", TypeForm.STRUCT, 56, 8),
    ("HashSet", TypeForm.STRUCT, 56, 8),
    ("ByteBuffer", TypeForm.STRUCT, 32, 8),
    ("String", TypeForm.STRUCT, 24, 8),
    ("StringView", TypeForm.STRUCT, 40, 8),
)
TRAITS = (
    "Drop",
    "Clone",
    "Move",
    "IteratorTrait",
    "IntoIteratorTrait",
    "CallableTrait",
    "Hash",
    "Equal",
    "Compare",
)


def _module(value: ModuleObject) -> NodeDef:
    return NodeDef(
        value.id,
        value.name,
        NodeKind.MODULE,
        value.parent,
        type_id=MODULE_TYPE,
        data=value.to_payload(),
    )


def _type_node(value: TypeObject, parent: int) -> NodeDef:
    return NodeDef(
        value.id,
        value.name,
        NodeKind.TYPE,
        parent,
        type_id=TYPE_TYPE,
        data=value.to_payload(),
    )


def _operation_plan(kind: StdlibRecordKind, name: str) -> tuple[SemanticOpcode, ...]:
    specific = {
        "heap_allocate": SemanticOpcode.ALLOCATE,
        "heap_resize": SemanticOpcode.COPY,
        "heap_register_slot": SemanticOpcode.STORE_FIELD,
        "heap_update_slot": SemanticOpcode.MOVE_SLOT,
        "heap_plan_compaction": SemanticOpcode.MOVE_SLOT,
        "heap_apply_move_plan": SemanticOpcode.MOVE_SLOT,
        "heap_pin": SemanticOpcode.PIN,
        "heap_unpin": SemanticOpcode.UNPIN,
        "heap_borrow": SemanticOpcode.BORROW,
        "heap_release_borrow": SemanticOpcode.RELEASE_BORROW,
        "heap_release": SemanticOpcode.CLEANUP,
        "array_new": SemanticOpcode.ALLOCATE,
        "array_get": SemanticOpcode.READ,
        "array_set": SemanticOpcode.WRITE,
        "vector_push": SemanticOpcode.WRITE,
        "vector_pop": SemanticOpcode.READ,
        "deque_push_front": SemanticOpcode.WRITE,
        "deque_push_back": SemanticOpcode.WRITE,
        "deque_pop_front": SemanticOpcode.READ,
        "hash_map_get": SemanticOpcode.HASH,
        "hash_map_insert": SemanticOpcode.HASH,
        "hash_map_remove": SemanticOpcode.HASH,
        "hash_set_insert": SemanticOpcode.HASH,
        "hash_set_contains": SemanticOpcode.HASH,
        "byte_buffer_append": SemanticOpcode.APPEND_BYTES,
        "string_from_bytes": SemanticOpcode.UTF8_VALIDATE,
        "string_concat": SemanticOpcode.APPEND_BYTES,
        "string_compare": SemanticOpcode.COMPARE,
        "string_find": SemanticOpcode.SEARCH,
        "format_append": SemanticOpcode.FORMAT_INTEGER,
        "stable_sort": SemanticOpcode.READ,
        "binary_search": SemanticOpcode.COMPARE,
        "find": SemanticOpcode.COMPARE,
        "count": SemanticOpcode.CALL_CALLBACK,
        "map": SemanticOpcode.CALL_CALLBACK,
        "filter": SemanticOpcode.CALL_CALLBACK,
        "fold": SemanticOpcode.CALL_CALLBACK,
        "propagate": SemanticOpcode.READ_TAG,
        "map_success": SemanticOpcode.READ_TAG,
        "map_refusal": SemanticOpcode.MAP_REFUSAL,
        "catch_refusal": SemanticOpcode.MAP_REFUSAL,
        "enrich_refusal": SemanticOpcode.ENRICH_REFUSAL,
        "cleanup": SemanticOpcode.CLEANUP,
        "defer": SemanticOpcode.CLEANUP,
    }
    core = specific.get(name, SemanticOpcode.READ)
    transactional = kind == StdlibRecordKind.HEAP_API or name in {
        "array_new",
        "array_set",
        "vector_push",
        "vector_pop",
        "deque_push_front",
        "deque_push_back",
        "deque_pop_front",
        "hash_map_insert",
        "hash_map_remove",
        "hash_set_insert",
        "byte_buffer_append",
        "string_concat",
        "stable_sort",
    }
    branch = name in {
        "array_get",
        "hash_map_get",
        "hash_set_contains",
        "find",
        "binary_search",
        "propagate",
        "map_success",
        "map_refusal",
        "catch_refusal",
        "enrich_refusal",
    }
    loop = name in {
        "collection_iter",
        "string_scalars",
        "string_find",
        "string_split",
        "count",
        "stable_sort",
        "map",
        "filter",
        "fold",
        "iterator_take",
        "iterator_skip",
        "iterator_zip",
    }
    prefix = (SemanticOpcode.BEGIN_PRIVATE,) if transactional else ()
    ops = prefix + (core,)
    if name in {"hash_map_get", "hash_set_contains"}:
        ops += (SemanticOpcode.LOOKUP,)
    if name.startswith("string") or name in {"format_append"}:
        ops += (SemanticOpcode.UTF8_BOUNDARY,)
    if name == "collection_iter":
        ops += (SemanticOpcode.CANONICAL_ORDER,)
    if branch:
        ops += (
            SemanticOpcode.READ_TAG,
            SemanticOpcode.PROJECT_PAYLOAD,
            SemanticOpcode.CONSTRUCT_RESULT
            if name
            in {
                "propagate",
                "map_success",
                "map_refusal",
                "catch_refusal",
                "enrich_refusal",
            }
            else SemanticOpcode.CONSTRUCT_OPTION,
        )
    if loop:
        ops += (
            SemanticOpcode.ITER_INIT,
            SemanticOpcode.ITER_CONDITION,
            SemanticOpcode.ITER_PROJECT,
            SemanticOpcode.ITER_ADVANCE,
        )
    if name == "stable_sort":
        ops += (
            SemanticOpcode.CALL_CALLBACK,
            SemanticOpcode.READ,
            SemanticOpcode.WRITE,
            SemanticOpcode.MOVE_SLOT,
        )
    elif name in {"map", "filter"}:
        ops += (SemanticOpcode.CALL_CALLBACK,)
    if transactional:
        ops += (
            SemanticOpcode.VALIDATE,
            SemanticOpcode.PUBLISH,
            SemanticOpcode.ROLLBACK,
            SemanticOpcode.CLEANUP,
        )
    if kind == StdlibRecordKind.REFUSAL_COMBINATOR:
        ops += (SemanticOpcode.CLEANUP,)
    if name in {"count", "fold"}:
        return (
            SemanticOpcode.ITER_INIT,
            SemanticOpcode.ITER_CONDITION,
            SemanticOpcode.ITER_PROJECT,
            SemanticOpcode.CALL_CALLBACK,
            SemanticOpcode.ITER_ADVANCE,
            SemanticOpcode.RETURN_VALUE,
        )
    if name == "stable_sort":
        return (
            SemanticOpcode.BEGIN_PRIVATE,
            SemanticOpcode.ITER_INIT,
            SemanticOpcode.ITER_CONDITION,
            SemanticOpcode.ITER_PROJECT,
            SemanticOpcode.READ,
            SemanticOpcode.CALL_CALLBACK,
            SemanticOpcode.WRITE,
            SemanticOpcode.MOVE_SLOT,
            SemanticOpcode.ITER_ADVANCE,
            SemanticOpcode.VALIDATE,
            SemanticOpcode.PUBLISH,
            SemanticOpcode.ROLLBACK,
            SemanticOpcode.CLEANUP,
            SemanticOpcode.RETURN_VALUE,
        )
    return ops + (SemanticOpcode.RETURN_VALUE,)


def _cfg_shape(
    opcodes: tuple[SemanticOpcode, ...],
) -> tuple[tuple[TerminatorKind, tuple[int, ...]], ...]:
    if SemanticOpcode.PUBLISH in opcodes:
        return (
            (TerminatorKind.BRANCH, (1, 2)),
            (TerminatorKind.GOTO, (3,)),
            (TerminatorKind.GOTO, (3,)),
            (TerminatorKind.RETURN, ()),
        )
    if SemanticOpcode.READ_TAG in opcodes:
        return (
            (TerminatorKind.SWITCH_TAG, (1, 2, 3)),
            (TerminatorKind.GOTO, (3,)),
            (TerminatorKind.GOTO, (3,)),
            (TerminatorKind.RETURN, ()),
        )
    if SemanticOpcode.ITER_INIT in opcodes:
        return (
            (TerminatorKind.GOTO, (1,)),
            (TerminatorKind.LOOP, (2, 3)),
            (TerminatorKind.GOTO, (1,)),
            (TerminatorKind.RETURN, ()),
        )
    return ((TerminatorKind.RETURN, ()),)


def _semantic_parameter_types(
    callee_name: str,
    callable_by_name: dict[str, tuple[int, tuple[int, ...], tuple[int, ...], int]],
) -> tuple[int, ...]:
    parameters = callable_by_name[callee_name][2]
    return parameters[1:] if parameters and parameters[0] == REF_TYPE else parameters


def stdlib_nodes() -> list[NodeDef]:
    """Return stable-ID semantic declarations; no composed declaration claims execution."""
    provenance_id = 41005
    nodes = [
        _module(
            ModuleObject(
                STDLIB_FOUNDATION_MODULE_ID,
                "foundation",
                RXF_MODULE_ID,
                ModuleCategory.EXECUTION,
            )
        ),
        _module(
            ModuleObject(
                STDLIB_BASIC_MODULE_ID,
                "stdlib",
                RXF_MODULE_ID,
                ModuleCategory.EXECUTION,
            )
        ),
        _module(
            ModuleObject(
                STDLIB_LIBRARY_MODULE_ID,
                "algorithms",
                RXF_MODULE_ID,
                ModuleCategory.EXECUTION,
            )
        ),
        CompilerProvenance(
            provenance_id,
            "clang_18_1_3",
            STDLIB_FOUNDATION_MODULE_ID,
            18,
            hashlib.sha256(b"rxf-stdlib-native-v1").digest(),
        ).to_node(),
        _type_node(
            TypeObject(
                STDLIB_SEMANTIC_GRAPH_TYPE,
                "SemanticGraph",
                TypeForm.STRUCT,
                48,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_SEMANTIC_BLOCK_TYPE,
                "SemanticBlock",
                TypeForm.STRUCT,
                0,
                8,
                flags=TypeFlags.SELF_DESCRIBING | TypeFlags.VARIABLE_SIZE,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_SEMANTIC_OPERATION_TYPE,
                "SemanticOperation",
                TypeForm.STRUCT,
                64,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_SEMANTIC_VALUE_TYPE,
                "SemanticValue",
                TypeForm.STRUCT,
                0,
                8,
                flags=TypeFlags.SELF_DESCRIBING | TypeFlags.VARIABLE_SIZE,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_TAGGED_VARIANT_TYPE,
                "TaggedVariant",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_REFUSAL_MAPPING_TYPE,
                "RefusalMapping",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_REFUSAL_MAPPING_ENTRY_TYPE,
                "RefusalMappingEntry",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_REFUSAL_ENRICHMENT_TYPE,
                "RefusalEnrichment",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_CANONICAL_ORDER_AUTHORITY_TYPE,
                "CanonicalOrderAuthority",
                TypeForm.STRUCT,
                16,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_HASH_STORAGE_AUTHORITY_TYPE,
                "HashStorageAuthority",
                TypeForm.STRUCT,
                40,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_HASH_AUTHORITY_TYPE,
                "HashAuthority",
                TypeForm.STRUCT,
                24,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                HASH_BUCKET_STATE_TYPE,
                "HashBucketState",
                TypeForm.ENUM,
                4,
                4,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                HASH_MAP_BUCKET_TYPE,
                "HashMapBucket",
                TypeForm.STRUCT,
                48,
                8,
                field_count=4,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                HASH_SET_BUCKET_TYPE,
                "HashSetBucket",
                TypeForm.STRUCT,
                24,
                8,
                field_count=3,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                NATIVE_TRANSACTION_TYPE, "NativeTransaction", TypeForm.STRUCT, 48, 8
            ),
            STDLIB_FOUNDATION_MODULE_ID,
        ),
        _type_node(
            TypeObject(
                STDLIB_RECORD_TYPE,
                "StdlibRecord",
                TypeForm.STRUCT,
                40,
                8,
                flags=TypeFlags.SELF_DESCRIBING,
            ),
            STDLIB_BASIC_MODULE_ID,
        ),
    ]
    next_id = 60000
    type_ids: dict[str, int] = {}
    for name, form, size, align in TYPE_SPECS:
        type_id = next_id
        next_id += 1
        type_ids[name] = type_id
        flags = (
            TypeFlags.VARIABLE_SIZE
            if name
            in {
                "FixedArray",
                "Vector",
                "Deque",
                "HashMap",
                "HashSet",
                "ByteBuffer",
                "String",
            }
            else TypeFlags.NONE
        )
        nodes.append(
            _type_node(
                TypeObject(
                    type_id,
                    name,
                    form,
                    size,
                    align,
                    flags=flags,
                    field_count=5
                    if name in {"Slice", "MutSlice", "StringView", "HashMap", "HashSet"}
                    else 3
                    if name == "MutRef"
                    else 1
                    if name in {"Option", "TypedResult"}
                    else 0,
                ),
                STDLIB_BASIC_MODULE_ID,
            )
        )
    for owner_name in ("Option", "TypedResult"):
        owner = type_ids[owner_name]
        field = FieldObject(next_id, "tag", owner, U32_TYPE, 0)
        next_id += 1
        nodes.append(
            NodeDef(
                field.id,
                field.name,
                NodeKind.FIELD,
                owner,
                type_id=FIELD_TYPE,
                data=field.to_payload(),
            )
        )
    tagged_type_specs = (
        (
            OPTION_U32_TYPE,
            "Option_u32",
            (("none_payload", U32_TYPE, 0), ("some_payload", U32_TYPE, 1)),
        ),
        (
            OPTION_U64_TYPE,
            "Option_u64",
            (("none_payload", U64_TYPE, 0), ("some_payload", U64_TYPE, 1)),
        ),
        (
            OPTION_REF_TYPE,
            "Option_ref",
            (("none_payload", REF_TYPE, 0), ("some_payload", REF_TYPE, 1)),
        ),
        (
            TYPED_RESULT_U64_U32_TYPE,
            "TypedResult_u64_u32",
            (("ok_payload", U64_TYPE, 0), ("err_payload", U32_TYPE, 1)),
        ),
    )
    tagged_fields: dict[int, tuple[int, dict[int, int]]] = {}
    for tagged_type, tagged_name, variants in tagged_type_specs:
        layouts = {U32_TYPE: (4, 4), U64_TYPE: (8, 8), REF_TYPE: (24, 8)}
        payload_align = max(layouts[payload_type][1] for _, payload_type, _ in variants)
        payload_size = max(layouts[payload_type][0] for _, payload_type, _ in variants)
        payload_offset = (4 + payload_align - 1) & -payload_align
        total_size = (
            payload_offset + payload_size + payload_align - 1
        ) & -payload_align
        nodes.append(
            _type_node(
                TypeObject(
                    tagged_type,
                    tagged_name,
                    TypeForm.UNION,
                    total_size,
                    payload_align,
                    flags=TypeFlags.SELF_DESCRIBING,
                    field_count=1 + len(variants),
                ),
                STDLIB_BASIC_MODULE_ID,
            )
        )
        tag_field_id = next_id
        nodes.append(
            NodeDef(
                tag_field_id,
                "tag",
                NodeKind.FIELD,
                tagged_type,
                type_id=FIELD_TYPE,
                data=FieldObject(
                    tag_field_id, "tag", tagged_type, U32_TYPE, 0
                ).to_payload(),
            )
        )
        next_id += 1
        payload_fields = {}
        for variant_name, payload_type, tag_value in variants:
            payload_field_id, variant_id = next_id, next_id + 1
            next_id += 2
            payload_fields[tag_value] = payload_field_id
            nodes.append(
                NodeDef(
                    payload_field_id,
                    variant_name,
                    NodeKind.FIELD,
                    tagged_type,
                    type_id=FIELD_TYPE,
                    data=FieldObject(
                        payload_field_id,
                        variant_name,
                        tagged_type,
                        payload_type,
                        payload_offset,
                        flags=FieldFlags.OPTIONAL,
                    ).to_payload(),
                )
            )
            nodes.append(
                TaggedVariant(
                    variant_id,
                    variant_name.removesuffix("_payload"),
                    tagged_type,
                    tag_value,
                    payload_type,
                    payload_field_id,
                ).to_node(STDLIB_TAGGED_VARIANT_TYPE)
            )
        tagged_fields[tagged_type] = (tag_field_id, payload_fields)

    # Canonical handle is object_id/generation/offset. Views append start/length.
    field_ids: dict[tuple[str, str], int] = {}
    for owner_name in ("MutRef", "Slice", "MutSlice", "StringView"):
        owner = type_ids[owner_name]
        fields = [
            ("object_id", U64_TYPE, 0, FieldFlags.NONE),
            ("generation", U64_TYPE, 8, FieldFlags.NONE),
            ("offset", U64_TYPE, 16, FieldFlags.NONE),
        ]
        if owner_name in {"Slice", "MutSlice", "StringView"}:
            fields.extend(
                (
                    ("start", U64_TYPE, 24, FieldFlags.NONE),
                    ("length", U64_TYPE, 32, FieldFlags.NONE),
                )
            )
        for field_name, field_type, offset, flags in fields:
            field = FieldObject(
                next_id, field_name, owner, field_type, offset, flags=flags
            )
            field_ids[owner_name, field_name] = next_id
            next_id += 1
            nodes.append(
                NodeDef(
                    field.id,
                    field.name,
                    NodeKind.FIELD,
                    owner,
                    type_id=FIELD_TYPE,
                    data=field.to_payload(),
                )
            )
    # Hash collection values persist only durable handles and uint64 counters.
    for owner_name in ("HashMap", "HashSet"):
        owner = type_ids[owner_name]
        for field_name, field_type, offset in (
            ("bucket_storage", REF_TYPE, 0),
            ("capacity", U64_TYPE, 24),
            ("count", U64_TYPE, 32),
            ("tombstone_count", U64_TYPE, 40),
            ("generation", U64_TYPE, 48),
        ):
            field = FieldObject(next_id, field_name, owner, field_type, offset)
            field_ids[owner_name, field_name] = next_id
            next_id += 1
            nodes.append(
                NodeDef(
                    field.id,
                    field.name,
                    NodeKind.FIELD,
                    owner,
                    type_id=FIELD_TYPE,
                    data=field.to_payload(),
                )
            )
    for owner, fields in (
        (
            HASH_MAP_BUCKET_TYPE,
            (
                ("state", HASH_BUCKET_STATE_TYPE, 0),
                ("stored_hash", U64_TYPE, 8),
                ("key", U64_TYPE, 16),
                ("value", REF_TYPE, 24),
            ),
        ),
        (
            HASH_SET_BUCKET_TYPE,
            (
                ("state", HASH_BUCKET_STATE_TYPE, 0),
                ("stored_hash", U64_TYPE, 8),
                ("key", U64_TYPE, 16),
            ),
        ),
    ):
        for field_name, field_type, offset in fields:
            field = FieldObject(next_id, field_name, owner, field_type, offset)
            next_id += 1
            nodes.append(
                NodeDef(
                    field.id,
                    field.name,
                    NodeKind.FIELD,
                    owner,
                    type_id=FIELD_TYPE,
                    data=field.to_payload(),
                )
            )
    hash_storage_authority_id = next_id
    next_id += 1

    concrete_specs = (
        (
            "FixedArray_u8_16",
            specialize_value_type("FixedArray", ((U8_TYPE, 1, 1),), (16,)),
            TypeForm.ARRAY,
        ),
        (
            "FixedArray_u8_32",
            specialize_value_type("FixedArray", ((U8_TYPE, 1, 1),), (32,)),
            TypeForm.ARRAY,
        ),
        (
            "Tuple_u32_u32",
            specialize_value_type("Tuple2", ((U32_TYPE, 4, 4), (U32_TYPE, 4, 4))),
            TypeForm.STRUCT,
        ),
        (
            "Vector_u8",
            specialize_value_type("Vector", ((U8_TYPE, 1, 1),)),
            TypeForm.STRUCT,
        ),
        (
            "Vector_u32",
            specialize_value_type("Vector", ((U32_TYPE, 4, 4),)),
            TypeForm.STRUCT,
        ),
        (
            "Vector_String",
            specialize_value_type("Vector", ((type_ids["String"], 24, 8),)),
            TypeForm.STRUCT,
        ),
    )
    for concrete_name, concrete, form in concrete_specs:
        nodes.append(
            _type_node(
                TypeObject(
                    concrete.id, concrete_name, form, concrete.size, concrete.alignment
                ),
                STDLIB_BASIC_MODULE_ID,
            )
        )
    trait_ids = {}
    for name in TRAITS:
        trait_ids[name] = next_id
        nodes.append(Trait(next_id, name, STDLIB_BASIC_MODULE_ID).to_node())
        next_id += 1
    # Marker conformances are useful dispatch facts even before compiler-generated method bodies.
    for trait_name, concrete_name in (
        ("Drop", "Vector"),
        ("Clone", "String"),
        ("Move", "ByteBuffer"),
        ("IteratorTrait", "Iterator"),
        ("IntoIteratorTrait", "Vector"),
        ("CallableTrait", "Callable"),
        ("Hash", "String"),
        ("Equal", "String"),
        ("Compare", "String"),
    ):
        nodes.append(
            Conformance(
                next_id,
                f"{concrete_name}_{trait_name}",
                STDLIB_BASIC_MODULE_ID,
                trait_ids[trait_name],
                type_ids[concrete_name],
                (),
            ).to_node()
        )
        next_id += 1
    # Generic templates are real Template objects backed by declared semantic constructors.
    for model_name in (
        "Option",
        "TypedResult",
        "Tuple",
        "FixedArray",
        "Slice",
        "MutSlice",
        "Ref",
        "MutRef",
        "Range",
        "Iterator",
        "IntoIterator",
        "Callable",
    ):
        parameter_count = 2 if model_name in {"TypedResult", "Tuple"} else 1
        function_id, signature_id = next_id, next_id + 1
        next_id += 2
        params = []
        for index in range(parameter_count):
            params.append(next_id)
            nodes.append(
                GenericParameter(next_id, f"T{index}", function_id, index).to_node()
            )
            next_id += 1
        nodes.append(
            SignatureObject(signature_id, "signature", function_id, U64_TYPE).to_node()
        )
        nodes.append(
            FunctionObject(
                function_id,
                f"model_{model_name.lower()}",
                STDLIB_BASIC_MODULE_ID,
                signature_id,
                FunctionImplementation.ABSTRACT,
                FunctionLayer.BASIC,
            ).to_node()
        )
        nodes.append(
            Template(
                next_id, model_name, STDLIB_BASIC_MODULE_ID, function_id, tuple(params)
            ).to_node()
        )
        next_id += 1
    refusal_ids = []
    refusal_by_name: dict[str, int] = {}
    for code, name in enumerate(
        (
            "overflow",
            "division_by_zero",
            "shift_count",
            "non_finite",
            "inexact",
            "out_of_range",
            "alignment",
            "invalid_utf8",
            "out_of_memory",
            "borrow_conflict",
            "pinned",
        ),
        1,
    ):
        refusal_ids.append(next_id)
        refusal_by_name[name] = next_id
        nodes.append(
            RefusalVariant(next_id, name, STDLIB_FOUNDATION_MODULE_ID, code).to_node()
        )
        next_id += 1
    nodes.append(
        RefusalSet(
            STDLIB_REFUSAL_SET_ID,
            "StdlibRefusals",
            STDLIB_FOUNDATION_MODULE_ID,
            tuple(refusal_ids),
        ).to_node()
    )
    refusal_authorities: dict[str, int] = {}
    mapping_specs = {
        "map_refusal": (
            ("out_of_range", "invalid_utf8"),
            RefusalDefaultPolicy.PROPAGATE_UNMATCHED,
        ),
        "catch_refusal": (
            ("invalid_utf8", "invalid_utf8"),
            RefusalDefaultPolicy.PROPAGATE_UNMATCHED,
        ),
    }
    for mapping_name, (
        (source_name, destination_name),
        policy,
    ) in mapping_specs.items():
        mapping_id, entry_id = next_id, next_id + 1
        next_id += 2
        refusal_authorities[mapping_name] = mapping_id
        nodes.append(
            RefusalMapping(
                mapping_id,
                f"{mapping_name}_mapping",
                STDLIB_BASIC_MODULE_ID,
                STDLIB_REFUSAL_SET_ID,
                STDLIB_REFUSAL_SET_ID,
                (entry_id,),
                policy,
            ).to_node(STDLIB_REFUSAL_MAPPING_TYPE)
        )
        nodes.append(
            RefusalMappingEntry(
                entry_id,
                f"{source_name}_to_{destination_name}",
                mapping_id,
                refusal_by_name[source_name],
                next(
                    code
                    for name, code in (
                        ("out_of_memory", 1),
                        ("bounds", 2),
                        ("invalid_utf8", 8),
                        ("invalid_state", 4),
                        ("borrow_conflict", 10),
                        ("generation_mismatch", 6),
                        ("overflow", 7),
                        ("unaligned", 8),
                        ("not_found", 9),
                        ("out_of_range", 6),
                    )
                    if name == source_name
                ),
                refusal_by_name[destination_name],
                next(
                    code
                    for name, code in (
                        ("out_of_memory", 1),
                        ("bounds", 2),
                        ("invalid_utf8", 8),
                        ("invalid_state", 4),
                        ("borrow_conflict", 10),
                        ("generation_mismatch", 6),
                        ("overflow", 7),
                        ("unaligned", 8),
                        ("not_found", 9),
                        ("out_of_range", 6),
                    )
                    if name == destination_name
                ),
            ).to_node(STDLIB_REFUSAL_MAPPING_ENTRY_TYPE)
        )
    enrichment_id = next_id
    next_id += 1
    refusal_authorities["enrich_refusal"] = enrichment_id
    nodes.append(
        RefusalEnrichment(
            enrichment_id,
            "enrich_refusal_metadata",
            STDLIB_BASIC_MODULE_ID,
            STDLIB_REFUSAL_SET_ID,
            STDLIB_REFUSAL_SET_ID,
            refusal_by_name["borrow_conflict"],
            refusal_by_name["borrow_conflict"],
            (tagged_fields[TYPED_RESULT_U64_U32_TYPE][1][1],),
            (),
            preserve_identity=True,
        ).to_node(STDLIB_REFUSAL_ENRICHMENT_TYPE)
    )
    canonical_slot_order_authority_id = next_id
    next_id += 1
    nodes.append(
        CanonicalOrderAuthority(
            canonical_slot_order_authority_id,
            "ascending_slot_index",
            STDLIB_BASIC_MODULE_ID,
            U64_TYPE,
            U64_TYPE,
            CanonicalOrderPolicy.ASCENDING_SLOT_INDEX,
            True,
        ).to_node(STDLIB_CANONICAL_ORDER_AUTHORITY_TYPE)
    )
    callable_by_name: dict[str, tuple[int, tuple[int, ...], tuple[int, ...], int]] = {}
    numeric_functions_by_name = {
        node.name: node for node in numeric_nodes() if node.type_id == FUNCTION_TYPE
    }
    for kind, names in API_GROUPS.items():
        parent = (
            STDLIB_FOUNDATION_MODULE_ID
            if kind in {StdlibRecordKind.HEAP_API, StdlibRecordKind.MEMORY_API}
            else STDLIB_LIBRARY_MODULE_ID
            if kind == StdlibRecordKind.ALGORITHM
            else STDLIB_BASIC_MODULE_ID
        )
        for name in names:
            native_symbol = NATIVE_SYMBOLS.get(name)
            alias_target = COMPOSED_ALIASES.get(name)
            semantic_plan = (
                _operation_plan(kind, name)
                if not native_symbol and not alias_target
                else ()
            )
            if native_symbol:
                parameter_types, value_type = NATIVE_SIGNATURES[name]
            elif alias_target:
                target = callable_by_name[alias_target]
                parameter_types, value_type = target[2], target[3]
            elif name in COMPOSED_SIGNATURES:
                parameter_types, value_type = COMPOSED_SIGNATURES[name]
            else:
                parameter_types, value_type = (U64_TYPE,), U64_TYPE
            function_id, signature_id = next_id, next_id + 1
            next_id += 2
            parameter_ids = tuple(range(next_id, next_id + len(parameter_types)))
            next_id += len(parameter_types)
            for index, (parameter_id, parameter_type) in enumerate(
                zip(parameter_ids, parameter_types, strict=True)
            ):
                nodes.append(
                    ParameterObject(
                        parameter_id,
                        f"argument_{index}",
                        signature_id,
                        parameter_type,
                        index,
                    ).to_node()
                )
            result_id = next_id
            next_id += 1
            nodes.append(
                ResultSlot(result_id, "value", signature_id, value_type, 0).to_node()
            )
            nodes.append(
                SignatureObject(
                    signature_id,
                    "signature",
                    function_id,
                    value_type,
                    parameter_ids,
                    (result_id,),
                    STDLIB_REFUSAL_SET_ID,
                ).to_node()
            )
            effects = Effect.NONE
            if name.startswith("heap_"):
                effects |= Effect.ALLOCATE | Effect.WRITE_MEMORY
            if name.startswith("memory_") or alias_target in {
                "memory_copy",
                "memory_fill",
                "memory_compare",
            }:
                effects |= Effect.READ_MEMORY
            if (
                name.startswith("memory_")
                and name not in {"memory_compare", "memory_load_u32"}
                or alias_target in {"memory_copy", "memory_fill"}
            ):
                effects |= Effect.WRITE_MEMORY
            body_id = 0
            if alias_target:
                target_function, target_parameters, target_types, _target_result = (
                    callable_by_name[alias_target]
                )
                value_ids = tuple(range(next_id, next_id + len(parameter_ids)))
                next_id += len(value_ids)
                argument_ids = tuple(range(next_id, next_id + len(parameter_ids)))
                next_id += len(argument_ids)
                body_id = next_id
                next_id += 1
                for index, (
                    value_id,
                    source_parameter,
                    value_type_id,
                    argument_id,
                    target_parameter,
                ) in enumerate(
                    zip(
                        value_ids,
                        parameter_ids,
                        target_types,
                        argument_ids,
                        target_parameters,
                        strict=True,
                    )
                ):
                    nodes.append(
                        ValueObject(
                            value_id,
                            f"argument_{index}",
                            function_id,
                            value_type_id,
                            ValueKind.PARAMETER,
                            source_parameter.to_bytes(8, "little"),
                        ).to_node()
                    )
                    nodes.append(
                        ArgumentObject(
                            argument_id,
                            f"argument_{index}",
                            body_id,
                            target_parameter,
                            value_id,
                        ).to_node()
                    )
                nodes.append(
                    CallObject(
                        body_id,
                        alias_target,
                        function_id,
                        target_function,
                        argument_ids,
                        value_type,
                    ).to_node()
                )
            semantic_nodes: list[NodeDef] = []
            if semantic_plan:
                runtime_for_opcode = {
                    SemanticOpcode.BEGIN_PRIVATE: "runtime_begin_private",
                    SemanticOpcode.RESOLVE_HANDLE: "runtime_resolve",
                    SemanticOpcode.ALLOCATE: "runtime_allocate_prepare",
                    SemanticOpcode.COPY: "runtime_read",
                    SemanticOpcode.WRITE: "runtime_write",
                    SemanticOpcode.STORE_FIELD: "runtime_write",
                    SemanticOpcode.READ: "runtime_read",
                    SemanticOpcode.VALIDATE: "runtime_resolve",
                    SemanticOpcode.PUBLISH: "runtime_publish",
                    SemanticOpcode.ROLLBACK: "runtime_rollback",
                    SemanticOpcode.CLEANUP: "runtime_cleanup",
                    SemanticOpcode.UTF8_VALIDATE: "runtime_read",
                    SemanticOpcode.FORMAT_INTEGER: "runtime_format_u32",
                    SemanticOpcode.APPEND_BYTES: "runtime_append",
                    SemanticOpcode.SEARCH: "runtime_search",
                    SemanticOpcode.HASH: "hash_u64"
                    if name in {"hash_map_get", "hash_set_contains"}
                    else "runtime_read",
                    SemanticOpcode.LOOKUP: "runtime_hash_map_lookup"
                    if name == "hash_map_get"
                    else "runtime_hash_set_lookup",
                    SemanticOpcode.COMPARE: "runtime_read",
                    SemanticOpcode.BORROW: "runtime_borrow",
                    SemanticOpcode.RELEASE_BORROW: "runtime_release_borrow",
                    SemanticOpcode.PIN: "runtime_pin",
                    SemanticOpcode.UNPIN: "runtime_unpin",
                    SemanticOpcode.MOVE_SLOT: "runtime_write",
                }
                callback_names = {
                    "count": "wrapping_add_uint64_t",
                    "stable_sort": "less_uint64_t",
                    "map": "bit_not_uint64_t",
                    "filter": "less_uint64_t",
                    "fold": "wrapping_add_uint64_t",
                }
                callback_function = numeric_functions_by_name.get(
                    callback_names.get(name, "")
                )
                # Function.body_id remains a checked compatibility Call, while
                # SemanticGraph is the sole lowering authority. Use the owner's
                # exact signature and parameter values; never synthesize marker
                # operands from a different function's shape.
                compatibility_values = tuple(
                    range(next_id, next_id + len(parameter_ids))
                )
                next_id += len(compatibility_values)
                compatibility_arguments = tuple(
                    range(next_id, next_id + len(parameter_ids))
                )
                next_id += len(compatibility_arguments)
                body_id = next_id
                next_id += 1
                for index, (
                    parameter_id,
                    parameter_type,
                    value_id,
                    argument_id,
                ) in enumerate(
                    zip(
                        parameter_ids,
                        parameter_types,
                        compatibility_values,
                        compatibility_arguments,
                        strict=True,
                    )
                ):
                    semantic_nodes.extend(
                        (
                            ValueObject(
                                value_id,
                                f"compatibility_argument_{index}",
                                function_id,
                                parameter_type,
                                ValueKind.PARAMETER,
                                parameter_id.to_bytes(8, "little"),
                            ).to_node(),
                            ArgumentObject(
                                argument_id,
                                f"compatibility_argument_{index}",
                                body_id,
                                parameter_id,
                                value_id,
                            ).to_node(),
                        )
                    )
                semantic_nodes.append(
                    CallObject(
                        body_id,
                        "semantic_graph_compatibility_entry",
                        function_id,
                        function_id,
                        compatibility_arguments,
                        value_type,
                    ).to_node()
                )
                shape = _cfg_shape(semantic_plan)
                graph_id = next_id
                next_id += 1
                block_ids = tuple(range(next_id, next_id + len(shape)))
                next_id += len(shape)
                # Explicit Function parameter values preserve the Signature parameter identity.
                graph_parameters = []
                for index, (parameter_id, parameter_type) in enumerate(
                    zip(parameter_ids, parameter_types, strict=True)
                ):
                    value_id = next_id
                    next_id += 1
                    graph_parameters.append(value_id)
                    semantic_nodes.append(
                        SemanticValue(
                            value_id,
                            f"parameter_{index}",
                            graph_id,
                            parameter_type,
                            SemanticValueKind.PARAMETER,
                            function_id,
                            block_ids[0],
                            0,
                            index,
                            parameter_id,
                        ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                    )
                seed = graph_parameters[0]
                # Iterator headers and bodies carry state plus an invariant bound.
                # Other merge blocks retain their single status/result parameter.
                iterator_loop = name in {
                    "collection_iter",
                    "string_scalars",
                    "string_find",
                    "string_split",
                    "count",
                    "map",
                    "filter",
                    "fold",
                    "iterator_take",
                    "iterator_skip",
                    "iterator_zip",
                }
                block_parameters: dict[int, tuple[int, ...]] = {}
                loop_state_count = 2
                for index, block_id in enumerate(block_ids[1:], 1):
                    loop_state_count = (
                        3
                        if name
                        in {
                            "count",
                            "map",
                            "filter",
                            "fold",
                            "iterator_take",
                            "iterator_skip",
                            "iterator_zip",
                        }
                        else 2
                    )
                    count = loop_state_count if iterator_loop and index in {1, 2} else 1
                    value_ids = tuple(range(next_id, next_id + count))
                    next_id += count
                    block_parameters[block_id] = value_ids
                    for parameter_index, value_id in enumerate(value_ids):
                        semantic_nodes.append(
                            SemanticValue(
                                value_id,
                                (
                                    "iterator_state"
                                    if parameter_index == 0
                                    else "iterator_end"
                                )
                                if iterator_loop and index in {1, 2}
                                else f"block_{index}_value",
                                block_id,
                                U64_TYPE,
                                SemanticValueKind.BLOCK_PARAMETER,
                                function_id,
                                block_id,
                                0,
                                parameter_index,
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                        )
                iterator_end_value = 0
                iterator_extra_value = 0
                if iterator_loop:
                    iterator_end_value = next_id
                    next_id += 1
                    semantic_nodes.append(
                        SemanticValue(
                            iterator_end_value,
                            "iterator_initial_end",
                            graph_id,
                            U64_TYPE,
                            SemanticValueKind.LITERAL,
                            function_id,
                            block_ids[0],
                            literal=(1).to_bytes(8, "little"),
                        ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                    )
                    if loop_state_count == 3:
                        iterator_extra_value = (
                            graph_parameters[1] if name == "fold" else next_id
                        )
                        if name != "fold":
                            next_id += 1
                            semantic_nodes.append(
                                SemanticValue(
                                    iterator_extra_value,
                                    "iterator_initial_accumulator",
                                    graph_id,
                                    U64_TYPE,
                                    SemanticValueKind.LITERAL,
                                    function_id,
                                    block_ids[0],
                                    literal=(0).to_bytes(8, "little"),
                                ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                            )
                operations_by_block: list[list[SemanticOperation]] = [[] for _ in shape]
                operation_values: dict[int, int] = {}
                for opcode in semantic_plan:
                    if iterator_loop and opcode == SemanticOpcode.ITER_CONDITION:
                        block_index = 1
                    elif opcode in {SemanticOpcode.PUBLISH, SemanticOpcode.VALIDATE}:
                        block_index = 1 if len(shape) > 1 else 0
                    elif opcode in {
                        SemanticOpcode.ROLLBACK,
                        SemanticOpcode.RETURN_REFUSAL,
                    }:
                        block_index = 2 if len(shape) > 2 else 0
                    elif opcode in {
                        SemanticOpcode.CLEANUP,
                        SemanticOpcode.RETURN_VALUE,
                    }:
                        block_index = len(shape) - 1
                    elif opcode in {
                        SemanticOpcode.ITER_PROJECT,
                        SemanticOpcode.ITER_ADVANCE,
                        SemanticOpcode.CALL_CALLBACK,
                        SemanticOpcode.SORT_INSERT,
                        SemanticOpcode.MOVE_SLOT,
                    }:
                        block_index = 2 if len(shape) > 2 else 0
                    else:
                        block_index = 0
                    block_id = block_ids[block_index]
                    prior = operations_by_block[block_index]
                    input_value = (
                        operation_values[prior[-1].id]
                        if prior
                        else (
                            seed if block_index == 0 else block_parameters[block_id][0]
                        )
                    )
                    op_id = next_id
                    next_id += 1
                    target_field = (
                        field_ids.get(("MutRef", "offset"), 0)
                        if opcode
                        in {SemanticOpcode.LOAD_FIELD, SemanticOpcode.STORE_FIELD}
                        else 0
                    )
                    callee_name = runtime_for_opcode.get(opcode)
                    callee = (
                        callable_by_name[callee_name][0]
                        if callee_name
                        else callback_function.id
                        if opcode == SemanticOpcode.CALL_CALLBACK and callback_function
                        else 0
                    )
                    value_type_by_id = {
                        SemanticValue.from_node(n).id: SemanticValue.from_node(
                            n
                        ).type_id
                        for n in semantic_nodes
                        if n.type_id == STDLIB_SEMANTIC_VALUE_TYPE
                    }
                    input_types = (
                        _semantic_parameter_types(callee_name, callable_by_name)
                        if callee_name
                        else (
                            ((U64_TYPE,) if name == "map" else (U64_TYPE, U64_TYPE))
                            if opcode == SemanticOpcode.CALL_CALLBACK
                            else (value_type_by_id.get(input_value, U64_TYPE),)
                        )
                    )
                    if iterator_loop and opcode == SemanticOpcode.ITER_CONDITION:
                        input_types = (U64_TYPE, U64_TYPE)
                    elif iterator_loop and opcode in {
                        SemanticOpcode.ITER_PROJECT,
                        SemanticOpcode.ITER_ADVANCE,
                    }:
                        input_types = (U64_TYPE,)
                    input_values = []
                    if (
                        opcode == SemanticOpcode.RETURN_VALUE
                        and SemanticOpcode.BEGIN_PRIVATE not in semantic_plan
                    ):
                        input_types = (value_type,)
                    for arg_index, arg_type in enumerate(input_types):
                        if opcode == SemanticOpcode.RETURN_VALUE and name in {
                            "cleanup",
                            "defer",
                        }:
                            value_id = next_id
                            next_id += 1
                            semantic_nodes.append(
                                SemanticValue(
                                    value_id,
                                    "cleanup_success_value",
                                    block_id,
                                    U64_TYPE,
                                    SemanticValueKind.LITERAL,
                                    function_id,
                                    block_id,
                                    literal=bytes(8),
                                ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                            )
                        elif (
                            opcode == SemanticOpcode.RETURN_VALUE
                            and SemanticOpcode.BEGIN_PRIVATE not in semantic_plan
                        ):
                            value_id = (
                                graph_parameters[0]
                                if value_type_by_id[graph_parameters[0]] == arg_type
                                else input_value
                            )
                        elif opcode == SemanticOpcode.CALL_CALLBACK:
                            value_id = (
                                input_value
                                if arg_index == 0
                                else block_parameters[block_id][-1]
                            )
                        elif iterator_loop and opcode == SemanticOpcode.ITER_CONDITION:
                            value_id = block_parameters[block_id][arg_index]
                        elif (
                            iterator_loop
                            and opcode
                            in {
                                SemanticOpcode.ITER_PROJECT,
                                SemanticOpcode.ITER_ADVANCE,
                            }
                            and arg_index == 0
                        ):
                            value_id = block_parameters[block_id][0]
                        elif (
                            name == "string_find"
                            and opcode == SemanticOpcode.SEARCH
                            and arg_index < 4
                        ):
                            value_id = graph_parameters[arg_index]
                        elif (
                            arg_type == NATIVE_TRANSACTION_TYPE
                            and opcode != SemanticOpcode.BEGIN_PRIVATE
                            and SemanticOpcode.BEGIN_PRIVATE not in semantic_plan
                        ):
                            value_id = next(
                                value
                                for value in graph_parameters
                                if value_type_by_id[value] == NATIVE_TRANSACTION_TYPE
                            )
                        elif (
                            arg_type == NATIVE_TRANSACTION_TYPE
                            and opcode != SemanticOpcode.BEGIN_PRIVATE
                            and SemanticOpcode.BEGIN_PRIVATE in semantic_plan
                        ):
                            value_id = next(
                                operation_values[prior_op.id]
                                for group in operations_by_block
                                for prior_op in group
                                if prior_op.opcode == SemanticOpcode.BEGIN_PRIVATE
                            )
                        elif (
                            name in {"hash_map_get", "hash_set_contains"}
                            and opcode == SemanticOpcode.HASH
                            and arg_index == 0
                        ):
                            value_id = graph_parameters[1]
                        elif (
                            name in {"hash_map_get", "hash_set_contains"}
                            and opcode == SemanticOpcode.LOOKUP
                            and arg_index == 0
                        ):
                            value_id = graph_parameters[0]
                        elif (
                            name in {"hash_map_get", "hash_set_contains"}
                            and opcode == SemanticOpcode.LOOKUP
                            and arg_index == 2
                        ):
                            value_id = input_value
                        elif (
                            name in {"hash_map_get", "hash_set_contains"}
                            and opcode == SemanticOpcode.LOOKUP
                            and arg_index == 3
                        ):
                            value_id = graph_parameters[1]
                        elif (
                            opcode == SemanticOpcode.ALLOCATE
                            and arg_index == 0
                            and arg_type == U64_TYPE
                        ):
                            value_id = graph_parameters[0]
                        elif arg_index == 0 and arg_type == U64_TYPE:
                            value_id = input_value
                        else:
                            value_id = next_id
                            next_id += 1
                            if arg_type == REF_TYPE:
                                object_id = (
                                    (function_id << 16) ^ op_id ^ arg_index
                                ) & 0xFFFF_FFFF_FFFF_FFFF
                                literal = ObjectHandle(object_id or 1, 1, 0).to_bytes()
                                value_kind = SemanticValueKind.OBJECT
                                source = object_id or 1
                            else:
                                number = (
                                    14695981039346656037
                                    if opcode == SemanticOpcode.HASH and arg_index == 1
                                    else function_id
                                    if opcode == SemanticOpcode.BEGIN_PRIVATE
                                    and arg_index == 1
                                    else 8
                                    if opcode == SemanticOpcode.ALLOCATE
                                    and arg_index in {1, 2}
                                    else 1
                                )
                                literal = number.to_bytes(8, "little")
                                value_kind = SemanticValueKind.LITERAL
                                source = 0
                            semantic_nodes.append(
                                SemanticValue(
                                    value_id,
                                    f"{opcode.name.lower()}_argument_{arg_index}",
                                    block_id,
                                    arg_type,
                                    value_kind,
                                    function_id,
                                    block_id,
                                    0,
                                    arg_index,
                                    source,
                                    literal,
                                ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                            )
                        input_values.append(value_id)
                    if (
                        opcode
                        in {SemanticOpcode.MAP_REFUSAL, SemanticOpcode.ENRICH_REFUSAL}
                        and parameter_types
                        and parameter_types[0] == TYPED_RESULT_U64_U32_TYPE
                    ):
                        input_types = (TYPED_RESULT_U64_U32_TYPE,)
                        input_values = [graph_parameters[0]]
                        target_field = tagged_fields[TYPED_RESULT_U64_U32_TYPE][1][1]
                    tagged_input_type = (
                        parameter_types[0]
                        if parameter_types and parameter_types[0] in tagged_fields
                        else value_type
                        if value_type in tagged_fields
                        else 0
                    )
                    if (
                        opcode
                        in {SemanticOpcode.READ_TAG, SemanticOpcode.PROJECT_PAYLOAD}
                        and tagged_input_type
                    ):
                        input_types = (tagged_input_type,)
                        tagged_source = (
                            graph_parameters[0]
                            if parameter_types[0] == tagged_input_type
                            else next(
                                (
                                    operation_values[item.id]
                                    for item in reversed(prior)
                                    if value_type_by_id.get(operation_values[item.id])
                                    == tagged_input_type
                                ),
                                input_values[0],
                            )
                        )
                        input_values = [tagged_source]
                        target_field = (
                            tagged_fields[tagged_input_type][0]
                            if opcode == SemanticOpcode.READ_TAG
                            else tagged_fields[tagged_input_type][1][0]
                        )
                    elif (
                        opcode
                        in {
                            SemanticOpcode.CONSTRUCT_OPTION,
                            SemanticOpcode.CONSTRUCT_RESULT,
                        }
                        and value_type in tagged_fields
                    ):
                        target_field = tagged_fields[value_type][1][
                            1 if opcode == SemanticOpcode.CONSTRUCT_OPTION else 0
                        ]
                    result_type = (
                        (BOOL_TYPE if name in {"stable_sort", "filter"} else U64_TYPE)
                        if opcode == SemanticOpcode.CALL_CALLBACK
                        else BOOL_TYPE
                        if iterator_loop and opcode == SemanticOpcode.ITER_CONDITION
                        else callable_by_name[callee_name][3]
                        if callee_name
                        else input_types[0]
                    )
                    if (
                        SemanticOpcode.READ_TAG in semantic_plan
                        and opcode == semantic_plan[0]
                        and opcode != SemanticOpcode.READ_TAG
                        and opcode != SemanticOpcode.HASH
                        and value_type in tagged_fields
                    ):
                        result_type = value_type
                    if (
                        opcode == SemanticOpcode.RETURN_VALUE
                        and value_type in tagged_fields
                        and input_values
                        and value_type_by_id.get(input_values[0]) != value_type
                    ):
                        input_types = (U64_TYPE,)
                    if opcode == SemanticOpcode.READ_TAG and tagged_input_type:
                        result_type = U32_TYPE
                    elif opcode == SemanticOpcode.PROJECT_PAYLOAD and tagged_input_type:
                        result_type = next(
                            v
                            for _n, v, tag in next(
                                spec[2]
                                for spec in tagged_type_specs
                                if spec[0] == tagged_input_type
                            )
                            if tag == 0
                        )
                    elif (
                        opcode
                        in {
                            SemanticOpcode.CONSTRUCT_OPTION,
                            SemanticOpcode.CONSTRUCT_RESULT,
                        }
                        and value_type in tagged_fields
                    ):
                        result_type = value_type
                    has_payload = not (callee_name in STATUS_ONLY_NATIVE_FUNCTIONS)
                    result_value = next_id if has_payload else 0
                    if has_payload:
                        next_id += 1
                    status_value = 0
                    if callee:
                        status_value = next_id
                        next_id += 1
                        semantic_nodes.append(
                            SemanticValue(
                                status_value,
                                f"{opcode.name.lower()}_status",
                                op_id,
                                U32_TYPE,
                                SemanticValueKind.OP_RESULT,
                                function_id,
                                block_id,
                                op_id,
                                1 if has_payload else 0,
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                        )
                    op = SemanticOperation(
                        op_id,
                        opcode.name.lower(),
                        block_id,
                        opcode,
                        len(prior),
                        input_types,
                        ((result_type,) if has_payload else ())
                        + ((U32_TYPE,) if status_value else ()),
                        tuple(input_values),
                        ((result_value,) if has_payload else ())
                        + ((status_value,) if status_value else ()),
                        status_value,
                        effects,
                        STDLIB_REFUSAL_SET_ID,
                        callee,
                        input_values[0] if input_values else 0,
                        target_field,
                        refusal_authorities.get(name, 0)
                        if opcode
                        in {SemanticOpcode.MAP_REFUSAL, SemanticOpcode.ENRICH_REFUSAL}
                        else canonical_slot_order_authority_id
                        if opcode == SemanticOpcode.CANONICAL_ORDER
                        else STDLIB_HASH_AUTHORITY_ID
                        if opcode in {SemanticOpcode.HASH, SemanticOpcode.LOOKUP}
                        and name in {"hash_map_get", "hash_set_contains"}
                        else 0,
                    )
                    operations_by_block[block_index].append(op)
                    operation_values[op_id] = (
                        result_value if has_payload else input_values[0]
                    )
                    if has_payload:
                        semantic_nodes.append(
                            SemanticValue(
                                result_value,
                                f"{opcode.name.lower()}_result",
                                op_id,
                                result_type,
                                SemanticValueKind.OP_RESULT,
                                function_id,
                                block_id,
                                op_id,
                                0,
                                result_id
                                if opcode == SemanticOpcode.RETURN_VALUE
                                else 0,
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                        )
                blocks = []
                for index, (terminator, successor_indexes) in enumerate(shape):
                    block_id = block_ids[index]
                    predecessors = tuple(
                        block_ids[p]
                        for p, (_t, succ) in enumerate(shape)
                        if index in succ
                    )
                    successors = tuple(block_ids[v] for v in successor_indexes)
                    ops = operations_by_block[index]
                    available = (
                        operation_values[ops[-1].id]
                        if ops
                        else (seed if index == 0 else block_parameters[block_id][0])
                    )
                    condition = 0
                    if terminator in {TerminatorKind.SWITCH_TAG, TerminatorKind.LOOP}:
                        condition = available
                    elif terminator == TerminatorKind.BRANCH:
                        predicate_value = next_id
                        next_id += 1
                        semantic_nodes.append(
                            SemanticValue(
                                predicate_value,
                                "explicit_branch_predicate",
                                block_id,
                                BOOL_TYPE,
                                SemanticValueKind.LITERAL,
                                function_id,
                                block_id,
                                literal=b"\x01",
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                        )
                        condition = predicate_value
                    terminal = (
                        available
                        if terminator in {TerminatorKind.RETURN, TerminatorKind.REFUSE}
                        else 0
                    )
                    available_type = next(
                        (
                            SemanticValue.from_node(n).type_id
                            for n in semantic_nodes
                            if n.type_id == STDLIB_SEMANTIC_VALUE_TYPE
                            and n.id == available
                        ),
                        U64_TYPE,
                    )
                    edge_value = available
                    if successors and available_type != U64_TYPE:
                        edge_value = next_id
                        next_id += 1
                        semantic_nodes.append(
                            SemanticValue(
                                edge_value,
                                "branch_status",
                                block_id,
                                U64_TYPE,
                                SemanticValueKind.LITERAL,
                                function_id,
                                block_id,
                                0,
                                0,
                                0,
                                (0).to_bytes(8, "little"),
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                        )
                    if iterator_loop and index == 0:
                        init_result = next(
                            operation_values[op.id]
                            for op in ops
                            if op.opcode == SemanticOpcode.ITER_INIT
                        )
                        initial_args = (init_result, iterator_end_value)
                        if iterator_extra_value:
                            initial_args += (iterator_extra_value,)
                        edge_args = (initial_args,)
                    elif iterator_loop and index == 1:
                        parameters = block_parameters[block_id]
                        edge_args = tuple(
                            parameters if successor_index == 0 else (parameters[-1],)
                            for successor_index, _successor in enumerate(successors)
                        )
                    elif iterator_loop and index == 2:
                        advance_result = next(
                            operation_values[op.id]
                            for op in reversed(ops)
                            if op.opcode == SemanticOpcode.ITER_ADVANCE
                        )
                        body_parameters = block_parameters[block_id]
                        backedge = (advance_result, body_parameters[1])
                        if len(body_parameters) == 3:
                            updated = next(
                                (
                                    operation_values[op.id]
                                    for op in reversed(ops)
                                    if op.opcode == SemanticOpcode.ACCUMULATE
                                    or (
                                        op.opcode == SemanticOpcode.CALL_CALLBACK
                                        and name in {"count", "map", "fold"}
                                    )
                                ),
                                body_parameters[2],
                            )
                            backedge += (updated,)
                        edge_args = (backedge,)
                    else:
                        edge_args = tuple((edge_value,) for _ in successors)
                    cases = (0, 1) if terminator == TerminatorKind.SWITCH_TAG else ()
                    block_parameter_types = tuple(
                        U64_TYPE for _ in block_parameters.get(block_id, ())
                    )
                    block = SemanticBlock(
                        block_id,
                        f"block_{index}",
                        graph_id,
                        index,
                        terminator,
                        tuple(op.id for op in ops),
                        predecessors,
                        successors,
                        block_parameter_types if predecessors else (),
                        block_parameters[block_id] if predecessors else (),
                        condition,
                        terminal,
                        edge_args,
                        cases,
                    )
                    blocks.append(block)
                # Split every fallible call into an immediate status branch. The
                # nonzero edge preserves the original uint32 status in one sink.
                call_count = sum(
                    bool(op.status_value_id)
                    for group in operations_by_block
                    for op in group
                )
                if call_count:
                    refusal_block_id = next_id
                    refusal_status_id = next_id + 1
                    next_id += 2
                    semantic_nodes.append(
                        SemanticValue(
                            refusal_status_id,
                            "primary_refusal_status",
                            refusal_block_id,
                            U32_TYPE,
                            SemanticValueKind.BLOCK_PARAMETER,
                            function_id,
                            refusal_block_id,
                        ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                    )
                    transformed = []
                    moved_operations = []
                    for original in blocks:
                        source_ops = operations_by_block[original.index]
                        current_id = original.id
                        current_parameters = original.parameter_value_ids
                        current_types = original.parameter_types
                        segment = []
                        for source_op in source_ops:
                            op = replace(
                                source_op, parent=current_id, index=len(segment)
                            )
                            segment.append(op)
                            moved_operations.append(op)
                            if op.status_value_id:
                                continuation_id = next_id
                                continuation_value = next_id + 1
                                next_id += 2
                                semantic_nodes.append(
                                    SemanticValue(
                                        continuation_value,
                                        "successful_call_status",
                                        continuation_id,
                                        U32_TYPE,
                                        SemanticValueKind.BLOCK_PARAMETER,
                                        function_id,
                                        continuation_id,
                                    ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                                )
                                transformed.append(
                                    SemanticBlock(
                                        current_id,
                                        f"status_{op.id}",
                                        graph_id,
                                        0,
                                        TerminatorKind.BRANCH,
                                        tuple(item.id for item in segment),
                                        (),
                                        (continuation_id, refusal_block_id),
                                        current_types,
                                        current_parameters,
                                        op.status_value_id,
                                        0,
                                        ((op.status_value_id,), (op.status_value_id,)),
                                    )
                                )
                                current_id = continuation_id
                                current_parameters = (continuation_value,)
                                current_types = (U32_TYPE,)
                                segment = []
                        transformed.append(
                            replace(
                                original,
                                id=current_id,
                                index=0,
                                operation_ids=tuple(item.id for item in segment),
                                parameter_types=current_types,
                                parameter_value_ids=current_parameters,
                            )
                        )
                    transformed.append(
                        SemanticBlock(
                            refusal_block_id,
                            "primary_refusal",
                            graph_id,
                            0,
                            TerminatorKind.REFUSE,
                            (),
                            (),
                            (),
                            (U32_TYPE,),
                            (refusal_status_id,),
                            0,
                            refusal_status_id,
                            (),
                        )
                    )
                    if SemanticOpcode.BEGIN_PRIVATE in semantic_plan:
                        next(
                            block
                            for block in transformed
                            if any(
                                op.id in block.operation_ids
                                and op.opcode == SemanticOpcode.BEGIN_PRIVATE
                                for op in moved_operations
                            )
                        )
                        rollback_status_block = next(
                            block
                            for block in transformed
                            if any(
                                op.id in block.operation_ids
                                and op.opcode == SemanticOpcode.ROLLBACK
                                for op in moved_operations
                            )
                        )
                        cleanup_status_block = next(
                            block
                            for block in transformed
                            if any(
                                op.id in block.operation_ids
                                and op.opcode == SemanticOpcode.CLEANUP
                                for op in moved_operations
                            )
                        )
                        terminal_parameter_ids = {
                            *rollback_status_block.parameter_value_ids,
                            *cleanup_status_block.parameter_value_ids,
                        }
                        semantic_nodes = [
                            replace(
                                SemanticValue.from_node(node), type_id=U32_TYPE
                            ).to_node(STDLIB_SEMANTIC_VALUE_TYPE)
                            if node.type_id == STDLIB_SEMANTIC_VALUE_TYPE
                            and node.id in terminal_parameter_ids
                            else node
                            for node in semantic_nodes
                        ]
                        transformed = [
                            replace(
                                block,
                                parameter_types=tuple(
                                    U32_TYPE for _ in block.parameter_value_ids
                                ),
                            )
                            if block.id
                            in {rollback_status_block.id, cleanup_status_block.id}
                            else block
                            for block in transformed
                        ]
                        rollback_status_block = next(
                            block
                            for block in transformed
                            if block.id == rollback_status_block.id
                        )
                        cleanup_status_block = next(
                            block
                            for block in transformed
                            if block.id == cleanup_status_block.id
                        )
                        value_types = {
                            value.id: value.type_id
                            for node in semantic_nodes
                            if node.type_id == STDLIB_SEMANTIC_VALUE_TYPE
                            for value in (SemanticValue.from_node(node),)
                        }

                        def carried_status(
                            block: SemanticBlock,
                            opcode: SemanticOpcode | None,
                            operation: SemanticOperation | None,
                            value_types: dict[int, int] = value_types,
                        ) -> int:
                            # Rollback and cleanup branch on their secondary native
                            # status, but the lifecycle edge carries the original
                            # refusal that entered the terminal chain.
                            if (
                                opcode
                                in {
                                    SemanticOpcode.ROLLBACK,
                                    SemanticOpcode.CLEANUP,
                                }
                                and block.parameter_value_ids
                            ):
                                return block.parameter_value_ids[0]
                            if operation is not None and operation.status_value_id:
                                return operation.status_value_id
                            statuses = tuple(
                                value_id
                                for value_id in block.parameter_value_ids
                                if value_types.get(value_id) == U32_TYPE
                            )
                            if len(statuses) != 1:
                                raise ValueError(
                                    f"SemanticBlock {block.id} has no unique carried status"
                                )
                            return statuses[0]

                        rewritten = []
                        for block in transformed:
                            block_ops = [
                                op
                                for op in moved_operations
                                if op.id in block.operation_ids
                            ]
                            operation = block_ops[-1] if block_ops else None
                            opcode = operation.opcode if operation else None
                            successor_ids = block.successor_ids
                            successor_arguments = block.successor_arguments
                            if (
                                block.terminator == TerminatorKind.BRANCH
                                and len(successor_ids) == 2
                                and operation is not None
                                and operation.status_value_id
                            ):
                                failure_target = (
                                    refusal_block_id
                                    if opcode == SemanticOpcode.BEGIN_PRIVATE
                                    else cleanup_status_block.id
                                    if opcode == SemanticOpcode.ROLLBACK
                                    else refusal_block_id
                                    if opcode == SemanticOpcode.CLEANUP
                                    else rollback_status_block.id
                                )
                                successor_ids = (successor_ids[0], failure_target)
                            role_targets = {
                                rollback_status_block.id,
                                cleanup_status_block.id,
                                refusal_block_id,
                            }
                            successor_arguments = tuple(
                                ((carried_status(block, opcode, operation),))
                                if target in role_targets
                                else arguments
                                for target, arguments in zip(
                                    successor_ids, successor_arguments
                                )
                            )
                            rewritten.append(
                                replace(
                                    block,
                                    successor_ids=successor_ids,
                                    successor_arguments=successor_arguments,
                                )
                            )
                        transformed = rewritten
                    # Original IDs remain the first segment entry; existing edges target them.
                    transformed = [
                        replace(block, index=index)
                        for index, block in enumerate(transformed)
                    ]
                    predecessor_map = {block.id: [] for block in transformed}
                    for block in transformed:
                        for target in block.successor_ids:
                            predecessor_map[target].append(block.id)
                    blocks = [
                        replace(
                            block,
                            predecessor_ids=tuple(sorted(predecessor_map[block.id])),
                        )
                        for block in transformed
                    ]
                    block_ids = tuple(block.id for block in blocks)
                    for block in blocks:
                        semantic_nodes.append(block.to_node(STDLIB_SEMANTIC_BLOCK_TYPE))
                    semantic_nodes.extend(
                        op.to_node(STDLIB_SEMANTIC_OPERATION_TYPE)
                        for op in moved_operations
                    )
                else:
                    for block in blocks:
                        semantic_nodes.append(block.to_node(STDLIB_SEMANTIC_BLOCK_TYPE))
                    semantic_nodes.extend(
                        op.to_node(STDLIB_SEMANTIC_OPERATION_TYPE)
                        for group in operations_by_block
                        for op in group
                    )
                terminal_blocks = [
                    b for b in blocks if b.terminator == TerminatorKind.RETURN
                ]
                graph_result = terminal_blocks[0].terminator_value_id
                semantic_nodes.append(
                    SemanticGraph(
                        graph_id,
                        f"{name}_graph",
                        function_id,
                        function_id,
                        block_ids[0],
                        block_ids,
                        STDLIB_REFUSAL_SET_ID,
                        effects,
                        value_type,
                        tuple(graph_parameters),
                        (graph_result,),
                    ).to_node(STDLIB_SEMANTIC_GRAPH_TYPE)
                )
            abi_ids: tuple[int, ...] | None = None
            code_ids: tuple[int, ...] = ()
            abi_location_ids: dict[int, tuple[int, ...]] = {}
            if native_symbol or semantic_plan:
                abi_ids = (
                    next_id,
                    next_id + 1,
                    STDLIB_AARCH64_LINUX_ABI_BASE + function_id,
                    STDLIB_X86_64_UEFI_ABI_BASE + function_id,
                )
                next_id += 2
                for abi_id in abi_ids:
                    count = (
                        len(parameter_ids) + 1 + int(name in NATIVE_OUTPUT_FUNCTIONS)
                        if native_symbol
                        else len(parameter_ids) + 3
                    )
                    if abi_id >= STDLIB_X86_64_UEFI_ABI_BASE:
                        base = STDLIB_X86_64_UEFI_LOCATION_BASE + function_id * 16
                        abi_location_ids[abi_id] = tuple(range(base, base + count))
                    elif abi_id >= STDLIB_AARCH64_LINUX_ABI_BASE:
                        base = STDLIB_AARCH64_LINUX_LOCATION_BASE + function_id * 16
                        abi_location_ids[abi_id] = tuple(range(base, base + count))
                    else:
                        abi_location_ids[abi_id] = tuple(
                            range(next_id, next_id + count)
                        )
                        next_id += count
                if native_symbol:
                    code_ids = (
                        next_id,
                        next_id + 1,
                        STDLIB_AARCH64_LINUX_CODE_BASE + function_id,
                        STDLIB_X86_64_UEFI_CODE_BASE + function_id,
                    )
                    next_id += 2
            implementation = (
                FunctionImplementation.CODE_BACKED
                if native_symbol
                else FunctionImplementation.COMPOSED
            )
            digest = semantic_digest(name, signature_id, effects, 1)
            nodes.append(
                FunctionObject(
                    function_id,
                    name,
                    parent,
                    signature_id,
                    implementation,
                    FunctionLayer.FOUNDATION
                    if parent == STDLIB_FOUNDATION_MODULE_ID
                    else FunctionLayer.LIBRARY,
                    effects,
                    body_id=body_id,
                    implementation_ids=code_ids,
                    semantic_digest_override=digest,
                ).to_node()
            )
            nodes.extend(semantic_nodes)
            if native_symbol and abi_ids is not None:
                assert len(code_ids) == 4 and len(abi_ids) == 4
                code_x, code_a, code_linux, code_uefi_x = code_ids
                classes = tuple(
                    ABIClass.POINTER if value == REF_TYPE else ABIClass.INTEGER
                    for value in parameter_types
                )
                abi_x, abi_a, abi_linux, abi_uefi_x = abi_ids
                nodes.extend(
                    (
                        ABISignature(
                            abi_x,
                            "sysv_status_out",
                            function_id,
                            X86_64_LINUX_TARGET_ID,
                            ABIKind.SYSV_X86_64,
                            classes,
                            ABIClass.POINTER,
                            abi_location_ids[abi_x],
                        ).to_node(),
                        ABISignature(
                            abi_a,
                            "aapcs64_status_out",
                            function_id,
                            AARCH64_UEFI_TARGET_ID,
                            ABIKind.AAPCS64,
                            classes,
                            ABIClass.POINTER,
                            abi_location_ids[abi_a],
                        ).to_node(),
                        ABISignature(
                            abi_uefi_x,
                            "sysv_uefi_status_out",
                            function_id,
                            X86_64_UEFI_TARGET_ID,
                            ABIKind.SYSV_X86_64,
                            classes,
                            ABIClass.POINTER,
                            abi_location_ids[abi_uefi_x],
                        ).to_node(),
                        ABISignature(
                            abi_linux,
                            "aapcs64_linux_status_out",
                            function_id,
                            AARCH64_LINUX_TARGET_ID,
                            ABIKind.AAPCS64,
                            classes,
                            ABIClass.POINTER,
                            abi_location_ids[abi_linux],
                        ).to_node(),
                    )
                )
                type_layout = {
                    REF_TYPE: (24, 8),
                    NATIVE_TRANSACTION_TYPE: (48, 8),
                    U64_TYPE: (8, 8),
                    U32_TYPE: (4, 4),
                    U8_TYPE: (1, 1),
                    I32_TYPE: (4, 4),
                }
                for abi_id, abi_kind in (
                    (abi_x, ABIKind.SYSV_X86_64),
                    (abi_a, ABIKind.AAPCS64),
                    (abi_linux, ABIKind.AAPCS64),
                    (abi_uefi_x, ABIKind.SYSV_X86_64),
                ):
                    locations = []
                    int_reg = 0
                    stack = 0
                    int_limit = 6 if abi_kind == ABIKind.SYSV_X86_64 else 8
                    ids = iter(abi_location_ids[abi_id])

                    def physical(
                        role,
                        passing,
                        vclass,
                        width,
                        align,
                        chunks,
                        association,
                        index,
                        pointee,
                        hidden=False,
                        mutable=False,
                        output=False,
                        name="location",
                        force_stack=False,
                        int_limit=int_limit,
                        locations=locations,
                        ids=ids,
                        abi_id=abi_id,
                    ):
                        nonlocal int_reg, stack
                        needed = chunks
                        if role == ABIRole.STATUS_RETURN:
                            bank = ABIRegisterBank.RETURN
                            registers = (0,)
                            offset = 0
                        elif not force_stack and int_reg + needed <= int_limit:
                            bank = ABIRegisterBank.INTEGER
                            registers = tuple(range(int_reg, int_reg + needed))
                            offset = 0
                            int_reg += needed
                        else:
                            bank = ABIRegisterBank.STACK
                            registers = ()
                            stack = (stack + align - 1) & -align
                            offset = stack
                            stack += ((width + 7) // 8) * 8
                        locations.append(
                            ABIValueLocation(
                                next(ids),
                                name,
                                abi_id,
                                association,
                                index,
                                role,
                                passing,
                                vclass,
                                width,
                                align,
                                chunks,
                                bank,
                                registers,
                                offset,
                                pointee,
                                hidden,
                                mutable,
                                output,
                            )
                        )

                    contextful = name in RUNTIME_CONTEXT_FUNCTIONS
                    parameter_start = 0
                    if contextful:
                        physical(
                            ABIRole.HIDDEN_CONTEXT,
                            ABIPassingMode.INDIRECT_BY_REFERENCE,
                            ABIClass.POINTER,
                            8,
                            8,
                            1,
                            parameter_ids[0],
                            0,
                            REF_TYPE,
                            True,
                            False,
                            False,
                            "hidden_context",
                        )
                        parameter_start = 1
                    for index, (parameter_id, ptype) in enumerate(
                        zip(parameter_ids, parameter_types, strict=True)
                    ):
                        if index < parameter_start:
                            continue
                        width, align = type_layout.get(ptype, (8, 8))
                        passing = ABIPassingMode.DIRECT_SCALAR
                        chunks = 1
                        vclass = ABIClass.INTEGER
                        pointee = 0
                        if index in TRANSACTION_PARAMETERS.get(name, ()):
                            passing = ABIPassingMode.TRANSACTION_POINTER
                            vclass = ABIClass.POINTER
                            pointee = ptype
                            width = 8
                            chunks = 1
                        elif index in RUNTIME_HANDLE_PARAMETERS.get(name, ()):
                            pointee = ptype
                            vclass = ABIClass.POINTER
                            if abi_kind == ABIKind.SYSV_X86_64:
                                passing = ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
                                chunks = 3
                            else:
                                passing = ABIPassingMode.INDIRECT_BY_REFERENCE
                                width = 8
                                chunks = 1
                        elif ptype == REF_TYPE:
                            passing = ABIPassingMode.RESOLVE_OBJECT_HANDLE
                            vclass = ABIClass.POINTER
                            pointee = ptype
                            width = 8
                            chunks = 1
                        physical(
                            ABIRole.SEMANTIC_ARGUMENT,
                            passing,
                            vclass,
                            width,
                            align,
                            chunks,
                            parameter_id,
                            index,
                            ptype if pointee else 0,
                            False,
                            False,
                            False,
                            f"argument_{index}",
                            abi_kind == ABIKind.SYSV_X86_64
                            and passing == ABIPassingMode.DIRECT_AGGREGATE_CHUNKS
                            and width > 16,
                        )
                    if name in NATIVE_OUTPUT_FUNCTIONS:
                        physical(
                            ABIRole.TRANSIENT_OUTPUT,
                            ABIPassingMode.INDIRECT_BY_REFERENCE,
                            ABIClass.POINTER,
                            type_layout.get(value_type, (8, 8))[0],
                            type_layout.get(value_type, (8, 8))[1],
                            1,
                            result_id,
                            0,
                            value_type,
                            True,
                            True,
                            True,
                            "output",
                        )
                    physical(
                        ABIRole.STATUS_RETURN,
                        ABIPassingMode.DIRECT_SCALAR,
                        ABIClass.INTEGER,
                        4,
                        4,
                        1,
                        0,
                        0,
                        U32_TYPE,
                        True,
                        False,
                        False,
                        "status",
                    )
                    nodes.extend(location.to_node() for location in locations)
                for code_id, code_name, target_id, raw, abi_id in (
                    (
                        code_x,
                        "x86_64_linux",
                        X86_64_LINUX_TARGET_ID,
                        STDLIB_X86_64[native_symbol],
                        abi_x,
                    ),
                    (
                        code_a,
                        "aarch64_uefi",
                        AARCH64_UEFI_TARGET_ID,
                        STDLIB_AARCH64[native_symbol],
                        abi_a,
                    ),
                    (
                        code_uefi_x,
                        "x86_64_uefi",
                        X86_64_UEFI_TARGET_ID,
                        STDLIB_X86_64[native_symbol],
                        abi_uefi_x,
                    ),
                    (
                        code_linux,
                        "aarch64_linux",
                        AARCH64_LINUX_TARGET_ID,
                        STDLIB_AARCH64[native_symbol],
                        abi_linux,
                    ),
                ):
                    nodes.append(
                        CodeObject(
                            code_id,
                            code_name,
                            function_id,
                            function_id,
                            target_id,
                            CodeFormat.NATIVE,
                            raw,
                            signature_id,
                            effects,
                            1,
                            digest,
                            abi_signature_id=abi_id,
                            provenance_id=provenance_id,
                        ).to_node()
                    )
            flags = StdlibFlags.OUTPUT_UNCHANGED_ON_REFUSAL
            if native_symbol:
                flags |= StdlibFlags.NATIVE_FOUNDATION
            elif alias_target:
                flags |= StdlibFlags.DECLARED_COMPOSED
            else:
                flags |= StdlibFlags.DECLARED_COMPOSED
            nodes.append(
                StdlibRecord(
                    next_id,
                    f"{name}_contract",
                    parent,
                    kind,
                    value_type,
                    0,
                    0,
                    flags,
                    (function_id,),
                ).to_node(STDLIB_RECORD_TYPE)
            )
            next_id += 1
            if semantic_plan and abi_ids is not None:
                type_layout = {
                    REF_TYPE: (24, 8),
                    NATIVE_TRANSACTION_TYPE: (48, 8),
                    FUNCTION_TYPE: (72, 8),
                    OPTION_U32_TYPE: (8, 4),
                    OPTION_U64_TYPE: (16, 8),
                    OPTION_REF_TYPE: (32, 8),
                    TYPED_RESULT_U64_U32_TYPE: (16, 8),
                    U64_TYPE: (8, 8),
                    U32_TYPE: (4, 4),
                    U8_TYPE: (1, 1),
                    I32_TYPE: (4, 4),
                    BOOL_TYPE: (1, 1),
                    BYTES_TYPE: (0, 1),
                }
                classes = tuple(
                    ABIClass.POINTER
                    if value in {REF_TYPE, FUNCTION_TYPE}
                    else ABIClass.INTEGER
                    for value in parameter_types
                )
                for abi_id, target_id, abi_kind in (
                    (abi_ids[0], X86_64_LINUX_TARGET_ID, ABIKind.SYSV_X86_64),
                    (abi_ids[1], AARCH64_UEFI_TARGET_ID, ABIKind.AAPCS64),
                    (abi_ids[2], AARCH64_LINUX_TARGET_ID, ABIKind.AAPCS64),
                    (abi_ids[3], X86_64_UEFI_TARGET_ID, ABIKind.SYSV_X86_64),
                ):
                    ids = iter(abi_location_ids[abi_id])
                    locations = []
                    int_reg = 0
                    stack = 0
                    int_limit = 6 if abi_kind == ABIKind.SYSV_X86_64 else 8

                    def add_location(
                        role,
                        association,
                        index,
                        width,
                        align,
                        passing,
                        vclass,
                        pointee=0,
                        hidden=False,
                        mutable=False,
                        output=False,
                        force_stack=False,
                        location_name="location",
                        int_limit=int_limit,
                        locations=locations,
                        ids=ids,
                        abi_id=abi_id,
                    ):
                        nonlocal int_reg, stack
                        if role == ABIRole.STATUS_RETURN:
                            bank, registers, offset = ABIRegisterBank.RETURN, (0,), 0
                        elif not force_stack and int_reg < int_limit:
                            bank, registers, offset = (
                                ABIRegisterBank.INTEGER,
                                (int_reg,),
                                0,
                            )
                            int_reg += 1
                        else:
                            bank, registers = ABIRegisterBank.STACK, ()
                            stack = (stack + max(8, align) - 1) & -max(8, align)
                            offset = stack
                            stack += ((max(width, 8) + 7) // 8) * 8
                        locations.append(
                            ABIValueLocation(
                                next(ids),
                                location_name,
                                abi_id,
                                association,
                                index,
                                role,
                                passing,
                                vclass,
                                width,
                                align,
                                1,
                                bank,
                                registers,
                                offset,
                                pointee,
                                hidden,
                                mutable,
                                output,
                            )
                        )

                    add_location(
                        ABIRole.HIDDEN_CONTEXT,
                        0,
                        0,
                        8,
                        8,
                        ABIPassingMode.INDIRECT_BY_REFERENCE,
                        ABIClass.POINTER,
                        REF_TYPE,
                        True,
                        False,
                        False,
                        location_name="hidden_context",
                    )
                    for index, (parameter_id, parameter_type) in enumerate(
                        zip(parameter_ids, parameter_types, strict=True)
                    ):
                        width, align = type_layout.get(parameter_type, (8, 8))
                        if parameter_type in {
                            OPTION_U32_TYPE,
                            OPTION_U64_TYPE,
                            OPTION_REF_TYPE,
                            TYPED_RESULT_U64_U32_TYPE,
                        }:
                            passing, vclass, physical_width, pointee = (
                                ABIPassingMode.INDIRECT_BY_REFERENCE,
                                ABIClass.POINTER,
                                8,
                                parameter_type,
                            )
                        elif parameter_type == REF_TYPE:
                            passing, vclass, physical_width, pointee = (
                                ABIPassingMode.RESOLVE_OBJECT_HANDLE,
                                ABIClass.POINTER,
                                8,
                                REF_TYPE,
                            )
                        elif parameter_type == FUNCTION_TYPE:
                            passing, vclass, physical_width, pointee = (
                                ABIPassingMode.CALL_FRAME_SLOT,
                                ABIClass.POINTER,
                                8,
                                FUNCTION_TYPE,
                            )
                        else:
                            passing, vclass, physical_width, pointee = (
                                ABIPassingMode.DIRECT_SCALAR,
                                ABIClass.INTEGER,
                                width,
                                0,
                            )
                        add_location(
                            ABIRole.SEMANTIC_ARGUMENT,
                            parameter_id,
                            index,
                            physical_width,
                            align,
                            passing,
                            vclass,
                            pointee,
                            location_name=f"argument_{index}",
                        )
                    result_width, result_align = type_layout.get(value_type, (8, 8))
                    add_location(
                        ABIRole.TRANSIENT_OUTPUT,
                        result_id,
                        0,
                        result_width,
                        result_align,
                        ABIPassingMode.INDIRECT_BY_REFERENCE,
                        ABIClass.POINTER,
                        value_type,
                        True,
                        True,
                        True,
                        location_name="output",
                    )
                    add_location(
                        ABIRole.STATUS_RETURN,
                        0,
                        0,
                        4,
                        4,
                        ABIPassingMode.DIRECT_SCALAR,
                        ABIClass.INTEGER,
                        U32_TYPE,
                        True,
                        location_name="status",
                    )
                    nodes.append(
                        ABISignature(
                            abi_id,
                            "sysv_composed_entry"
                            if abi_kind == ABIKind.SYSV_X86_64
                            else "aapcs64_composed_entry",
                            function_id,
                            target_id,
                            abi_kind,
                            classes,
                            ABIClass.POINTER,
                            abi_location_ids[abi_id],
                        ).to_node()
                    )
                    nodes.extend(location.to_node() for location in locations)
            callable_by_name[name] = (
                function_id,
                parameter_ids,
                parameter_types,
                value_type,
            )

    nodes.append(
        HashAuthority(
            STDLIB_HASH_AUTHORITY_ID,
            "fnv1a64_u64_le_v1",
            STDLIB_BASIC_MODULE_ID,
            callable_by_name["hash_u64"][0],
            callable_by_name["runtime_hash_map_lookup"][0],
            callable_by_name["runtime_hash_set_lookup"][0],
            U64_TYPE,
            U64_TYPE,
        ).to_node(STDLIB_HASH_AUTHORITY_TYPE)
    )
    nodes.append(
        HashStorageAuthority(
            hash_storage_authority_id,
            "linear_power_of_two_storage_v1",
            STDLIB_BASIC_MODULE_ID,
            type_ids["HashMap"],
            type_ids["HashSet"],
            HASH_MAP_BUCKET_TYPE,
            HASH_SET_BUCKET_TYPE,
            HASH_BUCKET_STATE_TYPE,
            hash_algorithm_id=STDLIB_HASH_AUTHORITY_ID,
        ).to_node(STDLIB_HASH_STORAGE_AUTHORITY_TYPE)
    )

    # Useful semantic application: allocate -> mutate Vector -> UTF-8 format -> find -> cleanup.
    nodes.append(
        _module(
            ModuleObject(
                STDLIB_RECEIPT_MODULE_ID,
                "receipt",
                RXF_MODULE_ID,
                ModuleCategory.APPLICATION,
            )
        )
    )
    receipt_signature = 90003
    receipt_result = 90004
    nodes.extend(
        (
            NodeDef(
                90007,
                "receipt_prefix_bytes",
                NodeKind.DATA,
                STDLIB_RECEIPT_MODULE_ID,
                type_id=BYTES_TYPE,
                data=b"Receipt total: ",
            ),
            NodeDef(
                90008,
                "receipt_newline_bytes",
                NodeKind.DATA,
                STDLIB_RECEIPT_MODULE_ID,
                type_id=BYTES_TYPE,
                data=b"\n",
            ),
            NodeDef(
                STDLIB_RECEIPT_EXPECTED_ID,
                "expected_utf8",
                NodeKind.DATA,
                STDLIB_RECEIPT_MODULE_ID,
                type_id=BYTES_TYPE,
                data=STDLIB_RECEIPT_BYTES,
            ),
            ResultSlot(
                receipt_result, "result", receipt_signature, U64_TYPE, 0
            ).to_node(),
            SignatureObject(
                receipt_signature,
                "signature",
                STDLIB_RECEIPT_FUNCTION_ID,
                U64_TYPE,
                (),
                (receipt_result,),
                STDLIB_REFUSAL_SET_ID,
            ).to_node(),
        )
    )
    # The receipt SemanticGraph is the lowering authority. Retain one exact
    # checked compatibility entry Call for Function.body_id.
    terminal_call = 90021
    nodes.append(
        CallObject(
            terminal_call,
            "semantic_graph_compatibility_entry",
            STDLIB_RECEIPT_FUNCTION_ID,
            STDLIB_RECEIPT_FUNCTION_ID,
            (),
            U64_TYPE,
        ).to_node()
    )
    nodes.append(
        FunctionObject(
            STDLIB_RECEIPT_FUNCTION_ID,
            "build_receipt",
            STDLIB_RECEIPT_MODULE_ID,
            receipt_signature,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            Effect.ALLOCATE | Effect.READ_MEMORY | Effect.WRITE_MEMORY,
            body_id=terminal_call,
        ).to_node()
    )
    receipt_graph, receipt_entry, receipt_success, receipt_refusal = (
        90030,
        90031,
        90032,
        90033,
    )
    receipt_opcodes = (
        SemanticOpcode.BEGIN_PRIVATE,
        SemanticOpcode.ALLOCATE,
        SemanticOpcode.APPEND_BYTES,
        SemanticOpcode.FORMAT_INTEGER,
        SemanticOpcode.APPEND_BYTES,
        SemanticOpcode.UTF8_VALIDATE,
        SemanticOpcode.SEARCH,
        SemanticOpcode.VALIDATE,
        SemanticOpcode.PUBLISH,
        SemanticOpcode.ROLLBACK,
        SemanticOpcode.CLEANUP,
        SemanticOpcode.RETURN_VALUE,
    )
    receipt_values = []

    def receipt_value(
        value_id, name, type_id, kind, block=0, op=0, index=0, source=0, literal=b""
    ):
        value = SemanticValue(
            value_id,
            name,
            receipt_graph,
            type_id,
            kind,
            STDLIB_RECEIPT_FUNCTION_ID,
            block,
            op,
            index,
            source,
            literal,
        )
        receipt_values.append(value)
        return value_id

    capacity = receipt_value(
        90060,
        "capacity",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(64).to_bytes(8, "little"),
    )
    prefix = receipt_value(
        90061,
        "prefix_handle",
        REF_TYPE,
        SemanticValueKind.OBJECT,
        receipt_entry,
        source=90007,
        literal=ObjectHandle(90007, 1, 0).to_bytes(),
    )
    total = receipt_value(
        90062,
        "decimal_input",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(570).to_bytes(8, "little"),
    )
    expected = receipt_value(
        90063,
        "decimal_expected",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(570).to_bytes(8, "little"),
    )
    prefix_len = receipt_value(
        90064,
        "prefix_length",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(15).to_bytes(8, "little"),
    )
    one = receipt_value(
        90068,
        "one",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(1).to_bytes(8, "little"),
    )
    zero = receipt_value(
        90083,
        "zero_offset",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=bytes(8),
    )
    alignment = receipt_value(
        90084,
        "alignment",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(8).to_bytes(8, "little"),
    )
    object_id_value = receipt_value(
        90085,
        "receipt_object_id",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(90090).to_bytes(8, "little"),
    )
    total_u32 = receipt_value(
        90086,
        "decimal_u32",
        U32_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(570).to_bytes(4, "little"),
    )
    final_length = receipt_value(
        90088,
        "final_length",
        U64_TYPE,
        SemanticValueKind.LITERAL,
        receipt_entry,
        literal=(19).to_bytes(8, "little"),
    )
    transaction = receipt_value(
        90089,
        "transaction_handle",
        REF_TYPE,
        SemanticValueKind.OBJECT,
        receipt_entry,
        source=90090,
        literal=ObjectHandle(90090, 1, 0).to_bytes(),
    )
    newline = receipt_value(
        90065,
        "newline_handle",
        REF_TYPE,
        SemanticValueKind.OBJECT,
        receipt_entry,
        source=90008,
        literal=ObjectHandle(90008, 1, 0).to_bytes(),
    )
    success_phi = receipt_value(
        90066, "published", U32_TYPE, SemanticValueKind.BLOCK_PARAMETER, receipt_success
    )
    refusal_phi = receipt_value(
        90067,
        "refusal_status",
        U32_TYPE,
        SemanticValueKind.BLOCK_PARAMETER,
        receipt_refusal,
    )
    receipt_ops = []
    current = capacity
    for index, opcode in enumerate(receipt_opcodes):
        block = (
            receipt_refusal
            if opcode in {SemanticOpcode.ROLLBACK, SemanticOpcode.CLEANUP}
            else receipt_success
            if opcode in {SemanticOpcode.PUBLISH, SemanticOpcode.RETURN_VALUE}
            else receipt_entry
        )
        if block == receipt_success:
            prior = [op for op in receipt_ops if op.parent == receipt_success]
            current = (
                success_phi
                if not prior
                else (
                    next(
                        (
                            v
                            for v in prior[-1].result_value_ids
                            if v != prior[-1].status_value_id
                        ),
                        success_phi,
                    )
                )
            )
        if block == receipt_refusal:
            prior = [op for op in receipt_ops if op.parent == receipt_refusal]
            current = (
                refusal_phi
                if not prior
                else (
                    next(
                        (
                            v
                            for v in prior[-1].result_value_ids
                            if v != prior[-1].status_value_id
                        ),
                        refusal_phi,
                    )
                )
            )
        inputs = (current,)
        if opcode == SemanticOpcode.APPEND_BYTES:
            prior_appends = sum(
                o.opcode == SemanticOpcode.APPEND_BYTES for o in receipt_ops
            )
            inputs = (
                (current, prefix, prefix_len)
                if not prior_appends
                else (current, newline, one)
            )
        elif opcode == SemanticOpcode.FORMAT_INTEGER:
            inputs = (current, total, expected)
        elif opcode == SemanticOpcode.UTF8_VALIDATE:
            inputs = (current, newline)
        op_id = 90070 + index
        result_id = 90100 + index
        pending_handle = 90101
        append_index = sum(o.opcode == SemanticOpcode.APPEND_BYTES for o in receipt_ops)
        if opcode == SemanticOpcode.BEGIN_PRIVATE:
            inputs = (transaction, object_id_value)
        elif opcode == SemanticOpcode.ALLOCATE:
            inputs = (object_id_value, zero, capacity, alignment, transaction)
        elif opcode == SemanticOpcode.APPEND_BYTES:
            inputs = (
                (pending_handle, transaction, prefix, prefix_len)
                if append_index == 0
                else (pending_handle, transaction, newline, one)
            )
        elif opcode == SemanticOpcode.FORMAT_INTEGER:
            inputs = (pending_handle, transaction, total_u32)
        elif opcode == SemanticOpcode.UTF8_VALIDATE:
            inputs = (pending_handle, transaction, zero, final_length)
        elif opcode == SemanticOpcode.SEARCH:
            inputs = (pending_handle, final_length, prefix, prefix_len, transaction)
        elif opcode == SemanticOpcode.VALIDATE:
            inputs = (pending_handle, final_length)
        elif opcode in {
            SemanticOpcode.PUBLISH,
            SemanticOpcode.ROLLBACK,
            SemanticOpcode.CLEANUP,
        }:
            inputs = (transaction,)
        receipt_callee_name = {
            SemanticOpcode.BEGIN_PRIVATE: "runtime_begin_private",
            SemanticOpcode.ALLOCATE: "runtime_allocate_prepare",
            SemanticOpcode.WRITE: "runtime_write",
            SemanticOpcode.APPEND_BYTES: "runtime_append",
            SemanticOpcode.FORMAT_INTEGER: "runtime_format_u32",
            SemanticOpcode.UTF8_VALIDATE: "runtime_read",
            SemanticOpcode.SEARCH: "runtime_search",
            SemanticOpcode.VALIDATE: "runtime_resolve",
            SemanticOpcode.PUBLISH: "runtime_publish",
            SemanticOpcode.ROLLBACK: "runtime_rollback",
            SemanticOpcode.CLEANUP: "runtime_cleanup",
        }.get(opcode)
        receipt_result_type = (
            callable_by_name[receipt_callee_name][3]
            if receipt_callee_name
            else U64_TYPE
        )
        receipt_has_payload = receipt_callee_name not in STATUS_ONLY_NATIVE_FUNCTIONS
        receipt_status_id = 0
        if receipt_callee_name:
            receipt_status_id = 90500 + index
            receipt_value(
                receipt_status_id,
                f"{opcode.name.lower()}_status",
                U32_TYPE,
                SemanticValueKind.OP_RESULT,
                block,
                op_id,
                index=1 if receipt_has_payload else 0,
            )
        if receipt_has_payload:
            receipt_value(
                result_id,
                f"{opcode.name.lower()}_result",
                receipt_result_type,
                SemanticValueKind.OP_RESULT,
                block,
                op_id,
                source=receipt_result if opcode == SemanticOpcode.RETURN_VALUE else 0,
            )
        receipt_callee_name = {
            SemanticOpcode.BEGIN_PRIVATE: "runtime_begin_private",
            SemanticOpcode.ALLOCATE: "runtime_allocate_prepare",
            SemanticOpcode.WRITE: "runtime_write",
            SemanticOpcode.APPEND_BYTES: "runtime_append",
            SemanticOpcode.FORMAT_INTEGER: "runtime_format_u32",
            SemanticOpcode.UTF8_VALIDATE: "runtime_read",
            SemanticOpcode.SEARCH: "runtime_search",
            SemanticOpcode.VALIDATE: "runtime_resolve",
            SemanticOpcode.PUBLISH: "runtime_publish",
            SemanticOpcode.ROLLBACK: "runtime_rollback",
            SemanticOpcode.CLEANUP: "runtime_cleanup",
        }.get(opcode)
        receipt_types = (
            _semantic_parameter_types(receipt_callee_name, callable_by_name)
            if receipt_callee_name
            else tuple(
                BYTES_TYPE if v in {prefix, newline} else U64_TYPE for v in inputs
            )
        )
        supplied = []
        existing = {value.id: value for value in receipt_values}
        for arg_index, arg_type in enumerate(receipt_types):
            candidate = inputs[arg_index] if arg_index < len(inputs) else 0
            if (
                arg_type == NATIVE_TRANSACTION_TYPE
                and opcode != SemanticOpcode.BEGIN_PRIVATE
            ):
                candidate = next(
                    (
                        value
                        for operation in receipt_ops
                        if operation.opcode == SemanticOpcode.BEGIN_PRIVATE
                        for value in operation.result_value_ids
                        if existing[value].type_id == NATIVE_TRANSACTION_TYPE
                    ),
                    candidate,
                )
            if candidate and existing[candidate].type_id == arg_type:
                value_id = candidate
            else:
                value_id = 90300 + index * 8 + arg_index
                if arg_type == REF_TYPE:
                    object_id = (STDLIB_RECEIPT_FUNCTION_ID << 16) ^ op_id ^ arg_index
                    receipt_value(
                        value_id,
                        f"{opcode.name.lower()}_abi_argument_{arg_index}",
                        arg_type,
                        SemanticValueKind.OBJECT,
                        block,
                        source=object_id,
                        literal=ObjectHandle(object_id, 1, 0).to_bytes(),
                    )
                else:
                    number = (
                        64
                        if opcode == SemanticOpcode.ALLOCATE and arg_index == 1
                        else 8
                        if opcode == SemanticOpcode.ALLOCATE and arg_index == 2
                        else 1
                    )
                    receipt_value(
                        value_id,
                        f"{opcode.name.lower()}_abi_argument_{arg_index}",
                        arg_type,
                        SemanticValueKind.LITERAL,
                        block,
                        literal=number.to_bytes(8, "little"),
                    )
            supplied.append(value_id)
        inputs = tuple(supplied)
        target = inputs[0] if inputs else current
        receipt_ops.append(
            SemanticOperation(
                op_id,
                opcode.name.lower(),
                block,
                opcode,
                sum(o.parent == block for o in receipt_ops),
                receipt_types,
                ((receipt_result_type,) if receipt_has_payload else ())
                + ((U32_TYPE,) if receipt_status_id else ()),
                inputs,
                ((result_id,) if receipt_has_payload else ())
                + ((receipt_status_id,) if receipt_status_id else ()),
                receipt_status_id,
                Effect.ALLOCATE | Effect.READ_MEMORY | Effect.WRITE_MEMORY,
                STDLIB_REFUSAL_SET_ID,
                callable_by_name[
                    {
                        SemanticOpcode.BEGIN_PRIVATE: "runtime_begin_private",
                        SemanticOpcode.ALLOCATE: "runtime_allocate_prepare",
                        SemanticOpcode.WRITE: "runtime_write",
                        SemanticOpcode.APPEND_BYTES: "runtime_append",
                        SemanticOpcode.FORMAT_INTEGER: "runtime_format_u32",
                        SemanticOpcode.UTF8_VALIDATE: "runtime_read",
                        SemanticOpcode.SEARCH: "runtime_search",
                        SemanticOpcode.VALIDATE: "runtime_resolve",
                        SemanticOpcode.PUBLISH: "runtime_publish",
                        SemanticOpcode.ROLLBACK: "runtime_rollback",
                        SemanticOpcode.CLEANUP: "runtime_cleanup",
                    }[opcode]
                ][0]
                if opcode
                in {
                    SemanticOpcode.BEGIN_PRIVATE,
                    SemanticOpcode.ALLOCATE,
                    SemanticOpcode.WRITE,
                    SemanticOpcode.APPEND_BYTES,
                    SemanticOpcode.FORMAT_INTEGER,
                    SemanticOpcode.UTF8_VALIDATE,
                    SemanticOpcode.SEARCH,
                    SemanticOpcode.VALIDATE,
                    SemanticOpcode.PUBLISH,
                    SemanticOpcode.ROLLBACK,
                    SemanticOpcode.CLEANUP,
                }
                else 0,
                target,
                0,
            )
        )
        current = result_id if receipt_has_payload else inputs[0]
    # Every receipt call gets its own status branch. Success continues to the
    # next call; failure enters rollback/cleanup while preserving primary status.
    call_ops = [op for op in receipt_ops if op.status_value_id]
    primary_ops = [
        op
        for op in call_ops
        if op.opcode not in {SemanticOpcode.ROLLBACK, SemanticOpcode.CLEANUP}
    ]
    rollback_op = next(op for op in call_ops if op.opcode == SemanticOpcode.ROLLBACK)
    cleanup_op = next(op for op in call_ops if op.opcode == SemanticOpcode.CLEANUP)
    return_op = next(
        op for op in receipt_ops if op.opcode == SemanticOpcode.RETURN_VALUE
    )
    chain_ids = [receipt_entry, *range(90600, 90600 + len(primary_ops) - 1)]
    rollback_block, cleanup_block, refuse_block = 90620, 90621, receipt_refusal
    primary_status = refusal_phi
    success_status_values = []
    for index in range(1, len(primary_ops)):
        success_status_values.append(
            receipt_value(
                90630 + index,
                "successful_call_status",
                U32_TYPE,
                SemanticValueKind.BLOCK_PARAMETER,
                chain_ids[index],
            )
        )
    rollback_primary = receipt_value(
        90650,
        "rollback_primary_status",
        U32_TYPE,
        SemanticValueKind.BLOCK_PARAMETER,
        rollback_block,
    )
    cleanup_primary = receipt_value(
        90651,
        "cleanup_primary_status",
        U32_TYPE,
        SemanticValueKind.BLOCK_PARAMETER,
        cleanup_block,
    )
    blocks = []
    receipt_ops_by_id = {op.id: op for op in receipt_ops}
    for index, op in enumerate(primary_ops):
        op = replace(op, parent=chain_ids[index], index=0)
        receipt_ops_by_id[op.id] = op
        current_id = chain_ids[index]
        success_target = (
            chain_ids[index + 1] if index + 1 < len(primary_ops) else receipt_success
        )
        parameters = () if index == 0 else (success_status_values[index - 1],)
        failure_target = (
            refuse_block
            if op.opcode == SemanticOpcode.BEGIN_PRIVATE
            else rollback_block
        )
        blocks.append(
            SemanticBlock(
                current_id,
                f"receipt_status_{op.id}",
                receipt_graph,
                index,
                TerminatorKind.BRANCH,
                (op.id,),
                (),
                (success_target, failure_target),
                (U32_TYPE,) if parameters else (),
                parameters,
                op.status_value_id,
                0,
                ((op.status_value_id,), (op.status_value_id,)),
            )
        )
    rollback_op = replace(rollback_op, parent=rollback_block, index=0)
    cleanup_op = replace(cleanup_op, parent=cleanup_block, index=0)
    return_op = replace(return_op, parent=receipt_success, index=0)
    receipt_ops_by_id.update(
        {
            rollback_op.id: rollback_op,
            cleanup_op.id: cleanup_op,
            return_op.id: return_op,
        }
    )
    receipt_ops = list(receipt_ops_by_id.values())
    blocks.extend(
        (
            SemanticBlock(
                receipt_success,
                "success",
                receipt_graph,
                len(blocks),
                TerminatorKind.RETURN,
                (return_op.id,),
                (),
                (),
                (U32_TYPE,),
                (success_phi,),
                0,
                return_op.result_value_ids[0],
                (),
            ),
            SemanticBlock(
                rollback_block,
                "rollback",
                receipt_graph,
                len(blocks) + 1,
                TerminatorKind.BRANCH,
                (rollback_op.id,),
                (),
                (cleanup_block, cleanup_block),
                (U32_TYPE,),
                (rollback_primary,),
                rollback_op.status_value_id,
                0,
                ((rollback_primary,), (rollback_primary,)),
            ),
            SemanticBlock(
                cleanup_block,
                "cleanup",
                receipt_graph,
                len(blocks) + 2,
                TerminatorKind.BRANCH,
                (cleanup_op.id,),
                (),
                (refuse_block, refuse_block),
                (U32_TYPE,),
                (cleanup_primary,),
                cleanup_op.status_value_id,
                0,
                ((cleanup_primary,), (cleanup_primary,)),
            ),
            SemanticBlock(
                refuse_block,
                "refusal",
                receipt_graph,
                len(blocks) + 3,
                TerminatorKind.REFUSE,
                (),
                (),
                (),
                (U32_TYPE,),
                (primary_status,),
                0,
                primary_status,
                (),
            ),
        )
    )
    predecessor_map = {block.id: [] for block in blocks}
    for block in blocks:
        for target in block.successor_ids:
            if block.id not in predecessor_map[target]:
                predecessor_map[target].append(block.id)
    receipt_blocks = tuple(
        replace(block, predecessor_ids=tuple(sorted(predecessor_map[block.id])))
        for block in blocks
    )
    success_out = return_op.result_value_ids[0]
    used_receipt_operations = {op.id for op in receipt_ops}
    nodes.extend(
        value.to_node(STDLIB_SEMANTIC_VALUE_TYPE)
        for value in receipt_values
        if not value.owner_operation_id
        or value.owner_operation_id in used_receipt_operations
    )
    for block in receipt_blocks:
        nodes.append(block.to_node(STDLIB_SEMANTIC_BLOCK_TYPE))
    nodes.extend(op.to_node(STDLIB_SEMANTIC_OPERATION_TYPE) for op in receipt_ops)
    receipt_abi_ids = (9_100_000, 9_100_001, 30_090_001, 36_090_001)
    for abi_id, target_id, abi_kind, base in (
        (receipt_abi_ids[0], X86_64_LINUX_TARGET_ID, ABIKind.SYSV_X86_64, 9_100_010),
        (receipt_abi_ids[1], AARCH64_UEFI_TARGET_ID, ABIKind.AAPCS64, 9_100_020),
        (receipt_abi_ids[2], AARCH64_LINUX_TARGET_ID, ABIKind.AAPCS64, 31_090_010),
        (receipt_abi_ids[3], X86_64_UEFI_TARGET_ID, ABIKind.SYSV_X86_64, 36_090_010),
    ):
        location_ids = (base, base + 1, base + 2)
        nodes.append(
            ABISignature(
                abi_id,
                "sysv_composed_entry"
                if abi_kind == ABIKind.SYSV_X86_64
                else "aapcs64_composed_entry",
                STDLIB_RECEIPT_FUNCTION_ID,
                target_id,
                abi_kind,
                (),
                ABIClass.POINTER,
                location_ids,
            ).to_node()
        )
        nodes.extend(
            (
                ABIValueLocation(
                    location_ids[0],
                    "hidden_context",
                    abi_id,
                    0,
                    0,
                    ABIRole.HIDDEN_CONTEXT,
                    ABIPassingMode.INDIRECT_BY_REFERENCE,
                    ABIClass.POINTER,
                    8,
                    8,
                    1,
                    ABIRegisterBank.INTEGER,
                    (0,),
                    pointee_type=REF_TYPE,
                    hidden=True,
                ).to_node(),
                ABIValueLocation(
                    location_ids[1],
                    "output",
                    abi_id,
                    receipt_result,
                    0,
                    ABIRole.TRANSIENT_OUTPUT,
                    ABIPassingMode.INDIRECT_BY_REFERENCE,
                    ABIClass.POINTER,
                    8,
                    8,
                    1,
                    ABIRegisterBank.INTEGER,
                    (1,),
                    pointee_type=U64_TYPE,
                    hidden=True,
                    mutable=True,
                    output_only=True,
                ).to_node(),
                ABIValueLocation(
                    location_ids[2],
                    "status",
                    abi_id,
                    0,
                    0,
                    ABIRole.STATUS_RETURN,
                    ABIPassingMode.DIRECT_SCALAR,
                    ABIClass.INTEGER,
                    4,
                    4,
                    1,
                    ABIRegisterBank.RETURN,
                    (0,),
                    pointee_type=U32_TYPE,
                    hidden=True,
                ).to_node(),
            )
        )
    nodes.append(
        SemanticGraph(
            receipt_graph,
            "build_receipt_graph",
            STDLIB_RECEIPT_FUNCTION_ID,
            STDLIB_RECEIPT_FUNCTION_ID,
            receipt_entry,
            tuple(block.id for block in receipt_blocks),
            STDLIB_REFUSAL_SET_ID,
            Effect.ALLOCATE | Effect.READ_MEMORY | Effect.WRITE_MEMORY,
            U64_TYPE,
            (),
            (success_out,),
        ).to_node(STDLIB_SEMANTIC_GRAPH_TYPE)
    )
    return nodes


def stdlib_inventory(nodes: list[NodeDef]) -> dict[str, int]:
    module_ids = {
        STDLIB_FOUNDATION_MODULE_ID,
        STDLIB_BASIC_MODULE_ID,
        STDLIB_LIBRARY_MODULE_ID,
    }
    nodes_by_id = {node.id: node for node in nodes}
    stdlib_functions = [
        node
        for node in nodes
        if node.type_id == FUNCTION_TYPE and node.parent in module_ids
    ]
    native = {node.id for node in stdlib_functions if node.name in NATIVE_SYMBOLS}
    return {
        "types": 1 + len(TYPE_SPECS),
        "concrete_specializations": sum(
            node.kind == NodeKind.TYPE
            and node.name
            in {
                "Option_u32",
                "FixedArray_u8_16",
                "FixedArray_u8_32",
                "Tuple_u32_u32",
                "Vector_u8",
                "Vector_u32",
                "Vector_String",
            }
            for node in nodes
        ),
        "functions": len(stdlib_functions),
        "templates": sum(
            node.type_id == 51 and node.parent == STDLIB_BASIC_MODULE_ID
            for node in nodes
        ),
        "traits": sum(
            node.type_id == 52 and node.parent == STDLIB_BASIC_MODULE_ID
            for node in nodes
        ),
        "conformances": sum(
            node.type_id == 55 and node.parent == STDLIB_BASIC_MODULE_ID
            for node in nodes
        ),
        "semantic_records": sum(node.type_id == STDLIB_RECORD_TYPE for node in nodes),
        "native_functions": len(native),
        "composed_functions": sum(
            decode_function(node).implementation == FunctionImplementation.COMPOSED
            for node in stdlib_functions
        ),
        "abstract_functions": sum(
            decode_function(node).implementation == FunctionImplementation.ABSTRACT
            for node in stdlib_functions
        ),
        "declared_functions": sum(
            decode_function(node).implementation == FunctionImplementation.DECLARED
            for node in stdlib_functions
        ),
        "api_functions": sum(node.type_id == STDLIB_RECORD_TYPE for node in nodes),
        "api_declared": sum(
            decode_function(
                nodes_by_id[StdlibRecord.from_node(node).related_ids[0]]
            ).implementation
            == FunctionImplementation.DECLARED
            for node in nodes
            if node.type_id == STDLIB_RECORD_TYPE
        ),
        "native_code": sum(
            node.kind == NodeKind.CODE and node.parent in native for node in nodes
        ),
    }
