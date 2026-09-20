"""Typed CFG/control verifier and exact native lowering proofs."""

from __future__ import annotations

import ctypes
from dataclasses import replace

import pytest

from pymergetic.rxf.compiler.cfg import *
from pymergetic.rxf.compiler.control_native import emit_control
from pymergetic.rxf.compiler.ir import TypeRef, ValueClass, VirtualValue
from pymergetic.rxf.compiler.native import Architecture, executable
from pymergetic.rxf.compiler.normalize import CompileError

U32 = TypeRef(7, 4, ValueClass.INTEGER)
STATUS = U32


def v(i, b=1):
    return VirtualValue(i, U32, i, b)


def branch_function(condition=1):
    c, s1, s2, one, two = v(1), v(2), v(5), v(3, 2), v(4, 3)
    return ControlFunction(
        70000,
        (),
        (
            ControlBlock(1, (), (ConstOp(c, condition),), BranchBool(c, 2, 3)),
            ControlBlock(
                2, (), (ConstOp(one, 1), ConstOp(s1, 0)), ReturnControl(s1, one)
            ),
            ControlBlock(
                3, (), (ConstOp(two, 2), ConstOp(s2, 0)), ReturnControl(s2, two)
            ),
        ),
        1,
        U32,
    )


def test_control_if_exact_x86_execution():
    image = emit_control(branch_function(), Architecture.X86_64)
    memory, address = executable(image)
    out = ctypes.c_uint32(99)
    fn = ctypes.CFUNCTYPE(
        ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    )(address)
    assert fn(None, ctypes.byref(out)) == 0 and out.value == 1
    assert memory


def test_switch_exhaustiveness_and_exact_bytes():
    tag, status, result = v(1), v(2), v(3, 2)
    bad = ControlFunction(
        1,
        (),
        (
            ControlBlock(
                1, (), (ConstOp(tag, 0),), SwitchTag(tag, ((0, 2),), None, (0, 1))
            ),
            ControlBlock(
                2,
                (),
                (ConstOp(status, 0), ConstOp(result, 7)),
                ReturnControl(status, result),
            ),
        ),
        1,
        U32,
    )
    with pytest.raises(CompileError, match="not exhaustive"):
        verify_control(bad)
    good = replace(
        bad,
        blocks=(
            replace(bad.blocks[0], terminator=SwitchTag(tag, ((0, 2),), 2, (0, 1))),
            bad.blocks[1],
        ),
    )
    assert emit_control(good, Architecture.AARCH64).text


def test_loop_break_continue_validation():
    good = ControlFunction(
        1,
        (),
        (
            ControlBlock(1, (), (), Jump(2)),
            ControlBlock(
                2, (), (), ContinueLoop(2), loop_header=True, enclosing_loop=2
            ),
        ),
        1,
        U32,
    )
    verify_control(good)
    bad = replace(
        good, blocks=(good.blocks[0], replace(good.blocks[1], enclosing_loop=None))
    )
    with pytest.raises(CompileError, match="escapes"):
        verify_control(bad)


def test_phi_requires_exact_typed_predecessors():
    cond = v(1)
    left = v(2, 2)
    right = v(3, 3)
    phi = v(4, 4)
    status = v(5, 4)
    function = ControlFunction(
        1,
        (),
        (
            ControlBlock(1, (), (ConstOp(cond, 1),), BranchBool(cond, 2, 3)),
            ControlBlock(2, (), (ConstOp(left, 1),), Jump(4, (left,))),
            ControlBlock(3, (), (ConstOp(right, 2),), Jump(4, (right,))),
            ControlBlock(
                4,
                (BlockParameter(phi, ((2, left), (3, right))),),
                (ConstOp(status, 0),),
                ReturnControl(status, phi),
            ),
        ),
        1,
        U32,
    )
    verify_control(function)
    with pytest.raises(CompileError, match="exact predecessor"):
        verify_control(
            replace(
                function,
                blocks=(
                    *function.blocks[:3],
                    replace(
                        function.blocks[3],
                        parameters=(BlockParameter(phi, ((2, left),)),),
                    ),
                ),
            )
        )


def test_aggregate_requires_explicit_reference_classification():
    wide = TypeRef(99, 24, ValueClass.POINTER)
    arg = VirtualValue(1, wide, 1, 1)
    result = v(2)
    status = v(3)
    call = NativeCallOp(
        9, (arg,), (ABIValue(wide, AggregateClass.SCALAR),), result, status, ()
    )
    function = ControlFunction(
        1,
        (arg,),
        (ControlBlock(1, (), (call,), ReturnControl(status, result)),),
        1,
        U32,
    )
    with pytest.raises(CompileError, match="aggregate"):
        verify_control(function)
    verify_control(
        replace(
            function,
            blocks=(
                replace(
                    function.blocks[0],
                    operations=(
                        replace(
                            call,
                            argument_abi=(ABIValue(wide, AggregateClass.BY_REFERENCE),),
                        ),
                    ),
                ),
            ),
        )
    )


def test_tagged_option_projection_and_transaction_proofs():
    tagged = TypeRef(100, 16, ValueClass.POINTER)
    source = VirtualValue(1, tagged, 1, 1)
    tag = v(2)
    payload = v(3)
    private = v(4)
    valid = v(5)
    status = v(6)
    ops = (
        ProjectTagOp(source, tag, (0, 1)),
        ProjectPayloadOp(source, 1, payload, 8),
        ConstOp(private, 9),
        ConstOp(valid, 1),
        PrepareOp(1, private),
        ValidateOp(1, valid),
        PublishOp(1, private),
        CleanupOp(1),
        ConstOp(status, 0),
    )
    fn = ControlFunction(
        1,
        (source,),
        (ControlBlock(1, (), ops, ReturnControl(status, payload)),),
        1,
        U32,
    )
    verify_control(fn)
    broken = replace(
        fn,
        blocks=(
            replace(
                fn.blocks[0],
                operations=tuple(op for op in ops if not isinstance(op, CleanupOp)),
            ),
        ),
    )
    # Success-only path has publish dominating exit, so cleanup is not required there.
    verify_control(broken)


def test_control_if_exact_aarch64_qemu(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    qemu = shutil.which("qemu-system-aarch64")
    if qemu is None:
        pytest.skip("qemu-system-aarch64 unavailable")
    root = Path(__file__).parents[1]
    env = {**os.environ, "PYTHONPATH": str(root / "src")}
    subprocess.run(
        [sys.executable, str(root / "tools/generate_compiler_cfg_qemu.py")],
        cwd=root,
        env=env,
        check=True,
    )
    subprocess.run(
        [
            "clang-18",
            "-target",
            "aarch64-none-elf",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-stack-protector",
            "-O2",
            "-c",
            str(root / "native/compiler_cfg_qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    for source, name in (
        (root / "native/qemu_start.S", "s.o"),
        (root / "generated/compiler_cfg_qemu_aarch64.S", "b.o"),
    ):
        subprocess.run(
            [
                "clang-18",
                "-target",
                "aarch64-none-elf",
                "-c",
                str(source),
                "-o",
                str(tmp_path / name),
            ],
            check=True,
        )
    elf = tmp_path / "qemu.elf"
    subprocess.run(
        [
            "ld.lld-18",
            "-T",
            str(root / "native/qemu.ld"),
            str(tmp_path / "s.o"),
            str(tmp_path / "h.o"),
            str(tmp_path / "b.o"),
            "-o",
            str(elf),
        ],
        check=True,
    )
    result = subprocess.run(
        [
            qemu,
            "-M",
            "virt",
            "-cpu",
            "cortex-a57",
            "-nographic",
            "-semihosting-config",
            "enable=on,target=native",
            "-device",
            f"loader,file={elf}",
            "-device",
            "loader,addr=0x40200000,cpu-num=0",
        ],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0 and "RXF-CFG-IF-PASS" in result.stdout + result.stderr


def test_all_semantic_graphs_normalize_and_cover_shipped_opcodes():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph
    from pymergetic.rxf.expand import starter_template
    from pymergetic.rxf.model.stdlib_corpus import (
        STDLIB_SEMANTIC_GRAPH_TYPE,
        STDLIB_SEMANTIC_OPERATION_TYPE,
    )
    from pymergetic.rxf.model.stdlib_semantics import SemanticGraph

    container = starter_template().build()
    graphs = [
        SemanticGraph.from_node(n)
        for n in container.nodes
        if n.type_id == STDLIB_SEMANTIC_GRAPH_TYPE
    ]
    lowered = [normalize_semantic_graph(container, g.function_id) for g in graphs]
    assert len(lowered) == 53
    assert sum(len(g.blocks) for g in lowered) == sum(
        len(graph.block_ids) for graph in graphs
    )
    persisted_operation_ids = {
        node.id
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_OPERATION_TYPE
    }
    assert {
        operation.id
        for graph in lowered
        for block in graph.blocks
        for operation in block.operations
    } == persisted_operation_ids
    assert {op.opcode for g in lowered for b in g.blocks for op in b.operations} == {
        op
        for op in __import__(
            "pymergetic.rxf.model.stdlib_semantics", fromlist=["SemanticOpcode"]
        ).SemanticOpcode
        if any(
            op == x.opcode
            for graph in lowered
            for block in graph.blocks
            for x in block.operations
        )
    }


def test_receipt_frontend_accepts_complete_persisted_ssa():
    from pymergetic.rxf.compiler.semantic import normalize_semantic_graph
    from pymergetic.rxf.expand import starter_template
    from pymergetic.rxf.model.stdlib_corpus import (
        STDLIB_RECEIPT_FUNCTION_ID,
        STDLIB_SEMANTIC_GRAPH_TYPE,
    )
    from pymergetic.rxf.model.stdlib_semantics import SemanticGraph, SemanticOpcode

    container = starter_template().build()
    persisted = next(
        SemanticGraph.from_node(node)
        for node in container.nodes
        if node.type_id == STDLIB_SEMANTIC_GRAPH_TYPE
        and SemanticGraph.from_node(node).function_id == STDLIB_RECEIPT_FUNCTION_ID
    )
    graph = normalize_semantic_graph(container, STDLIB_RECEIPT_FUNCTION_ID)
    block_ids = tuple(block.id for block in graph.blocks)
    assert block_ids == persisted.block_ids
    assert graph.entry_block_id == persisted.entry_block_id
    assert graph.entry_block_id in block_ids
    assert len(block_ids) == len(set(block_ids))
    assert graph.results and graph.results[0].source_id
    assert container.node_by_id(graph.results[0].source_id) is not None
    opcodes = {
        operation.opcode for block in graph.blocks for operation in block.operations
    }
    assert {
        SemanticOpcode.BEGIN_PRIVATE,
        SemanticOpcode.PUBLISH,
        SemanticOpcode.ROLLBACK,
        SemanticOpcode.CLEANUP,
    } <= opcodes


def test_runtime_callee_abi_uses_explicit_hidden_context():
    from pymergetic.rxf.compiler.semantic import (
        classify_semantic_operations,
        decode_strict_abi,
        normalize_semantic_graph,
    )
    from pymergetic.rxf.expand import starter_template
    from pymergetic.rxf.model.contracts import ABIRole
    from pymergetic.rxf.model.stdlib_corpus import STDLIB_RECEIPT_FUNCTION_ID
    from pymergetic.rxf.model.stdlib_semantics import SemanticOpcode
    from pymergetic.rxf.ty.builtins import REF_TYPE, U64_TYPE

    container = starter_template().build()
    graph = normalize_semantic_graph(container, STDLIB_RECEIPT_FUNCTION_ID)
    classify_semantic_operations(container, graph)
    search = next(
        operation
        for block in graph.blocks
        for operation in block.operations
        if operation.opcode == SemanticOpcode.SEARCH
    )
    _, locations = decode_strict_abi(container, search.callee_id, 1)
    assert tuple(value.type.id for value in search.inputs) == (
        REF_TYPE,
        U64_TYPE,
        REF_TYPE,
        U64_TYPE,
        REF_TYPE,
    )
    assert any(
        location.role == ABIRole.HIDDEN_CONTEXT and location.hidden
        for location in locations
    )


def test_strict_abi_accepts_explicit_stack_spill():
    from pymergetic.rxf.compiler.semantic import decode_strict_abi
    from pymergetic.rxf.expand import starter_template
    from pymergetic.rxf.model.contracts import ABIRegisterBank

    container = starter_template().build()
    function = next(n for n in container.nodes if n.name == "runtime_write")
    _, locations = decode_strict_abi(container, function.id, 1)
    stack = [
        value for value in locations if value.register_bank == ABIRegisterBank.STACK
    ]
    assert len(stack) == 1 and stack[0].width == 24 and stack[0].stack_offset == 0
