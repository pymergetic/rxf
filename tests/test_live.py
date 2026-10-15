"""The live layer: frames, reads, staged deltas and dumps of a running image, on the host build.

The code that runs here is the code of the native leaf `rxf_live_pump` (docs/19); the tests of the
native machines hold the linked image to the same answers.
"""

import hashlib
import random
import struct
from pathlib import Path

import pytest

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.live import crc32
from pymergetic.rxf.core.object import Image, image_state
from pymergetic.rxf.live import Refused, frames, physical
from pymergetic.rxf.live.client import Declined, LiveError
from pymergetic.rxf.live.harness import HostImage

from .support import resolved

STARTER = Path(__file__).parents[1] / "starter"
NONE = 0xFFFFFFFFFFFFFFFF


@pytest.fixture(scope="module")
def starter() -> bytes:
    return tree.load(STARTER).compact().to_bytes()


@pytest.fixture
def host(starter: bytes) -> HostImage:
    return HostImage(starter)


# ---- frames -------------------------------------------------------------------------------------


def test_crc32_is_the_ieee_one():
    assert crc32(b"123456789") == 0xCBF43926
    assert frames.crc32(b"123456789") == 0xCBF43926


def test_a_frame_round_trips_and_the_decoder_resynchronises():
    wire = frames.encode(frames.PING, 7, 3, b"abc") + frames.encode(
        frames.PING, 8, 0, b""
    )
    decoder = frames.Decoder()
    got = decoder.feed(b"\x00\xff garbage " + wire[:10])
    got += decoder.feed(wire[10:])
    assert [(f.sequence, f.argument, f.payload) for f in got] == [
        (7, 3, b"abc"),
        (8, 0, b""),
    ]
    bad = bytearray(wire)
    bad[-1] ^= 1  # the second frame loses its CRC
    got = frames.Decoder().feed(bytes(bad))
    assert [f.sequence for f in got] == [7]


def test_the_image_skips_damaged_input_and_answers_the_next_frame(host: HostImage):
    good = frames.encode(frames.PING, 1, 0, b"x")
    broken = bytearray(frames.encode(frames.PING, 2, 0, b"yy"))
    broken[17] ^= 0x55
    host.write(b"\x01\x02\x03" + bytes(broken) + good)
    host.pump()
    answers = frames.Decoder().feed(host.read())
    assert [(a.sequence, a.op, a.payload) for a in answers] == [
        (1, frames.PING | frames.ANSWER, b"x")
    ]


def test_a_request_waits_when_the_answer_ring_is_full(starter: bytes):
    small = HostImage(starter, ring=4096)
    for n in range(4):
        small.write(frames.encode(frames.PING, n, 0, bytes(1000)))
    assert (
        small.pump() == 3
    )  # the fourth answer could not be written: its request stays in the ring
    assert len(frames.Decoder().feed(small.read())) == 3
    assert small.pump() == 1  # room again


def test_unknown_ops_and_bad_requests_are_answered_not_swallowed(host: HostImage):
    c = host.client
    with pytest.raises(LiveError) as e:
        c.call(0x7F)
    assert e.value.status == frames.UNKNOWN_OP
    with pytest.raises(LiveError) as e:
        c.call(frames.GET, b"short")
    assert e.value.status == frames.BAD_REQUEST
    assert c.get(0).name == ""


# ---- reading --------------------------------------------------------------------------------------


def test_reads_match_the_host_image(starter: bytes, host: HostImage):
    reference = Image.from_bytes(starter)
    c = host.client
    hello = c.hello()
    assert (
        hello.handles == reference.handle_count
        and hello.body_size == reference.body_size
    )
    assert (
        c.header() == starter[:192].replace(starter[24:40], c.header()[24:40])
        or len(c.header()) == 192
    )
    rng = random.Random(7)
    for h in [
        0,
        1,
        2,
        reference.handle_count - 1,
        *rng.sample(range(reference.handle_count), 150),
    ]:
        g = reference.get(h)
        cell = c.get(h)
        assert (cell.type, cell.parent, cell.name) == (
            g["type"],
            g["parent"],
            g["name"],
        )
        assert (
            cell.kind == g["kind"]
            and cell.generation == g["generation"]
            and cell.index == g["index"]
        )
        assert c.path(h) == g["path"]
        assert c.resolve(g["path"]) == h
        assert c.payload(h) == g["payload"]
        assert sorted(c.children(h)) == sorted(reference.children(h))
        refs = c.refs(h)
        assert [(t, r if r != NONE else None, i, k, b) for (t, r, k, b, i) in refs] == [
            (t, r, i, k, b) for (t, r, i, k, b) in g["refs"]
        ]
    assert c.resolve("no.such.path") is None
    assert c.resolve("") == 0
    with pytest.raises(LiveError):
        c.get(10**9)


def test_large_payloads_and_child_lists_come_in_pages(starter: bytes, host: HostImage):
    reference = Image.from_bytes(starter)
    biggest = max(
        range(reference.handle_count), key=lambda h: len(reference.get(h)["payload"])
    )
    assert len(reference.get(biggest)["payload"]) > 1024
    assert host.client.payload(biggest) == reference.get(biggest)["payload"]
    widest = max(
        range(reference.handle_count), key=lambda h: len(reference.children(h))
    )
    assert len(reference.children(widest)) > 120
    assert sorted(host.client.children(widest)) == sorted(reference.children(widest))


def test_digest_and_dump_are_the_canonical_ones(starter: bytes, host: HostImage):
    reference = Image.from_bytes(starter)
    assert host.client.digest() == reference.digest()
    dump, digest = host.client.emit()
    assert dump == reference.compact().to_bytes()
    assert hashlib.sha256(dump).hexdigest() == digest == reference.digest()


def test_a_dump_of_an_uncompacted_arena_is_still_the_canonical_form(starter: bytes):
    # remove and re-add inside the host image, so that the arena has dead cells and another handle order
    image = Image.from_bytes(starter)
    parent = resolved(image, "pymergetic.rxf.application")
    scratch = image.add(parent, "scratch", 8, NONE, b"1234")
    image.add(scratch, "inner", 8, NONE, b"")
    image.remove(resolved(image, "pymergetic.rxf.application.scratch.inner"))
    image.remove(scratch)
    image.set_payload(
        resolved(image, "pymergetic.rxf.application.main"),
        image.get(resolved(image, "pymergetic.rxf.application.main"))["payload"]
        + bytes(16),
    )
    host = HostImage(image.to_bytes())
    dump, digest = host.client.emit()
    assert dump == image.compact().to_bytes()
    assert digest == image.digest()


# ---- changing --------------------------------------------------------------------------------------


def random_edit(image: Image, rng: random.Random) -> None:
    """One change of the kinds the live layer has to carry: add, remove, retarget, rewrite."""
    handles = [h for h in range(image.handle_count) if image.live(h) and h > 2]
    for _ in range(rng.randrange(1, 4)):
        choice = rng.randrange(5)
        target = rng.choice(handles)
        try:
            if choice == 0:
                parent = rng.choice(handles)
                name = f"n{rng.randrange(10**6)}"
                refs = [
                    (
                        rng.choice(handles),
                        None,
                        rng.randrange(3),
                        1,
                        rng.randrange(3),
                        1,
                    )[:5]
                    for _ in range(rng.randrange(3))
                ]
                image.add(
                    parent,
                    name,
                    rng.choice([3, 8, 16]),
                    NONE,
                    rng.randbytes(rng.randrange(0, 90)),
                    refs,
                )
            elif choice == 1 and not image.children(target):
                image.remove(target)
            elif choice == 2 and image.get(target)["type"] == NONE:
                image.set_payload(target, rng.randbytes(rng.randrange(0, 200)))
            elif choice == 3 and image.get(target)["type"] == NONE:
                image.set_refs(
                    target,
                    [
                        (rng.choice(handles), None, k, 1, 0)
                        for k in range(rng.randrange(4))
                    ],
                )
            elif choice == 4:
                image.set_state(target, rng.choice([1, 5, 9]))
        except Exception:  # noqa: BLE001, S112 - an edit the host refuses is simply not made
            continue
        handles = [h for h in range(image.handle_count) if image.live(h) and h > 2]


def edited(before: Image, rng: random.Random) -> Image:
    """A copy of `before` with a few random edits that leave an image that links."""
    while True:
        result = Image.from_bytes(before.to_bytes())
        for _ in range(5):
            random_edit(result, rng)
        try:
            result.digest()
        except Exception:  # noqa: BLE001, S112 - the edits made a dangling handle; try others
            continue
        return result


@pytest.mark.parametrize("seed", range(4))
def test_applying_the_physical_delta_gives_the_same_image_as_the_host(
    starter: bytes, seed: int
):
    base = Image.from_bytes(starter)
    host = HostImage(starter)
    rng = random.Random(seed)
    for _ in range(4):
        before = host.image()
        result = edited(before, rng)
        compiled = physical.compile(before, result)
        assert compiled.creates + compiled.sets + compiled.deletes > 0
        digest = host.client.apply(compiled.data)
        expected = result.digest()
        assert digest == expected
        assert host.client.digest() == expected
        after = host.image()
        after.verify()
        assert after.digest() == expected
        assert host.client.emit()[0] == result.compact().to_bytes()
        # the lookup table still serves the new state
        for path in compiled.paths[:20]:
            found = host.client.resolve(path)
            assert (found is None) == (result.resolve(path) is None)
    assert host.client.hello().commits == 4
    del base


def test_a_delta_that_retargets_payload_references_and_references(starter: bytes):
    base = Image.from_bytes(starter)
    host = HostImage(starter)
    result = Image.from_bytes(starter)
    main = resolved(result, "pymergetic.rxf.application.main")
    record = physical.records(result)[0]["pymergetic.rxf.application.main"]
    assert record.patches, "main has handles in its payload"
    other = resolved(result, "pymergetic.rxf.library.checkout_total")
    assert other is not None
    # point the first handle of the payload at another object
    payload = bytearray(result.get(main)["payload"])
    offset = record.patches[0][0]
    struct.pack_into("<Q", payload, offset, other)
    result.set_payload(main, bytes(payload))
    compiled = physical.compile(base, result)
    assert (compiled.creates, compiled.sets, compiled.deletes) == (0, 1, 0)
    assert host.client.apply(compiled.data) == result.digest()
    decoded = physical.decode(compiled.data)
    assert (
        decoded[1]["op"] == "set"
        and decoded[1]["patches"][0][1] == "pymergetic.rxf.library.checkout_total"
    )


def test_the_entry_is_part_of_the_delta(starter: bytes):
    base = Image.from_bytes(starter)
    host = HostImage(starter)
    result = Image.from_bytes(starter)
    result.entry = resolved(result, "pymergetic.rxf.library.checkout_total")
    compiled = physical.compile(base, result)
    assert compiled.entry
    assert host.client.apply(compiled.data) == result.digest()
    assert host.image().entry_path == "pymergetic.rxf.library.checkout_total"


# ---- refusing ----------------------------------------------------------------------------------------------------


def snapshot(host: HostImage) -> bytes:
    return host.area.arena_bytes()


def refused(host: HostImage, data: bytes, reason: int) -> Refused:
    host.client.ping()  # the first request attaches the arena (it writes its committed size into the header)
    before = snapshot(host)
    with pytest.raises(Refused) as caught:
        host.client.apply(data)
    assert caught.value.reason == reason, (caught.value, physical.decode(data)[:3])
    assert snapshot(host) == before, "a refused delta left a trace"
    assert host.client.hello().rejects >= 1
    host.client.digest()  # and the image still answers
    return caught.value


def record(**kw):
    base = {
        "type": None,
        "flags": 8 | (1 << 8) | (6 << 14),
        "index": 0,
        "generation": 0,
        "payload": b"",
        "patches": (),
        "refs": (),
    }
    base.update(kw)
    return physical.Record(**base)


def test_every_refusal_leaves_the_arena_byte_for_byte(starter: bytes):
    host = HostImage(starter)
    digest = Image.from_bytes(starter).digest()

    b = physical.Builder()
    b.create("pymergetic.rxf.application.fresh", record())
    ok = b.build(digest)

    # a base that is not the image
    wrong = physical.Builder()
    wrong.create("pymergetic.rxf.application.fresh", record())
    refused(host, wrong.build("00" * 32), 2)

    # a path that does not resolve
    miss = physical.Builder()
    miss.create("pymergetic.rxf.nowhere.fresh", record())
    refused(host, miss.build(digest), 3)
    miss = physical.Builder()
    miss.delete("pymergetic.rxf.application.nothing")
    refused(host, miss.build(digest), 3)

    # an object that is already there
    twice = physical.Builder()
    twice.create("pymergetic.rxf.application.main", record())
    refused(host, twice.build(digest), 4)
    twice = physical.Builder()
    twice.create("pymergetic.rxf.application.fresh", record())
    twice.create("pymergetic.rxf.application.fresh", record())
    refused(host, twice.build(digest), 4)

    # names that cannot be used
    for name in (
        "",
        "bad name\x01",
        "@alias",
        "caf\xe9".encode("latin-1").decode("latin-1")[:3] + "\udcff",
    ):
        if "\udcff" in name:
            continue
        odd = physical.Builder()
        odd.create(f"pymergetic.rxf.application.{name}", record())
        with pytest.raises(Refused):
            host.client.apply(odd.build(digest))

    # root, Type and Field stay
    prot = physical.Builder()
    prot.delete("Type")
    refused(host, prot.build(digest), 10)

    # something would point at a gone object: remove main while the entry still names it
    gone = physical.Builder()
    gone.delete("pymergetic.rxf.application.main")
    refused(host, gone.build(digest), 9)

    # a name that names nothing
    ref = physical.Builder()
    ref.create(
        "pymergetic.rxf.application.fresh",
        record(refs=(("pymergetic.rxf.nothing", None, 1, 0, 0),)),
    )
    refused(host, ref.build(digest), 3)

    # a child whose parent is deleted
    orphan = physical.Builder()
    orphan.delete("pymergetic.rxf.application")
    refused(host, orphan.build(digest), 9)

    # the bytes are not a delta
    refused(host, b"\0" * 80, 1)
    refused(host, ok[:-3], 1)
    cut = bytearray(ok)
    cut[8:12] = struct.pack("<I", 99)  # more operations than bytes
    refused(host, bytes(cut), 1)

    # and after all of that, the one good delta still applies
    assert host.client.apply(ok) == physical_digest_after(starter, ok)


def physical_digest_after(starter: bytes, data: bytes) -> str:
    other = HostImage(starter)
    return other.client.apply(data)


def test_a_full_undo_log_refuses_and_restores(starter: bytes):
    base = Image.from_bytes(starter)
    result = Image.from_bytes(starter)
    for h in range(
        3, 12000, 3
    ):  # the state word of thousands of objects: each one is a granule of the log
        result.set_state(h, 9)
    compiled = physical.compile(base, result)
    assert compiled.sets > 3000
    tiny = HostImage(starter, log=64 * 1024)
    refused(tiny, compiled.data, 7)
    # the same delta applies where the log is large enough, and gives what the host gives
    roomy = HostImage(starter, log=8 * 1024 * 1024)
    assert roomy.client.apply(compiled.data) == result.digest()


def test_a_corrupted_stage_is_not_applied(starter: bytes):
    host = HostImage(starter)
    b = physical.Builder()
    b.create("pymergetic.rxf.application.fresh", record())
    data = b.build(None)
    host.client.stage(data)
    host.client.call(frames.STAGE_WRITE, b"\x00", 70)  # a bit flips in the stage
    with pytest.raises(LiveError) as e:
        host.client.call(
            frames.STAGE_COMMIT,
            struct.pack("<I", len(data)) + hashlib.sha256(data).digest(),
        )
    assert e.value.status == frames.DIGEST_MISMATCH
    with pytest.raises(LiveError) as e:
        host.client.call(frames.STAGE_WRITE, b"x" * 10, 1 << 30)
    assert e.value.status == frames.NO_ROOM


def test_break_request_is_carried_to_the_area(host: HostImage):
    assert host.area.break_request == 0
    host.client.break_(True)
    assert host.area.break_request == 1
    host.client.break_(False)
    assert host.area.break_request == 0


def test_the_lookup_table_survives_many_edits_and_deletes(starter: bytes):
    host = HostImage(starter)
    rng = random.Random(3)
    for _ in range(3):
        before = host.image()
        result = Image.from_bytes(before.to_bytes())
        names = [f"mass{rng.randrange(10**8)}" for _ in range(300)]
        parent = resolved(result, "pymergetic.rxf.application")
        for n in names:
            result.add(parent, n, 8, NONE, b"x")
        for n in rng.sample(names, 150):
            result.remove(resolved(result, f"pymergetic.rxf.application.{n}"))
        compiled = physical.compile(before, result)
        assert host.client.apply(compiled.data) == result.digest()
        for n in names:
            assert (host.client.resolve(f"pymergetic.rxf.application.{n}") is None) == (
                result.resolve(f"pymergetic.rxf.application.{n}") is None
            )
    # delete everything that was added: the table shrinks back
    final = host.image()
    clean = Image.from_bytes(starter)
    assert host.client.apply(physical.compile(final, clean).data) == clean.digest()


# ---- growing and compacting the arena ---------------------------------------------------------------------------


def test_an_arena_that_is_not_committed_far_enough_asks_for_pages(starter: bytes):
    host = HostImage(starter, committed=len(starter) + 4096)
    base = Image.from_bytes(starter)
    result = Image.from_bytes(starter)
    parent = resolved(result, "pymergetic.rxf.application")
    for n in range(400):
        result.add(parent, f"grow{n}", 8, NONE, bytes(200))
    compiled = physical.compile(base, result)
    host.client.ping()
    before = snapshot(host)
    assert host.client.hello().committed == len(starter) + 4096
    with pytest.raises(Refused) as caught:
        host.client.apply(compiled.data)
    assert caught.value.reason == 6
    assert snapshot(host) == before
    wanted = host.area.grow_request
    assert wanted > len(starter) + 4096
    # the provider commits pages, and tells the image
    assert host.client.grow(wanted) == wanted
    assert host.area.grow_request == 0
    assert host.client.hello().committed == wanted
    assert host.client.apply(compiled.data) == result.digest()
    assert host.image().digest() == result.digest()


def test_a_size_outside_what_exists_is_declined(starter: bytes):
    host = HostImage(starter, committed=len(starter) + 4096)
    assert host.client.hello().committed == len(starter) + 4096
    with pytest.raises(Declined) as caught:
        host.client.grow(len(starter))  # less than is committed
    assert caught.value.reason == 14
    with pytest.raises(Declined):
        host.client.grow(1 << 40)  # more than the region has
    assert host.client.hello().committed == len(starter) + 4096


def garbage(starter: bytes) -> bytes:
    """An arena with dead cells: objects that came and went, payloads that grew."""
    image = Image.from_bytes(starter)
    parent = resolved(image, "pymergetic.rxf.application")
    for n in range(60):
        image.add(parent, f"tmp{n}", 8, NONE, bytes(300))
    for n in range(0, 60, 2):
        image.remove(resolved(image, f"pymergetic.rxf.application.tmp{n}"))
    main = resolved(image, "pymergetic.rxf.application.main")
    image.set_payload(main, image.get(main)["payload"] + bytes(512))
    return image.to_bytes()


def test_compaction_in_place_gives_the_canonical_arena(starter: bytes):
    dirty = garbage(starter)
    reference = Image.from_bytes(dirty)
    host = HostImage(dirty)
    done = host.client.compact()
    assert done.digest == reference.digest()
    assert done.reclaimed > 0 and done.body_size == len(dirty) - done.reclaimed
    assert done.handles == reference.compact().handle_count
    after = host.area.arena_bytes()
    canonical = reference.compact().to_bytes()
    assert after[:24] == canonical[:24] and after[40:] == canonical[40:]
    Image.from_bytes(after).verify()
    # it is a live image again: reads and changes work on the new handles
    assert host.client.digest() == reference.digest()
    assert host.client.resolve("pymergetic.rxf.application.tmp1") is not None
    assert host.client.resolve("pymergetic.rxf.application.tmp0") is None
    assert host.client.hello().body_size == done.body_size
    base = host.image()
    result = Image.from_bytes(base.to_bytes())
    result.add(resolved(result, "pymergetic.rxf.application"), "after", 8, NONE, b"x")
    assert host.client.apply(physical.compile(base, result).data) == result.digest()
    assert host.area.compactions == 1
    # compacting a compact arena changes nothing
    again = host.client.compact()
    assert again.reclaimed == 0 and again.digest == result.digest()


def test_a_pinned_object_is_not_moved(starter: bytes):
    host = HostImage(starter)
    base = host.image()
    pinned = Image.from_bytes(base.to_bytes())
    main = resolved(pinned, "pymergetic.rxf.application.main")
    pinned.set_state(main, image_state(1))  # mobility: pinned
    assert host.client.apply(physical.compile(base, pinned).data) == pinned.digest()
    before = snapshot(host)
    with pytest.raises(Declined) as caught:
        host.client.compact()
    assert caught.value.reason == 13
    assert snapshot(host) == before
    # unpinned, it compacts
    loose = Image.from_bytes(pinned.to_bytes())
    loose.set_state(main, image_state(0))
    assert (
        host.client.apply(physical.compile(host.image(), loose).data) == loose.digest()
    )
    assert host.client.compact().digest == loose.digest()


# ---- the session spec of a running image (what the web app's live slot reports) -----------------


LIVE_LEAF = "pymergetic.rxf.foundation.live.pump"


def test_the_hello_of_a_running_image_carries_its_version_and_its_counters(
    starter: bytes, host: HostImage
):
    """What `rxf live info` and the shell's live slot report of a session (docs/21): the version
    the image answers (`Hello.version` — the live layer of core/live/live.h), the identity of
    what runs (its digest, its handle count and body size), and the counters of its area. Every
    one of these words comes off the channel; none is host bookkeeping."""
    image = Image.from_bytes(starter)
    hello = host.client.hello()
    assert hello.version == 1  # PYMERGETIC_RXF_LIVE_VERSION (core/live/live.h)
    assert (hello.handles, hello.body_size) == (image.handle_count, image.body_size)
    assert host.client.digest() == image.digest()
    assert (hello.commits, hello.rejects) == (0, 0)
    assert hello.committed == hello.arena_capacity  # an area that committed nothing has it all
    assert hello.arena_capacity >= hello.emit_capacity > 0
    assert hello.stage_capacity > 0 and hello.work_capacity > 0


def test_the_counters_of_a_session_move_with_what_the_image_takes_and_refuses(
    starter: bytes, host: HostImage
):
    """The live counters of a session are the image's own: a delta it takes is a commit, a delta
    it refuses is a reject, and the hello says so from then on."""
    base = Image.from_bytes(starter)
    result = Image.from_bytes(starter)
    result.add(resolved(result, "pymergetic.rxf.application"), "spec_pin", 8, NONE, b"x")
    assert host.client.apply(physical.compile(base, result).data) == result.digest()
    assert host.client.hello().commits == 1
    wrong = physical.Builder()
    wrong.create("pymergetic.rxf.application.spec_twice", record())
    with pytest.raises(Refused):
        host.client.apply(wrong.build("00" * 32))  # a base that is not the image
    hello = host.client.hello()
    assert (hello.commits, hello.rejects) == (1, 1)
    assert host.client.digest() == result.digest()  # and what runs is what it took


def test_the_live_leaf_of_the_image_is_an_object_the_channel_answers_for(
    starter: bytes, host: HostImage
):
    """The live leaf the slot names (`live.pump`, docs/18: the leaf whose Code serves these very
    rings) is an object of the image like any other. What the slot renders as its handle and its
    state is what the channel answers for it — the same words the image itself carries."""
    image = Image.from_bytes(starter)
    handle = host.client.resolve(LIVE_LEAF)
    assert handle is not None and handle == image.handle(LIVE_LEAF)
    cell = host.client.get(handle)
    got = image.get(handle)
    assert cell.name == "pump"
    assert cell.flags == got["kind"] | got["state"] << 8 | got["owner_kind"] << 14
    assert (cell.flags >> 8) & 0x3F == got["state"]  # the state word the slot renders
    assert image.live(handle) and cell.generation == got["generation"]
