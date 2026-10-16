"""The RXFW package: the header and the linked image a wrapper carries beside its code.

A package is the memory image of the program (the one ``tools/native/link.py`` makes:
context, journal, function slots, object entries, arena, heap, scratch) behind a header
of ``HEADER_BYTES`` that names where it runs. Nothing in it is relocated: the image is
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
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from pymergetic.rxftk.core.object import Image

REPO = Path(__file__).resolve().parents[4]
NATIVE_TOOLS = REPO / "tools" / "native"

MAGIC = b"RXFW"
VERSION = 1
HEADER_BYTES = 192
BASE = 0x04000000  # the base every seat places its block at
DEFAULT_ENTRY = "pymergetic.rxf.application.main"
HEADER = "<4sI" + "Q" * 23  # magic, version, then the words and the reserved tail
RESERVED_FROM = 13  # words 13..25 of the header are reserved and zero
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


def link_module() -> ModuleType:
    """``tools/native/link.py``: the linker the images are made with.

    The native tools are scripts, not a package (see the pytest path of the repo), so
    they are reached by their directory.
    """
    tools = str(NATIVE_TOOLS)
    if tools not in sys.path:
        sys.path.insert(0, tools)
    return importlib.import_module("link")


def build(image: Image, entry: str | None = None, machine: str = "x86_64",
          base: int = BASE) -> bytes:
    """The package of an image: one header and the linked image behind it.

    ``entry`` names the function to run; it defaults to the entry of the image itself,
    which the starter keeps at ``pymergetic.rxf.application.main``.
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
    total = HEADER_BYTES + len(linked.data)
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
        *([0] * 12),
    )
    assert len(header) == HEADER_BYTES
    return header + linked.data


def sha256(package: bytes) -> str:
    """What the tests compare an artifact's package to."""
    return hashlib.sha256(package).hexdigest()


def parse(package: bytes) -> Package:
    """The header of the bytes, checked the way a wrapper checks it before it loads."""
    if len(package) < HEADER_BYTES:
        raise PackageError("the package is shorter than its header")
    words = struct.unpack(HEADER, package[:HEADER_BYTES])
    if words[0] != MAGIC or words[1] != VERSION:
        raise PackageError("the package magic or version is not RXFW 1")
    if any(words[RESERVED_FROM:]):
        raise PackageError("the reserved words of the package header are not zero")
    total, base, image, image_bytes = words[2], words[3], words[4], words[5]
    run, args, out, frame = words[6], words[7], words[8], words[9]
    if total != len(package):
        raise PackageError("the total of the package is not its size")
    if base % 4096:
        raise PackageError("the base of the package is not one page")
    if image < HEADER_BYTES or image_bytes < CONTEXT_BYTES or image + image_bytes > total:
        raise PackageError("the image is not within the package")
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
    )
