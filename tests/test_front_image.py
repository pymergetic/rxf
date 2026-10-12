"""The front end in the image (docs/19): source in, the words of a Function out, and it runs.

`native/front/front.rxf` is the compiler front end as a program: what `tools/native/rxfc.py`
compiles from source is what it writes from the same source, byte for byte
(`tests/test_front_corpus.py`). This is the line docs/19 named as not started -- a front end in
the image -- and here it runs there. A machine of each kind takes the source of
`selfedit/triple.rxf` through the request of `native/front/words.rxf`, writes the words `rxfc.py`
writes, and those words become the Function `selfedit.triple`: one delta over the channel, the
image applies it on its journal and relinks itself. The new Function runs on the interpreter and
then as the code `tiny.compile` makes of its words, both without a restart.

The source reaches the image as memory its harness writes (a provider of the channel, docs/19);
the Function travels as a physical delta through the rings, like every change of the image.
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

FRONT = Path(__file__).parents[1] / "native" / "front" / "front.rxf"
TRIPLE = Path(__file__).parent / "selfedit" / "triple.rxf"
FRONT_XML = Path(__file__).parent / "selfedit" / "front.xml"
TRIPLE_XML = Path(__file__).parent / "selfedit" / "triple.xml"
FRONT_FUNCTION = "pymergetic.rxf.selfedit.front"
TRIPLE_FUNCTION = "pymergetic.rxf.selfedit.triple"
TINY_FUNCTION = "pymergetic.rxf.foundation.tiny.compile"

N = 7  # the argument of the Function the image compiles and runs: triple(7) is 21
SCRATCH = (
    12 << 20
)  # the work memory of a request in the live block (link.LiveSpec.scratch)
FRONT_WORK = 8_963_144  # WORK_BYTES of native/front/words.rxf
TINY_WORK = (
    w.WORK_BYTES
)  # and of native/tiny (tools/native/rxfc.py keeps the numbers here)
OUT_ROOM = 2 << 20  # the words of a program, and the code tiny.compile writes of them


def authored(template: Path, path: str, program: bytes, source: bytes) -> str:
    """The delta that installs `path` with `program`, and the digest of what it means: its source."""
    ET.register_namespace("rxf", graph_sync.RXF)
    document = ET.parse(template)
    function = next(document.getroot().iter("Function"))
    function.set("semantic_digest", hashlib.sha256(source).hexdigest())
    function.append(graph_sync.bytecode(function, path, program))
    return ET.tostring(document.getroot(), encoding="unicode")


def identity(image: Image, path: str) -> int:
    handle = image.resolve(path)
    assert handle is not None
    return compose.object_id(image, handle)


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
    spec = link.LiveSpec(headroom=8 << 20, stage=8 << 20, scratch=SCRATCH)
    machine = lw.machine_of(request.param, image, spec)
    try:
        yield request.param, lw.Channel(machine)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_the_image_compiles_the_source_and_the_function_it_makes_runs(
    running, base: bytes
):
    name, channel = running
    machine = channel.machine
    live = machine.linked.live
    assert live is not None
    expected = Image.from_bytes(base)

    # the front end becomes a Function of the image. The host makes these words: rxfc.py and the
    # front end agree byte for byte on every source, so this one delta needs no in-image run.
    front_text = authored(
        FRONT_XML, FRONT_FUNCTION, rxfc.compile_file(FRONT), FRONT.read_bytes()
    )
    expected.apply(front_text)
    compiled = physical.from_xml(Image.from_bytes(base), ("front.xml", front_text))
    assert compiled.creates > 0
    assert channel.client.apply(compiled.data) == expected.digest()

    # the request of native/front/words.rxf in the memory of the machine: the source, the table of
    # the numbers it may use, and the two regions it writes (the work region behind the lookup
    # table of the object layer, and the emit region)
    w_addr, w_cap = live.regions["work"]
    e_addr, e_cap = live.regions["emit"]
    free = w_addr + (w_cap - SCRATCH)
    request_at, consts_at, names_at, source_at = (
        free,
        free + 0x100,
        free + 0x1000,
        free + 0x2000,
    )
    work_at, out_at = free + 0x100000, e_addr
    assert w_cap >= SCRATCH and e_cap >= 0x500000
    assert SCRATCH - 0x100000 >= FRONT_WORK

    entries = b""
    names = b""
    offset = 0
    numbers = sorted(graph_header.numbers().items())
    for cname, value in numbers:
        encoded = cname.encode() + b"\0"
        entries += struct.pack("<4Q", names_at + offset, len(cname), value, 0)
        names += encoded
        offset += len(encoded)
    source = TRIPLE.read_bytes()
    machine.write(consts_at, entries)
    machine.write(names_at, names)
    machine.write(source_at, source)
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
        identity(expected, f"{FRONT_FUNCTION}.bytecode"),
        struct.pack("<2Q", 0, request_at),
    )
    assert status == 0
    out_size, at = struct.unpack("<2Q", machine.read(request_at + 48, 16))
    assert at == 0 and out_size > 0
    words = machine.read(out_at, out_size)
    assert words == rxfc.compile_file(TRIPLE)

    # the words become the Function the source names: one delta over the channel, then it runs
    triple_text = authored(TRIPLE_XML, TRIPLE_FUNCTION, words, source)
    expected.apply(triple_text)
    before = Image.from_bytes(base)
    before.apply(front_text)  # the state the machine is in now
    compiled = physical.from_xml(before, ("triple.xml", triple_text))
    assert channel.client.apply(compiled.data) == expected.digest()
    assert channel.loaded().digest() == expected.digest()
    status, value = machine.run(
        identity(expected, f"{TRIPLE_FUNCTION}.bytecode"), struct.pack("<Q", N)
    )
    assert status == 0 and value == 3 * N

    # tiny.compile, the compiler of the image that is a Function of it, makes machine code of the
    # same words and the code answers the same. A commit writes the canonical image to the emit
    # region (the big routines of the pump run in one place, native/live.cpp), so the harness, a
    # provider of the channel, puts the words of the Function there again for the request to name.
    machine.write(out_at, words)
    code_at, twork_at = e_addr + 0x200000, e_addr + 0x400000
    table_at = machine.linked.base + machine.linked.table[0]
    count = (machine.linked.table[1] - machine.linked.table[0]) // 24
    machine.write(
        request_at,
        struct.pack(
            "<11Q",
            out_at,
            len(words),
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
