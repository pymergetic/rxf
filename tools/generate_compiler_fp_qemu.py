"""Generate exact AArch64 composed FP and existing leaf byte arrays."""

import json
from dataclasses import replace
from pathlib import Path

from pymergetic.rxf.compiler import Architecture, compile_function, emit
from tests.compiler_fp_fixture import FP_FUNCTION, fp_template

ROOT = Path(__file__).parents[1]
manifest = json.loads((ROOT / "generated/native_aarch64.json").read_text())["functions"]
items = []
for symbol, type_id, names in [
    ("composed_f32", 31, ("checked_add_float", "checked_multiply_float")),
    ("composed_f64", 10, ("checked_add_double", "checked_multiply_double")),
]:
    c = fp_template(type_id).build()
    ir = compile_function(c, FP_FUNCTION)
    graph = __import__(
        "pymergetic.rxf.compiler.normalize", fromlist=["normalize"]
    ).normalize(c, FP_FUNCTION)
    funcs = tuple(sorted({x.function_id for x in graph.calls}))
    objs = emit(ir, Architecture.AARCH64).object_ids
    image = emit(
        replace(ir, binding_function_ids=funcs, binding_object_ids=objs),
        Architecture.AARCH64,
    )
    items.append((symbol, image.text))
    items += [(f"blob_{n}", bytes.fromhex(manifest[n]["hex"])) for n in names]
lines = ['.section .text.compiler_fp,"ax"']
for name, raw in items:
    lines += [".balign 16", f".global {name}", f".type {name}, %function", f"{name}:"]
    for i in range(0, len(raw), 16):
        lines.append("    .byte " + ",".join(f"0x{x:02x}" for x in raw[i : i + 16]))
    lines.append(f".size {name}, .-{name}")
(ROOT / "generated/compiler_fp_qemu_aarch64.S").write_text("\n".join(lines) + "\n")
# Both fixtures use the same three durable object identities; function IDs come
# from normalized call authority, never harness tuple positions.
f32 = fp_template(31).build()
f64 = fp_template(10).build()
f32_graph = __import__(
    "pymergetic.rxf.compiler.normalize", fromlist=["normalize"]
).normalize(f32, FP_FUNCTION)
f64_graph = __import__(
    "pymergetic.rxf.compiler.normalize", fromlist=["normalize"]
).normalize(f64, FP_FUNCTION)
f32_functions = tuple(sorted({call.function_id for call in f32_graph.calls}))
f64_functions = tuple(sorted({call.function_id for call in f64_graph.calls}))
f32_objects = emit(compile_function(f32, FP_FUNCTION), Architecture.AARCH64).object_ids
f64_objects = emit(compile_function(f64, FP_FUNCTION), Architecture.AARCH64).object_ids
assert f32_objects == f64_objects and len(f32_objects) == 3
constants = (
    f"""#ifndef RXF_COMPILER_FP_QEMU_BINDINGS_H
#define RXF_COMPILER_FP_QEMU_BINDINGS_H
#define RXF_F32_ADD_FUNCTION_ID {f32_functions[0]}ULL
#define RXF_F32_MUL_FUNCTION_ID {f32_functions[1]}ULL
#define RXF_F64_ADD_FUNCTION_ID {f64_functions[0]}ULL
#define RXF_F64_MUL_FUNCTION_ID {f64_functions[1]}ULL
"""
    + "".join(
        f"#define RXF_FP_OBJECT_{index}_ID {object_id}ULL\n"
        for index, object_id in enumerate(f32_objects)
    )
    + "#endif\n"
)
(ROOT / "generated/compiler_fp_qemu_bindings.h").write_text(constants)
