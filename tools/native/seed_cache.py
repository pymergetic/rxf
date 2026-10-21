"""The seed compile cache: the CLI's hex per (argv, source), in memory and on disk.

The seed codegen is deterministic -- the same argv over the same source always
prints the same hex -- yet the suite compiles the same sources over and over: an
image composes the same snippets per test, and the pytest workers repeat each
other. Every CLI run goes through here first: an in-process memo for the repeats
within a worker, a content-addressed file for the repeats across workers and
across runs. The key is the sha256 of the argv and the source text, so an edit to
any input is a new key and a stale entry can never be served.

    from seed_cache import run
    hex = run(argv, source_text)

Only successes are cached; a refusal or a crash of the CLI raises through and
compiles again next time.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

# .cache/ is gitignored (line 47 of the package's .gitignore); the path is stable
# so the pytest workers share one store.
CACHE = Path(__file__).resolve().parents[2] / ".cache" / "seed"

MEMORY: dict[str, str] = {}


def key_of(argv: list[str], source: str) -> str:
    """The content address of one CLI run: argv and source text, hashed together."""
    return hashlib.sha256(
        "\0".join(argv).encode() + b"\0" + source.encode()
    ).hexdigest()


def run(argv: list[str], source: str) -> str:
    """The seed CLI's stdout for ``argv`` over ``source``: memoed in memory, then on disk."""
    key = key_of(argv, source)
    hit = MEMORY.get(key)
    if hit is not None:
        return hit
    path = CACHE / f"{key}.hex"
    if path.exists():
        hit = path.read_text().strip()
    else:
        hit = subprocess.run(
            argv, input=source, capture_output=True, text=True, check=True
        ).stdout.strip()
        CACHE.mkdir(parents=True, exist_ok=True)
        # The workers race here by design (same key, same bytes): write under a
        # name of this process and rename, which is atomic on the same filesystem.
        tmp = CACHE / f"{key}.{os.getpid()}.tmp"
        tmp.write_text(hit + "\n")
        tmp.replace(path)
    MEMORY[key] = hit
    return hit
