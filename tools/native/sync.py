"""Write the derived native objects of the starter: BIOS and wasm32.

The starter keeps, for every native function, its machine code per target next
to the ABI that says how to call it. The sets come from `native_build.py`, and
the sources of truth for the bytes are the Seed sources of `native/**/*.rxfs`
(docs/29). This script adds what follows from them:

* ``x86_64_bios``: the x86_64 bytes (Stage 2 runs them in long mode) with the
  System V ABI of the BIOS target.
* ``wasm32``: the function body the seed makes for that target, with an ABI
  derived from the shape of the function (the C operands the seed spells) and
  the semantic ABI of the x86_64 set (same arguments, same meaning, the wasm32
  way of passing).
* ``sources/``: the build history -- one module and one ``Source`` object per
  file of ``native/`` with its text and the digest of the text, named on every
  function its code was built from (docs/31 §3).

All of it is rewritten on every run, so a changed source shows up here.

    python tools/native/sync.py [--check] [STARTER]

``--check`` writes nothing and exits 1 when the starter is not what this
produces.
"""

from __future__ import annotations

import copy
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
import blob_sync
import boot_sync
import compose
import graph_header
import graph_sync
import host_sync
import live_sync
import native_build
import programs
import source_sync

from pymergetic.rxftk.core import tree

ROOT = HERE.parents[1]
RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
ET.register_namespace("rxf", RXF)

BIOS = "x86_64_bios"
WASM = "wasm32"
PROVENANCE_TAG = "CompilerProvenance"
GENERATED_CODE = {BIOS, WASM}


class SyncError(Exception):
    pass


def provenance_name(machine: str) -> str:
    """The seed recipe pin the CompilerProvenance points at."""
    return f"seed_{machine}"


def machine_of(target: str | None) -> str:
    """``x86_64`` for ``@targets.x86_64_bios_sysv``, ``wasm32`` for the wasm target."""
    name = word(target)
    for machine in native_build.MACHINES:
        if name.startswith(machine):
            return machine
    raise SyncError(f"no machine for the target {target}")


def set_provenance(function: ET.Element) -> None:
    """Every Code object names the recipe of its machine."""
    for code in function:
        if code.tag != "Code":
            continue
        machine = machine_of(code.get("runtime_target"))
        for ref in code.findall(f"{{{RXF}}}ref"):
            if word(ref.get("role")) == "Provenance":
                ref.set("target", f"@targets.{provenance_name(machine)}")


def word(value: str | None) -> str:
    """The last path segment of a written reference."""
    return (value or "").rsplit(".", 1)[-1]


def snippet_key(parts: tuple[str, ...], name: str, known: set[str]) -> str:
    """``checked.add_int8_t`` is ``checked_add_int8_t``, ``memory.copy`` is ``memory_copy``.

    The one rule lives in ``graph_sync`` (the builder that fills a body from its
    recipe maps a function path to its key the same way); this is the error of
    the sync."""
    try:
        return graph_sync.snippet_key(parts, name, known)
    except graph_sync.GraphSyncError as error:
        raise SyncError(str(error)) from error


def sync_file(
    path: Path,
    parts: tuple[str, ...],
    bytes_of: dict[str, dict[str, str]],
) -> int:
    document = ET.parse(path)
    known = set(bytes_of["x86_64"])
    changed = removed = 0
    for parent in document.getroot().iter():
        for child in list(parent):
            if child.tag == PROVENANCE_TAG:
                parent.remove(child)
                removed += 1
    for function in document.getroot().iter("Function"):
        codes = {c.get("name"): c for c in function if c.tag == "Code"}
        if "x86_64_linux" not in codes:
            continue
        key = snippet_key(parts, str(function.get("name")), known)
        for child in list(function):
            if child.tag == "ABISignature":
                # a register map is derived at the call boundary from the
                # Signature and the target (docs/32 5): never in a delta
                function.remove(child)
                removed += 1
            elif child.tag == "Code":
                for ref in list(child):
                    if (
                        ref.tag == f"{{{RXF}}}ref"
                        and word(ref.get("role")) == "AbiSignature"
                    ):
                        child.remove(ref)  # the map it names is derived
                        removed += 1
                if child.get("name") in GENERATED_CODE:
                    function.remove(child)
        linux = codes["x86_64_linux"]
        refresh(function, key, bytes_of)
        # BIOS: the same bytes under the BIOS target.
        code = copy.deepcopy(linux)
        code.set("name", BIOS)
        code.set("runtime_target", "@targets.x86_64_bios_sysv")
        function.append(code)
        # wasm32: the body the seed makes for that target.
        code = copy.deepcopy(linux)
        code.set("name", WASM)
        code.set("runtime_target", "@targets.wasm32_wasm_c")
        code.set("format", "@kinds.CodeFormat.Wasm")
        body = bytes_of["wasm32"][key]
        code.set("byte_count", str(len(body) // 2))
        code.set(DATA, body)
        function.append(code)
        set_provenance(function)
        changed += 1
    if changed or removed:
        document.write(path, encoding="UTF-8", xml_declaration=True)
    return changed


MACHINE_CODES = {
    "x86_64_linux": "x86_64",
    "x86_64_uefi": "x86_64",
    "aarch64_linux": "aarch64",
    "aarch64_uefi": "aarch64",
}


def refresh(
    function: ET.Element, key: str, bytes_of: dict[str, dict[str, str]]
) -> None:
    """The Code of every machine holds what native/ compiles to; UEFI is Linux's bytes."""
    for code in function:
        machine = (
            MACHINE_CODES.get(str(code.get("name"))) if code.tag == "Code" else None
        )
        if machine is None:
            continue
        body = bytes_of[machine][key]
        code.set(DATA, body)
        code.set("byte_count", str(len(body) // 2))


def write_provenance(path: Path) -> None:
    """One CompilerProvenance per machine in the targets module."""
    document = ET.parse(path)
    module = next(document.getroot().iter("Module"))
    major = native_build.SEED_MAJOR
    for machine in native_build.MACHINES:
        ET.SubElement(
            module,
            PROVENANCE_TAG,
            {
                "name": provenance_name(machine),
                "compiler_major": major,
                "recipe_digest": native_build.recipe_digest(machine),
            },
        )
    document.write(path, encoding="UTF-8", xml_declaration=True)


def leaf_paths(starter: Path, known: set[str]) -> dict[str, str]:
    """Where each native Function is, by snippet key: ``runtime_read`` is ``...runtime.read``."""
    found: dict[str, str] = {}
    for path in sorted(starter.rglob("*.rxfd")):
        parts = path.relative_to(starter).with_suffix("").parts
        for module_path, function in graph_sync.functions_in(
            ET.parse(path).getroot(), graph_sync.module_prefix(parts)
        ):
            if any(
                c.tag == "Code" and c.get("name") == "x86_64_linux" for c in function
            ):
                found[snippet_key(parts, str(function.get("name")), known)] = (
                    module_path
                )
    return found


def sync_bytecode(work: Path) -> int:
    """The program of every semantic function, from the graphs of the image."""
    programs = compose.programs(tree.load(work))
    done: list[str] = []
    for path in sorted(work.rglob("*.rxfd")):
        parts = path.relative_to(work).with_suffix("").parts
        document = ET.parse(path)
        written = graph_sync.write_bytecode(document, parts, programs)
        if written:
            graph_sync.drop_unused_aliases(document)
            document.write(path, encoding="UTF-8", xml_declaration=True)
            done += written
    if sorted(done) != sorted(programs):
        raise SyncError(f"no function for {sorted(set(programs) - set(done))}")
    return len(done)


def sync(starter: Path, check: bool = False) -> tuple[int, bool]:
    """Return (functions written, the starter is already what this makes)."""
    bytes_of = native_build.build()
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory) / "starter"
        shutil.copytree(starter, work)
        live_sync.write_live(work)
        paths = leaf_paths(work, set(bytes_of["x86_64"]))
        graph_sync.write_interpreter(
            work,
            bytes_of,
            {
                key: path
                for key, path in paths.items()
                if key in graph_header.leaves()
            },
        )
        total = 0
        for path in sorted(work.rglob("*.rxfd")):
            parts = path.relative_to(work).with_suffix("").parts
            total += sync_file(path, parts, bytes_of)
        write_provenance(work / "pymergetic" / "rxf" / "targets.rxfd")
        source_sync.write_sources(work, paths)
        boot_sync.write_codes(work)  # the glue's code, pinned to what make builds
        host_sync.write_hosts(work)  # the entry every host shape runs (docs/31 §5)
        # ``sync_file`` strips every CompilerProvenance object (the recipes are
        # regenerated, never patched in place), so the glue's recipe is written
        # back before anything loads the tree to link it.  The compiled bodies
        # move into their CodeBlob objects first: a newly authored Function
        # carries body references that only those blobs resolve.
        blob_sync.write_blobs(work)
        sync_bytecode(work)
        programs.strip(work)  # the built code is source and recipe, not stored
        image = tree.load(work)
        canonical = Path(directory) / "canonical"
        tree.write(image, canonical)
        names = sorted(
            p.relative_to(canonical).as_posix() for p in canonical.rglob("*.rxfd")
        )
        current = sorted(
            p.relative_to(starter).as_posix() for p in starter.rglob("*.rxfd")
        )
        same = names == current and all(
            (canonical / n).read_bytes() == (starter / n).read_bytes() for n in names
        )
        if not check and not same:
            for stale in set(current) - set(names):
                (starter / stale).unlink()
            for name in names:
                target = starter / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(canonical / name, target)
        return total, same


def main() -> None:
    arguments = sys.argv[1:]
    check = "--check" in arguments
    rest = [a for a in arguments if a != "--check"]
    starter = Path(rest[0]) if rest else ROOT / "starter"
    try:
        total, same = sync(starter, check)
    except SyncError as error:
        raise SystemExit(f"sync: {error}") from error
    print(f"{total} native functions; starter {'is' if same else 'was not'} up to date")
    if check and not same:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
