"""Roles are objects: a reference names its role by path and its place in a list by an index."""

import pytest

from pymergetic.rxftk.core.delta import DeltaError
from pymergetic.rxftk.core.object import Image

NS = 'xmlns:rxf="urn:pymergetic:rxf"'

BASE = f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="operand"/>
  <Thing name="a"/>
  <Thing name="user">
    <rxf:ref role="operand" index="0" kind="RefKind.Data" target="a"/>
    <rxf:ref role="operand" index="1" kind="RefKind.Data" target="a"/>
  </Thing>
</delta>"""


@pytest.fixture
def image():
    result = Image.empty()
    result.apply(BASE)
    return result


def refuses(image, text, fragment):
    before = image.digest()
    with pytest.raises(DeltaError, match=fragment):
        image.apply(text)
    assert image.digest() == before


def test_a_role_is_a_path_and_an_index_is_a_position(image):
    refs = image.get(image.handle("user"))["refs"]
    a, operand = image.handle("a"), image.handle("operand")
    data = 1  # DATA
    assert refs == [(a, operand, 0, data, 0), (a, operand, 1, data, 0)]
    text = image.export()
    assert (
        'role="operand" kind="RefKind.Data" target="a"' in text
    )  # index 0 is the default
    assert 'role="operand" index="1" kind="RefKind.Data" target="a"' in text


def test_a_role_that_is_not_there_yet_resolves_when_it_arrives():
    image = Image.empty()
    image.apply(
        f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="a"><rxf:ref role="late" target="a"/></Thing>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="late"/>
</delta>"""
    )
    (target, role, _, _, _) = image.get(image.handle("a"))["refs"][0]
    assert target == image.handle("a") and role == image.handle("late")


def test_a_role_that_never_arrives_refuses_the_delta():
    image = Image.empty()
    refuses(
        image,
        f'<delta {NS}><Type name="T" form="Form.Struct" size="0" align="1"/>'
        '<T name="a"><rxf:ref role="nobody" target="a"/></T></delta>',
        "does not resolve",
    )


def test_a_role_in_use_cannot_be_deleted(image):
    refuses(
        image,
        f'<delta {NS}><rxf:delete path="operand"/></delta>',
        "role of a reference",
    )
    image.apply(
        f'<delta {NS}><rxf:delete path="user"/><rxf:delete path="operand"/></delta>'
    )
    assert image.resolve("operand") is None and image.check() == []


def test_compaction_keeps_roles_and_indices(image):
    image.apply(f'<delta {NS}><Thing name="gone"/><rxf:delete path="gone"/></delta>')
    compact = image.compact()
    refs = compact.get(compact.handle("user"))["refs"]
    assert [(compact.path(r[1]), r[2]) for r in refs] == [
        ("operand", 0),
        ("operand", 1),
    ]
    assert compact.digest() == image.digest() and compact.check() == []


def test_a_role_survives_the_text_round_trip(image):
    again = Image.empty()
    again.apply(image.export())
    assert again.digest() == image.digest() and again.export() == image.export()


def test_the_role_is_part_of_the_content(image):
    other = Image.empty()
    other.apply(BASE.replace('role="operand" index="1"', 'role="operand" index="2"'))
    assert other.digest() != image.digest()
