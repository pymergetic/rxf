"""Stage all RXF generators and emit machine-readable provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
GENERATORS = ("generate_conversions.py", "generate_native.py", "generate_qemu_blobs.py")
INPUTS = (
    "native/basics.c",
    "native/basics.h",
    "tools/generate_conversions.py",
    "tools/generate_native.py",
    "tools/extract_native.py",
    "tools/native_symbols.py",
)
ARTIFACTS = (
    "native/conversions.c",
    "generated/native_x86_64.json",
    "generated/native_aarch64.json",
    "generated/qemu_aarch64_blobs.S",
)
PROVENANCE = Path("generated/provenance.json")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> dict[str, bytes | None]:
    return {
        name: (ROOT / name).read_bytes() if (ROOT / name).exists() else None
        for name in (*ARTIFACTS, str(PROVENANCE))
    }


def provenance() -> dict[str, object]:
    compiler = json.loads((ROOT / "generated/native_x86_64.json").read_text())
    return {
        "schema": 1,
        "generators": [
            {"path": f"tools/{name}", "sha256": digest(ROOT / "tools" / name)}
            for name in GENERATORS
        ],
        "sources": [{"path": name, "sha256": digest(ROOT / name)} for name in INPUTS],
        "artifacts": [
            {"path": name, "sha256": digest(ROOT / name)} for name in ARTIFACTS
        ],
        "compiler": {
            "name": compiler.get("compiler"),
            "target": compiler.get("target"),
            "recipe": compiler.get("recipe"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    before = snapshot()
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    for name in GENERATORS:
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / name)], cwd=ROOT, env=env, check=True
        )
    body = json.dumps(provenance(), indent=2, sort_keys=True) + "\n"
    (ROOT / PROVENANCE).write_text(body)
    if args.check:
        after = snapshot()
        changed = sorted(name for name in after if before.get(name) != after[name])
        for name, content in before.items():
            path = ROOT / name
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        if changed:
            print("generated files are stale: " + ", ".join(changed), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
