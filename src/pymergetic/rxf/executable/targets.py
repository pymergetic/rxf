"""Canonical executable targets and deterministic target resolution."""

from __future__ import annotations

from pymergetic.rxf.executable.models import (
    ArtifactFormat,
    ExecutablePlatform,
    ExecutableTarget,
)
from pymergetic.rxf.model.target import (
    AAPCS64_ID,
    AARCH64_ID,
    AARCH64_LINUX_TARGET_ID,
    AARCH64_UEFI_TARGET_ID,
    BIOS_ID,
    LINUX_ID,
    SYSV_ID,
    UEFI_ID,
    X86_64_BIOS_TARGET_ID,
    X86_64_ID,
    X86_64_LINUX_TARGET_ID,
    X86_64_UEFI_TARGET_ID,
)

EXECUTABLE_TARGETS = (
    ExecutableTarget(
        X86_64_LINUX_TARGET_ID,
        "x86_64_linux_sysv",
        ArtifactFormat.ELF64,
        ExecutablePlatform.LINUX,
        X86_64_ID,
        SYSV_ID,
        LINUX_ID,
    ),
    ExecutableTarget(
        AARCH64_LINUX_TARGET_ID,
        "aarch64_linux_aapcs64",
        ArtifactFormat.ELF64,
        ExecutablePlatform.LINUX,
        AARCH64_ID,
        AAPCS64_ID,
        LINUX_ID,
    ),
    ExecutableTarget(
        X86_64_UEFI_TARGET_ID,
        "x86_64_uefi_sysv",
        ArtifactFormat.PE32_PLUS,
        ExecutablePlatform.UEFI,
        X86_64_ID,
        SYSV_ID,
        UEFI_ID,
    ),
    ExecutableTarget(
        AARCH64_UEFI_TARGET_ID,
        "aarch64_uefi_aapcs64",
        ArtifactFormat.PE32_PLUS,
        ExecutablePlatform.UEFI,
        AARCH64_ID,
        AAPCS64_ID,
        UEFI_ID,
    ),
    ExecutableTarget(
        X86_64_BIOS_TARGET_ID,
        "x86_64_bios_sysv",
        ArtifactFormat.BIOS_DISK_IMAGE,
        ExecutablePlatform.BIOS,
        X86_64_ID,
        SYSV_ID,
        BIOS_ID,
        legacy_shim=True,
    ),
)

_BY_ID = {target.target_id: target for target in EXECUTABLE_TARGETS}
_BY_NAME = {target.name: target for target in EXECUTABLE_TARGETS}
if len(_BY_ID) != len(EXECUTABLE_TARGETS) or len(_BY_NAME) != len(EXECUTABLE_TARGETS):
    raise RuntimeError("duplicate executable target identity")


def resolve_executable_target(value: int | str) -> ExecutableTarget:
    """Resolve a canonical target by uint64 ID, decimal ID string, or exact name."""
    if isinstance(value, bool):
        raise TypeError("executable target must be an ID or name")
    if isinstance(value, int):
        target = _BY_ID.get(value)
    elif isinstance(value, str):
        target = _BY_NAME.get(value)
        if target is None and value.isdecimal():
            target = _BY_ID.get(int(value))
    else:
        raise TypeError("executable target must be an ID or name")
    if target is None:
        raise ValueError(f"unknown executable target {value!r}")
    return target
