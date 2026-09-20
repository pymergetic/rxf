"""Owned deterministic composed floating-point RXF fixture."""

from __future__ import annotations

import struct

from pymergetic.rxf.expand import Template
from pymergetic.rxf.model.contracts import ResultObject
from pymergetic.rxf.model.execution import (
    ArgumentObject,
    CallObject,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    SignatureObject,
    ValueKind,
    ValueObject,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.numeric import numeric_nodes
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.builtins import F32_TYPE, RXF_MODULE_ID

FP_FUNCTION = 60000
FP_SIGNATURE = 60001


def fp_template(type_id=F32_TYPE, values=(1.25, 2.5, 2.0)):
    numeric = numeric_nodes()
    functions = {n.name: n for n in numeric if n.type_id == 14}
    signatures = {n.id: n for n in numeric if n.type_id == 15}
    suffix = "float" if type_id == F32_TYPE else "double"
    nodes = [*numeric, NodeDef(0, "root", NodeKind.ROOT)]
    next_id = 60100

    def alloc():
        nonlocal next_id
        result = next_id
        next_id += 1
        return result

    objects = []
    for index, value in enumerate(values):
        oid, vid = alloc(), alloc()
        width = 4 if type_id == F32_TYPE else 8
        raw = struct.pack("<f" if width == 4 else "<d", value)
        nodes += [
            NodeDef(
                oid,
                f"value_{index}",
                NodeKind.DATA,
                RXF_MODULE_ID,
                type_id=type_id,
                data=raw,
            ),
            ValueObject(
                vid,
                f"value_{index}",
                FP_FUNCTION,
                type_id,
                ValueKind.OBJECT,
                oid.to_bytes(8, "little"),
            ).to_node(),
        ]
        objects.append(vid)
    previous = objects[0]
    calls = []
    for opname, right in (
        (f"checked_add_{suffix}", objects[1]),
        (f"checked_multiply_{suffix}", objects[2]),
    ):
        fn = functions[opname]
        sigid = struct.unpack_from("<Q", fn.data, 16)[0]
        params = [r.target for r in signatures[sigid].refs if r.to_off == 204]
        args = []
        callid = alloc()
        for position, value in enumerate((previous, right)):
            aid = alloc()
            nodes.append(
                ArgumentObject(
                    aid, f"arg_{position}", callid, params[position], value
                ).to_node()
            )
            args.append(aid)
        nodes.append(
            CallObject(
                callid, opname, FP_FUNCTION, fn.id, tuple(args), type_id
            ).to_node()
        )
        vid = alloc()
        nodes.append(
            ValueObject(
                vid,
                opname,
                FP_FUNCTION,
                type_id,
                ValueKind.RESULT,
                callid.to_bytes(8, "little"),
            ).to_node()
        )
        previous = vid
        calls.append(callid)
    rid = alloc()
    nodes += [
        ResultObject(rid, "result", FP_SIGNATURE, type_id, 0).to_node(),
        SignatureObject(
            FP_SIGNATURE, "signature", FP_FUNCTION, type_id, (), (rid,)
        ).to_node(),
        FunctionObject(
            FP_FUNCTION,
            "composed_fp",
            RXF_MODULE_ID,
            FP_SIGNATURE,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            body_id=calls[-1],
        ).to_node(),
    ]
    return Template(entry=FP_FUNCTION, include_lowered=False, extra_nodes=nodes)
