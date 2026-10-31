"""The SLIP line of the lwIP card (docs/23 4.4, docs/25 13): the face, the host seat's fill, and
the contract every seat's fill must meet.

The line is a byte line. The face (``native/lwip/slip_line_face.c``) is imported with the card;
a seat's fill (``native/lwip/slip_line_*.c``) is native C that moves bytes between the line cell
and the seat's real line. ``LineContract`` is the one suite every seat runs: open/close, TX
bytes in order, TX overrun counted and never short-written, RX bytes in order, RX backpressure
(a full ring loses nothing), the readiness word, the ``framing`` and ``overrun`` counters as
words of the cell, and the MTU word. A seat that cannot run it in the current environment is a
stub that FAILS, named in ``SEATS`` (never a skipped seat).
"""

from __future__ import annotations

import ctypes
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import lwip_import
import pytest

from . import lwip_udp_echo, rxf_native
from .test_lwip_import import CORPUS, OK, Host

ROOT = Path(__file__).resolve().parents[1]
LINE = ROOT / "native" / "lwip"
FACE_BODIES = (
    "sio_open",
    "sio_send",
    "sio_tryread",
    "rxf_slip_line_close",
    "rxf_slip_line_ready",
)
RING = 4096
MTU = 1000
END, ESC, ESC_END, ESC_ESC = 0xC0, 0xDB, 0xDC, 0xDD

# offsets in the line cell (native/lwip/slip_line.h): four words, then the TX ring, then the RX
COUNTERS = struct.Struct("<4I")  # open, mtu, overrun, framing
TX_AT = COUNTERS.size
RX_AT = TX_AT + 8 + RING


def cell_words(cell, at: int) -> tuple[int, int]:
    return struct.unpack_from("<2I", cell, at)


class Face:
    """The face of the line as one call surface: imported graphs or the natively compiled C."""

    def __init__(self, cell) -> None:
        self.cell = cell
        self.address = ctypes.addressof(ctypes.c_uint8.from_buffer(cell))

    @property
    def counters(self) -> tuple[int, int, int, int]:
        return COUNTERS.unpack_from(self.cell, 0)

    @property
    def tx_waiting(self) -> int:
        head, tail = cell_words(self.cell, TX_AT)
        return (head - tail) & 0xFFFFFFFF

    def open(self, device: int) -> int:
        raise NotImplementedError

    def send(self, byte: int) -> None:
        raise NotImplementedError

    def tryread(self, count: int) -> bytes:
        raise NotImplementedError

    def ready(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class ImportedFace(Face):
    def __init__(self, host: Host, memory: rxf_native.Memory) -> None:
        super().__init__(host.named["rxf_slip_line"])
        self.host, self.memory = host, memory

    def open(self, device: int) -> int:
        status, answer = self.host.run(self.memory, "sio_open", device)
        assert status == OK
        return answer

    def send(self, byte: int) -> None:
        assert self.host.run(self.memory, "sio_send", byte, self.address)[0] == OK

    def tryread(self, count: int) -> bytes:
        self.memory.write(0x400, bytes(count))
        status, got = self.host.run(
            self.memory, "sio_tryread", self.address, self.memory.at(0x400), count
        )
        assert status == OK
        return bytes(self.memory.read(0x400, got))

    def ready(self) -> int:
        status, answer = self.host.run(self.memory, "rxf_slip_line_ready")
        assert status == OK
        return answer

    def close(self) -> None:
        assert self.host.run(self.memory, "rxf_slip_line_close")[0] == OK


class NativeFace(Face):
    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        size = COUNTERS.size + 2 * (8 + RING)
        symbol = ctypes.c_uint8.in_dll(lib, "rxf_slip_line")
        super().__init__((ctypes.c_uint8 * size).from_address(ctypes.addressof(symbol)))
        self.address = ctypes.addressof(symbol)
        lib.sio_open.restype = ctypes.c_void_p
        lib.sio_open.argtypes = [ctypes.c_uint8]
        lib.sio_send.argtypes = [ctypes.c_uint8, ctypes.c_void_p]
        lib.sio_tryread.restype = ctypes.c_uint32
        lib.sio_tryread.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        lib.rxf_slip_line_ready.restype = ctypes.c_uint32

    def open(self, device: int) -> int:
        return self.lib.sio_open(device) or 0

    def send(self, byte: int) -> None:
        self.lib.sio_send(byte, self.address)

    def tryread(self, count: int) -> bytes:
        buffer = ctypes.create_string_buffer(count)
        got = self.lib.sio_tryread(self.address, buffer, count)
        return buffer.raw[:got]

    def ready(self) -> int:
        return self.lib.rxf_slip_line_ready()

    def close(self) -> None:
        self.lib.rxf_slip_line_close()


class HostFill(ctypes.Structure):
    """struct rxf_slip_line_host of native/lwip/slip_line_host.h."""

    _fields_ = (
        ("near_fd", ctypes.c_int),
        ("far_fd", ctypes.c_int),
        ("escaped", ctypes.c_uint32),
        ("frame", ctypes.c_uint32),
    )


class HostSeat:
    """The host seat's fill (slip_line_host.c, a socketpair) and the peer on its far end."""

    name = "host"

    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        lib.rxf_slip_line_host_pump.restype = ctypes.c_int64
        lib.rxf_slip_line_host_pump.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(HostFill),
        ]
        self.fill = HostFill()
        assert lib.rxf_slip_line_host_init(ctypes.byref(self.fill)) == 0

    def close(self) -> None:
        self.lib.rxf_slip_line_host_deinit(ctypes.byref(self.fill))

    def pump(self, face: Face) -> int:
        moved = self.lib.rxf_slip_line_host_pump(face.address, ctypes.byref(self.fill))
        assert moved >= 0, "the fill's I/O failed"
        return moved

    def peer_send(self, data: bytes) -> int:
        """Bytes the peer manages to put on the line (a full socket buffer takes a part)."""
        try:
            return os.write(self.fill.far_fd, data)
        except BlockingIOError:
            return 0

    def peer_recv(self) -> bytes:
        out = b""
        while True:
            try:
                got = os.read(self.fill.far_fd, 65536)
            except BlockingIOError:
                return out
            if not got:
                return out
            out += got


def frame(payload: bytes) -> bytes:
    """RFC 1055: END, the stuffed payload, END."""
    body = payload.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
    return b"\xc0" + body + b"\xc0"


class LineContract:
    """The contract suite of the SLIP line: the same assertions on every seat and every face."""

    def __init__(self, face: Face, seat: HostSeat) -> None:
        self.face, self.seat = face, seat

    def run(self) -> None:
        face, seat = self.face, self.seat
        # open: only device 0 exists; a send on a closed line goes nowhere
        face.send(0x41)
        assert face.tx_waiting == 0, "a closed line takes no byte"
        assert face.open(1) == 0, "no device 1"
        assert face.counters[0] == 0
        assert face.open(0) == face.address, (
            "sio_open answers the handle of the line cell"
        )
        assert face.counters[0] == 1 and face.counters[1] == MTU, (
            "open, and the MTU word is 1000"
        )

        # TX: bytes reach the peer in order, whole
        sent = bytes(range(256))
        for byte in sent:
            face.send(byte)
        assert face.tx_waiting == 256
        assert seat.pump(face) == 256
        assert seat.peer_recv() == sent
        assert face.tx_waiting == 0

        # TX overrun: a full ring drops the rest and counts every dropped byte
        before = face.counters[2]
        for index in range(RING + 10):
            face.send(index & 0xFF)
        assert face.tx_waiting == RING
        assert face.counters[2] == before + 10, "ten bytes dropped, ten counted"
        seat.pump(face)
        assert seat.peer_recv() == bytes(index & 0xFF for index in range(RING))

        # RX: bytes arrive in order, and the readiness word says how many wait
        assert face.ready() == 0 and face.tryread(8) == b""
        data = bytes((index * 7 + 3) & 0xFF for index in range(3000))
        assert seat.peer_send(data) == 3000
        seat.pump(face)
        assert face.ready() == 3000
        got = b""
        for chunk in (1, 7, 100, 1000):
            got += face.tryread(chunk)
        assert got == data[:1108]
        assert face.ready() == 3000 - 1108
        got += face.tryread(4000)
        assert got == data and face.ready() == 0 and face.tryread(1) == b""

        # RX backpressure: a full ring loses nothing, the rest waits on the line
        big = bytes(index & 0xFF for index in range(6000))
        queued = 0
        while queued < len(big):
            wrote = seat.peer_send(big[queued:])
            assert wrote > 0
            queued += wrote
        seat.pump(face)
        assert face.ready() == RING, "the ring holds what it can"
        received = face.tryread(RING)
        seat.pump(face)
        assert face.ready() == 6000 - RING
        received += face.tryread(RING)
        assert received == big, "not one byte lost across the full ring"

        # framing: an escape followed by a byte that is neither ESC_END nor ESC_ESC
        base = face.counters[3]
        seat.peer_send(bytes([END, ESC, ESC_END, ESC, ESC_ESC, END]))
        seat.pump(face)
        face.tryread(16)
        assert face.counters[3] == base, "well-formed escapes are not errors"
        seat.peer_send(bytes([END, ESC, 0x41, END]))
        seat.pump(face)
        face.tryread(16)
        assert face.counters[3] == base + 1, (
            "ESC then 0x41 is a framing error, counted once"
        )

        # MTU: exactly the MTU is fine, one more is an overrun, counted once per frame
        base = face.counters[2]
        seat.peer_send(frame(b"\x41" * MTU))
        seat.pump(face)
        face.tryread(RING)
        assert face.counters[2] == base
        seat.peer_send(frame(b"\x41" * (MTU + 1)))
        seat.pump(face)
        face.tryread(RING)
        assert face.counters[2] == base + 1
        # a stuffed frame is counted by its decoded length, not its wire length
        seat.peer_send(frame(b"\xc0" * (MTU // 2 + 1)))
        seat.pump(face)
        face.tryread(RING)
        assert face.counters[2] == base + 1, "stuffing is not payload"

        # close: the line is shut and takes no byte again
        face.close()
        assert face.counters[0] == 0
        face.send(0x42)
        assert face.tx_waiting == 0


@pytest.fixture(scope="module")
def line_host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(
        tmp_path_factory.mktemp("lwip_line"),
        CORPUS / "slip_line.c",
        FACE_BODIES,
        natives=(LINE / "slip_line_host.c",),
    )


def test_the_host_seat_fill_meets_the_contract_on_the_imported_face(line_host: Host):
    seat = HostSeat(line_host.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            LineContract(ImportedFace(line_host, memory), seat).run()
    finally:
        seat.close()


def test_the_host_seat_fill_meets_the_contract_on_the_native_reference(line_host: Host):
    seat = HostSeat(line_host.native)
    try:
        LineContract(NativeFace(line_host.native), seat).run()
    finally:
        seat.close()


def test_the_face_is_imported_as_graphs_not_as_a_fill(line_host: Host):
    """The bodies lwIP calls are programs of the image; no leaf or native symbol serves them."""
    for name in FACE_BODIES:
        assert f"{lwip_import.MODULE}.{name}" in line_host.programs, name
    assert "rxf_slip_line" in line_host.named


# ---- the real UDP echo over the line (tests/lwip_udp_echo.py) -----------------------------------

# the interpreter with room the echo path needs (see lwip_udp_echo.echo_path_budget): the 8-link,
# 8 KiB budget of native/graph.h is short of lwIP's call chain; this is the smallest round
# numbers over what the chain measures (14960 bytes of frames at depth 9 on the C path)
EXPERIMENT_CELL = 32768
EXPERIMENT_DEPTH = 16
SHIPPED_CELL = 8192
SHIPPED_DEPTH = 8


@pytest.fixture(scope="module")
def echo_host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return lwip_udp_echo.build_host(tmp_path_factory.mktemp("lwip_udp_echo"))


def assert_same(
    imported: lwip_udp_echo.Outcome, reference: lwip_udp_echo.Outcome
) -> None:
    assert imported.status == 0, f"the imported run was refused ({imported.status})"
    for scenario in lwip_udp_echo.scenarios():
        if scenario.divergent:
            continue
        assert imported.replies[scenario.name] == reference.replies[scenario.name], (
            f"{scenario.name}: the imported graphs put {imported.replies[scenario.name].hex()} "
            f"on the line, natively it is {reference.replies[scenario.name].hex()}"
        )
        lwip_udp_echo.check_reply(
            scenario, lwip_udp_echo.decode(reference.replies[scenario.name])
        )
    assert imported.counters == reference.counters
    assert imported.counters[2:] == (0, 0), (
        "no overrun and no framing error on a clean line"
    )


@pytest.mark.slow
def test_the_echo_path_needs_more_than_the_shipped_interpreter_has(echo_host: Host):
    """The measured machine need: the chain from the harness entry down the real path is over
    the run cell (8192 bytes) and over the call depth (8 links) of native/graph.h, and the
    shipped interpreter refuses it with the budget refusals, never a wrong answer."""
    frames, links = lwip_udp_echo.echo_path_budget(echo_host)
    assert (frames, links) == (14960, 9), "the frames of the echo path, measured"
    assert links > SHIPPED_DEPTH, "the path is deeper than the depth budget"
    assert frames > SHIPPED_CELL, "the frames of the echo path exceed the run cell"
    assert frames <= 16384, "...and fit a 16 KiB cell"
    imported, _reference = lwip_udp_echo.run_both(echo_host)
    assert imported.status in (lwip_udp_echo.CELL_BUDGET, lwip_udp_echo.CALL_DEPTH), (
        f"the shipped interpreter answered {imported.status}: the budget moved, flip the xfail "
        "of test_the_udp_echo_matches_native_on_the_shipped_interpreter"
    )


@pytest.mark.slow
@pytest.mark.xfail(
    strict=True,
    reason=(
        "machine need (docs/35): RXF_GRAPH_CELL_BYTES 8192 -> >= 16384 and RXF_GRAPH_CALL_DEPTH "
        "8 -> >= 9 in native/graph.h and native/graph.rxfs; the echo path's frames sum to "
        "14960 bytes at depth 9"
    ),
)
def test_the_udp_echo_matches_native_on_the_shipped_interpreter(echo_host: Host):
    imported, reference = lwip_udp_echo.run_both(echo_host)
    assert_same(imported, reference)


@pytest.fixture(scope="module")
def echo_with_room() -> dict:
    """The echo scenarios on the shipped seed program with a larger run cell and call depth (the
    one machine need the budget test measures); nothing else differs."""
    done = subprocess.run(
        [
            sys.executable, "-m", "tests.lwip_udp_echo",
            "--cell", str(EXPERIMENT_CELL), "--depth", str(EXPERIMENT_DEPTH),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


@pytest.mark.slow
def test_the_udp_echo_matches_native_on_an_interpreter_with_room(echo_with_room: dict):
    """The real UDP echo: SLIP-framed IPv4/UDP and ICMP datagrams in over the line, the imported
    graphs of the card answer, the reply bytes out over the line equal what the same lwIP C
    compiled natively puts there, scenario by scenario (the one divergent scenario is pinned
    below). The replies are also read by the peer: right addresses, ports, payloads, checksums."""
    answer = echo_with_room
    assert answer["status"] == 0, f"the imported run was refused ({answer['status']})"
    scenarios = lwip_udp_echo.scenarios()
    assert list(answer["same"]) == [scenario.name for scenario in scenarios]
    for scenario in scenarios:
        lwip_udp_echo.check_reply(
            scenario,
            lwip_udp_echo.decode(bytes.fromhex(answer["replies"][scenario.name])),
        )
        if not scenario.divergent:
            assert answer["same"][scenario.name], (
                f"{scenario.name}: the reply bytes differ"
            )
    imported, reference = answer["counters"]
    assert imported == reference and imported[2:] == [0, 0]


@pytest.mark.slow
def test_the_divergent_scenario_is_exactly_the_one_named(echo_with_room: dict):
    """Only the ICMP port-unreachable differs, and it differs in the destination address."""
    differing = [name for name, same in echo_with_room["same"].items() if not same]
    assert differing == ["udp to a closed port"], differing


@pytest.mark.slow
@pytest.mark.xfail(
    strict=True,
    reason="importer defect (docs/35): " + lwip_udp_echo.KNOWN_DIVERGENCE,
)
def test_the_icmp_unreachable_matches_native(echo_with_room: dict):
    assert echo_with_room["same"]["udp to a closed port"]
