"""rxfb, the Rust face (rxfb/rust: rxfb-sys + rxfb), proven against the C example.

The Rust crates declare and wrap the one C core (rxfb/rxfb.c, compiled by rxfb-sys's build.rs);
nothing of the reader is written again. The prove is the roadmap's done-when for L1d:

1. the Rust example (rxfb/rust/rxfb/examples/dump.rs) maps the real v7 starter image and prints
   what rxfb/example/rxfb_dump.c prints, byte for byte - the image digest, every object of the
   walk (path, kind, size, payload digest), eight objects in full, the function slots of three
   machines - and refuses the same way (stdout, stderr and exit code) on every question the
   library answers with a status, and on damaged images;
2. the workspace is lint clean (clippy with -D warnings over all targets, rustfmt --check) and its
   own tests pass;
3. the safe crate is `#![no_std]` with no dependency but rxfb-sys: it builds for the freestanding
   targets that are installed (wasm32-unknown-unknown, x86_64-unknown-none).

The only reason to skip is a host without cargo.
"""

# The C example's fixtures (the built rxfb_dump, the starter image, the tiny image) are the same
# fixtures here: they are imported, and a test names them as arguments (pytest's fixture idiom).
# ruff: noqa: F811

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic import rxfb
from pymergetic.rxftk.core.arena import INDEX_OFFSET

from .test_rxfb import (  # noqa: F401
    OBJECTS,
    RUN,
    bridge,
    dump,
    starter_file,
    tiny,
    world,
)

ROOT = Path(__file__).parents[1]
WORKSPACE = ROOT / "rxfb" / "rust"
MACHINES = ("x86_64_linux", "aarch64_linux", "wasm32")
# The interpreter's leaf slots (1..49 in the starter); 0 and 50 are not leaves.
SLOTS = tuple(range(1, 50))
FREESTANDING = ("wasm32-unknown-unknown", "x86_64-unknown-none")


def cargo_binary() -> str:
    found = shutil.which("cargo") or str(Path.home() / ".cargo" / "bin" / "cargo")
    if not Path(found).exists():
        pytest.skip(
            "cargo is not installed on this host (no cargo on PATH or ~/.cargo/bin)"
        )
    return found


def cargo(
    *arguments: str, target_dir: Path, check: bool = True
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["CARGO_TARGET_DIR"] = str(target_dir)
    environment["PATH"] = f"{Path(cargo_binary()).parent}:{environment['PATH']}"
    result = subprocess.run(
        [cargo_binary(), *arguments],
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
        env=environment,
        timeout=900,
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def target_dir(tmp_path_factory) -> Path:
    cargo_binary()
    return tmp_path_factory.mktemp("rxfb_cargo")


@pytest.fixture(scope="module")
def rust_dump(target_dir) -> Path:
    """The Rust example, built from the workspace as it is (Cargo.lock honoured)."""
    cargo("build", "--locked", "--release", "--example", "dump", target_dir=target_dir)
    return target_dir / "release" / "examples" / "dump"


Answer = subprocess.CompletedProcess[bytes]


def ask(binary: Path, image: Path, *arguments: str) -> Answer:
    """One run, as bytes: a damaged image can name an object in no encoding at all."""
    return subprocess.run(
        [str(binary), str(image), *arguments],
        capture_output=True,
        timeout=120,
        check=False,
    )


def both(
    dump: Path, rust_dump: Path, image: Path, *arguments: str
) -> tuple[Answer, Answer]:
    return ask(dump, image, *arguments), ask(rust_dump, image, *arguments)


def same(c: Answer, rust: Answer):
    assert rust.returncode == c.returncode, (c.stderr, rust.stderr)
    assert rust.stderr == c.stderr
    assert rust.stdout == c.stdout


# ---- the example reads the real image as the C example does -------------------------------------


@pytest.mark.slow
def test_the_image_line_is_the_c_examples(dump, rust_dump, starter_file, bridge):
    c, rust = both(dump, rust_dump, starter_file)
    assert c.returncode == 0, c.stderr
    same(c, rust)
    assert rust.stdout.strip().split(b"\t")[3].decode() == bridge.digest()


@pytest.mark.slow
def test_every_object_of_the_walk_is_the_c_examples(dump, rust_dump, starter_file):
    c, rust = both(dump, rust_dump, starter_file, "walk")
    assert c.returncode == 0, c.stderr
    assert len(c.stdout.splitlines()) > 20000
    same(c, rust)


@pytest.mark.slow
def test_objects_in_full_are_the_c_examples(dump, rust_dump, starter_file):
    c, rust = both(dump, rust_dump, starter_file, "object", *OBJECTS)
    assert c.returncode == 0, c.stderr
    assert c.stdout.count(b"\nrefs\t") >= len(OBJECTS)
    same(c, rust)


@pytest.mark.slow
def test_the_tables_are_the_c_examples(dump, rust_dump, starter_file):
    """`table NAME`: the rows read straight off the table object of sys/relations (the one-pass
    story of `rxfb_table`) - the Rust face's `table()` prints what the C face's does, byte for
    byte."""
    image = rxfb.attach(starter_file.read_bytes())
    names = [
        child.name
        for child in image.lookup("sys.relations").children()
        if child.name is not None
    ]
    assert len(names) > 1000
    sample = names[:: max(1, len(names) // 7)][:8]
    c, rust = both(dump, rust_dump, starter_file, "table", *sample)
    assert c.returncode == 0, c.stderr
    same(c, rust)


@pytest.mark.slow
@pytest.mark.parametrize("machine", MACHINES)
def test_the_function_slots_are_the_c_examples(dump, rust_dump, starter_file, machine):
    c, rust = both(dump, rust_dump, starter_file, "code", machine, *map(str, SLOTS))
    assert c.returncode == 0, c.stderr
    assert len(c.stdout.splitlines()) == len(SLOTS)
    same(c, rust)


@pytest.mark.slow
def test_the_refusals_are_the_c_examples(dump, rust_dump, starter_file):
    questions = (
        ("code", "wasm32", "0"),
        ("code", "wasm32", "50"),
        ("code", "riscv64", "1"),
        ("object", f"{RUN}.nothing_here"),
        ("object", "pymergetic..rxf"),
        ("object", "nothing"),
        ("object", ""),
        ("table", "no.such.object"),
        ("table", ""),
        ("frobnicate",),
        ("code",),
    )
    refused = 0
    for arguments in questions:
        c, rust = both(dump, rust_dump, starter_file, *arguments)
        same(c, rust)
        refused += c.returncode != 0
    assert (
        refused >= len(questions) - 2
    )  # the root and an empty `code` are the only quiet ones


# ---- the example does not trust the image either ------------------------------------------------


def test_a_truncated_or_foreign_file_is_refused_as_c_refuses_it(
    dump, rust_dump, tiny, tmp_path
):
    _, image = tiny
    path = tmp_path / "bad.rxfb"
    for name, data in (
        ("empty", b""),
        ("text", b"not an image at all" * 500),
        ("cut", image[: len(image) // 2]),
        ("super", image[:INDEX_OFFSET]),
        ("magic", b"RXFB" + image[4:]),
    ):
        path.write_bytes(data)
        for arguments in ((), ("walk",)):
            c, rust = both(dump, rust_dump, path, *arguments)
            assert c.returncode != 0, name
            same(c, rust)


def test_random_damage_answers_as_the_c_example_does(dump, rust_dump, tiny, tmp_path):
    """The same damaged bytes, the same answer from both faces: a status or a walk, never a
    difference (and, on the Rust side, never a panic: that would be exit 101)."""
    _, image = tiny
    generator = random.Random(20261031)
    path = tmp_path / "fuzzed.rxfb"
    refused = 0
    for _ in range(150):
        data = bytearray(image)
        for _ in range(generator.choice((1, 2, 4))):
            at = generator.randrange(32) if generator.random() < 0.33 else None
            if at is None:
                at = generator.randrange(INDEX_OFFSET, len(data))
            data[at] = generator.randrange(256)
        path.write_bytes(data)
        for arguments in (("walk",), ("object", "pkg.leaf", "pkg.inner.deep")):
            c, rust = both(dump, rust_dump, path, *arguments)
            assert rust.returncode != 101, rust.stderr
            same(c, rust)
            refused += c.returncode == 1
    assert refused > 0  # the damage does land on structure: the refusals are exercised


# ---- the workspace: tests, lints, freestanding --------------------------------------------------


def test_the_crates_own_tests_pass(target_dir):
    result = cargo("test", "--locked", "--workspace", target_dir=target_dir)
    assert "6 passed" in result.stdout


def test_clippy_is_clean(target_dir):
    cargo(
        "clippy",
        "--locked",
        "--workspace",
        "--all-targets",
        "--",
        "-D",
        "warnings",
        target_dir=target_dir,
    )


def test_rustfmt_is_clean(target_dir):
    cargo("fmt", "--all", "--check", target_dir=target_dir)


def test_the_safe_crate_depends_on_nothing_but_the_sys_crate(target_dir):
    tree = cargo(
        "tree",
        "--locked",
        "-p",
        "rxfb",
        "-e",
        "normal",
        "--prefix",
        "none",
        target_dir=target_dir,
    )
    assert [line.split()[0] for line in tree.stdout.splitlines()] == [
        "rxfb",
        "rxfb-sys",
    ]


def installed_targets() -> set[str]:
    cargo_binary()
    rustc = Path(cargo_binary()).with_name("rustc")
    sysroot = subprocess.run(
        [str(rustc), "--print", "sysroot"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {entry.name for entry in (Path(sysroot) / "lib" / "rustlib").iterdir()}


@pytest.mark.parametrize("target", FREESTANDING)
def test_the_safe_crate_builds_for_a_target_without_std(target, target_dir):
    """`#![no_std]` is proven by a target that has no std: the C core, the bindings and the safe
    wrappers all build there."""
    if target not in installed_targets():
        pytest.skip(
            f"the rust target {target} is not installed (rustup target add {target})"
        )
    cargo(
        "build",
        "--locked",
        "--release",
        "-p",
        "rxfb",
        "--target",
        target,
        target_dir=target_dir,
    )
    assert (target_dir / target / "release" / "librxfb.rlib").exists()
