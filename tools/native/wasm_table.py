"""The wasm32 seat's function table: the image's leaf bodies, as one module.

On wasm32 a function slot's address IS the leaf id -- an index into table 0
(``tools/native/link.py``: "wasm32 calls through table 0, whose index is the
id"). The bodies that table must hold are the image's own Code cells (the
arena carries every leaf's wasm32 code, seed-built from ``native/*.rxfs``);
WebAssembly cannot call code that sits in linear memory, so the seat's host
composes them into one module over the boot module's memory and table and
fills the table by id. The convention is the composer's
(``tools/native/wasm_module.py``: memory 0, table 0, global 0) and the fill is
the one ``tests/wasm_steps.js`` performs -- ``table.set(id, exports[leaf{id}])``.

    python tools/native/wasm_table.py OUT.wasm         # the module of this tree

This tool is bytes packing: it reads the image and writes a module. It never
generates source.
"""

from __future__ import annotations

import sys
from pathlib import Path

import compose
import graph_header
import programs
import seed_build
import wasm_module

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image

ROOT = Path(__file__).resolve().parents[2]
STARTER = ROOT / "starter"
RUN = "pymergetic.rxf.foundation.graph.run"
MACHINE = "wasm32"


class WasmTableError(Exception):
    """The image does not carry what the table must hold."""


def leaf_bodies(image: Image) -> dict[int, bytes]:
    """``{leaf id: wasm32 body}`` of every leaf of the image's interpreter.

    The walk is the one of ``tests/graph_world.py``'s ``code_of``: a leaf is a
    ``Leaf`` reference of the run, its ``Code`` child named for the machine
    carries the entry offset, and the bytes live in the ``CodeBlob`` that body
    names. The seat's table must hold exactly these bytes -- the code the
    image carries, not a second build of it.
    """
    composer = compose.Composer(image)
    runner = composer.target(RUN)
    found: dict[int, bytes] = {}
    for identifier, target in composer.references(runner, "Leaf"):
        for child in composer.children(target, "Code"):
            if image.path(child).rsplit(".", 1)[-1] != MACHINE:
                continue
            body = image.resolve(dict(composer.fields(child)).get("body", ""))
            if body is None or image.type_name(body) != "CodeBlob":
                raise WasmTableError(f"{image.path(child)} says no blob for its bytes")
            found[identifier] = bytes.fromhex(
                programs.fields_of(image, body).get("data", "")
            )
    return found


def call_types() -> list[list[str]]:
    """One type per leaf in the order of its ids -- the type section's head.

    The bodies name these positions in their ``call_indirect`` instructions
    (``tools/native/compile_build.py``), so the list is the type section and
    is never reordered or deduplicated there.
    """
    return [
        [wasm_module.wasm_type(letter) for letter in seed_build.LEAF_LETTERS[name]]
        for name, _ in sorted(graph_header.leaves().items(), key=lambda item: item[1])
    ]


def module(image: Image) -> bytes:
    """The function-table module of this image's leaves."""
    bodies = leaf_bodies(image)
    # the leaves the image's graph.run wires (docs/23 4.9, step 0): an image may wire fewer
    # leaves than the header names; each one it wires must carry wasm32 code
    composer = compose.Composer(image)
    wired = {i for i, _ in composer.references(composer.target(RUN), "Leaf")}
    wanted = {
        key: identifier
        for key, identifier in graph_header.leaves().items()
        if identifier in wired
    }
    missing = set(wanted.values()) - set(bodies)
    if missing:
        raise WasmTableError(
            f"the image carries no wasm32 code for leaves {sorted(missing)}"
        )
    missing_letters = set(wanted) - set(seed_build.LEAF_LETTERS)
    if missing_letters:
        raise WasmTableError(f"no call letters for {sorted(missing_letters)}")
    types = call_types()  # the type section's head: one per leaf, in id order
    by_id = {
        identifier: [
            wasm_module.wasm_type(letter)
            for letter in seed_build.LEAF_LETTERS[name]
        ]
        for name, identifier in wanted.items()
    }
    return wasm_module.module_of(
        [(f"leaf{identifier}", by_id[identifier], bodies[identifier]) for identifier in sorted(bodies)],
        types,
    )


def main() -> None:
    arguments = sys.argv[1:]
    start = STARTER
    if arguments[:1] == ["--image"]:
        start = Path(arguments[1])
        arguments = arguments[2:]
    if len(arguments) != 1:
        raise SystemExit(__doc__)
    image = programs.filled(tree.load(start))
    out = Path(arguments[0])
    data = module(image)
    out.write_bytes(data)
    print(f"leaf table: {out} {len(data)} bytes, {len(graph_header.leaves())} leaves")


if __name__ == "__main__":
    main()
