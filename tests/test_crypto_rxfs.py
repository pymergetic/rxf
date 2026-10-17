"""The SHA-256 words of the crypto card (native/crypto/sha256.rxfs), pinned by the vectors.

The words are a program of ``native/graph.h`` like any other. What runs them is what runs every
program: the simulator of ``rxf_sim`` as the dev oracle, the interpreter of ``native/graph.c``
on x86_64, aarch64 and wasm32, and the in-image compiler of ``native/compile.c`` (docs/18) whose
code must compute the same digests. The vectors are their own: FIPS 180-4 and RFC 6234's
SHA-256 cases, with a sweep of message lengths around every block and padding boundary held to
hashlib as the reference implementation.
"""

from __future__ import annotations

import hashlib
import shutil
import struct
from pathlib import Path

import pytest
import rxfc

from pymergetic.rxftk.core import tree

from . import graph_world as world
from . import rxf_sim, rxf_world
from .snippets import STARTER

ROOT = Path(__file__).parents[1]
PROGRAM = rxfc.compile_file(ROOT / "native" / "crypto" / "sha256.rxfs")

MSG = rxf_world.DATA
OUT = MSG + 0x400
WORK = MSG + 0x800
WORK_BYTES = 0x800

# the vectors of FIPS 180-4 and of RFC 6234's SHA-256 cases
VECTORS = [
    (
        b"",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ),
    (
        b"abc",
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ),
    (
        b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ),
    (
        (
            b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
            b"ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu"
        ),
        "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
    ),
]

# message lengths around every boundary of a block and of the padding
LENS = [0, 1, 3, 54, 55, 56, 57, 63, 64, 65, 118, 119, 120, 121, 127, 128, 200]

MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]


def pattern(length: int) -> bytes:
    """A message of mixed bytes, so no shortcut of a repeated byte passes."""
    return bytes(((i * 7) + 3) & 0xFF for i in range(length))


CASES = [(message, bytes.fromhex(digest)) for message, digest in VECTORS] + [
    (message, hashlib.sha256(message).digest())
    for length in LENS
    for message in (pattern(length), b"\x00" * length)
]


def arguments(message: bytes) -> bytes:
    return struct.pack("<QQQQ", MSG, len(message), OUT, WORK)


@pytest.fixture(scope="module")
def code():
    return world.code_of(tree.load(STARTER))


@pytest.mark.parametrize(("message", "digest"), CASES)
def test_the_simulator_computes_the_digest(message, digest):
    memory = rxf_sim.Memory()
    memory.write(MSG, message)
    memory.write(WORK, bytes(WORK_BYTES))
    status, value = rxf_sim.run(PROGRAM, arguments(message), memory)
    assert (status, value) == (0, 0)
    assert memory.read(OUT, 32) == digest


@pytest.mark.parametrize("machine", MACHINES)
def test_the_interpreter_of_every_machine_computes_the_digest(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for message, digest in CASES:
            session.install(PROGRAM, arguments(message))
            session.write(MSG, message)
            session.write(WORK, bytes(WORK_BYTES))
            result = session.run()
            assert result.status == 0, (machine, len(message))
            assert session.read(OUT, 32) == digest, (machine, len(message))
    finally:
        session.close()


@pytest.mark.parametrize("machine", MACHINES)
def test_the_compiler_of_the_image_makes_code_of_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for message, digest in CASES:
            session.install(PROGRAM, arguments(message))
            session.write(MSG, message)
            session.write(WORK, bytes(WORK_BYTES))
            compiled, size, at = session.compile()
            assert compiled == 0, (machine, len(message), at)
            result = session.execute(size)
            assert size > 0 and result.status == 0, (machine, len(message))
            assert session.read(OUT, 32) == digest, (machine, len(message))
    finally:
        session.close()
