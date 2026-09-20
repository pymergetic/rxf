"""Legacy BIOS envelope and shared compiled-entry certification."""

from __future__ import annotations

import shutil
import struct
import subprocess
import zlib
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.checker import check
from pymergetic.rxf.executable.bios import (
    _HEADER,
    _SEGMENT,
    BOOT_LOADER_SECTORS_OFFSET,
    MANIFEST_OFFSET,
    PAYLOAD_OFFSET,
    SECTOR_SIZE,
    STAGE2_SIZE,
    BiosImageError,
    build_bios_image,
    extract_bios_rxf,
    parse_bios_image,
)
from pymergetic.rxf.executable.build import prepare_executable
from pymergetic.rxf.executable.models import SegmentKind, SegmentPermissions
from pymergetic.rxf.executable.pipeline import build_executable_image
from pymergetic.rxf.executable.targets import resolve_executable_target
from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template
from pymergetic.rxf.output.engine import unpack_layout

TARGET = resolve_executable_target("x86_64_bios_sysv")
LINUX_TARGET = resolve_executable_target("x86_64_linux_sysv")


@lru_cache(maxsize=1)
def _image():
    compiled = build_executable_image(
        starter_template().build(), STARTER_MAIN_FUNCTION_ID, LINUX_TARGET
    )
    return replace(compiled, target=TARGET)


def _raw() -> bytearray:
    return bytearray(build_bios_image(_image()))


def _header(raw: bytearray) -> list[int | bytes]:
    return list(_HEADER.unpack_from(raw, MANIFEST_OFFSET))


def _put_header(raw: bytearray, values: list[int | bytes]) -> None:
    _HEADER.pack_into(raw, MANIFEST_OFFSET, *values)


def _descriptor(raw: bytearray, index: int = 0) -> list[int]:
    return list(
        _SEGMENT.unpack_from(
            raw, MANIFEST_OFFSET + _HEADER.size + index * _SEGMENT.size
        )
    )


def _put_descriptor(raw: bytearray, values: list[int], index: int = 0) -> None:
    _SEGMENT.pack_into(
        raw, MANIFEST_OFFSET + _HEADER.size + index * _SEGMENT.size, *values
    )


def test_native_loader_builds_with_bounded_real_mode_stage() -> None:
    from pymergetic.rxf.executable.bios import _native_blobs

    boot, stage = _native_blobs()
    assert len(boot) == SECTOR_SIZE and boot[-2:] == b"\x55\xaa"
    assert len(stage) == STAGE2_SIZE


def test_exact_geometry_deterministic_pack_parse_and_shared_entry() -> None:
    image = _image()
    first = build_bios_image(image)
    assert first == build_bios_image(image)
    assert first[510:512] == b"\x55\xaa"
    assert (
        struct.unpack_from("<H", first, BOOT_LOADER_SECTORS_OFFSET)[0]
        == len(first) // SECTOR_SIZE - 1
    )
    assert MANIFEST_OFFSET == SECTOR_SIZE + STAGE2_SIZE
    assert PAYLOAD_OFFSET == SECTOR_SIZE + STAGE2_SIZE + 4096
    manifest = parse_bios_image(first)
    assert manifest.entry_address == image.entry_address
    assert manifest.entry_function_id == STARTER_MAIN_FUNCTION_ID
    assert manifest.segments == tuple(
        replace(segment, data=segment.data, permissions=segment.permissions)
        for segment in manifest.segments
    )
    graph = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    extracted = extract_bios_rxf(first)
    assert extracted == graph.data
    restored = layout_to_container(unpack_layout(extracted))
    assert not check(restored)
    original = starter_template().build()
    assert {n.id for n in original.nodes} <= {n.id for n in restored.nodes}
    restored_nodes = {n.id: n for n in restored.nodes}
    assert all(restored_nodes[n.id].data == n.data for n in original.nodes)


def test_extraction_uses_descriptor_and_binary_header_bounds() -> None:
    raw = _raw()
    manifest = parse_bios_image(bytes(raw))
    index = next(
        i for i, s in enumerate(manifest.segments) if s.kind is SegmentKind.RXF_IMAGE
    )
    descriptor = _descriptor(raw, index)
    descriptor[5] = int(SegmentKind.TEXT)
    _put_descriptor(raw, descriptor, index)
    with pytest.raises(BiosImageError, match="exactly one"):
        extract_bios_rxf(bytes(raw))
    raw = _raw()
    descriptor = _descriptor(raw, index)
    struct.pack_into("<Q", raw, PAYLOAD_OFFSET + descriptor[3] + 24, descriptor[2] + 1)
    header = _header(raw)
    header[10] = (
        zlib.crc32(raw[PAYLOAD_OFFSET : PAYLOAD_OFFSET + int(header[9])]) & 0xFFFFFFFF
    )
    _put_header(raw, header)
    with pytest.raises(BiosImageError, match="image_size"):
        extract_bios_rxf(bytes(raw))


def test_checksum_mutation_refuses() -> None:
    raw = _raw()
    raw[PAYLOAD_OFFSET] ^= 1
    with pytest.raises(BiosImageError, match="checksum"):
        parse_bios_image(bytes(raw))


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: raw.__setitem__(slice(510, 512), b"NO"), "signature"),
        (
            lambda raw: struct.pack_into("<H", raw, BOOT_LOADER_SECTORS_OFFSET, 1),
            "sector count",
        ),
        (lambda raw: raw.extend(b"\0"), "geometry"),
        (lambda raw: raw.__setitem__(MANIFEST_OFFSET, 0), "header"),
    ],
)
def test_outer_geometry_and_header_refuse(mutation, message: str) -> None:
    raw = _raw()
    mutation(raw)
    with pytest.raises(BiosImageError, match=message):
        parse_bios_image(bytes(raw), verify_checksum=False)


@pytest.mark.parametrize("field", [1, 2, 11, 12])
def test_version_header_size_flags_and_reserved_refuse(field: int) -> None:
    raw = _raw()
    values = _header(raw)
    values[field] = int(values[field]) + 1
    _put_header(raw, values)
    with pytest.raises(BiosImageError):
        parse_bios_image(bytes(raw), verify_checksum=False)


def test_payload_bounds_and_padded_geometry_refuse() -> None:
    raw = _raw()
    values = _header(raw)
    values[9] = int(values[9]) + SECTOR_SIZE
    _put_header(raw, values)
    with pytest.raises(BiosImageError, match="geometry"):
        parse_bios_image(bytes(raw), verify_checksum=False)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (1, (1 << 64) - 1, "identity map|overflow"),
        (2, (1 << 64) - 1, "file size"),
        (3, 1, "overlap|alias|ownership"),
        (4, int(SegmentPermissions.WRITE | SegmentPermissions.EXECUTE), "permissions"),
        (4, 8, "permissions"),
        (5, 0xFFFF, "kind"),
        (6, 1, "reserved"),
    ],
)
def test_descriptor_validation_refuses(field: int, value: int, message: str) -> None:
    raw = _raw()
    descriptor = _descriptor(raw)
    descriptor[field] = value
    _put_descriptor(raw, descriptor)
    with pytest.raises(BiosImageError, match=message):
        parse_bios_image(bytes(raw), verify_checksum=False)


def test_unsorted_memory_overlap_and_payload_alias_refuse() -> None:
    for field, message in ((0, "unsorted|overlap"), (3, "overlap|alias|ownership")):
        raw = _raw()
        first = _descriptor(raw, 1)
        second = _descriptor(raw, 2)
        assert first[2] > 0 and second[2] > 0
        second[field] = first[field]
        _put_descriptor(raw, second, 2)
        with pytest.raises(BiosImageError, match=message):
            parse_bios_image(bytes(raw), verify_checksum=False)


def test_unowned_payload_entry_and_function_id_refuse() -> None:
    raw = _raw()
    descriptor = _descriptor(raw, 1)
    assert descriptor[2] > 0
    descriptor[2] -= 1
    _put_descriptor(raw, descriptor, 1)
    with pytest.raises(BiosImageError, match="ownership|alias|overlap"):
        parse_bios_image(bytes(raw), verify_checksum=False)
    raw = _raw()
    values = _header(raw)
    values[5] = 0
    _put_header(raw, values)
    with pytest.raises(BiosImageError, match="Function ID"):
        parse_bios_image(bytes(raw), verify_checksum=False)
    raw = _raw()
    values = _header(raw)
    values[4] = 0x100000
    _put_header(raw, values)
    with pytest.raises(BiosImageError, match="entry"):
        parse_bios_image(bytes(raw), verify_checksum=False)


def test_build_rejects_overlap_wx_and_is_integrated() -> None:
    image = _image()
    with pytest.raises(ValueError, match="overlap"):
        replace(
            image,
            segments=(
                image.segments[0],
                replace(
                    image.segments[1],
                    virtual_address=image.segments[0].virtual_address,
                ),
            ),
        )
    with pytest.raises(ValueError, match="writable and executable"):
        replace(
            image.segments[0],
            permissions=SegmentPermissions.WRITE | SegmentPermissions.EXECUTE,
        )
    prepared = prepare_executable(
        starter_template().build(),
        b"bios-integration",
        STARTER_MAIN_FUNCTION_ID,
        "x86_64_bios_sysv",
    )
    assert (
        prepared["ok"]
        and parse_bios_image(prepared["artifact"]).entry_function_id
        == STARTER_MAIN_FUNCTION_ID
    )


@pytest.mark.skipif(
    shutil.which("qemu-system-x86_64") is None, reason="QEMU unavailable"
)
def test_qemu_runs_actual_shared_compiled_payload_and_refuses_nonzero(
    tmp_path: Path,
) -> None:
    disk = tmp_path / "rxf.img"
    disk.write_bytes(build_bios_image(_image()))
    assert len(extract_bios_rxf(disk.read_bytes())) > 4 * 1024 * 1024
    command = [
        "qemu-system-x86_64",
        "-machine",
        "pc",
        "-display",
        "none",
        "-serial",
        "stdio",
        "-no-reboot",
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-boot",
        "order=c,strict=on",
        "-drive",
        f"format=raw,file={disk},if=ide,index=0,media=disk",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=20, check=False
    )
    assert result.returncode == 33
    assert "RXF-BIOS-PASS 570" in result.stdout
    raw = bytearray(disk.read_bytes())
    raw[PAYLOAD_OFFSET] ^= 1
    disk.write_bytes(raw)
    refused = subprocess.run(
        command, capture_output=True, text=True, timeout=20, check=False
    )
    assert refused.returncode == 35
    assert refused.stdout == "RXF-BIOS-REFUSED checksum\n"
