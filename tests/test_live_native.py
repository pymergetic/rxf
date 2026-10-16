"""The live layer as native code: the leaf `rxf_live_pump` of the linked image on three machines.

Each machine holds the starter linked with a live block (`link.LiveSpec`) and is talked to over its
rings with the same client the host build is tested with. What the native leaf answers, and what it
leaves in the arena, must be what the host build answers and leaves, byte for byte.
"""

import hashlib
import random
import shutil
import struct
from pathlib import Path

import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import Refused, frames, physical
from pymergetic.rxftk.live.client import Declined, LiveError
from pymergetic.rxftk.live.harness import HostImage

from . import live_world as lw
from .snippets import STARTER
from .support import resolved

NONE = 0xFFFFFFFFFFFFFFFF
MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]


def unsized(arena: bytes) -> bytes:
    """The arena without the header words that say how much room its region has (24..40)."""
    return arena[:24] + bytes(16) + arena[40:]


@pytest.fixture(scope="module")
def image() -> Image:
    return tree.load(Path(STARTER))


@pytest.fixture(scope="module")
def compact(image: Image) -> bytes:
    return image.compact().to_bytes()


@pytest.fixture(scope="module")
def raw(image: Image) -> bytes:
    """The arena as the linker puts it in memory: not compacted."""
    return image.to_bytes()


@pytest.fixture(scope="module", params=MACHINES)
def name(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def native(name: str, image: Image):
    machine = lw.machine_of(name, image)
    try:
        yield lw.Channel(machine)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_the_context_names_the_channel(name: str, image: Image):
    linked = lw.linked_image(image, name)
    assert linked.live is not None
    words = struct.unpack_from("<18Q", linked.data)
    assert words[6] == 1
    table = words[7] - linked.base
    assert struct.unpack_from("<3Q", linked.data, table) == (1, 1, linked.live.area)
    # the leaf is in the function slots, behind the interpreter's leaves
    assert lw.PUMP in linked.slots


def test_the_pump_serves_a_ping_and_the_area_is_found_in_the_context(
    native: lw.Channel,
):
    assert native.client.ping(b"hello") == b"hello"
    # area 0: the leaf takes the first Channel capability of the context
    native.write(frames.encode(frames.PING, 99, 0, b"cap"))
    assert native.machine.pump(0) == 0
    answers = frames.Decoder().feed(native.read())
    assert [(a.sequence, a.payload) for a in answers] == [(99, b"cap")]


def test_a_pump_without_a_channel_is_refused(name: str, image: Image):
    machine = lw.machine_of(name, image)
    try:
        linked = machine.linked
        # a context whose capability count is zero, and no area given
        machine.write(linked.context + 48, struct.pack("<Q", 0))
        assert machine.pump(0) == 13
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_reads_match_the_host_build(native: lw.Channel, image: Image, raw: bytes):
    host = HostImage(raw)
    n, h = native.client, host.client
    assert n.hello().handles == h.hello().handles
    assert n.digest() == h.digest() == image.digest()
    rng = random.Random(11)
    reference = Image.from_bytes(raw)
    for handle in [
        0,
        1,
        2,
        reference.handle_count - 1,
        *rng.sample(range(reference.handle_count), 40),
    ]:
        assert n.get(handle) == h.get(handle)
        assert n.path(handle) == h.path(handle)
        assert n.payload(handle) == h.payload(handle)
        assert sorted(n.children(handle)) == sorted(h.children(handle))
        assert n.refs(handle) == h.refs(handle)
    assert n.resolve("pymergetic.rxf.application.main") == h.resolve(
        "pymergetic.rxf.application.main"
    )
    assert n.resolve("no.such.path") is None
    with pytest.raises(LiveError):
        n.get(10**9)


def test_the_dump_is_the_canonical_one(native: lw.Channel, compact: bytes):
    dump, digest = native.client.emit()
    assert dump == compact
    assert digest == hashlib.sha256(compact).hexdigest()


def test_a_change_gives_the_image_the_host_gives(native: lw.Channel, raw: bytes):
    host = HostImage(raw)
    before = Image.from_bytes(raw)
    result = Image.from_bytes(raw)
    parent = resolved(result, "pymergetic.rxf.application")
    fresh = result.add(parent, "fresh", 8, NONE, b"payload")
    result.add(fresh, "inner", 8, NONE, b"")
    main = resolved(result, "pymergetic.rxf.application.main")
    result.set_state(main, 9)
    compiled = physical.compile(before, result)
    assert compiled.creates >= 2 and compiled.sets >= 1
    digest = native.client.apply(compiled.data)
    assert digest == host.client.apply(compiled.data) == result.digest()
    # the arena is the host's arena, byte for byte
    assert unsized(native.arena()) == unsized(host.area.arena_bytes())
    assert native.client.emit()[0] == result.compact().to_bytes()
    assert native.client.resolve("pymergetic.rxf.application.fresh.inner") is not None
    hello = native.client.hello()
    assert (hello.commits, hello.rejects) == (1, 0)


def test_a_refused_change_leaves_the_arena_byte_for_byte(
    native: lw.Channel, raw: bytes
):
    digest = Image.from_bytes(raw).digest()
    native.client.ping()  # the first request attaches the arena
    before = native.arena()
    cases = []
    gone = physical.Builder()
    gone.delete("pymergetic.rxf.application.main")
    cases.append((gone.build(digest), 9))
    missing = physical.Builder()
    missing.delete("pymergetic.rxf.application.nothing")
    cases.append((missing.build(digest), 3))
    wrong = physical.Builder()
    wrong.delete("pymergetic.rxf.application.main")
    cases.append((wrong.build("00" * 32), 2))
    cases.append((b"\0" * 80, 1))
    for data, reason in cases:
        with pytest.raises(Refused) as caught:
            native.client.apply(data)
        assert caught.value.reason == reason
        assert native.arena() == before
    assert native.client.hello().rejects == len(cases)
    assert native.client.digest() == digest


def test_random_edits_over_several_rounds(native: lw.Channel, raw: bytes):
    from .test_live import edited

    host = HostImage(raw)
    rng = random.Random(5)
    for _ in range(2):
        before = host.image()
        result = edited(before, rng)
        compiled = physical.compile(before, result)
        expected = result.digest()
        assert native.client.apply(compiled.data) == expected
        assert host.client.apply(compiled.data) == expected
        assert unsized(native.arena()) == unsized(host.area.arena_bytes())
        assert native.client.digest() == expected


link_ = lw.link


def context_of(
    channel: lw.Channel, count: int | None = None
) -> tuple[bytes, bytes, bytes]:
    """The context words, the function slots and the object entries as the machine holds them."""
    machine = channel.machine
    linked = machine.linked
    words = machine.read(linked.context, 144)
    slots, entries = (
        struct.unpack_from("<QQ", words, 24)[0],
        struct.unpack_from("<Q", words, 40)[0],
    )
    slot_count = struct.unpack_from("<Q", words, 16)[0]
    entry_count = struct.unpack_from("<Q", words, 32)[0] if count is None else count
    return (
        words,
        machine.read(slots, slot_count * link_.SLOT_BYTES),
        machine.read(entries, entry_count * link_.ENTRY_BYTES),
    )


def matches_linker(native: lw.Channel, name: str) -> None:
    """The context is what the linker writes for the arena the machine holds, at the same places."""
    linked = native.machine.linked
    first = struct.unpack_from("<Q", linked.data, 32)[0]
    room = lw.link.LiveSpec().entries
    words, slots, entries = context_of(native)
    count = struct.unpack_from("<Q", words, 32)[0]
    # the same places in memory: the linker is told how many entries the headroom still has
    arena = Image.from_bytes(native.arena())
    assert linked.live is not None
    region = linked.live.regions["arena"][1]
    spec = lw.link.LiveSpec(
        entries=room - (count - first), headroom=region - len(arena.to_bytes())
    )
    again = lw.linked_image(arena, name, spec)
    assert words == again.data[:144]
    assert slots == again.data[again.table[0] : again.table[1]]
    at = struct.unpack_from("<Q", again.data, 40)[0] - again.base
    # the spare entries are the programs'; the image writes the ones behind them
    spare = link_.SPARE * link_.ENTRY_BYTES
    assert entries[spare:] == again.data[at + spare : at + len(entries)]


def test_a_relink_writes_what_the_linker_writes(native: lw.Channel):
    linked = native.machine.linked
    native.client.ping()
    words, slots, entries = context_of(native)
    assert words == linked.data[:144]
    # the context is stale: no addresses in the slots (their ids stay: the image checks them), no
    # entries behind the spare ones, and a word that says how many there are
    machine = native.machine
    slot_at, entry_at = (
        struct.unpack_from("<Q", words, 24)[0],
        struct.unpack_from("<Q", words, 40)[0],
    )
    addressed = (
        linked.machine != "wasm32"
    )  # a wasm32 slot is a table index, not an address
    for k in range(len(slots) // link_.SLOT_BYTES if addressed else 0):
        machine.write(slot_at + k * link_.SLOT_BYTES + 16, bytes(8))
    spare = link_.SPARE * link_.ENTRY_BYTES
    machine.write(entry_at + spare, bytes(len(entries) - spare))
    machine.write(linked.context + 32, struct.pack("<Q", 0))
    assert context_of(native, len(entries) // link_.ENTRY_BYTES)[2] != entries
    written, count = native.client.relink()
    # the slots of wasm32 are table indices, which the image does not write
    expected = len(slots) // link_.SLOT_BYTES if addressed else 0
    assert (written, count) == (len(entries) // link_.ENTRY_BYTES, expected)
    assert context_of(native) == (words, slots, entries)


def test_a_change_leaves_a_context_that_the_linker_would_write(
    name: str, native: lw.Channel, raw: bytes
):
    native.client.ping()
    linked = native.machine.linked
    first = struct.unpack_from("<Q", linked.data, 32)[0]
    version = Image.from_bytes(raw)

    def assert_linked() -> None:
        matches_linker(native, name)

    typed = version.get(resolved(version, "pymergetic.rxf.application.discount"))[
        "type"
    ]
    state = Image.from_bytes(raw)

    def apply(edit) -> None:
        nonlocal state
        result = Image.from_bytes(state.to_bytes())
        edit(result)
        native.client.apply(physical.compile(state, result).data)
        state = result

    def add(image: Image) -> None:
        parent = resolved(image, "pymergetic.rxf.application")
        fresh = image.add(parent, "fresh", 8, typed, b"\x07\0\0\0")
        image.add(fresh, "inner", 8, typed, b"\x09\0\0\0")

    apply(add)
    assert struct.unpack_from("<Q", context_of(native)[0], 32)[0] == first + 2
    assert_linked()

    def rewrite(image: Image) -> None:
        image.set_payload(
            resolved(image, "pymergetic.rxf.application.fresh.inner"), b"\x01\0\0\0"
        )

    apply(rewrite)
    assert_linked()

    def remove(image: Image) -> None:
        image.remove(resolved(image, "pymergetic.rxf.application.fresh.inner"))

    apply(remove)
    assert struct.unpack_from("<Q", context_of(native)[0], 32)[0] == first + 1
    assert_linked()


def dirty_image(image: Image) -> Image:
    """An image with dead cells: objects that came and went, payloads that grew."""
    dirty = Image.from_bytes(image.to_bytes())
    parent = resolved(dirty, "pymergetic.rxf.application")
    for n in range(60):
        dirty.add(parent, f"tmp{n}", 8, NONE, bytes(300))
    for n in range(0, 60, 2):
        dirty.remove(resolved(dirty, f"pymergetic.rxf.application.tmp{n}"))
    main = resolved(dirty, "pymergetic.rxf.application.main")
    dirty.set_payload(main, dirty.get(main)["payload"] + bytes(512))
    return Image.from_bytes(dirty.to_bytes())


def with_garbage(image: Image) -> Image:
    """The image with objects that came and went at the end of the arena, and one that grew."""
    image = Image.from_bytes(image.to_bytes())
    parent = resolved(image, "pymergetic.rxf.application")
    for n in range(60):
        image.add(parent, f"tmp{n}", 8, NONE, bytes(300))
    for n in range(0, 60, 2):
        image.remove(resolved(image, f"pymergetic.rxf.application.tmp{n}"))
    return Image.from_bytes(image.to_bytes())


def test_compacting_a_canonical_arena_changes_nothing_and_relinks(
    name: str, image: Image, compact: bytes
):
    canonical = Image.from_bytes(compact)
    machine = lw.machine_of(name, canonical)
    try:
        native = lw.Channel(machine)
        host = HostImage(compact)
        done = native.client.compact()
        reference = host.client.compact()
        assert done.reclaimed == reference.reclaimed == 0
        assert (done.handles, done.body_size, done.digest) == (
            reference.handles,
            reference.body_size,
            reference.digest,
        )
        assert unsized(native.arena()) == unsized(compact)
        matches_linker(native, name)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def compacted_like_the_host(name: str, dirty: Image, native: lw.Channel) -> None:
    host = HostImage(dirty.to_bytes())
    reference = host.client.compact()
    done = native.client.compact()
    assert done.reclaimed > 0
    assert (done.handles, done.body_size, done.digest, done.reclaimed) == (
        reference.handles,
        reference.body_size,
        reference.digest,
        reference.reclaimed,
    )
    assert unsized(native.arena()) == unsized(host.area.arena_bytes())
    matches_linker(native, name)


def test_code_that_would_move_is_compacted_from_a_copy_of_the_pump(
    name: str, image: Image
):
    """The pump is an object of the arena, and in canonical order it is somewhere else. Native
    code copies itself out of the arena (into the log region), serves the compaction from there, and
    the function table points at its new place afterwards: the arena is the host's, the table is the
    linker's, and the pump is still the pump. wasm32 holds its code outside the arena."""
    dirty = with_garbage(image)
    machine = lw.machine_of(name, dirty)
    try:
        native = lw.Channel(machine)
        native.client.ping()
        before = native.arena()
        slot_before = None if name == "wasm32" else machine.slot(lw.PUMP)
        compacted_like_the_host(name, dirty, native)
        assert native.arena() != before
        if slot_before is not None:
            # the leaf did move, and the live area says it is no longer running from a copy
            assert machine.slot(lw.PUMP) != slot_before
        area = native.area
        assert struct.unpack("<3I", machine.read(area + 256, 12)) == (0, 0, 0)
        # the same pump serves on: from its new place, and the digest is still the host's
        native.client.ping()
        assert native.client.digest() == Image.from_bytes(native.arena()).digest()
        # and it can compact again (nothing left to win)
        assert native.client.compact().reclaimed == 0
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_a_frame_behind_the_compaction_is_served_by_the_next_call(
    name: str, image: Image
):
    """The copy serves the one compaction (the log region is its ground); what waits behind it is
    served by the next call of the pump, from the new place."""
    if name == "wasm32":
        pytest.skip("wasm32 serves the whole batch from its own code")
    dirty = with_garbage(image)
    machine = lw.machine_of(name, dirty)
    try:
        native = lw.Channel(machine)
        native.client.ping()
        native.write(frames.encode(frames.COMPACT, 1, 0, b""))
        native.write(frames.encode(frames.PING, 2, 0, b""))
        native.pump()
        first = frames.Decoder().feed(native.read())
        assert [f.sequence for f in first] == [1]
        native.pump()
        second = frames.Decoder().feed(native.read())
        assert [f.sequence for f in second] == [2]
        assert struct.unpack("<Q", machine.read(native.area + 240, 8))[0] == 1
    finally:
        machine.close()


def test_code_is_not_moved_from_under_an_interpreter_that_is_below_the_pump(
    name: str, image: Image
):
    """When the interpreter called the pump at a safe point it holds the addresses of the leaves:
    the compaction that would move them is declined, the arena untouched."""
    if name == "wasm32":
        pytest.skip("wasm32 keeps its code outside the arena: nothing to protect")
    dirty = with_garbage(image)
    machine = lw.machine_of(name, dirty)
    try:
        native = lw.Channel(machine)
        native.client.ping()
        before = native.arena()
        machine.write(native.area + 256, struct.pack("<I", 1))  # in_run
        with pytest.raises(Declined) as caught:
            native.client.compact()
        assert caught.value.reason == 13
        assert native.arena() == before
        assert native.client.digest() == dirty.digest()
        # what the dump is for: the canonical form is still there to be taken
        assert native.client.emit()[0] == dirty.compact().to_bytes()
    finally:
        machine.close()


def test_a_pump_without_room_for_a_copy_declines_instead_of_moving(
    name: str, image: Image
):
    if name == "wasm32":
        pytest.skip("wasm32 has no copy to make")
    dirty = with_garbage(image)
    machine = lw.machine_of(name, dirty, lw.link.LiveSpec(log=4096))
    try:
        native = lw.Channel(machine)
        native.client.ping()
        before = native.arena()
        with pytest.raises(Declined) as caught:
            native.client.compact()
        assert caught.value.reason == 13
        assert native.arena() == before
    finally:
        machine.close()


def test_a_pinned_object_is_not_moved_natively(native: lw.Channel, raw: bytes):
    from pymergetic.rxftk.core.object import image_state

    base = Image.from_bytes(raw)
    pinned = Image.from_bytes(raw)
    pinned.set_state(
        resolved(pinned, "pymergetic.rxf.application.main"), image_state(1)
    )
    native.client.apply(physical.compile(base, pinned).data)
    before = native.arena()
    with pytest.raises(Declined) as caught:
        native.client.compact()
    assert caught.value.reason == 13
    assert native.arena() == before


def test_an_arena_committed_too_short_asks_for_pages_natively(
    name: str, image: Image, raw: bytes
):
    spec = lw.link.LiveSpec(committed=len(raw) + 4096)
    machine = lw.machine_of(name, image, spec)
    try:
        native = lw.Channel(machine)
        assert native.client.hello().committed == len(raw) + 4096
        base = Image.from_bytes(raw)
        result = Image.from_bytes(raw)
        parent = resolved(result, "pymergetic.rxf.application")
        for n in range(400):
            result.add(parent, f"grow{n}", 8, NONE, bytes(200))
        data = physical.compile(base, result).data
        before = native.arena()
        with pytest.raises(Refused) as caught:
            native.client.apply(data)
        assert caught.value.reason == 6
        assert native.arena() == before
        wanted = struct.unpack("<Q", machine.read(native.area + 232, 8))[0]
        assert wanted > len(raw) + 4096
        assert native.client.grow(wanted) == wanted
        assert native.client.apply(data) == result.digest()
        with pytest.raises(Declined):
            native.client.grow(1)
    finally:
        if hasattr(machine, "close"):
            machine.close()
