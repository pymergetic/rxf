"""Images and deltas that are wrong in ways only hostile input is: the core says so and stays small."""

import struct

import pytest

from pymergetic.rxf.core.arena import HANDLE_FIELD, HANDLE_ROOT, HANDLE_TYPE, ArenaError
from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.object import Image

POINT = """
<delta>
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="y" value_type="uint64_t" offset="8"/>
  </Type>
  <Point name="p" x="1" y="2"/>
</delta>
"""


@pytest.fixture
def image():
    image = Image.empty()
    image.apply(POINT)
    assert image.check() == []
    return image


def messages(image):
    return [message for _, message in image.check()]


def test_a_name_is_text_without_controls(image):
    root = HANDLE_ROOT
    for bad in ("a\x01b", "a\x7fb", "a\nb", "a.b", ""):
        with pytest.raises(ArenaError):
            image.add(root, bad, 8, image.handle("Point"), bytes(16))
    image.add(root, "caf\u00e9-\u4e16\u754c", 8, image.handle("Point"), bytes(16))
    assert image.check() == []
    # An image whose name bytes are not UTF-8 does not load.
    data = bytearray(image.compact().to_bytes())
    at = data.index("caf\u00e9".encode())
    data[at + 3] = 0xFF
    with pytest.raises(ArenaError):
        Image.from_bytes(bytes(data))


def test_type_and_field_names_must_be_element_and_attribute_names(image):
    point = image.get(image.handle("Point"))
    image.add(HANDLE_ROOT, "two words", 4, HANDLE_TYPE, point["payload"])
    assert any("name of a Type" in m for m in messages(image))
    other = Image.empty()
    other.apply(POINT)
    field = other.get(other.handle("Point.x"))
    other.add(other.handle("Point"), "name", 5, HANDLE_FIELD, field["payload"])
    assert any("name of a Field" in m for m in messages(other))


def test_the_ground_cannot_be_altered(image):
    field_size = image.handle("Type.size")
    got = image.get(field_size)
    payload = bytearray(got["payload"])
    struct.pack_into("<Q", payload, 24, 4)  # the offset of Type.size
    image.set_payload(field_size, bytes(payload))
    assert any("differs from the bootstrap" in m for m in messages(image))


def test_an_enormous_field_does_not_make_an_enormous_layout(image):
    field = image.handle("Point.y")
    payload = bytearray(image.get(field)["payload"])
    struct.pack_into("<Q", payload, 32, 1 << 40)  # count
    image.set_payload(field, bytes(payload))
    assert any("out of range" in m for m in messages(image))
    payload = bytearray(image.get(field)["payload"])
    struct.pack_into("<Q", payload, 32, 1 << 30)  # in range, but not 2^30 values
    image.set_payload(field, bytes(payload))
    assert messages(image)  # refused, and quickly


def test_a_type_that_cannot_fit_the_image_has_no_objects():
    image = Image.empty(limit=1 << 26)
    with pytest.raises(DeltaError, match="limit of the image"):
        image.apply(
            '<delta><Type name="Huge" form="Form.Struct" size="1099511627776" align="8">'
            '<Field name="x" value_type="uint64_t"/></Type><Huge name="h"/></delta>'
        )
    assert image.check() == []
