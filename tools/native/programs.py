"""The four large programs are their source and their recipe (Wave 4b step 2).

The starter stores the source and the recipe of ``foundation.compile:program``,
``foundation.live:pump``, ``foundation.graph:run`` and ``foundation.tiny:compile``
and not their code: the code is derived data, and derived data is built, not
stored. ``sync.py`` calls ``strip`` after the tree is written, so the starter
carries what the recipes must fill -- the byte count, and for a native body its
``content_digest`` -- and not the bytes. ``filled`` fills a copy of a loaded
image from the recipes (the Seed CLI over ``native/*.rxfs``, ``rxfc`` over
``native/tiny/tiny.rxfs``): that is what ``link.py`` links and packages, so the
binary a seat runs is complete and the seed a delta carries is source. The
caller's image is untouched -- its digest stays the digest of the seed.

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
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import compile_build
import graph_build
import live_build
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


@functools.lru_cache(maxsize=None)
def native() -> dict[str, dict[str, str]]:
    """``{path: {machine: hex}}`` of the three native programs, from their recipes."""
    return {path: recipe.build() for path, recipe in NATIVE_PROGRAMS.items()}


@functools.lru_cache(maxsize=None)
def tiny() -> str:
    """The hex of the compiler as a program, from ``native/tiny/tiny.rxfs``."""
    return rxfc.compile_file(TINY_SOURCE).hex()


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
    raise KeyError(f"{path}:{name} is not built here")


def fields_of(image, handle: int) -> dict[str, str]:
    """The fields of an object, with the bytes the builder fills in filled in."""
    values = dict(image.fields(handle))
    if values.get("data"):
        return values
    name = str(image.get(handle)["name"])
    path = image.path(image.get(handle)["parent"])
    if path in BUILT and _built_object(name):
        values["data"] = built_data(path, name)
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
        if path not in BUILT or not _built_object(name):
            continue
        values = dict(made.fields(handle))
        if values.get("data"):
            continue  # stored bytes
        data = bytes.fromhex(built_data(path, name))
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
                if path not in BUILT:
                    continue
                for child in function:
                    name = str(child.get("name", ""))
                    if not _built_object(name):
                        continue
                    if fill_it:
                        child.set(DATA, built_data(path, name))
                    elif DATA in child.attrib:
                        del child.attrib[DATA]
                    else:
                        continue
                    changed = True
                    touched += 1
        if changed:
            document.write(file, encoding="UTF-8", xml_declaration=True)
    return touched


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("fill", "strip"):
        raise SystemExit(__doc__)
    starter = Path(sys.argv[2])
    moved = fill(starter) if sys.argv[1] == "fill" else strip(starter)
    print(sys.argv[1], moved, "objects")


if __name__ == "__main__":
    main()
