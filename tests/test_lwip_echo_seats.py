"""The UDP echo on the emulated seats: every seat proves or names its wall (all-platforms).

The echo differential of ``tests/lwip_slip.py`` runs on the host seat (the socketpair fill
``slip_line_host.c``). The emulated seats of the boot smoke -- x86_64-bios, aarch64-virt,
wasm32-node, x86_64-uefi (``boot/run_smoke.sh``) -- carry the image and their own line; the
echo belongs on every one of them. A seat that can carry the echo runs it and the reply bytes
are compared to the native reference byte for byte; a seat that cannot is a **named failing
stub** here -- a strict xfail with the wall it hits and why. Never a skip, never a silent
pass, no dark port.

The line fills themselves are written and meet the contract on every seat (tests/test_lwip_slip.py:
``slip_line_uart.c`` for the three UART seats, ``slip_line_js.c`` for the wasm32 seat). The
wall that stands is the second one of the all-platforms rule: the seat's **image** must carry
the card -- the machine, the lwIP graphs and the echo bodies. The images carry the glue
program of the entry mini-runtime (``boot/run_smoke.sh`` never mentions the echo), so the
echo cannot run on them yet.

A wall is falsifiable: the assertion under the xfail is the image carriage tripwire -- it
fails today and passes the moment the seat's image carries the echo (``boot/run_smoke.sh``
starts it). The strict xfail then turns into an xpass and the failure forces the echo's
prove onto that seat: the differential of ``tests/lwip_udp_echo.py`` over that seat's line,
scenario by scenario, byte for byte, and then the mark comes off.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "boot" / "run_smoke.sh"

SEATS = ("x86_64-bios", "aarch64-virt", "wasm32-node", "x86_64-uefi")

# (the seat's line fill, the wall and why). The three firmware seats speak UART (16550 on
# x86_64, PL011 on aarch64): one fill, three carriers. The wasm32 seat speaks its FIFO block.
WALLS = {
    "x86_64-bios": (
        "slip_line_uart.c",
        (
            "the BIOS seat's line is the 16550 UART, its fill meets the contract "
            "(tests/test_lwip_slip.py) but the seat's image carries the glue program of the "
            "entry mini-runtime, not the card or the machine the echo needs (boot/run_smoke.sh)"
        ),
    ),
    "aarch64-virt": (
        "slip_line_uart.c",
        (
            "the virt seat's line is the PL011 UART, its fill meets the contract "
            "(tests/test_lwip_slip.py) but the seat's image carries the glue program of the "
            "entry mini-runtime, not the card or the machine the echo needs (boot/run_smoke.sh)"
        ),
    ),
    "wasm32-node": (
        "slip_line_js.c",
        (
            "the wasm32 seat's line is its FIFO block, its fill meets the contract "
            "(tests/test_lwip_slip.py) but the seat's image carries the live pump, not the "
            "card or the machine the echo needs (boot/run_smoke.sh)"
        ),
    ),
    "x86_64-uefi": (
        "slip_line_uart.c",
        (
            "the UEFI seat's line is the 16550 UART behind OVMF, its fill meets the contract "
            "(tests/test_lwip_slip.py) but the seat's image carries the glue program of the "
            "entry mini-runtime, not the card or the machine the echo needs (boot/run_smoke.sh)"
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
    """The wall, asserted falsifiable: the seat's line fill is written, the seat's image does
    not carry the echo yet (this xfail is that absence, named). When the image carries it,
    this xpasses and the strict xfail forces the echo's scenarios over this seat's line onto
    the suite -- the reply bytes equal to the native reference (the differential of
    tests/lwip_udp_echo.py), and then this mark comes off."""
    assert (ROOT / "native" / "lwip" / fill).is_file(), f"{seat}: {fill} is unwritten ({why})"
    assert "slip_echo" in SMOKE.read_text(), (
        f"{seat}: {why} -- when the image carries the echo this xpasses: run the echo's "
        "scenarios over this seat's line, byte for byte against the native reference "
        "(tests/lwip_udp_echo.py), and drop this xfail"
    )
