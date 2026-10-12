"""Strict WebAssembly reader for self-contained RXF native leaves.

A wasm32 snippet is the *body* of one function (local declarations, the
expression, ``end``) with the function type as its signature. It is
self-contained under one convention, the whole wasm ABI of the starter:

* memory 0 is the linear memory (imported by whoever composes the snippets),
* global 0 is the shadow-stack pointer (a mutable i32),
* a snippet calls no function and has no data segment; the one that runs a
  composed function (``graph_run``) calls the leaves through table 0, which is
  imported too. Its ``call_indirect`` names the function type of the leaf it
  calls by the index of the *call types*: the types that come first in the type
  section of whoever composes the snippets, in the order of the leaves.

``check_object`` refuses an object file whose snippets need anything else,
``read_module`` cuts the bodies out of the linked module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

MAGIC = b"\0asm\1\0\0\0"
VALUE_TYPES = {0x7F: "i32", 0x7E: "i64", 0x7D: "f32", 0x7C: "f64"}
FUNCTION_TYPE = 0x60
I32 = 0x7F
MUTABLE = 0x01
STACK_POINTER = "__stack_pointer"

Signature = tuple[tuple[str, ...], tuple[str, ...]]


class Section(IntEnum):
    CUSTOM = 0
    TYPE = 1
    IMPORT = 2
    FUNCTION = 3
    GLOBAL = 6
    EXPORT = 7
    CODE = 10


class External(IntEnum):
    """What an import or an export names (the same codes in both sections)."""

    FUNCTION = 0
    TABLE = 1
    MEMORY = 2
    GLOBAL = 3


class Symbol(IntEnum):
    """Kinds in the symbol table of the ``linking`` section."""

    FUNCTION = 0
    DATA = 1
    GLOBAL = 2
    SECTION = 3
    EVENT = 4
    TABLE = 5


# Symbol kinds that name an index into a module space (imports and definitions).
INDEXED_SYMBOLS = (Symbol.FUNCTION, Symbol.GLOBAL, Symbol.EVENT, Symbol.TABLE)
# For an undefined symbol the name lives in the import of the same space.
IMPORT_OF_SYMBOL = {
    Symbol.FUNCTION: External.FUNCTION,
    Symbol.GLOBAL: External.GLOBAL,
    Symbol.TABLE: External.TABLE,
}

LINKING_SYMBOL_TABLE = 8
SYMBOL_UNDEFINED = 0x10
SYMBOL_EXPLICIT_NAME = 0x40
LIMITS_HAS_MAXIMUM = 0x01

R_WASM_TYPE_INDEX_LEB = 6  # the index of a function type, in a call_indirect
R_WASM_GLOBAL_INDEX_LEB = 7  # the stack pointer
R_WASM_TABLE_NUMBER_LEB = 20  # table 0, in a call_indirect
INDIRECT_TABLE = "__indirect_function_table"
PADDED_LEB = 5  # a relocated index is written in five bytes
# Relocation types whose entry ends in a signed LEB addend (WebAssembly
# tool-conventions, Linking): memory addresses, section/function offsets.
RELOCATIONS_WITH_ADDEND = frozenset({3, 4, 5, 8, 9, 11, 14, 15, 16, 17, 21, 22, 23, 25})


@dataclass(frozen=True)
class Function:
    params: tuple[str, ...]
    results: tuple[str, ...]
    body: bytes


def _uleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, at


def _sleb(data: bytes, at: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            if byte & 0x40:
                value -= 1 << shift
            return value, at


def _name(data: bytes, at: int) -> tuple[str, int]:
    size, at = _uleb(data, at)
    return data[at : at + size].decode("ascii"), at + size


def _limits(data: bytes, at: int) -> int:
    """Skip the limits of a table or memory; return the offset behind them."""
    flags, at = _uleb(data, at)
    _, at = _uleb(data, at)
    if flags & LIMITS_HAS_MAXIMUM:
        _, at = _uleb(data, at)
    return at


def _sections(data: bytes) -> list[tuple[int, int, int]]:
    """Every section as (id, start of its payload, payload size)."""
    if data[:8] != MAGIC:
        raise ValueError("not a WebAssembly 1.0 binary")
    found: list[tuple[int, int, int]] = []
    at = 8
    while at < len(data):
        sid = data[at]
        size, start = _uleb(data, at + 1)
        found.append((sid, start, size))
        at = start + size
    if at != len(data):
        raise ValueError("section sizes do not add up")
    return found


def _types(data: bytes, at: int) -> list[Signature]:
    count, at = _uleb(data, at)
    types: list[Signature] = []
    for _ in range(count):
        if data[at] != FUNCTION_TYPE:
            raise ValueError("expected a function type")
        n, at = _uleb(data, at + 1)
        params = tuple(VALUE_TYPES[t] for t in data[at : at + n])
        at += n
        n, at = _uleb(data, at)
        results = tuple(VALUE_TYPES[t] for t in data[at : at + n])
        at += n
        types.append((params, results))
    return types


def _import_names(data: bytes, start: int) -> dict[int, list[str]]:
    """The imported names of an object file, per kind of import."""
    imported: dict[int, list[str]] = {kind: [] for kind in External}
    count, at = _uleb(data, start)
    for _ in range(count):
        _, at = _name(data, at)
        label, at = _name(data, at)
        kind = data[at]
        at += 1
        if kind == External.FUNCTION:
            _, at = _uleb(data, at)  # the type index
        elif kind == External.TABLE:
            at = _limits(data, at + 1)  # the element type, then the limits
        elif kind == External.MEMORY:
            at = _limits(data, at)
        else:
            at += 2  # a global: value type and mutability
        imported[kind].append(label)
    return imported


def _symbol_names(
    data: bytes, at: int, imported: dict[int, list[str]]
) -> list[str | None]:
    """The names in the symbol table; ``None`` where a symbol has none."""
    names: list[str | None] = []
    count, at = _uleb(data, at)
    for _ in range(count):
        kind = data[at]
        flags, at = _uleb(data, at + 1)
        label: str | None = None
        if kind in INDEXED_SYMBOLS:
            index, at = _uleb(data, at)
            if not flags & SYMBOL_UNDEFINED or flags & SYMBOL_EXPLICIT_NAME:
                label, at = _name(data, at)
            elif kind in IMPORT_OF_SYMBOL:
                known = imported[IMPORT_OF_SYMBOL[Symbol(kind)]]
                label = known[index] if index < len(known) else None
        elif kind == Symbol.DATA:
            label, at = _name(data, at)
            if not flags & SYMBOL_UNDEFINED:
                for _ in range(3):  # segment, offset, size
                    _, at = _uleb(data, at)
        else:  # a section symbol names the section by index
            _, at = _uleb(data, at)
        names.append(label)
    return names


def _custom_sections(
    data: bytes, sections: list[tuple[int, int, int]]
) -> dict[str, tuple[int, int]]:
    """Custom sections by label, as (start of the content, end)."""
    found: dict[str, tuple[int, int]] = {}
    for sid, start, size in sections:
        if sid == Section.CUSTOM:
            label, at = _name(data, start)
            found[label] = (at, start + size)
    return found


def _linking_symbols(
    data: bytes, linking: tuple[int, int], imported: dict[int, list[str]]
) -> list[str | None]:
    at, end = linking
    _, at = _uleb(data, at)  # the version of the linking metadata
    while at < end:
        kind = data[at]
        size, at = _uleb(data, at + 1)
        if kind == LINKING_SYMBOL_TABLE:
            return _symbol_names(data, at, imported)
        at += size
    return []


@dataclass(frozen=True)
class Relocation:
    kind: int
    offset: int  # in the code section, from the start of its payload
    index: int  # a symbol, or a type for R_WASM_TYPE_INDEX_LEB


def _relocations(data: bytes, relocations: tuple[int, int]) -> list[Relocation]:
    found: list[Relocation] = []
    at, _ = relocations
    _, at = _uleb(data, at)  # the section the relocations apply to
    count, at = _uleb(data, at)
    for _ in range(count):
        kind = data[at]
        offset, at = _uleb(data, at + 1)
        index, at = _uleb(data, at)
        if kind in RELOCATIONS_WITH_ADDEND:
            _, at = _sleb(data, at)
        found.append(Relocation(kind, offset, index))
    return found


def _relocation_problems(
    relocations: list[Relocation], names: list[str | None]
) -> list[str]:
    problems: list[str] = []
    for found in relocations:
        if found.kind == R_WASM_TYPE_INDEX_LEB:
            continue
        target = names[found.index] if found.index < len(names) else None
        stack = found.kind == R_WASM_GLOBAL_INDEX_LEB and target == STACK_POINTER
        table = found.kind == R_WASM_TABLE_NUMBER_LEB and target == INDIRECT_TABLE
        if not stack and not table:
            problems.append(f"relocation {found.kind} against {target}")
    return problems


def check_object(path: Path) -> list[str]:
    """What keeps the functions of an object file from standing alone."""
    data = path.read_bytes()
    sections = _sections(data)
    custom = _custom_sections(data, sections)
    imported: dict[int, list[str]] = {kind: [] for kind in External}
    for sid, start, _ in sections:
        if sid == Section.IMPORT:
            imported = _import_names(data, start)
    names = _linking_symbols(data, custom["linking"], imported)
    if "reloc.CODE" not in custom:
        return []
    relocations = _relocations(data, custom["reloc.CODE"])
    return sorted(set(_relocation_problems(relocations, names)))


def _read_imports(data: bytes, start: int) -> list[tuple[str, str, int]]:
    """The imports of a linked module: the linear memory, and table 0 for the leaves."""
    imports: list[tuple[str, str, int]] = []
    count, at = _uleb(data, start)
    for _ in range(count):
        module, at = _name(data, at)
        label, at = _name(data, at)
        kind = data[at]
        imports.append((module, label, kind))
        if kind == External.MEMORY:
            at = _limits(data, at + 1)
        elif kind == External.TABLE:
            at = _limits(data, at + 2)  # the element type, then the limits
        else:
            raise ValueError(f"module imports {module}.{label}, not a memory or table")
    return imports


def _read_exports(data: bytes, start: int) -> dict[str, int]:
    exports: dict[str, int] = {}
    count, at = _uleb(data, start)
    for _ in range(count):
        label, at = _name(data, at)
        kind = data[at]
        index, at = _uleb(data, at + 1)
        if kind == External.FUNCTION:
            exports[label] = index
    return exports


def _read_bodies(data: bytes, start: int) -> list[tuple[int, bytes]]:
    """The function bodies as (offset from the payload of the section, bytes)."""
    bodies: list[tuple[int, bytes]] = []
    count, at = _uleb(data, start)
    for _ in range(count):
        length, at = _uleb(data, at)
        bodies.append((at - start, data[at : at + length]))
        at += length
    return bodies


def _check_globals(data: bytes, start: int) -> int:
    """The number of globals; the one allowed is a mutable i32 (global 0)."""
    count, at = _uleb(data, start)
    if count != 1 or data[at : at + 2] != bytes((I32, MUTABLE)):
        raise ValueError("global 0 must be the one mutable i32 stack pointer")
    return count


def _padded_leb(value: int) -> bytes:
    """An index in five bytes, the way a linker writes a relocated one."""
    return bytes(
        ((value >> (7 * n)) & 0x7F) | (0x80 if n < PADDED_LEB - 1 else 0)
        for n in range(PADDED_LEB)
    )


def _canonical_calls(
    body: bytes,
    start: int,
    relocations: list[Relocation],
    types: list[Signature],
    call_types: list[Signature],
) -> bytes:
    """The body with each call_indirect naming its type among the call types."""
    patched = bytearray(body)
    for found in relocations:
        at = found.offset - start
        if found.kind != R_WASM_TYPE_INDEX_LEB or not 0 <= at < len(body):
            continue
        signature = types[found.index]
        if signature not in call_types:
            raise ValueError(
                f"call_indirect of {signature} is not among the call types"
            )
        patched[at : at + PADDED_LEB] = _padded_leb(call_types.index(signature))
    return bytes(patched)


def read_module(
    path: Path, exported: set[str], call_exports: tuple[str, ...] = ()
) -> dict[str, Function]:
    """The exported functions of a linked module, once it fits the convention.

    The call types are the types of the functions ``call_exports`` in this order;
    the module has to be linked with ``--emit-relocs`` when a body calls through
    the table.
    """
    data = path.read_bytes()
    sections = _sections(data)
    custom = _custom_sections(data, sections)
    types: list[Signature] = []
    functions: list[int] = []
    exports: dict[str, int] = {}
    bodies: list[tuple[int, bytes]] = []
    imports: list[tuple[str, str, int]] = []
    globals_seen = 0
    for sid, start, _ in sections:
        if sid == Section.TYPE:
            types = _types(data, start)
        elif sid == Section.IMPORT:
            imports = _read_imports(data, start)
        elif sid == Section.FUNCTION:
            count, at = _uleb(data, start)
            for _ in range(count):
                index, at = _uleb(data, at)
                functions.append(index)
        elif sid == Section.GLOBAL:
            globals_seen = _check_globals(data, start)
        elif sid == Section.EXPORT:
            exports = _read_exports(data, start)
        elif sid == Section.CODE:
            bodies = _read_bodies(data, start)
        elif sid != Section.CUSTOM:
            raise ValueError(f"section {sid} is outside the snippet convention")
    kinds = sorted(kind for _, _, kind in imports)
    if kinds not in ([External.MEMORY], [External.TABLE, External.MEMORY]):
        raise ValueError(
            "the module must import the linear memory, and at most table 0"
        )
    if globals_seen != 1:
        raise ValueError("the module must define exactly the stack pointer")
    if set(exports) != exported:
        raise ValueError(
            f"export set mismatch; missing={sorted(exported - set(exports))}, "
            f"extra={sorted(set(exports) - exported)}"
        )
    call_types = [types[functions[exports[name]]] for name in call_exports]
    relocations = (
        _relocations(data, custom["reloc.CODE"]) if "reloc.CODE" in custom else []
    )
    found: dict[str, Function] = {}
    for label, index in exports.items():
        params, results = types[functions[index]]
        start, body = bodies[index]
        if any(r.kind == R_WASM_TYPE_INDEX_LEB for r in relocations):
            body = _canonical_calls(body, start, relocations, types, call_types)
        found[label] = Function(params, results, body)
    return found
