"""The crypto words of the card (native/crypto/*.rxfs), pinned by the vectors.

The words are programs of ``native/graph.h`` like any other. What runs them is what runs
every program: the simulator of ``rxf_sim`` as the dev oracle, the interpreter of
``native/graph.c`` on x86_64, aarch64 and wasm32, and the in-image compiler of
``native/compile.c`` (docs/18) whose code must compute the same answers. The vectors are
their own: FIPS 180-4 and RFC 6234 for SHA-256, RFC 4231 for HMAC-SHA-256, RFC 5869 for
HKDF, and sweeps of lengths around every block and padding boundary held to hashlib and
hmac as the reference implementations.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
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
CRYPTO = ROOT / "native" / "crypto"
SHA256 = rxfc.compile_file(CRYPTO / "sha256.rxfs")
HMAC_SHA256 = rxfc.compile_file(CRYPTO / "hmac_sha256.rxfs")
HKDF = rxfc.compile_file(CRYPTO / "hkdf.rxfs")

MSG = rxf_world.DATA
OUT = MSG + 0x400
WORK = MSG + 0x800
WORK_BYTES = 0x800

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


def arguments(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}Q", *values)


# ---- SHA-256: the vectors of FIPS 180-4 and RFC 6234 --------------------------------------------

SHA_VECTORS = [
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

SHA_LENS = [0, 1, 3, 54, 55, 56, 57, 63, 64, 65, 118, 119, 120, 121, 127, 128, 200]

# ---- HMAC-SHA-256: the cases of RFC 4231 --------------------------------------------------------

HMAC_VECTORS = [
    (
        b"\x0b" * 20,
        b"Hi There",
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
    ),
    (
        b"Jefe",
        b"what do ya want for nothing?",
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    ),
    (
        b"\xaa" * 20,
        b"\xdd" * 50,
        "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe",
    ),
    (
        bytes(range(1, 26)),
        b"\xcd" * 50,
        "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b",
    ),
    (
        b"\xaa" * 131,
        b"Test Using Larger Than Block-Size Key - Hash Key First",
        "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
    ),
    (
        b"\xaa" * 131,
        (
            b"This is a test using a larger than block-size key and a larger than "
            b"block-size data. The key needs to be hashed before being used by the "
            b"HMAC algorithm."
        ),
        "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
    ),
]

HMAC_KEY_LENS = [0, 1, 20, 63, 64, 65, 131]
HMAC_MSG_LENS = [0, 55, 64, 100]

# ---- HKDF: the cases of RFC 5869 ---------------------------------------------------------------

HKDF_VECTORS = [
    (
        bytes(range(13)),
        b"\x0b" * 22,
        bytes(range(0xF0, 0xFA)),
        42,
        (
            "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        ),
    ),
    (
        bytes(range(0x60, 0xB0)),
        bytes(range(0x50)),
        bytes(range(0xB0, 0x100)),
        82,
        (
            "b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c"
            "59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71"
            "cc30c58179ec3e87c14c01d5c1f3434f1d87"
        ),
    ),
    (
        b"",
        b"\x0b" * 22,
        b"",
        42,
        (
            "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8"
        ),
    ),
]

HKDF_SWEEP = [
    (b"", b"\x11" * 32, b"", 1),
    (b"salt", b"\x22" * 16, b"", 31),
    (b"salt", pattern(40), b"info", 33),
    (pattern(70), pattern(70), pattern(70), 100),
]


def hkdf_reference(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    key = salt if salt else bytes(32)
    prk = hmac_module.new(key, ikm, hashlib.sha256).digest()
    out, piece, counter = b"", b"", 1
    while len(out) < length:
        piece = hmac_module.new(
            prk, piece + info + bytes([counter]), hashlib.sha256
        ).digest()
        out += piece
        counter += 1
    return out[:length]


# ---- the cases of the three programs: (name, program, args, writes, out length, expected) --------

CASES: list[tuple[str, bytes, bytes, tuple[tuple[int, bytes], ...], int, bytes]] = []

for message, digest in SHA_VECTORS:
    CASES.append(
        (
            f"sha256({len(message)})",
            SHA256,
            arguments(MSG, len(message), OUT, WORK),
            ((MSG, message), (WORK, bytes(WORK_BYTES))),
            32,
            bytes.fromhex(digest),
        )
    )
for length in SHA_LENS:
    for message in (pattern(length), b"\x00" * length):
        CASES.append(
            (
                f"sha256({length})",
                SHA256,
                arguments(MSG, len(message), OUT, WORK),
                ((MSG, message), (WORK, bytes(WORK_BYTES))),
                32,
                hashlib.sha256(message).digest(),
            )
        )

for key, message, tag in HMAC_VECTORS:
    CASES.append(
        (
            f"hmac({len(key)},{len(message)})",
            HMAC_SHA256,
            arguments(MSG, len(key), MSG + 0x120, len(message), OUT, WORK),
            (
                (MSG, key),
                (MSG + 0x120, message),
                (WORK, bytes(WORK_BYTES)),
            ),
            32,
            bytes.fromhex(tag),
        )
    )
for key_len in HMAC_KEY_LENS:
    for message_len in HMAC_MSG_LENS:
        key, message = pattern(key_len), pattern(message_len)
        CASES.append(
            (
                f"hmac({key_len},{message_len})",
                HMAC_SHA256,
                arguments(MSG, key_len, MSG + 0x120, message_len, OUT, WORK),
                (
                    (MSG, key),
                    (MSG + 0x120, message),
                    (WORK, bytes(WORK_BYTES)),
                ),
                32,
                hmac_module.new(key, message, hashlib.sha256).digest(),
            )
        )

for salt, ikm, info, length, okm in HKDF_VECTORS:
    CASES.append(
        (
            f"hkdf({len(salt)},{len(ikm)},{len(info)},{length})",
            HKDF,
            arguments(
                MSG, len(salt), MSG + 0x80, len(ikm), MSG + 0x100, len(info), OUT, length, WORK
            ),
            (
                (MSG, salt),
                (MSG + 0x80, ikm),
                (MSG + 0x100, info),
                (WORK, bytes(WORK_BYTES)),
            ),
            length,
            bytes.fromhex(okm),
        )
    )
for salt, ikm, info, length in HKDF_SWEEP:
    CASES.append(
        (
            f"hkdf({len(salt)},{len(ikm)},{len(info)},{length})",
            HKDF,
            arguments(
                MSG, len(salt), MSG + 0x80, len(ikm), MSG + 0x100, len(info), OUT, length, WORK
            ),
            (
                (MSG, salt),
                (MSG + 0x80, ikm),
                (MSG + 0x100, info),
                (WORK, bytes(WORK_BYTES)),
            ),
            length,
            hkdf_reference(salt, ikm, info, length),
        )
    )


# ---- what runs them -----------------------------------------------------------------------------

def answer_of_sim(
    program: bytes,
    args: bytes,
    writes: tuple[tuple[int, bytes], ...],
    out_len: int,
) -> bytes:
    memory = rxf_sim.Memory()
    for address, data in writes:
        memory.write(address, data)
    status, value = rxf_sim.run(program, args, memory)
    assert (status, value) == (0, 0)
    return memory.read(OUT, out_len)


def answer_of_session(
    session, program, args, writes, out_len: int, execute: bool = False
) -> bytes:
    session.install(program, args)
    for address, data in writes:
        session.write(address, data)
    if execute:
        compiled, size, at = session.compile()
        assert compiled == 0, at
        result = session.execute(size)
        assert size > 0
    else:
        result = session.run()
    assert result.status == 0
    return session.read(OUT, out_len)


@pytest.fixture(scope="module")
def code():
    return world.code_of(tree.load(STARTER))


@pytest.mark.parametrize(
    ("name", "program", "args", "writes", "out_len", "expected"), CASES, ids=[c[0] for c in CASES]
)
def test_the_simulator_computes_the_answer(name, program, args, writes, out_len, expected):
    assert answer_of_sim(program, args, writes, out_len) == expected


@pytest.mark.parametrize("machine", MACHINES)
def test_the_interpreter_of_every_machine_computes_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for name, program, args, writes, out_len, expected in CASES:
            assert answer_of_session(session, program, args, writes, out_len) == expected, (
                machine,
                name,
            )
    finally:
        session.close()


@pytest.mark.parametrize("machine", MACHINES)
def test_the_compiler_of_the_image_makes_code_of_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for name, program, args, writes, out_len, expected in CASES:
            got = answer_of_session(
                session, program, args, writes, out_len, execute=True
            )
            assert got == expected, (machine, name)
    finally:
        session.close()
