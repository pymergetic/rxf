"""The UART as a provider of the Channel on every machine: x86_64 COM1, aarch64 PL011, wasm32 FIFOs.

The leaf `uart.pump` moves bytes between the device and the two rings; the live pump serves them.
A UART model sits behind the machine (port I/O hooks for the 16550, an MMIO region for the PL011)
and the same client of the host build talks over it. wasm32 has no port and no trap on a load: its
device is two FIFOs in linear memory (native/graph.h) and the host between two steps is the line.
"""

import shutil
import struct
from collections import deque
from pathlib import Path

import graph_header
import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import frames, physical
from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.harness import HostImage

from . import live_world as lw
from .snippets import STARTER
from .support import resolved

UART = graph_header.leaves()["uart_pump"]
COM1 = 0x3F8
PL011 = 0x09000000
BASE = {"x86_64": COM1, "aarch64": PL011}
NUMBERS = graph_header.numbers()
NS16550_MMIO = NUMBERS["RXF_UART_DEVICE_NS16550_MMIO"]
PL011_MMIO = NUMBERS["RXF_UART_DEVICE_PL011_MMIO"]
NS16550_PORT = NUMBERS["RXF_UART_DEVICE_NS16550_PORT"]
SHIFT_AT, WIDE = NUMBERS["RXF_UART_SHIFT_AT"], NUMBERS["RXF_UART_WIDE"]
MMIO_BASE = 0x09000000
# (machine, device word of the capability, base of the device, register shift, wide registers)
DEVICES = {
    "x86_64 16550 ports": ("x86_64", 1, COM1, 0, False),
    "aarch64 PL011": ("aarch64", 1, PL011, 0, False),
    "x86_64 16550 in memory": ("x86_64", NS16550_MMIO, MMIO_BASE, 0, False),
    "x86_64 16550 in memory, shift 2": (
        "x86_64",
        NS16550_MMIO | (2 << SHIFT_AT),
        MMIO_BASE,
        2,
        False,
    ),
    "x86_64 PL011 in memory": ("x86_64", PL011_MMIO, MMIO_BASE, 0, False),
    "aarch64 16550 in memory": ("aarch64", NS16550_MMIO, MMIO_BASE, 0, False),
    "aarch64 16550 in memory, shift 2, wide": (
        "aarch64",
        NS16550_MMIO | (2 << SHIFT_AT) | WIDE,
        MMIO_BASE,
        2,
        True,
    ),
    "aarch64 PL011 spelled out": ("aarch64", PL011_MMIO, MMIO_BASE, 0, False),
}
RX_HEAD, RX_TAIL = 120, 124


class Uart:
    """The device: bytes the peer sends wait in a FIFO, bytes the image writes are logged. After each
    byte the transmitter is busy for `busy` polls, like a device that is slower than the CPU."""

    def __init__(self, busy: int = 0, layout: str = "pl011", shift: int = 0) -> None:
        self.fifo: deque[int] = deque()
        self.sent = bytearray()
        self.busy = busy
        self.wait = 0
        self.layout = layout  # "pl011" or "ns16550"
        self.shift = shift

    def ready(self) -> bool:
        if self.wait > 0:
            self.wait -= 1
            return False
        return True

    def send(self, value: int) -> None:
        self.sent.append(value & 0xFF)
        self.wait = self.busy

    # 16550: data at port 0, line status at port 5
    def port_in(self, port: int) -> int:
        if port == COM1:
            return self.fifo.popleft() if self.fifo else 0
        if port == COM1 + 5:
            return (1 if self.fifo else 0) | (0x20 if self.ready() else 0)
        return 0

    def port_out(self, port: int, value: int) -> None:
        if port == COM1:
            self.send(value)

    # in memory. PL011: data register at 0, flags at 0x18 (bit 4: receive FIFO empty, bit 5:
    # transmit FIFO full). 16550: registers a shift apart, data at 0, line status at 5.
    def read(self, offset: int) -> int:
        if self.layout == "ns16550":
            if offset % (1 << self.shift):
                return 0xFF
            return self.port_in(COM1 + (offset >> self.shift))
        if offset == 0:
            return self.fifo.popleft() if self.fifo else 0
        if offset == 0x18:
            return (0x10 if not self.fifo else 0) | (0 if self.ready() else 0x20)
        return 0

    def write(self, offset: int, value: int) -> None:
        if self.layout == "ns16550":
            if offset % (1 << self.shift) == 0:
                self.port_out(COM1 + (offset >> self.shift), value & 0xFF)
        elif offset == 0:
            self.send(value)


def attach(
    machine: lw.Machine, name: str, device: Uart, ports: bool, base: int = PL011
) -> None:
    import unicorn
    from unicorn import x86_const

    uc = machine.emulator.uc  # type: ignore[attr-defined]
    if ports:
        uc.hook_add(
            unicorn.UC_HOOK_INSN,
            lambda uc, port, size, user: device.port_in(port),
            None,
            1,
            0,
            x86_const.UC_X86_INS_IN,
        )
        uc.hook_add(
            unicorn.UC_HOOK_INSN,
            lambda uc, port, size, value, user: device.port_out(port, value),
            None,
            1,
            0,
            x86_const.UC_X86_INS_OUT,
        )
    else:
        uc.mmio_map(
            base,
            0x1000,
            lambda uc, offset, size, user: device.read(offset),
            None,
            lambda uc, offset, size, value, user: device.write(offset, value),
            None,
        )


class UartLink:
    """What a wire to the image is: the host puts bytes in the device and takes what the device sent."""

    def __init__(self, machine: lw.Machine, device: Uart) -> None:
        self.machine = machine
        self.device = device

    def write(self, data: bytes) -> None:
        self.device.fifo.extend(data)

    def read(self) -> bytes:
        out = bytes(self.device.sent)
        self.device.sent.clear()
        return out

    def pump(self) -> None:
        """What a loop in the image does: the device into the rings, serve, the ring out to the device."""
        assert self.machine.leaf(UART) == 0
        self.machine.pump()
        assert self.machine.leaf(UART) == 0


@pytest.fixture(scope="module")
def image() -> Image:
    return tree.load(Path(STARTER))


@pytest.fixture(params=list(DEVICES))
def serial(request: pytest.FixtureRequest, image: Image):
    name, word_, base, shift, _ = DEVICES[request.param]
    spec = lw.link.LiveSpec(uart=base, uart_device=word_)
    machine = lw.machine_of(name, image, spec)
    layout = (
        "ns16550"
        if (word_ & 0xFF) == NS16550_MMIO or "ports" in request.param
        else "pl011"
    )
    device = Uart(busy=0, layout=layout, shift=shift)  # a slow one: test_a_slow_device
    attach(machine, name, device, ports="ports" in request.param, base=base)
    link = UartLink(machine, device)
    yield name, machine, device, link, Client(link, pump=link.pump)


def word(machine: lw.Machine, offset: int) -> int:
    assert machine.linked.live is not None
    return struct.unpack("<I", machine.read(machine.linked.live.area + offset, 4))[0]


def test_the_client_talks_to_the_image_over_the_uart(serial, image: Image):
    _, _, _, _, client = serial
    raw = image.to_bytes()
    host = HostImage(raw)
    assert client.ping(b"over a wire") == b"over a wire"
    assert client.hello().handles == host.client.hello().handles
    assert client.digest() == image.digest()
    assert client.resolve("pymergetic.rxf.application.main") == host.client.resolve(
        "pymergetic.rxf.application.main"
    )
    # a change goes in over the same wire and gives what the host gives
    result = Image.from_bytes(raw)
    parent = result.resolve("pymergetic.rxf.application")
    fresh = result.add(parent, "fresh", 8, 0xFFFFFFFFFFFFFFFF, b"over the uart")
    result.add(fresh, "inner", 8, 0xFFFFFFFFFFFFFFFF, b"")
    compiled = physical.compile(Image.from_bytes(raw), result)
    assert client.apply(compiled.data) == result.digest()
    assert client.emit()[0] == result.compact().to_bytes()
    assert client.resolve("pymergetic.rxf.application.fresh.inner") is not None


def test_a_slow_device_is_not_waited_for(serial):
    _, machine, device, link, _ = serial
    device.busy = 5
    link.write(frames.encode(frames.PING, 1, 0, b"x" * 200))
    assert machine.leaf(UART) == 0
    # the whole request is in the ring, and the image has not answered yet
    assert word(machine, RX_HEAD) - word(machine, RX_TAIL) == len(
        frames.encode(frames.PING, 1, 0, b"x" * 200)
    )
    machine.pump()
    assert machine.leaf(UART) == 0
    first = len(device.sent)
    assert 0 < first < 220  # the device took what it could in one call
    for _ in range(220 * (device.busy + 1) + 10):  # one byte per busy+1 polls
        machine.leaf(UART)
    answers = frames.Decoder().feed(link.read())
    assert [(a.sequence, a.payload) for a in answers] == [(1, b"x" * 200)]


def test_a_full_ring_leaves_the_rest_in_the_device(serial):
    _, machine, device, link, client = serial
    assert machine.linked.live is not None
    capacity = machine.linked.live.regions["rx"][1]
    link.write(
        bytes(range(256)) * ((capacity + 4096) // 256)
    )  # more than the ring holds, all noise
    assert machine.leaf(UART) == 0
    assert word(machine, RX_HEAD) - word(machine, RX_TAIL) == capacity
    assert len(device.fifo) == 4096
    # the image skips the noise a byte at a time, and the device gives the rest
    link.pump()
    link.pump()
    assert word(machine, RX_HEAD) - word(machine, RX_TAIL) < capacity
    assert not device.fifo
    assert client.ping(b"after the noise") == b"after the noise"


@pytest.mark.parametrize(
    "name",
    [
        "x86_64",
        "aarch64",
        pytest.param(
            "wasm32",
            marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
        ),
    ],
)
def test_the_leaf_is_unbound_without_a_device(name: str, image: Image):
    machine = lw.machine_of(name, image)  # a live image without a Uart capability
    try:
        assert machine.leaf(UART) == 13
    finally:
        if hasattr(machine, "close"):
            machine.close()


@pytest.mark.parametrize(
    "name, word_",
    [
        ("aarch64", NS16550_PORT),  # no port I/O on aarch64
        ("aarch64", 99),
        ("x86_64", 99),
        ("x86_64", 0x100 | 77),  # a shift does not make a kind
    ],
)
def test_a_device_the_machine_cannot_have_is_unbound(
    name: str, word_: int, image: Image
):
    spec = lw.link.LiveSpec(uart=MMIO_BASE, uart_device=word_)
    machine = lw.machine_of(name, image, spec)
    assert machine.leaf(UART) == 13


FIFO = graph_header.numbers()
FIFO_BYTES = FIFO["RXF_UART_FIFO_BYTES"]
FIFO_RX, FIFO_TX = FIFO["RXF_UART_FIFO_RX"], FIFO["RXF_UART_FIFO_TX"]


class FifoLine:
    """The line of a wasm32 machine: the host puts bytes in the first FIFO and takes them from the
    second, each time the machine is stopped. What does not fit waits here."""

    def __init__(self, machine: lw.Machine) -> None:
        assert machine.linked.live is not None
        self.machine = machine
        self.base = machine.linked.live.regions["uart"][0]
        self.pending = bytearray()
        self.sent = bytearray()

    def word(self, at: int) -> int:
        return struct.unpack("<I", self.machine.read(self.base + at, 4))[0]

    def put(self, at: int, value: int) -> None:
        self.machine.write(self.base + at, struct.pack("<I", value))

    def wrapped(self, at: int, start: int, count: int) -> list[tuple[int, int, int]]:
        """(address, from, to) of `count` bytes of a FIFO from the counter `start`."""
        first = min(count, FIFO_BYTES - start % FIFO_BYTES)
        parts = [(self.base + at + start % FIFO_BYTES, 0, first)]
        if count > first:
            parts.append((self.base + at, first, count))
        return parts

    def feed(self) -> None:
        head, tail = self.word(0), self.word(4)
        count = min(FIFO_BYTES - (head - tail), len(self.pending))
        data = bytes(self.pending[:count])
        del self.pending[:count]
        for address, start, stop in self.wrapped(FIFO_RX, head, count):
            self.machine.write(address, data[start:stop])
        self.put(0, head + count)

    def drain(self) -> None:
        head, tail = self.word(8), self.word(12)
        for address, start, stop in self.wrapped(FIFO_TX, tail, head - tail):
            self.sent += self.machine.read(address, stop - start)
        self.put(12, head)

    # a link
    def write(self, data: bytes) -> None:
        self.pending += data

    def read(self) -> bytes:
        out = bytes(self.sent)
        self.sent.clear()
        return out

    def pump(self) -> None:
        self.feed()
        assert self.machine.leaf(UART) == 0
        self.machine.pump()
        assert self.machine.leaf(UART) == 0
        self.drain()


@pytest.fixture
def fifo(image: Image):
    if shutil.which("node") is None:
        pytest.skip("needs node")
    machine = lw.machine_of("wasm32", image, lw.link.LiveSpec(uart=1))
    line = FifoLine(machine)
    try:
        yield machine, line, Client(line, pump=line.pump)
    finally:
        machine.close()


def test_the_client_talks_to_a_wasm32_image_over_the_fifos(fifo, image: Image):
    _, _, client = fifo
    raw = image.to_bytes()
    host = HostImage(raw)
    assert client.ping(b"over a wire") == b"over a wire"
    assert client.digest() == image.digest()
    assert client.resolve("pymergetic.rxf.application.main") == host.client.resolve(
        "pymergetic.rxf.application.main"
    )
    result = Image.from_bytes(raw)
    parent = resolved(result, "pymergetic.rxf.application")
    fresh = result.add(parent, "fresh", 8, 0xFFFFFFFFFFFFFFFF, b"over the fifos")
    result.add(fresh, "inner", 8, 0xFFFFFFFFFFFFFFFF, b"")
    compiled = physical.compile(Image.from_bytes(raw), result)
    assert client.apply(compiled.data) == result.digest()
    assert client.resolve("pymergetic.rxf.application.fresh.inner") is not None


def test_a_full_fifo_leaves_the_rest_with_the_line(fifo):
    machine, line, client = fifo
    line.write(bytes(range(256)) * 4)  # more than a FIFO holds, all noise
    line.feed()
    assert machine.leaf(UART) == 0
    assert line.word(0) - line.word(4) == 0  # the image took all the FIFO had ...
    assert len(line.pending) == 1024 - FIFO_BYTES  # ... and the line kept the rest
    for _ in range(8):
        line.pump()
    assert not line.pending
    assert client.ping(b"after the noise") == b"after the noise"


def test_a_full_transmit_fifo_leaves_the_rest_in_the_ring(fifo):
    machine, line, _ = fifo
    line.write(frames.encode(frames.PING, 1, 0, b"x" * 600))
    while line.pending:  # the request comes in 256 bytes at a time
        line.feed()
        assert machine.leaf(UART) == 0
    machine.pump()
    assert machine.leaf(UART) == 0
    # 600 bytes and a header do not fit 256: the FIFO is full and the ring still holds the rest
    assert line.word(8) - line.word(12) == FIFO_BYTES
    assert word(machine, 128) - word(machine, 132) == 600 + 20 - FIFO_BYTES
    for _ in range(4):
        line.drain()
        assert machine.leaf(UART) == 0
    line.drain()
    answers = frames.Decoder().feed(line.read())
    assert [(a.sequence, a.payload) for a in answers] == [(1, b"x" * 600)]
