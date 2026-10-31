"""The module tree view of the inspector (docs/21, docs/23 §4.6).

The API is landed and pinned by ``test_module_tree.py`` — ``Image.resolve_module`` is the one
``mp_import_stat`` answers — and ``test_mount.py`` pins the host's import walk over the same
trichotomy. This file pins the VIEW: the module tree the inspector renders answers the same
trichotomy the API does, through the same adapter face (docs/21: one face, fills may differ),
so what the user sees is what the guest imports.

What these pins hold:

* the forest starts at the API's start points: the Quad (``module_roots()``) — named whether
  or not the image spells them, a root the image does not spell shown as an empty place —
  plus every other top-level Module cell (the image's own trees: ``pymergetic`` in the
  starter, a user's ``mygame`` beside it);
* every node's ``stat`` IS ``Image.resolve_module`` of its path (package / module / none),
  and a package's children are ``module_children`` in order — the view never re-derives the
  tree, it renders the API's answer;
* a name the API refuses (an alias spelling, an unknown root) is answered as the refusal
  itself, never guessed into a wrong trichotomy;
* the app serves it at ``/api/modules`` and the inspector page carries the tree (the
  ``#module-tree`` mount, beside the flat object tree) wired to that one feed;
* the mount's own corpus (test_mount's guest import walk) holds the view to the same
  trichotomy over the whole starter: every package the view names is a package the guest can
  import from, every leaf module one it imports, and no module name the view shows ever
  answers ``none``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector

STARTER = Path(__file__).parents[1] / "starter"

# The module tree corpus of test_module_tree.py, verbatim: one image with the Quad spelled,
# a package with sub-modules, bare words (self-contained modules), and a program node.
MODULES = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Module" form="Form.Struct" size="0" align="1" node_kind="NodeKind.Module"/>
  <Type name="Fn" form="Form.Struct" size="0" align="1" node_kind="NodeKind.Fn"/>
  <Type name="Code" form="Form.Struct" size="0" align="1" node_kind="NodeKind.Code"/>
  <Module name="px">
    <Module name="math">
      <Fn name="sin">
        <Code name="bytecode"/>
      </Fn>
    </Module>
  </Module>
  <Module name="pm">
    <Module name="gc"/>
  </Module>
  <Module name="pt"/>
  <Module name="py">
    <Module name="machine"/>
  </Module>
  <Module name="alpha">
    <Module name="beta"/>
    <Module name="gamma"/>
  </Module>
  <Module name="math">
    <Fn name="sin">
      <Code name="bytecode"/>
    </Fn>
  </Module>
</delta>"""


@pytest.fixture
def modules() -> Inspector:
    image = Image.empty()
    image.apply(MODULES)
    return Inspector(image)


@pytest.fixture(scope="module")
def starter() -> Inspector:
    return Inspector(tree.load(STARTER))


# ---- the forest: the start points the API gives ----------------------------------------------------


def test_the_forest_starts_at_the_quad_and_the_top_level_module_cells(modules):
    forest = modules.module_tree("")
    assert forest["stat"] == "root"
    # the Quad first, in module_roots() order, then every other top-level Module cell
    assert [row["name"] for row in forest["children"]] == [
        "px",
        "pm",
        "pt",
        "py",
        "alpha",
        "math",
    ]
    stats = {row["name"]: row["stat"] for row in forest["children"]}
    assert stats == {
        "px": "package",  # px.math is a sub-module: what makes it a package
        "pm": "package",
        "pt": "module",  # a leaf: no sub-modules
        "py": "package",
        "alpha": "package",
        "math": "module",  # a bare word: the semantic tree of one program
    }


def test_every_forest_row_is_the_apis_answer(modules):
    image = modules.image
    forest = modules.module_tree("")
    for row in forest["children"]:
        assert row["stat"] == image.resolve_module(row["name"]), row["name"]
        assert row["kind"] == "Module"
        assert row["count"] == len(image.module_children(row["name"]))


def test_a_quad_root_the_image_does_not_spell_is_an_empty_place():
    """A starter-like image that holds no ``px``/``pm``/``pt``/``py`` cells still names the
    Quad — the roots are the format's, not the image's — and shows each unspelled root as
    ``none``: an empty place, never a hidden one."""
    image = Image.empty()
    image.apply('<delta xmlns:rxf="urn:pymergetic:rxf"></delta>')
    forest = Inspector(image).module_tree("")
    assert [row["name"] for row in forest["children"]] == ["px", "pm", "pt", "py"]
    assert all(row["stat"] == "none" for row in forest["children"])
    assert all(row["kind"] is None for row in forest["children"])


def test_the_starter_forest_carries_the_images_own_tree(starter):
    forest = starter.module_tree("")
    names = [row["name"] for row in forest["children"]]
    assert names[:4] == ["px", "pm", "pt", "py"]  # the Quad, the format's four
    assert "pymergetic" in names  # the starter's own top-level tree
    top = {row["name"]: row for row in forest["children"]}["pymergetic"]
    assert top["stat"] == "package" and top["count"] == 1  # pymergetic.rxf


# ---- the recursion: a package's children, the API's order -------------------------------------------


def test_a_packages_children_are_the_apis_children_in_order(modules):
    image = modules.image
    alpha = modules.module_tree("alpha", depth=1)
    assert alpha["stat"] == "package"
    assert [row["name"] for row in alpha["children"]] == image.module_children("alpha")
    assert [row["name"] for row in alpha["children"]] == ["beta", "gamma"]
    for row in alpha["children"]:
        assert row["stat"] == image.resolve_module(f"alpha.{row['name']}")
        assert row["count"] == 0  # beta and gamma are leaves


def test_the_depth_bounds_the_recursion(modules):
    flat = modules.module_tree("px", depth=0)
    assert flat["stat"] == "package"
    assert flat["children"] == []  # depth 0: this node alone, no children fetched
    assert flat["count"] == 1  # the count is the API's answer: px holds math, unfetched
    one = modules.module_tree("px", depth=1)
    assert [row["name"] for row in one["children"]] == ["math"]
    assert one["children"][0]["children"] == []  # a leaf module has no children at any depth
    # a module with program children is a leaf of the MODULE tree: math's Fn never shows
    deep = modules.module_tree("px", depth=8)
    assert [row["name"] for row in deep["children"]] == ["math"]
    assert deep["children"][0]["stat"] == "module"


def test_a_program_node_is_never_in_the_module_tree(modules):
    # the trichotomy of the API: a Function is an attribute of its module, not a module
    assert modules.module_tree("px.math", depth=2)["stat"] == "module"
    assert modules.module_tree("px.math", depth=2)["children"] == []
    # the module tree shows px -> math (module) and nothing below: sin is not a sub-module
    px = modules.module_tree("px", depth=2)
    ((math_row,)) = [row for row in px["children"] if row["name"] == "math"]
    assert math_row["stat"] == "module" and math_row["children"] == []


# ---- refusals are the API's, surfaced as refusals -------------------------------------------------


def test_a_refused_name_is_answered_as_the_refusal_itself(modules):
    """The API never guesses a trichotomy for a name that spells no one clear module; neither
    does the view — it surfaces the refusal's own words (docs/21: refusals are first-class
    UI). (The empty name is not a module name in the view: it is the forest — the API refuses
    it as one, the route serves it as the start points.)"""
    for name in ("@call.Callee", "px.", "px..math"):
        got = modules.module_tree(name)
        assert got["stat"] == "refused", name
        assert got["refusal"], name
        assert got["children"] == [], name
    for name in ("Type.form", "zz.foo"):
        got = modules.module_tree(name)
        assert got["stat"] == "refused" and "unknown root" in got["refusal"], name
    # and a plain miss is a miss: none, not a refusal
    assert modules.module_tree("px.nope")["stat"] == "none"


def test_the_starter_walk_agrees_with_the_api_everywhere(starter):
    """The view over the starter's real tree: every package the view can reach is a package
    the API answers for, and the children shown are the API's list — value for value, over
    the whole tree (the starter's module tree is three levels deep)."""
    image = starter.image

    def walk(name: str, depth: int) -> None:
        got = starter.module_tree(name, depth=1)
        if got["stat"] not in ("package", "root"):
            return
        for row in got["children"]:
            full = f"{name}.{row['name']}" if name else row["name"]
            assert row["stat"] == image.resolve_module(full), full
            assert row["count"] == len(image.module_children(full)), full
            if depth > 0:
                walk(full, depth - 1)

    walk("", 3)


# ---- the same trichotomy the guest imports (the mount corpus, test_mount) --------------------------


def test_every_module_the_view_names_the_guest_can_import(starter):
    """test_mount's pin, from the view side: the mounted tree is the guest's tree — every
    name the view shows as package or module is one the guest's import walk answers for, and
    no module name the view holds ever answers ``none``."""
    image = starter.image
    mounted_names = set(image.mount_names())

    def walk(name: str, depth: int) -> set[str]:
        shown: set[str] = set()
        got = starter.module_tree(name, depth=1)
        rows = got["children"] if got["stat"] in ("package", "root") else []
        for row in rows:
            full = f"{name}.{row['name']}" if name else row["name"]
            if row["stat"] in ("package", "module"):
                assert full in mounted_names, full
                shown.add(full)
                if depth > 1:
                    shown |= walk(full, depth - 1)
        return shown

    shown = walk("", 5)
    assert "pymergetic.rxf" in shown and "pymergetic.rxf.kinds" in shown
    assert len(shown) > 40  # the starter's module tree is real


# ---- the app serves it; the page carries it --------------------------------------------------------


def test_the_app_serves_the_module_tree_route(modules):
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.server.app import create_app

    app = create_app(modules.image)
    handlers = {
        route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)
    }
    assert "/api/modules" in handlers
    forest = handlers["/api/modules"]()
    assert [row["name"] for row in forest["children"]][:4] == ["px", "pm", "pt", "py"]
    deep = handlers["/api/modules"]("alpha", 1)
    assert deep["stat"] == "package"
    assert [row["name"] for row in deep["children"]] == ["beta", "gamma"]
    # the refusal is served too, words and all (a 404 would bury it)
    refused = handlers["/api/modules"]("Type.form", 1)
    assert refused["stat"] == "refused" and "unknown root" in refused["refusal"]


def test_the_inspector_page_carries_the_module_tree_wired_to_the_feed():
    """The view is IN the page: the ``#module-tree`` mount sits beside the flat object tree,
    its rows come from ``/api/modules`` (the one feed), and the Quad's four names are the
    opening view (docs/23 §4.6: the user remembers the module-based tree — it is visible)."""
    from pymergetic.rxftk.server.app import PAGE

    html = PAGE.read_text(encoding="utf-8")
    assert 'id="module-tree"' in html
    assert 'href="/static/ui/ui.css"' in html  # one stylesheet (docs/21, no fork)
    script = re.search(r"<script>(.*)</script>", html, re.DOTALL)
    assert script, "the inspector page carries no inline script"
    assert "/api/modules" in script.group(1)
    # the module tree is the opening view beside the object tree, not a replacement
    assert '<h2 class="eyebrow">Object tree</h2>' in html
    assert '<h2 class="eyebrow">Module tree</h2>' in html
    assert html.index('id="module-tree"') < html.index('id="tree"')

    # the Quad is spelled in the page's opening logic: the four names are the first paint
    forest_call = re.search(r'modulesOf\("",\s*1\)', script.group(1))
    assert forest_call, "the module tree never fetches the forest (the Quad's first level)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_module_tree_script_parses(tmp_path):
    """The page's inline script (module tree included) parses under node."""
    from pymergetic.rxftk.server.app import PAGE

    found = re.search(r"<script>(.*)</script>", PAGE.read_text(), re.DOTALL)
    assert found is not None
    path = tmp_path / "page.js"
    path.write_text(found.group(1))
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
