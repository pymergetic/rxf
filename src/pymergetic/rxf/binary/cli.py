"""Commands on the binary an image is: `rxf binary <command> SOURCE`."""

from __future__ import annotations

from typing import Any

from pymergetic.rxf import binary, source
from pymergetic.rxf.cli import Problems


def check(src: str) -> dict[str, Any]:
    """Every native function has code and ABI for every target; what the rest calls exists.

    Exit status 1 when something is missing or disagrees.
    """
    report = binary.check(source.load(src))
    if not report["ok"]:
        raise Problems(report)
    return report
