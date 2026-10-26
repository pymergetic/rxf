"""Safe points and break: a run of the interpreter that is stopped, read, changed and continued.

The interpreter looks at the doorbell of the Channel between two instructions (docs/19, control).
Each case runs a loop program in the linked starter on the three machines. The loop adds the value
of the object `application.discount` to a sum, n times. Inside the loop the program rings the
doorbell, or asks for a break, by writing a word of the live area itself: that is the one place a
test can act while a run goes on without a second thread, and it is the same on every machine.
"""

import hashlib
import shutil
import struct
from typing import Any, cast

import graph_header
import pytest

from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import frames, physical

from . import live_world as lw
from .graph_programs import (
    NONZERO,
    OP,
    Item,
    assemble,
    call,
    edge,
    lit,
    move,
    obj,
    slot,
)
from .snippets import running_image
from .support import resolved

pytestmark = [pytest.mark.nightly, pytest.mark.slow]

N = graph_header.numbers()
LEAF = graph_header.leaves()
LIVE = 1  # RXF_OBJECT_LIVE
DISCOUNT = "pymergetic.rxf.application.discount"
ROUNDS = 40
AT = 10  # the iteration that rings the doorbell or asks for the break
PROGRAM_ID = 1  # the first of the spare entries of the context
RX_HEAD, BREAK_REQUEST, BREAK_ACK = 120, 176, 180
PROGRAM_AT, DOORBELL_AT = 0x800, 0xC00  # in the scratch memory behind the heap
MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]
EMULATED = ["x86_64", "aarch64"]


@pytest.fixture(scope="module")
def image() -> Image:
    return running_image()


def program(identity: int, trigger: list[Item]) -> bytes:
    n, i, acc, tmp, flag = 0, 8, 16, 24, 32
    items: list[Item] = [
        move(i, 8, lit(0)),
        move(acc, 8, lit(0)),
        "loop",
        [OP["LESS"], flag, *slot(i), *slot(n)],
        [OP["BRANCH"], *slot(flag, 1), NONZERO, *edge("body"), *edge("done")],
        "body",
        [OP["FIELD"], tmp, 8, *obj(identity, 4), 0, 4],
        call("wrapping_add_uint64_t", [slot(acc), slot(tmp)], (acc, 8)),
        [OP["SWITCH"], *slot(i), 1, AT, *edge("trigger"), *edge("next")],
        "trigger",
        *trigger,
        "next",
        [OP["INCREMENT"], i, 8, *slot(i)],
        [OP["GOTO"], *edge("loop")],
        "done",
        [OP["RETURN"], *slot(acc), 8],
    ]  # fmt: skip
    return assemble(64, [(n, 8)], items)


def ring(area: int, bell: int) -> list[Item]:
    """The doorbell: rx_head is set from a word the host has put in memory (`bell`)."""
    return [
        call("memory_copy", [lit(area + RX_HEAD), lit(4), lit(bell), lit(4), lit(4)])
    ]


def stop(area: int, bell: int) -> list[Item]:
    """A break request: the word of the area is set to 1."""
    return [call("memory_fill", [lit(area + BREAK_REQUEST), lit(4), lit(1, 1), lit(1)])]


class World:
    """One machine with the starter, the loop installed as a spare object, and a channel."""

    def __init__(self, name: str, image: Image, trigger_of) -> None:
        self.name = name
        self.machine = lw.machine_of(name, image)
        linked = self.machine.linked
        assert linked.live is not None
        self.linked = linked
        self.area = linked.live.area
        self.channel = lw.Channel(self.machine)
        self.discount = linked.ids[DISCOUNT]
        words = program(
            self.discount, trigger_of(self.area, linked.scratch + DOORBELL_AT)
        )
        at = linked.scratch + PROGRAM_AT
        self.machine.write(at, words)
        # the program is a spare entry of the context: id 1, generation 1, live
        entries = struct.unpack_from("<Q", linked.data, 40)[0]
        self.machine.write(
            entries,
            struct.pack(
                "<10Q", PROGRAM_ID, 1, 0, at, len(words), len(words), LIVE, 0, 0, 0
            ),
        )

    def close(self) -> None:
        if hasattr(self.machine, "close"):
            self.machine.close()

    def run(self) -> tuple[int, int]:
        return self.machine.run(PROGRAM_ID, struct.pack("<Q", ROUNDS))

    def word(self, offset: int) -> int:
        return struct.unpack("<I", self.machine.read(self.area + offset, 4))[0]

    def queue(self, data: bytes) -> None:
        """Puts requests in the ring without ringing the doorbell: that is the program's to do."""
        address, capacity = self.channel.rx
        assert len(data) <= capacity
        self.machine.write(address, data)
        self.machine.write(
            self.linked.scratch + DOORBELL_AT, struct.pack("<I", len(data))
        )

    def quiet(self) -> None:
        """The program rings with the head the ring has now: no request waits."""
        self.machine.write(
            self.linked.scratch + DOORBELL_AT, struct.pack("<I", self.word(RX_HEAD))
        )

    def answers(self) -> list[frames.Frame]:
        return frames.Decoder().feed(self.channel.read())


def script(base: Image, result: Image | None) -> tuple[bytes, list[int]]:
    """Requests: resolve and read the object, apply the delta (if any), read it again."""
    out = bytearray()
    sequence = [0]
    order: list[int] = []

    def put(op: int, argument: int = 0, payload: bytes = b"") -> None:
        sequence[0] += 1
        order.append(op)
        out.extend(frames.encode(op, sequence[0], argument, payload))

    put(frames.PING, payload=b"stopped")
    put(frames.RESOLVE, payload=DISCOUNT.encode())
    if result is not None:
        data = physical.compile(base, result).data
        put(frames.STAGE_RESET)
        for at in range(0, len(data), frames.MAX_PAYLOAD):
            put(frames.STAGE_WRITE, at, data[at : at + frames.MAX_PAYLOAD])
        put(
            frames.STAGE_COMMIT,
            payload=struct.pack("<I", len(data)) + hashlib.sha256(data).digest(),
        )
    put(frames.DIGEST)
    return bytes(out), order


def edited(base: Image, value: int) -> Image:
    result = Image.from_bytes(base.to_bytes())
    result.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf">'
        f'<rxf:set path="{DISCOUNT}" rxf:data="{struct.pack("<I", value).hex()}"/></delta>'
    )
    return result


def the_value(image: Image) -> int:
    return struct.unpack("<I", image.get(resolved(image, DISCOUNT))["payload"][:4])[0]


@pytest.fixture(params=MACHINES)
def machine_name(request: pytest.FixtureRequest) -> str:
    return request.param


def baseline(name: str, image: Image) -> int:
    """The run with nothing to serve: the value of the sum."""
    world = World(name, image, lambda area, bell: [])
    try:
        status, value = world.run()
        assert status == 0
        return value
    finally:
        world.close()


def test_a_run_without_a_request_is_what_it_was(machine_name: str, image: Image):
    assert baseline(machine_name, image) == ROUNDS * the_value(image)


def test_a_running_program_serves_a_request_at_a_safe_point(
    machine_name: str, image: Image
):
    base = Image.from_bytes(image.to_bytes())
    data, order = script(base, None)
    world = World(machine_name, image, ring)
    try:
        world.queue(data)
        assert world.word(RX_HEAD) == 0  # nothing is served before the program rings
        status, value = world.run()
        assert status == 0
        # reading does not change what the run computes
        assert value == ROUNDS * the_value(image)
        answers = world.answers()
        assert [a.op for a in answers] == [op | frames.ANSWER for op in order]
        assert answers[0].payload == b"stopped" and all(a.status == 0 for a in answers)
        assert answers[-1].payload.hex() == base.digest()
    finally:
        world.close()


def test_a_change_made_while_the_program_runs_is_seen_by_its_next_iterations(
    machine_name: str, image: Image
):
    base = Image.from_bytes(image.to_bytes())
    old = the_value(base)
    result = edited(base, old - 5)
    data, order = script(base, result)
    world = World(machine_name, image, ring)
    try:
        world.queue(data)
        status, value = world.run()
        assert status == 0
        # iterations 0..AT read the old value, the rest the new one: the program was not restarted
        assert value == (AT + 1) * old + (ROUNDS - AT - 1) * (old - 5)
        answers = world.answers()
        assert all(a.status == 0 for a in answers) and len(answers) == len(order)
        assert answers[-1].payload.hex() == result.digest()
        assert world.channel.client.digest() == result.digest()
        # and the context of the image is the one the linker writes for it
        from .test_live_native import matches_linker

        matches_linker(world.channel, machine_name)
        # a second run on the changed image is the run on that image from the start
        world.quiet()
        assert world.run()[1] == ROUNDS * (old - 5)
    finally:
        world.close()


def test_a_break_with_the_continue_already_queued_gives_the_run_it_had(
    machine_name: str, image: Image
):
    base = Image.from_bytes(image.to_bytes())
    data, order = script(base, None)
    world = World(machine_name, image, ring)
    try:
        # stop, look, go on: the host's whole visit is in the ring when the program rings
        world.queue(
            frames.encode(frames.BREAK, 98, 1, b"")
            + data
            + frames.encode(frames.BREAK, 99, 0, b"")
        )
        status, value = world.run()
        assert status == 0 and value == ROUNDS * the_value(image)
        assert world.word(BREAK_REQUEST) == 0 and world.word(BREAK_ACK) == 0
        assert [a.op for a in world.answers()] == [
            op | frames.ANSWER for op in [frames.BREAK, *order, frames.BREAK]
        ]
    finally:
        world.close()


@pytest.mark.parametrize("machine", EMULATED)
def test_a_stopped_run_waits_for_the_host_and_goes_on_when_told(
    machine: str, image: Image
):
    import unicorn

    base = Image.from_bytes(image.to_bytes())
    old = the_value(base)
    result = edited(base, old + 7)
    data, _order = script(base, result)
    tail = frames.encode(frames.BREAK, 99, 0, b"")
    world = World(machine, image, stop)
    try:
        emulator = cast(lw.Emulated, world.machine).emulator
        pump = world.linked.slots[lw.PUMP]
        seen: list[
            tuple[int, int]
        ] = []  # at each entry of the pump: the request and the answer to it

        def hook(uc: Any, address: int, size: int, _user: object) -> None:
            seen.append((world.word(BREAK_REQUEST), world.word(BREAK_ACK)))
            if len(seen) == 3:
                # the host has looked at the stopped run, and now it speaks
                world.queue(data + tail)
                world.machine.write(
                    world.area + RX_HEAD, struct.pack("<I", len(data) + len(tail))
                )
            if len(seen) > 20:
                uc.emu_stop()

        emulator.uc.hook_add(unicorn.UC_HOOK_CODE, hook, begin=pump, end=pump)
        status, value = world.run()
        assert status == 0
        # the run stayed in the pump while the break was held, and said so
        assert seen[:3] == [(1, 1)] * 3 and len(seen) == 3
        # then the delta of the host and the continue: the run goes on with the changed image
        assert value == (AT + 1) * old + (ROUNDS - AT - 1) * (old + 7)
        assert world.word(BREAK_REQUEST) == 0 and world.word(BREAK_ACK) == 0
        answers = world.answers()
        assert answers[-1].op == frames.BREAK | frames.ANSWER
        assert answers[-2].payload.hex() == result.digest()
    finally:
        world.close()
