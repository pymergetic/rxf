"""The UDP echo on the emulated seats: every seat proves or names its wall (all-platforms).

The echo differential of ``tests/lwip_slip.py`` runs on the host seat (the socketpair fill
``slip_line_host.c``). The emulated seats of the boot smoke -- x86_64-bios, aarch64-virt,
wasm32-node, x86_64-uefi (``boot/run_smoke.sh``) -- carry the image and their own line; the
echo belongs on every one of them. A seat that can carry the echo runs it and the reply bytes
are compared to the native reference byte for byte; a seat that cannot is a **named failing
stub** here -- a strict xfail with the wall it hits and why. Never a skip, never a silent
pass, no dark port.

A wall is falsifiable: the assertion under the xfail is the seat's line fill (the file that
moves SLIP frames between the card's line cell and the seat's real line). When the fill lands
the strict xfail turns into an xpass and the failure forces the echo's prove onto that seat
(the pattern of ``tests/test_lwip_slip.py::test_the_seat_has_a_fill_that_meets_the_contract``).
The named reasons carry the second wall of every seat as well: the seat's image must carry the
card (the machine, the lwIP graphs and the echo bodies). It does not today -- the images carry
the glue program of the entry mini-runtime -- so both walls stand together and fall together.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINE = ROOT / "native" / "lwip"

SEATS = ("x86_64-bios", "aarch64-virt", "wasm32-node", "x86_64-uefi")

# (the seat's line fill, the wall and why). The three firmware seats speak UART (16550 on
# x86_64, PL011 on aarch64): one fill, three carriers. The wasm32 seat speaks its FIFO block.
WALLS = {
    "x86_64-bios": (
        "slip_line_uart.c",
        (
            "the BIOS seat's line is the 16550 UART: the fill slip_line_uart.c is unwritten "
            "(named in tests/test_lwip_slip.py UNPROVEN_SEATS) and the seat's image carries the "
            "glue program of the entry mini-runtime, not the card or the machine the echo needs"
        ),
    ),
    "aarch64-virt": (
        "slip_line_uart.c",
        (
            "the virt seat's line is the PL011 UART: the fill slip_line_uart.c is unwritten "
            "(named in tests/test_lwip_slip.py UNPROVEN_SEATS) and the seat's image carries the "
            "glue program of the entry mini-runtime, not the card or the machine the echo needs"
        ),
    ),
    "wasm32-node": (
        "slip_line_js.c",
        (
            "the wasm32 seat's line is its FIFO block: the fill slip_line_js.c is unwritten "
            "(named in tests/test_lwip_slip.py UNPROVEN_SEATS) and the seat's image carries the "
            "live pump, not the card or the machine the echo needs"
        ),
    ),
    "x86_64-uefi": (
        "slip_line_uart.c",
        (
            "the UEFI seat's line is the 16550 UART behind OVMF: the fill slip_line_uart.c is "
            "unwritten (named in tests/test_lwip_slip.py UNPROVEN_SEATS) and the seat's image "
            "carries the glue program of the entry mini-runtime, not the card or the machine "
            "the echo needs"
        ),
    ),
}

assert set(WALLS) == set(SEATS), "every seat names its wall, one wall per seat"


@pytest.mark.parametrize(
    ("seat", "fill", "why"),
    [
        pytest.param(
            seat,
            *WALLS[seat],
            marks=pytest.mark.xfail(
                strict=True,
                reason=f"{WALLS[seat][0]}: {WALLS[seat][1]}",
            ),
        )
        for seat in SEATS
    ],
)
def test_the_echo_runs_on_the_seat_or_the_seat_names_its_wall(
    seat: str, fill: str, why: str
) -> None:
    """The wall, asserted falsifiable: the seat's line fill exists. When it does, the seat is
    claimed and the echo must run over that fill's line, scenario for scenario, the reply bytes
    equal to the native reference (the differential of tests/lwip_udp_echo.py over this seat's
    line face) -- that prove is the force of this strict xfail."""
    assert (LINE / fill).is_file(), f"{seat}: {fill} is unwritten ({why})"
    pytest.fail(
        f"{seat}: {fill} exists -- run the echo's scenarios over this seat's line and compare "
        "the reply bytes to the native reference (tests/lwip_udp_echo.py), then keep this seat "
        "proven"
    )
