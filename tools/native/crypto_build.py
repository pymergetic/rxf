"""Build the cap.crypto Rust card (``native/crypto``) and report its library.

The card is ``impl = rs``: one Cargo crate, no external crates, offline
buildable. This helper is the one build path — the host binding
(``src/pymergetic/rxftk/net/crypto.py``) and the tests both come here, so the
build and its cache live in exactly one place.

    python tools/native/crypto_build.py            # build if stale, print the library path
    python tools/native/crypto_build.py --force    # always run cargo
    python tools/native/crypto_build.py --json     # print {"library": ..., "cached": ...}
    python tools/native/crypto_build.py --check    # exit 0 when nothing is stale (print nothing)

The build is cached by mtime: ``cargo build --release`` runs only when a
``native/crypto`` source (``*.rs``, ``Cargo.toml``, ``Cargo.lock``) is newer
than the built library. With cargo's default target directory the library is
``native/crypto/target/release/libpm_crypto.so``; when ``CARGO_TARGET_DIR`` is
set, cargo puts it under that directory and this helper follows it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CRATE = ROOT / "native" / "crypto"
LIB_NAME = {
    "linux": "libpm_crypto.so",
    "darwin": "libpm_crypto.dylib",
}.get(sys.platform, "libpm_crypto.dll")


class BuildError(RuntimeError):
    """cargo is missing, or the crate did not build."""


def sources(crate: Path = CRATE) -> list[Path]:
    """Every file whose mtime decides whether the build is stale."""
    found = [crate / "Cargo.toml"]
    lock = crate / "Cargo.lock"
    if lock.is_file():
        found.append(lock)
    found.extend(sorted(crate.rglob("*.rs")))
    return found


def target_dir(crate: Path = CRATE) -> Path:
    """cargo's target directory for the crate (honors ``CARGO_TARGET_DIR``)."""
    override = os.environ.get("CARGO_TARGET_DIR")
    return Path(override) if override else crate / "target"


def library_path(crate: Path = CRATE) -> Path:
    """Where the release build of the card lands."""
    return target_dir(crate) / "release" / LIB_NAME


def is_stale(crate: Path = CRATE) -> bool:
    """True when the library is missing or older than any source."""
    library = library_path(crate)
    if not library.is_file():
        return True
    built = library.stat().st_mtime_ns
    return any(path.stat().st_mtime_ns > built for path in sources(crate))


def build(*, force: bool = False, cargo: str = "cargo", crate: Path = CRATE) -> Path:
    """``cargo build --release`` in ``native/crypto``, cached by source mtime.

    Returns the built library's path. Runs cargo only when ``is_stale()`` says
    so (or when ``force``), keeping test runs fast.
    """
    library = library_path(crate)
    if not force and not is_stale(crate):
        return library
    command = [cargo, "build", "--release"]
    try:
        ran = subprocess.run(
            command, cwd=crate, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise BuildError(f"{cargo!r} not found on PATH; the card is impl = rs") from error
    if ran.returncode != 0:
        raise BuildError(
            f"{' '.join(command)} failed in {crate} (exit {ran.returncode}):\n"
            f"{ran.stderr.strip() or ran.stdout.strip()}"
        )
    if not library.is_file():
        raise BuildError(f"cargo reported success but {library} does not exist")
    return library


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--force", action="store_true", help="always run cargo")
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
