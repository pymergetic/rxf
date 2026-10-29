"""One image, several wires: shared memory between two processes, and a pty (a serial line)."""

import gc
import json
import multiprocessing
import multiprocessing.shared_memory
import os
import pty
import threading
import tty
from pathlib import Path

import link as link_
import pytest

from pymergetic.rxftk import source
from pymergetic.rxftk.cli import main
from pymergetic.rxftk.core import live, tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import link, physical
from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.harness import HostImage, fit, table_slots

from . import live_serve
from .snippets import STARTER
from .support import canonical_of, digest_of

MiB = 1 << 20
SIZES = {
    "arena": 8 * MiB,
    "log": MiB,
    "stage": MiB,
    "work": MiB,
    "emit": 8 * MiB,
    "ring": 1 << 16,
}


def sized(raw: bytes) -> dict:
    """The area sizes with arena and emit holding this image (fit: what the linker gives it).
    The starter grows; these are floors, never a ceiling the image cannot fit in."""
    return {**SIZES, "arena": fit(raw), "emit": fit(raw)}

DELTA = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Module name="wire" rxf:parent="pymergetic.rxf" category="ModuleCategory.Application">
    <uint32_t name="seven" rxf:data="07000000" />
  </Module>
  <rxf:set path="pymergetic.rxf.application.discount" rxf:data="2d000000"/>
</delta>
"""


@pytest.fixture(scope="module")
def image() -> Image:
    # The world the engine runs: the filled model (tools/native/programs.py),
    # whose v7 arena (link.arena_of) is the form the seats serve.
    import programs

    return programs.filled(tree.load(Path(STARTER)))


@pytest.fixture
def context():
    return multiprocessing.get_context("spawn")


@pytest.fixture
def shm(image: Image, context):
    raw = link_.arena_of(image)
    sizes = sized(raw)
    size = live.Area.required_size(
        sizes["arena"],
        sizes["log"],
        sizes["stage"],
        sizes["work"],
        sizes["emit"],
        sizes["ring"],
    )
    segment = multiprocessing.shared_memory.SharedMemory(create=True, size=size)
    assert segment.buf is not None
    area = live.Area.shared(
        segment.buf,
        raw,
        sizes["arena"],
        sizes["log"],
        sizes["stage"],
        sizes["work"],
        sizes["emit"],
        sizes["ring"],
        table_slots(raw),
    )
    stop, ready = context.Event(), context.Event()
    child = context.Process(
        target=live_serve.serve_shared, args=(segment.name, stop, ready)
    )
    child.start()
    assert ready.wait(60)
    # `shared` only lays the memory out. The area holds absolute addresses and the one that attaches
    # makes them its own, so the creator lets go of it and the clients talk through the rings.
    del area
    yield segment.name, raw
    stop.set()
    child.join(30)
    assert child.exitcode == 0
    gc.collect()
    segment.close()
    segment.unlink()


@pytest.mark.xfail(
    reason="blocked on the arena lane: the engine's dump/apply answers 'image "
    "not usable' while the rebuild/canonical-walk work is in progress",
    strict=False,
)
def test_two_processes_share_one_image_over_the_rings(shm, image: Image):
    name, raw = shm
    wire = link.SharedMemory(name)
    client = Client(wire)
    assert client.ping(b"across processes") == b"across processes"
    assert client.digest() == digest_of(image)
    host = HostImage(raw)
    assert client.hello().handles == host.client.hello().handles
    wire.close()


@pytest.mark.xfail(
    reason="blocked on the arena lane: the engine's dump/apply answers 'image "
    "not usable' while the rebuild/canonical-walk work is in progress",
    strict=False,
)
def test_a_change_made_from_the_other_process_is_in_the_shared_image(
    shm, image: Image, tmp_path
):
    name, _raw = shm
    delta = tmp_path / "wire.rxfd"
    delta.write_text(DELTA, encoding="utf-8")
    wire = link.SharedMemory(name)
    client = Client(wire)
    expected = Image.from_bytes(image.to_bytes())
    expected.apply(DELTA)
    compiled = physical.from_xml(image, ("wire.rxfd", DELTA), check_base=False)
    assert client.apply(compiled.data) == digest_of(expected)
    # a second client of the same segment sees the change too
    other = link.SharedMemory(name)
    assert Client(other).digest() == digest_of(expected) != digest_of(image)
    other.close()
    assert client.emit()[0] == canonical_of(expected)
    wire.close()


@pytest.fixture
def serial(image: Image):
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    tty.setraw(
        slave
    )  # before anything is written: no echo of the answers back to the server
    stop, ready = threading.Event(), threading.Event()
    raw = link_.arena_of(image)
    sizes = sized(raw)
    server = threading.Thread(
        target=live_serve.serve_descriptor,
        args=(master, raw, stop, ready, sizes["arena"], sizes["emit"]),
    )
    server.start()
    assert ready.wait(60)
    yield path, master
    stop.set()
    server.join(30)
    os.close(master)
    os.close(slave)


@pytest.mark.xfail(
    reason="blocked on the arena lane: the engine's dump answers 'image not "
    "usable' while the rebuild/canonical-walk work is in progress",
    strict=False,
)
def test_a_client_talks_to_the_image_over_a_pty(serial, image: Image):
    path, _ = serial
    wire = link.open_link(f"pty:{path}")
    client = Client(wire, timeout=60)
    assert client.ping(b"over a line") == b"over a line"
    assert client.digest() == digest_of(image)
    wire.close()


@pytest.mark.xfail(
    reason="blocked on the arena lane: the engine's dump/apply answers 'image "
    "not usable' while the rebuild/canonical-walk work is in progress",
    strict=False,
)
def test_the_rxf_live_commands_work_on_a_pty(serial, image: Image, tmp_path, capsys):
    path, _ = serial
    spec = f"pty:{path}"
    delta = tmp_path / "wire.rxfd"
    delta.write_text(DELTA, encoding="utf-8")
    assert main(["live", "digest", spec, "--timeout=60"]) == 0
    assert capsys.readouterr().out.strip().strip("'") == digest_of(image)
    assert main(["live", "apply", spec, str(delta), "--timeout=60"]) == 0
    applied = capsys.readouterr().out
    expected = Image.from_bytes(image.to_bytes())
    expected.apply(DELTA)
    assert digest_of(expected) in applied
    # the dump of the running image, to the three forms
    for name in ("run.rxfd", "run.rxfb", "run"):
        target = tmp_path / name
        assert main(["live", "emit", spec, str(target), "--timeout=60"]) == 0
        capsys.readouterr()
        assert source.load(target).digest() == expected.digest()
    # a running image is a source like any other
    assert source.load(f"live:{spec}").digest() == expected.digest()
    # the console reads a script
    script = tmp_path / "script.txt"
    script.write_text(
        "ping hi there\ndigest\nresolve pymergetic.rxf.wire.seven\nnonsense\nquit\ndigest\n"
    )
    assert main(["live", "console", spec, f"--script={script}", "--timeout=60"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "hi there" and out[1] == digest_of(expected)
    assert out[2].isdigit() and out[3].startswith("error: ")
    assert json.loads("\n".join(out[4:])) == {"commands": 4}  # `quit` ended it


@pytest.mark.xfail(
    reason="blocked on the arena lane: the engine's dump answers 'image not "
    "usable' while the rebuild/canonical-walk work is in progress",
    strict=False,
)
def test_the_rxf_live_commands_work_on_shared_memory(
    shm, image: Image, tmp_path, capsys
):
    name, _ = shm
    spec = f"shm:{name}"
    assert main(["live", "info", spec]) == 0
    assert digest_of(image) in capsys.readouterr().out
    assert main(["live", "ping", spec, "wire"]) == 0
    assert "wire" in capsys.readouterr().out
    assert main(["live", "halt", spec]) == 0
    capsys.readouterr()
    assert main(["live", "go", spec]) == 0
    capsys.readouterr()


def test_a_bad_link_is_unusable(capsys):
    assert main(["live", "digest", "nothing:here"]) == 2
