"""The native snippets of the starter, strictly from the Seed (docs/31 step 5).

There is no C compiler in the path: every byte of every ``Code`` object is what
the seed builders of ``tools/native/`` compile from ``native/**/*.rxfs``, the
wasm32 ABI of a code is derived from the shape of that source, and the
``CompilerProvenance`` a code names is the seed recipe that built it. These pins
hold the checked-in starter to that, and to being up to date with its sources
(``sync.py --check`` rebuilds everything and compares byte for byte).
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import native_build
import pytest
import sync

from .snippets import STARTER

ROOT = Path(__file__).parents[1]
RXF = "{urn:pymergetic:rxf}"


def test_the_targets_are_the_three_machines_of_the_starter():
    document = ET.parse(STARTER / "pymergetic" / "rxf" / "targets.rxfd")
    targets = {
        e.get("name", "").split(".")[-1]
        for e in document.getroot().iter("RuntimeTarget")
    }
    assert targets == {
        "x86_64_linux_sysv",
        "x86_64_uefi_sysv",
        "x86_64_bios_sysv",
        "aarch64_linux_aapcs64",
        "aarch64_uefi_aapcs64",
        "wasm32_wasm_c",
    }
    assert set(native_build.MACHINES) == {"x86_64", "aarch64", "wasm32"}


def test_every_starter_snippet_is_what_the_sources_compile_to():
    """The one form of the rule docs/31 §3 names: every Code body of every machine
    is what the seed builders make of `native/**/*.rxfs`, and the starter is
    already exactly that (a rebuild changes no byte)."""
    total, same = sync.sync(STARTER, check=True)
    assert total >= 321 and same


def test_the_compiler_provenance_is_the_seed_recipe():
    document = ET.parse(STARTER / "pymergetic" / "rxf" / "targets.rxfd")
    found = {
        p.get("name", ""): (
            p.get("compiler_major", ""),
            p.get("recipe_digest", ""),
        )
        for p in document.getroot().iter("CompilerProvenance")
    }
    assert sorted(found) == ["seed_aarch64", "seed_wasm32", "seed_x86_64"]
    assert found == {
        name: (native_build.SEED_MAJOR, native_build.recipe_digest(machine))
        for machine in native_build.MACHINES
        for name in (sync.provenance_name(machine),)
    }


def test_every_code_names_one_of_the_seed_recipes():
    """A Code object's provenance reference is one of the three seed recipes of the
    targets module -- nothing in the starter claims a compiler that is gone. A code of
    the Bytecode format is a program of the interpreter (what ``compose`` makes of a
    graph); it is not compiled by any machine and names no compiler."""
    names = {
        p.get("name")
        for p in ET.parse(STARTER / "pymergetic" / "rxf" / "targets.rxfd")
        .getroot()
        .iter("CompilerProvenance")
    }
    codes = compiled = provenance = 0
    for path in sorted(STARTER.rglob("*.rxfd")):
        for code in ET.parse(path).getroot().iter("Code"):
            codes += 1
            if sync.word(code.get("format")) == "Bytecode":
                continue
            compiled += 1
            for ref in code.findall(f"{RXF}ref"):
                if sync.word(ref.get("role")) == "Provenance":
                    provenance += 1
                    assert sync.word(ref.get("target")) in names, path
    assert codes > 300 and compiled > 300 and provenance == compiled


def test_no_c_source_is_left_in_native():
    """The C/C++ of the native modules is gone (docs/31 §6 step 3); what stays at
    the top of ``native/`` is the checked-in ABI description the seed implements."""
    assert sorted(p.name for p in (ROOT / "native").glob("*.c")) == []
    assert sorted(p.name for p in (ROOT / "native").glob("*.cpp")) == []
    assert sorted(p.name for p in (ROOT / "native").glob("*.h")) == [
        "basics.h",
        "graph.h",
        "stdlib_memory.h",
        "stdlib_runtime.h",
    ]


@pytest.mark.skipif(
    shutil.which("clang-format-18") is None, reason="needs clang-format-18"
)
def test_the_c_sources_are_formatted():
    sources = sorted((ROOT / "native").glob("*.[ch]")) + sorted(
        (ROOT / "tests" / "host").glob("*.[ch]")
    )
    result = subprocess.run(
        ["clang-format-18", "--dry-run", "-Werror", *sources],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
