"""An image as a tree of files, one per module, parsed and printed on several threads."""

from pathlib import Path

import pytest

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.core.tree import TreeError

STARTER = Path(__file__).parents[1] / "starter"

MODULES = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Thing" form="Form.Struct" size="8" align="8">
    <Field name="other" value_type="uint64_t" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Type name="Module" form="Form.Struct" size="0" align="1"/>
  <Module name="alpha" rxf:kind="NodeKind.Module"/>
  <Module name="beta" rxf:parent="alpha" rxf:kind="NodeKind.Module"/>
  <Thing name="one" rxf:parent="alpha.beta" other="alpha.two"/>
  <Thing name="two" rxf:parent="alpha"/>
  <Module name="zz" rxf:kind="NodeKind.Module"/>
  <Thing name="last" rxf:parent="zz"/>
  <rxf:entry path="zz.last"/>
</delta>"""


@pytest.fixture
def image():
    result = Image.empty()
    result.apply(MODULES)
    return result


def names(files):
    return [name for name, _ in files]


def test_files_are_laid_out_like_the_modules(image):
    files = dict(tree.files(image))
    # A module with sub-modules is a package (alpha/__init__.rxfd); one without is a file (zz.rxfd).
    assert list(files) == [
        "__init__.rxfd",
        "alpha/__init__.rxfd",
        "alpha/beta.rxfd",
        "zz.rxfd",
    ]
    assert "<Module" not in files["__init__.rxfd"] and "<Type" in files["__init__.rxfd"]
    assert 'name="two"' in files["alpha/__init__.rxfd"]
    assert 'name="one"' not in files["alpha/__init__.rxfd"]
    assert 'name="one"' in files["alpha/beta.rxfd"]
    assert "<rxf:entry" in files["__init__.rxfd"]
    # A file does not name its parent: the path of the file says it.
    assert (
        "rxf:parent" not in files["alpha/beta.rxfd"]
        and "rxf:parent" not in files["zz.rxfd"]
    )


def test_write_then_load_is_the_same_image(image, tmp_path):
    assert tree.write(image, tmp_path / "t") == 4
    loaded = tree.load(tmp_path / "t")
    assert loaded.check() == []
    assert loaded.digest() == image.digest()
    assert loaded.delta_digest() == image.delta_digest()
    assert (
        loaded.entry_path == "zz.last"
    )  # __init__.rxfd sorts before zz.rxfd, but is applied last


def test_the_result_does_not_depend_on_the_number_of_threads(image, tmp_path):
    assert tree.files(image, threads=1) == tree.files(image, threads=8)
    tree.write(image, tmp_path / "one", threads=1)
    tree.write(image, tmp_path / "many", threads=8)
    for name, text in tree.files(image):
        assert (tmp_path / "one" / name).read_text() == text
        assert (tmp_path / "many" / name).read_text() == text
    assert (
        tree.load(tmp_path / "one", threads=1).digest()
        == tree.load(tmp_path / "many", threads=8).digest()
    )


def test_writing_twice_gives_the_same_files(image, tmp_path):
    tree.write(image, tmp_path / "a")
    tree.write(tree.load(tmp_path / "a"), tmp_path / "b")
    for name, _ in tree.files(image):
        assert (tmp_path / "a" / name).read_bytes() == (
            tmp_path / "b" / name
        ).read_bytes()


def test_a_module_below_an_object_is_still_a_file_of_its_own(tmp_path):
    image = Image.empty()
    image.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf">'
        '<Type name="Module" form="Form.Struct" size="0" align="1"/>'
        '<Type name="Box" form="Form.Struct" size="0" align="1"/>'
        '<Box name="box"/><Module name="inner" rxf:parent="box" rxf:kind="NodeKind.Module"/></delta>'
    )
    files = dict(tree.files(image))
    assert "box.inner.rxfd" not in files and "box/inner.rxfd" in files
    assert 'name="inner"' not in files["__init__.rxfd"]
    # The file does not say where it sits; the tree does.
    tree.write(image, tmp_path / "t")
    assert tree.load(tmp_path / "t").digest() == image.digest()


def test_a_used_directory_is_only_replaced_on_request(image, tmp_path):
    target = tmp_path / "t"
    tree.write(image, target)
    with pytest.raises(TreeError, match="not empty"):
        tree.write(image, target)
    (target / "notes.txt").write_text("keep")
    (target / "stale.rxfd").write_text("<delta/>")
    (target / "old").mkdir()
    (target / "old" / "gone.rxfd").write_text("<delta/>")
    tree.write(image, target, overwrite=True)
    assert (target / "notes.txt").read_text() == "keep"
    assert not (target / "stale.rxfd").exists() and not (target / "old").exists()
    assert tree.load(target).digest() == image.digest()


def test_a_file_is_not_a_directory(image, tmp_path):
    (tmp_path / "f").write_text("x")
    with pytest.raises(TreeError, match="not a directory"):
        tree.write(image, tmp_path / "f")
    with pytest.raises(TreeError, match="not a directory"):
        tree.load(tmp_path / "f")
    (tmp_path / "empty").mkdir()
    with pytest.raises(TreeError, match="no .rxfd"):
        tree.load(tmp_path / "empty")


def test_a_broken_file_is_named_and_nothing_is_loaded(image, tmp_path):
    tree.write(image, tmp_path / "t")
    (tmp_path / "t" / "alpha" / "beta.rxfd").write_text("<delta><Thing")
    with pytest.raises(TreeError, match=r"cannot parse alpha/beta\.rxfd: line 1"):
        tree.load(tmp_path / "t")
    (tmp_path / "t" / "alpha" / "beta.rxfd").write_text(
        '<delta xmlns:rxf="urn:pymergetic:rxf"><Module name="beta" rxf:kind="NodeKind.Module">'
        '<Thing name="x" other="nowhere"/></Module></delta>'
    )
    with pytest.raises(TreeError, match="does not resolve"):
        tree.load(tmp_path / "t")


def test_a_file_defines_its_module_and_nothing_else(image, tmp_path):
    tree.write(image, tmp_path / "t")
    beta = tmp_path / "t" / "alpha" / "beta.rxfd"
    beta.write_text('<delta><Thing name="x"/></delta>')
    with pytest.raises(
        TreeError, match=r"alpha/beta\.rxfd: must start with the module 'alpha.beta'"
    ):
        tree.load(tmp_path / "t")
    beta.write_text(
        '<delta xmlns:rxf="urn:pymergetic:rxf"><Module name="beta" rxf:kind="NodeKind.Module"/>'
        '<Thing name="x" rxf:parent="zz"/></delta>'
    )
    with pytest.raises(TreeError, match="outside the module 'alpha.beta'"):
        tree.load(tmp_path / "t")
    beta.write_text("<delta/>")
    with pytest.raises(TreeError, match="must define the module"):
        tree.load(tmp_path / "t")


def test_a_path_is_a_module_or_it_is_refused(image, tmp_path):
    tree.write(image, tmp_path / "t")
    (tmp_path / "t" / "alpha.beta.rxfd").write_text("<delta/>")
    with pytest.raises(TreeError, match="not the file of a module"):
        tree.load(tmp_path / "t")
    (tmp_path / "t" / "alpha.beta.rxfd").unlink()
    (tmp_path / "t" / "alpha.rxfd").write_text("<delta/>")  # next to alpha/__init__.rxfd
    with pytest.raises(TreeError, match="a second time"):
        tree.load(tmp_path / "t")


@pytest.mark.parametrize("name", ["a/b", "a\\b"])
def test_a_module_name_cannot_leave_the_directory(name, tmp_path):
    hostile = Image.empty()
    hostile.apply(
        '<delta><Type name="Module" form="Form.Struct" size="0" align="1"/></delta>'
    )
    hostile.apply(
        f'<delta xmlns:rxf="urn:pymergetic:rxf"><Module name="{name}" rxf:kind="NodeKind.Module"/></delta>'
    )
    with pytest.raises(TreeError, match="cannot be a file name"):
        tree.write(hostile, tmp_path / "t")
    assert not (tmp_path / "t").exists()


def test_a_module_called_init_has_no_file(tmp_path):
    image = Image.empty()
    image.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf"><Type name="Module" form="Form.Struct" size="0" align="1"/>'
        '<Module name="__init__" rxf:kind="NodeKind.Module"/></delta>'
    )
    with pytest.raises(TreeError, match="cannot be a file name"):
        tree.files(image)


def test_the_starter_tree_loads_clean():
    image = tree.load(STARTER)
    assert image.check() == []
    assert image.entry_path == "pymergetic.rxf.application.main"
    assert names(tree.files(image)) == [
        "__init__.rxfd",
        *sorted(n for n in names(tree.files(image)) if n != "__init__.rxfd"),
    ]
    assert len(tree.files(image)) == len(list(STARTER.rglob("*.rxfd")))


def test_a_delta_error_is_not_a_tree_error(image):
    with pytest.raises(DeltaError):
        image.apply("<delta><Nothing name='x'/></delta>")
