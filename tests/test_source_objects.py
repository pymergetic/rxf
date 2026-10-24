"""The build history in the image: Source objects and the code they built (docs/31 §3).

``tests/test_native.py`` proves on the host that every starter snippet is what the
sources compile to. Here the same chain is visible inside the image:

    Source --(build)--> Code --(graph_run)--> effect

One ``Source`` object per file of ``native/`` carries the text that was built and
the digest of that text (``sha256(text)``: the digest of what it means, its
source). The edge is stored on the built code (``Code.source``, a field is the
reference); the codes a source builds are computed from it, as the model computes
the other direction (``tests/test_starter_delta.py``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import seed_build
import source_sync
import sync
from pymergetic.rxftk.core import tree

from .support import resolved

STARTER = Path(__file__).parents[1] / "starter"
TARGETS = (
    "x86_64_linux",
    "x86_64_bios",
    "x86_64_uefi",
    "aarch64_linux",
    "aarch64_uefi",
    "wasm32",
)


def built() -> dict[str, str]:
    """Every snippet key with the path of its Function (``sync.leaf_paths``)."""
    return sync.leaf_paths(STARTER, set(seed_build.keys()))


def source_names() -> dict[str, Path]:
    """The Source objects by name with the file of ``native/`` each carries."""
    return {
        source_sync.name_of(seed_build.source_of(key)): Path(
            seed_build.source_of(key)
        )
        for key in seed_build.keys()
    }


def rows() -> dict[str, tuple[Path, str, str]]:
    """Every Source object by name: the file it carries, its lang and its arch."""
    native = {
        name: (file, "Rxfs", "Any") for name, file in source_names().items()
    }
    boot = {
        name: (file, lang, arch)
        for name, file, lang, arch in source_sync.boot_rows()
    }
    assert not native.keys() & boot.keys()
    return native | boot


def codes_of(image, handle: int) -> dict[str, int]:
    """The Code objects of a function by target name."""
    return {
        image.get(child)["name"]: child
        for child in image.children(handle)
        if image.type_name(child) == "Code"
    }


def test_every_source_carries_the_text_of_the_file_it_names():
    image = tree.load(STARTER)
    for name, (file, lang, arch) in rows().items():
        text = file.read_bytes()
        fields = dict(image.fields(resolved(image, f"pymergetic.rxf.sources.{name}")))
        assert bytes.fromhex(fields["data"]) == text, name
        assert int(fields["byte_count"]) == len(text), name
        assert fields["semantic_digest"] == hashlib.sha256(text).hexdigest(), name
        assert str(fields["lang"]).endswith(lang), name
        assert str(fields["arch"]).endswith(arch), name


def test_every_file_of_boot_is_a_source_object():
    """The table is total (boot/README.md's Appendix B): a new file of ``boot/`` is
    lifted, or the table says why it is not. Only the README (documentation) and
    the build directory (derived data) are outside."""
    boot = Path(__file__).parents[1] / "boot"
    files = {
        f"boot/{path.relative_to(boot).as_posix()}"
        for path in boot.rglob("*")
        if path.is_file() and "build" not in path.relative_to(boot).parts
    }
    assert files - {"boot/README.md"} == set(source_sync.BOOT_ARCH), files


def test_every_native_code_names_the_source_it_was_built_from():
    image = tree.load(STARTER)
    names = source_names()
    built_codes = 0
    for key, path in built().items():
        source = source_sync.name_of(seed_build.source_of(key))
        assert source in names, key
        codes = codes_of(image, resolved(image, path))
        assert set(codes) >= set(TARGETS), key
        for name, code in codes.items():
            if name == "bytecode":
                continue  # the program of its graph: the image builds it
            named = dict(image.fields(code))["source"]
            assert named == f"pymergetic.rxf.sources.{source}", key
            built_codes += 1
    assert built_codes == 321 * len(TARGETS) == 1926


def test_the_codes_a_source_builds_are_the_codes_that_name_it():
    """The other direction of the edge is computed, and it is total."""
    image = tree.load(STARTER)
    expected: dict[str, set[str]] = {name: set() for name in source_names()}
    for key, path in built().items():
        source = source_sync.name_of(seed_build.source_of(key))
        expected[source].update(f"{path}.{target}" for target in TARGETS)
    builds: dict[str, set[str]] = {name: set() for name in expected}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Code":
            continue
        named = dict(image.fields(handle)).get("source")
        if named is None:
            continue  # the bytecode of a semantic function: the image builds it
        source = str(named).rsplit(".", 1)[-1]
        assert source in builds, named
        builds[source].add(image.path(handle))
    assert builds == expected


def test_the_sources_of_the_image_are_the_module_the_docs_describe():
    image = tree.load(STARTER)
    module = resolved(image, "pymergetic.rxf.sources")
    assert image.type_name(module) == "Module"
    names = {image.get(child)["name"] for child in image.children(module)}
    assert names == set(rows()) | {"kinds"}, names
    source_type = resolved(image, "pymergetic.rxf.execution.Source")
    assert image.type_name(source_type) == "Type"
    fields = {
        image.get(field)["name"]
        for field in image.children(source_type)
        if image.type_name(field) == "Field"
    }
    assert fields == {"arch", "byte_count", "lang", "semantic_digest"}, fields
