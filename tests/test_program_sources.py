"""The four large programs are pinned by what their source and recipe build.

``foundation.compile:program``, ``foundation.live:pump`` and
``foundation.graph:run`` are Seed ``fn``s of ``native/*.rxfs``, built per machine
by the Seed CLI; ``foundation.tiny:compile`` is the compiler as a program (docs/19,
WP12), the bytecode ``tools/native/rxfc.py`` builds from ``native/tiny/tiny.rxfs``.
The starter stores the source, the recipe and the pin -- the byte count, and for
a native body its ``content_digest`` -- and not the bytes (Wave 4b step 2): the
code is derived data, and derived data is built, not stored.

This pins that what the builder fills in IS what the pin names. It is the same
claim ``tests/test_tiny.py`` makes from the other side on every machine: the
compiler as a program writes what the compiler of the image writes.
"""

import hashlib

import programs

from pymergetic.rxftk.core import tree

from .snippets import STARTER


def pins_of(image, path: str) -> dict[str, dict[str, str]]:
    """The fields of the built objects of a function, keyed as their names are."""
    parent = next(
        h
        for h in range(image.handle_count)
        if image.live(h) and image.type_name(h) == "Function" and image.path(h) == path
    )
    found = {}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.get(handle)["parent"] != parent:
            continue
        name = str(image.get(handle)["name"])
        if (name.startswith("body_")) or name == "bytecode":
            found[name] = dict(image.fields(handle))
    return found


def test_each_native_program_is_pinned_by_what_its_recipe_builds():
    image = tree.load(STARTER)
    for path, built in programs.native().items():
        pins = pins_of(image, path)
        assert set(pins) == {f"body_{m}" for m in built}, path
        for machine, hex in built.items():
            pin = pins[f"body_{machine}"]
            assert "data" not in pin, f"{path}:{machine} stores what it must build"
            assert int(pin["byte_count"]) == len(hex) // 2
            assert pin["content_digest"] == hashlib.sha256(
                bytes.fromhex(hex)
            ).hexdigest()


def test_the_compiler_as_a_program_is_pinned_by_what_rxfc_builds():
    image = tree.load(STARTER)
    (pin,) = pins_of(image, programs.TINY).values()
    hex = programs.tiny()
    assert "data" not in pin  # the starter stores source and recipe, not the code
    assert int(pin["byte_count"]) == len(hex) // 2
