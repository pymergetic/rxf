"""An ELF relocatable of the seed's object-ABI code: the leaves a C link binds.

The import differential links a corpus file with the leaves it calls so the compiled
guest runs against the same leaf bodies the imported graph runs. Those bodies are the
seed's (``tools/native/seed_build.py``, ``native/**/*.rxfs``), never a C twin: this
module packs the object-ABI code of one snippet per ``rxf_*`` symbol into an ELF64
x86_64 relocatable -- bytes, the way a compiler writes them -- so the host toolchain
can link them beside the corpus file. The snippets are position-independent (they run
wherever ``tests/rxf_native.py`` maps them), so the object needs no relocations: one
``.text`` of the bodies, one global function symbol per snippet.

The packing is the one packer of the tree (``tools/native/seed_object.py`` -- the same
one ``tools/native/crypto_build.py`` links the cap.crypto card with); this module keeps
the differential's own step: only the symbols a link names are built (the ``rxf_*``
names of the sources given), one seed run per name, cached beside the host glue of
``rxf_native``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from functools import cache
from pathlib import Path

import seed_build
from seed_object import bodies_of, object_bytes, symbols

HERE = Path(__file__).parent
BUILD = HERE / "host" / "build"


@cache
def _code(names: tuple[str, ...]) -> dict[str, bytes]:
    """The x86_64 object-ABI bodies of the names that name a snippet (one run per name)."""
    return bodies_of("x86_64", names)


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
        made.write_bytes(object_bytes(bodies, machine))
        made.replace(out)  # a build of another process is replaced whole, never half
    return out
