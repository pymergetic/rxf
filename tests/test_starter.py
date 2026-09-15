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
from pymergetic.rxf.model.target import AARCH64_UEFI_TARGET_ID, X86_64_LINUX_TARGET_ID
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


def test_starter_preflights_exact_code_for_both_targets() -> None:
    container = starter_template().build()
    x86 = preflight(container, STARTER_MAIN_FUNCTION_ID, X86_64_LINUX_TARGET_ID)
    arm = preflight(container, STARTER_MAIN_FUNCTION_ID, AARCH64_UEFI_TARGET_ID)
    assert x86.ok and len(x86.functions) == 4
    assert arm.ok and len(arm.functions) == 4
    assert all(item.code_id for item in (*x86.functions, *arm.functions))


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
