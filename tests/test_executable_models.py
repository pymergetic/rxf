"""Focused executable model and durable target corpus invariants."""

from dataclasses import replace

import pytest

from pymergetic.rxf.executable import (
    EXECUTABLE_TARGETS,
    ArtifactFormat,
    ExecutableImage,
    ExecutablePlatform,
    ExecutableProvenance,
    ExecutableSegment,
    ExecutableSymbol,
    RuntimeTable,
    RuntimeTableKind,
    SegmentKind,
    SegmentPermissions,
    SymbolKind,
    resolve_executable_target,
)
from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.model.target import (
    AARCH64_LINUX_TARGET_ID,
    AARCH64_UEFI_TARGET_ID,
    BIOS_ID,
    X86_64_BIOS_TARGET_ID,
    X86_64_LINUX_TARGET_ID,
    X86_64_UEFI_TARGET_ID,
    EnvironmentKind,
    EnvironmentObject,
    RuntimeTargetObject,
    native_target_nodes,
)


def test_complete_target_corpus_and_compatibility_anchors() -> None:
    assert X86_64_LINUX_TARGET_ID == 817
    assert AARCH64_UEFI_TARGET_ID == 818
    assert [(item.target_id, item.name) for item in EXECUTABLE_TARGETS] == [
        (817, "x86_64_linux_sysv"),
        (AARCH64_LINUX_TARGET_ID, "aarch64_linux_aapcs64"),
        (X86_64_UEFI_TARGET_ID, "x86_64_uefi_sysv"),
        (818, "aarch64_uefi_aapcs64"),
        (X86_64_BIOS_TARGET_ID, "x86_64_bios_sysv"),
    ]
    bios = resolve_executable_target(X86_64_BIOS_TARGET_ID)
    assert bios.platform is ExecutablePlatform.BIOS
    assert bios.artifact_format is ArtifactFormat.BIOS_DISK_IMAGE
    assert bios.environment_id == BIOS_ID and bios.legacy_shim


def test_target_resolver_and_target_roundtrip() -> None:
    for target in EXECUTABLE_TARGETS:
        assert resolve_executable_target(target.target_id) is target
        assert resolve_executable_target(str(target.target_id)) is target
        assert resolve_executable_target(target.name) is target
        assert type(target).from_dict(target.to_dict()) == target
    with pytest.raises(ValueError, match="unknown executable target"):
        resolve_executable_target("amd64-linux")


def test_native_target_nodes_roundtrip_all_targets() -> None:
    nodes = native_target_nodes(7)
    environments = {
        node.id: EnvironmentObject.from_node(node)
        for node in nodes
        if node.name in {"linux", "uefi", "bios"}
    }
    targets = {
        node.id: RuntimeTargetObject.from_node(node)
        for node in nodes
        if node.id in {item.target_id for item in EXECUTABLE_TARGETS}
    }
    assert environments[BIOS_ID].kind is EnvironmentKind.BIOS
    assert set(targets) == {item.target_id for item in EXECUTABLE_TARGETS}
    assert targets[817].name == "x86_64_linux_sysv"
    assert targets[818].name == "aarch64_uefi_aapcs64"


def test_starter_carries_complete_target_authority() -> None:
    nodes = {node.id: node for node in starter_template().build().nodes}
    for target in EXECUTABLE_TARGETS:
        assert nodes[target.target_id].name == target.name
    assert EnvironmentObject.from_node(nodes[BIOS_ID]).kind is EnvironmentKind.BIOS


def test_segment_and_image_invariants() -> None:
    target = resolve_executable_target("x86_64_linux_sysv")
    text = ExecutableSegment(
        "text",
        SegmentKind.TEXT,
        0x1000,
        4,
        0x1000,
        0x1000,
        SegmentPermissions.READ | SegmentPermissions.EXECUTE,
        b"code",
    )
    data = ExecutableSegment(
        "data",
        SegmentKind.DATA,
        0x2000,
        8,
        0x2000,
        0x1000,
        SegmentPermissions.READ | SegmentPermissions.WRITE,
        b"data",
    )
    symbol = ExecutableSymbol(5, "entry", SymbolKind.ENTRY, 0x1000, 4)
    table = RuntimeTable(RuntimeTableKind.FUNCTION, 0x2000, 24, (5, 7))
    image = ExecutableImage(
        target,
        5,
        0x1000,
        (text, data),
        (symbol,),
        (table,),
        provenance=ExecutableProvenance(bytes(32), bytes(32), ("rxf",)),
    )
    assert image.entry_address == 0x1000
    with pytest.raises(ValueError, match="writable and executable"):
        replace(data, permissions=SegmentPermissions.WRITE | SegmentPermissions.EXECUTE)
    with pytest.raises(ValueError, match="sorted"):
        replace(image, segments=(data, text))
    with pytest.raises(ValueError, match="sorted and unique"):
        replace(table, object_ids=(7, 5))
    with pytest.raises(ValueError, match="uint64"):
        replace(symbol, object_id=1 << 64)
    with pytest.raises(ValueError, match="32 bytes"):
        ExecutableProvenance(b"short", bytes(32))
