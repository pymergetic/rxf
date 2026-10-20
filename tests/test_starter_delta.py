"""The checked-in starter (starter/, one file per module) on the C++ core."""

import json
import re
from pathlib import Path

from pymergetic.rxftk.cli import main
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.kinds import NodeKind

from .support import resolved

STARTER = Path(__file__).parents[1] / "starter"

# Bump these two together with the files in starter/, never one alone.
DIGEST = "e157fe5bdc705f8e50f3820f00db58bbc82d466634c7bee0c0bd1dfc1b6aaa2d"
DELTA_DIGEST = "e061951fef544fc4d3dcaa152336bfa8fd5f273c273c393979b6686eac9118cc"
OBJECTS = 17863


def test_the_starter_replays_and_checks_clean(capsys):
    assert main(["core", "object", "check", str(STARTER)]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True, "issues": []}
    assert main(["core", "object", "info", str(STARTER)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["issues"] == []
    assert report["objects"] == OBJECTS
    assert report["digest"] == DIGEST
    assert report["delta_digest"] == DELTA_DIGEST
    assert report["entry"] == "pymergetic.rxf.application.main"


def test_the_starter_files_are_the_canonical_tree_of_the_image(tmp_path):
    image = tree.load(STARTER)
    tree.write(image, tmp_path / "again")
    names = sorted(p.relative_to(STARTER).as_posix() for p in STARTER.rglob("*.rxfd"))
    assert names == sorted(
        p.relative_to(tmp_path / "again").as_posix()
        for p in (tmp_path / "again").rglob("*.rxfd")
    )
    for name in names:
        assert (STARTER / name).read_text() == (tmp_path / "again" / name).read_text()


def test_the_starter_names_roles_and_list_places_not_numbers():
    image = tree.load(STARTER)
    prefix = "pymergetic.rxf.roles."
    roles = indexed = 0
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        info = image.get(handle)
        assert not info["name"].startswith("_"), info["path"]
        assert not re.search(r"[a-z]_\d+$", info["name"]) or "x86" in info["name"], (
            info["path"]
        )
        indexed += bool(re.search(r"\[\d+\]$", info["name"]))
        for _, role, _, _, _ in info["refs"]:
            if role is not None:
                roles += 1
                assert (
                    image.path(role).startswith(prefix)
                    and image.type_name(role) == "Role"
                )
    assert roles > 5000 and indexed > 4000


def test_the_starter_is_laid_out_like_python_modules():
    names = sorted(p.relative_to(STARTER).as_posix() for p in STARTER.rglob("*.rxfd"))
    assert "__init__.rxfd" in names and "pymergetic/__init__.rxfd" in names
    assert (
        "pymergetic/rxf/foundation/__init__.rxfd" in names
    )  # a module with sub-modules
    assert "pymergetic/rxf/foundation/heap.rxfd" in names  # a module without
    assert "pymergetic/rxf/foundation.rxfd" not in names
    assert max(len(p.read_text().splitlines()) for p in STARTER.rglob("*.rxfd")) < 10000


def test_the_starter_writes_words_not_numbers():
    """Enumerations are names; the only numbers are counts, sizes, indices, offsets, widths."""
    text = "".join(p.read_text() for p in STARTER.rglob("*.rxfd"))
    assert 'rxf:parent="' not in text.replace('xmlns:rxf="urn:pymergetic:rxf"', "")
    for word in ("kind", "category", "layer", "implementation", "opcode", "terminator"):
        assert not re.search(rf'\s{word}="\d', text), word
    assert not re.search(r'<Type [^>]*\bform="\d', text)
    assert not re.search(r'\bflags="\d', text)


def test_the_starter_shortens_paths_with_aliases():
    image = tree.load(STARTER)
    library = (STARTER / "pymergetic/rxf/library.rxfd").read_text()
    assert '<Alias name="kinds" target="pymergetic.rxf.kinds" />' in library
    assert 'layer="@kinds.FunctionLayer.Library"' in library
    # an alias is an object of the module; the image holds the absolute path
    handle = resolved(image, "pymergetic.rxf.library.kinds")
    assert image.type_name(handle) == "Alias"
    assert dict(image.fields(handle))["target"] == "pymergetic.rxf.kinds"
    assert image.get(handle)["kind"] == NodeKind.Alias


def test_the_root_holds_only_what_a_module_needs_to_exist():
    # The ground (made by the core), the object kinds of the module system (`Module`, `Alias`)
    # and of the documentation (`Doc`) with the enumerations of their fields, the primitive
    # types, and the first module.
    image = tree.load(STARTER)
    names = {image.path(h) for h in image.children(0)}
    assert names == {
        "Type", "Field", "uint32_t", "uint64_t", "Enumerator",
        "NodeKind", "RefKind", "Binding", "OwnerKind", "Mobility",
        "Form", "TypeFlags", "FieldFlags",
        "Alias", "AliasFlags", "Module", "ModuleCategory", "ModuleFlags", "pymergetic",
        "Doc", "DocKind", "DocStatus", "DocCodec", "DocRole",
        "bool", "void", "uint8_t", "uint16_t", "int8_t", "int16_t", "int32_t", "int64_t",
        "float", "double", "Role",
    }  # fmt: skip
    assert image.type_name(resolved(image, "pymergetic.rxf")) == "Module"


def test_the_starter_names_every_word_by_the_path_of_its_enumerator():
    """No bare `LIBRARY`, `DATA`, `STRUCT`: a value is `@kinds.Effects.Read|Write`, `RefKind.Data`."""
    text = "".join(p.read_text() for p in STARTER.rglob("*.rxfd"))
    bare = re.findall(
        r'\s(?!name=)[A-Za-z_:]+="[A-Z][A-Z0-9_]+(?:\|[A-Z][A-Z0-9_]+)*"', text
    )
    assert bare == []
    assert 'kind="RefKind.Data"' in text and 'form="Form.Struct"' in text
    assert 'effects="@kinds.Effects.Write|Allocate"' in text


def test_the_starter_spells_enumerators_and_roles_in_camel_case():
    image = tree.load(STARTER)
    camel = re.compile(r"^[A-Z][A-Za-z0-9]*$")
    named = 0
    for handle in range(image.handle_count):
        if image.live(handle) and image.type_name(handle) in ("Role", "Enumerator"):
            assert camel.match(image.get(handle)["name"]), image.path(handle)
            named += 1
    assert named > 300


def test_the_starter_says_every_edge_once():
    """A field is the reference; a list of children is the tree; the other direction is computed."""
    image = tree.load(
        STARTER
    )  # the link check refuses a reference that repeats a field
    refs = children = 0
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        info = image.get(handle)
        refs += len(info["refs"])
        children += sum(
            1 for target, *_ in info["refs"] if image.get(target)["parent"] == handle
        )
    # 39,180 before the repeats and the child lists were taken out; each of the
    # 317 native functions now has three more codes, each with two references.
    assert refs < 8000
    assert (
        children < 1000
    )  # only lists that name some of the children, where no type says which
    assert not any(
        image.path(h).endswith("roles.semantic.Predecessor")
        for h in range(image.handle_count)
        if image.live(h)
    )


def test_a_list_that_is_not_in_name_order_says_its_order_with_an_index():
    image = tree.load(STARTER)
    signature = resolved(
        image, "pymergetic.rxf.foundation.graph.run.aapcs64_linux_status_out"
    )
    # `hidden_context` comes first in the ABI although `argument[1]` sorts before it
    kids = image.children(signature)
    names = [image.get(k)["name"] for k in image.ordered(kids)]
    assert names[0] == "hidden_context" and names[1] == "argument[1]"
    assert any(image.get(k)["index"] > 0 for k in kids)


def test_the_schema_says_which_children_are_which_list():
    """`ABISignature.locations` is declared on the type; no instance names its own list."""
    image = tree.load(STARTER)
    signature = resolved(
        image, "pymergetic.rxf.foundation.graph.run.aapcs64_linux_status_out"
    )
    ((field, items),) = image.members(signature)
    assert image.get(field)["name"] == "locations" and len(items) == 6
    assert image.get(items[0])["name"] == "hidden_context"
    declared = {
        (image.get(image.get(f)["parent"])["name"], image.get(f)["name"])
        for f in range(image.handle_count)
        if image.live(f)
        and image.type_name(f) == "Field"
        and "Children" in dict(image.fields(f)).get("flags", "")
    }
    assert ("ABISignature", "locations") in declared and len(declared) == 11


def test_the_predecessors_of_a_block_are_computed_from_the_successors():
    image = tree.load(STARTER)
    block = resolved(
        image, "pymergetic.rxf.algorithms.binary_search.binary_search_graph.status[0]"
    )
    successors = {
        t
        for t, role, *_ in image.get(block)["refs"]
        if image.path(role).endswith("Successor")
    }
    for target in successors:
        names = {image.get(f)["name"]: items for f, items in image.members(target)}
        assert block in names["predecessors"]
