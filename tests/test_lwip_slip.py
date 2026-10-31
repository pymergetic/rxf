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
import errno
import json
import os
import socket
import struct
import subprocess
import sys
from pathlib import Path

import lwip_import
import pytest

from pymergetic.rxftk.net.link import slip

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


# ---- the unix seat's tap fill: a SLIP <-> packet bridge (slip_line_tap.c) ------------------------


class TapFill(ctypes.Structure):
    """struct rxf_slip_line_tap of native/lwip/slip_line_tap.h."""

    _fields_ = (
        ("fd", ctypes.c_int),
        ("tx_escaped", ctypes.c_uint32),
        ("tx_length", ctypes.c_uint32),
        ("tx_overrun", ctypes.c_uint32),
        ("rx_length", ctypes.c_uint32),
        ("tx_packet", ctypes.c_uint8 * 2048),
        ("rx_packet", ctypes.c_uint8 * 2048),
    )


class TapSeat:
    """The tap fill, with a datagram socketpair standing in for the tun device: one packet per
    read and per write, which is all the bridge asks of its descriptor. The peer is the IP side."""

    name = "tap (datagram stand-in)"

    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        lib.rxf_slip_line_tap_pump.restype = ctypes.c_int64
        lib.rxf_slip_line_tap_pump.argtypes = [ctypes.c_void_p, ctypes.POINTER(TapFill)]
        self.fill = TapFill()
        lib.rxf_slip_line_tap_init(ctypes.byref(self.fill))
        near, self.far = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.far.setblocking(False)
        assert lib.rxf_slip_line_tap_adopt(ctypes.byref(self.fill), near.detach()) == 0

    def close(self) -> None:
        self.lib.rxf_slip_line_tap_deinit(ctypes.byref(self.fill))
        self.far.close()

    def pump(self, face: Face) -> int:
        moved = self.lib.rxf_slip_line_tap_pump(face.address, ctypes.byref(self.fill))
        assert moved >= 0, "the bridge's I/O failed"
        return moved

    def peer_send(self, packet: bytes) -> None:
        self.far.send(packet)

    def peer_recv(self) -> list[bytes]:
        packets: list[bytes] = []
        while True:
            try:
                packets.append(self.far.recv(4096))
            except BlockingIOError:
                return packets


class PacketContract:
    """The contract suite of a packet-device fill (tap, utun): the same line cell and face as
    ``LineContract``, the device side judged by packets. What the guest sends as RFC 1055 frames
    arrives at the device as the packets inside them; what the device delivers reaches the guest
    as the frame the RFC 1055 model (``pymergetic.rxftk.net.link.slip.encode``) makes of it."""

    def __init__(self, face: Face, seat: TapSeat) -> None:
        self.face, self.seat = face, seat

    def send_frame(self, payload: bytes) -> None:
        for byte in slip.encode(payload):
            self.face.send(byte)

    def run(self) -> None:
        face, seat = self.face, self.seat
        assert face.open(0) == face.address
        assert face.counters[:2] == (1, MTU)

        # TX: whole packets, in order; END and ESC inside a packet survive the stuffing
        packets = [
            bytes([0x45]) + bytes(19),
            bytes([END, ESC, ESC_END, ESC_ESC, 0x00, END, ESC] * 5),
            bytes(range(256)) * 3 + bytes(232),  # 1000 bytes: exactly the MTU
            b"\x01",
        ]
        for packet in packets:
            self.send_frame(packet)
        face.send(END)  # an empty frame (a doubled END) is a flush, not a packet
        assert seat.pump(face) == len(packets)
        assert seat.peer_recv() == packets
        assert face.tx_waiting == 0 and face.counters[2:] == (0, 0)

        # TX over the MTU: dropped whole, counted once, and the line goes on
        self.send_frame(bytes(MTU + 1))
        seat.pump(face)
        assert seat.peer_recv() == [] and face.counters[2] == 1
        self.send_frame(b"after")
        seat.pump(face)
        assert seat.peer_recv() == [b"after"]

        # RX: every device packet is one RFC 1055 frame, byte for byte the model's
        for packet in packets[:3]:
            seat.peer_send(packet)
            seat.pump(face)
            assert face.tryread(RING) == slip.encode(packet)
        assert face.ready() == 0

        # RX backpressure: a full ring loses no packet; the rest wait for room
        burst = [bytes([index]) * 900 for index in range(12)]
        for packet in burst:
            seat.peer_send(packet)
        received = b""
        for _ in range(40):
            seat.pump(face)
            received += face.tryread(RING)
            if len(received) >= sum(len(slip.encode(packet)) for packet in burst):
                break
        assert received == b"".join(slip.encode(packet) for packet in burst)

        # RX over the MTU: never enters the line, counted
        before = face.counters[2]
        seat.peer_send(bytes(MTU + 1))
        seat.pump(face)
        assert face.ready() == 0 and face.counters[2] == before + 1

        face.close()
        assert face.counters[0] == 0


@pytest.fixture(scope="module")
def tap_host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(
        tmp_path_factory.mktemp("lwip_tap"),
        CORPUS / "slip_line.c",
        FACE_BODIES,
        natives=(LINE / "slip_line_tap.c",),
    )


def test_the_tap_fill_meets_the_packet_contract_on_the_imported_face(tap_host: Host):
    seat = TapSeat(tap_host.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            PacketContract(ImportedFace(tap_host, memory), seat).run()
    finally:
        seat.close()


def test_the_tap_fill_meets_the_packet_contract_on_the_native_reference(tap_host: Host):
    seat = TapSeat(tap_host.native)
    try:
        PacketContract(NativeFace(tap_host.native), seat).run()
    finally:
        seat.close()


def test_the_tun_device_open_needs_net_admin(tap_host: Host):
    """The one thing the stand-in cannot prove: TUNSETIFF. Without CAP_NET_ADMIN it is EPERM and
    the test says so (an xfail, named); with it, the device opens and closes."""
    lib = ctypes.CDLL(tap_host.native._name, use_errno=True)
    fill = TapFill()
    lib.rxf_slip_line_tap_init(ctypes.byref(fill))
    lib.rxf_slip_line_tap_open.argtypes = [ctypes.POINTER(TapFill), ctypes.c_char_p]
    lib.rxf_slip_line_tap_init.argtypes = [ctypes.POINTER(TapFill)]
    lib.rxf_slip_line_tap_deinit.argtypes = [ctypes.POINTER(TapFill)]
    if lib.rxf_slip_line_tap_open(ctypes.byref(fill), b"rxfslip0") == 0:
        lib.rxf_slip_line_tap_deinit(ctypes.byref(fill))
        return
    code = ctypes.get_errno()
    assert code in (errno.EPERM, errno.EACCES, errno.ENOENT), os.strerror(code)
    pytest.xfail(
        f"the tun device cannot be opened here ({os.strerror(code)}): the open is unproven"
    )


# ---- the seats whose fill is not written: named, failing (all-platforms: no dark ports) ----------

UNPROVEN_SEATS = {
    "browser (wasm32 FIFO block <-> WebSocket)": (
        "slip_line_js.c",
        (
            "needs the wasm32 FIFO block bridged to a WebSocket gateway and a browser harness, "
            "and the card is not in the wasm32 seat's image"
        ),
    ),
    "firmware (UART 16550 / PL011)": (
        "slip_line_uart.c",
        "needs QEMU firmware images that carry the card (they do not) and a UART loopback harness",
    ),
}


@pytest.mark.parametrize(
    ("seat", "file", "why"),
    [
        pytest.param(
            seat,
            file,
            why,
            marks=pytest.mark.xfail(strict=True, reason=f"{file}: {why}"),
        )
        for seat, (file, why) in UNPROVEN_SEATS.items()
    ],
)
def test_the_seat_has_a_fill_that_meets_the_contract(seat: str, file: str, why: str):
    """A stub that fails its prove: the fill file does not exist, so the seat is not claimed. When
    it is written this xpasses and the strict xfail forces the contract suite onto it."""
    assert (LINE / file).is_file(), f"{seat}: {file} ({why})"


# ---- the real UDP echo over the line (tests/lwip_udp_echo.py) -----------------------------------

# the interpreter with room the echo path needs (see lwip_udp_echo.echo_path_budget): the 8-link,
# 8 KiB budget of native/graph.h is short of lwIP's call chain; this is the smallest round
# numbers over what the chain measures (15072 bytes of frames at depth 9 on the C path)
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
    assert (frames, links) == (15072, 9), "the frames of the echo path, measured"
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
        "15072 bytes at depth 9"
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
    compiled natively puts there, scenario by scenario -- all eight, the ICMP port-unreachable
    included (its destination used to be 0.0.0.0: a pointer to the caller's local, read by a call
    card, was no address the callee could name; the local rides in a cell now, docs/35). The
    replies are also read by the peer: right addresses, ports, payloads, checksums."""
    answer = echo_with_room
    assert answer["status"] == 0, f"the imported run was refused ({answer['status']})"
    scenarios = lwip_udp_echo.scenarios()
    assert list(answer["same"]) == [scenario.name for scenario in scenarios]
    for scenario in scenarios:
        lwip_udp_echo.check_reply(
            scenario,
            lwip_udp_echo.decode(bytes.fromhex(answer["replies"][scenario.name])),
        )
        assert answer["same"][scenario.name], f"{scenario.name}: the reply bytes differ"
    imported, reference = answer["counters"]
    assert imported == reference and imported[2:] == [0, 0]


@pytest.mark.slow
def test_no_scenario_differs_from_native(echo_with_room: dict):
    """The scenario that used to differ (the ICMP port-unreachable, whose destination was
    0.0.0.0) is byte-equal to native now: no scenario of the echo differs."""
    assert "udp to a closed port" in echo_with_room["same"]
    assert [name for name, same in echo_with_room["same"].items() if not same] == []
