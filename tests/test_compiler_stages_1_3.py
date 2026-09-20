"""Stages 1-3 composed compiler, movable slots, and native image proofs."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from pymergetic.rxf.compiler import (
    Architecture,
    CompileError,
    RuntimeContext,
    compile_function,
    compile_targets,
    emit,
    normalize,
)
from pymergetic.rxf.compiler.ir import CompiledFunction
from pymergetic.rxf.compiler.native import LinkError, _validate_fixups
from pymergetic.rxf.expand import (
    STARTER_CHECKOUT_FUNCTION_ID,
    STARTER_MAIN_FUNCTION_ID,
    starter_template,
)


def test_x86_object_resolver_supports_complete_tables_over_255_entries() -> None:
    from pymergetic.rxf.compiler.compile import compile_function
    from pymergetic.rxf.compiler.native import Architecture, emit
    from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template

    container = starter_template().build()
    ir = compile_function(container, STARTER_MAIN_FUNCTION_ID)
    object_ids = tuple(sorted(node.id for node in container.nodes))
    assert len(object_ids) > 255
    image = emit(replace(ir, binding_object_ids=object_ids), Architecture.X86_64)
    assert image.text


def test_aarch64_object_resolver_materializes_full_table_offsets() -> None:
    container = starter_template().build()
    ir = compile_function(container, STARTER_CHECKOUT_FUNCTION_ID)
    # Place real referenced object IDs beyond the 12-bit immediate range while
    # retaining deterministic sorted-index semantics.
    referenced = {
        -argument.id - 1
        for block in ir.blocks
        for operation in block.operations
        for argument in operation.arguments
        if argument.id < 0
    }
    padding = tuple(range(1, 101))
    object_ids = tuple(sorted((*padding, *referenced)))
    image = emit(replace(ir, binding_object_ids=object_ids), Architecture.AARCH64)
    # MOVZ/MOVK + register ADD is required once index * 80 exceeds 12 bits.
    words = [
        int.from_bytes(image.text[i : i + 4], "little")
        for i in range(0, len(image.text), 4)
    ]
    assert any(word & 0xFF80001F == 0xD2800012 for word in words)
    assert any(word & 0xFFE0FC00 == 0x8B000000 for word in words)


def test_starter_normalizes_independent_of_node_order():
    original = starter_template().build()
    shuffled = deepcopy(original)
    random.Random(57).shuffle(shuffled.nodes)
    assert normalize(original, STARTER_CHECKOUT_FUNCTION_ID) == normalize(
        shuffled, STARTER_CHECKOUT_FUNCTION_ID
    )
    assert (
        compile_targets(original, STARTER_CHECKOUT_FUNCTION_ID).inspect()
        == compile_targets(shuffled, STARTER_CHECKOUT_FUNCTION_ID).inspect()
    )


def test_checkout_and_main_have_status_explicit_ir_and_both_targets():
    container = starter_template().build()
    checkout = compile_function(container, STARTER_CHECKOUT_FUNCTION_ID)
    main = compile_function(container, STARTER_MAIN_FUNCTION_ID)
    assert isinstance(checkout, CompiledFunction)
    assert isinstance(main, CompiledFunction)
    assert sum(len(block.operations) for block in checkout.blocks) == 6
    assert sum(len(block.operations) for block in main.blocks) == 1
    for ir in (checkout, main):
        images = {arch: emit(ir, arch) for arch in Architecture}
        assert images[Architecture.X86_64].text
        assert images[Architecture.AARCH64].text.endswith(bytes.fromhex("c0035fd6"))
        assert all(image.fixups for image in images.values())
        assert all(
            (slot.offset + slot.width) <= 256
            for slot in images[Architecture.X86_64].frame
        )


def test_slots_move_code_and_object_without_changing_durable_ids():
    context = RuntimeContext.empty()
    context.codes.publish({9: object()})
    context.objects.publish({17: bytearray(b"old")})
    old_code = context.codes.resolve(9)
    old_object = context.objects.resolve(17)
    new_code_payload = object()
    new_object_payload = bytearray(b"new")
    context.codes.update(9, new_code_payload)
    context.objects.update(17, new_object_payload)
    assert context.codes.resolve(9).payload is new_code_payload
    assert context.objects.resolve(17).payload is new_object_payload
    assert context.codes.resolve(9).generation == old_code.generation + 1
    assert context.objects.resolve(17).generation == old_object.generation + 1


def test_corrupt_fixup_refuses():
    image = compile_targets(
        starter_template().build(), STARTER_MAIN_FUNCTION_ID
    ).images[0]
    broken = [deepcopy(image.fixups[0])]
    object.__setattr__(broken[0], "offset", len(image.text))
    with pytest.raises(LinkError, match="invalid fixup"):
        _validate_fixups(bytearray(image.text), broken)


def test_result_cycle_refuses():
    container = starter_template().build()
    graph = normalize(container, STARTER_CHECKOUT_FUNCTION_ID)
    terminal_call = container.node_by_id(graph.calls[-1].id)
    assert terminal_call is not None
    argument_id = next(ref.target for ref in terminal_call.refs if ref.to_off == 203)
    argument = container.node_by_id(argument_id)
    assert argument is not None
    value = container.node_by_id(int.from_bytes(argument.data[8:16], "little"))
    assert value is not None
    value.data = (
        value.data[:8]
        + (3).to_bytes(4, "little")
        + bytes(4)
        + (8).to_bytes(8, "little")
        + graph.calls[-1].id.to_bytes(8, "little")
    )
    with pytest.raises(CompileError, match="cycle"):
        normalize(container, STARTER_CHECKOUT_FUNCTION_ID)


def _x86_execution(refuse_index=None):
    import ctypes
    import json
    import mmap
    from dataclasses import replace
    from pathlib import Path

    from pymergetic.rxf.compiler.native import executable
    from pymergetic.rxf.compiler.runtime import native_context

    container = starter_template().build()
    graph = normalize(container, STARTER_CHECKOUT_FUNCTION_ID)
    manifest = json.loads(
        (Path(__file__).parents[1] / "generated/native_x86_64.json").read_text()
    )["functions"]
    callback_type = ctypes.CFUNCTYPE(
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
    )
    keepalive = []
    functions = {}
    for position, call in enumerate(graph.calls):
        function = container.node_by_id(call.function_id)
        assert function is not None
        raw = bytes.fromhex(manifest[function.name]["hex"])
        memory = mmap.mmap(
            -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
        )
        memory.write(raw)
        keepalive.append(memory)
        leaf = callback_type(ctypes.addressof(ctypes.c_char.from_buffer(memory)))
        positions = [
            i
            for i, candidate in enumerate(graph.calls)
            if candidate.function_id == call.function_id
        ]
        if refuse_index in positions:
            calls = {"value": 0}

            def refusing_provider(
                a, b, out, leaf=leaf, positions=positions, calls=calls
            ):
                current = positions[calls["value"]]
                calls["value"] += 1
                return current + 11 if current == refuse_index else leaf(a, b, out)

            provider = refusing_provider
        else:

            def provider(a, b, out, leaf=leaf):
                return leaf(a, b, out)

        callback = callback_type(provider)
        keepalive.append(callback)
        functions[call.function_id] = ctypes.cast(callback, ctypes.c_void_p).value
    context = RuntimeContext.empty()
    context.functions.publish(functions)
    values = []
    objects = {}
    local = compile_function(container, STARTER_CHECKOUT_FUNCTION_ID)
    checkout_objects = emit(local, Architecture.X86_64).object_ids
    for object_id in checkout_objects:
        node = container.node_by_id(object_id)
        assert node is not None
        value = ctypes.c_uint32(int.from_bytes(node.data, "little"))
        values.append(value)
        objects[object_id] = ctypes.addressof(value)
    context.objects.publish(objects)
    global_functions = tuple(sorted((*functions, STARTER_CHECKOUT_FUNCTION_ID)))
    checkout_ir = replace(
        local,
        binding_function_ids=global_functions,
        binding_object_ids=tuple(sorted(objects)),
    )
    checkout_image = emit(checkout_ir, Architecture.X86_64)
    checkout_memory, checkout_address = executable(checkout_image)
    keepalive.append(checkout_memory)
    context.functions.update(STARTER_CHECKOUT_FUNCTION_ID, checkout_address)
    owner = native_context(context)
    main_ir = replace(
        compile_function(container, STARTER_MAIN_FUNCTION_ID),
        binding_function_ids=global_functions,
        binding_object_ids=tuple(sorted(objects)),
    )
    main_image = emit(main_ir, Architecture.X86_64)
    main_memory, main_address = executable(main_image)
    keepalive.append(main_memory)
    function = ctypes.CFUNCTYPE(
        ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)
    )(main_address)
    output = ctypes.c_uint32(0xA5A5A5A5)
    status = function(ctypes.byref(owner.context), ctypes.byref(output))
    return status, output.value, checkout_image, main_image


def test_exact_x86_composed_main_returns_570():
    status, output, checkout, main = _x86_execution()
    assert (status, output) == (0, 570)
    assert checkout.text and main.text


def test_exact_x86_composed_main_subprocess_reports_crashes_safely():
    import subprocess
    import sys

    script = (
        "from tests.test_compiler_stages_1_3 import _x86_execution; "
        "status, output, checkout, main = _x86_execution(); "
        "assert (status, output) == (0, 570); "
        "print(len(checkout.text), len(main.text), len(checkout.fixups), len(main.fixups))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, (result.returncode, result.stdout, result.stderr)


def test_legacy_x86_object_fixups_use_current_runtime_entry_layout():
    import ctypes

    from pymergetic.rxf.compiler.native import FixupNamespace
    from pymergetic.rxf.compiler.runtime import NativeObjectEntry

    assert ctypes.sizeof(NativeObjectEntry) == 80
    assert NativeObjectEntry.type_id.offset == 16
    assert NativeObjectEntry.payload.offset == 24
    container = starter_template().build()
    local = compile_function(container, STARTER_CHECKOUT_FUNCTION_ID)
    image = emit(local, Architecture.X86_64)
    object_fixups = [
        fixup for fixup in image.fixups if fixup.namespace == FixupNamespace.OBJECT
    ]
    assert {fixup.target_id for fixup in object_fixups} == set(image.object_ids)
    assert all(fixup.offset + fixup.width <= len(image.text) for fixup in image.fixups)
    assert b"\x4d\x8b\x5b\x18" in image.text


@pytest.mark.parametrize("position", range(6))
def test_x86_each_leaf_refusal_preserves_output(position):
    status, output, _, _ = _x86_execution(position)
    assert status == position + 11
    assert output == 0xA5A5A5A5


def test_aarch64_qemu_executes_exact_composed_bytes(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    qemu = shutil.which("qemu-system-aarch64")
    if qemu is None:
        pytest.skip("qemu-system-aarch64 unavailable")
    root = Path(__file__).parents[1]
    subprocess.run(
        [sys.executable, str(root / "tools/generate_compiler_qemu.py")], check=True
    )
    flags = [
        "-target",
        "aarch64-none-elf",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
    ]
    subprocess.run(
        [
            "clang-18",
            *flags,
            "-O2",
            "-c",
            str(root / "native/compiler_qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "clang-18",
            "-target",
            "aarch64-none-elf",
            "-c",
            str(root / "native/qemu_start.S"),
            "-o",
            str(tmp_path / "s.o"),
        ],
        check=True,
    )
    subprocess.run(
        [
            "clang-18",
            "-target",
            "aarch64-none-elf",
            "-c",
            str(root / "generated/compiler_qemu_aarch64.S"),
            "-o",
            str(tmp_path / "b.o"),
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
    assert result.returncode == 0
    assert "RXF-COMPOSED-PASS 570" in result.stdout + result.stderr


def _x86_fp_execution(type_id, values, sentinel_bits):
    import ctypes
    import json
    import mmap
    import struct
    from dataclasses import replace
    from pathlib import Path

    from pymergetic.rxf.compiler.native import executable
    from pymergetic.rxf.compiler.runtime import native_context
    from pymergetic.rxf.ty.builtins import F32_TYPE
    from tests.compiler_fp_fixture import FP_FUNCTION, fp_template

    container = fp_template(type_id, values).build()
    graph = normalize(container, FP_FUNCTION)
    manifest = json.loads(
        (Path(__file__).parents[1] / "generated/native_x86_64.json").read_text()
    )["functions"]
    scalar = ctypes.c_float if type_id == F32_TYPE else ctypes.c_double
    keep = []
    functions = {}
    for call in graph.calls:
        fn = container.node_by_id(call.function_id)
        assert fn is not None
        raw = bytes.fromhex(manifest[fn.name]["hex"])
        mem = mmap.mmap(
            -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
        )
        mem.write(raw)
        keep.append(mem)
        functions[call.function_id] = ctypes.addressof(ctypes.c_char.from_buffer(mem))
    context = RuntimeContext.empty()
    context.functions.publish(functions)
    objects = {}
    vals = []
    local = compile_function(container, FP_FUNCTION)
    image0 = emit(local, Architecture.X86_64)
    for oid in image0.object_ids:
        node = container.node_by_id(oid)
        assert node is not None
        value = scalar(
            struct.unpack("<f" if type_id == F32_TYPE else "<d", node.data)[0]
        )
        vals.append(value)
        objects[oid] = ctypes.addressof(value)
    context.objects.publish(objects)
    ir = replace(
        local,
        binding_function_ids=tuple(sorted(functions)),
        binding_object_ids=tuple(sorted(objects)),
    )
    image = emit(ir, Architecture.X86_64)
    memory, address = executable(image)
    keep.append(memory)
    owner = native_context(context)
    bits = (
        ctypes.c_uint32(sentinel_bits)
        if type_id == F32_TYPE
        else ctypes.c_uint64(sentinel_bits)
    )
    function = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p)(
        address
    )
    status = function(ctypes.byref(owner.context), ctypes.byref(bits))
    return status, bits.value, image, ir


@pytest.mark.parametrize(
    ("type_id", "values", "sentinel", "expected"),
    [
        (31, (1.25, 2.5, 2.0), 0x7FC12345, 0x40F00000),
        (10, (1.25, 2.5, 2.0), 0x7FF8123456789ABC, 0x401E000000000000),
    ],
)
def test_exact_x86_composed_fp_finite(type_id, values, sentinel, expected):
    status, bits, image, ir = _x86_fp_execution(type_id, values, sentinel)
    assert (status, bits) == (0, expected)
    assert all(slot.width == (4 if type_id == 31 else 8) for slot in image.frame)
    assert ir.semantic_digest


@pytest.mark.parametrize(
    ("type_id", "values", "sentinel"),
    [
        (31, (float("inf"), 1.0, 2.0), 0x7FC12345),
        (31, (3.0, 1.0, float("inf")), 0x7FC12345),
        (10, (float("nan"), 1.0, 2.0), 0x7FF8123456789ABC),
        (10, (3.0, 1.0, float("inf")), 0x7FF8123456789ABC),
    ],
)
def test_exact_x86_composed_fp_refusal_preserves_bits(type_id, values, sentinel):
    status, bits, _, _ = _x86_fp_execution(type_id, values, sentinel)
    assert status == 4 and bits == sentinel


def test_exact_x86_composed_fp_signed_zero():
    status, bits, _, _ = _x86_fp_execution(31, (-0.0, 0.0, 1.0), 0x7FC12345)
    assert status == 0 and bits == 0x00000000


def test_composed_fp_shuffle_determinism():
    from tests.compiler_fp_fixture import FP_FUNCTION, fp_template

    original = fp_template().build()
    shuffled = deepcopy(original)
    random.Random(99).shuffle(shuffled.nodes)
    assert compile_function(original, FP_FUNCTION) == compile_function(
        shuffled, FP_FUNCTION
    )


def test_aarch64_qemu_executes_exact_composed_fp_bytes(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    qemu = shutil.which("qemu-system-aarch64")
    if qemu is None:
        pytest.skip("qemu-system-aarch64 unavailable")
    root = Path(__file__).parents[1]
    subprocess.run(
        [sys.executable, str(root / "tools/generate_compiler_fp_qemu.py")],
        check=True,
        cwd=root,
        env={**__import__("os").environ, "PYTHONPATH": f"{root / 'src'}:{root}"},
    )
    flags = [
        "-target",
        "aarch64-none-elf",
        "-ffreestanding",
        "-fno-builtin",
        "-fno-stack-protector",
    ]
    subprocess.run(
        [
            "clang-18",
            *flags,
            "-O2",
            "-c",
            str(root / "native/compiler_fp_qemu_harness.c"),
            "-o",
            str(tmp_path / "h.o"),
        ],
        check=True,
    )
    for source, name in (
        (root / "native/qemu_start.S", "s.o"),
        (root / "generated/compiler_fp_qemu_aarch64.S", "b.o"),
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
    assert (
        result.returncode == 0
        and "RXF-COMPOSED-FP-PASS" in result.stdout + result.stderr
    )


def test_legacy_aarch64_frame_excludes_callee_save_area():
    container = starter_template().build()
    image = emit(
        compile_function(container, STARTER_CHECKOUT_FUNCTION_ID),
        Architecture.AARCH64,
    )
    # Five saved register pairs occupy sp+0..79; SSA storage must start above it.
    assert min(slot.offset for slot in image.frame) >= 80
    assert all(slot.offset + slot.width <= 4095 for slot in image.frame)
    assert b"\x31\x0e\x40\xf9" in image.text  # object payload load at +24
