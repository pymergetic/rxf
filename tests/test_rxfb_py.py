"""pymergetic.rxfb: the CPython face of the rxfb reader library (rxfb/python).

The extension is a translation layer over the C core (rxfb/rxfb.c is linked as it is); these tests
prove the face, not the reader again (tests/test_rxfb.py proves the reader):

1. it reproduces the C example: for the real v7 starter image and for a small one, the text that
   `rxfb_dump` prints (image digest, the whole walk, objects in full, function slots) is
   rebuilt from the Python objects and equals the C program's stdout byte for byte;
2. it is zero copy: payloads, code bodies and reference tables are slices of one memoryview of the
   caller's buffer - a byte changed in the bytearray shows through the view - and the buffer stays
   pinned while any view lives;
3. it refuses what the C core refuses, with a typed exception (never a crash): the damaged images
   of tests/test_rxfb.py, random byte damage, and bad arguments;
4. the checked-in stub is what the module reports.
"""

import gc
import hashlib
import mmap
import random
import struct
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pymergetic import rxfb
from pymergetic.rxftk.core.arena import (
    INDEX_OFFSET,
    OBJECT_HEADER_SIZE,
    ObjectArena,
)

from . import test_rxfb as shared
from .test_rxfb import (
    OBJECTS,
    RUN,
    STATUS_BAD_HANDLE,
    STATUS_BAD_IMAGE,
    STATUS_BAD_NAME,
    STATUS_NOT_FOUND,
    corrupted,
    run_dump,
    sha,
)

# The fixtures of the reader's own tests, as they are: the C example built by its CMake target, the
# small image, the starter exported to a v7 file.
bridge = shared.bridge
dump = shared.dump
starter_file = shared.starter_file
tiny = shared.tiny
tiny_file = shared.tiny_file
world = shared.world

ROOT = Path(__file__).parents[1]
STUB = ROOT / "src/pymergetic/rxfb/__init__.pyi"
STUBGEN = "python -m nanobind.stubgen -m pymergetic.rxfb -q -o src/pymergetic/rxfb/__init__.pyi"
REFS_SHOWN = 4  # rxfb_dump.c: how many references an `object` record spells out
NAMES_SHOWN = 8  # ... and how many child names
MACHINES = ("x86_64_linux", "aarch64_linux", "wasm32")


# ---- what rxfb_dump prints, rebuilt from the Python face ----------------------------------------
# Formatting only: every value below is something the extension answered. The one thing Python
# brings is the SHA-256, as the C example brings the engine's (the library hashes nothing).


def digest_of(view: memoryview) -> str:
    return hashlib.sha256(view).hexdigest()


def preorder(image: rxfb.Image) -> Iterator[rxfb.Object]:
    """Every object, the tree in chain order, children before the next sibling."""
    stack: list[Iterator[rxfb.Object]] = [iter([image.root])]
    while stack:
        for current in stack[-1]:
            yield current
            try:
                children = iter(current.children())
            except rxfb.Protected:
                continue  # the object index: a table, not a chain
            stack.append(children)
            break
        else:
            stack.pop()


def image_line(image: rxfb.Image) -> str:
    return f"image\t{image.size}\t{image.count}\t{digest_of(image.view)}\n"


def walk_text(image: rxfb.Image) -> str:
    lines = []
    for current in preorder(image):
        payload = current.payload
        lines.append(
            f"walk\t{current.path()}\t{current.kind}\t{len(payload)}\t{digest_of(payload)}\n"
        )
    return "".join(lines)


def object_text(image: rxfb.Image, path: str) -> str:
    current = image.lookup(path)
    payload = current.payload
    spec = current.spec()
    type_path = "" if spec.type is None else image.get(spec.type).path()
    names = [child.name or "" for child in current.children()]
    refs = current.refs()
    spelled = []
    for ref in list(refs)[:REFS_SHOWN]:
        role = "" if ref.role is None else image.get(ref.role).path()
        spelled.append(f"{image.get(ref.target).path()}:{role}:{ref.kind}:{ref.index}")
    return (
        f"object\t{path}\t{current.kind}\t{len(payload)}\t{payload.hex()}\t{digest_of(payload)}\n"
        f"spec\t{path}\t{type_path}\t{spec.flags}\t{spec.index}\t{spec.generation}\n"
        f"children\t{path}\t{','.join(names[:NAMES_SHOWN])}\t{len(names)}\n"
        + "\t".join([f"refs\t{path}\t{len(refs)}", *spelled])
        + "\n"
    )


def code_text(image: rxfb.Image, machine: str, slot: int) -> str:
    code = image.slot(slot, machine)
    owner = image.get(image.get(code.code).parent or 0).path()
    return f"code\t{machine}\t{slot}\t{owner}\t{code.length}\t{digest_of(code.view)}\n"


def status_of(stderr: str) -> int:
    return int(stderr.strip().rsplit("status ", 1)[1])


# ---- the C example's output, byte for byte -------------------------------------------------------


def test_a_small_image_reads_as_the_c_example_prints_it(dump, tiny, tiny_file):
    _, data = tiny
    image = rxfb.attach(data)
    assert run_dump(dump, tiny_file).stdout == image_line(image)
    assert run_dump(dump, tiny_file, "walk").stdout == walk_text(image)
    paths = ("pkg", "pkg.leaf", "pkg.grüße", "pkg.inner", "pkg.inner.deep")
    expected = run_dump(dump, tiny_file, "object", *paths)
    assert expected.returncode == 0, expected.stderr
    assert expected.stdout == "".join(object_text(image, path) for path in paths)


@pytest.mark.slow
def test_the_starter_reads_as_the_c_example_prints_it(dump, bridge, starter_file):
    data = starter_file.read_bytes()
    image = rxfb.attach(data)
    assert image.size == len(bridge.arena)
    assert image.count == bridge.count
    assert len(image) == bridge.count
    assert run_dump(dump, starter_file).stdout == image_line(image)
    assert digest_of(image.view) == bridge.digest()  # the model's digest, too
    walked = run_dump(dump, starter_file, "walk")
    assert walked.returncode == 0, walked.stderr
    assert walked.stdout == walk_text(image)
    assert len(walked.stdout.splitlines()) == image.count


@pytest.mark.slow
def test_objects_of_the_starter_read_as_the_c_example_prints_them(dump, starter_file):
    image = rxfb.attach(starter_file.read_bytes())
    result = run_dump(dump, starter_file, "object", *OBJECTS)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "".join(object_text(image, path) for path in OBJECTS)


@pytest.mark.slow
@pytest.mark.parametrize("machine", MACHINES)
def test_the_function_slots_read_as_the_c_example_prints_them(
    dump, starter_file, machine
):
    image = rxfb.attach(starter_file.read_bytes())
    slots = range(1, 50)  # every leaf of the interpreter
    result = run_dump(dump, starter_file, "code", machine, *map(str, slots))
    assert result.returncode == 0, result.stderr
    assert result.stdout == "".join(code_text(image, machine, slot) for slot in slots)


@pytest.mark.slow
def test_a_function_is_asked_for_its_code_by_machine(starter_file):
    image = rxfb.attach(starter_file.read_bytes())
    run = image.lookup(RUN)
    for machine in MACHINES:
        by_slot = image.slot(1, machine)
        assert by_slot.length == len(by_slot) == len(by_slot.view)
        assert image.slot(1, machine, run).entry == by_slot.entry
        function = image.get(image.get(by_slot.code).parent or 0)
        again = function.code(machine)
        assert (again.entry, again.length, again.code, again.blob) == (
            by_slot.entry,
            by_slot.length,
            by_slot.code,
            by_slot.blob,
        )
        assert again.view == by_slot.view
        assert image.view[again.entry : again.entry + again.length] == again.view


@pytest.mark.slow
def test_a_slot_the_interpreter_does_not_have_is_not_found(dump, starter_file):
    image = rxfb.attach(starter_file.read_bytes())
    for slot, machine in ((0, "wasm32"), (50, "wasm32"), (1, "riscv64")):
        with pytest.raises(rxfb.NotFound) as caught:
            image.slot(slot, machine)
        assert caught.value.status == STATUS_NOT_FOUND
        result = run_dump(dump, starter_file, "code", machine, str(slot))
        assert status_of(result.stderr) == caught.value.status
    with pytest.raises(rxfb.BadName):
        image.slot(1, "wasm\x0032")


@pytest.mark.slow
def test_objects_know_their_neighbours(starter_file):
    image = rxfb.attach(starter_file.read_bytes())
    run = image.lookup(RUN)
    assert image[RUN] == run
    assert image[run.handle] == run
    assert image.lookup(RUN.encode()) == run
    assert run.path() == RUN
    assert hash(image[RUN]) == hash(run)
    assert image.root.path() == ""
    assert image.root.parent is None
    assert image[""] == image.root
    entry = image.entry
    assert entry is None or isinstance(entry, rxfb.Object)
    parent = image.get(run.parent or 0)
    assert parent.path() == RUN.rsplit(".", 1)[0]
    siblings = [child.handle for child in parent.children()]
    assert run.handle in siblings
    assert len(parent.children()) == len(siblings)
    assert [child.handle for child in parent.children()] == siblings  # walkable again
    assert run.kind == rxfb.OBJ_MODULE
    assert run.name == "run" and image.symbol(run.name_id) == "run"
    assert run.spec().type is not None
    assert "graph.run" in repr(run)
    # the references of the interpreter: Leaf slots by number, as the seats link them
    refs = run.refs()
    assert len(refs) >= 49
    assert len(list(refs)) == len(refs)
    assert refs[-1].index == list(refs)[-1].index
    with pytest.raises(IndexError):
        refs[len(refs)]
    leaves = {
        ref.index: ref
        for ref in refs
        if ref.role is not None and image.get(ref.role).name == "Leaf"
    }
    assert sorted(leaves) == list(range(1, 50))
    rows = refs.view
    assert len(rows) == 24 * len(refs)
    target, role, kind, binding, index = struct.unpack_from("<QQHHI", rows, 24 * 3)
    ref = refs[3]
    assert (target, role, kind, binding, index) == (
        ref.target,
        rxfb_role(ref),
        ref.kind,
        ref.binding,
        ref.index,
    )
    assert refs.view.obj is image.view.obj


def rxfb_role(ref: rxfb.Ref) -> int:
    return (1 << 64) - 1 if ref.role is None else ref.role


# ---- zero copy ----------------------------------------------------------------------------------


def test_a_view_shares_the_callers_bytes(tiny):
    arena, data = tiny
    buffer = bytearray(data)
    image = rxfb.attach(buffer)
    leaf = image.lookup("pkg.leaf")
    payload = leaf.payload
    assert payload.readonly and image.view.readonly
    assert bytes(payload[:16]) == bytes(range(16))
    assert payload.obj is buffer and image.view.obj is buffer
    assert payload.nbytes == leaf.payload_size

    # where the bytes are in the caller's own buffer: the arena's record of the leaf
    handle = arena.find(arena.find(arena.root_handle, "pkg"), "leaf")
    record, _ = arena.locate(handle)
    start = record + OBJECT_HEADER_SIZE
    assert buffer[start : start + 16] == bytes(range(16))
    buffer[start + 3] = 0xAA
    assert payload[3] == 0xAA  # the view saw the caller's write: it is not a copy
    assert image.lookup("pkg.leaf").payload[3] == 0xAA
    assert image.view[start + 3] == 0xAA
    with pytest.raises(TypeError):
        payload[3] = 1  # read-only through the face
    assert buffer[start + 3] == 0xAA


def test_the_buffer_stays_pinned_while_a_view_lives(tiny):
    _, data = tiny
    buffer = bytearray(data)
    image = rxfb.attach(buffer)
    with pytest.raises(BufferError):
        buffer.append(0)  # the image is mapped in place: it cannot move
    payload = image.lookup("pkg.leaf").payload
    del image
    gc.collect()
    with pytest.raises(BufferError):
        buffer.extend(b"x")  # the payload view still holds it
    assert bytes(payload[:4]) == bytes(range(4))
    del payload
    gc.collect()
    buffer.append(0)  # nothing is exported any more


def test_an_object_keeps_its_image_alive(tiny):
    _, data = tiny
    current = rxfb.attach(bytearray(data)).lookup("pkg.inner.deep")
    gc.collect()
    assert current.path() == "pkg.inner.deep"
    assert bytes(current.payload) == b"deepdeep"
    children = rxfb.attach(bytes(data)).root.children()
    gc.collect()
    assert "pkg" in [child.name for child in children]


@pytest.mark.slow
def test_the_starter_maps_from_a_file_without_a_copy(starter_file):
    with (
        starter_file.open("rb") as handle,
        mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as mapped,
    ):
        image = rxfb.attach(mapped)
        assert image.view.obj is mapped
        assert image.count > 20000
        run = image.lookup(RUN)
        payload = run.payload
        assert payload.obj is mapped
        del image, run, payload  # the mapping cannot close while a view holds it
        gc.collect()


# ---- refusals: a typed exception, never a crash --------------------------------------------------


def test_things_that_are_not_images_are_refused_with_types():
    assert issubclass(rxfb.BadImage, rxfb.RxfbError)
    assert issubclass(rxfb.NotFound, LookupError)
    assert issubclass(rxfb.BadImage, ValueError)
    for data in (b"", b"RXFA", bytes(4096), bytes(1 << 16), b"x" * 5000):
        with pytest.raises(rxfb.BadImage) as caught:
            rxfb.attach(data)
        assert caught.value.status == STATUS_BAD_IMAGE
        assert "attach" in str(caught.value)
    with pytest.raises(TypeError):
        rxfb.attach("a string is not a buffer")  # type: ignore[arg-type]
    with pytest.raises(BufferError):
        rxfb.attach(memoryview(bytes(8192))[::2])  # not a flat run of bytes


def test_a_misaligned_mapping_is_refused(tiny):
    _, data = tiny
    shifted = bytearray(len(data) + 8)
    shifted[1 : len(data) + 1] = data
    with pytest.raises(rxfb.BadImage):
        rxfb.attach(memoryview(shifted)[1 : len(data) + 1])
    shifted[8 : len(data) + 8] = data
    assert rxfb.attach(memoryview(shifted)[8 : len(data) + 8]).count > 0


def test_a_damaged_image_is_refused_at_attach(tiny):
    _, image = tiny
    committed = struct.unpack_from("<Q", image, 8)[0]
    capacity = struct.unpack_from("<I", image, INDEX_OFFSET + OBJECT_HEADER_SIZE + 4)[0]
    cases = {  # the set of tests/test_rxfb.py::test_a_damaged_image_is_refused_not_trusted
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
        with pytest.raises(rxfb.BadImage) as caught:
            rxfb.attach(data)
        assert caught.value.status == STATUS_BAD_IMAGE, name


def test_a_damaged_record_is_refused_where_it_is_read(dump, tiny, tmp_path):
    arena, image = tiny
    package = arena.find(arena.root_handle, "pkg")
    leaf = arena.find(package, "leaf")
    record, _ = arena.locate(leaf)
    cases = {  # the set of tests/test_rxfb.py::test_a_damaged_record_is_refused_not_followed
        "stale record": (
            corrupted(image, record + 2, "<H", 77),
            STATUS_BAD_HANDLE,
            rxfb.BadHandle,
        ),
        "payload past the end": (
            corrupted(image, record + 20, "<I", 1 << 30),
            STATUS_BAD_IMAGE,
            rxfb.BadImage,
        ),
        "chain cycle": (
            corrupted(image, record + 12, "<I", leaf),
            STATUS_BAD_IMAGE,
            rxfb.BadImage,
        ),
        "slot outside": (
            corrupted(
                image,
                INDEX_OFFSET + OBJECT_HEADER_SIZE + 8 + 16 * (leaf & 0xFFFFF) + 8,
                "<Q",
                len(image) + 4096,
            ),
            STATUS_BAD_HANDLE,
            rxfb.BadHandle,
        ),
    }
    for name, (data, status, error) in cases.items():
        damaged = rxfb.attach(
            data
        )  # the superblock and the index are intact: it attaches
        with pytest.raises(error) as caught:
            walk_text(damaged)
        assert caught.value.status == status, name
        # and it is the status the C example answers for the same bytes
        path = tmp_path / "damaged.rxfb"
        path.write_bytes(data)
        assert status_of(run_dump(dump, path, "walk").stderr) == status, name


def test_a_path_that_names_nothing_is_not_found(dump, tiny_file, tiny):
    _, data = tiny
    image = rxfb.attach(data)
    for path, status, error in (
        ("pkg.nothing", STATUS_NOT_FOUND, rxfb.NotFound),
        ("pkg.leaf.below", STATUS_NOT_FOUND, rxfb.NotFound),  # a leaf has no children
        ("pkg..leaf", STATUS_BAD_NAME, rxfb.BadName),
        ("pkg.leaf.", STATUS_BAD_NAME, rxfb.BadName),
    ):
        with pytest.raises(error) as caught:
            image.lookup(path)
        assert caught.value.status == status, path
        with pytest.raises(error):
            image[path]
        result = run_dump(dump, tiny_file, "object", path)
        assert status_of(result.stderr) == status, path
    with pytest.raises(TypeError):
        image.lookup(5)  # type: ignore[arg-type]


def test_a_bad_handle_is_refused_not_overflowed(tiny):
    _, data = tiny
    image = rxfb.attach(data)
    for handle in (-1, 1 << 40, rxfb.HANDLE_NONE, image.count, (1 << 20) - 1):
        with pytest.raises(rxfb.BadHandle) as caught:
            image.get(handle)
        assert caught.value.status == STATUS_BAD_HANDLE, handle
    with pytest.raises(rxfb.BadHandle):
        image[image.count + 7]
    with pytest.raises(TypeError):
        image.get("1")  # type: ignore[arg-type]
    with pytest.raises(rxfb.BadName):
        image.symbol(1 << 20)  # no such interned name
    # the object index (handle index 0) is a table: it has no children
    index = next(
        current for current in preorder(image) if current.handle & 0xFFFFF == 0
    )
    with pytest.raises(rxfb.Protected):
        index.children()
    assert index.payload_size > 0
    assert image.root.parent is None


def test_random_damage_never_crashes_the_face(tiny):
    """400 randomly damaged images: every one attaches and reads or is refused with a typed
    exception. A read outside the bytes would be a segfault here (the C example's sanitized twin
    is in tests/test_rxfb.py)."""
    _, data = tiny
    generator = random.Random(20260431)
    refused = 0
    for _ in range(400):
        damaged = bytearray(data)
        for _ in range(generator.choice((1, 2, 4))):
            at = generator.randrange(32) if generator.random() < 0.33 else None
            if at is None:
                at = generator.randrange(INDEX_OFFSET, len(damaged))
            damaged[at] = generator.randrange(256)
        try:
            image = rxfb.attach(damaged)
        except rxfb.RxfbError:
            refused += 1
            continue
        try:
            walk_text(image)
            for path in ("pkg.leaf", "pkg.inner.deep", "pkg", ""):
                object_text(image, path)
        except rxfb.RxfbError:
            refused += 1  # (damaged name bytes decode through surrogate escapes: no UnicodeError)
    assert refused > 0  # the damage does land on structure sometimes


# ---- the stub -----------------------------------------------------------------------------------


def test_the_checked_in_stub_matches_the_module(tmp_path):
    """The .pyi next to the extension is what editors read; it is generated."""
    generated = tmp_path / "__init__.pyi"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "nanobind.stubgen",
            "-m",
            "pymergetic.rxfb",
            "-q",
            "-o",
            str(generated),
        ],
        check=True,
        capture_output=True,
    )
    assert STUB.read_text() == generated.read_text(), f"stale; run: {STUBGEN}"


def test_the_face_is_the_same_core(tiny):
    """Not a Python reader: the one in the module is the C core's, so the engine's walk of the
    same bytes agrees object for object (names, kinds, payload sizes)."""
    _, data = tiny
    image = rxfb.attach(data)
    arena = ObjectArena.attach(data)
    assert image.count == arena.handle_count
    for current in preorder(image):
        handle = current.handle
        kind, *_ = arena.load(handle)
        assert current.kind == kind
        assert current.path() == arena.path_of(handle)
        assert bytes(current.payload) == arena.payload(handle)
        assert sha(bytes(current.payload)) == digest_of(current.payload)
