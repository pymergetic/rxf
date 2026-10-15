"""The pins of the highlighting lane.

The meaning table (``tools/highlight/meaning.json``) may not drift from the
sources of truth it was transcribed from, and the generated grammars may not
drift from the table. Every pin below names the file it guards:

- the leaves: ``native/graph.h`` (the ids are not invented here) and
  ``tools/native/graph_header.py`` (the keys the tools use);
- the operators: ``tools/native/rxfc.py`` (what every operator lowers to) and
  the self-hosted mirror ``native/front/words.rxf``;
- the enums: ``starter/pymergetic/rxf/kinds.xml`` (the module
  ``pymergetic.rxf.kinds``), the ground of ``starter/__init__.xml`` and the core
  stubs ``src/pymergetic/rxf/core/kinds.pyi``;
- the refusal sets: ``starter/pymergetic/rxf/foundation/__init__.xml``;
- the grammars: regenerate with ``tools/highlight/grammar_gen.py`` and compare
  byte for byte;
- the resolver: ``tests/selfedit/triple.xml`` and starter deltas must resolve,
  and a reference that does not resolve must say why.
"""

from __future__ import annotations

import ast
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]

from tools.highlight import grammar_gen, meaning, resolve

GRAPH_H = ROOT / "native" / "graph.h"
FRONT_WORDS = ROOT / "native" / "front" / "words.rxf"
RXFC = ROOT / "tools" / "native" / "rxfc.py"
KINDS_XML = ROOT / "starter" / "pymergetic" / "rxf" / "kinds.xml"
GROUND_XML = ROOT / "starter" / "__init__.xml"
KINDS_PYI = ROOT / "src" / "pymergetic" / "rxf" / "core" / "kinds.pyi"
FOUNDATION_XML = ROOT / "starter" / "pymergetic" / "rxf" / "foundation" / "__init__.xml"
STARTER = ROOT / "starter"
SELFEDIT = ROOT / "tests" / "selfedit"
GRAMMARS = ROOT / "editors" / "rxf-highlight" / "syntaxes"
MANIFEST = ROOT / "editors" / "rxf-highlight" / "package.json"

TABLE = meaning.load()


# ---- parsers of the sources of truth ----------------------------------------------------------


def leaves_of_graph_h() -> dict[int, str]:
    """The ids of ``enum rxf_graph_leaf`` of native/graph.h, by id."""
    block = GRAPH_H.read_text().split("enum rxf_graph_leaf {", 1)[1].split("};", 1)[0]
    return {int(value): name for name, value in re.findall(r"(RXF_LEAF_\w+)\s*=\s*(\d+)", block)}


def shapes_of_graph_h() -> dict[str, str]:
    """The shape every leaf is called with: the switch of ``shape_of`` in native/graph.h."""
    body = GRAPH_H.read_text().split("rxf_u32 shape_of(rxf_u64 leaf) {", 1)[1].split("\n}", 1)[0]
    shapes: dict[str, str] = {}
    pending: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        case = re.match(r"case (RXF_LEAF_\w+):", line)
        if case:
            pending.append(case.group(1))
            continue
        ret = re.match(r"return RXF_SHAPE_([A-Z0-9_]+);", line)
        if ret:
            for name in pending:
                shapes[name] = ret.group(1).lower()
            pending = []
    return shapes


def rxfc_constants() -> dict[str, Any]:
    """The tables of tools/native/rxfc.py: BINARY, PRECEDENCE, LOADS, STORES."""
    found: dict[str, Any] = {}
    for node in ast.parse(RXFC.read_text()).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("BINARY", "PRECEDENCE", "LOADS", "STORES"):
                found[name] = ast.literal_eval(node.value)
    return found


def front_leaves() -> dict[str, str]:
    """The leaves of native/front/words.rxf: ``const LEAF_ADD = RXF_LEAF_...;``"""
    return dict(re.findall(r"const (LEAF_\w+) = (RXF_LEAF_\w+);", FRONT_WORDS.read_text()))


def front_no_leaf_operators() -> set[str]:
    """The operators native/front/words.rxf says the language has no leaf for."""
    text = FRONT_WORDS.read_text()
    found = re.search(r"no leaf for \(([^)]*)\)", text)
    assert found, "the comment that names the operators without a leaf is gone"
    return set(re.findall(r"`([^`]+)`", found.group(1)))


def enums_of_xml(path: Path) -> dict[str, tuple[bool, dict[str, int]]]:
    """Every ``Type form="Form.Enum"`` of a delta document, with its enumerators."""
    found: dict[str, tuple[bool, dict[str, int]]] = {}
    for type_element in ET.fromstring(path.read_text()).iter("Type"):
        if type_element.get("form") != "Form.Enum":
            continue
        found[type_element.get("name", "")] = (
            type_element.get("flags") == "TypeFlags.Bitset",
            {
                e.get("name", ""): int(e.get("value", "0"))
                for e in type_element.findall("Enumerator")
            },
        )
    return found


def enums_of_pyi(path: Path) -> dict[str, tuple[bool, dict[str, int]]]:
    """Every enumeration class of the Python stubs of pymergetic.rxf.core.kinds."""
    found: dict[str, tuple[bool, dict[str, int]]] = {}
    for node in ast.parse(path.read_text()).body:
        if not isinstance(node, ast.ClassDef):
            continue
        enumerators: dict[str, int] = {}
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and isinstance(stmt.targets[0], ast.Name):
                enumerators[stmt.targets[0].id] = ast.literal_eval(stmt.value)
        flags = any(isinstance(base, ast.Attribute) and base.attr == "Flag" for base in node.bases)
        found[node.name] = (flags, enumerators)
    return found


def table_enums(source: str) -> dict[str, tuple[bool, dict[str, int]]]:
    return {
        entry.type: (entry.flags, dict(entry.enumerators))
        for entry in TABLE.rxfd.enums
        if entry.source == source
    }


# ---- the leaves -------------------------------------------------------------------------------


def test_the_leaf_ids_and_names_are_the_registry_of_graph_h():
    registry = leaves_of_graph_h()
    assert sorted(registry) == list(range(1, 49))  # there is no leaf 0
    assert {leaf.id: leaf.c_symbol for leaf in TABLE.leaves.entries} == registry


def test_the_leaf_names_are_the_keys_the_tools_use():
    import graph_header  # tools/native, on the path the tests run with

    assert {leaf.name: leaf.id for leaf in TABLE.leaves.entries} == graph_header.leaves()


def test_the_leaf_shapes_are_the_shape_of_switch():
    shapes = shapes_of_graph_h()
    table = {leaf.c_symbol: leaf.shape for leaf in TABLE.leaves.entries}
    assert table == {name: shapes.get(name, "none") for name in table}


def test_the_table_is_coherent_with_itself():
    entries = TABLE.leaves.entries
    classes = set(TABLE.leaves.classes)
    assert all(leaf.klass in classes for leaf in entries)
    assert all(leaf.name == leaf.c_symbol.removeprefix("RXF_LEAF_").lower() for leaf in entries)
    names = {leaf.name for leaf in entries}
    for operator in TABLE.rxfs.operators:
        assert operator.mapping in ("one-to-one", "desugared", "none"), operator
        assert set(operator.leaves) <= names, operator
        if operator.mapping == "none":
            assert operator.leaves == (), operator
            assert operator.klass == "assignment", operator  # no leaf: the scope says so
        else:
            assert 1 <= len(operator.leaves) <= 2, operator
            assert operator.klass in classes, operator
    for builtin in TABLE.rxfs.builtins:
        assert set(builtin.leaves) <= names, builtin
        assert builtin.klass in classes
    for refusal_set in TABLE.rxfd.refusal_sets:
        assert refusal_set.variant_count == len(refusal_set.variants)
    sources = {entry.source for entry in TABLE.rxfd.enums}
    assert sources == {
        "starter/pymergetic/rxf/kinds.xml",
        "starter/__init__.xml",
        "src/pymergetic/rxf/core/kinds.pyi",
    }


# ---- the operators ----------------------------------------------------------------------------


def test_the_one_to_one_operators_are_the_b_table_of_rxfc():
    binary = rxfc_constants()["BINARY"]
    assert {o.token: o.leaves[0] for o in TABLE.rxfs.operators if o.mapping == "one-to-one"} == binary


def test_the_operators_without_a_leaf_are_the_ones_the_front_names():
    desugared = {o.token for o in TABLE.rxfs.operators if o.mapping == "desugared"}
    # `*` is not in the comment of words.rxf because it is a shift when it can be
    # (x * 2^k is x << k): the table says so in its lowering, and rxfc.py refuses
    # a multiplier that is not a constant power of two.
    assert front_no_leaf_operators() == desugared - {"*"}
    assert "*" in desugared and all(o.leaves == ("shift_left_uint64_t",) for o in TABLE.rxfs.operators if o.token == "*")


def test_the_builtin_calls_are_the_memory_calls_of_rxfc():
    constants = rxfc_constants()
    loads = {o.name for o in TABLE.rxfs.builtins if o.name.startswith("load")}
    stores = {o.name for o in TABLE.rxfs.builtins if o.name.startswith("store")}
    assert loads == set(constants["LOADS"]) and stores == set(constants["STORES"])
    by_name = {builtin.name: builtin for builtin in TABLE.rxfs.builtins}
    for name in loads | stores:
        assert by_name[name].leaves == ("memory_copy",)  # a load or store is a memory_copy
    assert by_name["copy"].leaves == ("memory_copy",)
    assert by_name["fill"].leaves == ("memory_fill",)
    assert by_name["put"].leaves == ("memory_copy", "wrapping_add_uint64_t")


def test_the_front_names_the_same_operator_leaves():
    one_to_one = {leaf for o in TABLE.rxfs.operators for leaf in o.leaves if o.mapping == "one-to-one"}
    one_to_one |= {"memory_copy", "memory_fill"}  # LEAF_COPY and LEAF_FILL of words.rxf
    assert set(front_leaves().values()) == {TABLE.leaf(name).c_symbol for name in one_to_one}


def test_the_precedence_is_the_one_of_rxfc():
    assert rxfc_constants()["PRECEDENCE"] == [list(level) for level in TABLE.rxfs.precedence]


def test_the_frontend_lowers_the_way_the_table_says():
    """The table may claim what it likes; the compiler decides. `~` reaches the
    wrapping subtract leaf and not the bit not leaf, `x * 8` is a shift and not a
    multiply, and the comparisons without a leaf are the less and equal leaves."""
    import rxfc  # tools/native, on the path the tests run with

    source = (
        "program p(a, b) {\n"
        "    var r;\n"
        "    r = ~a;\n"
        "    r = a * 8;\n"
        "    r = a != b;\n"
        "    r = a > b;\n"
        "    r = a <= b;\n"
        "    r = -a;\n"
        "    r = !a;\n"
        "    return r;\n"
        "}\n"
    )
    maker = rxfc.Maker(source, "p.rxf")
    maker.parse()
    assert maker.leaf_used == {
        "wrapping_subtract_uint64_t",
        "shift_left_uint64_t",
        "equal_uint64_t",
        "less_uint64_t",
    }
    assert "bit_not_uint64_t" not in maker.leaf_used  # a leaf of the table that this language never calls
    assert "wrapping_multiply_uint64_t" not in maker.leaf_used


def test_the_corpus_programs_reach_only_the_leaves_the_table_names():
    import rxfc  # tools/native, on the path the tests run with

    reachable = {leaf for o in TABLE.rxfs.operators for leaf in o.leaves}
    reachable |= {leaf for b in TABLE.rxfs.builtins for leaf in b.leaves}
    for name in ("arith", "branches", "procs", "table"):
        path = ROOT / "tests" / "rxfc" / f"{name}.rxf"
        maker = rxfc.Maker(path.read_text(), str(path))
        maker.parse()
        assert maker.leaf_used <= reachable, name


# ---- the delta vocabulary ---------------------------------------------------------------------


def test_the_enum_vocab_is_the_kinds_module():
    assert table_enums("starter/pymergetic/rxf/kinds.xml") == enums_of_xml(KINDS_XML)


def test_the_ground_enums_are_the_root_delta():
    assert table_enums("starter/__init__.xml") == enums_of_xml(GROUND_XML)


def test_the_core_enums_are_the_python_stubs():
    assert table_enums("src/pymergetic/rxf/core/kinds.pyi") == enums_of_pyi(KINDS_PYI)


def test_the_refusal_sets_are_the_foundation_module():
    found = []
    module = ET.fromstring(FOUNDATION_XML.read_text()).find("Module")
    assert module is not None
    codes = {
        variant.get("name"): variant.get("code")
        for variant in module.findall("RefusalVariant")
    }
    for refusal_set in module.findall("RefusalSet"):
        variants = []
        for ref in refusal_set.findall("{urn:pymergetic:rxf}ref"):
            name = (ref.get("target") or "").rsplit(".", 1)[-1]
            variants.append((name, ref.get("target"), codes[name]))
        found.append(
            (
                f"pymergetic.rxf.foundation.{refusal_set.get('name')}",
                int(refusal_set.get("variant_count") or 0),
                variants,
            )
        )
    assert found == [
        (
            refusal_set.path,
            refusal_set.variant_count,
            [(v.name, v.target, v.code) for v in refusal_set.variants],
        )
        for refusal_set in TABLE.rxfd.refusal_sets
    ]


# ---- the generated grammars -------------------------------------------------------------------


def test_the_checked_in_grammars_are_generated_from_the_table():
    for name, text in grammar_gen.render_all().items():
        path = GRAMMARS / name
        assert path.read_text() == text, f"{path} is stale: run python3 -m tools.highlight.grammar_gen"


def test_the_grammars_scope_the_words_of_the_table():
    rxfs = json.loads((GRAMMARS / "rxfs.tmLanguage.json").read_text())
    rxfd = json.loads((GRAMMARS / "rxfd.tmLanguage.json").read_text())
    assert rxfs["scopeName"] == TABLE.rxfs.scope_name
    assert rxfd["scopeName"] == TABLE.rxfd.scope_name
    keywords = " ".join(
        pattern["match"] for pattern in rxfs["repository"]["keyword"]["patterns"]
    )
    for group in TABLE.rxfs.keywords:
        for word in group.words:
            assert re.escape(word) in keywords, word
    scopes = {pattern["name"] for pattern in rxfs["repository"]["operator"]["patterns"]}
    assert scopes == {operator.scope for operator in TABLE.rxfs.operators}
    builtins = rxfs["repository"]["builtin"]["match"]
    for builtin in TABLE.rxfs.builtins:
        assert re.escape(builtin.name) in builtins, builtin.name
    elements = rxfd["repository"]["tag"]["begin"] + rxfd["repository"]["rxf-tag"]["begin"]
    for element in (*TABLE.rxfd.elements, *(f"rxf:{e}" for e in TABLE.rxfd.namespace.elements)):
        assert re.escape(element.rsplit(":", 1)[-1]) in elements, element
    reference = rxfd["repository"]["reference-attribute"]["begin"]
    for shape in TABLE.rxfd.references:
        assert re.escape(shape.attribute) in reference, shape.attribute
    enumerations = rxfd["repository"]["enum-value"]["match"]
    for entry in TABLE.rxfd.enums:
        assert re.escape(entry.type) in enumerations, entry.type


def test_the_extension_manifest_wires_the_final_extensions():
    manifest = json.loads(MANIFEST.read_text())
    languages = {
        language["id"]: language["extensions"]
        for language in manifest["contributes"]["languages"]
    }
    # The final names are .rxfs and .rxfd: `pymergetic.rxf` is the Python package
    # and never a file extension, and the old `.rxf` is not an extension either.
    assert languages == {"rxfs": [".rxfs"], "rxfd": [".rxfd"]}
    assert manifest["contributes"]["grammars"] == [
        {"language": "rxfs", "scopeName": "source.rxfs", "path": "./syntaxes/rxfs.tmLanguage.json"},
        {"language": "rxfd", "scopeName": "text.xml.rxfd", "path": "./syntaxes/rxfd.tmLanguage.json"},
    ]
    assert "main" not in manifest and "dependencies" not in manifest
    for grammar in manifest["contributes"]["grammars"]:
        assert (MANIFEST.parent / grammar["path"]).exists()


# ---- the resolver -----------------------------------------------------------------------------


def test_the_reference_shapes_drive_the_resolver():
    shapes = resolve.reference_shapes()
    assert set(shapes) == {"signature", "refusal_set", "rxf:parent", "callee"}
    assert {name: shape.expected_kind for name, shape in shapes.items()} == {
        "signature": "Signature",
        "refusal_set": "RefusalSet",
        "rxf:parent": "Module",
        "callee": "Function",
    }
    assert set(resolve.attribute_keys()) == {
        "signature",
        "refusal_set",
        "callee",
        "{urn:pymergetic:rxf}parent",
    }


@pytest.fixture(scope="module")
def world() -> resolve.World:
    """The starter as it is laid out, plus the module the self-edit deltas declare."""
    known = resolve.corpus_world(STARTER)
    resolve.add_document(
        known,
        (SELFEDIT / "double.xml").read_text(),
        source="tests/selfedit/double.xml",
    )
    return known


def test_the_corpus_layout_gives_every_document_its_default_parent():
    at = lambda path: resolve.layout_default_parent(STARTER, STARTER / path)
    assert at("pymergetic/rxf/foundation/__init__.xml") == "pymergetic.rxf"
    assert at("pymergetic/rxf/library.xml") == "pymergetic.rxf"
    assert at("pymergetic/rxf/numeric/convert/uint8_t.xml") == "pymergetic.rxf.numeric.convert"
    assert at("pymergetic/__init__.xml") == ""
    assert at("__init__.xml") == ""


def test_the_corpus_declares_what_the_references_name(world: resolve.World):
    for path, kind in [
        ("pymergetic.rxf", "Module"),
        ("pymergetic.rxf.kinds", "Module"),
        ("pymergetic.rxf.kinds.FunctionImplementation", "Type"),
        ("pymergetic.rxf.kinds.FunctionImplementation.Semantic", "Enumerator"),
        ("pymergetic.rxf.foundation.StdlibRefusals", "RefusalSet"),
        ("pymergetic.rxf.numeric.checked.add_uint32_t", "Function"),
        ("pymergetic.rxf.selfedit", "Module"),
    ]:
        found = world.lookup(path)
        assert found is not None and found.kind == kind, path


def test_the_triple_delta_resolves_against_the_starter(world: resolve.World):
    text = (SELFEDIT / "triple.xml").read_text()
    references = resolve.check_document(text, known=world, source="tests/selfedit/triple.xml")
    assert {reference.attribute for reference in references} == {
        "signature",
        "refusal_set",
        "rxf:parent",
    }
    assert all(reference.resolved for reference in references), [
        (r.attribute, r.value, r.problem) for r in references if not r.resolved
    ]
    by_attribute = {reference.attribute: reference for reference in references}
    assert by_attribute["rxf:parent"].target == "pymergetic.rxf.selfedit"
    assert by_attribute["rxf:parent"].found_kind == "Module"
    assert by_attribute["signature"].target == "pymergetic.rxf.selfedit.triple.signature"
    assert by_attribute["signature"].found_kind == "Signature"
    assert by_attribute["refusal_set"].target == "pymergetic.rxf.foundation.StdlibRefusals"
    assert by_attribute["refusal_set"].found_kind == "RefusalSet"


def test_the_call_reference_of_a_graph_resolves_to_its_callee(world: resolve.World):
    """The CALL card names its callee by path (native/graph.h): a `callee=` value must
    resolve to the Function it names, a leaf-backed one or a graph that is a program of
    its own. The two calls of `selfedit.double` name a library Function and a checked
    add, both Functions."""
    text = (SELFEDIT / "double.xml").read_text()
    references = resolve.check_document(text, known=world, source="tests/selfedit/double.xml")
    callees = [r for r in references if r.attribute == "callee"]
    assert {r.value for r in callees} == {
        "pymergetic.rxf.library.checkout_total",
        "pymergetic.rxf.numeric.checked.add_uint32_t",
    }
    assert all(r.resolved and r.found_kind == "Function" for r in callees), [
        (r.value, r.problem) for r in callees if not r.resolved
    ]


@pytest.mark.parametrize(
    "relative",
    ["pymergetic/rxf/foundation/safe.xml", "pymergetic/rxf/foundation/graph.xml"],
)
def test_a_starter_delta_resolves(world: resolve.World, relative: str):
    path = STARTER / relative
    references = resolve.check_document(
        path.read_text(),
        known=world,
        default_parent=resolve.layout_default_parent(STARTER, path),
        source=relative,
    )
    assert len(references) >= 3  # the signatures of the Functions and their refusal set
    assert any(r.attribute == "signature" for r in references)
    assert any(r.attribute == "refusal_set" for r in references)
    assert all(reference.resolved for reference in references), [
        (r.attribute, r.value, r.problem) for r in references if not r.resolved
    ]


TRIPLE = (SELFEDIT / "triple.xml").read_text()


@pytest.mark.parametrize(
    ("old", "new", "attribute", "problem"),
    [
        (
            "pymergetic.rxf.selfedit.triple.signature",
            "pymergetic.rxf.selfedit.triple.nope",
            "signature",
            "is not declared",
        ),
        (
            "pymergetic.rxf.foundation.StdlibRefusals",
            "pymergetic.rxf.foundation.NoSuchSet",
            "refusal_set",
            "is not declared",
        ),
        (
            'rxf:parent="pymergetic.rxf.selfedit"',
            'rxf:parent="pymergetic.rxf.nowhere"',
            "rxf:parent",
            "is not declared",
        ),
        (
            "pymergetic.rxf.selfedit.triple.signature",
            "@nobody.triple.signature",
            "signature",
            "no alias",
        ),
        (
            "pymergetic.rxf.selfedit.triple.signature",
            "pymergetic.rxf.selfedit.triple",
            "signature",
            "is a Function, not a Signature",
        ),
    ],
)
def test_a_reference_that_does_not_resolve_says_why(
    world: resolve.World, old: str, new: str, attribute: str, problem: str
):
    text = TRIPLE.replace(old, new, 1)
    assert text != TRIPLE
    broken = [
        reference
        for reference in resolve.check_document(text, known=world)
        if reference.attribute == attribute and not reference.resolved
    ]
    assert len(broken) == 1
    assert broken[0].problem is not None and problem in broken[0].problem


def test_an_alias_is_not_visible_outside_its_module(world: resolve.World):
    text = (
        '<delta xmlns:rxf="urn:pymergetic:rxf">\n'
        '  <Module name="app">\n'
        '    <Alias name="here" target="pymergetic.rxf.selfedit" />\n'
        '    <Function name="f" signature="@here.double.signature" />\n'
        "  </Module>\n"
        '  <Module name="other">\n'
        '    <Function name="g" signature="@here.double.signature" />\n'
        "  </Module>\n"
        "</delta>\n"
    )
    by_site = {reference.site: reference for reference in resolve.check_document(text, known=world)}
    assert by_site["app.f"].resolved and by_site["app.f"].target == (
        "pymergetic.rxf.selfedit.double.signature"
    )
    assert not by_site["other.g"].resolved
    assert by_site["other.g"].problem is not None and "no alias" in by_site["other.g"].problem


def test_only_a_top_level_element_names_a_parent(world: resolve.World):
    text = TRIPLE.replace(
        '<Signature name="signature"',
        '<Signature name="signature" rxf:parent="pymergetic.rxf"',
    )
    broken = [
        reference
        for reference in resolve.check_document(text, known=world)
        if reference.attribute == "rxf:parent" and not reference.resolved
    ]
    assert len(broken) == 1  # the Function is fine; the Signature may not name one
    assert broken[0].problem is not None and "top-level" in broken[0].problem


def test_the_document_stands_partly_on_its_own():
    """A delta is checked against what it declares and what is known: the nested
    Signature of the Function resolves from the document alone, the refusal set
    and the parent are the world's."""
    by_attribute = {
        reference.attribute: reference
        for reference in resolve.check_document(TRIPLE)
    }
    assert by_attribute["signature"].resolved
    assert not by_attribute["refusal_set"].resolved
    assert not by_attribute["rxf:parent"].resolved
