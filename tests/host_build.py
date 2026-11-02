"""Building the host C proofs of ``tests/host`` (the SMP witness, the asyncio model).

One recipe for every proof binary: the clang the native sources are pinned to (else the system
compiler), C11 with every warning an error, a mode for the sanitizers, and a cache keyed by the
content of the sources, so a binary is rebuilt only when a source or a flag changed.
"""

import hashlib
import os
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

HOST = Path(__file__).parent / "host"
BUILD = HOST / "build"


def compiler() -> str:
    """The host C compiler: the clang the native sources are pinned to, else the system one."""
    return "clang-18" if shutil.which("clang-18") else "cc"


def binary(
    sources: Sequence[Path], stem: str, mode: str = "", extra: Sequence[str] = ()
) -> Path:
    """The proof binary of ``sources`` for a mode ("" standard, "tsan", "asan"), compiled when
    a source changed."""
    cc = compiler()
    flags = ["-std=c11", "-O2", "-g", "-pthread",
             "-Wall", "-Wextra", "-Werror", "-Wimplicit-function-declaration"]
    if mode == "tsan":
        flags += ["-fsanitize=thread"]
    elif mode == "asan":
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                  "-DRXF_SMP_ASAN"]
    flags += list(extra)
    digest = hashlib.sha256()
    for source in [*sources, Path(cc), Path(mode)]:
        digest.update(source.name.encode())
        if source.is_file():
            digest.update(source.read_bytes())
    # the headers the sources include are part of the content too
    for header in sorted(HOST.glob("*.h")):
        digest.update(header.read_bytes())
    digest.update(" ".join(flags).encode())
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / f"{stem}-{digest.hexdigest()[:16]}{('.' + mode) if mode else ''}"
    if not out.exists():
        made = out.with_suffix(f".{os.getpid()}")
        subprocess.run([cc, *flags, *map(str, sources), "-o", str(made)], check=True)
        os.replace(made, out)  # a build of another process is replaced whole, never half
    return out
