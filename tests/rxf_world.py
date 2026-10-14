"""The world of a program that is longer than the programs of the starter: the interpreter, the leaves, and room.

``graph_world`` is the world of the starter's programs: a few kilobytes of program, a heap of 8 KB.
The programs of ``.rxf`` (``tools/native/rxfc.py``), and the compiler that is one of them, need more.
Here the program lies at ``PROGRAM_AT`` with room for 1 MB, and ``DATA`` is the rest of a 10 MB
memory: what a program is given to read and where it writes. A ``Session`` is one machine that
stays up for several steps, whatever the machine is: the interpreter runs a program, the compiler
of ``native/compile.c`` makes code of it, that code runs. x86_64 and aarch64 are emulated, wasm32
is a node process (``wasm_live.js``).
"""

from __future__ import annotations

import base64
import json
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import graph_header

from . import graph_world as world
from . import scenario
from .emulator import Emulator, Integer
from .wasm import module_of

BASE = scenario.BASE
CONTEXT = BASE
OBJECTS = BASE + 0x100
ARGS = BASE + 0x800
OUT = BASE + 0x900
HEAP = BASE + 0x1000
HEAP_SIZE = 0x1000
REQUEST = BASE + 0xB00  # what the compiler is asked
TINY_REQUEST = BASE + 0xC00  # the compiler that is a program
FUNCTIONS = BASE + 0x2000  # the function table of a run
TABLE = BASE + 0x2800  # its table of the leaves
PROGRAM_AT = BASE + 0x4000
PROGRAM_BYTES = 0x100000
DATA = BASE + 0x110000
CODE = DATA + 0x100000  # where the compiler writes code
CODE_BYTES = 0x200000
WORK = DATA + 0x300000  # and where it keeps its tables
WORK_BYTES = 0x100000
AGAIN = WORK + WORK_BYTES  # code and tables of a second compiler
MEMORY = 0xA00000  # from BASE
SHIFT = 0x4000000  # the code and stack of the emulator, behind that
PAGE = 0x10000
PROGRAM_ID = 900001
HANDLE = (PROGRAM_ID, 1, 0)
HANDLE_COPY = BASE + 0x980  # the handle a wasm caller passes by value, by address
LEAVES = graph_header.leaves()
HARNESS = Path(__file__).with_name("wasm_live.js")


@dataclass(frozen=True)
class Result:
    status: int
    value: int


def head(program: bytes, args: bytes, slots: list[tuple[int, int]]) -> bytes:
    """Everything below ``DATA``: context, the object of the program, function table, arguments."""
    assert len(program) <= PROGRAM_BYTES and len(slots) * 24 <= 0x800
    data = bytearray(DATA - BASE)

    def put(address: int, content: bytes) -> None:
        data[address - BASE : address - BASE + len(content)] = content

    put(
        OBJECTS,
        scenario.entry(PROGRAM_ID, 1, 0, PROGRAM_AT, len(program), len(program), 1),
    )
    put(
        CONTEXT,
        struct.pack(
            "<18Q",
            2, 144,  # version, size
            len(slots), FUNCTIONS,
            1, OBJECTS,
            0, 0,  # capabilities
            HEAP, HEAP_SIZE, HEAP_SIZE, 0,  # base, committed, limit, frontier
            0, 4, BASE + 0xA00,  # journal
            0, 0, 0,  # cleanup
        ),
    )  # fmt: skip
    put(PROGRAM_AT, program)
    put(ARGS, args)
    put(FUNCTIONS, table_of(slots))
    put(HANDLE_COPY, struct.pack("<3Q", *HANDLE))
    return bytes(data)


def table_of(slots: list[tuple[int, int]]) -> bytes:
    """The function table of a machine: id, generation, address."""
    return b"".join(struct.pack("<3Q", i, 1, address) for i, address in slots)


def arguments(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}Q", *values)


def compile_request(
    program_size: int,
    out: int = CODE,
    capacity: int = CODE_BYTES,
    work: int = WORK,
    work_bytes: int = WORK_BYTES,
    machine: int | None = None,
    table: int = 0,
    count: int = 0,
    program: int = PROGRAM_AT,
) -> bytes:
    """The request of the compiler of compile.c (eight words), or of the one that is a program."""
    words = [program, program_size, out, capacity, work, work_bytes, 0, 0]
    if machine is not None:
        words += [machine, table, count]
    return arguments(*words)


class Session:
    """A machine that stays up: the interpreter and every leaf, memory, and what runs on it."""

    machine: str
    slots: list[tuple[int, int]]
    program: bytes
    words: int

    def install(self, program: bytes, args: bytes = b"") -> None:
        raise NotImplementedError

    def write(self, address: int, data: bytes) -> None:
        raise NotImplementedError

    def read(self, address: int, size: int) -> bytes:
        raise NotImplementedError

    def run(self) -> Result:
        """The interpreter on the installed program."""
        raise NotImplementedError

    def execute(self, size: int) -> Result:
        """The code of ``size`` bytes at ``CODE`` on the installed arguments."""
        raise NotImplementedError

    def leaf(self, name: str, *arguments: int) -> int:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def compile(self, capacity: int = CODE_BYTES) -> tuple[int, int, int]:
        """The compiler of ``native/compile.c`` on the installed program: status, the size of the
        code it wrote at ``CODE``, and the word it stopped at."""
        self.write(REQUEST, compile_request(len(self.program), capacity=capacity))
        status = self.leaf("compile_program", REQUEST)
        *_, size, at = struct.unpack("<8Q", self.read(REQUEST, 64))
        return status, size, at


class Emulated(Session):
    """x86_64 or aarch64 as machine code in an emulator, with the interpreter and every leaf loaded."""

    def __init__(self, machine: str, code: world.Code) -> None:
        self.machine = machine
        self.emulator = Emulator(machine, SHIFT)
        self.emulator.map(BASE, MEMORY)
        self.slots = sorted(
            (identity, self.emulator.load(data))
            for identity, data in code.leaves[machine].items()
        )
        self.leaves = dict(self.slots)
        self.interpreter = self.emulator.load(code.run[machine])
        self.words = 0
        self.program = b""

    def install(self, program: bytes, args: bytes = b"") -> None:
        self.emulator.write(BASE, head(program, args, self.slots))
        self.words = len(args) // 8
        self.program = program

    def write(self, address: int, data: bytes) -> None:
        self.emulator.write(address, data)

    def read(self, address: int, size: int) -> bytes:
        return self.emulator.read(address, size)

    def run(self) -> Result:
        self.emulator.flush()
        status = self.emulator.call(
            self.interpreter,
            [
                Integer(CONTEXT),
                world.Blob(struct.pack("<3Q", *HANDLE)),
                Integer(ARGS),
                Integer(self.words),
                Integer(OUT),
            ],
        )
        return Result(status, int.from_bytes(self.read(OUT, 8), "little"))

    def execute(self, size: int) -> Result:
        self.emulator.flush()
        status = self.emulator.call(
            CODE,
            [Integer(CONTEXT), Integer(ARGS), Integer(self.words), Integer(OUT)],
        )
        return Result(status, int.from_bytes(self.read(OUT, 8), "little"))

    def leaf(self, name: str, *arguments: int) -> int:
        self.emulator.flush()
        return self.emulator.call(
            self.leaves[LEAVES[name]], [Integer(CONTEXT), *map(Integer, arguments)]
        )


class Node(Session):
    """wasm32 in a node process that stays up: the memory and the table live between calls."""

    def __init__(self, code: world.Code) -> None:
        self.machine = "wasm32"
        leaves = code.leaves["wasm32"]
        self.slots = [
            (i, i) for i in sorted(leaves)
        ]  # the address of a leaf is its table index
        spec = world.letters()
        by_id = {value: key for key, value in LEAVES.items()}
        module = module_of(
            [
                *(
                    (
                        f"leaf{i}",
                        [world.wasm_type(c) for c in spec[by_id[i]]],
                        leaves[i],
                    )
                    for i, _ in self.slots
                ),
                ("run", ["i32", "i32", "i64", "i64", "i32"], code.run["wasm32"]),
            ],
            world.call_types(),
        )
        self.process = subprocess.Popen(
            ["node", str(HARNESS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.ask(
            {
                "cmd": "init",
                "module": module.hex(),
                "pages": (BASE + MEMORY) // PAGE + 2,
                "base": BASE,
                "image": "",
                "table": {str(i): f"leaf{i}" for i, _ in self.slots},
            }
        )
        self.modules = 0
        self.words = 0
        self.program = b""

    def ask(self, request: dict) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        answer = json.loads(self.process.stdout.readline())
        if "error" in answer:
            raise RuntimeError(answer["error"])
        return answer

    def install(self, program: bytes, args: bytes = b"") -> None:
        self.write(BASE, head(program, args, self.slots))
        self.words = len(args) // 8
        self.program = program

    def write(self, address: int, data: bytes) -> None:
        self.ask(
            {"cmd": "write", "at": address, "data": base64.b64encode(data).decode()}
        )

    def read(self, address: int, size: int) -> bytes:
        return base64.b64decode(
            self.ask({"cmd": "read", "at": address, "size": size})["data"]
        )

    def call(self, fn: str, args: list[list[str]], module: str | None = None) -> int:
        request: dict = {"cmd": "call", "fn": fn, "args": args}
        if module:
            request["module"] = module
        return self.ask(request)["status"]

    def value(self) -> int:
        return int.from_bytes(self.read(OUT, 8), "little")

    def run(self) -> Result:
        self.write(HANDLE_COPY, struct.pack("<3Q", *HANDLE))
        status = self.call(
            "run",
            [
                ["i32", str(CONTEXT)],
                ["i32", str(HANDLE_COPY)],
                ["i64", str(ARGS)],
                ["i64", str(self.words)],
                ["i32", str(OUT)],
            ],
        )
        return Result(status, self.value())

    def execute(self, size: int) -> Result:
        """The code is the body of a function: a module of its own over the memory and the table."""
        name = f"code{self.modules}"
        self.modules += 1
        module = module_of(
            [("compiled", ["i32", "i32", "i64", "i32"], self.read(CODE, size))],
            world.call_types(),
            stack=1 << 15,
        )
        self.ask({"cmd": "module", "name": name, "module": module.hex()})
        status = self.call(
            "compiled",
            [
                ["i32", str(CONTEXT)],
                ["i32", str(ARGS)],
                ["i64", str(self.words)],
                ["i32", str(OUT)],
            ],
            name,
        )
        return Result(status, self.value())

    def leaf(self, name: str, *arguments: int) -> int:
        return self.call(
            f"leaf{LEAVES[name]}",
            [["i32", str(CONTEXT)], *(["i32", str(a)] for a in arguments)],
        )

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=10)


def session_of(machine: str, code: world.Code) -> Session:
    return Node(code) if machine == "wasm32" else Emulated(machine, code)


def run_once(
    machine: str,
    code: world.Code,
    program: bytes,
    args: bytes,
    writes: tuple[tuple[int, bytes], ...] = (),
    reads: tuple[tuple[int, int], ...] = (),
) -> tuple[Result, list[bytes]]:
    """One run of the interpreter of any machine; the result and the ranges of memory asked for."""
    session = session_of(machine, code)
    try:
        session.install(program, args)
        for address, data in writes:
            session.write(address, data)
        result = session.run()
        return result, [session.read(at, size) for at, size in reads]
    finally:
        session.close()
