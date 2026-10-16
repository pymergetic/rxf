"""The interpreter of ``native/graph.c`` on the host: programs run natively, not in Python.

``rxf_sim`` interprets the words of a program a step at a time in Python; it is a tool for
writing such programs. What a program does on the real interpreter is what the machines run, and
the real interpreter is ``native/graph.c``. This module compiles that C (with the leaves the
starter ships: ``native/basics.c``, ``stdlib_memory.c``, ``stdlib_runtime.c``, ``safe.c`` and the
thin ``tests/host/runner.c`` around them) into one shared library, loads it, and runs programs
through ``rxf_graph_run`` -- the same C every machine runs, natively on the host.

``run(program, args, memory)`` answers what ``rxf_sim.run`` answers: ``(status, value)`` for the
arguments of whole words. The memory is the same flat address space of ``rxf_sim.Memory``, but
the bytes are the real ones the leaves read and write: the addresses a program is given are the
addresses of this block (``memory.at``), and ``FRAME_AT`` is where ``rxf_sim`` keeps the frame of
a run (the frame of ``rxf_graph_run`` is its own stack frame; its slot addresses are what the
leaves see).

The library is built once per checkout into ``tests/host/build/``, named by a digest of its
sources, and loaded with ctypes. ``rxf_sim`` stays as the dev oracle of the corpus tests.
"""

from __future__ import annotations

import ctypes
import hashlib
import mmap
import os
import shutil
import struct
import subprocess
from pathlib import Path
from typing import Self

import graph_header

ROOT = Path(__file__).parents[1]
NATIVE = ROOT / "native"
HERE = Path(__file__).parent
SOURCES = [
    NATIVE / "graph.c",
    NATIVE / "basics.c",
    NATIVE / "stdlib_memory.c",
    NATIVE / "stdlib_runtime.c",
    NATIVE / "safe.c",
    HERE / "host" / "runner.c",
]
BUILD = HERE / "host" / "build"
FRAME_AT = 0x100000  # where the frame of a run lies in the memory of rxf_sim
NO_END = 0xFFFFFFFF  # the answer of a run that did not end within its budget
MAGIC = graph_header.numbers()["RXF_GRAPH_MAGIC"]


def _compiler() -> str:
    """The host C compiler: the clang the native sources are pinned to, else the system one."""
    return "clang-18" if shutil.which("clang-18") else "cc"


def _library() -> Path:
    """The shared library of the interpreter, compiled when its sources changed."""
    compiler = _compiler()
    digest = hashlib.sha256()
    for source in [*SOURCES, Path(compiler)]:
        digest.update(source.name.encode())
        if source.is_file():
            digest.update(source.read_bytes())
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / f"rxf_host_runner-{digest.hexdigest()[:16]}.so"
    if not out.exists():
        made = out.with_suffix(f".{os.getpid()}.so")
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-fPIC",
                "-shared",
                "-Wno-unknown-pragmas",
                "-I",
                str(NATIVE),
                *map(str, SOURCES),
                "-o",
                str(made),
            ],
            check=True,
        )
        os.replace(made, out)  # a build of another process is replaced whole, never half
    return out


def _load() -> ctypes.CDLL:
    library = ctypes.CDLL(str(_library()))
    library.rxf_host_init()
    library.rxf_host_run.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    ]
    library.rxf_host_run.restype = ctypes.c_uint32
    library.rxf_host_bind.argtypes = [
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    library.rxf_host_bind.restype = ctypes.c_uint32
    return library


_lib = _load()


class Memory:
    """Bytes at addresses: one flat block of host memory, zero until written.

    The addresses of a run live in this block; ``at`` is where a number a program is given as an
    address points. Like ``rxf_sim.Memory``, but the bytes are the ones the leaves really read
    and write (the block is anonymous memory of the process, and closes with the instance).
    """

    def __init__(self, size: int) -> None:
        self.size = size
        self.block = mmap.mmap(-1, size)
        self.base = ctypes.addressof(ctypes.c_char.from_buffer(self.block))

    def at(self, address: int) -> int:
        """Where the address of the flat space lives in this process (what a program is told)."""
        return self.base + address

    def read(self, address: int, size: int) -> bytes:
        return self.block[address : address + size]

    def write(self, address: int, data: bytes) -> None:
        self.block[address : address + len(data)] = data

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]

    def close(self) -> None:
        self.block.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def run(
    program: bytes, args: bytes, memory: Memory, limit: int | None = 50_000_000,
    objects: dict[int, bytes | bytearray] | None = None,
) -> tuple[int, int]:
    """(status, value) of a run of the program on arguments of whole words.

    What ``rxf_sim.run`` computes, on the interpreter of ``native/graph.c``. ``limit`` is its
    step budget: the run is stopped and ``RuntimeError`` is raised when the program does not end
    (``None`` lets the run go on). ``objects`` are the secondary objects of the run, by the
    object id that names them: the programs a call card calls (the callee is a program of
    its own, bound at the id the card's handle carries) and the cells an Object handle
    resolves. A ``bytes`` payload is the read-only words of a program (copied for the
    run); a ``bytearray`` is the live cell: the run writes through it and the bytes stay
    for the next run of the same cell -- the boot calls of the qstr corpus store the
    addresses of their pools into a cell this way. One run at a time in a process, like
    the tests run them.
    """
    assert len(program) % 8 == 0 and len(program) >= 40
    magic, _frame_bytes, _parameters, words, _pc = struct.unpack_from("<5Q", program)
    assert magic == MAGIC
    assert len(args) == words * 8
    code = (ctypes.c_uint64 * (len(program) // 8)).from_buffer_copy(program)
    given = (ctypes.c_uint8 * max(len(args), 1)).from_buffer_copy(args or b"\0")

    def held_of(payload: bytes | bytearray) -> ctypes.Array[ctypes.c_uint8]:
        room = max(len(payload), 1)
        if isinstance(payload, bytearray) and payload:
            return (ctypes.c_uint8 * room).from_buffer(payload)
        return (ctypes.c_uint8 * room).from_buffer_copy(payload or b"\0")

    ordered = sorted((objects or {}).items())
    held = [held_of(payload) for _, payload in ordered]
    for (identity, payload), block in zip(ordered, held):
        assert _lib.rxf_host_bind(identity, ctypes.addressof(block), len(payload)) == 0
    out = ctypes.c_uint64()
    status = _lib.rxf_host_run(
        code,
        len(program),
        given,
        len(args),
        memory.base,
        memory.size,
        0 if limit is None else limit,
        ctypes.byref(out),
    )
    if status == NO_END:
        raise RuntimeError("the program does not end")
    return status, out.value
