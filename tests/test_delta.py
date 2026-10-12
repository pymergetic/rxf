"""Deltas on the C++ core: typed fields, path references, atomic replay, canonical text."""

import hashlib

import pytest

from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.hash import sha256
from pymergetic.rxf.core.object import Image

from .support import resolved

TYPES = """
<delta>
  <Type name="uint8_t" form="Form.Uint" size="1" align="1"/>
  <Type name="uint16_t" form="Form.Uint" size="2" align="2"/>
  <Type name="int16_t" form="Form.Sint" size="2" align="2"/>
  <Type name="int64_t" form="Form.Sint" size="8" align="8"/>
  <Type name="bool" form="Form.Bool" size="1" align="1"/>
  <Type name="float" form="Form.Float" size="4" align="4"/>
  <Type name="double" form="Form.Float" size="8" align="8"/>
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="y" value_type="uint64_t" offset="8"/>
  </Type>
  <Type name="Link" form="Form.Struct" size="8" align="8">
    <Field name="target" value_type="uint64_t" flags="FieldFlags.Ref"/>
  </Type>
  <Type name="MaybeLink" form="Form.Struct" size="8" align="8">
    <Field name="target" value_type="uint64_t" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Type name="Mixed" form="Form.Struct" size="56" align="8">
    <Field name="small" value_type="uint8_t"/>
    <Field name="word" value_type="uint16_t" offset="2"/>
    <Field name="signed" value_type="int16_t" offset="4"/>
    <Field name="flag" value_type="bool" offset="6"/>
    <Field name="real" value_type="float" offset="8"/>
    <Field name="wide" value_type="double" offset="16"/>
    <Field name="big" value_type="int64_t" offset="24"/>
    <Field name="digest" value_type="uint8_t" offset="32" count="4"/>
    <Field name="pair" value_type="Point" offset="40" count="1"/>
  </Type>
  <Type name="Blob" form="Form.Struct" size="8" align="8" flags="TypeFlags.Variable">
    <Field name="length" value_type="uint64_t"/>
  </Type>
</delta>
"""


def fresh(extra=""):
    image = Image.empty()
    image.apply(TYPES)
    if extra:
        image.apply(extra)
    return image


def refuses(image, text, *fragments):
    before = image.digest(), image.export()
    with pytest.raises(DeltaError) as caught:
        image.apply(text)
    for fragment in fragments:
        assert fragment in str(caught.value), str(caught.value)
    assert (image.digest(), image.export()) == before  # the image did not change
    return str(caught.value)


def test_sha256_matches_the_standard():
    assert sha256(b"") == hashlib.sha256(b"").hexdigest()
    assert sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()
    big = bytes(range(256)) * 1000
    assert sha256(big) == hashlib.sha256(big).hexdigest()


def test_empty_image_is_the_ground_only():
    image = Image.empty()
    assert image.check() == []
    assert image.export().strip().splitlines() == [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<delta xmlns:rxf="urn:pymergetic:rxf" />',
    ]
    assert image.resolve("uint32_t") is not None
    assert image.type_name(image.handle("uint32_t")) == "Type"


def test_types_and_fields_round_trip_as_text():
    image = fresh()
    assert image.check() == []
    text = image.export()
    assert '<Type name="Point" form="Form.Struct" size="16" align="8">' in text
    assert '<Field name="y" value_type="uint64_t" offset="8" />' in text
    # field_count and owner_type are derived, never written.
    assert "field_count" not in text and "owner_type" not in text
    again = Image.empty()
    again.apply(text)
    assert again.export() == text
    assert again.digest() == image.digest()


def test_typed_scalars_and_forward_references():
    image = fresh(
        """
<delta>
  <Link name="a" target="b"/>
  <Link name="b" target="a"/>
  <Point name="p" x="3" y="18446744073709551615"/>
  <Mixed name="m" small="255" word="65535" signed="-32768" flag="true" real="1.5" wide="-0.25"
         big="-9223372036854775808" digest="00ff10ee"/>
</delta>"""
    )
    assert image.check() == []
    a, b = image.handle("a"), image.handle("b")
    assert dict(image.fields(a)) == {"target": "b"}
    assert dict(image.fields(b)) == {"target": "a"}
    assert dict(image.fields(image.handle("p"))) == {
        "x": "3",
        "y": "18446744073709551615",
    }
    fields = dict(image.fields(image.handle("m")))
    assert fields == {
        "small": "255",
        "word": "65535",
        "signed": "-32768",
        "flag": "1",
        "real": "1.5",
        "wide": "-0.25",
        "big": "-9223372036854775808",
        "digest": "00ff10ee",
    }
    text = image.export()
    again = Image.empty()
    again.apply(text)
    assert again.export() == text and again.digest() == image.digest()


def test_nested_struct_and_array_fields_get_dotted_names():
    image = fresh('<delta><Mixed name="m" pair.x="7" pair.y="9"/></delta>')
    assert dict(image.fields(image.handle("m"))) == {"pair.x": "7", "pair.y": "9"}


def test_variable_size_types_carry_a_tail():
    image = fresh('<delta><Blob name="b" length="3" rxf:data="aabbcc"/></delta>')
    assert dict(image.fields(image.handle("b"))) == {"length": "3", "data": "aabbcc"}
    refuses(image, '<delta><Point name="q" rxf:data="00"/></delta>', "fixed size")


def test_values_are_range_checked():
    image = fresh()
    refuses(image, '<delta><Mixed name="m" small="256"/></delta>', "does not fit")
    refuses(image, '<delta><Mixed name="m" signed="32768"/></delta>', "does not fit")
    refuses(image, '<delta><Mixed name="m" small="-1"/></delta>', "unsigned")
    refuses(image, '<delta><Mixed name="m" real="x"/></delta>', "not a number")
    refuses(image, '<delta><Mixed name="m" digest="00"/></delta>', "4 bytes")
    refuses(image, '<delta><Point name="p" z="1"/></delta>', "unknown attribute z")


def test_unresolved_and_mandatory_references_refuse_the_whole_delta():
    image = fresh()
    refuses(
        image,
        '<delta><Point name="ok" x="1"/><Link name="l" target="nowhere"/></delta>',
        "does not resolve",
        "nowhere",
    )
    assert image.resolve("ok") is None  # the first operation was rolled back, too
    refuses(image, '<delta><Link name="l"/></delta>', "mandatory reference")
    image.apply(
        '<delta><MaybeLink name="l"/></delta>'
    )  # an optional one may stay unset
    assert image.fields(image.handle("l")) == []


def test_operations_are_validated_in_order():
    image = fresh()
    refuses(
        image,
        '<delta><Point name="p"/><Point name="p"/></delta>',
        "cannot create",
        "NameTaken",
    )
    refuses(image, '<delta><Nothing name="p"/></delta>', "unknown type")
    refuses(image, '<delta><Point name="a.b"/></delta>', "BadName")
    refuses(
        image,
        '<delta><Point name="p" rxf:parent="missing"/></delta>',
        "parent does not exist",
    )


def test_the_ground_is_protected():
    image = fresh()
    refuses(
        image,
        '<delta><Type name="Type" form="Form.Meta" size="1" align="1"/></delta>',
        "NameTaken",
    )
    refuses(image, '<delta><rxf:set path="Type" size="1"/></delta>', "ground")
    refuses(image, '<delta><rxf:delete path="uint32_t"/></delta>', "ground")
    refuses(
        image,
        '<delta><Field rxf:parent="Type" name="extra" value_type="uint32_t"/></delta>',
        "ground",
    )


def test_set_changes_listed_fields_only():
    image = fresh('<delta><Point name="p" x="1" y="2"/><Point name="q"/></delta>')
    image.apply('<delta><rxf:set path="p" y="20"/></delta>')
    assert dict(image.fields(image.handle("p"))) == {"x": "1", "y": "20"}
    image.apply('<delta><rxf:set path="p" x="0" y="0"/></delta>')
    assert image.fields(image.handle("p")) == []
    refuses(image, '<delta><rxf:set path="nothing" x="1"/></delta>', "no such object")
    refuses(image, '<delta><rxf:set path="p" z="1"/></delta>', "unknown attribute")


def test_set_can_change_a_reference_and_object_properties():
    image = fresh(
        '<delta><Point name="p"/><Point name="q"/><Link name="l" target="p"/></delta>'
    )
    image.apply(
        '<delta><rxf:set path="l" target="q" rxf:mobility="Mobility.Pinned" rxf:generation="4"/></delta>'
    )
    info = image.get(image.handle("l"))
    assert dict(image.fields(image.handle("l"))) == {"target": "q"}
    assert info["generation"] == 4 and (info["state"] >> 4) & 3 == 1


def test_delete_removes_a_subtree_and_refuses_while_in_use():
    image = fresh(
        """<delta>
  <Point name="holder"/>
  <Point name="kept" rxf:parent="holder"/>
  <Link name="user" target="holder.kept"/>
</delta>"""
    )
    refuses(image, '<delta><rxf:delete path="holder"/></delta>', "still in use", "user")
    image.apply('<delta><rxf:delete path="user"/><rxf:delete path="holder"/></delta>')
    assert image.resolve("holder") is None and image.resolve("holder.kept") is None
    assert image.check() == []
    # A type cannot go while objects have it.
    image.apply('<delta><Point name="again"/></delta>')
    refuses(image, '<delta><rxf:delete path="Point"/></delta>', "still in use")


def test_references_between_objects_are_written_as_ref_elements():
    image = fresh(
        """<delta>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="callee"/>
  <Role name="argument"/>
  <Point name="a"/>
  <Point name="b">
    <rxf:ref kind="RefKind.Call" target="a"/>
    <rxf:ref role="argument" index="3" kind="RefKind.Type" binding="Binding.Optional" target="a"/>
    <rxf:ref role="later" kind="RefKind.Data" target="a"/>
  </Point>
  <Role name="later"/>
</delta>"""
    )
    a, argument, later = (
        image.handle("a"),
        image.handle("argument"),
        image.handle("later"),
    )
    refs = image.get(image.handle("b"))["refs"]
    # (target, role, index, kind, binding)
    assert refs == [(a, None, 0, 0, 0), (a, argument, 3, 3, 1), (a, later, 0, 1, 0)]
    text = image.export()
    assert '<rxf:ref kind="RefKind.Call" target="a" />' in text
    assert (
        '<rxf:ref role="argument" index="3" kind="RefKind.Type" binding="Binding.Optional" target="a" />'
        in text
    )
    assert '<rxf:ref role="later" kind="RefKind.Data" target="a" />' in text
    assert '<Point name="a" />' in text
    refuses(
        image,
        '<delta><Point name="c"><rxf:ref role="nobody" target="a"/></Point></delta>',
        "does not resolve",
    )
    refuses(
        image,
        '<delta><Point name="c"><rxf:ref target="zz"/></Point></delta>',
        "does not resolve",
    )


def test_object_properties_survive_a_round_trip():
    image = fresh(
        '<delta><Point name="p" rxf:kind="NodeKind.Code" rxf:owner="OwnerKind.Build" rxf:generation="9" rxf:mobility="Mobility.Relocatable" x="2"/></delta>'
    )
    info = image.get(image.handle("p"))
    assert info["kind"] == 7 and info["owner_kind"] == 3 and info["generation"] == 9
    again = Image.empty()
    again.apply(image.export())
    assert again.digest() == image.digest()
    assert (
        'rxf:kind="NodeKind.Code" rxf:owner="OwnerKind.Build" rxf:generation="9" rxf:mobility="Mobility.Relocatable"'
        in image.export()
    )


def test_canonical_text_does_not_depend_on_creation_order():
    one = fresh(
        '<delta><Point name="a" x="1"/><Point name="b" x="2"/><Link name="c" target="a"/></delta>'
    )
    two = fresh(
        '<delta><Link name="c" target="a"/><Point name="b" x="2"/><Point name="a" x="1"/></delta>'
    )
    assert one.export() == two.export()
    assert one.digest() == two.digest() and one.delta_digest() == two.delta_digest()


def test_delta_stacks_equal_one_total_delta():
    stacked = Image.empty()
    stacked.apply(TYPES)
    stacked.apply('<delta><Point name="p" x="1"/></delta>')
    stacked.apply('<delta><Link name="l" target="p"/></delta>')
    total = Image.empty()
    total.apply(
        TYPES.replace("</delta>", "")
        + '<Point name="p" x="1"/><Link name="l" target="p"/></delta>'
    )
    assert stacked.export() == total.export()
    assert stacked.digest() == total.digest()


def test_compaction_rewrites_handles_held_in_payloads():
    image = fresh()
    for i in range(30):
        image.apply(f'<delta><Point name="pad{i}"/></delta>')
    image.apply(
        '<delta><Point name="target" x="5"/><Link name="l" target="target"/></delta>'
    )
    image.apply(
        "<delta>"
        + "".join(f'<rxf:delete path="pad{i}"/>' for i in range(30))
        + "</delta>"
    )
    compact = image.compact()
    assert compact.check() == []
    assert dict(compact.fields(compact.handle("l"))) == {"target": "target"}
    assert compact.handle_count < image.handle_count
    # The handle inside the payload is the new one.
    payload = compact.get(compact.handle("l"))["payload"]
    assert int.from_bytes(payload, "little") == compact.handle("target")


def test_check_finds_broken_images():
    image = Image.empty()
    image.apply(TYPES)
    image.apply('<delta><Link name="l" target="uint32_t"/></delta>')
    link = image.handle("l")
    # Point the link at a handle that is not live.
    image.set_payload(link, (4000).to_bytes(8, "little"))
    problems = image.check()
    assert any("dead handle" in message for _, message in problems)
    # A payload of the wrong size.
    image.set_payload(link, b"\x00" * 4)
    assert any("does not fit" in message for _, message in image.check())


def test_type_checks():
    image = Image.empty()
    refuses(
        image,
        '<delta><Type name="Bad" form="Form.Struct" size="4" align="4"><Field name="a" value_type="uint64_t"/></Type></delta>',
        "exceeds its type",
    )
    refuses(
        image,
        '<delta><Type name="Bad" form="Form.Struct" size="16" align="8">'
        '<Field name="a" value_type="uint64_t"/><Field name="b" value_type="uint64_t" offset="4"/></Type></delta>',
        "overlaps",
        "misaligned",
    )
    refuses(
        image,
        '<delta><Type name="Bad" form="Form.Struct" size="3" align="3"/></delta>',
        "power of two",
    )
    refuses(
        image,
        '<delta><Type name="Bad" form="Form.Bad" size="3" align="1"/></delta>',
        "Form.Bad is not an object",
    )
    refuses(
        image,
        '<delta><Type name="Bad" form="Form.Struct" size="8" align="8">'
        '<Field name="a" value_type="uint32_t" flags="FieldFlags.Ref"/></Type></delta>',
        "reference field must be one uint64",
    )
    refuses(
        image,
        '<delta><Type name="Dup" form="Form.Struct" size="0" align="1"/><Type name="Dup" form="Form.Struct" size="0" align="1" rxf:parent="Type"/></delta>',
        "ground",
    )


def test_type_names_are_unique_across_modules():
    image = Image.empty()
    image.apply(
        '<delta><Module name="m"/></delta>'.replace("<Module", "<Type").replace(
            'name="m"', 'name="Module" form="Form.Struct" size="0" align="1"'
        )
    )
    image.apply('<delta><Module name="a"/><Module name="b"/></delta>')
    refuses(
        image,
        '<delta><Type name="T" form="Form.Struct" size="0" align="1" rxf:parent="a"/><Type name="T" form="Form.Struct" size="0" align="1" rxf:parent="b"/></delta>',
        "more than one Type object",
    )


# ---- the text ----------------------------------------------------------------


def test_parser_reads_comments_entities_and_character_references():
    image = fresh()
    image.apply(
        '<?xml version="1.0"?><!-- c --><delta><!-- d --><Point name="p&#120;" x="&#49;"/></delta>'
    )
    assert image.resolve("px") is not None
    assert dict(image.fields(image.handle("px"))) == {"x": "1"}


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "No document element"),
        ("<delta>", "tags mismatch"),
        ("<delta></gamma>", "tags mismatch"),
        ('<delta><Point name="p" name="q"/></delta>', "twice"),
        ("<delta><Point name=p/></delta>", "attribute"),
        ("<delta>text</delta>", "text is not allowed"),
        ("<other/>", "must be <delta>"),
        ('<delta a="1"/>', "takes only xmlns:rxf"),
        ("<!DOCTYPE x><delta/>", "DTD"),
        ("<delta><Point/></delta>", "needs a name"),
        ('<delta><Point name="p" rxf:bogus="1"/></delta>', "unknown attribute"),
        ('<delta><Point name="p" rxf:kind="NodeKind.Nope"/></delta>', "unknown kind"),
        ("<delta><rxf:set/></delta>", "needs a path"),
        ('<delta><rxf:delete path="x"><a/></rxf:delete></delta>', "<rxf:delete>"),
        ("<delta><rxf:ref/></delta>", "unknown element"),
    ],
)
def test_parser_refuses_malformed_text(text, fragment):
    image = Image.empty()
    with pytest.raises(DeltaError) as caught:
        image.apply(text)
    assert fragment in str(caught.value)


def test_parser_reports_the_line():
    image = Image.empty()
    with pytest.raises(DeltaError) as caught:
        image.apply('<delta>\n\n  <Point name="p" x=1/>\n</delta>')
    assert "line 3" in str(caught.value)


def test_special_characters_in_values_are_escaped_and_survive():
    image = fresh('<delta><Point name="p&lt;q"/></delta>')
    assert image.resolve("p<q") is not None
    text = image.export()
    assert 'name="p&lt;q"' in text
    again = Image.empty()
    again.apply(text)
    assert again.digest() == image.digest()


def test_digest_is_stable_and_follows_content():
    one = fresh('<delta><Point name="p" x="1"/></delta>')
    two = fresh('<delta><Point name="p" x="1"/></delta>')
    other = fresh('<delta><Point name="p" x="2"/></delta>')
    assert one.digest() == two.digest() != other.digest()
    assert len(one.digest()) == 64
    assert one.delta_digest() == hashlib.sha256(one.export().encode()).hexdigest()


def test_the_entry_is_part_of_the_delta():
    image = fresh('<delta><Point name="main" x="1" y="2"/></delta>')
    assert image.entry is None
    image.apply('<delta><rxf:entry path="main"/></delta>')
    assert image.entry_path == "main"
    text = image.export()
    assert '<rxf:entry path="main" />' in text
    again = Image.empty()
    again.apply(text)
    assert again.entry_path == "main" and again.digest() == image.digest()
    # It survives compaction (the handle changes, the path does not).
    assert image.compact().entry_path == "main"
    refuses(image, '<delta><rxf:entry path="nothing"/></delta>', "no such object")
    image.apply('<delta><rxf:entry path=""/></delta>')
    assert image.entry is None
    image.apply('<delta><rxf:entry path="main"/></delta>')
    refuses(
        image,
        '<delta><rxf:delete path="main"/><rxf:entry path="main"/></delta>',
        "in use",
        "the entry of the image",
    )
    image.apply('<delta><rxf:entry path=""/><rxf:delete path="main"/></delta>')
    assert image.resolve("main") is None


ORDERED = """
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Point name="list" x="0" y="0">
    <Point name="hidden" x="1" y="0"/>
    <Point name="argument[0]" rxf:index="1" x="2" y="0"/>
    <Point name="argument[1]" rxf:index="2" x="3" y="0"/>
  </Point>
</delta>
"""


def test_an_index_orders_siblings_and_survives_text_and_compaction():
    image = fresh(ORDERED)
    get = lambda name: image.get(resolved(image, "list." + name))["index"]
    assert (get("hidden"), get("argument[0]"), get("argument[1]")) == (0, 1, 2)
    text = image.export()
    assert (
        'name="argument[0]" rxf:index="1"' in text
        and 'name="hidden" rxf:index' not in text
    )
    again = Image.empty()
    again.apply(text)
    assert (
        again.digest() == image.digest()
        and again.delta_digest() == image.delta_digest()
    )
    kept = image.compact()
    assert kept.get(resolved(kept, "list.argument[1]"))["index"] == 2
    image.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf"><rxf:set path="list.hidden" rxf:index="7"/></delta>'
    )
    assert get("hidden") == 7


def test_an_index_that_is_not_a_number_is_refused():
    image = fresh()
    with pytest.raises(DeltaError, match="bad index"):
        image.apply(
            '<delta xmlns:rxf="urn:pymergetic:rxf"><Point name="p" rxf:index="x" x="0" y="0"/></delta>'
        )


def test_a_reference_that_repeats_a_field_does_not_link():
    image = fresh()
    refuses(
        image,
        """<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="peer"/>
  <Link name="b" target="c"><rxf:ref role="peer" target="c"/></Link>
  <Link name="c" target="c"/>
</delta>""",
        "repeats the field target",
    )
