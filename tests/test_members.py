"""Declared relations: a Field that says which children are a list, or which objects point here."""

import pytest

from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.object import Image

SCHEMA = """
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="Follows"/>
  <Type name="Item" form="Form.Struct" size="8" align="8">
    <Field name="next" flags="FieldFlags.Optional|Ref" value_type="uint64_t"/>
  </Type>
  <Type name="Slot" form="Form.Struct" size="4" align="4">
    <Field name="index" value_type="uint32_t"/>
  </Type>
  <Type name="Box" form="Form.Struct" size="8" align="8">
    <Field name="size" value_type="uint64_t"/>
    <Field name="items" flags="FieldFlags.Children" value_type="Item"/>
    <Field name="slots" flags="FieldFlags.Children" value_type="Slot"/>
    <Field name="before" flags="FieldFlags.Inverse" value_type="Item" via="Item.next"/>
    <Field name="followers" flags="FieldFlags.Inverse" value_type="Item" via="Follows"/>
  </Type>
</delta>
"""

DATA = """
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Box name="box" size="3">
    <Item name="argument[10]"/>
    <Item name="argument[2]"/>
    <Item name="argument[1]"/>
    <Item name="output"/>
    <Item name="hidden" rxf:index="0"/>
    <Slot name="c" index="2"/>
    <Slot name="a" index="3"/>
    <Slot name="b" index="1"/>
  </Box>
  <Item name="x" next="box"/>
  <Item name="y"><rxf:ref role="Follows" target="box"/></Item>
</delta>
"""


@pytest.fixture
def image():
    image = Image.empty()
    image.apply(SCHEMA)
    image.apply(DATA)
    return image


def named(image, handles):
    return [image.get(h)["name"] for h in handles]


def test_a_children_field_lists_the_children_of_its_type_in_order(image):
    members = {
        image.get(f)["name"]: items for f, items in image.members(image.resolve("box"))
    }
    # `[n]` is a number: 1, 2, 10; a name without brackets sorts before the numbered ones of its own name
    assert named(image, members["items"]) == [
        "argument[1]",
        "argument[2]",
        "argument[10]",
        "hidden",
        "output",
    ]
    # a type with a field `index` is ordered by it, whatever the names say
    assert named(image, members["slots"]) == ["b", "c", "a"]


def test_an_inverse_field_finds_the_objects_that_point_here(image):
    members = {
        image.get(f)["name"]: items for f, items in image.members(image.resolve("box"))
    }
    assert named(image, members["before"]) == ["x"]  # through the field Item.next
    assert named(image, members["followers"]) == [
        "y"
    ]  # through a reference of the role Follows


def test_a_declared_relation_takes_no_place_in_the_payload(image):
    box = image.get(image.resolve("box"))
    assert len(box["payload"]) == 8
    assert dict(image.fields(image.resolve("box"))) == {"size": "3"}
    assert image.check() == []


def test_the_text_says_what_the_field_is_and_survives_a_round_trip(image):
    text = image.export()
    assert (
        'flags="FieldFlags.Children"' in text and 'flags="FieldFlags.Inverse"' in text
    )
    again = Image.empty()
    again.apply(text)
    assert again.digest() == image.digest()


def test_ordered_is_the_same_rule_for_any_siblings(image):
    kids = image.children(image.resolve("box"))
    assert named(image, image.ordered(kids))[:3] == [
        "argument[1]",
        "argument[2]",
        "argument[10]",
    ]


def bad(image, field, message):
    with pytest.raises(DeltaError) as caught:
        image.apply(
            f'<delta xmlns:rxf="urn:pymergetic:rxf"><Field rxf:parent="Box" name="more" {field}/></delta>'
        )
    assert message in str(caught.value)


def test_a_relation_that_does_not_make_sense_does_not_link(image):
    bad(
        image,
        'flags="FieldFlags.Children" value_type="Item"',
        "another field already lists the children",
    )
    bad(
        image,
        'flags="FieldFlags.Children|Inverse" value_type="Role"',
        "children or is an inverse, not both",
    )
    bad(image, 'flags="FieldFlags.Children|Ref" value_type="Role"', "holds no value")
    bad(
        image, 'flags="FieldFlags.Inverse" value_type="Role"', "names the Field or Role"
    )
    bad(
        image,
        'flags="FieldFlags.Inverse" value_type="Item" via="Item"',
        "neither a Field nor a Role",
    )
    bad(
        image,
        'flags="FieldFlags.Inverse" value_type="Slot" via="Item.next"',
        "reference Field of the value type",
    )
    bad(
        image,
        'flags="FieldFlags.Children" value_type="Role" via="Follows"',
        "via belongs to an inverse",
    )
    bad(
        image,
        'value_type="uint64_t" via="Follows" offset="0"',
        "via belongs to an inverse",
    )
