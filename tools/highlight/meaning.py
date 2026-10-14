"""The meaning table of the RXF languages, loaded from ``meaning.json``.

The table is the one source of the highlighting: what every leaf of the function
table is (``native/graph.h`` transcribed, never invented), what every operator of
``.rxfs`` lowers to (``tools/native/rxfc.py`` transcribed), the enum vocabulary of
``.rxfd`` (``starter/pymergetic/rxf/kinds.xml`` transcribed) and the reference
shapes of the delta documents. ``tests/test_highlight.py`` pins every transcribed
part back to where it comes from, so the table may not drift.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TABLE_PATH = HERE / "meaning.json"


@dataclass(frozen=True)
class Leaf:
    """One id of the function table of the runtime context."""

    id: int
    name: str  # the key tools use: `RXF_LEAF_*` lowered (`wrapping_add_uint64_t`)
    c_symbol: str
    klass: str  # the class of the leaf (`arith`, `compare`, ...): JSON key `class`
    shape: str  # the rxf_shape of the call (`qqo`, `p`, ...)


@dataclass(frozen=True)
class Operator:
    """What one operator form of ``.rxfs`` lowers to."""

    token: str
    form: str  # binary | prefix | statement
    klass: str  # JSON key `class`: the leaf class it colors as
    scope: str  # the TextMate scope the grammar gives the token
    mapping: str  # one-to-one | desugared | none
    leaves: tuple[str, ...]  # the leaves the operator reaches
    lowering: str  # the honest rewrite, where the mapping is not 1:1


@dataclass(frozen=True)
class Builtin:
    """What one builtin call of ``.rxfs`` lowers to."""

    name: str
    form: str
    klass: str  # JSON key `class`
    mapping: str
    leaves: tuple[str, ...]
    lowering: str


@dataclass(frozen=True)
class KeywordGroup:
    """A group of keywords of ``.rxfs`` and the scope it gets."""

    group: str
    scope: str
    words: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class EnumType:
    """One enumeration of the delta vocabulary and its enumerators."""

    type: str
    source: str  # the file the values are transcribed from
    flags: bool  # a bitset: the values combine with `|`
    enumerators: dict[str, int]


@dataclass(frozen=True)
class RefusalVariant:
    name: str
    target: str  # the path the rxf:ref names
    code: str  # the RefusalCode of the variant, e.g. `RefusalCode.Overflow`


@dataclass(frozen=True)
class RefusalSet:
    path: str
    name: str
    module: str
    variant_count: int
    variants: tuple[RefusalVariant, ...]


@dataclass(frozen=True)
class ReferenceShape:
    """One reference attribute of ``.rxfd`` and what its value resolves to."""

    attribute: str  # `signature`, `refusal_set`, `rxf:parent`
    applies_to: tuple[str, ...]  # the elements seen carrying it (`*` for any)
    expected_kind: str  # the kind the target must be (`Signature`, ...)
    shape: str  # how the value is written (`path`, absolute or `@alias`)
    resolves_to: str  # what the value stands for


@dataclass(frozen=True)
class Namespace:
    """The `rxf` namespace of the delta documents."""

    prefix: str
    urn: str
    elements: tuple[str, ...]
    attributes: tuple[str, ...]


@dataclass(frozen=True)
class Rxfs:
    extension: str
    scope_name: str
    note: str
    keywords: tuple[KeywordGroup, ...]
    precedence: tuple[tuple[str, ...], ...]
    operators: tuple[Operator, ...]
    builtins: tuple[Builtin, ...]


@dataclass(frozen=True)
class Rxfd:
    extension: str
    scope_name: str
    note: str
    namespace: Namespace
    elements: tuple[str, ...]
    attributes: tuple[str, ...]
    enums: tuple[EnumType, ...]
    refusal_sets: tuple[RefusalSet, ...]
    references: tuple[ReferenceShape, ...]


@dataclass(frozen=True)
class Leaves:
    note: str
    classes: dict[str, str]
    entries: tuple[Leaf, ...]


@dataclass(frozen=True)
class Table:
    """The whole meaning table."""

    about: str
    sources: dict[str, str]
    leaves: Leaves
    rxfs: Rxfs
    rxfd: Rxfd

    def leaf(self, name: str) -> Leaf:
        for entry in self.leaves.entries:
            if entry.name == name:
                return entry
        raise KeyError(name)

    def enum(self, type_name: str) -> EnumType:
        for entry in self.rxfd.enums:
            if entry.type == type_name:
                return entry
        raise KeyError(type_name)


def load(path: Path | None = None) -> Table:
    """The table as it is checked in."""
    raw = json.loads((path or TABLE_PATH).read_text())
    leaves = Leaves(
        note=raw["leaves"]["note"],
        classes=dict(raw["leaves"]["classes"]),
        entries=tuple(
            Leaf(
                id=entry["id"],
                name=entry["name"],
                c_symbol=entry["c_symbol"],
                klass=entry["class"],
                shape=entry["shape"],
            )
            for entry in raw["leaves"]["entries"]
        ),
    )
    rxfs_raw = raw["rxfs"]
    rxfs = Rxfs(
        extension=rxfs_raw["extension"],
        scope_name=rxfs_raw["scope_name"],
        note=rxfs_raw["note"],
        keywords=tuple(
            KeywordGroup(
                group=entry["group"],
                scope=entry["scope"],
                words=tuple(entry["words"]),
                note=str(entry.get("note", "")),
            )
            for entry in rxfs_raw["keywords"]
        ),
        precedence=tuple(tuple(level) for level in rxfs_raw["precedence"]),
        operators=tuple(_operator(entry) for entry in rxfs_raw["operators"]),
        builtins=tuple(_builtin(entry) for entry in rxfs_raw["builtins"]),
    )
    rxfd_raw = raw["rxfd"]
    rxfd = Rxfd(
        extension=rxfd_raw["extension"],
        scope_name=rxfd_raw["scope_name"],
        note=rxfd_raw["note"],
        namespace=Namespace(**rxfd_raw["namespace"]),
        elements=tuple(rxfd_raw["elements"]),
        attributes=tuple(rxfd_raw["attributes"]),
        enums=tuple(
            EnumType(
                type=entry["type"],
                source=entry["source"],
                flags=entry["flags"],
                enumerators=dict(entry["enumerators"]),
            )
            for entry in rxfd_raw["enums"]
        ),
        refusal_sets=tuple(
            RefusalSet(
                path=entry["path"],
                name=entry["name"],
                module=entry["module"],
                variant_count=entry["variant_count"],
                variants=tuple(RefusalVariant(**variant) for variant in entry["variants"]),
            )
            for entry in rxfd_raw["refusal_sets"]
        ),
        references=tuple(ReferenceShape(**entry) for entry in rxfd_raw["references"]),
    )
    return Table(
        about=raw["about"],
        sources=dict(raw["sources"]),
        leaves=leaves,
        rxfs=rxfs,
        rxfd=rxfd,
    )


def _operator(entry: dict[str, Any]) -> Operator:
    return Operator(
        token=str(entry["token"]),
        form=str(entry["form"]),
        klass=str(entry["class"]),
        scope=str(entry["scope"]),
        mapping=str(entry["mapping"]),
        leaves=tuple(entry["leaves"]),
        lowering=str(entry["lowering"]),
    )


def _builtin(entry: dict[str, Any]) -> Builtin:
    return Builtin(
        name=str(entry["name"]),
        form=str(entry["form"]),
        klass=str(entry["class"]),
        mapping=str(entry["mapping"]),
        leaves=tuple(entry["leaves"]),
        lowering=str(entry["lowering"]),
    )
