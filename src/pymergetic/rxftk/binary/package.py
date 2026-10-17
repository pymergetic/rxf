"""The RXFW package: the header, the linked image and the cert block of the tail.

A package is the memory image of the program (the one ``tools/native/link.py`` makes:
context, journal, function slots, object entries, arena, heap, scratch) behind a header
of ``HEADER_BYTES`` that names where it runs, and behind that the cert block of the
tail: the statements of the voices over this image (docs/11), in fixed records the
wrapper's activation check reads. Nothing in the image is relocated: the image is
linked at ``base``, every address in it is the one it was linked at, and a wrapper
places one block at that base and loads the image into it. That is the whole shell
(docs/17): the program is the image, the executable is a thin wrapper that loads it,
passes the activation seam, runs the entry the header names through the run cell of the
image itself, and reports what the entry answered.

The header is the C struct ``rxf_wrap_package`` of ``native/wrap/wrap.h`` (192 bytes);
the tests pin the two descriptions of it equal. The entry is the handle of one
``bytecode`` object -- the program words of a function -- and the run cell is the
interpreter of the image: ``rxf_graph_run`` as the machine has it. The starter's entry
is ``pymergetic.rxf.application.main``, which takes no arguments and answers one word:
the shape every wrapper calls (see the entry convention in ``native/wrap/wrap.h``).
"""

from __future__ import annotations

import hashlib
import importlib
import struct
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pymergetic.rxftk import certify
from pymergetic.rxftk.core.object import Image

REPO = Path(__file__).resolve().parents[4]
NATIVE_TOOLS = REPO / "tools" / "native"

MAGIC = b"RXFW"
VERSION = 1
HEADER_BYTES = 192
BASE = 0x04000000  # the base every seat places its block at
DEFAULT_ENTRY = "pymergetic.rxf.application.main"
HEADER = "<4sI" + "Q" * 23  # magic, version, then the words and the reserved tail
ARENA_FROM = 13  # words 13..14 name the arena window of the image; zero for now
CERT_FROM = 15  # words 15..16 name the cert block of the tail: (offset, bytes)
CERT_KEY_BYTES = 64  # the key id field of one cert record
CERT_RECORD_BYTES = 232  # three digests (32), the key id (64), the stamp (8), the signature (64)
CONTEXT_VERSION = 2
CONTEXT_BYTES = 144


class PackageError(Exception):
    """The image cannot be packed, or the bytes are no RXFW package."""


@dataclass(frozen=True)
class Package:
    """The header of a package, as the wrapper reads it."""

    total: int
    base: int
    image: int
    image_bytes: int
    run: int
    args: int
    out: int
    frame: int
    entry: tuple[int, int, int]
    cert: tuple[int, int]  # the cert block of the tail: (offset, bytes), or (0, 0)


def link_module() -> ModuleType:
    """``tools/native/link.py``: the linker the images are made with.

    The native tools are scripts, not a package (see the pytest path of the repo), so
    they are reached by their directory.
    """
    tools = str(NATIVE_TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return importlib.import_module("link")


def build(
    image: Image,
    entry: str | None = None,
    machine: str = "x86_64",
    base: int = BASE,
    statements: tuple[certify.Statement, ...] = (),
) -> bytes:
    """The package of an image: one header, the linked image, the cert block of the tail.

    ``entry`` names the function to run; it defaults to the entry of the image itself,
    which the starter keeps at ``pymergetic.rxf.application.main``. ``statements`` are
    the voices over this image (docs/11): they travel in the tail behind the linked
    bytes, named by the header's cert window, and never inside the block a wrapper
    loads and runs.
    """
    if base % 4096:
        raise PackageError("the base of a package is one page")
    linked = link_module().link(image, machine, base)
    if linked.run is None:
        raise PackageError(f"{machine} has no run cell to call")
    path = entry or image.entry_path or DEFAULT_ENTRY
    handle = image.resolve(f"{path}.bytecode")
    if handle is None:
        raise PackageError(f"{path} has no bytecode to run")
    words = struct.unpack("<3Q", linked.handle(f"{path}.bytecode"))
    tail = cert_block(statements)
    cert_at = HEADER_BYTES + len(linked.data)
    total = cert_at + len(tail)
    window = (cert_at, len(tail)) if tail else (0, 0)
    header = struct.pack(
        HEADER,
        MAGIC,
        VERSION,
        total,
        base,
        HEADER_BYTES,
        len(linked.data),
        linked.run,
        linked.args,
        linked.out,
        linked.frame,
        *words,
        0,  # the arena window of the image (words 13..14): named, still zero
        0,
        *window,
        *([0] * 8),
    )
    assert len(header) == HEADER_BYTES
    return header + linked.data + tail


def sha256(package: bytes) -> str:
    """What the tests compare an artifact's package to."""
    return hashlib.sha256(package).hexdigest()


def block_digest(package: bytes) -> str:
    """The SHA-256 of the linked image of a built package: the word a voice signs.

    This is the digest of the exact bytes a wrapper loads into its block and
    runs -- the `block` word of a statement (``certify.Statement``) and the
    check ``native/wrap/wrap_activate.c`` makes at boot. It covers the linked
    image only, never the cert block of the tail: the statements sign the
    program, and the tail travels beside it.
    """
    parsed = parse(package)
    region = package[parsed.image : parsed.image + parsed.image_bytes]
    return hashlib.sha256(region).hexdigest()


def parse(package: bytes) -> Package:
    """The header of the bytes, checked the way a wrapper checks it before it loads."""
    if len(package) < HEADER_BYTES:
        raise PackageError("the package is shorter than its header")
    words = struct.unpack(HEADER, package[:HEADER_BYTES])
    if words[0] != MAGIC or words[1] != VERSION:
        raise PackageError("the package magic or version is not RXFW 1")
    if any(words[ARENA_FROM:CERT_FROM]) or any(words[CERT_FROM + 2 :]):
        raise PackageError("the reserved words of the package header are not zero")
    total, base, image, image_bytes = words[2], words[3], words[4], words[5]
    run, args, out, frame = words[6], words[7], words[8], words[9]
    cert_at, cert_bytes = words[CERT_FROM], words[CERT_FROM + 1]
    if total != len(package):
        raise PackageError("the total of the package is not its size")
    if base % 4096:
        raise PackageError("the base of the package is not one page")
    if image < HEADER_BYTES or image_bytes < CONTEXT_BYTES or image + image_bytes > total:
        raise PackageError("the image is not within the package")
    if (cert_at == 0) != (cert_bytes == 0):
        raise PackageError("the cert window of the package header is not whole")
    if cert_bytes:
        if cert_bytes % CERT_RECORD_BYTES:
            raise PackageError("the cert block is not whole records")
        if cert_at < image + image_bytes or cert_at + cert_bytes != total:
            raise PackageError("the cert block is not the tail of the package")
    limit = base + image_bytes
    for address, name in ((run, "run"), (args, "args"), (out, "out"), (frame, "frame")):
        if not base <= address <= limit - 8:
            raise PackageError(f"the {name} address is not within the image")
    words_of_image = struct.unpack_from("<2Q", package, image)
    if words_of_image != (CONTEXT_VERSION, CONTEXT_BYTES):
        raise PackageError("the image does not start with the runtime context")
    return Package(
        total=total,
        base=base,
        image=image,
        image_bytes=image_bytes,
        run=run,
        args=args,
        out=out,
        frame=frame,
        entry=(words[10], words[11], words[12]),
        cert=(cert_at, cert_bytes),
    )


def cert_block(statements: Sequence[certify.Statement]) -> bytes:
    """The cert block: one fixed record per statement, the tail a wrapper reads.

    A record is the three digests as raw words (32 each), the key id zero-padded
    to ``CERT_KEY_BYTES``, the stamp as one little-endian word and the 64-byte
    signature: ``CERT_RECORD_BYTES`` in all. This is the one layout of the
    statements' binary face -- ``native/wrap/wrap_activate.c`` parses exactly
    these bytes -- and its meaning is one ``certify.Statement`` each.
    """
    records = bytearray()
    for statement in statements:
        key = statement.key_id.encode("ascii")
        if len(key) >= CERT_KEY_BYTES:
            raise PackageError(f"the key id {statement.key_id!r} does not fit a cert record")
        record = (
            bytes.fromhex(statement.image_digest)
            + bytes.fromhex(statement.text_digest)
            + bytes.fromhex(statement.block_digest)
            + key.ljust(CERT_KEY_BYTES, b"\0")
            + struct.pack("<Q", statement.issued)
            + statement.signature
        )
        assert len(record) == CERT_RECORD_BYTES
        records += record
    return bytes(records)


def statements(package: bytes) -> tuple[certify.Statement, ...]:
    """The statements of a package's cert block, in record order (empty: none)."""
    parsed = parse(package)
    at, size = parsed.cert
    found: list[certify.Statement] = []
    for index in range(size // CERT_RECORD_BYTES):
        record = package[at + index * CERT_RECORD_BYTES : at + (index + 1) * CERT_RECORD_BYTES]
        key, _, padding = record[96 : 96 + CERT_KEY_BYTES].partition(b"\0")
        if any(padding):
            raise PackageError(f"cert record {index}: the key id is not zero-padded")
        try:
            found.append(
                certify.Statement(
                    record[0:32].hex(),
                    record[32:64].hex(),
                    record[64:96].hex(),
                    key.decode("ascii"),
                    struct.unpack_from("<Q", record, 160)[0],
                    record[168:232],
                )
            )
        except (UnicodeDecodeError, ValueError) as error:
            raise PackageError(f"cert record {index} is not a statement: {error}") from error
        except certify.MalformedStatement as refusal:
            raise PackageError(f"cert record {index} is not a statement: {refusal}") from refusal
    return tuple(found)
