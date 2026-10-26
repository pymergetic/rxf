"""The glue's code objects are what the build of boot/ makes (docs/31 §4).

``tools/native/boot_sync.py`` writes one built code object per arch of the glue
that hands control to the image: each Code names the source that defines it and
carries the pin of its body -- the byte count and the ``sha256`` of the artifact
``make -C boot`` builds. The bytes are derived data like every other body; the
pin is what the image carries. Here the pin is proven against a real build: what
``make`` builds IS what the pin names, the same rule
``tests/test_program_sources.py`` pins for the programs the Seed builds.

The toolchain is the one boot/README.md lists. Without it the test skips loudly
and says so; no test pretends a build happened.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

import boot_sync
from pymergetic.rxftk.core import tree

from .support import resolved

STARTER = Path(__file__).parents[1] / "starter"
TOOLS = ["make", "clang", "as", "ld", "wasm-ld"]  # what boot/Makefile runs


def pins() -> dict[str, tuple[int, str]]:
    """The pins the image carries, by body name: ``{body: (byte_count, digest)}``."""
    image = tree.load(STARTER)
    held = {}
    for family in boot_sync.FAMILIES:
        handle = resolved(
            image, f"pymergetic.rxf.boot.{family['function']}.{family['body']}"
        )
        fields = dict(image.fields(handle))
        held[str(family["body"])] = (
            int(fields["byte_count"]),
            fields["content_digest"],
        )
    return held


def test_every_boot_code_names_the_source_that_defines_it():
    image = tree.load(STARTER)
    held = pins()
    for family in boot_sync.FAMILIES:
        function = resolved(image, f"pymergetic.rxf.boot.{family['function']}")
        codes = {
            str(image.get(child)["name"]): child
            for child in image.children(function)
            if image.type_name(child) == "Code"
        }
        assert set(codes) == set(family["codes"]), family["function"]
        for seat, code in codes.items():
            fields = dict(image.fields(code))
            source = boot_sync.source_name(str(family["sources"][0]))
            assert fields["source"] == f"pymergetic.rxf.sources.{source}", seat
            assert fields["body"] == (
                f"pymergetic.rxf.boot.{family['function']}.{family['body']}"
            ), seat
            assert int(fields["byte_count"]) == held[str(family["body"])][0], seat
            assert fields["semantic_digest"] == dict(
                image.fields(function)
            )["semantic_digest"], seat


def test_every_boot_code_names_the_recipe_that_made_it():
    """The same provenance as everything else (docs/31 §4): every code says
    which recipe built it. The glue is no Seed build, so the recipe is the
    ``boot/Makefile`` recipe the module carries as its own ``CompilerProvenance``
    -- nothing in the image claims a compiler that did not run."""
    image = tree.load(STARTER)
    recipe = resolved(image, "pymergetic.rxf.boot.recipe")
    fields = dict(image.fields(recipe))
    assert image.type_name(recipe) == "CompilerProvenance"
    assert fields["compiler_major"] == boot_sync.BOOT_MAJOR
    assert fields["recipe_digest"] == boot_sync.recipe_digest()
    for family in boot_sync.FAMILIES:
        function = resolved(image, f"pymergetic.rxf.boot.{family['function']}")
        for child in image.children(function):
            if image.type_name(child) != "Code":
                continue
            provenance = [
                image.path(target)
                for target, role, _, _, _ in image.get(child)["refs"]
                if role is not None
                and image.path(role).rsplit(".", 1)[-1] == "Provenance"
            ]
            assert provenance == ["pymergetic.rxf.boot.recipe"], image.path(child)


def test_the_pin_is_what_make_builds():
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip("boot build tools missing: " + ", ".join(missing))
    built = boot_sync.build()
    held = pins()
    assert set(built) == set(held)
    for name, data in built.items():
        byte_count, digest = held[name]
        assert byte_count == len(data), name
        assert digest == hashlib.sha256(data).hexdigest(), name


def test_the_uefi_body_reproduces_byte_for_byte():
    """The PE link stamps the header with a wall-clock timestamp unless the
    recipe says otherwise (boot/Makefile: ``/timestamp:0``): two fresh builds
    have to be one artifact, or the pin is only cache-lucky."""
    missing = [tool for tool in TOOLS if shutil.which(tool) is None]
    if missing:
        pytest.skip("boot build tools missing: " + ", ".join(missing))
    family = next(f for f in boot_sync.FAMILIES if f["body"] == "body_uefi")
    first = boot_sync.build()[str(family["body"])]
    (boot_sync.BOOT / str(family["artifact"])).unlink()
    second = boot_sync.build()[str(family["body"])]
    assert first == second


def test_the_starter_carries_the_pin_and_not_the_bytes():
    """Derived data is built, not stored: the bodies of the glue are pins."""
    text = (STARTER / "pymergetic/rxf/boot.rxfd").read_text()
    assert "<CodeBlob" in text
    assert "rxf:data" not in text
