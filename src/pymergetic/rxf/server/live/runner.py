"""The runner of `rxf serve`: the real running image behind the app (docs/19, "who runs when").

One link, one runner: this module runs ONE image in this process — its own interpreter and its
own live leaf, natively, in the memory the image is linked into — and registers its world at the
link the CLI names. A run is not a frame: whoever runs the image is the host of its Functions,
so `rxf console --link=SPEC` and the editor drive the front end **of that image** through the
one seam (`server/live/session.py`) while the app serves, and shutting down takes the
registration with it (`Session.close` is atexit-safe).

The seed is the one docs/19 allows (the fixpoint): the host compiler (`tools/native/rxfc.py`)
writes the words of the front end (`native/front/front.rxf`) once, and the image compiles
everything from then on. The host tools are scripts of the checkout, not a package on any
search path — they are loaded from their path here, the way `rxf` loads its own command
modules (`pymergetic/rxf/cli.py`); a checkout-less install is refused plainly instead of booted
half way.

The machine here is a **linked image**, not `live.harness.HostImage`. `HostImage` runs the live
leaf over a `core.live.Area` and serves frames, but it exposes no memory read/write and no
`run`, so no `Front` can be built on it (and the request memory of a run must be the memory the
machine runs in). What runs here is what `tests/test_console.py` calls `seat_of`, in production
code: the host linker `tools/native/link.py` (the one whose bytes the image's own RELINK
writes) lays the image out in one block of host memory at its own addresses, and its
interpreter (`graph.run`) and its live pump (leaf 29) run natively there. The regions the front
end needs are the regions of that live block (`linked.live.regions`), derived exactly as
`seat_of` derives them, with the same SCRATCH pattern.
"""

from __future__ import annotations

import ctypes
import functools
import importlib.util
import mmap
import platform
import struct
import sys
from pathlib import Path
from typing import Any, Self

import pymergetic.rxf
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live.client import Client
from pymergetic.rxf.live.front import Front, Region
from pymergetic.rxf.live.link import RingLink
from pymergetic.rxf.server.live.session import Session

SCRATCH = (
    12 << 20
)  # the work memory of one request: its rows, then FRONT_WORK (words.rxf)
GROWTH = (
    8 << 20
)  # what the arena, the log and the stage grow by (link.LiveSpec in the tests)
NATIVE_MACHINES = {"x86_64": "x86_64", "aarch64": "aarch64"}


def _checkout() -> Path:
    """The checkout the seed needs: `tools/native` (the host compilers) and `native/front` (the
    source of the front end). A wheel install ships neither and is refused, not faked."""
    for start in (Path(__file__).resolve(), Path(pymergetic.rxf.__file__).resolve()):
        for parent in start.parents:
            if (parent / "tools" / "native" / "rxfc.py").is_file() and (
                parent / "native" / "front" / "front.rxf"
            ).is_file():
                return parent
    raise RuntimeError(
        "rxf serve needs the checkout to boot the image: tools/native (the host compiler that "
        "seeds the front end, docs/19) and native/front/front.rxf are not in this install"
    )


_NEEDS: dict[str, tuple[str, ...]] = {
    "compose": ("graph_header",),
    "rxfc": ("graph_header",),
    "link": ("graph_header", "compose"),
}


def _tool(name: str) -> Any:
    """One module of the host tools (`tools/native`), loaded from its path, the way `rxf` loads
    its own commands: they are scripts of the checkout with bare import names (`rxfc` imports
    `graph_header`, `link` imports `compose`), so each is registered in `sys.modules` under that
    name as it loads, and the ones that bare-import each other are loaded first (`_NEEDS`). A
    name that is already imported is the very module (the test suite runs these same files as
    bare names)."""
    found = sys.modules.get(name)
    if found is not None:
        return found
    for need in _NEEDS.get(name, ()):
        _tool(need)
    path = _checkout() / "tools" / "native" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@functools.cache
def _seed() -> tuple[bytes, bytes, dict[str, int]]:
    """The seed the fixpoint of docs/19 allows: the words of the front end from the host
    compiler (`rxfc.compile_file`), the source that made them, and the numbers the source may
    use (`graph_header`). What the image compiles after this one boot is the image's own work."""
    rxfc = _tool("rxfc")
    header = _tool("graph_header")
    source = _checkout() / "native" / "front" / "front.rxf"
    return rxfc.compile_file(source), source.read_bytes(), header.numbers()


class _Handle(ctypes.Structure):
    """`rxf_object_handle` (native/stdlib_runtime.h): the id a run names a program by."""

    _fields_ = [
        ("object_id", ctypes.c_uint64),
        ("generation", ctypes.c_uint64),
        ("offset", ctypes.c_uint64),
    ]


_RUN = ctypes.CFUNCTYPE(
    ctypes.c_uint32,  # rxf_graph_run(...)
    ctypes.c_void_p,  # rxf_runtime_context *
    _Handle,  # rxf_object_handle, by value
    ctypes.c_uint64,  # args
    ctypes.c_uint64,  # argc
    ctypes.c_void_p,  # out
)
_LEAF = ctypes.CFUNCTYPE(
    ctypes.c_uint32,  # a leaf (context, argument)
    ctypes.c_void_p,
    ctypes.c_void_p,
)


@functools.cache
def _run_at(address: int) -> Any:
    """The interpreter at `address`, as a callable of this process — the entry `link.py`
    computes from the Code of the image (a compaction moves it with everything else that does
    not run)."""
    return _RUN(address)


@functools.cache
def _leaf_at(address: int) -> Any:
    """The leaf at `address`, as a callable of this process."""
    return _LEAF(address)


@functools.cache
def _pump_id() -> int:
    """The id of the live leaf (leaf 29, `native/graph.h`): what serves the rings."""
    return int(_tool("graph_header").numbers()["RXF_LEAF_LIVE_PUMP"])


class Seat:
    """One linked image whose own code runs natively in this process.

    The image is linked (`tools/native/link.py`) into one block of host memory at its own
    addresses, so its interpreter, its leaves and its live pump run here as they run on a
    machine. `read`/`write` are memory by address (what `Front` writes its request through),
    `run` is the interpreter on one program, and `pump` is the image's own live leaf serving
    its rings — the frames face and the world face of one link, not two.

    The block is what `tests/live_world.py` maps for its machines, run natively instead of in an
    emulator: the code of the starter is relocation-free (`native_build.py`), which is what lets
    it run where the arena keeps it. The image moves its own code when it compacts itself, so a
    leaf is looked up in the function table of the context each time it is called (`slot`).
    """

    def __init__(self, image: Image) -> None:
        machine = NATIVE_MACHINES.get(platform.machine())
        if machine is None:
            raise RuntimeError(
                f"rxf serve runs the image on this host's own machine: {platform.machine()} "
                "has no linked form to run here (x86_64 and aarch64 do)"
            )
        linking = _tool("link")
        spec = linking.LiveSpec(
            headroom=GROWTH, log=GROWTH, stage=GROWTH, scratch=SCRATCH
        )
        # One pass links at a placeholder to learn the size of the block, one at the address of
        # the mapping: linking computes every address from the base it is given.
        probe = linking.link(image, machine, 0, spec)
        self._memory = mmap.mmap(
            -1,
            max(len(probe.data), 1),
            prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC,
        )
        self.base = ctypes.addressof(ctypes.c_char.from_buffer(self._memory))
        self.linked = linking.link(image, machine, self.base, spec)
        self._memory[: len(self.linked.data)] = self.linked.data
        self._closed = False
        assert self.linked.live is not None
        live = self.linked.live
        self.client = Client(RingLink(self, live.area), pump=self.pump)
        arena_at, arena_cap = live.regions["arena"]
        work_at, work_cap = live.regions["work"]
        self.front = Front(
            self.client,
            self,
            Region(arena_at, arena_cap),
            Region(work_at + (work_cap - SCRATCH), SCRATCH),
        )

    # -- the machine of the front end (live.front.Machine) -----------------------------------

    def read(self, address: int, size: int) -> bytes:
        at = address - self.base
        return bytes(self._memory[at : at + size])

    def write(self, address: int, data: bytes) -> None:
        at = address - self.base
        self._memory[at : at + len(data)] = data

    def slot(self, identity: int) -> int:
        """Where a leaf is now: the address in the function table of the context in memory (the
        image moves its own code when it compacts itself)."""
        count, table = struct.unpack("<2Q", self.read(self.linked.context + 16, 16))
        for k in range(count):
            slot = self.read(table + 24 * k, 24)
            if struct.unpack_from("<Q", slot)[0] == identity:
                return (
                    struct.unpack_from("<Q", slot, 16)[0] or self.linked.slots[identity]
                )
        raise KeyError(identity)

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]:
        """The interpreter of the image on one program (`graph.run`): its status and its value.
        A run is not a frame: this call IS the host running the image (docs/19)."""
        self._check_open()
        self.write(self.linked.args, arguments or bytes(8))
        status = _run_at(self.linked.run)(
            self.linked.context,
            _Handle(identity, 1, 0),
            self.linked.args,
            len(arguments) // 8,
            self.linked.out,
        )
        return status, struct.unpack("<Q", self.read(self.linked.out, 8))[0]

    # -- the channel of the image ------------------------------------------------------------

    def pump(self) -> int:
        """The image's own live leaf (leaf 29, `native/live.cpp`) serves its rings."""
        self._check_open()
        assert self.linked.live is not None
        return _leaf_at(self.slot(_pump_id()))(
            self.linked.context, self.linked.live.area
        )

    def close(self) -> None:
        """The seat is over: the block is unmapped and no call goes in again. Nothing outlives
        the machine that runs the image (docs/19, "who runs when")."""
        if self._closed:
            return
        self._closed = True
        self._memory.close()

    # -- internals ----------------------------------------------------------------------------

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "the seat is closed: this process no longer runs the image"
            )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *problem: object) -> None:
        self.close()


class Runner:
    """The running image behind `rxf serve`: one seat, one channel, one front end, one link.

    `session` is the world `rxf console --link=SPEC` attaches to — opened here, because whoever
    runs the image registers its world (docs/19, "who runs when"). A link whose image already
    runs answers `WorldIsRegistered` and keeps its runner; `close` takes the registration and
    the machine with it (also at exit, through `Session.close`).
    """

    def __init__(self, image: Image, link: str = "shm:rxf") -> None:
        self.link = link
        self._seat = Seat(image)
        self.session = Session(link, self._seat.client, self._seat.front)
        try:
            self.session.open()  # one link, one runner: a taken link refuses here
            words, source, consts = _seed()
            self._seat.front.boot(
                words, source, consts
            )  # the seed docs/19 allows, once
        except BaseException:
            self.close()
            raise

    @property
    def client(self) -> Client:
        """The channel of the running image: what `rxf live` speaks frames on."""
        return self._seat.client

    @property
    def machine(self) -> Seat:
        """The machine the image runs on: its memory and its interpreter (docs/19)."""
        return self._seat

    @property
    def front(self) -> Front:
        """The front end of the running image: what a console or the editor compiles through."""
        return self._seat.front

    def close(self) -> None:
        """The run is over: the link gives no runner again and the machine is gone with it."""
        self.session.close()
        self._seat.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *problem: object) -> None:
        self.close()
