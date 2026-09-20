"""Target-ready derived compilation artifacts and inspection views."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pymergetic.rxf.compiler.compile import compile_function
from pymergetic.rxf.compiler.native import Architecture, NativeImage, emit
from pymergetic.rxf.model.container import Container


@dataclass(frozen=True)
class CompilationArtifact:
    function_id: int
    signature_id: int
    semantic_digest: bytes
    images: tuple[NativeImage, ...]

    def inspect(self) -> dict:
        return {
            "function_id": str(self.function_id),
            "signature_id": str(self.signature_id),
            "semantic_digest": self.semantic_digest.hex(),
            "images": [
                {
                    "architecture": image.architecture.name.lower(),
                    "text_size": len(image.text),
                    "text": image.text.hex(),
                    "digest": image.digest.hex(),
                    "frame": [asdict(slot) for slot in image.frame],
                    "calls": [str(value) for value in image.function_ids],
                    "objects": [str(value) for value in image.object_ids],
                    "fixups": [
                        {
                            **asdict(fixup),
                            "kind": fixup.kind.name.lower(),
                            "namespace": fixup.namespace.name.lower(),
                            "target_id": str(fixup.target_id),
                        }
                        for fixup in image.fixups
                    ],
                }
                for image in self.images
            ],
        }


def compile_targets(container: Container, function_id: int) -> CompilationArtifact:
    ir = compile_function(container, function_id)
    images = tuple(emit(ir, architecture) for architecture in Architecture)
    return CompilationArtifact(function_id, ir.signature_id, ir.semantic_digest, images)
