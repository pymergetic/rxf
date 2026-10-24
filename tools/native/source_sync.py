"""The build history of the native code as Source objects of the starter (docs/31 §3).

Every Code object the Seed builds comes from one file of ``native/**``. This module
writes that fact into the image: one ``Source`` object per file -- its text as the
payload, the digest of that text (``sha256(text)``, the digest of what it means:
its source), and the language it is in -- digest-wired to the code objects it was
built into. The glue of ``boot/`` follows the same rule (docs/31 §4): every file
of the boot tree is a Source object too, with the arch it is for. The edge is
stored on the built code (``Code.source``: a field is the
reference); the other direction is computed, as the model computes the other
direction (``tests/test_starter_delta.py``). The codes a source builds are the
code objects that name it.

``sync.py`` calls ``write_sources`` on its working copy after the Code objects are
written and before the tree is canonicalized. The schema it needs (the ``Source``
type in ``pymergetic.rxf.execution``, the ``source`` field on ``Code``, the enums
of ``pymergetic.rxf.kinds``) is ensured here -- the same way ``live_sync`` writes
the Functions it derives from the sources. Running it twice writes the same tree.

    python tools/native/sync.py [--check] [STARTER]

is the entry; this module has none of its own.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping

import graph_sync
import seed_build

ROOT = Path(__file__).resolve().parents[2]
RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
ET.register_namespace("rxf", RXF)

SOURCES = Path("pymergetic/rxf/sources.rxfd")
KINDS = Path("pymergetic/rxf/kinds.rxfd")
EXECUTION = Path("pymergetic/rxf/execution/__init__.rxfd")
ALIAS = "sources"

# The languages of the source files. A native source is Any (a source text is
# not built for one machine -- the Code objects it builds name their targets);
# the glue of ``boot/`` is per arch (docs/31 §4), so its rows carry the arch.
LANG = {
    ".S": "Asm",
    ".c": "C",
    ".h": "C",
    ".js": "Js",
    ".ld": "Asm",
    ".rxfs": "Rxfs",
    ".sh": "Sh",
}

# Each file of ``boot/`` as a Source object (boot/README.md's Appendix B): the
# arch the file is for, ``Any`` where one file serves every seat. The README and
# the build directory are not build history; every other file of the tree is.
BOOT_ARCH = {
    "boot/Makefile": "Any",
    "boot/aarch64.S": "Aarch64",
    "boot/aarch64.ld": "Aarch64",
    "boot/check_map.sh": "Any",
    "boot/image_stub/entry.c": "Any",
    "boot/image_stub/image.ld": "Any",
    "boot/image_stub/uart.h": "Any",
    "boot/image_stub/uart_aarch64.c": "Aarch64",
    "boot/image_stub/uart_wasm32.c": "Wasm32",
    "boot/image_stub/uart_x86_64.c": "X8664",
    "boot/image_stub/world.c": "Any",
    "boot/image_stub/world.h": "Any",
    "boot/run_smoke.sh": "Any",
    "boot/uefi_stub.c": "X8664",
    "boot/wasm_shim.c": "Wasm32",
    "boot/wasm_shim.js": "Wasm32",
    "boot/x86_64.S": "X8664",
    "boot/x86_64.ld": "X8664",
}

SOURCE_TYPE = {
    "name": "Source",
    "form": "Form.Opaque",
    "size": "48",
    "align": "1",
    "flags": "TypeFlags.Variable",
}
SOURCE_FIELDS = (
    {"name": "arch", "value_type": "@kinds.SourceArch", "offset": "4"},
    {"name": "byte_count", "value_type": "uint64_t", "offset": "8"},
    {"name": "lang", "value_type": "@kinds.SourceLang", "offset": "0"},
    {
        "name": "semantic_digest",
        "value_type": "uint8_t",
        "offset": "16",
        "count": "32",
    },
)
# On the Code type after the semantic digest (56 + 32): the type is variable, the
# tail of every code object moves behind it. The field is a reference, so its
# eight bytes are a handle (``owner_function`` sits in the hole at 0 the same way).
CODE_SOURCE_FIELD = {
    "name": "source",
    "flags": "FieldFlags.Optional|Ref",
    "value_type": "uint64_t",
    "offset": "88",
}
ENUMS = {
    "SourceArch": (
        ("Aarch64", "2"),
        ("Any", "4"),
        ("Wasm32", "3"),
        ("X8664", "1"),
    ),
    "SourceLang": (
        ("Asm", "2"),
        ("C", "3"),
        ("Js", "4"),
        ("Make", "5"),
        ("Rxfs", "1"),
        ("Sh", "6"),
    ),
}


def name_of(source: str) -> str:
    """``native/numeric/wrapping.rxfs`` is ``numeric_wrapping``."""
    path = Path(source)
    return "_".join((*path.parts[1:-1], path.stem))


def boot_name_of(source: str) -> str:
    """``boot/image_stub/world.c`` is ``boot_image_stub_world_c`` (the suffix is a
    part of the name: ``boot/`` carries source and link script of one name)."""
    path = Path(source)
    tail = [*path.parts[1:-1], path.stem]
    if path.suffix:
        tail.append(path.suffix[1:])
    return "_".join(("boot", *tail))


def lang_of(file: Path) -> str:
    """The language of a source file: its suffix, or Make for a bare ``Makefile``."""
    return "Make" if not file.suffix else LANG[file.suffix]


def boot_rows() -> list[tuple[str, Path, str, str]]:
    """``(name, file, lang, arch)`` for each file of ``boot/`` (Appendix B)."""
    rows = []
    for source, arch in sorted(BOOT_ARCH.items()):
        file = ROOT / source
        rows.append((boot_name_of(source), file, lang_of(file), arch))
    return rows


def insert_sorted(module: ET.Element, element: ET.Element) -> None:
    """Keep the children of a module in the order the canonical writer keeps them."""
    name = element.get("name", "")
    for position, sibling in enumerate(module):
        if sibling.tag == element.tag and (sibling.get("name") or "") > name:
            module.insert(position, element)
            return
    module.append(element)


def ensure_type(module: ET.Element, name: str, fields: tuple[dict, ...]) -> None:
    """The ``Source`` type of the execution module, once."""
    if any(e.tag == "Type" and e.get("name") == name for e in module):
        return
    element = ET.Element("Type", SOURCE_TYPE)
    for field in fields:
        ET.SubElement(element, "Field", field)
    insert_sorted(module, element)


def ensure_enums(module: ET.Element) -> None:
    for name, enumerators in ENUMS.items():
        existing = next(
            (e for e in module if e.tag == "Type" and e.get("name") == name), None
        )
        if existing is None:
            existing = ET.Element(
                "Type", {"name": name, "form": "Form.Enum", "size": "4", "align": "4"}
            )
            insert_sorted(module, existing)
        known = {e.get("name") for e in existing if e.tag == "Enumerator"}
        for enumerator, value in enumerators:
            if enumerator in known:
                continue
            insert_sorted(existing, ET.Element("Enumerator", {"name": enumerator, "value": value}))


def ensure_field(function: ET.Element, field: dict) -> None:
    if not any(f.get("name") == field["name"] for f in function if f.tag == "Field"):
        insert_sorted(function, ET.Element("Field", field))


def ensure_schema(starter: Path) -> None:
    """The types and the field this module writes objects of."""
    kinds = ET.parse(starter / KINDS)
    ensure_enums(next(kinds.getroot().iter("Module")))
    kinds.write(starter / KINDS, encoding="UTF-8", xml_declaration=True)

    execution = ET.parse(starter / EXECUTION)
    module = next(execution.getroot().iter("Module"))
    ensure_type(module, "Source", SOURCE_FIELDS)
    code = next(e for e in module if e.tag == "Type" and e.get("name") == "Code")
    ensure_field(code, CODE_SOURCE_FIELD)
    execution.write(starter / EXECUTION, encoding="UTF-8", xml_declaration=True)


def module_of(path: Path, starter: Path) -> tuple[str, ET.ElementTree]:
    parts = path.relative_to(starter).with_suffix("").parts
    return graph_sync.module_prefix(parts), ET.parse(path)


def add_alias(document: ET.ElementTree, name: str, target: str) -> None:
    module = next(document.getroot().iter("Module"))
    if any(a.tag == "Alias" and a.get("name") == name for a in module):
        return
    insert_sorted(module, ET.Element("Alias", {"name": name, "target": target}))


def set_sources(starter: Path, built: Mapping[str, str]) -> dict[str, str]:
    """Name the source on every Code object of every function ``built`` maps.

    ``built`` is ``leaf_paths``: every snippet key with the path of the Function
    its Code objects belong to (``sync.leaf_paths``). Returns each source by name
    with the key that carries its file. The alias each module needs to write the
    reference short (``@sources.numeric_wrapping``) is added where a reference is
    written.
    """
    functions = {path: key for key, path in built.items()}
    named: dict[str, str] = {}
    for path in sorted(starter.rglob("*.rxfd")):
        prefix, document = module_of(path, starter)
        changed = False
        for function_path, function in graph_sync.functions_in(
            document.getroot(), prefix
        ):
            key = functions.get(function_path)
            if key is None:
                continue
            source = name_of(seed_build.source_of(key))
            named[source] = key
            for code in function:
                # The bytecode of a function is the program of its graph: the image
                # builds it, not the file of native/ (its machine codes carry the
                # source).
                if code.tag == "Code" and code.get("name") != graph_sync.BYTECODE:
                    code.set("source", f"@{ALIAS}.{source}")
                    changed = True
        if changed:
            add_alias(document, ALIAS, f"pymergetic.rxf.{ALIAS}")
            document.write(path, encoding="UTF-8", xml_declaration=True)
    return named


def write_module(starter: Path, named: Mapping[str, str]) -> int:
    """``sources.rxfd``: one Source per file the image is built from -- every file
    of ``native/`` the Seed builds code from, and every file of ``boot/`` (the
    glue's build history, docs/31 §4)."""
    rows = [
        (name, ROOT / seed_build.source_of(key), "Rxfs", "Any")
        for name, key in named.items()
    ]
    rows += boot_rows()
    root = ET.Element("delta")
    module = ET.SubElement(
        root,
        "Module",
        {"name": ALIAS, "category": "ModuleCategory.Library"},
    )
    ET.SubElement(module, "Alias", {"name": "kinds", "target": "pymergetic.rxf.kinds"})
    for name, file, lang, arch in sorted(rows):
        text = file.read_bytes()
        ET.SubElement(
            module,
            "Source",
            {
                "name": name,
                "arch": f"@kinds.SourceArch.{arch}",
                "byte_count": str(len(text)),
                "lang": f"@kinds.SourceLang.{lang}",
                "semantic_digest": hashlib.sha256(text).hexdigest(),
                DATA: text.hex(),
            },
        )
    ET.ElementTree(root).write(
        starter / SOURCES, encoding="UTF-8", xml_declaration=True
    )
    return len(rows)


def write_sources(starter: Path, built: Mapping[str, str]) -> int:
    """The sources of the native code into the image; returns the Source count."""
    ensure_schema(starter)
    named = set_sources(starter, built)
    missing = [key for key in built if name_of(seed_build.source_of(key)) not in named]
    if missing:
        raise ValueError(f"no code named a source for {sorted(missing)}")
    return write_module(starter, named)
