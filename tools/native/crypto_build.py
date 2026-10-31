"""Build the cap.crypto card (``native/crypto``) and report its library.

The card is the words of ``native/crypto/*.rxfs`` -- the ONE defining source of every
primitive (docs/25 §11) -- run by the C fill ``native/crypto/pm_crypto.c`` over the
seed's interpreter (``native/graph.rxfs``) and in-image compiler
(``native/compile.rxfs``, docs/18/29). This helper is the one build path: it compiles
the words out of the ``.rxfs`` sources into the generated ``build/crypto_words.c``
(bytes), packs the runtime the fill runs over -- the seed's own object-ABI bodies of
the interpreter, the compiler and the leaves, as one ELF relocatable
(``tools/native/seed_object.py``), never a C twin -- and compiles and links the fill,
the words and the runtime into the shared library of the card. The host binding
(``src/pymergetic/rxftk/net/crypto.py``) and the tests both come here, so the build and its
cache live in exactly one place.

    python tools/native/crypto_build.py            # build if stale, print the library path
    python tools/native/crypto_build.py --force    # always run the C compiler
    python tools/native/crypto_build.py --json     # print {"library": ..., "cached": ...}
    python tools/native/crypto_build.py --check    # exit 0 when nothing is stale (print nothing)

The build is cached by mtime: the C compiler runs only when a source (``*.rxfs`` of the
words or the runtime, the C of the fill, or the seed and words compilers of
``tools/native/``) is newer than the
built library. The library is ``native/crypto/build/libpm_crypto.so`` (or the shared
library name of the platform).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import seed_build
import seed_object

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CARD = ROOT / "native" / "crypto"
BUILD = CARD / "build"
NATIVE = ROOT / "native"
LIB_NAME = {
    "linux": "libpm_crypto.so",
    "darwin": "libpm_crypto.dylib",
}.get(sys.platform, "libpm_crypto.dll")

# The programs of the card the ABI names, and where their words live.
PROGRAMS = [
    "sha256",
    "blake2s",
    "chacha20poly1305",
    "aes_gcm",
    "hkdf",
    "hkdf_blake2s",
    "x25519",
]

# The C of the card: the fill of the ABI. The runtime the words run on is not C -- it is
# the seed's own bodies (native/graph.rxfs the interpreter, native/compile.rxfs the
# in-image compiler, the families of native/ the leaves), linked from their object-ABI
# code (docs/29/30) as build/runtime.o. A C twin of any of them is refused
# (one defining lang).
C_SOURCES = [
    CARD / "pm_crypto.c",
]

# The seed machine per host machine: the runtime the fill links is the host's own code.
HOST_MACHINE = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "aarch64",
    "arm64": "aarch64",
}


class BuildError(RuntimeError):
    """The C compiler is missing, or the card did not build."""


def _compiler() -> str:
    """The host C compiler: the clang the native sources are pinned to, else the system one."""
    return "clang-18" if shutil.which("clang-18") else "cc"


def host_machine() -> str:
    """The seed machine this host runs: the runtime bodies are the host's own code."""
    found = HOST_MACHINE.get(platform.machine().lower())
    if found is None:
        raise BuildError(
            f"no seed machine for host {platform.machine()!r}: the card links the "
            "object-ABI bodies of the host's own machine"
        )
    return found


def runtime_names(card: Path = CARD) -> list[str]:
    """The runtime the fill links, by symbol: every ``rxf_*`` name the fill references
    that a seed snippet defines (the interpreter, the compiler, the leaves)."""
    named = seed_object.symbols([card / "pm_crypto.c"])
    return [name for name in named if seed_object.key_of(name) is not None]


def runtime_object(card: Path = CARD) -> Path:
    """One ELF relocatable of the runtime bodies the fill links (bytes, the way a
    compiler writes them): the seed's object-ABI code of every symbol, rebuilt when
    the code changed."""
    machine = host_machine()
    bodies = seed_object.bodies_of(machine, runtime_names(card))
    data = seed_object.object_bytes(bodies, machine)
    out = card / "build" / "runtime.o"
    if not out.is_file() or out.read_bytes() != data:
        out.parent.mkdir(parents=True, exist_ok=True)
        made = out.with_name(f"{out.name}.{os.getpid()}.tmp")
        made.write_bytes(data)
        os.replace(made, out)  # a build of another process is replaced whole, never half
    return out


def words_sources(card: Path = CARD) -> list[Path]:
    """The ``.rxfs`` sources: what the words of the card are made of."""
    return sorted(card.glob("*.rxfs"))


def sources(card: Path = CARD) -> list[Path]:
    """Every file whose mtime decides whether the build is stale."""
    here = Path(__file__).resolve()
    tools = ROOT / "tools" / "native"
    found = [here, tools / "rxfc.py", card / "words.h"]
    found.extend(
        tools / f"{name}.py"
        for name in ("seed_object", "seed_build", "seed_cache", "graph_build", "compile_build")
    )
    found.append(NATIVE / "seed" / "seed.c")
    found.extend(C_SOURCES)
    found.extend(sorted((NATIVE).glob("*.h")))
    found.extend(words_sources(card))
    # the .rxfs sources of the runtime bodies the fill links (graph_build/compile_build
    # compile through seed_build too, so their sources are named by the keys)
    for name in runtime_names(card):
        key = seed_object.key_of(name)
        assert key is not None
        found.append(ROOT / seed_build.source_of(key))
    return [path for path in found if path.is_file()]


def library_path(card: Path = CARD) -> Path:
    """Where the built card lands."""
    return card / "build" / LIB_NAME


def words_path(card: Path = CARD) -> Path:
    """The generated words: the bytes of every program, as one C file."""
    return card / "build" / "crypto_words.c"


def is_stale(card: Path = CARD) -> bool:
    """True when the library is missing or older than any source."""
    library = library_path(card)
    if not library.is_file():
        return True
    built = library.stat().st_mtime_ns
    return any(path.stat().st_mtime_ns > built for path in sources(card))


def make_words(card: Path = CARD) -> Path:
    """Compile the ``.rxfs`` programs into the generated C of their words.

    The generated file is bytes and nothing else (the programs stay where their language
    lives); it is rewritten when any program changed.
    """
    sys.path.insert(0, str(HERE))
    try:
        import rxfc
    finally:
        sys.path.pop(0)
    out = words_path(card)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "/* The words of the .rxfs sources of native/crypto, generated by",
        " * tools/native/crypto_build.py. Do not edit: the programs are the .rxfs sources,",
        " * this file is their bytes. */",
        '#include "../words.h"',
        "",
    ]
    for name in PROGRAMS:
        program = rxfc.compile_file(card / f"{name}.rxfs")
        if len(program) % 8 != 0 or len(program) < 40:
            raise BuildError(f"{name}.rxfs compiled to {len(program)} bytes, not words")
        magic, _frame, _params, _words, _pc = struct.unpack_from("<5Q", program)
        if magic != 0x31475852:
            raise BuildError(f"{name}.rxfs compiled to a foreign magic {magic:#x}")
        lines.append(f"static const rxf_u64 words_{name}[] = {{")
        values = struct.unpack(f"<{len(program) // 8}Q", program)
        for i in range(0, len(values), 4):
            chunk = ", ".join(f"0x{value:016x}ULL" for value in values[i : i + 4])
            lines.append(f"    {chunk},")
        lines.append("};")
        lines.append("")
    lines.append("const pm_words pm_words_table[] = {")
    for name in PROGRAMS:
        lines.append(f'    {{"{name}", words_{name}, sizeof(words_{name})}},')
    lines.append("    {0, 0, 0},")
    lines.append("};")
    lines.append("")
    made = out.with_suffix(f".{os.getpid()}.c")
    made.write_text("\n".join(lines))
    os.replace(made, out)  # a build of another process is replaced whole, never half
    return out


def build(*, force: bool = False, compiler: str | None = None, card: Path = CARD) -> Path:
    """Compile the card into its shared library, cached by source mtime.

    Returns the built library's path. Runs the C compiler only when ``is_stale()`` says so
    (or when ``force``), keeping test runs fast.
    """
    library = library_path(card)
    if not force and not is_stale(card):
        return library
    cc = compiler or _compiler()
    if not shutil.which(cc):
        raise BuildError(f"{cc!r} not found on PATH; the card is the C fill of its words")
    words = make_words(card)
    runtime = runtime_object(card)
    library.parent.mkdir(parents=True, exist_ok=True)
    # Link to a private temp path and swap it in whole: a parallel worker crossing
    # crypto.load() must never open a half-written library. Same discipline as the
    # part artifacts above (os.replace), now for the final link too.
    made = library.parent / f".{library.name}.{os.getpid()}.tmp"
    command = [
        cc,
        "-std=c11",
        "-O2",
        "-fPIC",
        "-shared",
        "-Wno-unknown-pragmas",
        *[str(path) for path in C_SOURCES],
        str(words),
        str(runtime),
        "-o",
        str(made),
    ]
    try:
        ran = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise BuildError(f"{cc!r} not found on PATH") from error
    if ran.returncode != 0:
        made.unlink(missing_ok=True)
        raise BuildError(
            f"{cc} failed (exit {ran.returncode}):\n{ran.stderr.strip() or ran.stdout.strip()}"
        )
    if not made.is_file():
        raise BuildError(f"{cc} reported success but {made} does not exist")
    os.replace(made, library)  # a build of another process is replaced whole, never half
    return library


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--force", action="store_true", help="always run the C compiler")
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    parser.add_argument(
        "--check", action="store_true", help="exit 1 when the build is stale"
    )
    args = parser.parse_args(argv)

    stale = is_stale()
    if args.check:
        return 1 if stale else 0
    library = build(force=args.force)
    if args.json:
        cached = not stale and not args.force
        print(json.dumps({"library": str(library), "cached": cached}))
    else:
        print(library)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
