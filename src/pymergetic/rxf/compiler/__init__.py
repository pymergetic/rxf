from pymergetic.rxf.compiler.cfg import ControlFunction, verify_control
from pymergetic.rxf.compiler.control_native import emit_control

"""RXF composed Function compiler and native target artifacts."""

from pymergetic.rxf.compiler.artifact import CompilationArtifact, compile_targets
from pymergetic.rxf.compiler.compile import compile_function, verify
from pymergetic.rxf.compiler.native import (
    Architecture,
    Fixup,
    NativeImage,
    emit,
    emit_aarch64,
    emit_x86_64,
)
from pymergetic.rxf.compiler.normalize import (
    CompileError,
    NormalizedFunction,
    normalize,
)
from pymergetic.rxf.compiler.runtime import (
    CapabilitySlot,
    CodeSlot,
    FunctionSlot,
    NativeRuntimeContext,
    NativeSlot,
    ObjectSlot,
    ResolverError,
    RuntimeContext,
    native_context,
)
from pymergetic.rxf.compiler.semantic_lower import (
    OptimizationReport,
    SpecializationBinding,
)
from pymergetic.rxf.compiler.semantic_optimize import optimize_semantic_program

__all__ = [
    "Architecture",
    "CapabilitySlot",
    "CodeSlot",
    "CompilationArtifact",
    "CompileError",
    "ControlFunction",
    "Fixup",
    "FunctionSlot",
    "NativeImage",
    "NativeRuntimeContext",
    "NativeSlot",
    "NormalizedFunction",
    "ObjectSlot",
    "OptimizationReport",
    "ResolverError",
    "RuntimeContext",
    "SpecializationBinding",
    "compile_function",
    "compile_targets",
    "emit",
    "emit_aarch64",
    "emit_control",
    "emit_x86_64",
    "native_context",
    "normalize",
    "optimize_semantic_program",
    "verify",
    "verify_control",
]
