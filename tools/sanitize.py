"""Build the core with AddressSanitizer and UBSan and run the core tests against it.

    python tools/sanitize.py [pytest arguments]      (default: the core tests, RXF_FUZZ=5)

The extension is built in a temporary directory and loaded in place of the installed one; the
interpreter itself is not instrumented, so the sanitizer runtimes are preloaded. Linux, gcc or clang.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORE_TESTS = [
    "tests/test_fuzz.py",
    "tests/test_hardening.py",
    "tests/test_delta.py",
    "tests/test_arena.py",
    "tests/test_journal.py",
    "tests/test_live.py",
    "tests/test_delta_binary.py",
    "tests/test_core_module.py",
    "tests/test_roles.py",
    "tests/test_tree.py",
    "tests/test_starter_delta.py",
]
FLAGS = "-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=undefined"


def runtime(name: str) -> str:
    out = subprocess.run(
        ["gcc", f"-print-file-name=lib{name}.so"],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def main(arguments: list[str]) -> int:
    with tempfile.TemporaryDirectory(prefix="rxf-sanitize-") as build:
        configure = [
            "cmake",
            "-S",
            str(ROOT),
            "-B",
            build,
            "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
            f"-DPython_EXECUTABLE={sys.executable}",
            f"-DCMAKE_C_FLAGS={FLAGS}",
            f"-DCMAKE_CXX_FLAGS={FLAGS}",
            "-DCMAKE_SHARED_LINKER_FLAGS=-fsanitize=address,undefined",
        ]
        subprocess.run(configure, check=True)
        subprocess.run(["cmake", "--build", build, "-j"], check=True)
        assert glob.glob(f"{build}/__init__.cpython-*.so"), "the module was not built"
        env = dict(os.environ)
        env.setdefault("RXF_FUZZ", "5")
        env["ASAN_OPTIONS"] = "detect_leaks=0"
        env["UBSAN_OPTIONS"] = "print_stacktrace=1:halt_on_error=1"
        env["LD_PRELOAD"] = f"{runtime('asan')} {runtime('ubsan')}"
        tests = arguments or CORE_TESTS
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("sanitize_run.py")),
                build,
                "-q",
                "-p",
                "no:cacheprovider",
                *tests,
            ],
            cwd=ROOT,
            env=env,
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
