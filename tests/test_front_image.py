"""The front end in the image (docs/19): source in over the channel, the words of a Function out.

`native/front/front.rxfs` is the compiler front end as a program: what `tools/native/rxfc.py`
compiles from source is what it writes from the same source, byte for byte
(`tests/test_front_corpus.py`). Here the image does it and everything it reads or writes travels
as frames of the channel: the source, the table of the numbers it may use, and the room for its
words are objects of the arena, sent as one delta (`stage` is a byte stream of frames); the words
come back as `read` frames; the Function they name is installed by a delta through the same
rings; the image applies it on its journal and relinks itself. The new Function answers on the
interpreter and then as the code `tiny.compile` makes of the words that still lie in the arena,
both without a restart, on x86_64, aarch64 and wasm32.

What the harness still writes is the call block (the request of `native/front/words.rxfs` and the
arguments) and the work memory of the request, both behind the lookup table of the live block:
that is the memory the harness provider writes, docs/19. Content travels on the rings.

The three machines are three params of every test, and every test builds its own machine (a fresh
emulator or its own node process), so the params run in parallel:

    pytest tests/test_front_image.py -n auto     # pytest-xdist; -n 3 is one worker per machine
"""

import hashlib
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import compose
import graph_header
import graph_sync
import link
import pytest
import rxfc

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live import physical

from . import live_world as lw
from . import rxf_world as w
from .snippets import STARTER
from .test_rxfc import MACHINES
from .test_tiny import IDS

FRONT = Path(__file__).parents[1] / "native" / "front" / "front.rxfs"
TRIPLE = Path(__file__).parent / "selfedit" / "triple.rxfs"
FRONT_XML = Path(__file__).parent / "selfedit" / "front.rxfd"
TRIPLE_XML = Path(__file__).parent / "selfedit" / "triple.rxfd"
SELFEDIT = "pymergetic.rxf.selfedit"
FRONT_FUNCTION = f"{SELFEDIT}.front"
TRIPLE_FUNCTION = f"{SELFEDIT}.triple"
TINY_FUNCTION = "pymergetic.rxf.foundation.tiny.compile"

N = 7  # the argument of the Function the image compiles and runs: triple(7) is 21
SCRATCH = 12 << 20  # the work memory of a request in the live block (link.LiveSpec.scratch)
FRONT_WORK = 8_963_144  # WORK_BYTES of native/front/words.rxfs
TINY_WORK = w.WORK_BYTES  # and of native/tiny (tools/native/rxfc.py keeps the numbers here)
OUT_ROOM = 0x10000  # the room the words of the program are written into


def blob(name: str, data: bytes) -> ET.Element:
    """An object of the arena that is only its bytes: `ByteBuffer` with the bytes it does not
    describe (rxf:data, raw)."""
    return ET.Element(
        "ByteBuffer", {"name": name, f"{{{graph_sync.RXF}}}data": data.hex()}
    )


def authored(
    template: Path,
    path: str,
    program: bytes,
    source: bytes,
    objects: tuple[ET.Element, ...] = (),
) -> str:
    """The delta that installs `path` with `program`, and the digest of what it means: its source.
    `objects` join the module (the bytes the request of the program names)."""
    ET.register_namespace("rxf", graph_sync.RXF)
    document = ET.parse(template)
    function = next(document.getroot().iter("Function"))
    function.set("semantic_digest", hashlib.sha256(source).hexdigest())
    function.append(graph_sync.bytecode(function, path, program))
    if objects:
        module = next(document.getroot().iter("Module"))
        for item in objects:
            module.append(item)
    return ET.tostring(document.getroot(), encoding="unicode")


def set_bytes(path: str, data: bytes) -> str:
    """The delta that replaces the bytes of an object: the image writes its out object itself, the
    host mirrors the state of its run before it sends the next delta."""
    ET.register_namespace("rxf", graph_sync.RXF)
    delta = ET.Element("delta")
    item = ET.SubElement(
        delta, f"{{{graph_sync.RXF}}}set", {f"{{{graph_sync.RXF}}}data": data.hex()}
    )
    item.set("path", path)
    return ET.tostring(delta, encoding="unicode")


def identity(image: Image, path: str) -> int:
    handle = image.resolve(path)
    assert handle is not None
    return compose.object_id(image, handle)


def send(channel: lw.Channel, expected: Image, text: str) -> int:
    """One delta over the channel: compiled against the state the image is in (the host mirrors
    it), and accepted as the image agrees. What the image writes itself the host mirrors by hand
    before the next delta (set_bytes)."""
    compiled = physical.from_xml(Image.from_bytes(expected.to_bytes()), ("delta.rxfd", text))
    expected.apply(text)
    assert channel.client.apply(compiled.data) == expected.digest()
    return compiled.creates


def value_at(channel: lw.Channel, path: str) -> int:
    """Where the bytes of an object lie in the memory of the machine: the arena is mapped where the
    image keeps it, and the value of an object is its payload behind the fixed part of its type."""
    machine = channel.machine
    assert machine.linked.live is not None
    arena_at, _ = machine.linked.live.regions["arena"]
    arena = channel.arena()
    image = Image.from_bytes(arena)
    handle = image.resolve(path)
    assert handle is not None
    type_handle = image.get(handle)["type"]
    values = dict(image.fields(type_handle))
    variable = "Variable" in values.get("flags", "")
    prefix = image.layout(type_handle)["prefix"] if variable else 0
    return arena_at + link.arena_cells(arena)[handle] + link.CELL_BYTES + prefix


@pytest.fixture(scope="module")
def image() -> Image:
    return tree.load(Path(STARTER))


@pytest.fixture(scope="module")
def base(image: Image) -> bytes:
    return image.to_bytes()


def test_the_program_of_the_source_is_the_one_the_host_writes():
    words = rxfc.compile_file(TRIPLE)
    magic, frame, parameters, argwords, entry = struct.unpack_from("<5Q", words)
    assert magic == 0x31475852 and frame <= 1024
    assert parameters == 1 and argwords == 1 and entry == 5 + 2 * parameters
    assert len(words) % 8 == 0


@pytest.fixture(params=MACHINES)
def running(request: pytest.FixtureRequest, image: Image):
    spec = link.LiveSpec(headroom=8 << 20, log=8 << 20, stage=8 << 20, scratch=SCRATCH)
    machine = lw.machine_of(request.param, image, spec)
    try:
        yield request.param, lw.Channel(machine)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_the_image_compiles_the_source_and_the_function_it_makes_runs(running, base: bytes):
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    # the front end and the three objects its request names become the image in one delta: the
    # source of the program, the names of the numbers it may use, and the room for its words. The
    # host makes the words of the front end itself: rxfc.py and the front end agree byte for byte
    # on every source, so this one delta needs no in-image run.
    source = TRIPLE.read_bytes()
    numbers = sorted(graph_header.numbers().items())
    names = b"".join(name.encode() + b"\0" for name, _ in numbers)
    front_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        rxfc.compile_file(FRONT),
        FRONT.read_bytes(),
        (blob("source", source), blob("names", names), blob("out", bytes(OUT_ROOM))),
    )
    assert send(channel, expected, front_text) > 0

    # the request of native/front/words.rxfs: it points into the arena (the source, the names, the
    # out object) and into the work region behind the lookup table, where the front end keeps its
    # tables. The const rows name the addresses of the names object.
    source_at = value_at(channel, f"{SELFEDIT}.source")
    names_at = value_at(channel, f"{SELFEDIT}.names")
    out_at = value_at(channel, f"{SELFEDIT}.out")
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at, consts_at = free, free + 0x100
    work_at = free + 0x100000
    assert w_cap >= SCRATCH
    assert SCRATCH - 0x100000 >= FRONT_WORK

    entries = b""
    offset = 0
    for cname, value in numbers:
        encoded = cname.encode() + b"\0"
        entries += struct.pack("<4Q", names_at + offset, len(cname), value, 0)
        offset += len(encoded)
    machine.write(consts_at, entries)
    machine.write(
        request_at,
        struct.pack(
            "<12Q",
            source_at,
            len(source),  # R_SOURCE, R_SOURCE_SIZE
            out_at,
            OUT_ROOM,  # R_OUT, R_OUT_CAPACITY
            work_at,
            SCRATCH - 0x100000,  # R_WORK, R_WORK_CAPACITY
            0,
            0,  # R_OUT_SIZE, R_AT (written by the run)
            0,
            0,  # R_FILES, R_FILE_COUNT: triple has no include
            consts_at,
            len(numbers),  # R_CONSTS, R_CONST_COUNT
        ),
    )

    # the image runs its front end on the source, on the interpreter, and writes the words
    status, _ = machine.run(
        identity(expected, f"{FRONT_FUNCTION}.bytecode"), struct.pack("<2Q", 0, request_at)
    )
    assert status == 0
    out_size, at = struct.unpack("<2Q", machine.read(request_at + 48, 16))
    assert at == 0 and out_size > 0

    # the words come back over the channel as read frames, out of the out object of the arena (a
    # ByteBuffer describes no fields: its payload is its bytes)
    words = channel.client.read(
        channel.client.resolve(f"{SELFEDIT}.out"), 0, out_size
    )
    assert words == rxfc.compile_file(TRIPLE)

    # the words become the Function the source names: one delta over the channel, then it runs.
    # The image wrote its out object itself; the host mirrors that state before it compiles the
    # next delta against it.
    triple_text = authored(TRIPLE_XML, TRIPLE_FUNCTION, words, source)
    written = words + bytes(OUT_ROOM - len(words))
    expected.apply(set_bytes(f"{SELFEDIT}.out", written))  # the host mirrors the self-write
    assert send(channel, expected, triple_text) > 0
    assert channel.loaded().digest() == expected.digest()
    status, value = machine.run(
        identity(expected, f"{TRIPLE_FUNCTION}.bytecode"), struct.pack("<Q", N)
    )
    assert status == 0 and value == 3 * N

    # tiny.compile, the compiler of the image that is a Function of it, makes machine code of the
    # words that lie in the arena yet (the out object), and the code answers the same. Only the
    # code it writes goes to memory of the machine: the emit region keeps the image's own bytes
    # after a commit (the big routines of the pump run in one place, native/live.cpp).
    code_at, twork_at = live.regions["emit"][0], live.regions["emit"][0] + 0x200000
    table_at = machine.linked.base + machine.linked.table[0]
    count = (machine.linked.table[1] - machine.linked.table[0]) // 24
    machine.write(
        request_at,
        struct.pack(
            "<11Q",
            out_at,
            out_size,
            code_at,
            OUT_ROOM,  # out, capacity
            twork_at,
            TINY_WORK,  # work, work_bytes
            0,
            0,  # out size and the word it stopped at (written by the run)
            IDS[name],
            table_at,
            count,  # machine, the table of the leaves
        ),
    )
    status, _ = machine.run(
        identity(expected, f"{TINY_FUNCTION}.bytecode"),
        struct.pack("<2Q", machine.linked.context, request_at),
    )
    assert status == 0
    size, word = struct.unpack("<2Q", machine.read(request_at + 48, 16))
    assert word == 0 and size > 0
    status, value = machine.call_code(machine.read(code_at, size), struct.pack("<Q", N))
    assert status == 0 and value == 3 * N


SOURCES = sorted((Path(__file__).parents[1] / "native" / "front").glob("*.rxfs"))


def successor_sources() -> dict[str, bytes]:
    """The sources of the front end with the one change of the successor: a refusal answers its
    words in the other order (emit.rxfs). The same answers, different words -- and where they
    differ is where the change is."""
    files = {p.name: p.read_bytes() for p in SOURCES}
    before, after = (
        b"st = OUT_OF_MEMORY;\n        at = load64(work + G_LASTLINE);",
        b"at = load64(work + G_LASTLINE);\n        st = OUT_OF_MEMORY;",
    )
    text = files["emit.rxfs"]
    assert text.count(before) >= 2
    head, _, tail = text.rpartition(before)  # the refusal answers in the other order
    files["emit.rxfs"] = head + after + tail
    return files


def test_the_image_compiles_its_own_sources_and_installs_the_successor(
    running, base: bytes, tmp_path: Path
):
    """The loop closes (docs/19): the image compiles the sources of its own front end, and the
    delta installs the compiler that runs next -- the same machine, no restart. The successor is
    the one program of the image whose change is known: one refusal answers its words in the other
    order,     and the words it writes differ from the first front end where that is."""
    _name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)
    files = successor_sources()
    program = rxfc.compile_file(FRONT)  # the host's compiler makes the front end that boots
    for filename, text in files.items():
        (tmp_path / filename).write_bytes(text)
    successor = rxfc.compile_file(tmp_path / "front.rxfs")  # what the change compiles to
    changed = [i for i, (a, b) in enumerate(zip(program, successor)) if a != b]
    assert len(successor) == len(program) and changed
    assert max(changed) - min(changed) <= 512  # one refusal moved: that is all

    # one delta boots the front end with the objects of its request: the source of the compiler,
    # the names of the numbers it may use, the table of its files (names and texts), and the room
    # for the words it writes.
    numbers = sorted(graph_header.numbers().items())
    names = b"".join(nm.encode() + b"\0" for nm, _ in numbers)
    table, spans = b"", []
    for filename, text in files.items():
        encoded = filename.encode()
        spans.append((len(table), len(encoded), len(table) + len(encoded), len(text)))
        table += encoded + text
    out_room = 2 * len(program)
    front_text = authored(
        FRONT_XML,
        FRONT_FUNCTION,
        program,
        FRONT.read_bytes(),
        (
            blob("source", files["front.rxfs"]),
            blob("names", names),
            blob("files", table),
            blob("out", bytes(out_room)),
        ),
    )
    assert send(channel, expected, front_text) > 0

    # the request points into the arena (source, names, the table of the files, the out object)
    # and into the work memory of the request behind the lookup table. The rows of the file table
    # name the addresses of the file object; the host knows them where the image put the object.
    source_at = value_at(channel, f"{SELFEDIT}.source")
    names_at = value_at(channel, f"{SELFEDIT}.names")
    files_at = value_at(channel, f"{SELFEDIT}.files")
    out_at = value_at(channel, f"{SELFEDIT}.out")
    w_addr, w_cap = live.regions["work"]
    free = w_addr + (w_cap - SCRATCH)
    request_at, consts_at, files_at_rows = free, free + 0x100, free + 0x1000
    work_at = free + 0x100000
    assert consts_at + 32 * len(numbers) <= files_at_rows
    assert files_at_rows + 32 * len(spans) <= work_at

    rows = b""
    offset = 0
    for cname, value in numbers:
        encoded = cname.encode() + b"\0"
        rows += struct.pack("<4Q", names_at + offset, len(cname), value, 0)
        offset += len(encoded)
    machine.write(consts_at, rows)
    machine.write(
        files_at_rows,
        b"".join(
            struct.pack("<4Q", files_at + a, b, files_at + c, d) for a, b, c, d in spans
        ),
    )
    machine.write(
        request_at,
        struct.pack(
            "<12Q",
            source_at,
            len(files["front.rxfs"]),  # R_SOURCE, R_SOURCE_SIZE
            out_at,
            out_room,  # R_OUT, R_OUT_CAPACITY
            work_at,
            SCRATCH - 0x100000,  # R_WORK, R_WORK_CAPACITY
            0,
            0,  # R_OUT_SIZE, R_AT (written by the run)
            files_at_rows,
            len(spans),  # R_FILES, R_FILE_COUNT: the includes of the front end
            consts_at,
            len(numbers),  # R_CONSTS, R_CONST_COUNT
        ),
    )

    # the front end compiles its own sources in the image and writes the words of its successor
    status, _ = machine.run(
        identity(expected, f"{FRONT_FUNCTION}.bytecode"), struct.pack("<2Q", 0, request_at)
    )
    assert status == 0
    out_size, at = struct.unpack("<2Q", machine.read(request_at + 48, 16))
    assert at == 0 and out_size == len(successor)
    words = channel.client.read(
        channel.client.resolve(f"{SELFEDIT}.out"), 0, out_size
    )
    assert words == successor  # the image and the host compiler agree on the compiler itself

    # the delta installs the successor as the program of the Function: it replaces, it does not
    # create. The image wrote its out object itself; the host mirrors the state first.
    install = set_bytes(f"{FRONT_FUNCTION}.bytecode", words)
    expected.apply(set_bytes(f"{SELFEDIT}.out", words + bytes(out_room - len(words))))
    assert send(channel, expected, install) == 0
    assert channel.loaded().digest() == expected.digest()

    # the successor runs (same machine, no restart) and compiles the program of the test above
    assert send(channel, expected, set_bytes(f"{SELFEDIT}.source", TRIPLE.read_bytes())) == 0
    names_at = value_at(channel, f"{SELFEDIT}.names")
    out_at = value_at(channel, f"{SELFEDIT}.out")
    rows = b""
    offset = 0
    for cname, value in numbers:
        encoded = cname.encode() + b"\0"
        rows += struct.pack("<4Q", names_at + offset, len(cname), value, 0)
        offset += len(encoded)
    machine.write(consts_at, rows)
    source_at = value_at(channel, f"{SELFEDIT}.source")
    machine.write(
        request_at,
        struct.pack(
            "<12Q",
            source_at,
            len(TRIPLE.read_bytes()),  # R_SOURCE, R_SOURCE_SIZE
            out_at,
            out_room,  # R_OUT, R_OUT_CAPACITY
            work_at,
            SCRATCH - 0x100000,  # R_WORK, R_WORK_CAPACITY
            0,
            0,  # R_OUT_SIZE, R_AT
            0,
            0,  # R_FILES, R_FILE_COUNT: triple has no include
            consts_at,
            len(numbers),  # R_CONSTS, R_CONST_COUNT
        ),
    )
    status, _ = machine.run(
        identity(expected, f"{FRONT_FUNCTION}.bytecode"), struct.pack("<2Q", 0, request_at)
    )
    assert status == 0
    out_size, at = struct.unpack("<2Q", machine.read(request_at + 48, 16))
    words = channel.client.read(
        channel.client.resolve(f"{SELFEDIT}.out"), 0, out_size
    )
    assert words == rxfc.compile_file(TRIPLE)
