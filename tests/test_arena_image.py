"""The v7 arena as the model's Image: `arena_image.load` (docs/32 5.5).

The model is the world, the arena is what runs; `arena_of` writes the one from the other and `load`
reads it back through `pymergetic.rxfb` (the C core's reader). The properties here are the ones the
host's commands lean on: model -> arena -> model is the model again (by path, handles being the
loader's own), the arena of the loaded model is the arena it came from, and the digest of the
loaded model is the digest the live layer answers (tests/support.py `digest_of`).
"""

import dataclasses
import random
from pathlib import Path

import link
import programs
import pytest

from pymergetic import rxfb
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import arena_image, harness, physical

from .support import digest_of
from .test_live import edited

STARTER = Path(__file__).parents[1] / "starter"


@pytest.fixture(scope="module")
def world() -> Image:
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def raw(world: Image) -> bytes:
    return link.arena_of(world)


def worlds(world: Image, seed: int) -> tuple[Image, bytes]:
    """The starter with a few random edits (adds, removes, retargets, rewrites, state words) and
    its arena, seed 0 being the starter itself. An edit that leaves a reference to an object it
    removed is a world the writer refuses (a dangling ref), so another is drawn."""
    if not seed:
        return world, link.arena_of(world)
    rng = random.Random(seed)
    while True:
        model = edited(Image.from_bytes(world.to_bytes()), rng)
        try:
            return model, link.arena_of(model)
        except ValueError:
            continue


def padded(before: physical.Record, after: physical.Record) -> bool:
    """`after` is `before` with the arena's room after the value: the length of a payload is not
    in the arena (docs/32 1.2), so the bytes are the same and the tail is zeros."""
    same = dataclasses.replace(after, payload=after.payload[: len(before.payload)])
    return same == before and not any(after.payload[len(before.payload) :])


@pytest.mark.parametrize("seed", range(4))
def test_model_arena_model_is_the_model(world: Image, seed: int):
    model, arena = worlds(world, seed)
    loaded = arena_image.load(arena, like=model)
    # `like` is the world the arena came from: the model comes back object for object
    assert physical.records(loaded) == physical.records(model)
    loaded.verify()
    # the arena of the loaded model is the arena it came from, byte for byte
    assert link.arena_of(loaded) == arena
    assert arena_image.digest_of(loaded) == digest_of(model)


@pytest.mark.parametrize("seed", range(4))
def test_without_a_predecessor_the_model_is_the_filled_one_up_to_the_rooms(
    world: Image, seed: int
):
    model, arena = worlds(world, seed)
    loaded = arena_image.load(arena)
    before, entry = physical.records(programs.filled(model))
    after, loaded_entry = physical.records(loaded)
    assert loaded_entry == entry
    assert before.keys() == after.keys()
    assert [path for path in before if not padded(before[path], after[path])] == []
    # a loaded world needs no predecessor to be the one the live layer runs
    assert link.arena_of(loaded) == arena
    assert arena_image.digest_of(loaded) == digest_of(model)


def test_the_fixed_layouts_keep_their_length(world: Image, raw: bytes):
    """A scalar's length is the layout's own (`uint32_t` is 4 bytes), whatever room the arena
    gave it: the loaded model needs no predecessor for them."""
    loaded = arena_image.load(raw)
    filled = programs.filled(world)
    for path in (
        "pymergetic.rxf.application.discount",
        "pymergetic.rxf.application.tax_percent",
    ):
        found = loaded.resolve(path)
        assert found is not None
        assert (
            loaded.get(found)["payload"] == filled.get(filled.handle(path))["payload"]
        )


def test_what_the_arena_adds_around_the_world_is_not_loaded(raw: bytes):
    loaded = arena_image.load(raw)
    assert loaded.resolve("sys") is None
    assert loaded.resolve("honors") is None
    assert loaded.entry_path == "pymergetic.rxf.application.main"


def test_the_host_applies_what_the_loader_reads(world: Image, raw: bytes):
    """The loop the commands run: dump, load, edit the model, compile the difference against the
    dump's own digest, apply on the host build, load what it left."""
    base = arena_image.load(raw, like=world)
    result = Image.from_bytes(base.to_bytes())
    discount = result.resolve("pymergetic.rxf.application.discount")
    assert discount is not None
    result.set_payload(discount, (45).to_bytes(4, "little"))
    host = harness.HostImage(raw)
    digest = digest_of(world)
    compiled = physical.compile(base, result, base_digest=digest)
    assert (compiled.creates, compiled.sets, compiled.deletes) == (0, 1, 0)
    host.client.apply(compiled.data)
    after = host.image(like=world)
    found = after.resolve("pymergetic.rxf.application.discount")
    assert found is not None
    assert after.get(found)["payload"] == (45).to_bytes(4, "little")


def test_damaged_bytes_are_refused_by_the_reader(raw: bytes):
    with pytest.raises(rxfb.BadImage):
        arena_image.load(b"RXFB" + raw[4:])  # the v6 magic over v7 bytes
    with pytest.raises(rxfb.BadImage):
        arena_image.load(raw[:4096])  # a superblock and nothing else
