"""The bytes of the native code as CodeBlob objects (Wave 4b step 1: despec).

The seats of a machine run identical bytes -- x86_64 Linux, BIOS and UEFI are
one body, aarch64 Linux and UEFI are one -- so six Code objects per function
carry only three bodies, and the copies were a spec of nothing. The bytes live
in one object per function and machine (``body_x86_64``, ``body_aarch64``,
``body_wasm32``: a ``CodeBlob`` child of the function, its digest the sha256 of
what it is), and every Code names its blob. The Code keeps what only it says:
the code of one function for one target, with the ABI that calls it and the
recipe that built it; the blob is what that code is made of.

``sync.py`` calls ``write_blobs`` on its working copy after the Code objects are
refreshed and before the tree is canonicalized. Running it twice writes the
same tree: a blob of the previous run is replaced by one of the same name and
content, never duplicated.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
ET.register_namespace("rxf", RXF)

EXECUTION = Path("pymergetic/rxf/execution/__init__.rxfd")

BLOB_TYPE = {
    "name": "CodeBlob",
    "form": "Form.Opaque",
    "size": "40",
    "align": "1",
    "flags": "TypeFlags.Variable",
}
BLOB_FIELDS = (
    {"name": "byte_count", "value_type": "uint64_t", "offset": "0"},
    {
        "name": "content_digest",
        "value_type": "uint8_t",
        "offset": "8",
        "count": "32",
    },
)
# On the Code type after the source handle (88 + 8): a reference is a handle in
# the fixed part, so the bytes move out without moving the layout below.
CODE_BODY_FIELD = {
    "name": "body",
    "flags": "FieldFlags.Optional|Ref",
    "value_type": "uint64_t",
    "offset": "96",
}

# The Code name of a seat to the machine its body belongs to; the six names are
# what sync.py writes (``x86_64_linux`` and friends) and the one machine name.
MACHINE_OF = {
    "x86_64_linux": "x86_64",
    "x86_64_bios": "x86_64",
    "x86_64_uefi": "x86_64",
    "aarch64_linux": "aarch64",
    "aarch64_uefi": "aarch64",
    "wasm32": "wasm32",
}


def insert_sorted(module: ET.Element, element: ET.Element) -> None:
    """Keep the children of a module in the order the canonical writer keeps them."""
    name = element.get("name", "")
    for position, sibling in enumerate(module):
        if sibling.tag == element.tag and (sibling.get("name") or "") > name:
            module.insert(position, element)
            return
    module.append(element)


def ensure_type(module: ET.Element) -> None:
    """The ``CodeBlob`` type of the execution module, once."""
    if any(e.tag == "Type" and e.get("name") == "CodeBlob" for e in module):
        return
    element = ET.Element("Type", BLOB_TYPE)
    for field in BLOB_FIELDS:
        ET.SubElement(element, "Field", field)
    insert_sorted(module, element)


def ensure_field(element: ET.Element, field: dict) -> None:
    if not any(f.get("name") == field["name"] for f in element if f.tag == "Field"):
        insert_sorted(element, ET.Element("Field", field))


def ensure_schema(starter: Path) -> None:
    """The type and the field this module writes objects of."""
    execution = ET.parse(starter / EXECUTION)
    module = next(execution.getroot().iter("Module"))
    ensure_type(module)
    code = next(e for e in module if e.tag == "Type" and e.get("name") == "Code")
    ensure_field(code, CODE_BODY_FIELD)
    execution.write(starter / EXECUTION, encoding="UTF-8", xml_declaration=True)


def module_parts(path: Path, starter: Path) -> str:
    """The dotted module path a file defines (``__init__.rxfd`` defines its directory)."""
    parts = path.relative_to(starter).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def module_element(document: ET.ElementTree[ET.Element[str]]) -> ET.Element[str] | None:
    """The module a delta defines; None for the root image's type-only file."""
    root = document.getroot()
    return next(root.iter("Module"), None) if root is not None else None


def self_alias(module: ET.Element, path: str) -> str:
    """The alias a module writes its own references short with: ``checked``."""
    for alias in module:
        if alias.tag == "Alias" and alias.get("target") == path:
            return str(alias.get("name"))
    name = path.rsplit(".", 1)[-1]
    insert_sorted(module, ET.Element("Alias", {"name": name, "target": path}))
    return name


def write_blobs(starter: Path) -> int:
    """One CodeBlob per function and machine; returns the number of blobs."""
    ensure_schema(starter)
    made = 0
    for path in sorted(starter.rglob("*.rxfd")):
        module_path = module_parts(path, starter)
        document = ET.parse(path)
        module = module_element(document)
        if module is None:
            continue  # the root image module: types only, no functions
        changed = False
        for function in module.iter("Function"):
            bodies: dict[str, list[ET.Element]] = {}
            for code in function:
                machine = MACHINE_OF.get(str(code.get("name", "")))
                if code.tag == "Code" and machine and code.get(DATA):
                    bodies.setdefault(machine, []).append(code)
            for machine, codes in sorted(bodies.items()):
                texts = {str(c.get(DATA)) for c in codes}
                if len(texts) != 1:
                    raise ValueError(
                        f"{module_path}:{function.get('name')}: the "
                        f"{machine} seats do not agree on one body"
                    )
                text = texts.pop()
                name = f"body_{machine}"
                for old in function.findall("CodeBlob"):
                    if old.get("name") == name:
                        function.remove(old)  # the blob of the previous run
                body = ET.Element(
                    "CodeBlob",
                    {
                        "name": name,
                        "byte_count": str(len(text) // 2),
                        "content_digest": hashlib.sha256(
                            bytes.fromhex(text)
                        ).hexdigest(),
                        DATA: text,
                    },
                )
                insert_sorted(function, body)
                made += 1
                alias = self_alias(module, module_path)
                for code in codes:
                    code.set("body", f"@{alias}.{function.get('name')}.body_{machine}")
                    del code.attrib[DATA]
                    changed = True
        if changed:
            document.write(path, encoding="UTF-8", xml_declaration=True)
    return made
