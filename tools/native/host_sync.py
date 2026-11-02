"""The host entries of the image (docs/31 §5): a distribution's entry per host.

A distribution is source + code + capabilities + entry, and a host is just the
outermost capability set it services. The same RXF world runs in three host
shapes -- ``unix-program`` (the world is a process on unix), ``bare-metal-os``
(the world is the operating system) and ``wasm`` (the world is a wasm module
in a JS host) -- so the image carries one ``HostEntry`` per shape: the entry
the host runs (``pymergetic.rxf.application.main``, the image's entry) and the
capability requirements that are its outermost boundary. Which fill services a
requirement is the host's business (docs/28, the fill rule); the set is the
same world asking on every host.

``sync.py`` calls ``write_hosts`` on its working copy. ``ensure_schema`` adds
the ``HostKind`` enum, the ``HostEntry`` type and the ``CapabilityRequirement``
role the entry's capability set references. Everything here is derived from the
image itself -- the entry and the requirements are looked up, never spelled.

``tests/test_host_entries.py`` pins the entries and proves the seat each host
shape runs on.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RXF = "urn:pymergetic:rxf"
ET.register_namespace("rxf", RXF)

ROOT = Path(__file__).resolve().parents[2]
KINDS = Path("pymergetic/rxf/kinds.rxfd")
EXECUTION = Path("pymergetic/rxf/execution/__init__.rxfd")
ROLES = Path("pymergetic/rxf/roles/call.rxfd")
CAPABILITIES = Path("pymergetic/rxf/capabilities.rxfd")
MODULE = Path("pymergetic/rxf/hosts.rxfd")

ENTRY = "pymergetic.rxf.application.main"

# The three host shapes of docs/31 §5 and the kind each is.
HOSTS = (
    ("bare_metal_os", "BareMetalOs"),
    ("unix_program", "UnixProgram"),
    ("wasm", "Wasm"),
)

ENUMS = {
    "HostKind": (
        ("BareMetalOs", "2"),
        ("UnixProgram", "1"),
        ("Wasm", "3"),
    ),
}
HOST_ENTRY_TYPE = {
    "name": "HostEntry",
    "form": "Form.Struct",
    "size": "16",
    "align": "8",
    "flags": "TypeFlags.Variable|SelfDescribing",
}
# entry (the handle of the Function) at 0, kind at 8, the capability tail after.
HOST_ENTRY_FIELDS = (
    {"name": "capability_count", "value_type": "uint32_t", "offset": "12"},
    {"name": "entry", "flags": "FieldFlags.Ref", "value_type": "uint64_t"},
    {"name": "kind", "value_type": "@kinds.HostKind", "offset": "8"},
)
ROLE = {"name": "CapabilityRequirement"}


def insert_sorted(module: ET.Element, element: ET.Element) -> None:
    """The order the canonical writer keeps: children sorted by name."""
    name = element.get("name", "")
    for position, sibling in enumerate(module):
        if (sibling.get("name") or "") > name:
            module.insert(position, element)
            return
    module.append(element)


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


def ensure_schema(starter: Path) -> None:
    """The types and the role this module writes objects of."""
    kinds = ET.parse(starter / KINDS)
    ensure_enums(next(kinds.getroot().iter("Module")))
    kinds.write(starter / KINDS, encoding="UTF-8", xml_declaration=True)

    execution = ET.parse(starter / EXECUTION)
    module = next(execution.getroot().iter("Module"))
    if not any(e.tag == "Type" and e.get("name") == "HostEntry" for e in module):
        element = ET.Element("Type", HOST_ENTRY_TYPE)
        for field in HOST_ENTRY_FIELDS:
            ET.SubElement(element, "Field", field)
        insert_sorted(module, element)
    execution.write(starter / EXECUTION, encoding="UTF-8", xml_declaration=True)

    roles = ET.parse(starter / ROLES)
    module = next(roles.getroot().iter("Module"))
    if not any(e.tag == "Role" and e.get("name") == ROLE["name"] for e in module):
        insert_sorted(module, ET.Element("Role", ROLE))
    roles.write(starter / ROLES, encoding="UTF-8", xml_declaration=True)


def requirements(starter: Path) -> list[str]:
    """Every ``CapabilityRequirement`` of the image, by name: the outermost set."""
    document = ET.parse(starter / CAPABILITIES)
    return sorted(
        str(e.get("name"))
        for e in document.getroot().iter("CapabilityRequirement")
    )


def write_hosts(starter: Path) -> None:
    """``pymergetic.rxf.hosts``: one ``HostEntry`` per host shape of docs/31 §5."""
    ensure_schema(starter)
    names = requirements(starter)
    root = ET.Element("delta")
    module = ET.SubElement(
        root,
        "Module",
        {"name": "hosts", "category": "ModuleCategory.Library"},
    )
    for alias, target in (
        ("application", "pymergetic.rxf.application"),
        ("call", "pymergetic.rxf.roles.call"),
        ("capabilities", "pymergetic.rxf.capabilities"),
        ("kinds", "pymergetic.rxf.kinds"),
    ):
        ET.SubElement(module, "Alias", {"name": alias, "target": target})
    for name, kind in HOSTS:
        made = ET.Element(
            "HostEntry",
            {
                "name": name,
                "kind": f"@kinds.HostKind.{kind}",
                "entry": f"@application.{ENTRY.rsplit('.', 1)[-1]}",
                "capability_count": str(len(names)),
            },
        )
        for requirement in names:
            ET.SubElement(
                made,
                f"{{{RXF}}}ref",
                {
                    "role": "@call.CapabilityRequirement",
                    "kind": "RefKind.Data",
                    "target": f"@capabilities.{requirement}",
                },
            )
        insert_sorted(module, made)
    ET.ElementTree(root).write(
        starter / MODULE, encoding="UTF-8", xml_declaration=True
    )


def main() -> None:
    arguments = sys.argv[1:]
    starter = Path(arguments[0]) if arguments else ROOT / "starter"
    write_hosts(starter)
    print(f"{len(HOSTS)} host entries; {len(requirements(starter))} capabilities each")


if __name__ == "__main__":
    main()
