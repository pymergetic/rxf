"""The native snippets in the starter are what native/*.c compiles to."""

import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
RXF = "{urn:pymergetic:rxf}"

import native_build
import sync


def have_clang() -> bool:
    tools = (native_build.CLANG, native_build.LINKER, native_build.WASM_LINKER)
    if any(shutil.which(tool) is None for tool in tools):
        return False
    return native_build.clang_version() == native_build.PINNED


needs_clang = pytest.mark.skipif(
    not have_clang(),
    reason=f"needs {native_build.CLANG} {native_build.PINNED}, {native_build.LINKER} and {native_build.WASM_LINKER}",
)


def starter_code(known: set[str]) -> dict[tuple[str, str], str]:
    """``{(machine, snippet name): hex}`` as the starter says it, for every target."""
    found: dict[tuple[str, str], str] = {}
    for path in STARTER.rglob("*.rxfd"):
        parts = path.relative_to(STARTER).with_suffix("").parts
        for function in ET.parse(path).getroot().iter("Function"):
            for code in function.iter("Code"):
                if code.get("runtime_target") is None:
                    continue  # a program, not machine code
                machine = next(
                    m
                    for m in native_build.MACHINES
                    if code.get("runtime_target", "").split(".")[-1].startswith(m)
                )
                key = sync.snippet_key(parts, str(function.get("name")), known)
                data = code.get(RXF + "data")
                assert data is not None
                assert found.setdefault((machine, key), data) == data, (
                    f"{key}: the targets of one machine disagree"
                )
    return found


@needs_clang
def test_every_starter_snippet_is_what_the_sources_compile_to():
    built = native_build.build()
    wanted = {(m, n): h for m, fns in built.items() for n, h in fns.items()}
    have = starter_code({n for _, n in wanted})
    assert sorted(have) == sorted(wanted), "a snippet is missing or has no source"
    assert [k for k in wanted if have[k] != wanted[k]] == []


@needs_clang
def test_the_starter_is_what_the_sync_makes_of_the_sources():
    total, same = sync.sync(STARTER, check=True)
    assert (total, same) == (321, True)


def test_each_provenance_is_the_digest_of_the_recipe_of_its_machine():
    """No compiler: the recipe is data in native_build, and the starter records its digest."""
    root = ET.parse(STARTER / "pymergetic" / "rxf" / "targets.rxfd").getroot()
    recorded = {
        p.get("name"): (p.get("compiler_major"), p.get("recipe_digest"))
        for p in root.iter("CompilerProvenance")
    }
    major = native_build.PINNED.split(".")[0]
    assert recorded == {
        sync.provenance_name(m): (major, native_build.recipe_digest(m))
        for m in native_build.MACHINES
    }


def test_each_code_names_the_provenance_of_its_machine():
    for path in sorted(STARTER.rglob("*.rxfd")):
        for code in ET.parse(path).getroot().iter("Code"):
            refs = {
                sync.word(r.get("role")): sync.word(r.get("target"))
                for r in code.findall(f"{RXF}ref")
            }
            if code.get("runtime_target") is None:  # a program has no compiler
                assert "Provenance" not in refs, path
                continue
            machine = sync.machine_of(code.get("runtime_target"))
            assert refs["Provenance"] == sync.provenance_name(machine), path


@pytest.mark.skipif(
    shutil.which("clang-format-18") is None, reason="needs clang-format-18"
)
def test_the_c_sources_are_formatted():
    sources = sorted(str(p) for p in (ROOT / "native").glob("*.[ch]"))
    done = subprocess.run(
        ["clang-format-18", "--dry-run", "-Werror", *sources],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr[:2000]
