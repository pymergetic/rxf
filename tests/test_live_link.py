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

import pytest

from pymergetic.rxftk import source
from pymergetic.rxftk.cli import main
from pymergetic.rxftk.core import live, tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import link, physical
from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.harness import HostImage, table_slots

from . import live_serve
from .snippets import STARTER

MiB = 1 << 20
SIZES = {
    "arena": 8 * MiB,
    "log": MiB,
    "stage": MiB,
    "work": MiB,
    "emit": 8 * MiB,
    "ring": 1 << 16,
}

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
    return tree.load(Path(STARTER))


@pytest.fixture
def context():
    return multiprocessing.get_context("spawn")


@pytest.fixture
def shm(image: Image, context):
    raw = image.to_bytes()
    size = live.Area.required_size(
        SIZES["arena"],
        SIZES["log"],
        SIZES["stage"],
        SIZES["work"],
        SIZES["emit"],
        SIZES["ring"],
    )
    segment = multiprocessing.shared_memory.SharedMemory(create=True, size=size)
    assert segment.buf is not None
    area = live.Area.shared(
        segment.buf,
        raw,
        SIZES["arena"],
        SIZES["log"],
        SIZES["stage"],
        SIZES["work"],
        SIZES["emit"],
        SIZES["ring"],
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


def test_two_processes_share_one_image_over_the_rings(shm, image: Image):
    name, raw = shm
    wire = link.SharedMemory(name)
    client = Client(wire)
    assert client.ping(b"across processes") == b"across processes"
    assert client.digest() == image.digest()
    host = HostImage(raw)
    assert client.hello().handles == host.client.hello().handles
    wire.close()


def test_a_change_made_from_the_other_process_is_in_the_shared_image(
    shm, image: Image, tmp_path
):
    name, raw = shm
    delta = tmp_path / "wire.rxfd"
    delta.write_text(DELTA, encoding="utf-8")
    wire = link.SharedMemory(name)
    client = Client(wire)
    expected = Image.from_bytes(raw)
    expected.apply(DELTA)
    compiled = physical.from_xml(Image.from_bytes(raw), ("wire.rxfd", DELTA))
    assert client.apply(compiled.data) == expected.digest()
    # a second client of the same segment sees the change too
    other = link.SharedMemory(name)
    assert Client(other).digest() == expected.digest() != image.digest()
    other.close()
    assert client.emit()[0] == expected.compact().to_bytes()
    wire.close()


@pytest.fixture
def serial(image: Image):
    master, slave = pty.openpty()
    path = os.ttyname(slave)
    tty.setraw(
        slave
    )  # before anything is written: no echo of the answers back to the server
    stop, ready = threading.Event(), threading.Event()
    server = threading.Thread(
        target=live_serve.serve_descriptor,
        args=(master, image.to_bytes(), stop, ready, SIZES["arena"], SIZES["emit"]),
    )
    server.start()
    assert ready.wait(60)
    yield path, master
    stop.set()
    server.join(30)
    os.close(master)
    os.close(slave)


def test_a_client_talks_to_the_image_over_a_pty(serial, image: Image):
    path, _ = serial
    wire = link.open_link(f"pty:{path}")
    client = Client(wire, timeout=60)
    assert client.ping(b"over a line") == b"over a line"
    assert client.digest() == image.digest()
    wire.close()


def test_the_rxf_live_commands_work_on_a_pty(serial, image: Image, tmp_path, capsys):
    path, _ = serial
    spec = f"pty:{path}"
    delta = tmp_path / "wire.rxfd"
    delta.write_text(DELTA, encoding="utf-8")
    assert main(["live", "digest", spec, "--timeout=60"]) == 0
    assert capsys.readouterr().out.strip().strip("'") == image.digest()
    assert main(["live", "apply", spec, str(delta), "--timeout=60"]) == 0
    applied = capsys.readouterr().out
    expected = Image.from_bytes(image.to_bytes())
    expected.apply(DELTA)
    assert expected.digest() in applied
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
    assert out[0] == "hi there" and out[1] == expected.digest()
    assert out[2].isdigit() and out[3].startswith("error: ")
    assert json.loads("\n".join(out[4:])) == {"commands": 4}  # `quit` ended it


def test_the_rxf_live_commands_work_on_shared_memory(
    shm, image: Image, tmp_path, capsys
):
    name, _ = shm
    spec = f"shm:{name}"
    assert main(["live", "info", spec]) == 0
    assert image.digest() in capsys.readouterr().out
    assert main(["live", "ping", spec, "wire"]) == 0
    assert "wire" in capsys.readouterr().out
    assert main(["live", "halt", spec]) == 0
    capsys.readouterr()
    assert main(["live", "go", spec]) == 0
    capsys.readouterr()


def test_a_bad_link_is_unusable(capsys):
    assert main(["live", "digest", "nothing:here"]) == 2
