"""The runner of `rxf serve`: the real running image behind the app (docs/19, "who runs when").

One link, one runner: this module runs ONE image in this process — its own interpreter and its
own live leaf, natively, in the memory the image is linked into — and registers its world at the
link the CLI names. A run is not a frame: whoever runs the image is the host of its Functions,
so `rxf console --link=SPEC` and the editor drive the front end **of that image** through the
one seam (`server/live/session.py`) while the app serves, and shutting down takes the
registration with it (`Session.close` is atexit-safe).

The seed is the one docs/19 allows (the fixpoint): the host compiler (`tools/native/rxfc.py`)
writes the words of the front end (`native/front/front.rxfs`) once, and the image compiles
everything from then on. The host tools are scripts of the checkout, not a package on any
search path — they are loaded from their path here, the way `rxf` loads its own command
modules (`pymergetic/rxftk/cli.py`); a checkout-less install is refused plainly instead of booted
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

import atexit
import ctypes
import functools
import importlib.util
import mmap
import platform
import sys
import threading
from multiprocessing import shared_memory
from pathlib import Path
from typing import Any, ClassVar, Self

import pymergetic.rxftk
from pymergetic.rxftk.console.world import WorldIsRegistered
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live.client import Client
from pymergetic.rxftk.live.front import Front
from pymergetic.rxftk.live.link import (
    HEAD_SIZE,
    Head,
    SegmentGate,
    create_segment,
    segment_of,
)
from pymergetic.rxftk.live.machine import Machine
from pymergetic.rxftk.server.live.session import Session

SCRATCH = (
    12 << 20
)  # the work memory of one request: its rows, then FRONT_WORK (words.rxfs)
GROWTH = (
    8 << 20
)  # what the arena, the log and the stage grow by (link.LiveSpec in the tests)
NATIVE_MACHINES = {"x86_64": "x86_64", "aarch64": "aarch64"}


def _checkout() -> Path:
    """The checkout the seed needs: `tools/native` (the host compilers) and `native/front` (the
    source of the front end). A wheel install ships neither and is refused, not faked."""
    for start in (Path(__file__).resolve(), Path(pymergetic.rxftk.__file__).resolve()):
        for parent in start.parents:
            if (parent / "tools" / "native" / "rxfc.py").is_file() and (
                parent / "native" / "front" / "front.rxfs"
            ).is_file():
                return parent
    raise RuntimeError(
        "rxf serve needs the checkout to boot the image: tools/native (the host compiler that "
        "seeds the front end, docs/19) and native/front/front.rxfs are not in this install"
    )


_NEEDS: dict[str, tuple[str, ...]] = {
    "compile_build": ("graph_header", "seed_build", "seed_cache"),
    "compose": ("graph_header", "seed_build"),
    "graph_build": ("graph_header", "seed_build", "seed_cache"),
    "graph_sync": ("graph_header",),
    "link": ("arena_export", "compose", "graph_header", "programs"),
    "live_build": ("seed_build", "seed_cache"),
    "native_build": ("seed_build",),
    "programs": (
        "compile_build",
        "graph_build",
        "graph_sync",
        "live_build",
        "native_build",
        "rxfc",
    ),
    "rxfc": ("graph_header",),
    "seed_build": ("seed_cache",),
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
    source = _checkout() / "native" / "front" / "front.rxfs"
    return rxfc.compile_file(source), source.read_bytes(), header.numbers()


@functools.cache
def _pump_id() -> int:
    """The id of the live leaf (leaf 29, `native/graph.h`): what serves the rings."""
    return int(_tool("graph_header").numbers()["RXF_LEAF_LIVE_PUMP"])


class Seat(Machine):
    """One linked image whose own code runs natively in this process.

    The image is linked (`tools/native/link.py`) into one block of host memory at its own
    addresses, so its interpreter, its leaves and its live pump run here as they run on a
    machine. `read`/`write` are memory by address (what `Front` writes its request through),
    `run` is the interpreter on one program, and `pump` is the image's own live leaf serving
    its rings — the frames face and the world face of one link, not two.

    With `share`, the block is laid out in a shared-memory segment of that name — the area the
    link `shm:NAME` promises — and the facts a peer maps it by (the head `live.link.Head` reads)
    are written at the end of it: another process maps the very same block at the very same
    addresses and runs the very same image (`live/machine.py`), one request at a time behind the
    lock of the link. One link has one runner across processes too: a segment name that is
    already taken is refused `WorldIsRegistered` and keeps its runner.

    The block is what `tests/live_world.py` maps for its machines, run natively instead of in an
    emulator: the code of the starter is relocation-free (`native_build.py`), which is what lets
    it run where the arena keeps it. The image moves its own code when it compacts itself, so a
    leaf is looked up in the function table of the context each time it is called (`slot`).
    """

    def __init__(self, image: Image, share: str | None = None) -> None:
        native = NATIVE_MACHINES.get(platform.machine())
        if native is None:
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
        probe = linking.link(image, native, 0, spec)
        payload = max(len(probe.data), 1)
        self._segment: shared_memory.SharedMemory | None = None
        gate: SegmentGate | None = None
        memory: mmap.mmap
        if share is None:
            memory = mmap.mmap(
                -1,
                payload,
                prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC,
            )
        else:
            self._segment = self._shared(share, ((payload + 7) & ~7) + HEAD_SIZE)
            memory = mmap.mmap(
                getattr(self._segment, "_fd"),  # noqa: B009
                self._segment.size,
                prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC,
            )
            gate = SegmentGate(getattr(self._segment, "_fd"))  # noqa: B009
        self.base = ctypes.addressof(ctypes.c_char.from_buffer(memory))
        self.linked = linking.link(image, native, self.base, spec)
        memory[: len(self.linked.data)] = self.linked.data
        assert self.linked.live is not None
        live = self.linked.live
        arena_at, arena_cap = live.regions["arena"]
        work_at, work_cap = live.regions["work"]
        pump = _pump_id()
        facts = Head(
            block=self.base,
            area=live.area,
            context=self.linked.context,
            run=self.linked.run,
            args=self.linked.args,
            out=self.linked.out,
            pump=pump,
            pump_slot=self.linked.slots[pump],
            arena_at=arena_at,
            arena_cap=arena_cap,
            work_at=work_at + (work_cap - SCRATCH),
            work_cap=SCRATCH,
        )
        if self._segment is not None:
            memory[-HEAD_SIZE:] = facts.pack()  # the head a peer maps the block by
        super().__init__(facts, memory, memory.close, gate)
        atexit.register(self.close)  # a seat that ends without closing leaves nothing behind

    # -- the shared-memory segment of a `shm:` link -------------------------------------------

    @staticmethod
    def _shared(name: str, size: int) -> shared_memory.SharedMemory:
        """The segment the link `shm:NAME` promises, laid out here. One link has one runner
        across processes: a name that is taken keeps its runner and refuses this one."""
        try:
            return create_segment(name, size)
        except FileExistsError:
            raise WorldIsRegistered(
                f"shm:{name} is already laid out: one link has one runner, and the runner "
                "that has it keeps the link until it closes (stop it, or remove the segment "
                f"when no runner is there: /dev/shm/{name})"
            ) from None

    def close(self) -> None:
        """The seat is over: the block is unmapped, and a segment it laid out is unlinked. Nothing
        outlives the machine that runs the image (docs/19, "who runs when")."""
        if self._closed:
            return
        super().close()
        atexit.unregister(self.close)
        if self._segment is not None:
            self._segment.close()
            self._segment.unlink()
            self._segment = None


# One link, one runner (docs/19, "who runs when"): the attach decision is taken here, atomically,
# so two attaches cannot both take one link — the link never has two runners registered or
# running traffic. The runner that is there keeps its link until it closes; a second attach is
# refused with the seam's own refusal (`WorldIsRegistered` of `console/world.py`), never shared
# and never stolen. (A refused attach has already built its own private machine and unmaps it
# again in `close`; it never touches the link.)
_ATTACH = threading.Lock()


class Runner:
    """The running image behind `rxf serve`: one seat, one channel, one front end, one link.

    `session` is the world `rxf console --link=SPEC` attaches to — opened here, because whoever
    runs the image registers its world (docs/19, "who runs when"). A link whose image already
    runs is refused `WorldIsRegistered` and keeps its runner — even when the two attaches race,
    the link ends up with exactly one runner, the one that reserved it first; `close` takes the
    registration and the machine with it (also at exit, through `Session.close`).
    """

    _at_link: ClassVar[dict[str, Runner]] = {}

    def __init__(self, image: Image, link: str = "shm:rxf") -> None:
        self.link = link
        # Its own machine; it runs no traffic until the world registers. A `shm:` link lays the
        # image out in a shared-memory segment, which is what makes the link's promise to
        # another process true (docs/19).
        self._seat = Seat(image, share=segment_of(link))
        self.session = Session(link, self._seat.client, self._seat.front)
        try:
            with _ATTACH:  # one link, one runner: reserved atomically, refused honestly
                if link in Runner._at_link:
                    raise WorldIsRegistered(
                        f"{link} already has a runner: one link has one runner, and the "
                        "runner that is there keeps its link until it closes"
                    )
                Runner._at_link[link] = self
            self.session.open()  # and its world registers at the link (a taken link refuses here)
            words, source, consts = _seed()
            with self.session.transaction():  # the boot is one transaction on the channel
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
        with _ATTACH:
            if Runner._at_link.get(self.link) is self:
                del Runner._at_link[self.link]
        self.session.close()
        self._seat.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *problem: object) -> None:
        self.close()
