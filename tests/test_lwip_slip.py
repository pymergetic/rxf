"""The SLIP line of the lwIP card (docs/23 4.4, docs/25 13): the face, the seat fills, and the
contract every seat's fill must meet.

The line is a byte line. The face (``native/lwip/slip_line_face.c``) is imported with the card;
a seat's fill (``native/lwip/slip_line_*.c``) is native C that moves bytes between the line cell
and the seat's real line -- a socketpair (host), a tun device (unix tap), a FIFO block (browser,
wasm32), a 16550/PL011 UART (firmware). ``LineContract`` is the one suite every seat runs:
open/close, TX bytes in order, TX overrun counted and never short-written, RX bytes in order,
RX backpressure (a full ring loses nothing), the readiness word, the ``framing`` and ``overrun``
counters as words of the cell, and the MTU word. ``ParkWakeContract`` runs the RX readiness
park/wake (docs/25 4) on every seat: a sleeping consumer wakes on an arriving frame and no
frame is lost across the wake. Every seat's fill is written and meets both contracts; the
seats whose image does not carry the card yet name that wall in
``tests/test_lwip_echo_seats.py`` (a strict xfail per seat, never a skipped seat).
"""

from __future__ import annotations

import ctypes
import errno
import os
import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import graph_header
import lwip_import
import pytest

from pymergetic.rxftk.net.link import slip

from . import lwip_udp_echo, rxf_native
from .lwip_slip_fills import (
    JsSeat,
    UartFill,
    UartSeat,
    UART_NS16550_MMIO,
    UART_NS16550_PORT,
    UART_PL011_MMIO,
)
from .test_lwip_import import CORPUS, OK, Host

ROOT = Path(__file__).resolve().parents[1]
LINE = ROOT / "native" / "lwip"
PARK_POSIX = LINE / "slip_line_park_posix.c"
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

# the Uart capability's device word (native/graph.h): the kind in the low byte, the register
# shift at bit 8, RXF_UART_WIDE for a 32-bit register
UART_NS16550_MMIO = 2
UART_PL011_MMIO = 3
UART_NS16550_PORT = 4
UART_WIDE = 4096


def uart_device(kind: int, shift: int = 0, wide: bool = False) -> int:
    return kind | (shift << 8) | (UART_WIDE if wide else 0)


# offsets in the line cell (native/lwip/slip_line.h): six words, then the TX ring, then the RX
COUNTERS = struct.Struct("<4I")  # open, mtu, overrun, framing
PARK_WORDS = struct.Struct("<2I")  # waiting, wakes
TX_AT = COUNTERS.size + PARK_WORDS.size
RX_AT = TX_AT + 8 + RING


def cell_words(cell, at: int) -> tuple[int, int]:
    return struct.unpack_from("<2I", cell, at)


def bind_park(lib: ctypes.CDLL) -> None:
    """The seat's park/wake pair over the library (slip_line_fill.h's two names)."""
    lib.rxf_slip_line_wait.restype = ctypes.c_uint32
    lib.rxf_slip_line_wait.argtypes = [ctypes.c_void_p]
    lib.rxf_slip_line_notify.argtypes = [ctypes.c_void_p]


class Seat(Protocol):
    """What every seat's harness offers the contracts: the fill's far side, the peer."""

    def deliver(self, packet: bytes) -> None: ...

    def pump(self, face: Face) -> int: ...

    def close(self) -> None: ...


class WireSeat(Protocol):
    """What a stream seat's harness offers ``LineContract``: the SLIP byte stream of the far
    side (the host's socketpair, the browser's FIFO block, the firmware's UART registers)."""

    def wire_send(self, data: bytes) -> int: ...

    def wire_recv(self) -> bytes: ...

    def pump(self, face: Face) -> int: ...

    def pump_all(self, face: Face) -> int: ...

    def close(self) -> None: ...


class Face:
    """The face of the line as one call surface: imported graphs or the natively compiled C."""

    def __init__(self, cell) -> None:
        self.cell = cell
        self.address = ctypes.addressof(ctypes.c_uint8.from_buffer(cell))

    @property
    def counters(self) -> tuple[int, int, int, int]:
        return COUNTERS.unpack_from(self.cell, 0)

    @property
    def park(self) -> tuple[int, int]:
        """the cell's park/wake words: (waiting, wakes)"""
        return PARK_WORDS.unpack_from(self.cell, COUNTERS.size)

    @property
    def tx_waiting(self) -> int:
        head, tail = cell_words(self.cell, TX_AT)
        return (head - tail) & 0xFFFFFFFF

    @property
    def rx_waiting(self) -> int:
        """The RX ring's waiting bytes, read off the cell (never a program run: the parked
        consumer's thread owns the runs)."""
        head, tail = cell_words(self.cell, RX_AT)
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
        size = COUNTERS.size + PARK_WORDS.size + 2 * (8 + RING)
        symbol = ctypes.c_uint8.in_dll(lib, "rxf_slip_line")
        super().__init__((ctypes.c_uint8 * size).from_address(ctypes.addressof(symbol)))
        self.address = ctypes.addressof(symbol)
        lib.sio_open.restype = ctypes.c_void_p
        lib.sio_open.argtypes = [ctypes.c_uint8]
        lib.sio_send.argtypes = [ctypes.c_uint8, ctypes.c_void_p]
        lib.sio_tryread.restype = ctypes.c_uint32
        lib.sio_tryread.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
        lib.rxf_slip_line_ready.restype = ctypes.c_uint32
        bind_park(lib)

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

    def pump_all(self, face: Face) -> int:
        moved = 0
        while True:
            step = self.pump(face)
            moved += step
            if step == 0:
                return moved

    def wire_send(self, data: bytes) -> int:
        """Bytes the peer manages to put on the line (a full socket buffer takes a part)."""
        try:
            return os.write(self.fill.far_fd, data)
        except BlockingIOError:
            return 0

    def wire_recv(self) -> bytes:
        out = b""
        while True:
            try:
                got = os.read(self.fill.far_fd, 65536)
            except BlockingIOError:
                return out
            if not got:
                return out
            out += got

    def deliver(self, packet: bytes) -> None:
        """One IP packet onto the wire as one RFC 1055 frame (the peer speaks SLIP)."""
        line = slip.encode(packet)
        assert self.wire_send(line) == len(line)

    def replies(self) -> tuple[bytes, ...]:
        """The reply packets the peer deframed from what the line delivered."""
        return slip.SlipDecoder().feed(self.wire_recv())


def frame(payload: bytes) -> bytes:
    """RFC 1055: END, the stuffed payload, END."""
    body = payload.replace(b"\xdb", b"\xdb\xdd").replace(b"\xc0", b"\xdb\xdc")
    return b"\xc0" + body + b"\xc0"


class LineContract:
    """The contract suite of the SLIP line: the same assertions on every seat and every face."""

    def __init__(self, face: Face, seat: WireSeat) -> None:
        self.face, self.seat = face, seat

    def send_wire(self, data: bytes) -> None:
        """All of ``data`` onto the wire, whatever the transport takes per call."""
        queued = 0
        while queued < len(data):
            wrote = self.seat.wire_send(data[queued:])
            assert wrote > 0, "the wire takes nothing"
            queued += wrote

    def run(self) -> None:
        face, seat = self.face, self.seat
        face.close()  # a cell outlives one run of the suite: start from a closed line
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
        assert seat.pump_all(face) == 256
        assert seat.wire_recv() == sent
        assert face.tx_waiting == 0

        # TX overrun: a full ring drops the rest and counts every dropped byte
        before = face.counters[2]
        for index in range(RING + 10):
            face.send(index & 0xFF)
        assert face.tx_waiting == RING
        assert face.counters[2] == before + 10, "ten bytes dropped, ten counted"
        seat.pump_all(face)
        assert seat.wire_recv() == bytes(index & 0xFF for index in range(RING))

        # RX: bytes arrive in order, and the readiness word says how many wait
        assert face.ready() == 0 and face.tryread(8) == b""
        data = bytes((index * 7 + 3) & 0xFF for index in range(3000))
        self.send_wire(data)
        seat.pump_all(face)
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
        self.send_wire(big)
        seat.pump_all(face)
        assert face.ready() == RING, "the ring holds what it can"
        received = face.tryread(RING)
        seat.pump_all(face)
        assert face.ready() == 6000 - RING
        received += face.tryread(RING)
        assert received == big, "not one byte lost across the full ring"

        # framing: an escape followed by a byte that is neither ESC_END nor ESC_ESC
        base = face.counters[3]
        self.send_wire(bytes([END, ESC, ESC_END, ESC, ESC_ESC, END]))
        seat.pump_all(face)
        face.tryread(16)
        assert face.counters[3] == base, "well-formed escapes are not errors"
        self.send_wire(bytes([END, ESC, 0x41, END]))
        seat.pump_all(face)
        face.tryread(16)
        assert face.counters[3] == base + 1, (
            "ESC then 0x41 is a framing error, counted once"
        )

        # MTU: exactly the MTU is fine, one more is an overrun, counted once per frame
        base = face.counters[2]
        self.send_wire(frame(b"\x41" * MTU))
        seat.pump_all(face)
        face.tryread(RING)
        assert face.counters[2] == base
        self.send_wire(frame(b"\x41" * (MTU + 1)))
        seat.pump_all(face)
        face.tryread(RING)
        assert face.counters[2] == base + 1
        # a stuffed frame is counted by its decoded length, not its wire length
        self.send_wire(frame(b"\xc0" * (MTU // 2 + 1)))
        seat.pump_all(face)
        face.tryread(RING)
        assert face.counters[2] == base + 1, "stuffing is not payload"

        # close: the line is shut and takes no byte again
        face.close()
        assert face.counters[0] == 0
        face.send(0x42)
        assert face.tx_waiting == 0


class ParkWakeContract:
    """The RX readiness park/wake of the line (docs/25 4): a sleeping consumer is woken by an
    arriving frame and no frame is lost across the wake. The same assertions on every seat.

    The consumer is a thread that sleeps in the seat's park (``rxf_slip_line_wait``) and never
    polls; what wakes it is the fill's publish of the arriving frame (the seat's
    ``rxf_slip_line_notify``), and the readiness word is the condition it answers (a wake is a
    hint, the word is the data -- docs/20 S4). What the wakes carry to the consumer is the
    frames of the packets the peer sent, byte for byte and in order: a frame may reach the ring
    as two publishes (the ring's contiguous runs end where it wraps) and then wakes twice, which
    is why the contract counts wakes per publish and bytes per frame, never one wake per
    frame."""

    PACKETS = (b"park wake", bytes(range(200)), b"\xc0\xdb" * 40)

    def __init__(self, face: Face, seat: Seat, lib: ctypes.CDLL) -> None:
        self.face, self.seat, self.lib = face, seat, lib
        bind_park(lib)

    def run(self) -> None:
        face, seat, lib = self.face, self.seat, self.lib
        answered: list[int] = []
        chunks: list[bytes] = []

        def consume() -> None:
            while True:
                ready = lib.rxf_slip_line_wait(face.address)  # the park
                if ready == 0:  # the line is closed: the park ends
                    return
                answered.append(ready)
                chunks.append(face.tryread(ready))

        assert face.open(0) == face.address
        _waiting, wakes_before = face.park  # ``wakes`` counts for the cell's life, not the run
        consumer = threading.Thread(target=consume)
        consumer.start()
        frames = [slip.encode(packet) for packet in self.PACKETS]
        try:
            for count, packet in enumerate(self.PACKETS, start=1):
                self._until(lambda: face.park[0] == 1, "the consumer is parked")
                assert face.rx_waiting == 0, "the consumer sleeps with nothing waiting"
                seat.deliver(packet)
                seat.pump(face)  # the publish is the wake
                self._until(
                    lambda count=count: sum(map(len, chunks)) == sum(map(len, frames[:count])),
                    "the wake reaches the consumer",
                )
        finally:
            face.close()  # the park ends with the line ...
            lib.rxf_slip_line_notify(face.address)  # ... and the sleeper is woken to see it
            consumer.join(timeout=10)
            assert not consumer.is_alive(), "the consumer never woke"
        assert b"".join(chunks) == b"".join(frames), "not one byte lost across the wake"
        assert sum(answered) == sum(map(len, frames)), (
            "every park ended on the readiness word, at the bytes that arrived"
        )
        waiting, wakes = face.park
        assert waiting == 0, "the park token is down when the run ends"
        assert wakes - wakes_before >= len(self.PACKETS), (
            f"every arriving frame woke the sleeper (wakes={wakes - wakes_before}): one wake "
            "per publish, and a frame that crosses the ring's end is two publishes (docs/20 "
            "S4: a wake is a hint, the readiness word is the condition)"
        )

    @staticmethod
    def _until(condition: Callable[[], bool], what: str) -> None:
        deadline = time.monotonic() + 10
        while not condition():
            assert time.monotonic() < deadline, f"timeout: {what}"
            time.sleep(0.001)


@pytest.fixture(scope="module")
def line_host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(
        tmp_path_factory.mktemp("lwip_line"),
        CORPUS / "slip_line.c",
        FACE_BODIES,
        natives=(LINE / "slip_line_host.c", PARK_POSIX),
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


def test_the_host_seat_park_wakes_on_an_arriving_frame_on_the_imported_face(line_host: Host):
    seat = HostSeat(line_host.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            ParkWakeContract(ImportedFace(line_host, memory), seat, line_host.native).run()
    finally:
        seat.close()


def test_the_host_seat_park_wakes_on_an_arriving_frame_on_the_native_reference(line_host: Host):
    seat = HostSeat(line_host.native)
    try:
        ParkWakeContract(NativeFace(line_host.native), seat, line_host.native).run()
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

    def pump_all(self, face: Face) -> int:
        moved = 0
        while True:
            step = self.pump(face)
            moved += step
            if step == 0:
                return moved

    def peer_send(self, packet: bytes) -> int:
        return self.far.send(packet)

    def peer_recv(self) -> list[bytes]:
        packets: list[bytes] = []
        while True:
            try:
                packets.append(self.far.recv(4096))
            except BlockingIOError:
                return packets

    def deliver(self, packet: bytes) -> None:
        """One IP packet onto the wire: the tap's device side speaks packets, and the fill
        frames what it is given for the guest's SLIP deframer."""
        self.peer_send(packet)

    def replies(self) -> tuple[bytes, ...]:
        """The reply packets the device side received."""
        return tuple(self.peer_recv())


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
        assert seat.replies() == tuple(packets)
        assert face.tx_waiting == 0 and face.counters[2:] == (0, 0)

        # TX over the MTU: dropped whole, counted once, and the line goes on
        self.send_frame(bytes(MTU + 1))
        seat.pump(face)
        assert seat.replies() == () and face.counters[2] == 1
        self.send_frame(b"after")
        seat.pump(face)
        assert seat.replies() == (b"after",)

        # RX: every device packet is one RFC 1055 frame, byte for byte the model's
        for packet in packets[:3]:
            seat.deliver(packet)
            seat.pump_all(face)
            assert face.tryread(RING) == slip.encode(packet)
        assert face.ready() == 0

        # RX backpressure: a full ring loses no packet; the rest wait for room
        burst = [bytes([index]) * 900 for index in range(12)]
        for packet in burst:
            seat.deliver(packet)
        received = b""
        for _ in range(40):
            seat.pump(face)
            received += face.tryread(RING)
            if len(received) >= sum(len(slip.encode(packet)) for packet in burst):
                break
        assert received == b"".join(slip.encode(packet) for packet in burst)

        # RX over the MTU: never enters the line, counted
        before = face.counters[2]
        seat.deliver(bytes(MTU + 1))
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
        natives=(LINE / "slip_line_tap.c", PARK_POSIX),
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


def test_the_tap_fill_park_wakes_on_an_arriving_frame_on_the_imported_face(tap_host: Host):
    seat = TapSeat(tap_host.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            ParkWakeContract(ImportedFace(tap_host, memory), seat, tap_host.native).run()
    finally:
        seat.close()


def test_the_tap_fill_park_wakes_on_an_arriving_frame_on_the_native_reference(tap_host: Host):
    seat = TapSeat(tap_host.native)
    try:
        ParkWakeContract(NativeFace(tap_host.native), seat, tap_host.native).run()
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


# ---- the browser and firmware fills: the FIFO block and the UART registers (slip_line_*.c) ------


@pytest.fixture(scope="module")
def line_js(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(
        tmp_path_factory.mktemp("lwip_line"),
        CORPUS / "slip_line.c",
        FACE_BODIES,
        natives=(LINE / "slip_line_js.c", PARK_POSIX),
    )


def test_the_browser_seat_fill_meets_the_contract_on_the_imported_face(line_js: Host):
    seat = JsSeat(line_js.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            LineContract(ImportedFace(line_js, memory), seat).run()
    finally:
        seat.close()


def test_the_browser_seat_fill_meets_the_contract_on_the_native_reference(line_js: Host):
    seat = JsSeat(line_js.native)
    try:
        LineContract(NativeFace(line_js.native), seat).run()
    finally:
        seat.close()


def test_the_browser_seat_park_wakes_on_an_arriving_frame_on_the_imported_face(line_js: Host):
    seat = JsSeat(line_js.native)
    try:
        with rxf_native.Memory(1 << 16) as memory:
            ParkWakeContract(ImportedFace(line_js, memory), seat, line_js.native).run()
    finally:
        seat.close()


def test_the_browser_seat_park_wakes_on_an_arriving_frame_on_the_native_reference(line_js: Host):
    seat = JsSeat(line_js.native)
    try:
        ParkWakeContract(NativeFace(line_js.native), seat, line_js.native).run()
    finally:
        seat.close()


@pytest.fixture(scope="module")
def line_uart(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(
        tmp_path_factory.mktemp("lwip_line"),
        CORPUS / "slip_line.c",
        FACE_BODIES,
        natives=(LINE / "slip_line_uart.c", PARK_POSIX),
    )


UART_KINDS = (
    ("16550 (MMIO)", UART_NS16550_MMIO),
    ("16550 (I/O ports)", UART_NS16550_PORT),
    ("PL011 (MMIO)", UART_PL011_MMIO),
)


@pytest.mark.parametrize(("name", "kind"), UART_KINDS)
def test_the_firmware_seat_fill_meets_the_contract_on_the_native_reference(
    line_uart: Host, name: str, kind: int
):
    seat = UartSeat(line_uart.native, kind, f"firmware ({name})")
    try:
        LineContract(NativeFace(line_uart.native), seat).run()
    finally:
        seat.close()


@pytest.mark.parametrize(("name", "kind"), UART_KINDS)
def test_the_firmware_seat_park_wakes_on_an_arriving_frame_on_the_native_reference(
    line_uart: Host, name: str, kind: int
):
    seat = UartSeat(line_uart.native, kind, f"firmware ({name})")
    try:
        ParkWakeContract(NativeFace(line_uart.native), seat, line_uart.native).run()
    finally:
        seat.close()


def test_the_firmware_seat_fill_meets_the_contract_on_the_imported_face(line_uart: Host):
    seat = UartSeat(line_uart.native, UART_NS16550_MMIO, "firmware (16550 MMIO)")
    try:
        with rxf_native.Memory(1 << 16) as memory:
            LineContract(ImportedFace(line_uart, memory), seat).run()
    finally:
        seat.close()


def test_the_firmware_seat_park_wakes_on_an_arriving_frame_on_the_imported_face(
    line_uart: Host,
):
    seat = UartSeat(line_uart.native, UART_NS16550_MMIO, "firmware (16550 MMIO)")
    try:
        with rxf_native.Memory(1 << 16) as memory:
            ParkWakeContract(ImportedFace(line_uart, memory), seat, line_uart.native).run()
    finally:
        seat.close()


def test_the_firmware_fill_refuses_a_device_word_it_cannot_claim(line_uart: Host):
    """DEVICE_MACHINE is the seat's to resolve; the fill answers EINVAL, never claims blind."""
    lib = ctypes.CDLL(line_uart.native._name, use_errno=True)
    fill = UartFill()
    lib.rxf_slip_line_uart_init.restype = ctypes.c_int
    lib.rxf_slip_line_uart_init.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.rxf_slip_line_uart_deinit.argtypes = [ctypes.c_void_p]
    assert (
        lib.rxf_slip_line_uart_init(ctypes.byref(fill), 0, 0, None, None, None) != 0
    ), "device word 0 (DEVICE_MACHINE unresolved) must be refused"
    assert ctypes.get_errno() == errno.EINVAL, "the refusal is EINVAL, as the fill says"



# ---- the real UDP echo over the line (tests/lwip_udp_echo.py) -----------------------------------

# the budget the shipped interpreter declares (native/graph.h, read from the header the machine
# compiles with): the echo path measures 15072 bytes of frames at depth 9 (lwip_udp_echo.
# echo_path_budget) and fits the 32 KiB cell / 16 links the budget carries (32768/16, decided
# 2026-11-01 for margin over the measured path). The experiment of an interpreter with
# room is gone (enlarge_interpreter was its spec and is deleted with it): the shipped
# interpreter is the seat.
SHIPPED_CELL = graph_header.numbers()["RXF_GRAPH_CELL_BYTES"]
SHIPPED_DEPTH = graph_header.numbers()["RXF_GRAPH_CALL_DEPTH"]


@pytest.fixture
def echo_host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    """One host per test: the native lwIP of the shared object keeps its statics (the netif
    list among them) and they point into the memory of the run that made them, so two runs of
    one host walk the first run's stack. A host of its own is a shared object of its own."""
    return lwip_udp_echo.build_host(tmp_path_factory.mktemp("lwip_udp_echo"))


def assert_same(
    imported: lwip_udp_echo.Outcome, reference: lwip_udp_echo.Outcome
) -> None:
    assert imported.status == 0, f"the imported run was refused ({imported.status})"
    for scenario in lwip_udp_echo.scenarios():
        assert imported.replies[scenario.name] == reference.replies[scenario.name], (
            f"{scenario.name}: the imported graphs put "
            f"{[packet.hex() for packet in imported.replies[scenario.name]]} on the line, "
            f"natively it is {[packet.hex() for packet in reference.replies[scenario.name]]}"
        )
        lwip_udp_echo.check_reply(scenario, reference.replies[scenario.name])
    assert imported.counters == reference.counters
    assert imported.counters[2:] == (0, 0), (
        "no overrun and no framing error on a clean line"
    )


@pytest.mark.slow
def test_the_echo_path_measures_15072_bytes_at_9_links_and_fits_the_budget(echo_host: Host):
    """The measured machine need: the chain from the harness entry down the real path is 15072
    bytes of frames at depth 9 on the C path (the exact numbers, pinned), and the shipped
    interpreter's budget carries it with margin (32 KiB / 16 links, native/graph.h, the
    32768/16 decision of 2026-11-01). What
    does NOT fit is proven beside it by the budget cases of tests/graph_programs.py (the walls
    are the refusals, never a wrong answer)."""
    frames, links = lwip_udp_echo.echo_path_budget(echo_host)
    assert (frames, links) == (15072, 9), "the frames of the echo path, measured"
    assert links <= SHIPPED_DEPTH, "the path fits the depth budget"
    assert frames <= SHIPPED_CELL, "the frames of the echo path fit the run cell"
    imported, _reference = lwip_udp_echo.run_both(echo_host)
    assert imported.status == 0, (
        f"the shipped interpreter answered {imported.status}: the budget moved again"
    )


@pytest.mark.slow
def test_the_udp_echo_matches_native_on_the_shipped_interpreter(echo_host: Host):
    """The real UDP echo: SLIP-framed IPv4/UDP and ICMP datagrams in over the line, the imported
    graphs of the card answer, the reply bytes out over the line equal what the same lwIP C
    compiled natively puts there, scenario by scenario -- all eight, the ICMP port-unreachable
    included (its destination used to be 0.0.0.0: a pointer to the caller's local, read by a call
    card, was no address the callee could name; the local rides in a cell now, docs/35). The
    replies are also read by the peer: right addresses, ports, payloads, checksums. This is the
    shipped interpreter, the seat every run takes (the experiment of an interpreter with room
    ended when the budget grew: docs/35's machine need)."""
    imported, reference = lwip_udp_echo.run_both(echo_host)
    assert_same(imported, reference)


@pytest.mark.slow
def test_the_parked_echo_run_wakes_on_the_rx_word_and_replies_without_polling(echo_host: Host):
    """Q3's done-when: a parked echo run. The driver of the imported side sleeps in
    ``rxf_slip_line_wait`` and runs the stack only when the arriving frame's publish wakes it --
    no poll anywhere on that side -- and the replies still equal the native reference byte for
    byte, scenario by scenario. The stack runs are counted: one per publish of an arriving
    frame (two when a frame crosses the ring's end), never a poll
    (``lwip_udp_echo.run_parked``)."""
    imported, reference = lwip_udp_echo.run_parked(echo_host)
    assert_same(imported, reference)
