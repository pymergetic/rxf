"""The words of the format (core.kinds), enumerations of the data, and aliases."""

import enum

import pytest

from pymergetic.rxf.core import kinds
from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.object import Image

from .support import resolved

DELTA = """<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Module" form="Form.Struct" size="0" align="1" node_kind="NodeKind.Module"/>
  <Type name="AliasFlags" form="Form.Enum" size="4" align="4" flags="TypeFlags.Bitset">
    <Enumerator name="Reexport" value="1"/>
  </Type>
  <Type name="Alias" form="Form.Struct" size="16" align="8" node_kind="NodeKind.Alias">
    <Field name="target" value_type="uint64_t" flags="FieldFlags.Ref"/>
    <Field name="flags" value_type="AliasFlags" offset="8"/>
  </Type>
  <Type name="Color" form="Form.Enum" size="4" align="4">
    <Enumerator name="Red" value="1"/>
    <Enumerator name="Green" value="2"/>
  </Type>
  <Type name="Perm" form="Form.Enum" size="4" align="4" flags="TypeFlags.Bitset">
    <Enumerator name="R" value="1"/>
    <Enumerator name="W" value="2"/>
    <Enumerator name="X" value="4"/>
  </Type>
  <Type name="Pixel" form="Form.Struct" size="16" align="8">
    <Field name="color" value_type="Color"/>
    <Field name="perm" value_type="Perm" offset="4"/>
    <Field name="to" value_type="uint64_t" flags="FieldFlags.Optional|Ref" offset="8"/>
  </Type>
  <Module name="shapes">
    <Module name="deep" rxf:kind="NodeKind.Module">
      <Alias name="s" target="shapes"/>
      <Pixel name="p" color="Color.Green" perm="Perm.W|R" to="@s.target"/>
    </Module>
    <Pixel name="target"/>
  </Module>
</delta>"""

ENUMS = [
    (kinds.NodeKind, enum.IntEnum),
    (kinds.RefKind, enum.IntEnum),
    (kinds.Binding, enum.IntEnum),
    (kinds.OwnerKind, enum.IntEnum),
    (kinds.Mobility, enum.IntEnum),
    (kinds.Form, enum.IntEnum),
    (kinds.TypeFlags, enum.Flag),
    (kinds.FieldFlags, enum.Flag),
]


def rejects(text, message):
    with pytest.raises(DeltaError, match=message):
        Image.empty().apply(text)


@pytest.mark.parametrize(("words", "base"), ENUMS)
def test_each_table_is_a_python_enum(words, base):
    assert issubclass(words, base) and len(words) > 1
    assert len({m.name for m in words}) == len(words)


def test_the_words_are_the_ground_enumerators_of_the_image():
    """One table feeds both: what the image calls NodeKind.Module is kinds.NodeKind.Module."""
    image = Image.empty()
    for words in (member for member, _ in ENUMS):
        type_handle = resolved(image, words.__name__)
        assert image.type_name(resolved(image, f"{words.__name__}.{words(1).name}"))
        names = {image.get(h)["name"]: h for h in image.children(type_handle)}
        assert set(names) == {m.name for m in words}
        for member in words:
            value = dict(image.fields(names[member.name])).get("value", "0")
            assert int(value) == member.value, member


def test_flags_combine():
    both = kinds.TypeFlags.Variable | kinds.TypeFlags.Signed
    assert both.value == 3 and kinds.TypeFlags.Signed in both


def test_an_enumeration_reads_and_writes_names():
    image = Image.empty()
    image.apply(DELTA)
    assert image.check() == []
    fields = dict(image.fields(resolved(image, "shapes.deep.p")))
    assert fields["color"] == "Color.Green"
    assert fields["perm"] == "Perm.R|W"  # a bitset is written in value order
    again = Image.empty()
    again.apply(image.export())
    assert again.digest() == image.digest()
    assert image.export() == again.export()


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ('color="3"', "3 is not an object"),
        ('color="Green"', "Green is not an object"),  # a bare word is not a name
        ('color="Color.Blue"', "Color.Blue is not an object"),
        ('color="Perm.R"', "is not an Enumerator of Color"),
        ('perm="Perm.R|Z"', "'Z' is not a value of Perm"),
        ('perm="3"', "3 is not an object"),
        ('color="Color.Red|Green"', "is not an object"),
    ],
)
def test_a_number_a_bare_word_or_an_unknown_name_is_not_an_enumerator(bad, message):
    rejects(DELTA.replace('color="Color.Green" perm="Perm.W|R"', bad), message)


def test_a_form_or_flag_word_must_exist():
    rejects(
        '<delta><Type name="T" form="Form.Struct" size="4" align="4" flags="TypeFlags.Shiny"/></delta>',
        "TypeFlags.Shiny is not an object",
    )
    rejects(
        '<delta><Type name="T" form="3" size="4" align="4"/></delta>',
        "3 is not an object",
    )


def test_the_node_kind_of_a_type_is_the_kind_of_its_objects():
    image = Image.empty()
    image.apply(DELTA)
    module = image.get(resolved(image, "shapes"))
    assert (
        module["kind"] == kinds.NodeKind.Module
    )  # from Type.node_kind, written nowhere
    assert image.get(resolved(image, "shapes.target"))["kind"] == kinds.NodeKind.Data
    assert image.get(resolved(image, "shapes.deep.s"))["kind"] == kinds.NodeKind.Alias
    assert "rxf:kind" not in image.export()  # only a deviation from the type is written
    assert "node_kind" in image.export()


def test_a_kind_that_the_type_does_not_give_is_written():
    image = Image.empty()
    image.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf"><Type name="Box" form="Form.Struct" size="0" align="1"/>'
        '<Box name="b" rxf:kind="NodeKind.Section"/></delta>'
    )
    assert 'rxf:kind="NodeKind.Section"' in image.export()


def test_an_alias_resolves_in_its_module():
    image = Image.empty()
    image.apply(DELTA)
    assert dict(image.fields(resolved(image, "shapes.deep.p")))["to"] == "shapes.target"
    assert resolved(image, "shapes.deep.s") == resolved(image, "shapes.deep.s")
    assert image.type_name(resolved(image, "shapes.deep.s")) == "Alias"


def test_an_alias_is_not_found_from_outside_its_module():
    rejects(
        DELTA.replace(
            '<Pixel name="target"/>', '<Pixel name="target" to="@s.target"/>'
        ),
        "does not resolve",
    )


def test_an_unknown_alias_does_not_resolve():
    rejects(DELTA.replace('to="@s.target"', 'to="@nobody.target"'), "does not resolve")


def test_the_writer_uses_an_alias_only_where_it_is_shorter():
    # shapes.target is not below the alias `s` = shapes.deep, so it stays absolute and `s` is never written
    unused = DELTA.replace(
        '<Alias name="s" target="shapes"/>',
        '<Alias name="s" target="shapes.deep"/>',
    ).replace('to="@s.target"', 'to="shapes.target"')
    rejects(unused, "the alias is not used")
    image = Image.empty()
    image.apply(
        unused.replace(
            '<Alias name="s" target="shapes.deep"/>',
            '<Alias name="s" target="shapes.deep" flags="AliasFlags.Reexport"/>',
        )
    )
    text = image.export()
    assert 'to="shapes.target"' in text and 'target="shapes.deep"' in text

    long = DELTA.replace("shapes", "a_module_with_a_long_name")
    image = Image.empty()
    image.apply(long)
    assert 'to="@s.target"' in image.export()


def test_an_alias_knows_who_is_written_through_it():
    image = Image.empty()
    image.apply(DELTA.replace("shapes", "a_module_with_a_long_name"))
    alias = resolved(image, "a_module_with_a_long_name.deep.s")
    pixel = resolved(image, "a_module_with_a_long_name.deep.p")
    assert image.alias_uses() == [(alias, pixel)]


def test_an_unused_alias_is_refused_unless_it_is_reexported():
    name = "a_module_with_a_long_name"
    image = Image.empty()
    image.apply(DELTA.replace("shapes", name))
    spare = (
        '<delta xmlns:rxf="urn:pymergetic:rxf">'
        f'<Alias name="spare" target="{name}" rxf:parent="{name}.deep"%s/></delta>'
    )
    with pytest.raises(DeltaError, match="the alias is not used"):
        image.apply(spare % "")
    image.apply(spare % ' flags="AliasFlags.Reexport"')
    assert image.check() == []
    # the spare alias is not written through, so it is not a use
    assert image.alias_uses() == [
        (resolved(image, f"{name}.deep.s"), resolved(image, f"{name}.deep.p"))
    ]


def test_a_name_cannot_look_like_an_alias():
    rejects(
        '<delta><Type name="@x" form="Form.Struct" size="0" align="1"/></delta>',
        "BadName",
    )
