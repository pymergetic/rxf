"""Boot planning, reachability, and reflection tests."""

from pymergetic.rxf.execution import preflight
from pymergetic.rxf.execution.boot import boot_preflight
from pymergetic.rxf.execution.reachability import dce_report, prune_for_target
from pymergetic.rxf.expand import NATIVE_ENTRY_FUNCTION_ID, native_binding_template
from pymergetic.rxf.model.target import AARCH64_UEFI_TARGET_ID, X86_64_LINUX_TARGET_ID
from pymergetic.rxf.reflection import (
    migration_plan,
    reflect_functions,
    reflect_types,
    tooling_graph_json,
)


def test_boot_plan_is_deterministic_and_planning_only() -> None:
    container = native_binding_template().build()
    before = container.to_dict()
    first = boot_preflight(container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    second = boot_preflight(container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    assert first == second and first.ok
    assert container.to_dict() == before
    assert first.to_dict()["functions"]


def test_reachability_dce_report_is_nonmutating_and_idempotent() -> None:
    container = native_binding_template().build()
    before = tooling_graph_json(container)
    first = dce_report(container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    second = dce_report(container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    assert first == second
    assert first.reachable_ids == tuple(sorted(first.reachable_ids))
    assert tooling_graph_json(container) == before
    pruned, report = prune_for_target(
        container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID
    )
    assert tooling_graph_json(container) == before
    assert preflight(pruned, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID).ok
    assert AARCH64_UEFI_TARGET_ID in report.discarded_ids
    assert (
        prune_for_target(container, NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID)[
            0
        ].to_dict()
        == pruned.to_dict()
    )


def test_typed_reflection_and_migration_plan() -> None:
    container = native_binding_template().build()
    types = reflect_types(container)
    functions = reflect_functions(container)
    assert types and functions
    source = types[0]
    plan = migration_plan(container, source.id, source.id)
    assert plan.ok
    assert tooling_graph_json(container) == tooling_graph_json(container)
