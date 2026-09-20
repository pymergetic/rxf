"""Generate array_new AArch64 bytes and authority-derived QEMU bindings."""

from pathlib import Path

from pymergetic.rxf.compiler import Architecture
from pymergetic.rxf.compiler.native import FixupNamespace, emit
from pymergetic.rxf.compiler.semantic import SemanticValueKind, normalize_semantic_graph
from pymergetic.rxf.compiler.semantic_lower import lower_semantic_program
from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program
from pymergetic.rxf.expand import starter_template

root = Path(__file__).parents[1]
container = starter_template().build()
function = next(
    node for node in container.nodes if node.name == "array_new" and node.type_id == 14
)
raw = lower_semantic_program(
    container, normalize_semantic_graph(container, function.id)
)
program = optimize_semantic_program(raw)
raw_image = emit(raw, Architecture.AARCH64)
image = emit(program, Architecture.AARCH64)
by_opcode = {
    operation.source.opcode.name: operation.source for operation in program.operations
}
begin = by_opcode["BEGIN_PRIVATE"]
validate = by_opcode["VALIDATE"]
allocate = by_opcode["ALLOCATE"]
assert begin.inputs[0].kind == SemanticValueKind.OBJECT
assert validate.inputs[0].kind == SemanticValueKind.OBJECT
begin_object_id = begin.inputs[0].source_id
validate_object_id = validate.inputs[0].source_id
assert begin_object_id != validate_object_id
assert set(image.object_ids) == {begin_object_id, validate_object_id}
assert allocate.callee_id in image.function_ids
fixup_functions = {
    f.target_id for f in image.fixups if f.namespace == FixupNamespace.FUNCTION
}
fixup_objects = {
    f.target_id for f in image.fixups if f.namespace == FixupNamespace.OBJECT
}
assert fixup_functions == set(image.function_ids)
assert fixup_objects == set(image.object_ids)
raw_values = ",".join(f"0x{byte:02x}" for byte in raw_image.text)
values = ",".join(f"0x{byte:02x}" for byte in image.text)
(root / "generated/semantic_cfg_qemu_aarch64.S").write_text(
    ".section .text\n.global semantic_cfg_blob_raw\nsemantic_cfg_blob_raw:\n.byte "
    + raw_values
    + "\n.global semantic_cfg_blob_optimized\nsemantic_cfg_blob_optimized:\n.byte "
    + values
    + "\n"
)
constants = f"""#ifndef RXF_SEMANTIC_CFG_QEMU_BINDINGS_H
#define RXF_SEMANTIC_CFG_QEMU_BINDINGS_H
#define RXF_ARRAY_BEGIN_OBJECT_ID {begin_object_id}ULL
#define RXF_ARRAY_VALIDATE_OBJECT_ID {validate_object_id}ULL
#define RXF_ARRAY_BEGIN_FUNCTION_ID {begin.callee_id}ULL
#define RXF_ARRAY_ALLOCATE_FUNCTION_ID {allocate.callee_id}ULL
#define RXF_ARRAY_VALIDATE_FUNCTION_ID {validate.callee_id}ULL
#define RXF_ARRAY_PUBLISH_FUNCTION_ID {by_opcode["PUBLISH"].callee_id}ULL
#define RXF_ARRAY_ROLLBACK_FUNCTION_ID {by_opcode["ROLLBACK"].callee_id}ULL
#define RXF_ARRAY_CLEANUP_FUNCTION_ID {by_opcode["CLEANUP"].callee_id}ULL
#endif
"""
(root / "generated/semantic_cfg_qemu_bindings.h").write_text(constants)
