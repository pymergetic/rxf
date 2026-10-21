"""``tools/native/arch_proxy.py`` pinned on the starter: the map, the resolution, the gaps.

The proxy is the one place that answers which code a function carries for which machine.
The starter is pinned to answer for every target of the map, and a function of only a
bytecode program is pinned to never be part of the question: its program runs everywhere.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import arch_proxy
import graph_sync

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
ADD = "pymergetic.rxf.numeric.wrapping.add_int16_t"

MAP = {
    "aarch64_linux_aapcs64": {
        "architecture": "Aarch64",
        "abi": "Aapcs64",
        "environment": "Linux",
    },
    "aarch64_uefi_aapcs64": {
        "architecture": "Aarch64",
        "abi": "Aapcs64",
        "environment": "Uefi",
    },
    "wasm32_wasm_c": {
        "architecture": "Wasm32",
        "abi": "Wasm",
        "environment": "Wasm",
    },
    "x86_64_bios_sysv": {
        "architecture": "X8664",
        "abi": "Sysv",
        "environment": "Bios",
    },
    "x86_64_linux_sysv": {
        "architecture": "X8664",
        "abi": "Sysv",
        "environment": "Linux",
    },
    "x86_64_uefi_sysv": {
        "architecture": "X8664",
        "abi": "Sysv",
        "environment": "Uefi",
    },
}


def test_the_target_map_is_the_one_of_the_starter():
    assert arch_proxy.targets(STARTER) == MAP


def test_one_function_resolves_on_every_target():
    code = arch_proxy.resolve(STARTER, ADD)
    assert sorted(code) == sorted(MAP)
    for target, facts in code.items():
        assert "refusal" not in facts, target
        assert facts["format"] == ("Wasm" if target == "wasm32_wasm_c" else "Machine")
        assert int(facts["byte_count"]) > 0
        assert facts["provenance"].startswith("seed_"), facts
        assert facts["abi"], facts


def test_a_target_without_code_answers_the_named_refusal():
    assert arch_proxy.resolve(STARTER, ADD, "riscv64_linux_sysv") == {
        "riscv64_linux_sysv": {"refusal": "no code for riscv64_linux_sysv"}
    }


def test_the_starter_has_no_gaps_for_any_target():
    assert arch_proxy.gaps(STARTER) == []
    for target in MAP:
        assert arch_proxy.gaps(STARTER, target) == []


def test_a_program_of_the_interpreter_is_never_a_gap():
    """A function whose only Code has no runtime_target runs everywhere; the proxy leaves it out."""
    programs = set()
    for path in sorted(STARTER.rglob("*.rxfd")):
        parts = path.relative_to(STARTER).with_suffix("").parts
        for label, function in graph_sync.functions_in(
            ET.parse(path).getroot(), graph_sync.module_prefix(parts)
        ):
            codes = [c for c in function if c.tag == "Code"]
            if codes and all(c.get("runtime_target") is None for c in codes):
                programs.add(label)
    assert programs, "the starter should carry programs of the interpreter"
    assert not programs & set(arch_proxy.functions(STARTER))
    assert not [g for g in arch_proxy.gaps(STARTER) if g[0] in programs]


def test_an_unknown_target_is_not_silently_accepted():
    try:
        arch_proxy.gaps(STARTER, "riscv64_linux_sysv")
    except SystemExit as refusal:
        assert "not a target" in str(refusal)
    else:
        raise AssertionError("an unknown target must be refused")
