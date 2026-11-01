"""The interpreter and the programs of semantic functions, written into the starter files.

* ``graph.run`` is a native Function like the leaves: its Code is made from a
  copy of ``runtime.append`` (one handle, one pointer, two words in the same
  registers), with the bytes of ``native/graph.rxfs`` (the seed build,
  ``tools/native/native_build.py``). It names the leaves it
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
from collections.abc import Container, Mapping
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
CODES = {
    "x86_64_linux": "x86_64",
    "x86_64_uefi": "x86_64",
    "aarch64_linux": "aarch64",
    "aarch64_uefi": "aarch64",
}
# run(context, program: handle, args: address, argc: words, out: pointer)
# takes append(context, handle, transaction: pointer, source: address, count: word)
# with the pointer last: its Signature says so, and no register map is stored.
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
            function.remove(child)  # a derived register map: never in a delta
        elif child.tag == "Code" and child.get("name") in CODES:
            data = bytes_of[CODES[str(child.get("name"))]]["graph_run"]
            child.set("semantic_digest", digest)
            child.set("byte_count", str(len(data) // 2))
            child.set(DATA, data)
        elif child.tag == "Code":
            child.set("semantic_digest", digest)
    leaves = graph_header.leaves()
    for name, number in sorted(leaves.items(), key=lambda item: item[1]):
        if name not in leaf_paths:
            # the header may name a leaf the starter has no Function for yet (docs/23 4.9: an
            # image wires the leaves it has; step 9 adds the Function and this line wires it)
            continue
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


def snippet_key(parts: tuple[str, ...], name: str, known: Container[str]) -> str:
    """The snippet key a Function of a file names: ``numeric/bitwise.rxfd``'s
    ``bit_and_int8_t`` is ``bitwise_bit_and_int8_t``, ``memory/__init__.rxfd``'s
    ``copy`` is ``memory_copy``. One rule for the writer of the starter and the
    builder that fills a body from its recipe."""
    module = parts[-1]
    if module == "__init__" and len(parts) > 1:
        module = parts[-2]
    options = [f"{module}_{name}", name]
    if "convert" in parts:
        options.insert(0, f"convert_{module}_{name}")
    for option in options:
        if option in known:
            return option
    raise GraphSyncError(f"{'/'.join(parts)}: {name} has no source in native/")


def key_of_path(path: str, known: Container[str]) -> str:
    """``snippet_key`` over a dotted function path (``pymergetic.rxf.x.y`` is the
    file of module ``pymergetic.rxf.x``, function ``y``)."""
    parts = tuple(path.split("."))
    return snippet_key(parts[:-1], parts[-1], known)


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
