"""The numbers of ``native/graph.h``, read as the one place that defines them."""

from __future__ import annotations

import re
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[2] / "native"
HEADER = NATIVE / "graph.h"
RUNTIME = (
    NATIVE / "basics.h",
    NATIVE / "stdlib_runtime.h",
)  # the refusals of the leaves
ENUMERATOR = re.compile(r"\b(RXF_[A-Z0-9_]+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\b")
CONSTANT = re.compile(
    r"^#define\s+(RXF_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|\d+)U?L*\s*$", re.MULTILINE
)
COMPLEMENT = re.compile(r"^#define\s+(RXF_[A-Z0-9_]+)\s+\(~0U?L*\)\s*$", re.MULTILINE)


def numbers(text: str | None = None) -> dict[str, int]:
    """Every enumerator and numeric ``#define`` of the header, by name."""
    source = (
        "\n".join(path.read_text() for path in (HEADER, *RUNTIME))
        if text is None
        else text
    )
    found = {name: int(value, 0) for name, value in ENUMERATOR.findall(source)}
    found.update({name: int(value, 0) for name, value in CONSTANT.findall(source)})
    found.update({name: (1 << 64) - 1 for name in COMPLEMENT.findall(source)})
    return found


def leaves(text: str | None = None) -> dict[str, int]:
    """The leaves of the function table: snippet key (``runtime_read``) to id."""
    prefix = "RXF_LEAF_"
    return {
        name[len(prefix) :].lower(): value
        for name, value in numbers(text).items()
        if name.startswith(prefix)
    }
