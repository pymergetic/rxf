"""Commands on the binary an image is: `rxf binary <command> SOURCE`.

`build` is the packaging layer: the image of SOURCE becomes one artifact a boot
firmware or an OS loader can start (`rxf binary build --target=bios`, and `uefi` and
`elf`). What the artifact is and how it enters the image is the entry convention of
`native/wrap/wrap.h`; the build itself is `pymergetic.rxf.binary.builder`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pymergetic.rxf import binary, source
from pymergetic.rxf.binary import builder, package
from pymergetic.rxf.cli import Problems


def check(src: str) -> dict[str, Any]:
    """Every native function has code and ABI for every target; what the rest calls exists.

    Exit status 1 when something is missing or disagrees.
    """
    report = binary.check(source.load(src))
    if not report["ok"]:
        raise Problems(report)
    return report


def build(target: str, src: str = "", out: str = "dist") -> dict[str, Any]:
    """The artifact of one seat from the image of SOURCE.

    TARGET is `bios` (a bootable disk image), `uefi` (a PE/COFF EFI application,
    BOOTX64.EFI) or `elf` (a static executable). SOURCE is a module tree or image
    bytes and defaults to the starter of this repository. The artifact is written to
    OUT (a directory) and the report names it, its size, and the package it carries.
    """
    image = source.load(src or str(package.REPO / "starter"))
    return builder.build(target, image, Path(out))
