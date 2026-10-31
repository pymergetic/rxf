"""rxfb.hpp: the C++ face of the slim reader library (rxfb/cpp), proven against the C example.

The face is one header over the same C symbols. These tests prove three things:

1. it is freestanding: a program that touches the whole face, compiled with -std=c++20
   -ffreestanding -fno-exceptions -fno-rtti -Wall -Wextra -Werror for x86_64, aarch64, i386 and
   wasm32, needs no symbol but the rxfb_* ones of the C core (no operator new, no __cxa_*, no
   typeinfo, no libc), and it links with rxfb.c and nothing else;
2. it is the C core and nothing more: the C++ example (rxfb/cpp/example/rxfb_dump.cpp) prints,
   for the real v7 starter image, exactly what the C example (rxfb/example/rxfb_dump.c) prints,
   byte for byte: the image digest, the walk of every object (path, kind, size, payload SHA-256),
   objects in full, and the code story (function slot -> the bytes of the leaf);
3. it does not trust the image any more than the C core does: for damaged images the two
   programs answer the same statuses (stdout, stderr and exit code alike), and the C++ one is
   clean under the address and undefined-behaviour sanitizers.
"""

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

# The fixtures of test_rxfb: the starter image, its model and the small image with its damage.
from .test_rxfb import (  # noqa: F401  (pytest finds a fixture by name in this module)
    ALLOWED,
    INDEX_OFFSET,
    RUN,
    TARGETS,
    bridge,
    corrupted,
    dump,
    dump_sanitized,
    run_dump,
    starter_file,
    tiny,
    tiny_file,
    tool,
    world,
)

ROOT = Path(__file__).parents[1]
RXFB = ROOT / "rxfb"
CPP = RXFB / "cpp"
# The only symbols the face may leave undefined besides the C core's own. At -O0 a compiler may
# call the mem* helpers for a struct copy (the C standard grants it to a freestanding program).
COMPILER_HELPERS = {"memcpy", "memset", "memmove", "memcmp"}
FLAGS = ["-std=c++20", "-ffreestanding", "-fno-exceptions", "-fno-rtti"]
WARNINGS = ["-Wall", "-Wextra", "-Werror"]


@pytest.fixture(scope="module")
def c_dump(request) -> Path:
    """The C example, built by test_rxfb's fixture."""
    return request.getfixturevalue("dump")


@pytest.fixture(scope="module")
def c_dump_sanitized(request) -> Path:
    return request.getfixturevalue("dump_sanitized")


@pytest.fixture(scope="module")
def starter(request) -> Path:
    """The real v7 image: the starter, through the exporter."""
    return request.getfixturevalue("starter_file")


@pytest.fixture(scope="module")
def model(request):
    """The Python model of the starter (support.Bridge)."""
    return request.getfixturevalue("bridge")


@pytest.fixture(scope="module")
def small(request):
    """A small image with its arena: the shape and the damage."""
    return request.getfixturevalue("tiny")


@pytest.fixture
def small_file(request) -> Path:
    return request.getfixturevalue("tiny_file")


def undefined_symbols(obj: Path) -> set[str]:
    listing = subprocess.run(
        [tool("llvm-nm-18"), "-u", str(obj)], capture_output=True, text=True, check=True
    )
    return {line.split()[-1] for line in listing.stdout.splitlines() if line.strip()}


# ---- the face is freestanding ------------------------------------------------------------------


@pytest.mark.parametrize("optimization", ["-O0", "-O1", "-O2", "-Os"])
@pytest.mark.parametrize("target", TARGETS)
def test_the_face_needs_nothing_but_the_c_core(target, optimization, tmp_path):
    obj = tmp_path / "probe.o"
    compiled = subprocess.run(
        [
            tool("clang++-18"),
            f"--target={target}",
            *FLAGS,
            "-nostdlib",
            optimization,
            *WARNINGS,
            "-c",
            str(CPP / "test" / "probe.cpp"),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    outside = {
        symbol for symbol in undefined_symbols(obj) if not symbol.startswith("rxfb_")
    }
    allowed = ALLOWED | (COMPILER_HELPERS if optimization == "-O0" else set())
    assert outside <= allowed, outside - allowed


LINKS = (
    ("x86_64-linux-gnu", ["-static", "-Wl,-e,rxfb_probe"]),
    ("i386-linux-gnu", ["-static", "-fno-pic", "-Wl,-e,rxfb_probe"]),
    ("wasm32-unknown-unknown", ["-Wl,--no-entry", "-Wl,--export-all"]),
)


@pytest.mark.parametrize(("target", "linking"), LINKS)
def test_the_face_and_the_core_link_with_nothing_else(target, linking, tmp_path):
    """The whole face over rxfb.c is a complete program image: no libc, no C++ runtime."""
    objects = []
    for compiler, source, language in (
        ("clang++-18", CPP / "test" / "probe.cpp", FLAGS),
        ("clang-18", RXFB / "rxfb.c", ["-std=c11", "-ffreestanding"]),
    ):
        obj = tmp_path / f"{len(objects)}.o"
        subprocess.run(
            [
                tool(compiler),
                f"--target={target}",
                *language,
                "-O2",
                "-fno-pic" if target.startswith("i386") else "-O2",
                "-c",
                str(source),
                "-o",
                str(obj),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        objects.append(str(obj))
    linked = subprocess.run(
        [
            tool("clang++-18"),
            f"--target={target}",
            "-nostdlib",
            *linking,
            *objects,
            "-o",
            str(tmp_path / "probe.bin"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert linked.returncode == 0, linked.stderr


@pytest.mark.parametrize("target", TARGETS)
def test_the_header_is_included_without_the_c_core(target, tmp_path):
    """A translation unit that only includes the header compiles and leaves nothing undefined."""
    source = tmp_path / "include.cpp"
    source.write_text('#include "rxfb.hpp"\n')
    obj = tmp_path / "include.o"
    subprocess.run(
        [
            tool("clang++-18"),
            f"--target={target}",
            *FLAGS,
            *WARNINGS,
            f"-I{CPP}",
            "-c",
            str(source),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert not undefined_symbols(obj)


# ---- the example, built by the library's own CMake ---------------------------------------------


def cmake_options() -> list[str]:
    options = ["-DCMAKE_BUILD_TYPE=Release"]
    for variable, name in (
        ("CMAKE_C_COMPILER", "clang-18"),
        ("CMAKE_CXX_COMPILER", "clang++-18"),
    ):
        found = shutil.which(name)
        if found:
            options.append(f"-D{variable}={found}")
    return options


@pytest.fixture(scope="module")
def dump_cpp(tmp_path_factory) -> Path:
    """rxfb_dump_cpp, built by the library's own CMake target (with the probe object next to it)."""
    cmake = tool("cmake")
    build = tmp_path_factory.mktemp("rxfb_cpp_build")
    subprocess.run(
        [cmake, "-S", str(RXFB), "-B", str(build), *cmake_options()],
        capture_output=True,
        text=True,
        check=True,
    )
    built = subprocess.run(
        [cmake, "--build", str(build), "--target", "rxfb_dump_cpp", "rxfb_cpp_probe"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return build / "rxfb_dump_cpp"


def same_answer(c: Path, cpp: Path, image: Path, *arguments: str) -> str:
    """Runs both programs and demands one answer: stdout, stderr and the exit code."""
    expected = run_dump(c, image, *arguments)
    got = run_dump(cpp, image, *arguments)
    assert got.returncode == expected.returncode, (got.stderr, expected.stderr)
    assert got.stderr == expected.stderr
    assert got.stdout == expected.stdout
    return got.stdout


def test_the_image_digest_is_the_c_examples(c_dump, dump_cpp, starter, model):
    out = same_answer(c_dump, dump_cpp, starter)
    kind, size, handles, digest = out.strip().split("\t")
    assert (kind, int(size), int(handles)) == (
        "image",
        len(model.arena),
        model.count,
    )
    assert digest == model.digest()


@pytest.mark.slow
def test_every_object_of_the_starter_prints_as_the_c_examples(
    c_dump, dump_cpp, starter
):
    """Path, kind, payload size and payload SHA-256 of every object, byte for byte."""
    out = same_answer(c_dump, dump_cpp, starter, "walk")
    assert len(out.splitlines()) > 20000
    assert out.splitlines()[0].startswith("walk\t\t")  # the root's path is empty


def test_objects_print_in_full_as_the_c_examples(c_dump, dump_cpp, starter):
    paths = [
        "",
        "pymergetic",
        "pymergetic.rxf",
        "pymergetic.rxf.foundation",
        RUN,
        "pymergetic.rxf.foundation.live",
        "pymergetic.rxf.foundation.live.pump",
        "sys",
        "sys.specs",
        "sys.syms",
    ]
    out = same_answer(c_dump, dump_cpp, starter, "object", *paths)
    lines = out.splitlines()
    assert len(lines) == 4 * len(paths)
    # The interpreter holds the references the seats link by: its refs line is not empty.
    refs = [line for line in lines if line.startswith(f"refs\t{RUN}\t")]
    assert refs
    assert int(refs[0].split("\t")[2]) > 0


@pytest.mark.parametrize("machine", ["x86_64_linux", "aarch64_linux", "wasm32"])
def test_function_slots_print_as_the_c_examples(c_dump, dump_cpp, starter, machine):
    slots = [str(slot) for slot in (*range(1, 50), 51)]  # 50 is the operand's ref
    out = same_answer(c_dump, dump_cpp, starter, "code", machine, *slots)
    assert len(out.splitlines()) == len(slots)


@pytest.mark.slow
def test_slots_that_do_not_exist_are_refused_as_the_c_example_does(
    c_dump, dump_cpp, starter
):
    for slot, machine in ((0, "wasm32"), (50, "wasm32"), (1, "riscv64")):
        out = same_answer(c_dump, dump_cpp, starter, "code", machine, str(slot))
        assert out == ""


def test_a_path_that_names_nothing_is_refused_as_the_c_example_does(
    c_dump, dump_cpp, small_file
):
    for path in ("pkg.nothing", "pkg.leaf.below", "pkg..leaf", "pkg.leaf."):
        same_answer(c_dump, dump_cpp, small_file, "object", path)


def test_a_small_image_prints_as_the_c_example(c_dump, dump_cpp, small_file):
    assert same_answer(c_dump, dump_cpp, small_file, "walk")
    same_answer(c_dump, dump_cpp, small_file)
    out = same_answer(
        c_dump,
        dump_cpp,
        small_file,
        "object",
        "pkg",
        "pkg.leaf",
        "pkg.grüße",
        "pkg.inner",
    )
    assert (
        "children\tpkg\tleaf,grüße,,inner\t4" in out
    )  # an anonymous child is an empty name


def test_the_usage_and_unknown_commands_are_the_c_examples(
    c_dump, dump_cpp, small_file, tmp_path
):
    for arguments in (("nonsense",), ("code",)):
        same_answer(c_dump, dump_cpp, small_file, *arguments)
    for program in (c_dump, dump_cpp):
        usage = subprocess.run(
            [str(program)], capture_output=True, text=True, check=False
        )
        assert usage.returncode == 2
        assert usage.stderr.startswith("usage: rxfb_dump IMAGE")
    # an image that does not exist: the same perror text and code
    missing = tmp_path / "missing.rxfb"
    assert (
        run_dump(c_dump, missing).stderr.split(":")[-1]
        == run_dump(dump_cpp, missing).stderr.split(":")[-1]
    )


# ---- damaged images: the same statuses, and clean under the sanitizers -------------------------


def test_damaged_images_are_refused_as_the_c_example_refuses_them(
    c_dump, dump_cpp, small, tmp_path
):
    arena, image = small
    package = arena.find(arena.root_handle, "pkg")
    leaf = arena.find(package, "leaf")
    record, _ = arena.locate(leaf)
    cases = {
        "truncated": image[:-1],
        "no objects": image[:4096],
        "magic": b"RXFB" + image[4:],
        "version": corrupted(image, 4, "<I", 2),
        "committed past limit": corrupted(image, 8, "<Q", 1 << 40),
        "stale record": corrupted(image, record + 2, "<H", 77),
        "payload past the end": corrupted(image, record + 20, "<I", 1 << 30),
        "chain cycle": corrupted(image, record + 12, "<I", leaf),
        "name symbol outside": corrupted(image, record + 6, "<H", 60000),
        "parent outside": corrupted(image, record + 8, "<I", 0x7FFFF),
    }
    for name, data in cases.items():
        path = tmp_path / "damaged.rxfb"
        path.write_bytes(data)
        for arguments in (("walk",), ("object", "pkg.leaf", "pkg.inner.deep"), ()):
            try:
                same_answer(c_dump, dump_cpp, path, *arguments)
            except AssertionError as error:
                raise AssertionError(f"{name} {arguments}: {error}") from error


@pytest.fixture(scope="module")
def dump_cpp_sanitized(tmp_path_factory) -> Path:
    """rxfb_dump_cpp under AddressSanitizer and UndefinedBehaviorSanitizer."""
    clang, clangxx = tool("clang-18"), tool("clang++-18")
    build = tmp_path_factory.mktemp("rxfb_cpp_sanitized")
    flags = [
        "-O1",
        "-g",
        "-fsanitize=address,undefined",
        "-fno-sanitize-recover=undefined",
    ]
    objects = []
    for compiler, source, extra in (
        (clang, RXFB / "rxfb.c", ["-std=c11"]),
        (
            clangxx,
            CPP / "example" / "rxfb_dump.cpp",
            ["-std=c++20", "-fno-exceptions", "-fno-rtti"],
        ),
        (clangxx, RXFB / "example" / "sha256_engine.cpp", ["-std=c++20"]),
        (
            clangxx,
            ROOT / "src" / "pymergetic" / "rxftk" / "core" / "hash" / "sha256.cpp",
            ["-std=c++20"],
        ),
    ):
        obj = build / f"{len(objects)}.o"
        subprocess.run(
            [
                compiler,
                *flags,
                *extra,
                f"-I{ROOT / 'src'}",
                "-c",
                str(source),
                "-o",
                str(obj),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        objects.append(str(obj))
    binary = build / "rxfb_dump_cpp"
    subprocess.run(
        [clangxx, *flags, *objects, "-o", str(binary)],
        capture_output=True,
        text=True,
        check=True,
    )
    return binary


@pytest.mark.slow
def test_random_damage_reads_as_the_c_example_under_the_sanitizers(
    c_dump_sanitized, dump_cpp_sanitized, small, tmp_path
):
    """Flip bytes in a valid image and read all of it with both programs, both sanitized: the
    same answer every time, never a sanitizer error, a crash or a hang."""
    _, image = small
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "exitcode=77:detect_leaks=0"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:exitcode=78:print_stacktrace=1"
    generator = random.Random(20261101)
    path = tmp_path / "fuzzed.rxfb"
    refused = 0
    for _ in range(300):
        data = bytearray(image)
        for _ in range(generator.choice((1, 2, 4))):
            at = generator.randrange(32) if generator.random() < 0.33 else None
            if at is None:
                at = generator.randrange(INDEX_OFFSET, len(data))
            data[at] = generator.randrange(256)
        path.write_bytes(data)
        for arguments in (("walk",), ("object", "pkg.leaf", "pkg.inner.deep")):
            answers = [
                subprocess.run(
                    [str(program), str(path), *arguments],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=environment,
                    timeout=60,
                    check=False,
                )
                for program in (c_dump_sanitized, dump_cpp_sanitized)
            ]
            c, cpp = answers
            assert cpp.returncode in (0, 1), (cpp.returncode, cpp.stderr[-2000:])
            assert "Sanitizer" not in cpp.stderr and "runtime error" not in cpp.stderr
            assert (cpp.returncode, cpp.stdout, cpp.stderr) == (
                c.returncode,
                c.stdout,
                c.stderr,
            )
            refused += cpp.returncode == 1
    assert refused > 0  # the damage does land on structure: the refusals are exercised
