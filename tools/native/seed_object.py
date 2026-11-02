"""An ELF relocatable of the seed's object-ABI code: the bodies a C link binds.

The seed CLI emits per-machine code as object-ABI hex (``tools/native/seed_build.py``:
``f(params..., out) -> status``). This module is the one place that turns that hex into
what the host toolchain links: the bodies are position-independent (they run wherever
they are mapped), so the object needs no relocations -- one ``.text`` of the bodies, one
global function symbol per snippet. Two host links build it: the import differential
(``tests/seed_object.py``, whose corpus files call the leaves) and the cap.crypto card
(``tools/native/crypto_build.py``, whose fill links the interpreter of
``native/graph.rxfs``, the compiler of ``native/compile.rxfs`` and the leaves of
``native/`` over their ``rxf_*`` names). Never a C twin of a seed body.
"""

from __future__ import annotations

import re
import struct
from collections.abc import Iterable
from pathlib import Path

import seed_build

NAMES = re.compile(r"\brxf_[A-Za-z0-9_]+")
# The ELF machine of a seed machine: the object is that machine's own code.
ELF_MACHINE = {"x86_64": 62, "aarch64": 183}


def symbols(sources: Iterable[Path]) -> list[str]:
    """Every ``rxf_*`` name the sources name (the bodies their link must bind)."""
    found: set[str] = set()
    for source in sources:
        found.update(NAMES.findall(source.read_text()))
    return sorted(found)


def key_of(name: str) -> str | None:
    """The snippet key a ``rxf_*`` name names, or ``None`` when no snippet defines it
    (a type or a constant of the ABI headers, or a name of a face's own)."""
    assert name.startswith("rxf_")
    key = name[len("rxf_") :]
    return key if seed_build.is_seed(key) else None


def bodies_of(machine: str, names: Iterable[str]) -> dict[str, bytes]:
    """The object-ABI code of the names that name a snippet (one seed run per name)."""
    return {
        name: bytes.fromhex(seed_build.code(machine, key))
        for name in names
        if (key := key_of(name)) is not None
    }


def object_bytes(bodies: dict[str, bytes], machine: str = "x86_64") -> bytes:
    """One ELF64 relocatable (ET_REL) of the bodies, one function symbol each."""
    if machine not in ELF_MACHINE:
        raise ValueError(f"no ELF machine for seed machine {machine!r}")
    text = bytearray()
    marks: dict[str, tuple[int, int]] = {}
    for name in sorted(bodies):
        text += bytes(-len(text) % 16)  # every snippet starts on its own line
        marks[name] = (len(text), len(bodies[name]))
        text += bodies[name]

    string = bytearray(b"\0")
    names: dict[str, int] = {}
    for name in sorted(bodies):
        names[name] = len(string)
        string += name.encode() + b"\0"

    # sh_name indices of .shstrtab's own table (offset 0 is its leading NUL)
    shstr = bytearray(b"\0.text\0.symtab\0.strtab\0.shstrtab\0.note.GNU-stack\0")
    at = {}
    offset = 1
    for section in (".text", ".symtab", ".strtab", ".shstrtab", ".note.GNU-stack"):
        at[section] = offset
        offset += len(section) + 1

    null = bytes(24)
    symtab = bytearray(null)  # symbol 0 is the null symbol, the only local one
    for name in sorted(bodies):
        where, size = marks[name]
        symtab += struct.pack(
            "<IBBHQQ", names[name], (1 << 4) | 2, 0, 1, where, size  # GLOBAL FUNC in .text
        )

    head = 64
    text_at = head
    sym_at = text_at + len(text) + (-len(text) % 8)
    str_at = sym_at + len(symtab)
    shstr_at = str_at + len(string)
    note_at = shstr_at + len(shstr)
    sh_at = note_at + (-note_at % 8)
    shnum = 6
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8),  # ELF64, little, System V
        1,  # ET_REL
        ELF_MACHINE[machine],
        1,
        0,
        0,
        sh_at,
        0,
        64,
        0,
        0,
        64,
        shnum,
        4,  # .shstrtab
    )

    def shdr(name_at: int, kind: int, flags: int, at: int, size: int,
             link: int = 0, info: int = 0, align: int = 1, entry: int = 0) -> bytes:
        return struct.pack(
            "<IIQQQQIIQQ", name_at, kind, flags, 0, at, size, link, info, align, entry
        )

    sections = (
        shdr(0, 0, 0, 0, 0),
        shdr(at[".text"], 1, 0x6, text_at, len(text), align=16),
        shdr(at[".symtab"], 2, 0, sym_at, len(symtab), link=3, info=1, align=8, entry=24),
        shdr(at[".strtab"], 3, 0, str_at, len(string)),
        shdr(at[".shstrtab"], 3, 0, shstr_at, len(shstr)),
        shdr(at[".note.GNU-stack"], 1, 0, note_at, 0),
    )
    return bytes(ehdr + text + bytes(sym_at - (text_at + len(text))) + symtab
                 + string + shstr + bytes(sh_at - note_at) + b"".join(sections))
