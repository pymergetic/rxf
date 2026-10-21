"""An ELF relocatable of the seed's object-ABI code: the leaves a C link binds.

The import differential links a corpus file with the leaves it calls so the compiled
guest runs against the same leaf bodies the imported graph runs. Those bodies are the
seed's (``tools/native/seed_build.py``, ``native/**/*.rxfs``), never a C twin: this
module packs the object-ABI code of one snippet per ``rxf_*`` symbol into an ELF64
x86_64 relocatable -- bytes, the way a compiler writes them -- so the host toolchain
can link them beside the corpus file. The snippets are position-independent (they run
wherever ``tests/rxf_native.py`` maps them), so the object needs no relocations: one
``.text`` of the bodies, one global function symbol per snippet.

Only the symbols a link names are built: the ``rxf_*`` names of the sources given
(``symbols``), one seed run per name, cached beside the host glue of ``rxf_native``.
"""

from __future__ import annotations

import hashlib
import re
import struct
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import seed_build

HERE = Path(__file__).parent
BUILD = HERE / "host" / "build"
NAMES = re.compile(r"\brxf_[A-Za-z0-9_]+")


def symbols(sources: Iterable[Path]) -> list[str]:
    """Every ``rxf_*`` name the sources name (the leaves their link must bind)."""
    found: set[str] = set()
    for source in sources:
        found.update(NAMES.findall(source.read_text()))
    return sorted(found)


def key_of(name: str) -> str | None:
    """The snippet key a C leaf name names, or ``None`` when no snippet defines it
    (a corpus's own face or the header's inline helpers name ``rxf_*`` too)."""
    assert name.startswith("rxf_")
    key = name[len("rxf_") :]
    return key if key in seed_build.SOURCES else None


@lru_cache(maxsize=None)
def _code(names: tuple[str, ...]) -> dict[str, bytes]:
    """The x86_64 object-ABI bodies of the names that name a snippet (one run per name)."""
    return {
        name: bytes.fromhex(seed_build.code("x86_64", key))
        for name in names
        if (key := key_of(name)) is not None
    }


def object_bytes(bodies: dict[str, bytes]) -> bytes:
    """One ELF64 x86_64 relocatable (ET_REL) of the bodies, one function symbol each."""
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
        62,  # EM_X86_64
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


def write(sources: Iterable[Path], machine: str = "x86_64") -> Path:
    """The object file of the leaves ``sources`` name, built when the code changed."""
    assert machine == "x86_64", "the differential's link is the host's own"
    sources = sorted(sources)
    names = tuple(symbols(sources))
    bodies = _code(names)
    digest = hashlib.sha256()
    digest.update(repr(sorted(bodies)).encode())
    for name, body in sorted(bodies.items()):
        digest.update(name.encode() + body)
    digest.update((seed_build.NATIVE / "seed" / "seed.c").read_bytes())
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / f"seed_leaves-{digest.hexdigest()[:16]}.o"
    if not out.exists():
        made = out.with_suffix(".o.tmp")
        made.write_bytes(object_bytes(bodies))
        made.replace(out)  # a build of another process is replaced whole, never half
    return out
