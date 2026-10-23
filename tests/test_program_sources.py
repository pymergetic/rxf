"""The four large programs are their Seed source and its recipe (docs/29).

``foundation.compile:program``, ``foundation.live:pump`` and
``foundation.graph:run`` are Seed ``fn``s of ``native/compile.rxfs``,
``native/live.rxfs`` and ``native/graph.rxfs``, built per machine by
``tools/native/compile_build.py``, ``live_build.py`` and ``graph_build.py``;
``foundation.tiny:compile`` is the compiler as a program (docs/19, WP12): the
bytecode ``tools/native/rxfc.py`` builds from ``native/tiny/tiny.rxfs``. The
starter stores what these recipes return.

That is the license of Wave 4b step 2. The code of these four is the output of
a recipe over a stored source, byte for byte -- so the image can carry the
source and the recipe, and the builder can produce the code on demand. The
sizes this drops are never stored again: the 9.48 MB of ``compile.program``,
the 0.70 MB of ``tiny.compile``, the 0.57 MB of ``live.pump`` and ``graph.run``
are 96.7% of what the starter carries as code.

``tests/test_tiny.py`` pins the deeper half of the same claim on every machine:
the compiler as a program writes what the compiler of the image writes.
"""

from pathlib import Path

import compile_build
import graph_build
import live_build
import rxfc

from pymergetic.rxftk.core import tree

from .snippets import STARTER

NATIVE = Path(__file__).parents[1] / "native"

BUILT = {
    "pymergetic.rxf.foundation.compile.program": compile_build,
    "pymergetic.rxf.foundation.graph.run": graph_build,
    "pymergetic.rxf.foundation.live.pump": live_build,
}
TINY = "pymergetic.rxf.foundation.tiny.compile"


def function(image, path: str) -> int:
    for handle in range(image.handle_count):
        if image.live(handle) and image.type_name(handle) == "Function":
            if image.path(handle) == path:
                return handle
    raise AssertionError(path)


def bodies(image, path: str) -> dict[str, str]:
    """The data of every ``CodeBlob`` of the function, keyed by machine."""
    parent = function(image, path)
    found = {}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "CodeBlob":
            continue
        if image.get(handle)["parent"] != parent:
            continue
        name = image.get(handle)["name"]
        found[name.removeprefix("body_")] = dict(image.fields(handle))["data"]
    return found


def program_of(image, path: str) -> str:
    """The data of the one ``bytecode`` Code of the function."""
    parent = function(image, path)
    codes = [
        handle
        for handle in range(image.handle_count)
        if image.live(handle)
        and image.type_name(handle) == "Code"
        and image.get(handle)["parent"] == parent
    ]
    assert len(codes) == 1, path
    return dict(image.fields(codes[0]))["data"]


def test_each_native_program_is_its_source_built_by_its_recipe():
    image = tree.load(STARTER)
    for path, recipe in BUILT.items():
        assert recipe.build() == bodies(image, path), path


def test_the_compiler_as_a_program_is_its_source_built_by_rxfc():
    image = tree.load(STARTER)
    built = rxfc.compile_file(NATIVE / "tiny" / "tiny.rxfs").hex()
    assert built == program_of(image, TINY)
