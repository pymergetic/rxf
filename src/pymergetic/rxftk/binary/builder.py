"""The builders: a package and the wrapper of a seat, one artifact at a time.

Every artifact is the same two things: the RXFW package of the image
(``pymergetic.rxftk.binary.package``) and the wrapper of one seat
(``native/wrap/``), built with the pinned Clang of the repo (the toolchain
``tools/native/native_build.py`` records in CompilerProvenance). The seat decides the
shape of the artifact: a bootable disk image (``bios``), a PE/COFF EFI application
(``uefi``), or a static ELF executable (``elf``). There is no code generator and no
per-seat program: the wrapper C is the program of the shell, the image is the program.

The builds run from a work directory because the package is assembled into the
wrapper (``native/wrap/wrap_package.S`` reads ``rxf_wrap_package.bin`` beside it).
"""

from __future__ import annotations

import os
import struct
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pymergetic.rxftk import certify
from pymergetic.rxftk.core.object import Image

from . import package

REPO = package.REPO
WRAP = REPO / "native" / "wrap"

CLANG = os.environ.get("RXF_CLANG", "clang-18")
LINKER = os.environ.get("RXF_LINKER", "ld.lld-18")
OBJCOPY = os.environ.get("RXF_OBJCOPY", "llvm-objcopy-18")

TARGETS = ("bios", "uefi", "elf")
ARTIFACTS = {"bios": "rxf-bios.img", "uefi": "BOOTX64.EFI", "elf": "rxf-elf"}
TARGET_OF_TARGET = {"bios": "x86_64-unknown-linux-gnu", "uefi": "x86_64-unknown-windows",
                    "elf": "x86_64-unknown-linux-gnu", "flat": "x86_64-unknown-linux-gnu"}
SOURCES = {
    "bios": ("wrap.c", "wrap_mem.c", "wrap_activate.c", "wrap_crypto.c", "wrap_serial.c",
             "wrap_bios.c", "wrap_boot.S", "wrap_stage2.S", "wrap_package.S", "wrap_trust.S"),
    "uefi": ("wrap.c", "wrap_mem.c", "wrap_activate.c", "wrap_crypto.c", "wrap_serial.c",
             "wrap_uefi.c", "wrap_package.S", "wrap_trust.S"),
    "elf": ("wrap.c", "wrap_mem.c", "wrap_activate.c", "wrap_crypto.c", "wrap_elf.c",
            "wrap_package.S", "wrap_trust.S"),
    # The wrapper body alone: what the emulator harness runs.
    "flat": ("wrap.c", "wrap_mem.c", "wrap_activate.c", "wrap_crypto.c", "wrap_trust.S"),
}
# The trust anchor of a wrapper (native/wrap/wrap_trust.h): the words of the
# deployment, as bytes beside the code and never in the image.
TRUST_MAGIC = b"RXFT"
TRUST_VERSION = 1
TRUST_BYTES = 48
TRUST_KEY_BYTES = 96
TRUST_ID_BYTES = 64
# The compile flags of the repo (tools/native/native_build.py): freestanding code with
# no builtins, no runtime and no relocation.
FLAGS = (
    "-std=c11",
    "-O2",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-stack-protector",
    "-fno-unwind-tables",
    "-fno-asynchronous-unwind-tables",
    "-ffunction-sections",
    "-fdata-sections",
    "-fno-ident",
    "-fno-addrsig",
    "-fno-pic",
    "-fno-jump-tables",
)
FLAT_BASE = 0x800000  # where the emulator harness (tests/emulator.py) places code


class BuildError(Exception):
    """The toolchain could not build the artifact."""


def _run(command: list[str], work: Path) -> None:
    done = subprocess.run(command, cwd=work, capture_output=True, text=True, check=False)
    if done.returncode:
        raise BuildError(
            f"{command[0]} failed with {done.returncode}:\n{done.stderr or done.stdout}"
        )


def _compile(target: str, work: Path) -> list[Path]:
    """Every object of one build: the wrapper C of the seat and the package beside it."""
    objects = []
    for source in SOURCES[target]:
        obj = work / f"{source}.o"
        _run(
            [CLANG, "-target", TARGET_OF_TARGET[target], *FLAGS, "-c", str(WRAP / source),
             "-o", str(obj)],
            work,
        )
        objects.append(obj)
    return objects


def _package(image: Image, entry: str | None, work: Path,
             statements: Sequence[certify.Statement] = ()) -> bytes:
    built = package.build(image, entry=entry, statements=tuple(statements))
    (work / "rxf_wrap_package.bin").write_bytes(built)
    return built


def trust_block(trust: certify.TrustStore | None) -> bytes:
    """The trust anchor of a wrapper as bytes: what ``wrap_trust.S`` embeds.

    This is a build step embedding data (docs: a build step may embed bytes): the
    store's policy words, its keys and its revoked ids, in the layout
    ``native/wrap/wrap_trust.h`` documents and ``native/wrap/wrap_activate.c``
    reads. Without a store the anchor admits no voice -- a wrapper that checks
    nothing for a package that carries nothing to check.
    """
    policy = trust.policy if trust is not None else certify.Policy(0, 0, 0, 1)
    keys = trust.keys if trust is not None else ()
    revoked = tuple(sorted(trust.revoked)) if trust is not None else ()
    for key_id in (*(key.key_id for key in keys), *revoked):
        if len(key_id.encode("ascii")) >= TRUST_ID_BYTES:
            raise BuildError(f"the key id {key_id!r} does not fit the trust anchor")
    head = (
        TRUST_MAGIC
        + struct.pack("<I", TRUST_VERSION)
        + struct.pack("<3Q", policy.now, policy.max_age, policy.skew)
        + struct.pack("<3I", policy.quorum, len(keys), len(revoked))
        + struct.pack("<I", 0)
    )
    assert len(head) == TRUST_BYTES
    body = b"".join(
        key.key_id.encode("ascii").ljust(TRUST_ID_BYTES, b"\0") + key.public for key in keys
    )
    body += b"".join(
        key_id.encode("ascii").ljust(TRUST_ID_BYTES, b"\0") for key_id in revoked
    )
    return head + body


def _trust(trust: certify.TrustStore | None, work: Path) -> bytes:
    anchor = trust_block(trust)
    (work / "rxf_wrap_trust.bin").write_bytes(anchor)
    return anchor


def build(
    target: str,
    image: Image,
    out: Path,
    entry: str | None = None,
    work: Path | None = None,
    *,
    trust: certify.TrustStore | None = None,
    statements: Sequence[certify.Statement] = (),
) -> dict[str, Any]:
    """One artifact of one seat in ``out``; what the tests and the CLI report.

    ``trust`` is the activation seam's Python half (docs/11, the shape the
    certification lane pinned): with a trust store the statements' quorum over
    the image's three words must hold before the artifact is linked or written
    -- the same check the boot path repeats in ``native/wrap/wrap_activate.c``.
    The block word is the digest of the linked bytes, so the package is packed
    first (into the work directory, nothing lands in ``out`` yet) and the quorum
    is judged over what was packed. A certification that does not hold refuses
    the build with its typed ``certify`` refusal.
    """
    if statements and trust is None:
        raise BuildError("certification statements need a trust store")
    if target not in TARGETS:
        raise BuildError(f"no target {target}; the targets are {', '.join(TARGETS)}")
    out.mkdir(parents=True, exist_ok=True)
    artifact = out / ARTIFACTS[target]
    with tempfile.TemporaryDirectory(prefix="rxf-build-") as temporary:
        here = work if work is not None else Path(temporary)
        here.mkdir(parents=True, exist_ok=True)
        built = _package(image, entry, here, statements)
        _trust(trust, here)
        if trust is not None:
            certify.verify_quorum(
                image.digest(),
                image.delta_digest(),
                package.block_digest(built),
                list(statements),
                trust,
            )
        objects = _compile(target, here)
        if target == "uefi":
            _run(
                [
                    LINKER,
                    "-flavor",
                    "link",
                    "/subsystem:efi_application",
                    "/entry:efi_main",
                    "/nodefaultlib",
                    "/opt:ref",  # the gate vectors stay out of the seat (docs: gc-sections)
                    f"/out:{artifact}",
                    *map(str, objects),
                ],
                here,
            )
        elif target == "elf":
            _run(
                [
                    LINKER,
                    "-static",
                    "-nostdlib",
                    "--gc-sections",
                    "-T",
                    str(WRAP / "wrap_elf.ld"),
                    "-o",
                    str(artifact),
                    *map(str, objects),
                ],
                here,
            )
        else:  # bios: the flat image of the linker script, boot sector first
            linked = here / "rxf-bios.elf"
            _run(
                [
                    LINKER,
                    "-nostdlib",
                    "--gc-sections",
                    "-T",
                    str(WRAP / "wrap_bios.ld"),
                    "-o",
                    str(linked),
                    *map(str, objects),
                ],
                here,
            )
            _run([OBJCOPY, "-O", "binary", str(linked), str(artifact)], here)
    return {
        "target": target,
        "artifact": str(artifact),
        "bytes": artifact.stat().st_size,
        "base": package.BASE,
        "entry": entry or image.entry_path or package.DEFAULT_ENTRY,
        "package_sha256": package.sha256(built),
        "block_sha256": package.block_digest(built),
    }


def build_flat(out: Path, work: Path | None = None,
               trust: certify.TrustStore | None = None) -> dict[str, Any]:
    """The wrapper body as a flat binary: what the emulator harness (tests/emulator.py)
    loads to run the wrapper itself, with the package in memory beside it. The link is
    at FLAT_BASE, the code address of the harness. ``trust`` is the anchor the wrapper
    activates against (empty: the wrapper admits no voice and unsigned packages pass,
    the state of the tests that check the run, not the certification)."""
    from . import formats

    with tempfile.TemporaryDirectory(prefix="rxf-flat-") as temporary:
        here = work if work is not None else Path(temporary)
        here.mkdir(parents=True, exist_ok=True)
        _trust(trust, here)
        objects = _compile("flat", here)
        linked = out / "wrap-flat.elf"
        binary = out / "wrap-flat.bin"
        _run(
            [
                LINKER,
                "-static",
                "-nostdlib",
                "-e",
                "rxf_wrap_enter",
                # 0x-prefixed: a bare number would be parsed as hex by the linker.
                f"-Ttext={FLAT_BASE:#x}",
                "-o",
                str(linked),
                *map(str, objects),
            ],
            here,
        )
        _run([OBJCOPY, "-O", "binary", str(linked), str(binary)], here)
    symbols = formats.read_elf(linked.read_bytes()).symbols
    return {
        "binary": str(binary),
        "bytes": binary.stat().st_size,
        "base": FLAT_BASE,
        "enter": symbols["rxf_wrap_enter"],
    }
