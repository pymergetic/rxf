"""The live layer on the three machines: the linked image with its channel, run natively.

`link.link(image, machine, base, live=LiveSpec())` puts the area of the live layer behind the
image. A `Machine` is the memory of that image on one machine and a way to call the pump leaf
(function slot 29) in it: unicorn for x86_64 and aarch64, node for wasm32. `Channel` is the link a
`live.client.Client` talks over: it moves frames through the rx and tx rings in that memory.
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
from pathlib import Path
from typing import Protocol

import link

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live.client import Client

from . import graph_world as world
from .emulator import Blob, Emulator, Integer
from .wasm import module_of

HERE = Path(__file__).parent
HARNESS = HERE / "wasm_live.js"
PUMP = link.graph_header.numbers()["RXF_LEAF_LIVE_PUMP"]
EMULATED_BASE = 0x10000000
CODE_AT = 0x100000  # compiled code is put this far into the emit region
WASM_BASE = 0x100000
PAGE = 0x10000
# the rings and counters of core/live/live.h
RX_HEAD, RX_TAIL, TX_HEAD, TX_TAIL = 120, 124, 128, 132
FRAMES, COMMITS, REJECTS, LAST_STATUS = 152, 160, 168, 148


class Machine(Protocol):
    linked: link.Linked

    def close(self) -> None: ...

    def write(self, address: int, data: bytes) -> None: ...

    def read(self, address: int, size: int) -> bytes: ...

    def slot(self, identity: int) -> int:
        """Where the leaf with this id is now (a table index on wasm32)."""
        ...

    def pump(self, area: int | None = None) -> int:
        """Call the leaf with the context and the area (0: take it from the capability)."""
        ...

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]:
        """Run the program of the object `identity` in the interpreter: its status and its value."""
        ...

    def leaf(self, identity: int, argument: int | None = None) -> int:
        """Call the leaf with this id on the context and a pointer (default: the area of the channel)."""
        ...

    def call_code(
        self, code: bytes, arguments: bytes = b"", at: int | None = None
    ) -> tuple[int, int]:
        """Run compiled code (`status f(context, args, argc, out)`) on these arguments: status and
        value. `at` is where it is put (default: in the emit region, which a dump overwrites); a
        machine whose code is not in its memory (wasm32) ignores it."""
        ...


class Emulated:
    def __init__(self, machine: str, linked: link.Linked) -> None:
        self.linked = linked
        self.emulator = Emulator(machine)
        self.emulator.map(linked.base, len(linked.data))
        self.emulator.write(linked.base, linked.data)

    def close(self) -> None:
        """Nothing outlives an emulated machine."""

    def write(self, address: int, data: bytes) -> None:
        self.emulator.write(address, data)

    def read(self, address: int, size: int) -> bytes:
        return self.emulator.read(address, size)

    def slot(self, identity: int) -> int:
        """Where a leaf is now: the address in the function table of the context in memory (the
        image moves its own code when it compacts itself)."""
        context = self.linked.context
        count, table = struct.unpack("<2Q", self.read(context + 16, 16)[:16])
        for k in range(count):
            slot = self.read(table + 24 * k, 24)
            if struct.unpack_from("<Q", slot)[0] == identity:
                # a table that was wiped (a test of the relink) has no address: the one it was linked at
                return (
                    struct.unpack_from("<Q", slot, 16)[0] or self.linked.slots[identity]
                )
        raise KeyError(identity)

    def pump(self, area: int | None = None) -> int:
        assert self.linked.live is not None
        where = self.linked.live.area if area is None else area
        self.emulator.flush()
        return self.emulator.call(
            self.slot(PUMP), [Integer(self.linked.context), Integer(where)]
        )

    def leaf(self, identity: int, argument: int | None = None) -> int:
        assert self.linked.live is not None
        where = self.linked.live.area if argument is None else argument
        return self.emulator.call(
            self.slot(identity),
            [Integer(self.linked.context), Integer(where)],
        )

    def call_code(
        self, code: bytes, arguments: bytes = b"", at: int | None = None
    ) -> tuple[int, int]:
        linked = self.linked
        assert linked.live is not None
        at = linked.live.regions["emit"][0] + CODE_AT if at is None else at
        self.write(at, code)
        self.emulator.flush()
        self.write(linked.args, arguments or bytes(8))
        status = self.emulator.call(
            at,
            [
                Integer(linked.context),
                Integer(linked.args),
                Integer(len(arguments) // 8),
                Integer(linked.out),
            ],
        )
        return status, struct.unpack("<Q", self.read(linked.out, 8))[0]

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]:
        linked = self.linked
        self.write(linked.args, arguments or bytes(8))
        status = self.emulator.call(
            linked.run or 0,
            [
                Integer(linked.context),
                Blob(struct.pack("<3Q", identity, 1, 0)),
                Integer(linked.args),
                Integer(len(arguments) // 8),
                Integer(linked.out),
            ],
        )
        return status, struct.unpack("<Q", self.read(linked.out, 8))[0]


class Node:
    """wasm32 in a node process that stays up: memory and instance live between calls."""

    def __init__(self, code: world.Code, linked: link.Linked) -> None:
        self.linked = linked
        self.modules: list[str] = []
        leaves = code.leaves["wasm32"]
        spec = world.letters()
        by_id = {value: key for key, value in link.graph_header.leaves().items()}
        module = module_of(
            [
                *(
                    (
                        f"leaf{i}",
                        [world.wasm_type(c) for c in spec[by_id[i]]],
                        leaves[i],
                    )
                    for i in sorted(leaves)
                ),
                ("run", ["i32", "i32", "i64", "i64", "i32"], code.run["wasm32"]),
            ],
            world.call_types(),
        )
        pages = (linked.base + len(linked.data)) // PAGE + 2
        self.process = subprocess.Popen(
            ["node", str(HARNESS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._ask(
            {
                "cmd": "init",
                "module": module.hex(),
                "pages": pages,
                "base": linked.base,
                "image": base64.b64encode(linked.data).decode(),
                "table": {str(i): f"leaf{i}" for i in sorted(leaves)},
            }
        )

    def _ask(self, request: dict) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        answer = json.loads(self.process.stdout.readline())
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer

    def write(self, address: int, data: bytes) -> None:
        self._ask(
            {"cmd": "write", "at": address, "data": base64.b64encode(data).decode()}
        )

    def read(self, address: int, size: int) -> bytes:
        return base64.b64decode(
            self._ask({"cmd": "read", "at": address, "size": size})["data"]
        )

    def slot(self, identity: int) -> int:
        return self.linked.slots[
            identity
        ]  # the table of wasm32 is not in the memory of the image

    def pump(self, area: int | None = None) -> int:
        assert self.linked.live is not None
        where = self.linked.live.area if area is None else area
        return self._ask(
            {
                "cmd": "call",
                "fn": f"leaf{PUMP}",
                "args": [["i32", str(self.linked.context)], ["i32", str(where)]],
            }
        )["status"]

    def leaf(self, identity: int, argument: int | None = None) -> int:
        assert self.linked.live is not None
        where = self.linked.live.area if argument is None else argument
        return self._ask(
            {
                "cmd": "call",
                "fn": f"leaf{identity}",
                "args": [
                    ["i32", str(self.linked.context)],
                    ["i32", str(where)],
                ],
            }
        )["status"]

    def call_code(
        self, code: bytes, arguments: bytes = b"", at: int | None = None
    ) -> tuple[int, int]:
        """The body of a function is a module of its own over the memory and table of the machine."""
        linked = self.linked
        name = f"code{len(self.modules)}"
        # its own stack lies below the one of the leaves, so a frame is not run over by one of them
        module = module_of(
            [("compiled", ["i32", "i32", "i64", "i32"], code)],
            world.call_types(),
            stack=1 << 15,
        )
        self._ask({"cmd": "module", "name": name, "module": module.hex()})
        self.modules.append(name)
        self.write(linked.args, arguments or bytes(8))
        status = self._ask(
            {
                "cmd": "call",
                "module": name,
                "fn": "compiled",
                "args": [
                    ["i32", str(linked.context)],
                    ["i32", str(linked.args)],
                    ["i64", str(len(arguments) // 8)],
                    ["i32", str(linked.out)],
                ],
            }
        )["status"]
        return status, struct.unpack("<Q", self.read(linked.out, 8))[0]

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]:
        linked = self.linked
        self.write(linked.args, arguments or bytes(8))
        self.write(
            linked.frame, struct.pack("<3Q", identity, 1, 0)
        )  # the handle is passed by address
        status = self._ask(
            {
                "cmd": "call",
                "fn": "run",
                "args": [
                    ["i32", str(linked.context)],
                    ["i32", str(linked.frame)],
                    ["i64", str(linked.args)],
                    ["i64", str(len(arguments) // 8)],
                    ["i32", str(linked.out)],
                ],
            }
        )["status"]
        return status, struct.unpack("<Q", self.read(linked.out, 8))[0]

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def linked_image(
    image: Image, machine: str, spec: link.LiveSpec | None = None
) -> link.Linked:
    base = WASM_BASE if machine == "wasm32" else EMULATED_BASE
    return link.link(image, machine, base, spec or link.LiveSpec())


def machine_of(name: str, image: Image, spec: link.LiveSpec | None = None) -> Machine:
    linked = linked_image(image, name, spec)
    if name == "wasm32":
        return Node(world.code_of(image), linked)
    return Emulated(name, linked)


class Channel:
    """The two rings of a machine as a link, and the pump that serves them."""

    def __init__(self, machine: Machine) -> None:
        assert machine.linked.live is not None
        self.machine = machine
        self.area = machine.linked.live.area
        self.rx = machine.linked.live.regions["rx"]
        self.tx = machine.linked.live.regions["tx"]
        self.client = Client(self, pump=self.pump)

    def _word(self, offset: int) -> int:
        return struct.unpack("<I", self.machine.read(self.area + offset, 4))[0]

    def counters(self, offset: int) -> int:
        return self._word(offset)

    def write(self, data: bytes) -> None:
        head, tail = self._word(RX_HEAD), self._word(RX_TAIL)
        address, capacity = self.rx
        if len(data) > capacity - ((head - tail) & 0xFFFFFFFF):
            raise OverflowError("the ring to the image is full")
        for part, at in _wrapped(head, len(data), capacity):
            self.machine.write(address + part, data[at[0] : at[1]])
        self.machine.write(
            self.area + RX_HEAD, struct.pack("<I", (head + len(data)) & 0xFFFFFFFF)
        )

    def read(self) -> bytes:
        head, tail = self._word(TX_HEAD), self._word(TX_TAIL)
        address, capacity = self.tx
        count = (head - tail) & 0xFFFFFFFF
        out = b"".join(
            self.machine.read(address + part, at[1] - at[0])
            for part, at in _wrapped(tail, count, capacity)
        )
        if count:
            self.machine.write(self.area + TX_TAIL, struct.pack("<I", head))
        return out

    def pump(self) -> int:
        return self.machine.pump()

    def arena(self) -> bytes:
        """The bytes of the arena, header to end of the cell stream."""
        assert self.machine.linked.live is not None
        address, _ = self.machine.linked.live.regions["arena"]
        (body,) = struct.unpack("<Q", self.machine.read(address + 16, 8))
        return self.machine.read(address, body)

    def loaded(self) -> Image:
        return Image.from_bytes(self.arena())


def _wrapped(
    start: int, count: int, capacity: int
) -> list[tuple[int, tuple[int, int]]]:
    """A run of bytes in a ring: (offset in the ring, (from, to) in the data), split at the end."""
    out = []
    done = 0
    while done < count:
        at = (start + done) & (capacity - 1)
        take = min(count - done, capacity - at)
        out.append((at, (done, done + take)))
        done += take
    return out


def starter_image(starter: Path) -> Image:
    return tree.load(starter)
