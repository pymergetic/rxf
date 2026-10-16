"""The undo log of the arena (docs/19): a transaction rolls back byte for byte."""

import random

import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image

from .snippets import STARTER


@pytest.fixture(scope="module")
def starter() -> Image:
    return tree.load(STARTER)


def small() -> Image:
    """A few objects, so that every kind of write happens often."""
    image = Image.arena(limit=1 << 26)
    image.bootstrap()
    return image


def live_handles(image: Image) -> list[int]:
    return [h for h in range(image.handle_count) if image.live(h)]


def edit(image: Image, rng: random.Random, step: int) -> bool:
    """One random low-level edit; False when the image refused it."""
    live = live_handles(image)
    free = [h for h in live if h > 40]  # not the ground
    kind = rng.randrange(8)
    try:
        if kind == 0 or not free:
            parent = rng.choice(live)
            refs = [(rng.choice(live), None, 0, 0, 0) for _ in range(rng.randrange(3))]
            payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
            image.add(
                parent, f"n{step}_{rng.randrange(10**6)}", payload=payload, refs=refs
            )
        elif kind == 1:
            image.remove(rng.choice(free))
        elif kind == 2:
            size = rng.choice([0, 8, 16, 100, 4096])
            image.set_payload(
                rng.choice(free), bytes(rng.randrange(256) for _ in range(size))
            )
        elif kind == 3:
            refs = [(rng.choice(live), None, 0, 0, 0) for _ in range(rng.randrange(4))]
            image.set_refs(rng.choice(free), refs)
        elif kind == 4:
            image.set_parent(rng.choice(free), rng.choice(live))
        elif kind == 5:
            image.set_state(rng.choice(free), rng.randrange(1 << 12))
        elif kind == 6:
            image.set_type(rng.choice(free), rng.choice(live))
        else:
            image.entry = rng.choice(live)
    except Exception:  # noqa: BLE001 - a refusal of the image is part of the test
        return False
    return True


def mutate(image: Image, rng: random.Random, steps: int) -> int:
    """Random edits; returns how many the image accepted."""
    return sum(edit(image, rng, step) for step in range(steps))


def snapshot(image: Image) -> tuple[bytes, list[int], int, int, str | None]:
    image.verify()
    names = [image.path(h) for h in live_handles(image)]
    return (
        image.to_bytes(),
        [image.free_head, len(names)],
        image.handle_count,
        image.frontier,
        image.entry_path,
    )


@pytest.mark.parametrize("seed", range(12))
def test_a_rollback_gives_back_the_arena_byte_for_byte(seed):
    rng = random.Random(seed)
    image = small()
    mutate(image, rng, 60)
    before = snapshot(image)
    names = sorted(image.path(h) for h in live_handles(image))
    image.begin()
    assert mutate(image, rng, 120) > 20
    assert snapshot(image) != before
    image.rollback()
    assert snapshot(image) == before
    assert sorted(image.path(h) for h in live_handles(image)) == names
    for path in names:  # the lookups were rebuilt
        assert image.path(image.handle(path)) == path


@pytest.mark.parametrize("seed", range(4))
def test_a_commit_keeps_what_was_done_and_the_next_rollback_goes_back_to_it(seed):
    rng = random.Random(100 + seed)
    image = small()
    mutate(image, rng, 40)
    start = snapshot(image)
    image.begin()
    mutate(image, rng, 60)
    image.commit()
    middle = snapshot(image)
    assert middle != start
    image.begin()
    mutate(image, rng, 60)
    image.rollback()
    assert snapshot(image) == middle


def test_a_rollback_on_the_starter_gives_back_its_digest(starter):
    rng = random.Random(7)
    image = Image.from_bytes(starter.to_bytes())
    digest, before = image.digest(), image.to_bytes()
    image.begin()
    assert mutate(image, rng, 300) > 50
    image.rollback()
    assert image.to_bytes() == before
    assert image.digest() == digest


def test_a_transaction_begins_once_and_ends_once():
    image = small()
    with pytest.raises(Exception, match="NoJournal"):
        image.commit()
    image.begin()
    with pytest.raises(Exception, match="NoJournal"):
        image.begin()
    image.rollback()
    with pytest.raises(Exception, match="NoJournal"):
        image.rollback()


def test_a_full_log_refuses_and_the_rollback_still_works():
    rng = random.Random(3)
    image = small()
    mutate(image, rng, 80)
    before = snapshot(image)
    image.begin(
        log_limit=4096 + 72 * 3
    )  # the bitmap takes one page, then room for three granules
    refused = 0
    for _ in range(40):
        try:
            image.set_payload(
                rng.choice([h for h in live_handles(image) if h > 40]), bytes(300)
            )
        except Exception as error:  # noqa: BLE001
            assert "JournalFull" in str(error)
            refused += 1
    assert refused > 0
    image.rollback()
    assert snapshot(image) == before


def test_a_refused_delta_leaves_the_starter_as_it_was(starter):
    image = Image.from_bytes(starter.to_bytes())
    before = image.to_bytes()
    with pytest.raises(Exception, match="does not resolve|no such"):
        image.apply(
            '<delta xmlns:rxf="urn:pymergetic:rxf"><rxf:set path="nothing.here"/></delta>'
        )
    assert image.to_bytes() == before
