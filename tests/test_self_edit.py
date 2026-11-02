"""The first self-edit (docs/19, WP11): a running image takes a new Function made of existing ones.

The Function `selfedit.double` is a graph of calls to `library.checkout_total` and `checked.add`.
The host composes it into the program the interpreter runs (the compiler is the host's until WP12)
and sends it as one delta. The image applies it and relinks itself, and the new Function runs, on
the interpreter, without a restart. The image then dumps a successor; a machine started from the
dump runs the Function too, and the dump is editable XML that gives the same digest on the host.
"""

import hashlib
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import compose
import graph_sync
import pytest

from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import physical
from pymergetic.rxftk.live.front import model_of

from . import live_world as lw
from .snippets import running_image
from .support import digest_of

pytestmark = [pytest.mark.nightly, pytest.mark.slow]

DOUBLE = Path(__file__).parent / "selfedit" / "double.rxfd"
FUNCTION = "pymergetic.rxf.selfedit.double"
MAIN = "pymergetic.rxf.application.main"
TOTAL = 570
MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]


@pytest.fixture(scope="module")
def image() -> Image:
    return running_image()


@pytest.fixture(scope="module")
def base(image: Image) -> bytes:
    return image.to_bytes()


def authored(base: Image) -> str:
    """The delta of the new Function with the program of its graph (what the host composes)."""
    ET.register_namespace("rxf", graph_sync.RXF)
    document = ET.parse(DOUBLE)
    function = next(document.getroot().iter("Function"))
    # the digest of what the graph means: here, of the graph as written
    function.set("semantic_digest", hashlib.sha256(DOUBLE.read_bytes()).hexdigest())
    probe = model_of(base.to_bytes())
    probe.apply(DOUBLE.read_text(encoding="utf-8"))
    program = compose.programs(probe)[FUNCTION]
    function.append(graph_sync.bytecode(function, FUNCTION, program))
    return ET.tostring(document.getroot(), encoding="unicode")


def number(result: tuple[int, int]) -> int:
    status, value = result
    assert status == 0
    return value & 0xFFFFFFFF


def identity(image: Image, path: str) -> int:
    handle = image.resolve(path)
    assert handle is not None
    return compose.object_id(image, handle)


@pytest.fixture(params=MACHINES)
def running(request: pytest.FixtureRequest, image: Image):
    machine = lw.machine_of(request.param, image)
    try:
        yield request.param, lw.Channel(machine)
    finally:
        if hasattr(machine, "close"):
            machine.close()


def test_the_image_takes_a_function_of_existing_ones_and_the_successor_runs_it(
    running, base: bytes
):
    name, channel = running
    machine = channel.machine
    start = model_of(base)
    assert number(machine.run(identity(start, f"{MAIN}.bytecode"))) == TOTAL
    # the Function is not there: the interpreter finds no program under its id
    expected = model_of(base)
    text = authored(expected)
    expected.apply(text)
    new = identity(expected, f"{FUNCTION}.bytecode")
    assert machine.run(new)[0] != 0

    # one delta, applied by the image, which writes the entry of the new program itself
    compiled = physical.from_xml(model_of(base), ("double.rxfd", text))
    assert compiled.creates > 0
    assert channel.client.apply(compiled.data) == digest_of(expected)
    assert number(machine.run(new)) == 2 * TOTAL
    assert number(machine.run(identity(start, f"{MAIN}.bytecode"))) == TOTAL

    # the successor is the dump: what the host computes, and a machine started from it runs it
    successor, digest = channel.client.emit()
    assert successor == expected.compact().to_bytes()
    assert digest == digest_of(expected)
    fresh = lw.machine_of(name, model_of(successor))
    try:
        assert number(fresh.run(new)) == 2 * TOTAL
    finally:
        if hasattr(fresh, "close"):
            fresh.close()

    # the dump is editable XML with the same digest on the host
    exported = model_of(successor).export()
    assert '<Function name="double"' in exported
    again = Image.empty()
    again.apply(exported)
    assert digest_of(again) == digest_of(expected)

    # edit the XML (a unit price from 125 to 135), send the difference to the running image
    edited_text = exported.replace(
        'name="unit_price" rxf:data="7d000000"', 'name="unit_price" rxf:data="87000000"'
    )
    assert edited_text != exported
    edited = Image.empty()
    edited.apply(edited_text)
    change = physical.compile(model_of(successor), edited)
    assert channel.client.apply(change.data) == digest_of(edited)
    total = number(machine.run(identity(start, f"{MAIN}.bytecode")))
    assert total != TOTAL
    assert number(machine.run(new)) == 2 * total
