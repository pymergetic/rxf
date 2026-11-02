"""A body is built from its source and its recipe (Wave 4b step 2; docs/32 5).

The starter stores the source and the recipe of a body and not its code: binary
is storage form and glue only -- it is formed in the image when the machinery
needs it, and it never lives in a delta (docs/32, the delta/image seam).
 ``sync.py`` calls ``strip`` after the tree is written, so the starter
carries what the recipes must fill -- the byte count, and for a native body its
``content_digest`` -- and not the bytes. ``filled`` fills a copy of a loaded
image from the recipes (the Seed CLI over ``native/*.rxfs``, ``rxfc`` over
``native/tiny/tiny.rxfs``): that is what ``link.py`` links and packages, so the
binary a seat runs is complete and the seed a delta carries is source. The
caller's image is untouched -- its digest stays the digest of the seed.

Every body is covered: the three Seed fns (``foundation.compile:program``,
``foundation.live:pump``, ``foundation.graph:run``, compiled per machine by
``compile_build``/``graph_build``/``live_build``), the compiler as a program
(``foundation.tiny:compile`` over ``native/tiny/tiny.rxfs``), and every snippet
of ``native/`` its own seed recipe builds. The glue of the boot is not filled
here: its pins name what ``make`` builds, and a seat links that around the
image it carries.

``fields_of`` is the same fill for a reader, and ``fill``/``strip`` the same at
the tree level (``fill`` also on the command line). The pin is always the digest
the image carries: the fill writes the recipe output, and
``tests/test_program_sources.py`` pins that the recipe output IS what the pin
names.

    python tools/native/programs.py fill STATER    # write the built bytes in
    python tools/native/programs.py strip STATER   # and take them out again
"""

from __future__ import annotations

import functools
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import compile_build
import graph_build
import graph_sync
import live_build
import native_build
import rxfc

from pymergetic.rxftk.core.object import Image

RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
ET.register_namespace("rxf", RXF)

NATIVE = Path(__file__).resolve().parents[2] / "native"
TINY_SOURCE = NATIVE / "tiny" / "tiny.rxfs"

# The function paths whose code the recipes build: the three Seed fns and the
# compiler as a program.
NATIVE_PROGRAMS = {
    "pymergetic.rxf.foundation.compile.program": compile_build,
    "pymergetic.rxf.foundation.graph.run": graph_build,
    "pymergetic.rxf.foundation.live.pump": live_build,
}
TINY = "pymergetic.rxf.foundation.tiny.compile"
BUILT = frozenset(NATIVE_PROGRAMS) | {TINY}


@functools.cache
def native() -> dict[str, dict[str, str]]:
    """``{path: {machine: hex}}`` of the three native programs, from their recipes."""
    return {path: recipe.build() for path, recipe in NATIVE_PROGRAMS.items()}


@functools.cache
def tiny() -> str:
    """The hex of the compiler as a program, from ``native/tiny/tiny.rxfs``."""
    return rxfc.compile_file(TINY_SOURCE).hex()


@functools.cache
def snippets() -> dict[str, dict[str, str]]:
    """``{machine: {key: hex}}`` of every snippet of ``native/``, from its recipes."""
    return native_build.build()


@functools.cache
def known_keys() -> frozenset[str]:
    """The snippet keys the seed toolchain builds (the key table ``sync.py`` writes by)."""
    return frozenset(snippets().get("x86_64", ()))


def _built_object(name: str) -> bool:
    """Whether this object of a built function carries the bytes to fill: the
    blob of a native body, or the program of the compiler as a program. A
    machine Code does not: its bytes live in the blob its ``body`` names."""
    return name.startswith("body_") or name == "bytecode"


def built_data(path: str, name: str) -> str:
    """The hex the builder fills into the object ``name`` of the function ``path``."""
    if path in NATIVE_PROGRAMS and name.startswith("body_"):
        return native()[path][name.removeprefix("body_")]
    if path == TINY and name == "bytecode":
        return tiny()
    if name.startswith("body_"):
        try:
            key = graph_sync.key_of_path(path, known_keys())
        except graph_sync.GraphSyncError as error:
            raise KeyError(str(error)) from error
        return snippets()[name.removeprefix("body_")][key]
    raise KeyError(f"{path}:{name} is not built here")


def fields_of(image, handle: int) -> dict[str, str]:
    """The fields of an object, with the bytes the builder fills in filled in."""
    values = dict(image.fields(handle))
    if values.get("data"):
        return values
    name = str(image.get(handle)["name"])
    path = image.path(image.get(handle)["parent"])
    if _built_object(name):
        try:
            values["data"] = built_data(path, name)
        except KeyError:
            pass  # a body no recipe fills here: the glue's, which the seat links
    return values


def filled(image: Image) -> Image:
    """A copy of the image with the built bytes filled in from their recipes.

    What the builder links and packages: the caller's image is untouched, so the
    digest of a source and its recipe stays the digest of the seed, and what a
    seat runs is complete."""
    made = Image.from_bytes(image.to_bytes())
    for handle in range(made.handle_count):
        if not made.live(handle):
            continue
        name = str(made.get(handle)["name"])
        parent = made.get(handle)["parent"]
        if parent == (1 << 64) - 1:
            continue
        path = made.path(parent)
        if not _built_object(name):
            continue
        values = dict(made.fields(handle))
        if values.get("data"):
            continue  # stored bytes
        try:
            data = bytes.fromhex(built_data(path, name))
        except KeyError:
            continue  # a body no recipe fills here: the glue's, which the seat links
        payload = made.get(handle)["payload"]
        prefix = made.layout(made.get(handle)["type"])["prefix"]
        made.set_payload(handle, payload[:prefix] + data)
    return made


def module_parts(path: Path, starter: Path) -> str:
    """The dotted module path a file defines (``__init__.rxfd`` defines its directory)."""
    parts = path.relative_to(starter).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def fill(starter: Path) -> int:
    """The built bytes of the starter, from their recipes (in place)."""
    return _run(starter, True)


def strip(starter: Path) -> int:
    """Take the built bytes out of the starter: it carries source and recipe."""
    return _run(starter, False)


def _run(starter: Path, fill_it: bool) -> int:
    touched = 0
    for file in sorted(starter.rglob("*.rxfd")):
        document = ET.parse(file)
        changed = False
        for module in document.getroot().iter("Module"):
            prefix = module_parts(file, starter)
            for function in module.iter("Function"):
                path = f"{prefix}.{function.get('name')}"
                for child in function:
                    name = str(child.get("name", ""))
                    if fill_it:
                        if not _built_object(name):
                            continue
                        try:
                            child.set(DATA, built_data(path, name))
                        except KeyError:
                            continue
                    elif _kept_bytes(child, name, path):
                        continue
                    # `_kept_bytes` has taken the bytes out: record the change
                    changed = True
                    touched += 1
        if changed:
            document.write(file, encoding="UTF-8", xml_declaration=True)
    return touched


def _kept_bytes(child: ET.Element, name: str, path: str) -> bool:
    """Whether the delta carries this object's bytes and keeps them: the program
    of a semantic function (the composer's words, as ``who()`` carries its own).
    Everything else is a body of a machine, and a body never stores its bytes --
    not in the blob that pins them, not in the Code that names them."""
    if DATA not in child.attrib:
        return True  # nothing to take out
    if child.tag == "Code":
        if (child.get("format") or "").endswith("Bytecode") and path not in BUILT:
            return True  # the words of a semantic function live in the delta
    elif not name.startswith("body_") and path not in BUILT:
        return True
    del child.attrib[DATA]
    return False


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("fill", "strip"):
        raise SystemExit(__doc__)
    starter = Path(sys.argv[2])
    moved = fill(starter) if sys.argv[1] == "fill" else strip(starter)
    print(sys.argv[1], moved, "objects")


if __name__ == "__main__":
    main()
