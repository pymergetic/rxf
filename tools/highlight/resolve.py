"""Resolving the references of a ``.rxfd`` delta document against known declarations.

Pure Python (the standard library only), no UI: this is the shared brain the
stage-3 inspector webview and a future validator both use. Given a delta document
and the declarations the image already has, it says which ``signature=``,
``refusal_set=`` and ``rxf:parent=`` values resolve, to what, and why not when
they do not.

What a reference is (``src/pymergetic/rxf/core/xml/xml.hpp``): an attribute value
is either an absolute dotted path (``pymergetic.rxf.foundation.StdlibRefusals``)
or a path that starts with ``@``, which is an alias of the enclosing Module
(``@foundation.StdlibRefusals``). An alias is visible in its own module and the
modules below it, never outside it (``tests/test_kinds.py``). A top-level element
names its parent with ``rxf:parent``; without one the parent is the default
parent of the document (its place in a tree laid out like ``starter/``), and
without that the root.

Which kind each reference attribute must find comes from the meaning table
(``tools/highlight/meaning.json``): ``signature=`` names a ``Signature``,
``refusal_set=`` a ``RefusalSet``, ``rxf:parent=`` a ``Module``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from .meaning import ReferenceShape, Table
from .meaning import load as load_table

__all__ = [
    "Alias",
    "Declaration",
    "Reference",
    "World",
    "add_document",
    "check_document",
    "corpus_world",
    "layout_default_parent",
    "resolve_value",
]

_TABLE = load_table()
_URN = _TABLE.rxfd.namespace.urn
_QNAME_PARENT = "{" + _URN + "}parent"


def _local(tag: str) -> str:
    """The element name without the namespace of the format."""
    return tag.rsplit("}", 1)[-1]


@dataclass(frozen=True)
class Declaration:
    """One object a delta document declares (or the image already has)."""

    path: str  # the dotted path, e.g. `pymergetic.rxf.selfedit.triple.signature`
    kind: str  # the element that declared it: `Module`, `Signature`, `RefusalSet`, ...
    source: str = ""  # the file or document it comes from


@dataclass(frozen=True)
class Alias:
    """An `<Alias name="foundation" target="pymergetic.rxf.foundation"/>`."""

    name: str
    target: str
    module: str  # the module the alias is an object of: the enclosing Module
    source: str = ""


@dataclass(frozen=True)
class Reference:
    """One reference attribute of one element, and whether its value resolves."""

    attribute: str  # `signature`, `refusal_set`, `rxf:parent`
    value: str  # the value as written, `@alias.rest` and all
    site: str  # the declaration path of the element that carries the attribute
    expected_kind: str
    target: str | None  # the absolute path the value stands for, when it can be read
    resolved: bool
    found_kind: str | None  # the kind of the declaration the target finds
    problem: str | None  # None when resolved; otherwise why not


@dataclass
class World:
    """Everything a document may name: its own declarations plus known ones."""

    declarations: dict[str, Declaration] = field(default_factory=dict)
    aliases: list[Alias] = field(default_factory=list)

    def add(self, declaration: Declaration) -> None:
        self.declarations[declaration.path] = declaration

    def add_alias(self, alias: Alias) -> None:
        self.aliases.append(alias)

    def lookup(self, path: str) -> Declaration | None:
        return self.declarations.get(path)

    def find_alias(self, name: str, module: str) -> Alias | None:
        """The alias `name` as it is visible in `module`: of the module itself or
        of a module above it. An alias of another branch is not found (an alias
        is not found from outside its module)."""
        found = [
            alias
            for alias in self.aliases
            if alias.name == name
            and (module == alias.module or module.startswith(alias.module + "."))
        ]
        return max(found, key=lambda alias: len(alias.module), default=None)


def attribute_keys(table: Table | None = None) -> dict[str, str]:
    """The keys of ``xml.etree.ElementTree`` that carry the references of the table,
    by the name the table (and the report) spells them with."""
    table = table or load_table()
    prefix = f"{{{table.rxfd.namespace.urn}}}"
    keys: dict[str, str] = {}
    for shape in table.rxfd.references:
        name = shape.attribute
        key = prefix + name.removeprefix("rxf:") if name.startswith("rxf:") else name
        keys[key] = name
    return keys


def reference_shapes(table: Table | None = None) -> dict[str, ReferenceShape]:
    """The reference shapes of the table, by attribute name."""
    table = table or load_table()
    return {shape.attribute: shape for shape in table.rxfd.references}


def resolve_value(value: str, world: World, module: str) -> tuple[str | None, str | None]:
    """The absolute path a reference value stands for: ``(target, problem)``.

    The target is read even when nothing declares it; the problem is set when the
    value cannot be read at all (an alias that is not visible here)."""
    if value.startswith("@"):
        head, dot, tail = value[1:].partition(".")
        alias = world.find_alias(head, module)
        if alias is None:
            where = module or "the root"
            return None, f"no alias {head!r} is visible in {where}"
        return alias.target + (dot + tail if dot else ""), None
    return value, None


@dataclass
class _Raw:
    """One reference attribute as it is written in the document."""

    attribute: str
    value: str
    site: str
    module: str
    top_level: bool


def _walk(
    root: ET.Element,
    default_parent: str,
    source: str,
    keys: dict[str, str],
) -> tuple[list[Declaration], list[Alias], list[_Raw]]:
    declarations: list[Declaration] = []
    aliases: list[Alias] = []
    raws: list[_Raw] = []
    stack: list[tuple[ET.Element, str, str, str, bool]] = [
        # element, declaration path, site path, module path, is top level
        (root, "", "", "", False)
    ]
    while stack:
        element, path, site, module, top_level = stack.pop()
        name = element.get("name")
        kind = _local(element.tag)
        if element is not root and name is not None:
            parent = path
            if top_level:
                named = element.get(_QNAME_PARENT)
                parent = named if named is not None else default_parent
            path = f"{parent}.{name}" if parent else name
            declarations.append(Declaration(path, kind, source))
            site = path
            if kind == "Module":
                module = path
            elif top_level:
                # the parent a top-level element names is the Module around it
                module = parent
            if kind == "Alias":
                target = element.get("target")
                if target is not None:
                    aliases.append(Alias(name, target, module, source))
        for key, attribute in keys.items():
            value = element.get(key)
            if value is not None:
                raws.append(_Raw(attribute, value, site, module, top_level))
        for child in element:
            stack.append((child, path, site, module, element is root))
    return declarations, aliases, raws


def add_document(
    world: World,
    text: str,
    source: str = "",
    default_parent: str = "",
    table: Table | None = None,
) -> None:
    """Declare everything a delta document declares. Its references are not checked."""
    table = table or load_table()
    declarations, aliases, _ = _walk(
        ET.fromstring(text), default_parent, source, attribute_keys(table)
    )
    for declaration in declarations:
        world.add(declaration)
    for alias in aliases:
        world.add_alias(alias)


def check_document(
    text: str,
    known: World | None = None,
    default_parent: str = "",
    source: str = "",
    table: Table | None = None,
) -> list[Reference]:
    """Every ``signature=``, ``refusal_set=`` and ``rxf:parent=`` of the document,
    and whether it resolves against the document itself and `known`.

    The document is a delta that is about to be applied: its own declarations are
    part of the world, `known` is what the image already has."""
    table = table or load_table()
    shapes = reference_shapes(table)
    keys = attribute_keys(table)
    declarations, aliases, raws = _walk(ET.fromstring(text), default_parent, source, keys)
    world = World(dict(known.declarations) if known else {}, list(known.aliases) if known else [])
    for declaration in declarations:
        world.add(declaration)
    for alias in aliases:
        world.add_alias(alias)
    references = []
    for raw in raws:
        shape = shapes[raw.attribute]
        target, problem = resolve_value(raw.value, world, raw.module)
        found: Declaration | None = None
        if problem is None and target is not None:
            found = world.lookup(target)
            if found is None:
                problem = f"{target} is not declared"
            elif found.kind != shape.expected_kind:
                problem = f"{target} is a {found.kind}, not a {shape.expected_kind}"
        if problem is None and raw.attribute == "rxf:parent" and not raw.top_level:
            problem = "only a top-level element names rxf:parent"
        references.append(
            Reference(
                attribute=raw.attribute,
                value=raw.value,
                site=raw.site,
                expected_kind=shape.expected_kind,
                target=target,
                resolved=problem is None,
                found_kind=found.kind if found else None,
                problem=problem,
            )
        )
    return references


def layout_default_parent(root: Path, path: Path) -> str:
    """The default parent of a document of a tree laid out like ``starter/``.

    ``pymergetic/rxf/foundation/__init__.xml`` declares the module ``foundation``
    under ``pymergetic.rxf`` (a package file names the directory it is in);
    ``pymergetic/rxf/library.xml`` declares ``library`` under the same; the
    ``__init__.xml`` of the root declares at the root. That is why the starter
    never writes ``rxf:parent`` (``tests/test_starter_delta.py``)."""
    parts = list(path.relative_to(root).parts)
    if not parts:
        return ""
    if parts[-1] == "__init__.xml":
        parts = parts[:-2]  # the file, and the directory it names
    else:
        parts = parts[:-1]  # the file
    return ".".join(parts)


def corpus_world(root: Path) -> World:
    """Every declaration of the tree under `root`, laid out like ``starter/``."""
    world = World()
    for path in sorted(root.rglob("*.xml")):
        add_document(
            world,
            path.read_text(),
            source=str(path),
            default_parent=layout_default_parent(root, path),
        )
    return world
