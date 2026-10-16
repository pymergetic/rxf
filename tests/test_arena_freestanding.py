"""pymergetic::rxf::core::arena needs nothing but memcpy and memset from its target."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "src" / "pymergetic" / "rxftk" / "core" / "arena" / "arena.cpp"
TARGETS = (
    "x86_64-linux-gnu",
    "aarch64-linux-gnu",
    "i386-linux-gnu",
    "wasm32-unknown-unknown",
)
# memcpy and memset come from the target. The other two are linker symbols of the
# i386 PIC and wasm32 ABIs, not dependencies of the code.
ALLOWED = {"memcpy", "memset", "_GLOBAL_OFFSET_TABLE_", "__stack_pointer"}


@pytest.mark.parametrize("target", TARGETS)
def test_arena_is_freestanding(target, tmp_path):
    compiler, nm = shutil.which("clang++-18"), shutil.which("llvm-nm-18")
    if compiler is None or nm is None:
        pytest.skip("clang++-18 or llvm-nm-18 is not installed")
    obj = tmp_path / "arena.o"
    compiled = subprocess.run(
        [
            compiler,
            f"--target={target}",
            "-std=c++20",
            "-ffreestanding",
            "-nostdinc++",
            "-fno-exceptions",
            "-fno-rtti",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{ROOT / 'src'}",
            "-c",
            str(SOURCE),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    listing = subprocess.run(
        [nm, "-u", str(obj)], capture_output=True, text=True, check=True
    )
    undefined = {
        line.split()[-1] for line in listing.stdout.splitlines() if line.strip()
    }
    assert undefined <= ALLOWED, undefined - ALLOWED
