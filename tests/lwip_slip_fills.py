"""The seat harnesses of the SLIP line fills whose far side is no file descriptor
(native/lwip/slip_line_js.c, native/lwip/slip_line_uart.c): the far side is a device model the
test drives.

Every harness offers the contracts of tests/test_lwip_slip.py the same face: ``deliver`` and
``replies`` speak IP packets (the peer's view), ``wire_send`` and ``wire_recv`` the SLIP byte
stream the fill moves, and ``pump``/``pump_all`` run the fill. The models are register level,
the seat's own (native/graph.h's device words), so the prove hits the fill exactly as the seat
would."""

from __future__ import annotations

import ctypes
from typing import TYPE_CHECKING

from pymergetic.rxftk.net.link import slip

if TYPE_CHECKING:
    from .test_lwip_slip import Face

# the device words of the Uart capability (native/graph.h)
UART_DEVICE_MACHINE = 1
UART_NS16550_MMIO = 2
UART_PL011_MMIO = 3
UART_NS16550_PORT = 4
UART_SHIFT_AT = 8
UART_WIDE = 4096

# the FIFO block of a wasm32 seat (native/graph.h): four words (rx_head, rx_tail, tx_head,
# tx_tail), then the two 256-byte FIFOs
FIFO_BYTES = 256
FIFO_RX = 64
FIFO_TX = 320
FIFO_BLOCK = 576

# the 16550 registers and the bits the fill reads (native/lwip/slip_line_uart.c)
UART_RX_TX = 0
UART_LCR = 3
UART_LSR = 5
UART_LSR_DR = 0x01
UART_LSR_THRE = 0x20

# the PL011 registers (byte offset = register << shift)
PL011_DR = 0
PL011_FR = 6
PL011_FR_RXFE = 0x10


def uart_device(kind: int, shift: int = 0, wide: bool = False) -> int:
    """The generation word of the Uart capability: kind, register shift, register width."""
    return kind | (shift << UART_SHIFT_AT) | (UART_WIDE if wide else 0)


class JsFifo(ctypes.Structure):
    """The wasm32 FIFO block (native/graph.h) as the fill sees it."""

    _fields_ = (("bytes", ctypes.c_uint8 * FIFO_BLOCK),)

    def word(self, index: int) -> int:
        return int.from_bytes(bytes(self.bytes[index * 4 : index * 4 + 4]), "little")

    def set_word(self, index: int, value: int) -> None:
        self.bytes[index * 4 : index * 4 + 4] = value.to_bytes(4, "little")

    @property
    def rx_waiting(self) -> int:
        """The host -> image FIFO's waiting bytes (rx_head is the host's)."""
        return (self.word(0) - self.word(1)) & 0xFFFFFFFF

    @property
    def tx_waiting(self) -> int:
        """The image -> host FIFO's waiting bytes (tx_head is the image's)."""
        return (self.word(2) - self.word(3)) & 0xFFFFFFFF

    def rx_push(self, data: bytes) -> int:
        """The host writes what the FIFO has room for; answers the bytes it took."""
        room = FIFO_BYTES - self.rx_waiting
        data = data[:room]
        head = self.word(0)
        for index, byte in enumerate(data):
            self.bytes[FIFO_RX + ((head + index) & (FIFO_BYTES - 1))] = byte
        self.set_word(0, (head + len(data)) & 0xFFFFFFFF)
        return len(data)

    def tx_take(self) -> bytes:
        """The host drains what the image published."""
        tail, waiting = self.word(3), self.tx_waiting
        data = bytes(
            self.bytes[FIFO_TX + ((tail + index) & (FIFO_BYTES - 1))]
            for index in range(waiting)
        )
        self.set_word(3, (tail + waiting) & 0xFFFFFFFF)
        return data


class JsFill(ctypes.Structure):
    """struct rxf_slip_line_js of native/lwip/slip_line_js.h."""

    _fields_ = (
        ("words", ctypes.c_void_p),
        ("rx", ctypes.c_void_p),
        ("tx", ctypes.c_void_p),
        ("escaped", ctypes.c_uint32),
        ("frame", ctypes.c_uint32),
    )


class JsSeat:
    """The browser seat's fill over a FIFO block, with the host half of the block played here:
    the far side of the block is the WebSocket (docs/23 decision 8) carrying the SLIP stream,
    so the wire speaks the stream and ``deliver`` frames a packet onto it like any peer. What
    the block has no room for waits in the host's backlog -- the backpressure of the seat."""

    name = "browser (wasm32 FIFO block)"

    def __init__(self, lib: ctypes.CDLL) -> None:
        self.lib = lib
        lib.rxf_slip_line_js_init.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.rxf_slip_line_js_deinit.argtypes = [ctypes.c_void_p]
        lib.rxf_slip_line_js_pump.restype = ctypes.c_int64
        lib.rxf_slip_line_js_pump.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.block = JsFifo()
        self.fill = JsFill()
        lib.rxf_slip_line_js_init(ctypes.byref(self.fill), ctypes.byref(self.block))
        self.backlog = b""  # what the host has for the block but its FIFO has no room for
        self.wire = b""  # what the host read off the block (the WebSocket's stream)

    def close(self) -> None:
        self.lib.rxf_slip_line_js_deinit(ctypes.byref(self.fill))

    def wire_send(self, data: bytes) -> int:
        self.backlog += data
        return len(data)

    def wire_recv(self) -> bytes:
        out, self.wire = self.wire, b""
        return out

    def deliver(self, packet: bytes) -> None:
        self.wire_send(slip.encode(packet))

    def replies(self) -> tuple[bytes, ...]:
        return slip.SlipDecoder().feed(self.wire_recv())

    def pump(self, face: Face) -> int:
        self.wire += self.block.tx_take()  # the host drains the block before the fill runs
        self.backlog = self.backlog[self.block.rx_push(self.backlog) :]
        moved = self.lib.rxf_slip_line_js_pump(face.address, ctypes.byref(self.fill))
        assert moved >= 0, "the fill's I/O failed"
        self.wire += self.block.tx_take()
        return int(moved)

    def pump_all(self, face: Face) -> int:
        moved = 0
        while True:
            step = self.pump(face)
            moved += step
            if step == 0:
                return moved


class UartDevice:
    """A 16550 or a PL011 at register level, driven by the fill's register calls. What waits in
    the receive register is what the wire sent; what the fill writes to the transmit register is
    what the wire hears. The line is always ready (the device is a prove, not a UART with a
    baud rate to wait on)."""

    def __init__(self, kind: int) -> None:
        self.kind = kind
        self.rx: list[int] = []  # what the wire sent, waiting for the fill
        self.tx = bytearray()  # what the fill sent to the wire
        self.registers = bytearray(32)  # the bring-up the fill writes is seen and answered

    def read(self, _ctx: object, at: int, _wide: int) -> int:
        register = at  # the harness gives the fill base 0 and shift 0
        if self.kind == UART_PL011_MMIO:
            if register == PL011_DR:
                return self.rx.pop(0) if self.rx else 0
            if register == PL011_FR:
                return PL011_FR_RXFE if not self.rx else 0
            return self.registers[register]
        if register == UART_RX_TX:
            return self.rx.pop(0) if self.rx else 0
        if register == UART_LSR:
            return UART_LSR_THRE | (UART_LSR_DR if self.rx else 0)
        return self.registers[register]

    def write(self, _ctx: object, at: int, value: int, _wide: int) -> None:
        register = at
        if self.kind == UART_PL011_MMIO:
            if register == PL011_DR:
                self.tx.append(value & 0xFF)
            else:
                self.registers[register] = value & 0xFF
            return
        # the 16550: with DLAB (LCR bit 7) set, the first two registers are the divisor
        # latches, not the data and interrupt registers
        if register == UART_RX_TX and not (self.registers[UART_LCR] & 0x80):
            self.tx.append(value & 0xFF)
        else:
            self.registers[register] = value & 0xFF


_READ = ctypes.CFUNCTYPE(
    ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32
)
_WRITE = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32
)


class UartFill(ctypes.Structure):
    """struct rxf_slip_line_uart of native/lwip/slip_line_uart.h."""

    _fields_ = (
        ("base", ctypes.c_size_t),
        ("kind", ctypes.c_uint32),
        ("shift", ctypes.c_uint32),
        ("wide", ctypes.c_uint32),
        ("escaped", ctypes.c_uint32),
        ("frame", ctypes.c_uint32),
        ("in_fn", ctypes.c_void_p),
        ("out_fn", ctypes.c_void_p),
        ("ctx", ctypes.c_void_p),
    )


class UartSeat:
    """The firmware seat's fill over a register-level device model (both devices, one fill).
    The wire speaks the SLIP stream the registers carry, so ``deliver`` frames a packet onto it."""

    def __init__(self, lib: ctypes.CDLL, kind: int, name: str) -> None:
        self.name = name
        self.lib = lib
        self.device = UartDevice(kind)
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
        lib.rxf_slip_line_uart_pump.restype = ctypes.c_int64
        lib.rxf_slip_line_uart_pump.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.fill = UartFill()
        self.reads = _READ(self.device.read)
        self.writes = _WRITE(self.device.write)
        assert (
            lib.rxf_slip_line_uart_init(
                ctypes.byref(self.fill),
                0,
                uart_device(kind),
                self.reads,
                self.writes,
                None,
            )
            == 0
        ), "the device word is refused by the fill"
        self.wire = b""

    def close(self) -> None:
        self.lib.rxf_slip_line_uart_deinit(ctypes.byref(self.fill))
        self.reads = None  # type: ignore[assignment] - the callbacks die with the seat
        self.writes = None  # type: ignore[assignment]

    def wire_send(self, data: bytes) -> int:
        self.device.rx.extend(data)
        return len(data)

    def wire_recv(self) -> bytes:
        out, self.device.tx = bytes(self.device.tx), bytearray()
        return out

    def deliver(self, packet: bytes) -> None:
        self.wire_send(slip.encode(packet))

    def replies(self) -> tuple[bytes, ...]:
        return slip.SlipDecoder().feed(self.wire_recv())

    def pump(self, face: Face) -> int:
        moved = self.lib.rxf_slip_line_uart_pump(face.address, ctypes.byref(self.fill))
        assert moved >= 0, "the fill's I/O failed"
        return int(moved)

    def pump_all(self, face: Face) -> int:
        moved = 0
        while True:
            step = self.pump(face)
            moved += step
            if step == 0:
                return moved
