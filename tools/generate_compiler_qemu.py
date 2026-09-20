"""Generate authored assembly arrays containing exact compiler and leaf bytes."""

import json
from dataclasses import replace
from pathlib import Path

from pymergetic.rxf.compiler import Architecture, compile_function, emit
from pymergetic.rxf.expand import (
    STARTER_CHECKOUT_FUNCTION_ID,
    STARTER_MAIN_FUNCTION_ID,
    starter_template,
)

ROOT = Path(__file__).parents[1]
c = starter_template().build()
functions = (10633, 10663, 10693, 10723, STARTER_CHECKOUT_FUNCTION_ID)
objects = (40100, 40102, 40104, 40106, 40108, 40110)
checkout = emit(
    replace(
        compile_function(c, STARTER_CHECKOUT_FUNCTION_ID),
        binding_function_ids=functions,
        binding_object_ids=objects,
    ),
    Architecture.AARCH64,
)
main = emit(
    replace(
        compile_function(c, STARTER_MAIN_FUNCTION_ID),
        binding_function_ids=functions,
        binding_object_ids=objects,
    ),
    Architecture.AARCH64,
)
manifest = json.loads((ROOT / "generated/native_aarch64.json").read_text())["functions"]
items = [("composed_checkout", checkout.text), ("composed_main", main.text)] + [
    (f"blob_{n}", bytes.fromhex(manifest[n]["hex"]))
    for n in (
        "checked_add_uint32_t",
        "checked_subtract_uint32_t",
        "checked_multiply_uint32_t",
        "divide_uint32_t",
    )
]
lines = ['.section .text.compiler,"ax"']
for name, raw in items:
    lines += [".balign 16", f".global {name}", f".type {name}, %function", f"{name}:"]
    for i in range(0, len(raw), 16):
        lines.append("    .byte " + ",".join(f"0x{x:02x}" for x in raw[i : i + 16]))
    lines.append(f".size {name}, .-{name}")
(ROOT / "generated/compiler_qemu_aarch64.S").write_text("\n".join(lines) + "\n")
constants = (
    f"""#ifndef RXF_COMPILER_QEMU_BINDINGS_H
#define RXF_COMPILER_QEMU_BINDINGS_H
#define RXF_ADD_FUNCTION_ID {functions[0]}ULL
#define RXF_SUB_FUNCTION_ID {functions[1]}ULL
#define RXF_MUL_FUNCTION_ID {functions[2]}ULL
#define RXF_DIV_FUNCTION_ID {functions[3]}ULL
#define RXF_CHECKOUT_FUNCTION_ID {functions[4]}ULL
"""
    + "".join(
        f"#define RXF_OBJECT_{index}_ID {object_id}ULL\n"
        for index, object_id in enumerate(objects)
    )
    + "#endif\n"
)
(ROOT / "generated/compiler_qemu_bindings.h").write_text(constants)
