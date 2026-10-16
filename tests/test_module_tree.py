"""The hybrid module tree (m1, docs/23 §4.6): the image IS the module tree.

The module tree serves BOTH import directions over ONE storage: rximport compiles modules into
it (C/LLVM source -> named cells of an installed tree), and MicroPython later reads it AS its
module tree (``mp_import_stat`` over the same named cells). There is no filesystem, no ``.mpy``,
no second storage: a module is a named cell under a root, and the four roots are the Quad
(``px`` = extern/cpython, ``pm`` = mpy, ``pt`` = thirdparty, ``py`` = micro).

The trichotomy is the one ``mp_import_stat`` returns, so the upy port's fill is one call into
``Image.resolve_module``:

- ``"package"`` — a Module cell with sub-modules (MP_IMPORT_STAT_DIR);
- ``"module"``  — a Module cell without sub-modules: a leaf, or a container with program
  children (the semantic tree of one program — MP_IMPORT_STAT_FILE). A bare word is such a
  self-contained module;
- ``"none"``    — anything else (MP_IMPORT_STAT_NO): a name that names nothing, or names a
  program node (a Function is an attribute of its module, never a module name).

Refusals stay honest — a name that does not spell one clear module raises TreeError (unknown
root, ambiguous name, dead handle) instead of answering a wrong trichotomy.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.core.tree import TreeError

STARTER = Path(__file__).parents[1] / "starter"

# One delta of module tree: the Quad (the four roots and nothing else), a package with its
# sub-modules, self-contained modules (bare words: the semantic tree of one program), and one
# program node that is not a module. Every cell is an ordinary named cell below the existing
# root — the module tree adds no ground object and no new ground type.
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

WORDS = struct.pack("<5Q", 0x31475852, 0, 2, 0, 0)  # RXG1 header; word[2] is the parameter count


@pytest.fixture
def modules() -> Image:
    image = Image.empty()
    image.apply(MODULES)
    return image


# ---- the trichotomy ----------------------------------------------------------------------------


def test_a_module_installed_through_the_delta_path_resolves_as_module(modules):
    # a container with program children: the semantic tree of one program, still one module
    assert modules.resolve_module("px.math") == "module"
    assert modules.resolve("px.math.sin") is not None
    assert modules.resolve("px.math.sin.bytecode") is not None
    # a leaf module is a module too
    assert modules.resolve_module("pm.gc") == "module"


def test_a_package_resolves_as_package_with_its_children_listed(modules):
    assert modules.resolve_module("alpha") == "package"
    assert modules.module_children("alpha") == ["beta", "gamma"]
    # a sub-module of a package is a module of its own
    assert modules.resolve_module("alpha.beta") == "module"


def test_an_unknown_name_resolves_as_none(modules):
    assert modules.resolve_module("nope") == "none"
    assert modules.resolve_module("px.nope") == "none"
    assert modules.resolve_module("px.math.nope") == "none"


def test_the_quad_roots_are_the_four_and_nothing_else(modules):
    assert modules.module_roots() == ["px", "pm", "pt", "py"]  # the Quad, and nothing else
    # the Quad is real named cells under the root, each resolvable from its first segment
    for root in ("px", "pm", "pt", "py"):
        assert modules.resolve(root) is not None
        assert modules.resolve_module(root) in ("package", "module")
    assert modules.resolve_module("px") == "package"
    assert "math" in modules.module_children("px")


def test_bare_words_resolve_as_self_contained_modules(modules):
    # a bare word means a self-contained module: the semantic tree of one program, no sub-modules
    assert modules.resolve_module("math") == "module"
    assert modules.module_children("math") == []


def test_a_program_node_is_an_attribute_not_a_module(modules):
    # anything else NO (docs/23 §4.6): the guest's `import` reaches modules; a Function is read
    # as an attribute of its module after the import
    assert modules.resolve_module("px.math.sin") == "none"
    assert modules.resolve_module("px.math.sin.bytecode") == "none"


# ---- refusals stay honest ----------------------------------------------------------------------


def test_a_name_that_spells_no_one_module_is_refused_never_guessed(modules):
    with pytest.raises(TreeError, match="ambiguous name"):
        modules.resolve_module("px..math")
    with pytest.raises(TreeError, match="ambiguous name"):
        modules.resolve_module("")
    with pytest.raises(TreeError, match="ambiguous name"):
        modules.resolve_module("@call.Callee")  # an alias is a nickname, not a module name
    with pytest.raises(TreeError, match="ambiguous name"):
        modules.module_children("px.")


def test_an_unknown_root_is_a_refusal_not_a_none(modules):
    with pytest.raises(TreeError, match="unknown root"):
        modules.resolve_module("zz.foo")  # no root named zz: the claim fails, the walk refuses
    # the ground is not a root of the module tree, and its names are not module names
    with pytest.raises(TreeError, match="unknown root"):
        modules.resolve_module("Type.form")
    assert modules.resolve_module("Type") == "none"


def test_a_dead_handle_stops_the_walk_with_a_clear_error():
    # an image that never grew a root: the walk cannot start, and says so
    bare = Image.create()
    with pytest.raises(TreeError, match="dead handle"):
        bare.resolve_module("px")
    with pytest.raises(TreeError, match="dead handle"):
        bare.resolve_module("px.math")


# ---- the install direction: the editor path lands the module tree ------------------------------


class SeamVerdict:
    """What one run of the front end answers (live.front.Verdict)."""

    def __init__(self, words: bytes):
        self.ok = True
        self.words = words
        self.at = 0
        self.status = 0


class InstallingFront:
    """The seam (``live.front.Front``) stood in for by the test: compile answers the words of the
    program, and install sends the seam's own delta (``live.front._install_delta`` — the shape
    ``editor.install_delta_preview`` shows) into the image the test holds."""

    def __init__(self, image: Image, module: str):
        self.image = image
        self.module = module

    def compile(self, source: bytes, files=None):
        return SeamVerdict(WORDS)

    def install(self, name: str, words: bytes, source: bytes):
        from pymergetic.rxf.live import front as seam

        parameters = seam.Front._check_words(words)
        self.image.apply(seam._install_delta(self.module, name, words, source, parameters))
        return seam.Installed(
            path=f"{self.module}.{name}",
            identity=7,
            code_identity=8,
            byte_count=len(words),
            digest=self.image.digest(),
        )

    def numbers(self) -> dict[str, int]:
        return {}

    def rebase(self) -> None:
        """The stand-in stages against the image it holds; there is no other mirror."""


def test_the_editor_install_lands_the_module_tree_resolvable_immediately():
    """End to end through the editor path: the installed nodes appear under their root as named
    cells, and the resolution API answers the right trichotomy immediately after install —
    package (the package above), module (the container with the program's semantic tree), none
    (the program node itself)."""
    from pymergetic.rxf.server import editor

    image = tree.load(STARTER)
    # the console's module, made the delta way (what the front end's boot lands)
    image.apply(
        '<delta xmlns:rxf="urn:pymergetic:rxf">'
        '<Module name="selfedit" rxf:parent="pymergetic.rxf"/>'
        "</delta>"
    )

    front = InstallingFront(image, "pymergetic.rxf.selfedit")
    out = editor.install_source(front, "program double(x) = mul(x, 2)")

    assert out["ok"] is True
    assert out["path"] == "pymergetic.rxf.selfedit.double"

    # what it lands IS the module tree: named cells under their root, immediately resolvable
    assert image.resolve("pymergetic.rxf.selfedit.double") is not None

    # the trichotomy is right, immediately after install
    assert image.resolve_module("pymergetic.rxf") == "package"
    assert "selfedit" in image.module_children("pymergetic.rxf")
    assert (
        image.resolve_module("pymergetic.rxf.selfedit") == "module"
    )  # container with program children
    assert (
        image.resolve_module("pymergetic.rxf.selfedit.double") == "none"
    )  # a program node: an attribute of its module
    assert image.resolve_module("pymergetic.rxf.selfedit.nope") == "none"
