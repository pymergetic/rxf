"""Deterministic boot planning; no mapping or control transfer."""

from __future__ import annotations

from dataclasses import dataclass

from pymergetic.rxf.execution.binder import BindingPlan, Diagnostic, preflight
from pymergetic.rxf.model.capabilities import CapabilityManifest
from pymergetic.rxf.model.container import Container


@dataclass(frozen=True)
class BootPlan:
    entry_function: int
    target_id: int
    function_code: tuple[tuple[int, int], ...]
    import_requirements: tuple[tuple[int, int], ...]
    relocation_ids: tuple[int, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def ok(self) -> bool:
        return not self.diagnostics

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "entry_function": str(self.entry_function),
            "target_id": str(self.target_id),
            "functions": [
                {"function_id": str(function), "code_id": str(code)}
                for function, code in self.function_code
            ],
            "imports": [
                {"import_id": str(import_id), "requirement_id": str(requirement)}
                for import_id, requirement in self.import_requirements
            ],
            "relocations": [str(value) for value in self.relocation_ids],
            "refusals": [
                {
                    "code": item.code.value,
                    "object_id": str(item.object_id),
                    "message": item.message,
                }
                for item in self.diagnostics
            ],
        }


def boot_preflight(
    container: Container,
    entry_function: int,
    target_id: int,
    capabilities: CapabilityManifest | None = None,
) -> BootPlan:
    binding: BindingPlan = preflight(container, entry_function, target_id, capabilities)
    return BootPlan(
        entry_function,
        target_id,
        tuple((item.function_id, item.code_id) for item in binding.functions),
        tuple((item.import_id, item.requirement_id) for item in binding.imports),
        tuple(item.relocation_id for item in binding.patches),
        binding.diagnostics,
    )
