"""Deterministic lowering of ordinary Calls, including control Functions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from pymergetic.rxf.execution.decode import decode_function
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.execution import CallRole, FunctionIntrinsic
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.ops.ir import CFGBlockObject, CFGOpObject
from pymergetic.rxf.ty.builtins import CALL_TYPE, FUNCTION_TYPE

DERIVED_ID_PREFIX = 0xE000_0000_0000_0000
DERIVED_ID_MASK = 0x0FFF_FFFF_FFFF_FFFF


def derived_id(source_id: int, role: str) -> int:
    digest = hashlib.sha256(f"{source_id}:{role}".encode()).digest()
    return DERIVED_ID_PREFIX | (int.from_bytes(digest[:8], "big") & DERIVED_ID_MASK)


@dataclass
class LoweringResult:
    nodes: list[NodeDef]


def _role_targets(node: NodeDef, role: CallRole) -> list[int]:
    return [
        reference.target for reference in node.refs if reference.to_off == int(role)
    ]


def lower(container: Container) -> LoweringResult:
    source_nodes = [node for node in container.nodes if not node.attrs.get("derived")]
    by_id = {node.id: node for node in source_nodes}
    lowered: list[NodeDef] = []
    seen = set(by_id)

    def add(obj: CFGBlockObject | CFGOpObject) -> int:
        if obj.id in seen:
            raise ValueError(f"derived object ID collision at {obj.id}")
        seen.add(obj.id)
        lowered.append(obj.to_node())
        return obj.id

    for call in sorted(
        (node for node in source_nodes if node.type_id == CALL_TYPE),
        key=lambda node: node.id,
    ):
        callees = _role_targets(call, CallRole.CALLEE)
        arguments = _role_targets(call, CallRole.ARGUMENT)
        if len(callees) != 1:
            continue
        callee = by_id.get(callees[0])
        if callee is None or callee.type_id != FUNCTION_TYPE:
            continue
        function = decode_function(callee)
        if function.intrinsic == FunctionIntrinsic.IF:
            then_id = derived_id(call.id, "then")
            else_id = derived_id(call.id, "else")
            merge_id = derived_id(call.id, "merge")
            test_id = add(
                CFGBlockObject(
                    derived_id(call.id, "test"),
                    f"{call.name}_test",
                    call.id,
                    call.parent,
                    "if.test",
                    targets=[then_id, else_id],
                )
            )
            add(
                CFGBlockObject(
                    then_id,
                    f"{call.name}_then",
                    call.id,
                    call.parent,
                    "if.then",
                    targets=[merge_id],
                )
            )
            add(
                CFGBlockObject(
                    else_id,
                    f"{call.name}_else",
                    call.id,
                    call.parent,
                    "if.else",
                    targets=[merge_id],
                )
            )
            add(
                CFGBlockObject(
                    merge_id, f"{call.name}_merge", call.id, call.parent, "if.merge"
                )
            )
            add(
                CFGOpObject(
                    derived_id(call.id, "branch"),
                    f"{call.name}_branch",
                    call.id,
                    test_id,
                    "conditional.branch",
                    targets=[then_id, else_id],
                    arguments=arguments,
                )
            )
        elif function.intrinsic == FunctionIntrinsic.WHILE:
            test_id = derived_id(call.id, "test")
            body_id = derived_id(call.id, "body")
            exit_id = derived_id(call.id, "exit")
            add(
                CFGBlockObject(
                    test_id,
                    f"{call.name}_test",
                    call.id,
                    call.parent,
                    "while.test",
                    targets=[body_id, exit_id],
                )
            )
            add(
                CFGBlockObject(
                    body_id,
                    f"{call.name}_body",
                    call.id,
                    call.parent,
                    "while.body",
                    targets=[test_id],
                )
            )
            add(
                CFGBlockObject(
                    exit_id, f"{call.name}_exit", call.id, call.parent, "while.exit"
                )
            )
            add(
                CFGOpObject(
                    derived_id(call.id, "branch"),
                    f"{call.name}_branch",
                    call.id,
                    test_id,
                    "conditional.branch",
                    targets=[body_id, exit_id],
                    arguments=arguments,
                )
            )
        elif function.intrinsic == FunctionIntrinsic.SWITCH:
            dispatch_id = add(
                CFGBlockObject(
                    derived_id(call.id, "dispatch"),
                    f"{call.name}_dispatch",
                    call.id,
                    call.parent,
                    "switch.dispatch",
                )
            )
            add(
                CFGOpObject(
                    derived_id(call.id, "switch"),
                    f"{call.name}_switch",
                    call.id,
                    dispatch_id,
                    "switch",
                    arguments=arguments,
                )
            )
        elif function.intrinsic in (
            FunctionIntrinsic.SEQUENCE,
            FunctionIntrinsic.RETURN,
            FunctionIntrinsic.BREAK,
            FunctionIntrinsic.CONTINUE,
            FunctionIntrinsic.REFUSE,
        ):
            role = function.intrinsic.name.lower()
            add(
                CFGOpObject(
                    derived_id(call.id, role),
                    f"{call.name}_{role}",
                    call.id,
                    call.parent,
                    role,
                    arguments=arguments,
                )
            )
        else:
            add(
                CFGOpObject(
                    derived_id(call.id, "call"),
                    f"{call.name}_call",
                    call.id,
                    call.parent,
                    "call",
                    targets=[callee.id],
                    arguments=arguments,
                )
            )
    lowered.sort(key=lambda node: node.id)
    return LoweringResult(lowered)


def with_lowered(container: Container) -> Container:
    semantic = [node for node in container.nodes if not node.attrs.get("derived")]
    return Container(
        header=container.header,
        heap=container.heap,
        nodes=semantic
        + lower(
            Container(header=container.header, heap=container.heap, nodes=semantic)
        ).nodes,
    )
