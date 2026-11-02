"""The session policy of the suite: the aarch64 seat waits behind the others.

A change that goes through every seat (a compiler, a leaf, the starter) is proven on the seats
that run fast and are known to work first; the aarch64 seat is emulated, slow, and still has an
open wall (docs: the emulator faults in every compiled-code call), so running it before the
rest only buries the answer. A test whose parameter names the seat (`[aarch64]`,
`[aarch64-arith]`, `[aarch64 PL011]`) carries the marker `aarch64` and is held back:

    pytest ...                       the other seats; the aarch64 cases are deselected and counted
    pytest -m aarch64                only the aarch64 cases
    RXF_SEATS=all pytest ...         every seat, the aarch64 cases last
    tools/test_lanes.sh aarch64      the aarch64 lane (after the others in the `full` lane)

`aarch64_linux` and the like are the names of a *target* the rxfb readers look up, not a seat
that runs code, and are left alone.
"""

from __future__ import annotations

import os
import re

import pytest

SEATS_ENV = "RXF_SEATS"
_SEAT = re.compile(r"(?<!\w)aarch64(?!\w)")


def _asked_for(config: pytest.Config) -> bool:
    """Whether this run wants the aarch64 seat: by `-m` or by RXF_SEATS=all|aarch64."""
    if "aarch64" in (config.getoption("markexpr") or ""):
        return True
    return os.environ.get(SEATS_ENV, "").strip() in ("all", "aarch64")


def pytest_report_header(config: pytest.Config) -> str:
    where = "runs, last" if _asked_for(config) else f"waits ({SEATS_ENV}=all or -m aarch64 runs it)"
    return f"seats: wasm32, x86_64; aarch64 {where}"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        spec = getattr(item, "callspec", None)
        if spec is not None and _SEAT.search(spec.id):
            item.add_marker(pytest.mark.aarch64)
    held = [item for item in items if item.get_closest_marker("aarch64") is not None]
    if not held:
        return
    if _asked_for(config):
        items.sort(key=lambda item: item.get_closest_marker("aarch64") is not None)  # stable: last
        return
    config.hook.pytest_deselected(items=held)
    items[:] = [item for item in items if item not in held]
