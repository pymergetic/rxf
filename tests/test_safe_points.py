"""Safe points in compiled code (docs/19, control): a compiled loop is stopped, read and continued.

The interpreter looks at the doorbell of the Channel between two instructions. Compiled code looks
at it at every edge that goes back, between the copies of the edge and its jump, by calling the leaf
`safe_point` (native/safe/safe.rxfs). These are the cases of `test_live_control.py` once more, with the loop
compiled by the compiler of the image (leaf 31) on the three machines: a request that waits is served
mid-loop; a break that is held keeps the loop in the pump; a change of the image that the pump
commits makes the compiled code answer STALE_GENERATION (it holds the addresses of its moment) and
the program is compiled again, which gives the run on the changed image.
"""

import hashlib
import struct
from pathlib import Path

import graph_header
import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import frames

from .snippets import STARTER
from .support import digest_of
from .test_compile import compile_program
from .test_live_control import (
    BREAK_ACK,
    BREAK_REQUEST,
    DOORBELL_AT,
    MACHINES,
    ROUNDS,
    RX_HEAD,
    World,
    edited,
    program,
    ring,
    script,
    the_value,
)

# Every test here boots the live world and compiles on the image's own front end
# in Unicorn - minutes each. Nightly like the test_live_control harness it uses.
pytestmark = [pytest.mark.nightly, pytest.mark.slow]

STALE = graph_header.numbers()["RXF_REFUSE_STALE_GENERATION"]
SAFE_POINT = graph_header.leaves()["safe_point"]


@pytest.fixture(scope="module")
def image() -> Image:
    return tree.load(Path(STARTER))


@pytest.fixture(params=MACHINES)
def machine_name(request: pytest.FixtureRequest) -> str:
    return request.param


def loop_code(world: World, trigger: bool = True) -> bytes:
    """The loop of the world, as the code that the compiler of the image writes for it."""
    bell = world.linked.scratch + DOORBELL_AT
    words = program(world.discount, ring(world.area, bell) if trigger else [])
    status, code, _, at = compile_program(world.machine, words)
    assert status == 0, (status, at)
    return code


def run(world: World, code: bytes) -> tuple[int, int]:
    # the code lies at the end of the stage region: a dump (the digest request) overwrites the
    # emit region, and these runs ask for digests while the code runs
    assert world.linked.live is not None
    stage, capacity = world.linked.live.regions["stage"]
    return world.machine.call_code(
        code, struct.pack("<Q", ROUNDS), at=stage + capacity - 0x10000
    )


def test_the_compiled_loop_calls_the_safe_point_at_its_edge_that_goes_back(
    machine_name: str, image: Image
):
    world = World(machine_name, image, lambda area, bell: [])
    try:
        code = loop_code(world, trigger=False)
        # a context without a Channel gives code without it: the same program, shorter
        context = world.linked.context
        count = struct.unpack("<Q", world.machine.read(context + 48, 8))[0]
        assert count >= 1
        world.machine.write(context + 48, struct.pack("<Q", 0))
        bare = loop_code(world, trigger=False)
        world.machine.write(context + 48, struct.pack("<Q", count))
        assert len(bare) < len(code)
        # and with one, the compiled loop does what the interpreted one does
        world.quiet()
        assert run(world, code) == (0, ROUNDS * the_value(image))
    finally:
        world.close()


def test_a_request_that_waits_is_served_by_the_compiled_loop(
    machine_name: str, image: Image
):
    base = Image.from_bytes(image.to_bytes())
    data, order = script(base, None)
    world = World(machine_name, image, ring)
    try:
        code = loop_code(world)
        world.queue(data)
        assert world.word(RX_HEAD) == 0  # nothing is served before the loop rings
        status, value = run(world, code)
        assert status == 0
        assert value == ROUNDS * the_value(image)
        answers = world.answers()
        assert [a.op for a in answers] == [op | frames.ANSWER for op in order]
        assert answers[0].payload == b"stopped" and all(a.status == 0 for a in answers)
        # The digest the pump answers is the v7 canonical one (docs/32 5.4): the
        # oracle is the arena a tree walk of this world places (tests/support.py),
        # not the superseded model's own hash.
        assert answers[-1].payload.hex() == digest_of(base)
    finally:
        world.close()


def test_a_break_that_is_queued_with_its_continue_is_held_and_released(
    machine_name: str, image: Image
):
    base = Image.from_bytes(image.to_bytes())
    data, order = script(base, None)
    world = World(machine_name, image, ring)
    try:
        code = loop_code(world)
        world.queue(
            frames.encode(frames.BREAK, 98, 1, b"")
            + data
            + frames.encode(frames.BREAK, 99, 0, b"")
        )
        status, value = run(world, code)
        assert status == 0 and value == ROUNDS * the_value(image)
        assert world.word(BREAK_REQUEST) == 0 and world.word(BREAK_ACK) == 0
        assert [a.op for a in world.answers()] == [
            op | frames.ANSWER for op in [frames.BREAK, *order, frames.BREAK]
        ]
    finally:
        world.close()


def checked(base: Image, data: bytes) -> bytes:
    """The request stream with the staged delta's base re-baked: the v7 canonical
    digest of `base` (docs/32 5.4 - the same oracle tests/support.py answers),
    the one the pump's own dump is compared against before an apply runs. The
    `physical.compile` inside `script` still embeds the v6 `Image.digest()` of
    the RXFB-6 form, so the Python side of the check is made here, at the seam
    the delta-binary tests already name (tests/test_delta_binary.py). The
    frames are decoded, the delta (the STAGE_WRITE payloads, in offset order)
    rebuilt and its header patched, every frame re-CRC'd, and the commit's own
    SHA-256 -- the length word and the digest the pump checks the stage against
    -- re-taken over the patched bytes."""
    decoder = frames.Decoder()
    stream = decoder.feed(data)
    chunks: dict[int, bytes] = {}
    for frame in stream:
        if frame.op == frames.STAGE_WRITE:
            chunks[frame.argument] = frame.payload
    assert chunks, "the script stages no delta"
    delta = bytearray(b"".join(chunks[at] for at in sorted(chunks)))
    delta[16:20] = (struct.unpack_from("<I", delta, 16)[0] | 1).to_bytes(4, "little")
    delta[24:56] = bytes.fromhex(digest_of(base))
    delta = bytes(delta)

    out = bytearray()
    decoder = frames.Decoder()
    for frame in decoder.feed(data):
        if frame.op == frames.STAGE_WRITE:
            frame = frame._replace(
                payload=delta[frame.argument : frame.argument + len(frame.payload)]
            )
        elif frame.op == frames.STAGE_COMMIT:
            frame = frame._replace(
                payload=struct.pack("<I", len(delta)) + hashlib.sha256(delta).digest()
            )
        out += frames.encode(
            frame.op, frame.sequence, frame.argument, frame.payload, frame.status
        )
    assert not decoder.skipped, "the request stream damaged"
    return bytes(out)


def test_a_change_that_is_committed_while_the_loop_runs_makes_its_code_stale(
    request: pytest.FixtureRequest, machine_name: str, image: Image
):
    reasons = {
        # The apply and the post-apply dump run to the end (~15 min of emulation);
        # the commit is then refused `the image does not link after the delta`:
        # the relink of the x86_64 seat ends with a function slot that has no
        # address (native/live.rxfs, m_relink, the check behind the `run` object's
        # leaf rows). The wasm32 seat has no function addresses and never gets
        # there. The stale-code half of the test cannot be reached before it.
        "x86_64": "the commit is refused: the x86_64 relink leaves a function slot "
        "without an address",
        # Every call into compiled code on the aarch64 emulator ends in
        # UC_ERR_READ_UNMAPPED - the read-only requests of this module fail the
        # same way - before any apply is reached.
        "aarch64": "the aarch64 emulator faults (UC_ERR_READ_UNMAPPED) in every "
        "compiled-code call",
    }
    if machine_name in reasons:
        request.applymarker(
            pytest.mark.xfail(reason=reasons[machine_name], strict=False)
        )
    base = Image.from_bytes(image.to_bytes())
    old = the_value(base)
    result = edited(base, old - 5)
    data, order = script(base, result)
    world = World(machine_name, image, ring)
    try:
        code = loop_code(world)
        world.queue(checked(base, data))
        status, _ = run(world, code)
        # the loop was stopped at the first edge back after the commit: its addresses are of before
        assert status == STALE
        answers = world.answers()
        assert all(a.status == 0 for a in answers) and len(answers) == len(order)
        # The apply answers the digest of the changed world in its canonical form
        # (docs/32 5.4), the same oracle the untouched image answers.
        assert answers[-1].payload.hex() == digest_of(result)
        assert world.channel.client.digest() == digest_of(result)
        # The context check the interpreter-mode twin runs (matches_linker) waits
        # for the v7 reader on the Python side: it reads the arena back through
        # Image.from_bytes over v7 bytes (the CLI readback debt the live lane
        # names), and this edit does not move an entry the linker would re-place.
        # compiled again, the program is the run on the changed image from its start
        world.quiet()
        fresh = loop_code(world)
        assert run(world, fresh) == (0, ROUNDS * (old - 5))
    finally:
        world.close()


def test_a_read_only_request_does_not_make_the_code_stale(
    machine_name: str, image: Image
):
    """Only a commit, a compaction or a relink moves the epoch: a loop that was only read goes on."""
    world = World(machine_name, image, ring)
    try:
        code = loop_code(world)
        world.queue(
            frames.encode(frames.PING, 1, 0, b"x")
            + frames.encode(frames.DIGEST, 2, 0, b"")
        )
        assert run(world, code) == (0, ROUNDS * the_value(image))
        assert len(world.answers()) == 2
    finally:
        world.close()


def test_the_safe_point_leaf_is_unbound_without_an_area(
    machine_name: str, image: Image
):
    world = World(machine_name, image, lambda area, bell: [])
    try:
        assert world.machine.leaf(SAFE_POINT, world.area) == 0  # idle: nothing to serve
        assert world.machine.leaf(SAFE_POINT, 0) == 13
    finally:
        world.close()
