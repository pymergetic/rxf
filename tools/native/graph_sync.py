"""The interpreter and the programs of semantic functions, written into the starter files.

* ``graph.run`` is a native Function like the leaves: its Code and ABI are made
  from a copy of ``runtime.append`` (one handle, one pointer, two words in the
  same registers), with the bytes of ``native/graph.c``. It names the leaves it
  calls, by their id in ``native/graph.h``, with one ``Leaf`` reference each.
* Every semantic Function with a ``SemanticGraph`` gets one ``Code`` of format
  ``Bytecode``: the program that ``compose.py`` makes of the graph. It is the
  same for every target, so it names no target; it names the interpreter.
  The ``*_composed_entry*`` ABIs that described a native entry nobody built are
  gone.
"""

from __future__ import annotations

import copy
import hashlib
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

import graph_header

RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
INDEX = f"{{{RXF}}}index"
REF = f"{{{RXF}}}ref"
FOUNDATION = Path("pymergetic/rxf/foundation")
INTERPRETER = "pymergetic.rxf.foundation.graph.run"
BYTECODE = "bytecode"
COMPOSED_ENTRY = "composed_entry"
# What a location keeps when it takes the role of another: where it is.
POSITION = {"name", INDEX, "association", "semantic_index", DATA}
ABIS = ("sysv_status_out", "sysv_uefi_status_out")
ABIS_AARCH64 = ("aapcs64_linux_status_out", "aapcs64_status_out")
CODES = {
    "x86_64_linux": "x86_64",
    "x86_64_uefi": "x86_64",
    "aarch64_linux": "aarch64",
    "aarch64_uefi": "aarch64",
}
# run(context, program: handle, args: address, argc: words, out: pointer)
# takes append(context, handle, transaction: pointer, source: address, count: word)
# with the pointer last: the roles move by one, the registers stay where they are.
CLASSES = "0303030103"
SIGNATURE = ("@model.Ref", "@model.Ref", "@model.Ref", "uint64_t", "@model.Ref")


class GraphSyncError(Exception):
    pass


def word(value: str | None) -> str:
    return (value or "").rsplit(".", 1)[-1]


def replace_text(root: ET.Element, old: str, new: str) -> None:
    for element in root.iter():
        for key, value in list(element.attrib.items()):
            if old in value:
                element.set(key, value.replace(old, new))


def semantic_digest() -> str:
    """The identity of the interpreter's meaning: its name and the program format."""
    header = graph_header.HEADER.read_text()
    program_format = header[
        header.index("/*") : header.index("#define RXF_GRAPH_MAGIC")
    ]
    return hashlib.sha256(f"{INTERPRETER}\n{program_format}".encode()).hexdigest()


def retake_roles(abi: ET.Element) -> None:
    """Pass the roles of append's three middle arguments on: 2 to 4, 3 to 2, 4 to 3."""
    locations = {c.get("name"): c for c in abi if c.tag == "ABIValueLocation"}
    first, second, third = (locations[f"argument[{n}]"] for n in (2, 3, 4))
    donors = {
        "argument[2]": second,
        "argument[3]": third,
        "argument[4]": first,
    }
    saved = {
        name: {k: v for k, v in donor.attrib.items() if k not in POSITION}
        for name, donor in donors.items()
    }
    for name, kept in saved.items():
        location = locations[name]
        for key in [k for k in location.attrib if k not in POSITION]:
            del location.attrib[key]
        location.attrib.update(kept)
    abi.set(DATA, bytes.fromhex(CLASSES).hex())


def interpreter(
    runtime: ET.Element,
    bytes_of: Mapping[str, Mapping[str, str]],
    leaf_paths: Mapping[str, str],
) -> tuple[ET.Element, ET.Element]:
    """The Function ``run`` and its record, from ``append`` of the runtime module."""
    function = next(f for f in runtime.iter("Function") if f.get("name") == "append")
    record = next(
        r for r in runtime.iter("StdlibRecord") if r.get("name") == "append_contract"
    )
    function, record = copy.deepcopy(function), copy.deepcopy(record)
    digest = semantic_digest()
    for element in (function, record):
        replace_text(element, "@runtime.append", "@graph.run")
    function.set("name", "run")
    function.set("semantic_digest", digest)
    record.set("name", "run_contract")
    for child in list(function):
        if child.tag == REF:
            function.remove(child)
        elif child.tag == "Signature":
            parameters = [p for p in child if p.tag == "Parameter"]
            for parameter, kind in zip(parameters, SIGNATURE, strict=True):
                parameter.set("value_type", kind)
        elif child.tag == "ABISignature":
            if child.get("name") in ABIS + ABIS_AARCH64:
                retake_roles(child)
        elif child.tag == "Code" and child.get("name") in CODES:
            data = bytes_of[CODES[str(child.get("name"))]]["graph_run"]
            child.set("semantic_digest", digest)
            child.set("byte_count", str(len(data) // 2))
            child.set(DATA, data)
        elif child.tag == "Code":
            child.set("semantic_digest", digest)
    leaves = graph_header.leaves()
    for name, number in sorted(leaves.items(), key=lambda item: item[1]):
        ET.SubElement(
            function,
            REF,
            {
                "role": "@call.Leaf",
                "index": str(number),
                "kind": "RefKind.Data",
                "target": leaf_paths[name],
            },
        )
    return function, record


def write_interpreter(
    starter: Path,
    bytes_of: Mapping[str, Mapping[str, str]],
    leaf_paths: Mapping[str, str],
) -> None:
    source = ET.parse(starter / FOUNDATION / "runtime.rxfd").getroot()
    runtime = next(source.iter("Module"))
    function, record = interpreter(runtime, bytes_of, leaf_paths)
    root = ET.Element("delta")
    module = ET.SubElement(root, "Module", dict(runtime.attrib))
    module.set("name", "graph")
    for alias in runtime:
        if alias.tag == "Alias":
            copied = copy.deepcopy(alias)
            if copied.get("name") == "runtime":
                copied.set("name", "graph")
                copied.set("target", "pymergetic.rxf.foundation.graph")
            module.append(copied)
    module.extend((function, record))
    ET.ElementTree(root).write(
        starter / FOUNDATION / "graph.rxfd", encoding="UTF-8", xml_declaration=True
    )


def module_prefix(parts: tuple[str, ...]) -> str:
    """The path above the module a file defines (``__init__.rxfd`` defines its directory)."""
    return ".".join(parts[:-2] if parts[-1] == "__init__" else parts[:-1])


def functions_in(parent: ET.Element, prefix: str) -> list[tuple[str, ET.Element]]:
    found: list[tuple[str, ET.Element]] = []
    for module in parent:
        if module.tag != "Module":
            continue
        path = f"{prefix}.{module.get('name')}" if prefix else str(module.get("name"))
        for child in module:
            if child.tag == "Function":
                found.append((f"{path}.{child.get('name')}", child))
        found += functions_in(module, path)
    return found


def bytecode(function: ET.Element, path: str, program: bytes) -> ET.Element:
    code = ET.Element(
        "Code",
        {
            "name": BYTECODE,
            "owner_function": path,
            "signature": str(function.get("signature")),
            "format": "pymergetic.rxf.kinds.CodeFormat.Bytecode",
            "semantic_version": str(function.get("semantic_version")),
            "byte_count": str(len(program)),
            "semantic_digest": str(function.get("semantic_digest")),
            DATA: program.hex(),
        },
    )
    ET.SubElement(
        code,
        REF,
        {
            "role": "pymergetic.rxf.roles.call.Interpreter",
            "kind": "RefKind.Data",
            "target": INTERPRETER,
        },
    )
    return code


def write_bytecode(
    document: ET.ElementTree[ET.Element],
    parts: tuple[str, ...],
    programs: Mapping[str, bytes],
) -> list[str]:
    """Give the semantic functions of a file their program; the paths that got one."""
    done: list[str] = []
    for path, function in functions_in(document.getroot(), module_prefix(parts)):
        if path not in programs:
            continue
        for child in list(function):
            stale = child.tag == "ABISignature" and COMPOSED_ENTRY in str(
                child.get("name")
            )
            if stale or (child.tag == "Code" and child.get("name") == BYTECODE):
                function.remove(child)
        function.append(bytecode(function, path, programs[path]))
        done.append(path)
    return done


def drop_unused_aliases(document: ET.ElementTree[ET.Element]) -> None:
    """An alias that nothing written in its module goes through is an error of the image."""
    for module in document.getroot().iter("Module"):
        for alias in [a for a in module if a.tag == "Alias"]:
            mark = f"@{alias.get('name')}."
            used = any(
                mark in value
                for element in module.iter()
                if element is not alias
                for value in element.attrib.values()
            )
            if not used and "Reexport" not in str(alias.get("flags")):
                module.remove(alias)
