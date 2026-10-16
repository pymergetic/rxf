"""The live symbol check of the editor: red/green as you type, one meaning truth.

This is the brain the inspector's editor calls to say, token by token, whether what is typed is
a token the language has and what it means. Nothing here is a second vocabulary: the tokens, the
operators, the keywords, the builtins and the reference shapes all come from the one meaning table
(``tools/highlight/meaning.json``) and the resolver (``tools/highlight/resolve.py``). A token is
**green** when it is a token of the language and carries its meaning (the leaf it lowers to, the
group of a keyword, the object a reference resolves to); it is **red** when the language refuses
it (a symbol that is not an operator of ``.rxfs``, a reference of ``.rxfd`` that does not resolve),
and the tooltip carries the refusal.

``.rxfs`` (the program language) is checked by tokenizing against the table's TOKEN regex and
classifying each token from the table. ``.rxfd`` (the delta documents) is checked by resolving
every reference attribute with the resolver and validating every enum value against the table.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

from . import resolve
from .grammar_gen import alternation
from .meaning import Table
from .meaning import load as load_table

__all__ = [
    "Mark",
    "check",
    "check_rxfd",
    "check_rxfs",
    "token_regex",
]

# The shapes of a token of ``.rxfs`` that carry no meaning of their own beyond their face. A
# ``word`` is looked up (keyword, builtin, else a plain name); an ``operator`` is looked up in the
# table; ``bad`` is whatever the language has no token for and is always a refusal.
_KIND_MEANING = {
    "comment": "a comment",
    "string": "a string",
    "number": "a number",
    "punct": "punctuation",
}


@dataclass(frozen=True)
class Mark:
    """One token (or one reference value) of the source, and whether it is green or red."""

    start: int
    end: int
    line: int  # 1-based
    column: int  # 1-based
    text: str
    ok: bool
    kind: str  # operator | keyword | builtin | identifier | number | string | comment | punct
    #          | reference | enum | bad
    meaning: str | None
    refusal: str | None


def _line_column(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    start_of_line = text.rfind("\n", 0, offset) + 1
    return line, offset - start_of_line + 1


def _mark(
    text: str, start: int, end: int, ok: bool, kind: str, meaning: str | None, refusal: str | None
) -> Mark:
    line, column = _line_column(text, start)
    return Mark(start, end, line, column, text[start:end], ok, kind, meaning, refusal)


# ---- the tokenizer of .rxfs -------------------------------------------------------------------


def token_regex(table: Table | None = None) -> re.Pattern[str]:
    """The TOKEN regex of ``.rxfs``, derived from the meaning table (never a second vocabulary).

    The operator alternation is exactly the table's operators, longest first, so a symbol the
    language has no operator for (``%``, ``^``) falls through to ``bad`` and is refused. The
    keyword, builtin and other shapes are generic; the classification of a ``word`` reads the
    table at check time.
    """
    table = table or load_table()
    operators = alternation([operator.token for operator in table.rxfs.operators])
    return re.compile(
        r"(?P<comment>#[^\n]*)"
        r'|(?P<string>"(?:\\.|[^"\\])*")'
        r"|(?P<number>0[xX][0-9a-fA-F_]+|[0-9][0-9_]*)"
        r"|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
        rf"|(?P<operator>{operators})"
        r"|(?P<punct>[(){},;:])"
        r"|\s+"
        r"|(?P<bad>.)"
    )


def _lookups(table: Table) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[Any]]]:
    keywords = {
        word: group for group in table.rxfs.keywords for word in group.words
    }
    builtins = {builtin.name: builtin for builtin in table.rxfs.builtins}
    operators: dict[str, list[Any]] = {}
    for operator in table.rxfs.operators:
        operators.setdefault(operator.token, []).append(operator)
    return keywords, builtins, operators


def check_rxfs(text: str, table: Table | None = None) -> list[Mark]:
    """Every token of a program of ``.rxfs``, green with its meaning or red with its refusal."""
    table = table or load_table()
    keywords, builtins, operators = _lookups(table)
    marks: list[Mark] = []
    for match in token_regex(table).finditer(text):
        start, end = match.span()
        which = match.lastgroup
        face = match.group()
        if which == "word":
            if face in keywords:
                group = keywords[face]
                note = group.note or group.scope
                marks.append(
                    _mark(text, start, end, True, "keyword", f"{group.group}: {note}", None)
                )
            elif face in builtins:
                builtin = builtins[face]
                marks.append(
                    _mark(text, start, end, True, "builtin", builtin.lowering, None)
                )
            else:
                marks.append(
                    _mark(text, start, end, True, "identifier", "a name", None)
                )
        elif which == "operator":
            entries = operators.get(face, [])
            meaning = entries[0].lowering if entries else "an operator"
            forms = ", ".join(sorted({entry.form for entry in entries}))
            marks.append(
                _mark(text, start, end, True, "operator", f"{face} ({forms}): {meaning}", None)
            )
        elif which == "bad":
            marks.append(
                _mark(
                    text,
                    start,
                    end,
                    False,
                    "bad",
                    None,
                    f"{face!r} is not a token of .rxfs: the language has no such symbol "
                    "(its operators are + - * & | << >> == != < > <= >= ! ~ =)",
                )
            )
        else:
            marks.append(
                _mark(text, start, end, True, which or "other", _KIND_MEANING.get(which or "", None), None)
            )
    return marks


# ---- the check of .rxfd -----------------------------------------------------------------------


_ATTRIBUTE = re.compile(r"([\w:.-]+)(=)(\")([^\"]*)(\")")
_ENUM = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_]*\.)*([A-Za-z_][A-Za-z0-9_]*)\.([A-Z][A-Za-z0-9_]*(?:\|[A-Z][A-Za-z0-9_]*)*)"
)


def _enum_marks(text: str, table: Table) -> list[Mark]:
    """Every value that names an enum type (``...FunctionImplementation.Semantic``): green when
    the enumerator is in the table, red when the type is known but the enumerator is not."""
    by_type = {entry.type: entry for entry in table.rxfd.enums}
    marks: list[Mark] = []
    for match in _ENUM.finditer(text):
        type_name, enumerators = match.group(1), match.group(2)
        entry = by_type.get(type_name)
        if entry is None:
            continue  # not an enum type of the table: a plain path, not ours to judge
        start, end = match.span()
        bad = [name for name in enumerators.split("|") if name not in entry.enumerators]
        if bad:
            marks.append(
                _mark(
                    text,
                    start,
                    end,
                    False,
                    "enum",
                    None,
                    f"{bad[0]!r} is not an enumerator of {type_name}",
                )
            )
        else:
            value = " | ".join(
                f"{type_name}.{name} = {entry.enumerators[name]}"
                for name in enumerators.split("|")
            )
            marks.append(_mark(text, start, end, True, "enum", value, None))
    return marks


def _reference_marks(
    text: str, references: list[resolve.Reference], table: Table
) -> list[Mark]:
    """The reference attribute values, resolved by the resolver: green with what they resolve to,
    red with why they do not. Values are paired with the resolver's answers in order of
    appearance (the resolver answers the same value the same way in one module)."""
    shapes = resolve.reference_shapes(table)
    pending: dict[tuple[str, str], list[resolve.Reference]] = {}
    for reference in references:
        pending.setdefault((reference.attribute, reference.value), []).append(reference)
    marks: list[Mark] = []
    for match in _ATTRIBUTE.finditer(text):
        attribute, value = match.group(1), match.group(4)
        if attribute not in shapes:
            continue
        queue = pending.get((attribute, value))
        if not queue:
            continue
        reference = queue.pop(0)
        value_start = match.start(4)
        value_end = match.end(4)
        if reference.resolved:
            marks.append(
                _mark(
                    text,
                    value_start,
                    value_end,
                    True,
                    "reference",
                    f"{attribute} resolves to {reference.target} ({reference.found_kind})",
                    None,
                )
            )
        else:
            marks.append(
                _mark(
                    text,
                    value_start,
                    value_end,
                    False,
                    "reference",
                    None,
                    f"{attribute}={value!r}: {reference.problem}",
                )
            )
    return marks


def check_rxfd(
    text: str,
    known: resolve.World | None = None,
    default_parent: str = "",
    source: str = "",
    table: Table | None = None,
) -> list[Mark]:
    """Every reference and enum value of a delta document, green with its meaning or red with the
    refusal. A document that does not parse is one red mark with the parse error."""
    table = table or load_table()
    try:
        ET.fromstring(text)
    except ET.ParseError as problem:
        position = getattr(problem, "position", (1, 0))
        offset = _offset_of(text, position)
        return [
            _mark(text, offset, min(len(text), offset + 1), False, "bad", None, str(problem))
        ]
    references = resolve.check_document(
        text, known=known, default_parent=default_parent, source=source, table=table
    )
    return sorted(
        [*_reference_marks(text, references, table), *_enum_marks(text, table)],
        key=lambda mark: mark.start,
    )


def _offset_of(text: str, position: tuple[int, int]) -> int:
    line, column = position
    at = 0
    for _ in range(max(0, line - 1)):
        at = text.find("\n", at) + 1
        if at == 0:
            return len(text)
    return min(len(text), at + max(0, column))


# ---- one door ---------------------------------------------------------------------------------


def check(
    text: str,
    language: str,
    *,
    known: resolve.World | None = None,
    default_parent: str = "",
    source: str = "",
    table: Table | None = None,
) -> dict[str, Any]:
    """The whole check of the editor: the marks and a green/red summary. `language` is `rxfs` or
    `rxfd` (the old names `rxf`/`xml` are accepted as aliases)."""
    table = table or load_table()
    if language in ("rxfd", "xml", ".rxfd", ".xml"):
        marks = check_rxfd(text, known, default_parent, source, table)
    elif language in ("rxfs", "rxf", ".rxfs", ".rxf"):
        marks = check_rxfs(text, table)
    else:
        raise ValueError(f"no language {language!r}: use rxfs or rxfd")
    refused = [mark for mark in marks if not mark.ok]
    return {
        "language": language,
        "ok": not refused,
        "tokens": len(marks),
        "refused": len(refused),
        "marks": [
            {
                "start": mark.start,
                "end": mark.end,
                "line": mark.line,
                "column": mark.column,
                "text": mark.text,
                "ok": mark.ok,
                "kind": mark.kind,
                "meaning": mark.meaning,
                "refusal": mark.refusal,
            }
            for mark in marks
        ],
    }
