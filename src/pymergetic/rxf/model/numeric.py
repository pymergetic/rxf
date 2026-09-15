"""Complete RXF v5 numeric Function/Trait/Conformance/native Code corpus."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pymergetic.rxf.generated_native import AARCH64, COMPILER, RECIPE, X86_64
from pymergetic.rxf.model.contracts import (
    ABIClass,
    ABIKind,
    ABISignature,
    CompilerProvenance,
    NumericContract,
    NumericOperation,
    NumericPolicy,
    RefusalSet,
    RefusalVariant,
    ResultObject,
    canonical_semantic_digest,
)
from pymergetic.rxf.model.execution import (
    CodeFormat,
    CodeObject,
    Effect,
    FunctionImplementation,
    FunctionIntrinsic,
    FunctionLayer,
    FunctionObject,
    ParameterObject,
    SignatureObject,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.target import AARCH64_UEFI_TARGET_ID, X86_64_LINUX_TARGET_ID
from pymergetic.rxf.model.traits import (
    AssociatedType,
    Conformance,
    ImplementationBinding,
    ImplementationBindingKind,
    Trait,
    TraitRequirement,
)
from pymergetic.rxf.ty.builtins import (
    BASIC_MODULE_ID,
    BOOL_TYPE,
    F32_TYPE,
    F64_TYPE,
    I8_TYPE,
    I16_TYPE,
    I32_TYPE,
    I64_TYPE,
    NUMERIC_MODULE_ID,
    TRAITS_MODULE_ID,
    U8_TYPE,
    U16_TYPE,
    U32_TYPE,
    U64_TYPE,
    VOID_TYPE,
)

NUMERIC_ID_BASE = 10000
TYPES = (
    (U8_TYPE, "uint8_t", 8, False),
    (U16_TYPE, "uint16_t", 16, False),
    (U32_TYPE, "uint32_t", 32, False),
    (U64_TYPE, "uint64_t", 64, False),
    (I8_TYPE, "int8_t", 8, True),
    (I16_TYPE, "int16_t", 16, True),
    (I32_TYPE, "int32_t", 32, True),
    (I64_TYPE, "int64_t", 64, True),
    (F32_TYPE, "float", 32, True),
    (F64_TYPE, "double", 64, True),
)
INTEGER_TYPES = {i: n for i, n, _, _ in TYPES[:8]}
FLOAT_TYPES = {i: n for i, n, _, _ in TYPES[8:]}
TYPE_BY_NAME = {n: (i, w, s) for i, n, w, s in TYPES}
INTEGER = {n for _, n, _, _ in TYPES[:8]}
FLOAT = {"float", "double"}
SIGNED = {n for _, n, _, s in TYPES[:8] if s}
UNARY = {"bit_not", "checked_negate", "checked_absolute"}
BOOL_RESULT = {"equal", "less"}
OPS = (
    "equal",
    "less",
    "minimum",
    "maximum",
    "checked_add",
    "wrapping_add",
    "saturating_add",
    "checked_subtract",
    "wrapping_subtract",
    "saturating_subtract",
    "checked_multiply",
    "wrapping_multiply",
    "saturating_multiply",
    "divide",
    "remainder",
    "bit_not",
    "bit_and",
    "bit_or",
    "bit_xor",
    "shift_left",
    "shift_right",
    "checked_negate",
    "checked_absolute",
)
OP_ENUM = {n: getattr(NumericOperation, n.upper()) for n in OPS}
TRAITS = {
    "Equal": ("equal",),
    "Ordered": ("less", "minimum", "maximum"),
    "CheckedAdd": ("checked_add",),
    "WrappingAdd": ("wrapping_add",),
    "SaturatingAdd": ("saturating_add",),
    "CheckedSubtract": ("checked_subtract",),
    "WrappingSubtract": ("wrapping_subtract",),
    "SaturatingSubtract": ("saturating_subtract",),
    "CheckedMultiply": ("checked_multiply",),
    "WrappingMultiply": ("wrapping_multiply",),
    "SaturatingMultiply": ("saturating_multiply",),
    "Divide": ("divide",),
    "Remainder": ("remainder",),
    "Bitwise": ("bit_not", "bit_and", "bit_or", "bit_xor"),
    "Shift": ("shift_left", "shift_right"),
    "Negate": ("checked_negate", "checked_absolute"),
}
TRAIT_OPERATIONS = TRAITS


@dataclass
class IDs:
    value: int = NUMERIC_ID_BASE

    def next(self):
        r = self.value
        self.value += 1
        return r


def _policy(op: str, name: str) -> NumericPolicy:
    p = NumericPolicy.NONE
    if op.startswith("checked_") or op in {
        "divide",
        "remainder",
        "shift_left",
        "shift_right",
    }:
        p |= NumericPolicy.CHECKED
    if op.startswith("wrapping_"):
        p |= NumericPolicy.WRAPPING
    if op.startswith("saturating_"):
        p |= NumericPolicy.SATURATING
    if op in {"divide", "remainder"}:
        p |= NumericPolicy.TRUNCATE_ZERO
    if op == "remainder":
        p |= NumericPolicy.REMAINDER_DIVIDEND_SIGN
    if op.startswith("shift_"):
        p |= NumericPolicy.REFUSE_SHIFT_WIDTH
    if op == "shift_right" and name in SIGNED:
        p |= NumericPolicy.ARITHMETIC_RIGHT
    if name in FLOAT:
        p |= (
            NumericPolicy.FINITE_ONLY
            | NumericPolicy.REFUSE_NAN
            | NumericPolicy.REFUSE_INFINITY
            | NumericPolicy.PRESERVE_SIGNED_ZERO
        )
    return p


def numeric_nodes() -> list[NodeDef]:
    ids = IDs()
    nodes = []
    variants = []
    for name, code in [
        ("overflow", 1),
        ("division_by_zero", 2),
        ("shift_count", 3),
        ("non_finite", 4),
        ("inexact", 5),
        ("out_of_range", 6),
    ]:
        i = ids.next()
        variants.append(i)
        nodes.append(RefusalVariant(i, name, NUMERIC_MODULE_ID, code).to_node())
    refusals = ids.next()
    nodes.append(
        RefusalSet(
            refusals, "numeric_refusals", NUMERIC_MODULE_ID, tuple(variants)
        ).to_node()
    )
    provenance = ids.next()
    nodes.append(
        CompilerProvenance(
            provenance,
            COMPILER.replace(".", "_"),
            NUMERIC_MODULE_ID,
            18,
            hashlib.sha256(RECIPE.encode()).digest(),
        ).to_node()
    )
    traits = {}
    for tname, ops in TRAITS.items():
        tid, selfid = ids.next(), ids.next()
        req = []
        reqmap = {}
        for index, op in enumerate(ops):
            rid, fid, sid = ids.next(), ids.next(), ids.next()
            arity = 1 if op in UNARY else 2
            ps = tuple(ids.next() for _ in range(arity))
            for j, pid in enumerate(ps):
                nodes.append(
                    ParameterObject(
                        pid,
                        f"operand_{j}",
                        sid,
                        U32_TYPE if op.startswith("shift_") and j == 1 else selfid,
                        j,
                    ).to_node()
                )
            result = ids.next()
            nodes.append(
                ResultObject(
                    result, "value", sid, BOOL_TYPE if op in BOOL_RESULT else selfid, 0
                ).to_node()
            )
            nodes.append(
                SignatureObject(
                    sid,
                    "signature",
                    fid,
                    BOOL_TYPE if op in BOOL_RESULT else selfid,
                    ps,
                    (result,),
                    refusals,
                ).to_node()
            )
            nodes.append(
                FunctionObject(
                    fid,
                    op,
                    tid,
                    sid,
                    FunctionImplementation.ABSTRACT,
                    FunctionLayer.BASIC,
                ).to_node()
            )
            nodes.append(
                TraitRequirement(rid, f"{op}_requirement", tid, fid, index).to_node()
            )
            req.append(rid)
            reqmap[op] = rid
        nodes.append(AssociatedType(selfid, "Self", tid, 0).to_node())
        supers = (traits["Equal"][0],) if tname == "Ordered" else ()
        nodes.append(
            Trait(tid, tname, TRAITS_MODULE_ID, tuple(req), (selfid,), supers).to_node()
        )
        traits[tname] = (tid, selfid, reqmap)
    impl = {}

    def add_function(op, source, dest, symbol):
        st, swidth, ssigned = TYPE_BY_NAME[source]
        dt, _, _ = TYPE_BY_NAME[dest]
        fid, sid = ids.next(), ids.next()
        arity = 1 if op in UNARY or op == "convert" else 2
        ps = tuple(ids.next() for _ in range(arity))
        for j, pid in enumerate(ps):
            nodes.append(
                ParameterObject(
                    pid,
                    f"operand_{j}",
                    sid,
                    U32_TYPE if op.startswith("shift_") and j == 1 else st,
                    j,
                ).to_node()
            )
        result = ids.next()
        nodes.append(
            ResultObject(
                result, "value", sid, BOOL_TYPE if op in BOOL_RESULT else dt, 0
            ).to_node()
        )
        nodes.append(
            SignatureObject(
                sid,
                "signature",
                fid,
                BOOL_TYPE if op in BOOL_RESULT else dt,
                ps,
                (result,),
                refusals,
            ).to_node()
        )
        nc = ids.next()
        policy = (
            (
                NumericPolicy.CHECKED
                | NumericPolicy.EXACT
                | NumericPolicy.RANGE_CHECKED
                | NumericPolicy.ROUND_NEAREST_EVEN
            )
            if op == "convert"
            else _policy(op, source)
        )
        nodes.append(
            NumericContract(
                nc,
                f"{symbol}_contract",
                NUMERIC_MODULE_ID,
                NumericOperation.CONVERT if op == "convert" else OP_ENUM[op],
                st,
                dt,
                policy,
                swidth,
                ssigned,
            ).to_node()
        )
        abi_x, abi_a = ids.next(), ids.next()
        classes = tuple(
            ABIClass.FLOAT if source in FLOAT else ABIClass.INTEGER
            for _ in range(arity)
        )
        nodes.append(
            ABISignature(
                abi_x,
                "sysv_status_out",
                fid,
                X86_64_LINUX_TARGET_ID,
                ABIKind.SYSV_X86_64,
                classes,
            ).to_node()
        )
        nodes.append(
            ABISignature(
                abi_a,
                "aapcs64_status_out",
                fid,
                AARCH64_UEFI_TARGET_ID,
                ABIKind.AAPCS64,
                classes,
            ).to_node()
        )
        sig = next(node.data for node in nodes if node.id == sid)
        result_payload = next(node.data for node in nodes if node.id == result)
        refusal = next(node.data for node in nodes if node.id == refusals)
        numeric_payload = next(node.data for node in nodes if node.id == nc)
        digest = canonical_semantic_digest(
            sig, [result_payload], refusal, numeric_payload, 0, 1
        )
        cx, ca = ids.next(), ids.next()
        nodes.append(
            FunctionObject(
                fid,
                symbol,
                NUMERIC_MODULE_ID,
                sid,
                FunctionImplementation.CODE_BACKED,
                FunctionLayer.BASIC,
                implementation_ids=(cx, ca),
                numeric_contract_id=nc,
                semantic_digest_override=digest,
            ).to_node()
        )
        nodes.append(
            CodeObject(
                cx,
                "x86_64_linux",
                fid,
                fid,
                X86_64_LINUX_TARGET_ID,
                CodeFormat.NATIVE,
                X86_64[symbol],
                sid,
                Effect.NONE,
                1,
                digest,
                abi_signature_id=abi_x,
                provenance_id=provenance,
            ).to_node()
        )
        nodes.append(
            CodeObject(
                ca,
                "aarch64_uefi",
                fid,
                fid,
                AARCH64_UEFI_TARGET_ID,
                CodeFormat.NATIVE,
                AARCH64[symbol],
                sid,
                Effect.NONE,
                1,
                digest,
                abi_signature_id=abi_a,
                provenance_id=provenance,
            ).to_node()
        )
        return fid

    for _, name, _, _ in TYPES:
        allowed = [
            o
            for o in OPS
            if (
                name in INTEGER
                or o
                in {
                    "equal",
                    "less",
                    "minimum",
                    "maximum",
                    "checked_add",
                    "checked_subtract",
                    "checked_multiply",
                    "divide",
                    "checked_negate",
                    "checked_absolute",
                }
            )
            and (
                name in SIGNED
                or name in FLOAT
                or o not in {"checked_negate", "checked_absolute"}
            )
        ]
        for op in allowed:
            impl[name, op] = add_function(op, name, name, f"{op}_{name}")
    for _, source, _, _ in TYPES:
        for _, dest, _, _ in TYPES:
            if source != dest:
                add_function("convert", source, dest, f"convert_{source}_to_{dest}")
    for _, name, _, _ in TYPES:
        for tname, ops in TRAITS.items():
            if not all((name, o) in impl for o in ops):
                continue
            tid, selfid, reqs = traits[tname]
            bindings = []
            inherited = dict(reqs)
            if tname == "Ordered":
                inherited.update(traits["Equal"][2])
            cid = ids.next()
            for op, rid in inherited.items():
                bid = ids.next()
                nodes.append(
                    ImplementationBinding(bid, op, cid, rid, impl[name, op]).to_node()
                )
                bindings.append(bid)
            for label, aid in [
                ("Self", selfid),
                *([("Equal_Self", traits["Equal"][1])] if tname == "Ordered" else []),
            ]:
                bid = ids.next()
                nodes.append(
                    ImplementationBinding(
                        bid,
                        label,
                        cid,
                        aid,
                        TYPE_BY_NAME[name][0],
                        ImplementationBindingKind.ASSOCIATED_TYPE,
                    ).to_node()
                )
                bindings.append(bid)
            nodes.append(
                Conformance(
                    cid,
                    f"{tname}_{name}",
                    TRAITS_MODULE_ID,
                    tid,
                    TYPE_BY_NAME[name][0],
                    tuple(bindings),
                ).to_node()
            )
    return nodes


def control_nodes() -> list[NodeDef]:
    ids = IDs(30000)
    nodes = []
    specs = (
        (
            "sequence",
            FunctionIntrinsic.SEQUENCE,
            (U64_TYPE, U64_TYPE),
            (False, False),
            VOID_TYPE,
        ),
        (
            "if",
            FunctionIntrinsic.IF,
            (BOOL_TYPE, U64_TYPE, U64_TYPE),
            (False, True, True),
            VOID_TYPE,
        ),
        (
            "while",
            FunctionIntrinsic.WHILE,
            (U64_TYPE, U64_TYPE),
            (True, True),
            VOID_TYPE,
        ),
        (
            "switch",
            FunctionIntrinsic.SWITCH,
            (U64_TYPE, U64_TYPE, U64_TYPE),
            (False, True, True),
            VOID_TYPE,
        ),
        ("return", FunctionIntrinsic.RETURN, (U64_TYPE,), (False,), VOID_TYPE),
        ("break", FunctionIntrinsic.BREAK, (), (), VOID_TYPE),
        ("continue", FunctionIntrinsic.CONTINUE, (), (), VOID_TYPE),
        ("refuse", FunctionIntrinsic.REFUSE, (U32_TYPE,), (False,), VOID_TYPE),
    )
    for name, intrinsic, types, lazy, result_type in specs:
        fid, sid = ids.next(), ids.next()
        ps = tuple(ids.next() for _ in types)
        for i, (pid, t, l) in enumerate(zip(ps, types, lazy, strict=True)):
            nodes.append(ParameterObject(pid, f"argument_{i}", sid, t, i, l).to_node())
        result = ids.next()
        nodes.append(ResultObject(result, "value", sid, result_type, 0).to_node())
        nodes.append(
            SignatureObject(
                sid, "signature", fid, result_type, ps, (result,), 0
            ).to_node()
        )
        nodes.append(
            FunctionObject(
                fid,
                name,
                BASIC_MODULE_ID,
                sid,
                FunctionImplementation.INTRINSIC,
                FunctionLayer.BASIC,
                intrinsic=intrinsic,
            ).to_node()
        )
    return nodes
