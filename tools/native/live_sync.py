"""The pumps of the providers as Functions of the starter.

``live.pump`` is the native leaf of ``native/live.cpp``: it takes the area of a channel and
serves the frames waiting in its receive ring (``core/live/live.h``). Like ``graph.run`` it is
a copy of a runtime function with the same shape, here ``runtime.cleanup`` (the context and one
pointer, a status back). ``sync.py`` writes the bytes of every machine into it; this module
makes the Function and its record, and ``leaf id 29`` of ``native/graph.h`` names it. The
UART provider (``native/uart.c``, leaf 30) is the second one, in the module ``uart``; the
compiler of programs (``native/compile.c``, leaf 31) is ``compile.program``; the safe point
of compiled code (``native/safe.c``, leaf 38) is ``safe.point``.

``tiny.compile`` is not native: it is the compiler of ``native/tiny/*.rxfs`` as a program (a
Function of the Bytecode format that ``graph.run`` interprets), the one that, run by the
interpreter, writes the code of any program for x86_64, aarch64 and wasm32, itself included.
"""

from __future__ import annotations

import copy
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import graph_sync
import rxfc

RXF = "urn:pymergetic:rxf"
ET.register_namespace("rxf", RXF)
FOUNDATION = Path("pymergetic/rxf/foundation")
SOURCE = "cleanup"
ROOT = Path(__file__).resolve().parents[2]
# Each module has one Function (a `pump`, the `program` compiler); its meaning is the files it is
# defined by.
MODULES = {
    "live": (
        "pump",
        (ROOT / "src/pymergetic/rxf/core/live/live.h",),
    ),  # the layout of the area and the frames
    "uart": ("pump", (ROOT / "native/uart.c",)),  # the registers it moves bytes through
    "safe": (
        "point",
        (ROOT / "native/safe.c", ROOT / "src/pymergetic/rxf/core/live/live.h"),
    ),  # the words of the area it looks at
    "compile": (
        "program",
        (ROOT / "native/compile.c", ROOT / "native/graph.h"),
    ),  # the code it writes
}


TINY = ROOT / "native" / "tiny"
TINY_PATH = "pymergetic.rxf.foundation.tiny.compile"


def tiny_files() -> list[Path]:
    return sorted(TINY.glob("*.rxfs")) + [ROOT / "native" / "graph.h"]


def tiny_digest() -> str:
    """What the compiler that is a program means: its name and its sources."""
    text = "\n".join(file.read_text() for file in tiny_files())
    return hashlib.sha256(f"{TINY_PATH}\n{text}".encode()).hexdigest()


def semantic_digest(module: str) -> str:
    """What the pump of a module means: its name and the files that define it."""
    function, files = MODULES[module]
    path = f"pymergetic.rxf.foundation.{module}.{function}"
    text = "\n".join(file.read_text() for file in files)
    return hashlib.sha256(f"{path}\n{text}".encode()).hexdigest()


def replace_text(root: ET.Element, old: str, new: str) -> None:
    for element in root.iter():
        for key, value in list(element.attrib.items()):
            if old in value:
                element.set(key, value.replace(old, new))


def write_live(starter: Path) -> None:
    """The modules of the providers (``foundation/live.rxfd``, ``foundation/uart.rxfd``)."""
    for module in MODULES:
        write_module(starter, module)
    write_tiny(starter)


def write_module(starter: Path, name: str) -> None:
    """``foundation/<module>.rxfd``: the module with its Function and the record of it."""
    name_of = MODULES[name][0]
    source = ET.parse(starter / FOUNDATION / "runtime.rxfd").getroot()
    runtime = next(source.iter("Module"))
    function = copy.deepcopy(
        next(f for f in runtime.iter("Function") if f.get("name") == SOURCE)
    )
    record = copy.deepcopy(
        next(
            r
            for r in runtime.iter("StdlibRecord")
            if r.get("name") == f"{SOURCE}_contract"
        )
    )
    digest = semantic_digest(name)
    for element in (function, record):
        replace_text(element, f"@runtime.{SOURCE}", f"@{name}.{name_of}")
    function.set("name", name_of)
    function.set("semantic_digest", digest)
    record.set("name", f"{name_of}_contract")
    for child in function:
        if child.tag == "Code":
            child.set("semantic_digest", digest)
    root = ET.Element("delta")
    module = ET.SubElement(root, "Module", dict(runtime.attrib))
    module.set("name", name)
    for alias in runtime:
        if alias.tag == "Alias":
            copied = copy.deepcopy(alias)
            if copied.get("name") == "runtime":
                copied.set("name", name)
                copied.set("target", f"pymergetic.rxf.foundation.{name}")
            module.append(copied)
    module.extend((function, record))
    ET.ElementTree(root).write(
        starter / FOUNDATION / f"{name}.rxfd", encoding="UTF-8", xml_declaration=True
    )


def write_tiny(starter: Path) -> None:
    """``foundation/tiny.rxfd``: ``tiny.compile``, the compiler in ``native/tiny``, as Bytecode."""
    source = ET.parse(starter / FOUNDATION / "compile.rxfd").getroot()
    compile_module = next(source.iter("Module"))
    function = ET.Element(
        "Function",
        {
            "name": "compile",
            "implementation": "@kinds.FunctionImplementation.Semantic",
            "layer": "@kinds.FunctionLayer.Platform",
            "semantic_version": "1",
            "signature": "@tiny.compile.signature",
            "semantic_digest": tiny_digest(),
        },
    )
    signature = copy.deepcopy(
        next(e for e in compile_module.iter("Signature"))
    )  # a context and the request, a status back
    function.append(signature)
    program = rxfc.compile_file(TINY / "tiny.rxfs")
    function.append(graph_sync.bytecode(function, "@tiny.compile", program))
    root = ET.Element("delta")
    module = ET.SubElement(root, "Module", dict(compile_module.attrib))
    module.set("name", "tiny")
    for alias in compile_module:
        if alias.tag == "Alias" and alias.get("name") in {
            "kinds",
            "foundation",
            "model",
            "tiny",
            "compile",
        }:
            copied = copy.deepcopy(alias)
            if copied.get("name") == "compile":
                copied.set("name", "tiny")
                copied.set("target", "pymergetic.rxf.foundation.tiny")
            module.append(copied)
    module.append(function)
    ET.ElementTree(root).write(
        starter / FOUNDATION / "tiny.rxfd", encoding="UTF-8", xml_declaration=True
    )
