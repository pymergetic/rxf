"""The edit set (docs/27): one mutation is one delta.

What these pins hold:

* the set: create, rename, move, delete, put, ref, unset — every verb one mutation, one
  delta, and the transaction ``image.edit()`` is ONE .rxfd: all of it or none of it;
* the refusals before the delta exists (docs/27): derived is never written, a declared list
  takes no value, ``put`` writes leaves and ``ref`` is the only place a handle may be
  written, ``unset`` is for OPTIONAL fields only, a name is checked as any other name, a
  delete is refused while anything else points at it — each refusal one of the link check's
  codes, said as the delta would say it;
* the text form is the same ops written down: the .rxfd of a committed edit applies to the
  same start and arrives at the same world (the digests agree, byte for byte);
* the reading face is what editing needs to see (docs/27): one leaf by name through the
  generic reader, where it stands, what it is — and the link check is the edit validator;
* the mounted face carries the set as methods (docs/27): a property assignment is put, a
  reference goes through ref(), and the refusals are the same refusals.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.core.object import Image

# One small type of two leaves: a number and an OPTIONAL reference — the two kinds of value
# there are (docs/27). Fields name their offset; a name is checked as any other name.
WORLD = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Thing" form="Form.Struct" size="16" align="8">
    <Field name="count" value_type="uint32_t" offset="0"/>
    <Field name="other" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Type name="Module" form="Form.Struct" size="0" align="1"/>
  <Module name="game" rxf:kind="NodeKind.Module"/>
  <Thing name="player" rxf:parent="game" count="1"/>
</delta>"""


@pytest.fixture
def image() -> Image:
    result = Image.empty()
    result.apply(WORLD)
    return result


def at(image: Image, path: str) -> int:
    """The handle of a path: probes name things, the image identifies them."""
    handle = image.resolve(path)
    assert handle is not None, path
    return handle


def texts(image: Image, path: str) -> dict[str, str]:
    """The fields of one object as the generic reader spells them (zero and unset left out)."""
    return dict(image.fields(at(image, path)))


def test_create_answers_the_handle_and_the_verbs_write_one_mutation_each(image):
    handle = image.create("Thing", "game", "boss")
    assert handle == image.resolve("game.boss")
    assert image.path(handle) == "game.boss" and image.type_name(handle) == "Thing"
    # size and align come from the Type: the cell is made at the type's size
    assert len(image.get(handle)["payload"]) == 16
    # put is one leaf, by the field's width and sign
    image.put("game.boss", "count", 7)
    assert texts(image, "game.boss") == {"count": "7"}
    image.rename("game.boss", "villain")
    assert (
        image.resolve("game.villain") == handle and image.resolve("game.boss") is None
    )
    image.move("game.villain", "game.player")  # under another parent, not a sibling
    assert image.path(handle) == "game.player.villain"
    image.delete("game.player.villain")
    assert image.resolve("game.player.villain") is None


def test_ref_is_the_only_place_a_handle_may_be_written(image):
    boss = image.create("Thing", "game", "boss")
    image.ref("game.player", "other", boss)
    assert texts(image, "game.player")["other"] == "game.boss"
    # a leaf never takes a handle and a reference never takes a leaf value
    with pytest.raises(Exception, match="written by ref"):
        image.put("game.player", "other", boss)
    with pytest.raises(Exception, match="only a reference field"):
        image.ref("game.player", "count", boss)
    # unset is for OPTIONAL fields only
    with pytest.raises(Exception, match="only an optional field"):
        image.unset("game.player", "count")
    image.unset("game.player", "other")
    assert "other" not in texts(image, "game.player")  # an unset reference is left out


def test_the_refusals_come_before_the_delta_exists(image):
    # derived is never written (the ground's own words: descriptor_tag, field_count)
    with pytest.raises(Exception, match="derived is never written"):
        image.put("Thing", "field_count", 3)
    # a name is checked as any other name: no path separator, nothing empty
    with pytest.raises(Exception, match="BadName"):
        image.rename("game.player", "has.a.dot")
    with pytest.raises(Exception, match="BadName"):
        image.rename("game.player", "")
    # a name is taken among the siblings
    image.create("Thing", "game", "boss")
    with pytest.raises(Exception, match="NameTaken"):
        image.rename("game.boss", "player")
    with pytest.raises(Exception, match="NameTaken"):
        image.create("Thing", "game", "player")
    # a delete is refused while anything else points at it — the delta's own words
    image.ref("game.player", "other", "game.boss")
    with pytest.raises(Exception, match="still in use"):
        image.delete("game.boss")
    with pytest.raises(Exception, match="no such object"):
        # and a ref points at a live object
        image.ref("game.player", "other", "game.nope")
    # a delete is refused while anything else points at it — even a type with its objects
    with pytest.raises(Exception, match="still in use"):
        image.delete("Thing")  # player and boss are objects of the removed type
    # and the ground is never an ordinary object
    with pytest.raises(Exception, match="not an ordinary object"):
        image.delete("")  # the ground


def test_the_reading_face_is_what_editing_needs_to_see(image):
    player = image.resolve("game.player")
    # one leaf by name, through the generic reader: its text, or None where it is left out
    assert image.get(player, "count") == "1"
    assert image.get(player, "other") is None  # zero and unset are left out
    image.ref("game.player", "other", player)
    assert image.get(player, "other") == "game.player"
    # where it stands, what it is — and the link check is the edit validator
    assert image.path(player) == "game.player" and image.type_name(player) == "Thing"
    assert image.check() == []


def test_the_transaction_is_one_delta_and_the_text_form_is_the_same_ops(image):
    with image.edit() as e:
        e.create("Thing", "game", "boss")
        e.put("game.boss", "count", 9)
        e.ref(
            "game.player", "other", "game.boss"
        )  # paths name things: a target may point forward
        delta = e.commit()
    # one .rxfd: the same ops written down
    assert delta.startswith("<?xml") and delta.count("<rxf:set") == 2
    assert '<Thing name="boss"' in delta and 'path="game.boss"' in delta
    # and the same world: the start plus the delta arrives where the edit arrived
    fresh = Image.empty()
    fresh.apply(WORLD)
    fresh.apply(delta)
    assert fresh.digest() == image.digest()


def test_all_of_it_or_none_of_it(image):
    before = image.digest()
    with pytest.raises(Exception, match="no such object"), image.edit() as e:
        e.create("Thing", "game", "boss")
        e.put("game.nope", "count", 1)
        e.commit()  # the failing operation refuses the whole delta
    assert image.digest() == before and image.resolve("game.boss") is None
    with image.edit() as e:
        e.create("Thing", "game", "boss")
        e.rollback()
    assert image.resolve("game.boss") is None and not e.open
    with image.edit() as e:
        e.create(
            "Thing", "game", "boss"
        )  # no commit: an abandoned edit is not a mutation
    assert image.resolve("game.boss") is None


def test_the_mounted_classes_carry_the_edit_set_as_methods(image):
    from typing import cast

    from pymergetic.rxftk.core import mount as core_mount
    from pymergetic.rxftk.mount import Mounted

    synth = core_mount.Synthesizer(image)
    player = cast("Mounted", synth.object(at(image, "game.player")))
    # a property assignment is put (docs/27): alloc.size = 4096
    player.count = 41
    assert texts(image, "game.player")["count"] == "41"
    with pytest.raises(Exception, match="written by ref"):
        player.other = 3  # a reference is written by ref() only
    boss = image.create("Thing", "game", "boss")
    player.ref("other", cast("Mounted", synth.object(boss)))  # a mounted object is its id
    assert texts(image, "game.player")["other"] == "game.boss"
    player.unset("other")
    assert "other" not in texts(image, "game.player")
    player.rename("hero")
    assert image.resolve("game.hero") == player.id
    image.create("Thing", "game", "slots")
    player.move("game.slots")  # under another parent — a mounted object names it
    assert image.path(player.id) == "game.slots.hero"
    player.delete()
    assert image.resolve("game.slots.hero") is None
