"""Target-aware reachability closure and nonmutating DCE artifacts."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from pymergetic.rxf.execution.binder import preflight
from pymergetic.rxf.model.capabilities import CapabilityManifest
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.schema import NodeKind
from pymergetic.rxf.ty.builtins import (
    CODE_TYPE,
    FUNCTION_TYPE,
    RUNTIME_TARGET_TYPE,
    TARGET_TYPE,
)


@dataclass(frozen=True)
class ReachabilityReason:
    object_id: int
    reason: str


@dataclass(frozen=True)
class ReachabilityReport:
    entry_function: int
    target_id: int
    reachable_ids: tuple[int, ...]
    discarded_ids: tuple[int, ...]
    reasons: tuple[ReachabilityReason, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "entry_function": str(self.entry_function),
            "target_id": str(self.target_id),
            "reachable_ids": [str(value) for value in self.reachable_ids],
            "discarded_ids": [str(value) for value in self.discarded_ids],
            "reasons": [
                {"object_id": str(item.object_id), "reason": item.reason}
                for item in self.reasons
            ],
        }


def _close(
    by_id: dict[int, NodeDef], retained: set[int], reasons: dict[int, str]
) -> None:
    pending = sorted(retained, reverse=True)
    while pending:
        node = by_id.get(pending.pop())
        if node is None:
            continue
        linked = [(node.parent, "parent"), (node.type_id, "type")]
        linked.extend(
            (ref.target, "reference")
            for ref in node.refs
            if not (
                node.type_id == FUNCTION_TYPE
                and ref.to_off == 207  # CallRole.IMPLEMENTATION: selected by preflight
                and ref.target not in retained
            )
        )
        for target, reason in linked:
            if target in by_id and target not in retained:
                retained.add(target)
                reasons[target] = f"{reason} of {node.id}"
                pending.append(target)


def target_reachability(
    container: Container,
    entry_function: int,
    target_id: int,
    capabilities: CapabilityManifest | None = None,
) -> ReachabilityReport:
    plan = preflight(container, entry_function, target_id, capabilities)
    if not plan.ok:
        detail = "; ".join(item.message for item in plan.diagnostics)
        raise ValueError(f"target reachability requires successful preflight: {detail}")
    by_id = {node.id: node for node in container.nodes}
    retained = {0, entry_function, target_id}
    reasons = {
        0: "canonical root",
        entry_function: "entry function",
        target_id: "active target",
    }
    for selected in plan.functions:
        retained.update((selected.function_id, selected.code_id))
        reasons[selected.function_id] = "reachable terminal function"
        reasons[selected.code_id] = "selected target Code"
    for imported in plan.imports:
        retained.update(
            (imported.function_id, imported.import_id, imported.requirement_id)
        )
        reasons[imported.function_id] = "reachable imported function"
        reasons[imported.import_id] = "selected Import"
        reasons[imported.requirement_id] = "bound capability requirement"
    for patch in plan.patches:
        retained.update((patch.relocation_id, patch.target_id))
        reasons[patch.relocation_id] = "selected Code relocation"
        reasons[patch.target_id] = "relocation target"
    _close(by_id, retained, reasons)
    # Bootstrap self-description is always needed to inspect the pruned artifact.
    for node in container.nodes:
        if node.kind in (NodeKind.TYPE, NodeKind.FIELD):
            retained.add(node.id)
            reasons.setdefault(node.id, "bootstrap self-description")
    _close(by_id, retained, reasons)
    discarded = tuple(sorted(set(by_id) - retained))
    for value in discarded:
        node = by_id[value]
        if node.type_id == CODE_TYPE:
            reasons[value] = "unselected target Code"
        elif node.type_id in (TARGET_TYPE, RUNTIME_TARGET_TYPE):
            reasons[value] = "inactive target"
        elif node.type_id == FUNCTION_TYPE:
            reasons[value] = "unreachable Function"
        else:
            reasons[value] = "not in target closure"
    return ReachabilityReport(
        entry_function,
        target_id,
        tuple(sorted(retained)),
        discarded,
        tuple(ReachabilityReason(value, reasons[value]) for value in sorted(reasons)),
    )


def dce_report(
    container: Container,
    entry_function: int,
    target_id: int,
    capabilities: CapabilityManifest | None = None,
) -> ReachabilityReport:
    return target_reachability(container, entry_function, target_id, capabilities)


def prune_for_target(
    container: Container,
    entry_function: int,
    target_id: int,
    capabilities: CapabilityManifest | None = None,
) -> tuple[Container, ReachabilityReport]:
    """Return a deep-copied target artifact; never mutate the fat input."""
    report = target_reachability(container, entry_function, target_id, capabilities)
    keep = set(report.reachable_ids)
    pruned = deepcopy(container)
    pruned.nodes = [node for node in pruned.nodes if node.id in keep]
    for node in pruned.nodes:
        node.refs = [ref for ref in node.refs if ref.target in keep]
    pruned.header.entry_node = entry_function
    pruned.header.node_count = len(pruned.nodes)
    return pruned, report
