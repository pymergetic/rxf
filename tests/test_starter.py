"""Canonical user-facing starter image invariants."""

import json
from pathlib import Path

from pymergetic.rxf.bridge import container_to_layout
from pymergetic.rxf.checker import check
from pymergetic.rxf.execution import preflight, reachable_terminal_functions
from pymergetic.rxf.expand import (
    STARTER_APPLICATION_MODULE_ID,
    STARTER_FOUNDATION_MODULE_ID,
    STARTER_LIBRARY_MODULE_ID,
    STARTER_MAIN_FUNCTION_ID,
    STARTER_NORMALIZE_FUNCTION_ID,
    starter_template,
)
from pymergetic.rxf.model.module import derived_fqns
from pymergetic.rxf.model.target import (
    AARCH64_LINUX_TARGET_ID,
    AARCH64_UEFI_TARGET_ID,
    X86_64_LINUX_TARGET_ID,
    X86_64_UEFI_TARGET_ID,
)
from pymergetic.rxf.output.engine import pack_layout

ROOT = Path(__file__).parents[1]


def test_starter_tree_entry_and_reachability() -> None:
    container = starter_template().build()
    fqns = derived_fqns(container.nodes)
    assert check(container) == []
    assert container.header.entry_node == STARTER_MAIN_FUNCTION_ID
    assert fqns[STARTER_FOUNDATION_MODULE_ID] == "pymergetic.rxf.numeric"
    assert fqns[STARTER_LIBRARY_MODULE_ID] == "pymergetic.rxf.library"
    assert fqns[STARTER_APPLICATION_MODULE_ID] == "pymergetic.rxf.application"
    assert (
        fqns[STARTER_NORMALIZE_FUNCTION_ID] == "pymergetic.rxf.library.checkout_total"
    )
    assert fqns[STARTER_MAIN_FUNCTION_ID] == "pymergetic.rxf.application.main"
    assert "pymergetic.rxf.numeric" in fqns.values()
    assert len(reachable_terminal_functions(container, STARTER_MAIN_FUNCTION_ID)) == 4


def test_starter_preflights_exact_code_for_all_targets() -> None:
    container = starter_template().build()
    x86 = preflight(container, STARTER_MAIN_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    arm = preflight(container, STARTER_MAIN_FUNCTION_ID, AARCH64_UEFI_TARGET_ID)
    linux_arm = preflight(container, STARTER_MAIN_FUNCTION_ID, AARCH64_LINUX_TARGET_ID)
    uefi_x86 = preflight(container, STARTER_MAIN_FUNCTION_ID, X86_64_UEFI_TARGET_ID)
    plans = (x86, arm, linux_arm, uefi_x86)
    assert all(plan.ok and len(plan.functions) == 4 for plan in plans)
    assert all(item.code_id for plan in plans for item in plan.functions)
    uefi = {item.function_id: item.code_id for item in arm.functions}
    linux = {item.function_id: item.code_id for item in linux_arm.functions}
    assert uefi.keys() == linux.keys()
    assert all(uefi[function_id] != linux[function_id] for function_id in uefi)


def test_stdlib_generates_numeric_callback_index_once(monkeypatch) -> None:
    from pymergetic.rxf.model import stdlib_corpus

    calls = 0
    generate = stdlib_corpus.numeric_nodes

    def counted_numeric_nodes():
        nonlocal calls
        calls += 1
        return generate()

    monkeypatch.setattr(stdlib_corpus, "numeric_nodes", counted_numeric_nodes)
    stdlib_corpus.stdlib_nodes()
    assert calls == 1


def test_checked_in_starter_artifacts_are_deterministic() -> None:
    container = starter_template().build()
    expected_json = json.dumps(container.to_dict(), indent=2, sort_keys=True) + "\n"
    expected_rxf = pack_layout(container_to_layout(container))
    assert (ROOT / "examples/starter.json").read_text() == expected_json
    assert (ROOT / "examples/starter.rxf").read_bytes() == expected_rxf


def test_example_config_mounts_only_examples() -> None:
    config = (ROOT / "rxf-server.example.toml").read_text()
    assert 'path = "examples"' in config
    assert 'path = "tests"' not in config
    assert sorted(path.name for path in (ROOT / "examples").iterdir()) == [
        "starter.json",
        "starter.rxf",
    ]
