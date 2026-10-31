"""rxfb: the slim reader library (rxfb/), proven against the engine and the Python model.

The library is one freestanding C translation unit. These tests prove four things:

1. it is freestanding: compiled with -ffreestanding -nostdlib -Wall -Wextra -Werror for x86_64,
   aarch64, i386 and wasm32 it needs no symbol of a libc and holds no writable data (no state, no
   allocation);
2. its layout header is the engine's: rxfb/rxfb_layout.h against ground.h, field by field, in C
   and in C++, on every target;
3. it reads a real v7 image: the C example (rxfb/example/rxfb_dump.c) maps the image the starter
   exports and its output is compared byte for byte against the Python model (Bridge, digest_of)
   and the engine (ObjectArena): every object of the tree, a few objects in full, and the code
   story (function slot -> the bytes of the leaf);
4. it does not trust the image: a truncated, mis-versioned or corrupted image is refused with a
   status, never a read outside the bytes and never a hang.
"""

import hashlib
import os
import random
import shutil
import struct
import subprocess
from pathlib import Path

import compose
import programs
import pytest
import wasm_table

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    INDEX_OFFSET,
    INDEX_SELF,
    MODULE_SIZE,
    OBJ_BLOB,
    OBJ_MODULE,
    OBJECT_HEADER_SIZE,
    ObjectArena,
)
from pymergetic.rxftk.core.object import Image

from .snippets import STARTER
from .support import Bridge

ROOT = Path(__file__).parents[1]
RXFB = ROOT / "rxfb"
TARGETS = (
    "x86_64-linux-gnu",
    "aarch64-linux-gnu",
    "i386-linux-gnu",
    "wasm32-unknown-unknown",
)
# No libc symbol at all. The two others are linker symbols of the i386 PIC and wasm32 ABIs, not
# dependencies of the code (tests/test_arena_freestanding.py allows them too).
ALLOWED = {"_GLOBAL_OFFSET_TABLE_", "__stack_pointer"}
RUN = "pymergetic.rxf.foundation.graph.run"
STATUS_BAD_IMAGE = 1
STATUS_BAD_HANDLE = 2
STATUS_BAD_NAME = 3
STATUS_NOT_FOUND = 6


def tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        pytest.skip(f"{name} is not installed")
    return found


# ---- the library is freestanding ---------------------------------------------------------------


@pytest.mark.parametrize("optimization", ["-O0", "-O2"])
@pytest.mark.parametrize("target", TARGETS)
def test_the_core_is_freestanding(target, optimization, tmp_path):
    compiler, nm = tool("clang-18"), tool("llvm-nm-18")
    obj = tmp_path / "rxfb.o"
    compiled = subprocess.run(
        [
            compiler,
            f"--target={target}",
            "-std=c11",
            "-ffreestanding",
            "-nostdlib",
            optimization,
            "-Wall",
            "-Wextra",
            "-Werror",
            "-c",
            str(RXFB / "rxfb.c"),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr
    listing = subprocess.run(
        [nm, "-u", str(obj)], capture_output=True, text=True, check=True
    )
    undefined = {
        line.split()[-1] for line in listing.stdout.splitlines() if line.strip()
    }
    assert undefined <= ALLOWED, undefined - ALLOWED


@pytest.mark.parametrize(
    "target", ["x86_64-linux-gnu", "aarch64-linux-gnu", "i386-linux-gnu"]
)
def test_the_core_holds_no_state(target, tmp_path):
    """No .data and no .bss: nothing is allocated and nothing is global."""
    compiler, size = tool("clang-18"), tool("llvm-size-18")
    obj = tmp_path / "rxfb.o"
    subprocess.run(
        [
            compiler,
            f"--target={target}",
            "-std=c11",
            "-ffreestanding",
            "-nostdlib",
            "-O2",
            "-c",
            str(RXFB / "rxfb.c"),
            "-o",
            str(obj),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    report = subprocess.run(
        [size, str(obj)], capture_output=True, text=True, check=True
    )
    text, data, bss = report.stdout.splitlines()[1].split()[:3]
    assert (int(data), int(bss)) == (0, 0), report.stdout
    assert int(text) > 0


LINKS = (
    ("x86_64-linux-gnu", ["-static", "-Wl,-e,rxfb_attach"]),
    ("i386-linux-gnu", ["-static", "-fno-pic", "-Wl,-e,rxfb_attach"]),
    ("wasm32-unknown-unknown", ["-Wl,--no-entry", "-Wl,--export-all"]),
)


@pytest.mark.parametrize(("target", "linking"), LINKS)
def test_the_core_links_with_nothing_else(target, linking, tmp_path):
    """The core alone is a complete program image: it links with no libc, no startup code."""
    result = subprocess.run(
        [
            tool("clang-18"),
            f"--target={target}",
            "-std=c11",
            "-ffreestanding",
            "-nostdlib",
            "-O2",
            *linking,
            str(RXFB / "rxfb.c"),
            "-o",
            str(tmp_path / "rxfb.bin"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---- the layout is the engine's -----------------------------------------------------------------


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize(
    ("compiler", "language"),
    [("clang-18", ["-std=c11"]), ("clang++-18", ["-std=c++20", "-x", "c++"])],
)
def test_the_layout_is_the_engines(target, compiler, language):
    result = subprocess.run(
        [
            tool(compiler),
            f"--target={target}",
            *language,
            "-ffreestanding",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            f"-I{ROOT / 'src'}",
            str(RXFB / "test" / "layout_pins.c"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_api_header_compiles_as_cpp():
    """The C++ face (a later lane) starts from the same header."""
    result = subprocess.run(
        [
            tool("clang++-18"),
            "-std=c++20",
            "-x",
            "c++",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fsyntax-only",
            str(RXFB / "rxfb.h"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


# ---- the example: build it, map an image, read it ----------------------------------------------


@pytest.fixture(scope="module")
def dump(tmp_path_factory) -> Path:
    """rxfb_dump, built by the library's own CMake target."""
    cmake = tool("cmake")
    build = tmp_path_factory.mktemp("rxfb_build")
    options = ["-DCMAKE_BUILD_TYPE=Release"]
    for variable, name in (
        ("CMAKE_C_COMPILER", "clang-18"),
        ("CMAKE_CXX_COMPILER", "clang++-18"),
    ):
        found = shutil.which(name)
        if found:
            options.append(f"-D{variable}={found}")
    subprocess.run(
        [cmake, "-S", str(RXFB), "-B", str(build), *options],
        capture_output=True,
        text=True,
        check=True,
    )
    built = subprocess.run(
        [cmake, "--build", str(build), "--target", "rxfb", "rxfb_dump"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return build / "rxfb_dump"


def run_dump(
    dump: Path, image: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(dump), str(image), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def preorder(arena: ObjectArena) -> list[int]:
    """Every handle, the tree in chain order: a node, then its children (the engine's walk)."""
    found: list[int] = []
    stack = [arena.root_handle]
    while stack:
        handle = stack.pop()
        found.append(handle)
        if handle & 0xFFFFF == INDEX_SELF:
            continue  # the object index: its payload is a table, not a chain
        stack.extend(reversed(arena.children(handle)))
    return found


def walk_lines(arena: ObjectArena) -> list[str]:
    """What `rxfb_dump walk` must print, from the engine's own reader."""
    lines = []
    for handle in preorder(arena):
        kind, *_ = arena.load(handle)
        payload = arena.payload(handle)
        lines.append(
            f"walk\t{arena.path_of(handle)}\t{kind}\t{len(payload)}\t{sha(payload)}"
        )
    return lines


# ---- a small image, for the shape and the refusals ---------------------------------------------


@pytest.fixture(scope="module")
def tiny() -> tuple[ObjectArena, bytes]:
    arena = ObjectArena.create(1 << 20)
    arena.bootstrap("tiny")
    package = arena.new_object(arena.root_handle, "pkg", OBJ_MODULE, MODULE_SIZE)
    leaf = arena.new_object(package, "leaf", OBJ_BLOB, 16)
    arena.set_payload(leaf, bytes(range(16)))
    arena.new_object(package, "grüße", OBJ_BLOB, 8)
    arena.new_object(
        package, None, OBJ_BLOB, 8
    )  # anonymous: no path segment of its own
    inner = arena.new_object(package, "inner", OBJ_MODULE, MODULE_SIZE)
    arena.set_payload(arena.new_object(inner, "deep", OBJ_BLOB, 8), b"deepdeep")
    return arena, arena.to_bytes()


@pytest.fixture
def tiny_file(tiny, tmp_path) -> Path:
    path = tmp_path / "tiny.rxfb"
    path.write_bytes(tiny[1])
    return path


def test_the_tree_of_a_small_image_reads_as_the_engines(dump, tiny, tiny_file):
    arena, _ = tiny
    result = run_dump(dump, tiny_file, "walk")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == walk_lines(arena)


def test_an_object_reads_by_its_path(dump, tiny, tiny_file):
    arena, image = tiny
    package = arena.find(arena.root_handle, "pkg")
    leaf = arena.find(package, "leaf")
    result = run_dump(
        dump, tiny_file, "object", "pkg.leaf", "pkg.grüße", "pkg.inner.deep"
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    payload = arena.payload(leaf)
    assert lines[0] == (
        f"object\tpkg.leaf\t{arena.load(leaf)[0]}\t{len(payload)}\t{payload.hex()}\t{sha(payload)}"
    )
    # The bootstrap's semantic row is the default one (untyped, all zero); a bootstrapped image
    # has no sys/relations, so no refs, and a leaf has no children.
    kind_of_type, flags, index, generation = arena.spec(leaf)
    assert kind_of_type == HANDLE32_NONE
    assert lines[1] == f"spec\tpkg.leaf\t\t{flags}\t{index}\t{generation}"
    assert lines[2] == "children\tpkg.leaf\t\t0"
    assert lines[3] == "refs\tpkg.leaf\t0"
    assert any(line.startswith("object\tpkg.grüße\t") for line in lines)
    assert any(line.startswith("object\tpkg.inner.deep\t") for line in lines)
    # The image digest is the hash of the bytes the library was handed.
    whole = run_dump(dump, tiny_file)
    assert whole.stdout.split("\t")[3].strip() == sha(image)


def test_a_path_that_names_nothing_is_not_found(dump, tiny_file):
    for path, status in (
        ("pkg.nothing", STATUS_NOT_FOUND),
        ("pkg.leaf.below", STATUS_NOT_FOUND),  # a leaf has no children
        ("pkg..leaf", STATUS_BAD_NAME),
        ("pkg.leaf.", STATUS_BAD_NAME),
    ):
        result = run_dump(dump, tiny_file, "object", path)
        assert result.returncode == 1, path
        assert result.stderr.strip().endswith(f"status {status}"), (path, result.stderr)


def corrupted(image: bytes, at: int, fmt: str, value: int) -> bytes:
    patched = bytearray(image)
    struct.pack_into(fmt, patched, at, value)
    return bytes(patched)


def test_a_damaged_image_is_refused_not_trusted(dump, tiny, tmp_path):
    _, image = tiny
    committed = struct.unpack_from("<Q", image, 8)[0]
    capacity = struct.unpack_from("<I", image, INDEX_OFFSET + OBJECT_HEADER_SIZE + 4)[0]
    cases = {
        "truncated": image[:-1],
        "no objects": image[:4096],
        "magic": b"RXFB" + image[4:],
        "version": corrupted(image, 4, "<I", 2),
        "committed past limit": corrupted(image, 8, "<Q", (1 << 40)),
        "committed below the superblock": corrupted(image, 8, "<Q", 16),
        "index count over capacity": corrupted(
            image, INDEX_OFFSET + OBJECT_HEADER_SIZE, "<I", capacity + 1
        ),
        "index table outside the objects": corrupted(
            image, INDEX_OFFSET + OBJECT_HEADER_SIZE + 4, "<I", 1 << 19
        ),
    }
    assert committed < len(image)
    for name, data in cases.items():
        path = tmp_path / "damaged.rxfb"
        path.write_bytes(data)
        result = run_dump(dump, path, "walk")
        assert result.returncode == 1, name
        assert "attach: status 1" in result.stderr, (name, result.stderr)


def test_a_damaged_record_is_refused_not_followed(dump, tiny, tmp_path):
    arena, image = tiny
    package = arena.find(arena.root_handle, "pkg")
    leaf = arena.find(package, "leaf")
    record, _ = arena.locate(leaf)
    cases = {
        # the record's generation is not the handle's: a stale handle
        "stale record": (corrupted(image, record + 2, "<H", 77), STATUS_BAD_HANDLE),
        # a payload that runs past the committed objects
        "payload past the end": (
            corrupted(image, record + 20, "<I", 1 << 30),
            STATUS_BAD_IMAGE,
        ),
        # a sibling chain that loops back on itself
        "chain cycle": (corrupted(image, record + 12, "<I", leaf), STATUS_BAD_IMAGE),
        # a record outside the objects
        "slot outside": (
            corrupted(
                image,
                INDEX_OFFSET + OBJECT_HEADER_SIZE + 8 + 16 * (leaf & 0xFFFFF) + 8,
                "<Q",
                len(image) + 4096,
            ),
            STATUS_BAD_HANDLE,
        ),
    }
    for name, (data, status) in cases.items():
        path = tmp_path / "damaged.rxfb"
        path.write_bytes(data)
        result = run_dump(dump, path, "walk")
        assert result.returncode == 1, name
        assert result.stderr.strip().endswith(f"status {status}"), (name, result.stderr)


@pytest.fixture(scope="module")
def dump_sanitized(tmp_path_factory) -> Path:
    """rxfb_dump under AddressSanitizer and UndefinedBehaviorSanitizer: a read outside the image,
    or an unaligned or overflowing one, is an error instead of luck."""
    clang, clangxx = tool("clang-18"), tool("clang++-18")
    build = tmp_path_factory.mktemp("rxfb_sanitized")
    flags = [
        "-O1",
        "-g",
        "-fsanitize=address,undefined",
        "-fno-sanitize-recover=undefined",
    ]
    objects = []
    for compiler, source, extra in (
        (clang, RXFB / "rxfb.c", ["-std=c11"]),
        (clang, RXFB / "example" / "rxfb_dump.c", ["-std=c11"]),
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
    binary = build / "rxfb_dump"
    subprocess.run(
        [clangxx, *flags, *objects, "-o", str(binary)],
        capture_output=True,
        text=True,
        check=True,
    )
    return binary


@pytest.mark.slow
def test_random_damage_never_reads_outside_the_image(dump_sanitized, tiny, tmp_path):
    """Flip bytes in the superblock and the objects of a valid image and read all of it: every run ends in a status (0 or a
    refusal), never a sanitizer error, a crash or a hang."""
    _, image = tiny
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "exitcode=77:detect_leaks=0"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:exitcode=78:print_stacktrace=1"
    generator = random.Random(20261031)
    path = tmp_path / "fuzzed.rxfb"
    refused = 0
    for _ in range(400):
        data = bytearray(image)
        for _ in range(generator.choice((1, 2, 4))):
            # a third of the damage lands on the superblock's fields, the rest on the objects
            at = generator.randrange(32) if generator.random() < 0.33 else None
            if at is None:
                at = generator.randrange(INDEX_OFFSET, len(data))
            data[at] = generator.randrange(256)
        path.write_bytes(data)
        for arguments in (("walk",), ("object", "pkg.leaf", "pkg.inner.deep")):
            result = subprocess.run(
                [str(dump_sanitized), str(path), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=environment,
                timeout=60,
                check=False,
            )
            assert result.returncode in (0, 1), (
                result.returncode,
                result.stderr[-2000:],
            )
            assert (
                "Sanitizer" not in result.stderr
                and "runtime error" not in result.stderr
            )
            refused += result.returncode == 1
    assert (
        refused > 0
    )  # the damage does land on structure sometimes: the refusals are exercised


@pytest.mark.slow
def test_the_starter_reads_clean_under_the_sanitizers(dump_sanitized, starter_file):
    environment = dict(os.environ)
    environment["ASAN_OPTIONS"] = "exitcode=77:detect_leaks=0"
    environment["UBSAN_OPTIONS"] = "halt_on_error=1:exitcode=78"
    result = subprocess.run(
        [str(dump_sanitized), str(starter_file), "walk"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert len(result.stdout.splitlines()) > 20000


# ---- the real image: the starter, through the exporter ------------------------------------------


@pytest.fixture(scope="module")
def world() -> Image:
    return programs.filled(tree.load(Path(STARTER)))


@pytest.fixture(scope="module")
def bridge(world) -> Bridge:
    return Bridge(world)


@pytest.fixture(scope="module")
def starter_file(bridge, tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("rxfb_image") / "starter.rxfb"
    path.write_bytes(bridge.arena)
    return path


@pytest.fixture(scope="module")
def engine(bridge) -> ObjectArena:
    return ObjectArena.attach(bridge.arena)


def padded(value: bytes) -> bytes:
    return value + bytes(-len(value) % 8)


def arena_payload(
    bridge: Bridge, engine: ObjectArena, world: Image, path: str
) -> bytes:
    """The payload the arena carries for a path, from the world model: its value, remapped by the
    bridge, padded to the 8-byte slot, behind the chain head when the object is a module."""
    source = 0 if path == "" else world.resolve(path)
    assert source is not None, path
    handle = bridge.handle(source)
    value = padded(bridge.value(source))
    if engine.load(handle)[0] != OBJ_MODULE:
        return value
    children = engine.children(handle)
    head = struct.pack("<II", children[0] if children else HANDLE32_NONE, len(children))
    return head + value


@pytest.mark.slow
def test_the_image_digest_is_the_models(dump, bridge, starter_file):
    result = run_dump(dump, starter_file)
    assert result.returncode == 0, result.stderr
    kind, size, handles, digest = result.stdout.strip().split("\t")
    assert kind == "image"
    assert int(size) == len(bridge.arena)
    assert int(handles) == bridge.count
    assert (
        digest == bridge.digest()
    )  # support.digest_of: the hash of the canonical bytes


@pytest.mark.slow
def test_every_object_of_the_starter_reads_as_the_engines(dump, engine, starter_file):
    result = run_dump(dump, starter_file, "walk")
    assert result.returncode == 0, result.stderr
    expected = walk_lines(engine)
    assert len(expected) == engine.handle_count
    assert result.stdout.splitlines() == expected


OBJECTS = (
    "",
    "pymergetic.rxf.application.main",
    RUN,
    "pymergetic.rxf.roles.call.Leaf",
    "pymergetic.rxf.numeric.wrapping.add_uint64_t",
    "pymergetic.rxf.numeric.wrapping.add_uint64_t.x86_64_linux",
    "pymergetic.rxf.numeric.wrapping.add_uint64_t.body_x86_64",
    "pymergetic.rxf.numeric.wrapping.add_uint64_t.body_wasm32",
)


def object_lines(
    bridge: Bridge, engine: ObjectArena, world: Image, path: str
) -> list[str]:
    """What `rxfb_dump object PATH` must print, from the model: the payload as the world's value,
    the semantic row from the world's own fields, the children in the world's order and the
    references with their targets and roles spelled as paths."""
    source = 0 if path == "" else world.resolve(path)
    assert source is not None
    info = world.get(source)
    handle = bridge.handle(source)
    payload = arena_payload(bridge, engine, world, path)
    kind = engine.load(handle)[0]
    lines = [f"object\t{path}\t{kind}\t{len(payload)}\t{payload.hex()}\t{sha(payload)}"]

    type_path = "" if info["type"] == (1 << 64) - 1 else world.path(info["type"])
    flags = info["kind"] | (info["state"] << 8) | (info["owner_kind"] << 14)
    lines.append(
        f"spec\t{path}\t{type_path}\t{flags}\t{info['index']}\t{info['generation']}"
    )

    if path == "":
        # The root of the arena also holds what the arena adds to the world: sys/ and the
        # founding core (docs/32 2, docs/36), in the engine's chain order.
        names = [
            engine.symbol(engine.load(child)[3]) for child in engine.children(handle)
        ]
    else:
        names = [world.get(child)["name"] for child in world.children(source)]
    lines.append(f"children\t{path}\t{','.join(names[:8])}\t{len(names)}")

    refs = info["refs"]
    spelled = []
    for target, role, index, kind_of_ref, _binding in refs[:4]:
        role_path = "" if role is None else world.path(role)
        spelled.append(f"{world.path(target)}:{role_path}:{kind_of_ref}:{index}")
    lines.append("\t".join([f"refs\t{path}\t{len(refs)}", *spelled]))
    return lines


@pytest.mark.slow
def test_objects_read_in_full_as_the_python_model_has_them(
    dump, bridge, engine, starter_file, world
):
    result = run_dump(dump, starter_file, "object", *OBJECTS)
    assert result.returncode == 0, result.stderr
    expected = []
    for path in OBJECTS:
        expected += object_lines(bridge, engine, world, path)
    assert result.stdout.splitlines() == expected


def leaf_codes(world: Image, machine: str) -> dict[int, tuple[str, bytes]]:
    """{leaf id: (path of the function, its bytes for the machine)} - the seats' own walk
    (tools/native/wasm_table.py leaf_bodies) with the machine as a parameter."""
    composer = compose.Composer(world)
    runner = composer.target(RUN)
    found: dict[int, tuple[str, bytes]] = {}
    for identifier, target in composer.references(runner, "Leaf"):
        for child in composer.children(target, "Code"):
            if world.path(child).rsplit(".", 1)[-1] != machine:
                continue
            fields = dict(composer.fields(child))
            body = world.resolve(fields["body"])
            assert body is not None
            data = bytes.fromhex(programs.fields_of(world, body)["data"])
            (entry,) = struct.unpack_from("<Q", world.get(child)["payload"], 40)
            (declared,) = struct.unpack_from("<Q", world.get(body)["payload"], 0)
            # The blob's own header says how many bytes the code is. The data a build fills in
            # can run ahead of that pin while a starter regeneration is pending; the reader
            # follows the header, as the layout says.
            found[identifier] = (world.path(target), data[entry:declared])
    return found


@pytest.mark.slow
@pytest.mark.parametrize("machine", ["x86_64_linux", "aarch64_linux", "wasm32"])
def test_a_function_slot_resolves_to_the_leafs_bytes(
    dump, starter_file, world, machine
):
    codes = leaf_codes(world, machine)
    assert len(codes) == 49  # every leaf of the interpreter has code for the machine
    slots = sorted(codes)
    result = run_dump(dump, starter_file, "code", machine, *map(str, slots))
    assert result.returncode == 0, result.stderr
    expected = [
        f"code\t{machine}\t{slot}\t{codes[slot][0]}\t{len(codes[slot][1])}\t{sha(codes[slot][1])}"
        for slot in slots
    ]
    assert result.stdout.splitlines() == expected


@pytest.mark.slow
def test_the_wasm32_slots_are_the_seats_table(dump, starter_file, world):
    """The bytes a wasm32 seat puts in its function table are the bytes the library resolves."""
    bodies = wasm_table.leaf_bodies(world)
    slots = sorted(bodies)
    result = run_dump(dump, starter_file, "code", "wasm32", *map(str, slots))
    assert result.returncode == 0, result.stderr
    declared = {slot: code for slot, (_, code) in leaf_codes(world, "wasm32").items()}
    digests = [line.split("\t")[5] for line in result.stdout.splitlines()]
    assert len(digests) == len(slots)
    same = 0
    for slot, digest in zip(slots, digests, strict=True):
        # The table's bytes begin with the bytes the library resolves (they are the same bytes:
        # a build can run ahead of a blob's pinned length, never behind it).
        assert bodies[slot][: len(declared[slot])] == declared[slot]
        assert digest == sha(declared[slot])
        same += bodies[slot] == declared[slot]
    assert same >= len(slots) - 1


@pytest.mark.slow
def test_a_slot_the_interpreter_does_not_have_is_not_found(dump, starter_file):
    for slot, machine in ((0, "wasm32"), (50, "wasm32"), (1, "riscv64")):
        result = run_dump(dump, starter_file, "code", machine, str(slot))
        assert result.returncode == 1
        assert result.stderr.strip().endswith(f"status {STATUS_NOT_FOUND}"), (
            result.stderr
        )
