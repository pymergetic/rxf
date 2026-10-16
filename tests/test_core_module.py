"""The nanobind module reports the layout pinned in src/pymergetic/rxftk/core/ground.h."""

import importlib
import subprocess
import sys
from pathlib import Path

from pymergetic.rxftk import core
from pymergetic.rxftk.core.arena import (
    ARENA_HEADER_SIZE,
    ARENA_VERSION,
    BLOCK_INDEX,
    BLOCK_NAMES,
    CELL_ALIGN,
    CELL_SIZE,
    FIELD_SIZE,
    HANDLE_FIELD,
    HANDLE_NONE,
    HANDLE_ROOT,
    HANDLE_TYPE,
    INDEX_CHUNK_ENTRIES,
    KIND_BLOCK,
    KIND_DEAD,
    NAME_BLOCK_PAYLOAD,
    REF_SIZE,
    TYPE_SIZE,
)

CORE = Path(__file__).parents[1] / "src/pymergetic/rxftk/core"
STUBGEN = (
    "python -m nanobind.stubgen -m pymergetic.rxftk.core -r -q -O src/pymergetic/rxftk"
)


def test_the_checked_in_stubs_match_the_module(tmp_path):
    """The .pyi files next to the C++ modules are what editors read; they are generated."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "nanobind.stubgen",
            "-m",
            "pymergetic.rxftk.core",
            "-r",
            "-q",
            "-O",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
    )
    generated = {p.name: p.read_text() for p in (tmp_path / "core").glob("*.pyi")}
    checked_in = {p.name: p.read_text() for p in CORE.glob("*.pyi")}
    assert generated.keys() == checked_in.keys(), f"run: {STUBGEN}"
    for name, text in generated.items():
        assert checked_in[name] == text, f"{name} is stale; run: {STUBGEN}"


def test_every_submodule_imports_by_its_path():
    for name in ("kinds", "arena", "hash", "object", "meta", "delta", "tree", "mount"):
        module = importlib.import_module(f"pymergetic.rxftk.core.{name}")
        assert module.__name__ == f"pymergetic.rxftk.core.{name}"
        assert getattr(core, name) is module


def test_core_reports_ground_layout():
    assert ARENA_VERSION == 6
    assert ARENA_HEADER_SIZE == 192
    assert CELL_SIZE == 64
    assert REF_SIZE == 24
    assert TYPE_SIZE == 48
    assert FIELD_SIZE == 48
    assert CELL_ALIGN == 16
    assert INDEX_CHUNK_ENTRIES == 4096
    assert HANDLE_NONE == 2**64 - 1
    assert (HANDLE_ROOT, HANDLE_TYPE, HANDLE_FIELD) == (0, 1, 2)
    assert (KIND_DEAD, KIND_BLOCK) == (254, 255)
    assert (BLOCK_INDEX, BLOCK_NAMES) == (1, 2)
    assert NAME_BLOCK_PAYLOAD == 4096
