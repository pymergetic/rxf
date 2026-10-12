"""Runs pytest against the sanitized core in a build directory (started by sanitize.py).

usage: sanitize_run.py BUILD_DIR PYTEST_ARGS...

The sanitized extension is loaded as `pymergetic.rxf.core` before anything imports
the installed one; the tests then import it by its package path as usual.
"""

import glob
import importlib.util
import sys

import pytest

import pymergetic.rxf as package


def main(build: str, arguments: list[str]) -> int:
    (library,) = glob.glob(f"{build}/__init__.cpython-*.so")
    spec = importlib.util.spec_from_file_location("pymergetic.rxf.core", library)
    assert spec is not None and spec.loader is not None
    core = importlib.util.module_from_spec(spec)
    sys.modules["pymergetic.rxf.core"] = core
    spec.loader.exec_module(core)
    package.core = core  # type: ignore[attr-defined]
    return pytest.main(arguments)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2:]))
