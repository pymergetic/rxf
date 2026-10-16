"""The ground types keep their layout on every target and in both languages.

The assertions live in src/pymergetic/rxftk/core/ground.h; this test compiles the header and
fails if any assertion fails.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
INCLUDE = ROOT / "src"
CHECKS = Path(__file__).parent / "ground"
TARGETS = (
    "x86_64-linux-gnu",
    "aarch64-linux-gnu",
    "i386-linux-gnu",
    "wasm32-unknown-unknown",
)
LANGUAGES = (
    ("clang-18", "c11", "check.c"),
    ("clang++-18", "c++20", "check.cpp"),
)


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize(("compiler", "standard", "source"), LANGUAGES)
def test_ground_layout_holds(target, compiler, standard, source):
    if shutil.which(compiler) is None:
        pytest.skip(f"{compiler} is not installed")
    result = subprocess.run(
        [
            compiler,
            f"--target={target}",
            f"-std={standard}",
            "-ffreestanding",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            f"-I{INCLUDE}",
            str(CHECKS / source),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
