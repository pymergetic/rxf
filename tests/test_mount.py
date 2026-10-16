"""The host mount (docs/16): one image, one import hook, the object world as Python.

What these pins hold:

* the mounted tree is the ``.rxfd`` tree and the guest's tree — the names the host mounts
  are the modules on disk (``starter/``, one file per module) and exactly the names the
  guest's import walk answers ``package``/``module`` for (``Image.resolve_module``,
  docs/23 §4.6). One card, two seats, no third list to keep in step;
* the face is the self-description: a Type object becomes a class, a Module cell a module,
  an object an instance carrying ``id``; every property reads the payload at the field's
  offset and agrees, value for value, with the generic reader (``Image.fields``) that the
  text form is written from;
* the ground rides every module (Type, Field, ...: what a module needs to exist);
* a user module tree in the same image mounts under its own name; a name the image does not
  have is refused by name; without an image the error says so.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from pymergetic.rxftk import mount
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image

STARTER = Path(__file__).parents[1] / "starter"

# One user module tree in the same image — the objection "users open other module trees".
USER = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Module name="mygame">
    <Module name="engine"/>
  </Module>
</delta>"""


def xfd_modules() -> set[str]:
    """The module names of the .rxfd tree: one file per module, laid out like Python."""
    names = set()
    for p in STARTER.rglob("*.rxfd"):
        rel = p.relative_to(STARTER).as_posix()[: -len(".rxfd")]
        if rel == "__init__":
            continue  # the root of the tree: not a module name
        rel = rel.removesuffix("/__init__")
        names.add(rel.replace("/", "."))
    return names


@pytest.fixture
def image() -> Image:
    return tree.load(STARTER)


@pytest.fixture
def mounted(image):
    image.apply(USER)
    mount.install(image)
    yield image
    mount.uninstall()


def test_the_mounted_tree_is_the_xfd_tree_and_the_guest_tree(image):
    names = set(image.mount_names())
    assert names == xfd_modules()  # what the host mounts is what the tree holds
    # and exactly what the guest's import walk answers for: one card, two seats
    guest = {n for n in names if image.resolve_module(n) in ("package", "module")}
    assert guest == names
    assert "pymergetic.rxf" in names and len(names) > 50


def test_the_mounted_root_carries_the_ground_and_its_own_names(mounted):
    rxf = importlib.import_module("pymergetic.rxf")
    # the ground (the root's types) rides every module: Type, Field, ...
    assert type(rxf.Type).__name__ == "Type"
    assert rxf.Type.id == mounted.resolve("Type")
    # its own names win: the modules below it are there, lazily and once
    kinds = importlib.import_module("pymergetic.rxf.kinds")
    assert kinds.__rxf_mount__ is True
    assert rxf.kinds is kinds


def test_an_object_carries_its_id_and_reads_the_cell_at_the_fields_offset(mounted):
    alias = importlib.import_module("pymergetic.rxf.library").kinds
    assert type(alias).__name__ == "Alias"
    assert alias.id == mounted.resolve("pymergetic.rxf.library.kinds")
    assert repr(alias) == "<Alias pymergetic.rxf.library.kinds>"
    # a reference leaf is the handle it holds; the generic reader says the same as a path
    assert alias.target == mounted.resolve("pymergetic.rxf.kinds")
    text = dict(mounted.fields(alias.id))
    assert text["target"] == mounted.path(alias.target)
    # an enumeration with no value reads as None (the text form omits it too)
    assert alias.flags is None and "flags" not in text
    # the tail bytes of the payload ride as `data` (here: the type is fully covered)
    assert alias.data == b""


def test_a_declared_relation_arrives_as_a_list_of_mounted_objects(mounted):
    run = importlib.import_module("pymergetic.rxf.foundation.graph").run
    assert type(run).__name__ == "Function"
    ((_, items),) = mounted.members(run.id)
    implementations = run.implementations
    assert [mounted.path(o.id) for o in implementations] == [
        mounted.path(i) for i in items
    ]
    assert [o.id for o in implementations] == list(items)
    # every face is the class of the object's own Type — the image decides, not the test
    assert all(type(o).__name__ == mounted.type_name(o.id) for o in implementations)


def test_every_property_agrees_with_the_generic_reader(mounted):
    """The consistency pin: the direct reader and the codec of the text form are one
    layout (meta/types.cpp) — value for value, over a fixed sample of the image."""
    from pymergetic.rxftk.core import mount as core_mount
    from pymergetic.rxftk.core.kinds import NodeKind

    synth = core_mount.Synthesizer(mounted)
    checked = 0
    for handle in range(0, mounted.handle_count, 37):
        if handle == 0 or not mounted.live(handle):
            continue  # the root of the tree is not an ordinary object
        if mounted.get(handle)["kind"] == int(NodeKind.Module):
            continue  # a Module cell mounts as a module, not as a field carrier
        obj = synth.object(handle)
        text = dict(mounted.fields(handle))
        for name, value in text.items():
            got = getattr(obj, name, "MISSING")
            assert got != "MISSING", mounted.path(handle)
            if isinstance(got, bool):
                assert str(int(got)) == value
            elif isinstance(got, int):
                assert (
                    mounted.path(got) if name in _refs(mounted, handle) else str(got)
                ) == value
            elif isinstance(got, str):
                assert got == value
            elif isinstance(got, float):
                assert float(value) == got
            elif isinstance(got, bytes):
                assert got.hex() == value
            elif got is None:
                raise AssertionError(
                    f"{mounted.path(handle)}.{name} is None but the reader says {value}"
                )
        checked += 1
    assert checked > 200


def _refs(image: Image, handle: int) -> set[str]:
    """The names of the reference leaves of an object's type (a reference reads as a path
    in the text form and as a handle on the mounted face)."""
    layout = image.layout(image.get(handle)["type"])
    return {leaf["name"] for leaf in layout["leaves"] if leaf["reference"]}


def test_a_user_module_tree_in_the_same_image_appears_under_its_fqn(mounted):
    mygame = importlib.import_module("mygame")
    assert mygame.__rxf_mount__ is True
    assert mounted.resolve_module("mygame") == "package"
    engine = importlib.import_module("mygame.engine")
    assert engine is mygame.engine
    assert mounted.resolve_module("mygame.engine") == "module"
    assert (
        "mygame" in mounted.mount_names() and "mygame.engine" in mounted.mount_names()
    )


def test_attribute_access_and_import_give_the_one_object(mounted):
    heap = importlib.import_module("pymergetic.rxf.foundation.heap")
    assert heap is importlib.import_module("pymergetic.rxf.foundation.heap")
    assert heap is importlib.import_module("pymergetic.rxf.foundation").heap
    assert sys.modules["pymergetic.rxf.foundation.heap"] is heap


def test_a_name_the_image_does_not_have_is_refused_by_name(mounted):
    with pytest.raises(ImportError, match="image has no"):
        importlib.import_module("pymergetic.rxf.nope")
    # a name outside the image's world is not ours to refuse: the system answers
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pymergetic.rxftk.nope")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("nope")


def test_without_an_image_the_error_says_so():
    mount.uninstall()
    with pytest.raises(ImportError, match="no RXF image loaded"):
        mount.mounted()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("pymergetic.rxf")
    assert not [m for m in sys.modules.values() if getattr(m, "__rxf_mount__", False)]


def test_one_image_at_a_time(mounted):
    with pytest.raises(ImportError, match="already mounted"):
        mount.install(Image.create())
