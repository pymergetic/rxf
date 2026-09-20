"""Real shared-starter PE32+ UEFI certification."""

from __future__ import annotations

import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.checker import check
from pymergetic.rxf.executable import build_executable_image
from pymergetic.rxf.executable.models import SegmentKind
from pymergetic.rxf.executable.pe import (
    MACHINE_AARCH64,
    MACHINE_X86_64,
    PEFormatError,
    check_pe32_plus,
    extract_pe_rxf,
    format_pe32_plus,
    install_uefi_startup,
)
from pymergetic.rxf.executable.targets import resolve_executable_target
from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template
from pymergetic.rxf.model.target import X86_64_UEFI_TARGET_ID
from pymergetic.rxf.output.engine import unpack_layout

ROOT = Path(__file__).parents[1]


def _startup(tmp: Path, arch: str) -> bytes:
    clang = shutil.which("clang-18")
    objcopy = shutil.which("llvm-objcopy-18")
    linker = shutil.which("ld.lld-18")
    if not clang or not objcopy or not linker:
        pytest.fail("clang-18, ld.lld-18, and llvm-objcopy-18 are required")
    triple = "x86_64-none-elf" if arch == "x86_64" else "aarch64-none-elf"
    obj = tmp / f"{arch}.o"
    linked = tmp / f"{arch}.elf"
    raw = tmp / f"{arch}.bin"
    subprocess.run(
        [
            clang,
            "-target",
            triple,
            "-ffreestanding",
            "-c",
            ROOT / f"native/executable/uefi/{arch}_start.S",
            "-o",
            obj,
        ],
        check=True,
    )
    source = obj
    if arch == "aarch64":
        subprocess.run(
            [linker, "-Ttext=0", "--entry=efi_main", "-o", linked, obj],
            check=True,
        )
        source = linked
    subprocess.run([objcopy, f"--dump-section=.text={raw}", source], check=True)
    return raw.read_bytes()


@pytest.mark.parametrize(
    ("target", "arch", "machine"),
    [
        ("x86_64_uefi_sysv", "x86_64", MACHINE_X86_64),
        ("aarch64_uefi_aapcs64", "aarch64", MACHINE_AARCH64),
    ],
)
def test_shared_starter_uefi_is_deterministic_and_independently_valid(
    tmp_path: Path, target: str, arch: str, machine: int
) -> None:
    image = build_executable_image(
        starter_template().build(),
        STARTER_MAIN_FUNCTION_ID,
        resolve_executable_target(target),
    )
    bound = install_uefi_startup(image, _startup(tmp_path, arch))
    first = format_pe32_plus(bound)
    assert first == format_pe32_plus(bound)
    parsed = check_pe32_plus(first, expected_machine=machine)
    graph = next(s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE)
    assert extract_pe_rxf(first) == graph.data
    restored = layout_to_container(unpack_layout(extract_pe_rxf(first)))
    assert not check(restored)
    original = starter_template().build()
    restored_nodes = {n.id: n for n in restored.nodes}
    assert {n.id for n in original.nodes} <= restored_nodes.keys()
    assert all(restored_nodes[n.id].data == n.data for n in original.nodes)
    graph_section = next(s for s in parsed.sections if s.name == ".rxfimg")
    # Simulate a firmware load at a different base. Only derived runtime pointers
    # move; persisted IDs/offsets and the original graph are never rebased.
    rebased = bytearray(first)
    reloc_rva, reloc_size = parsed.directories[5]
    reloc_section = next(s for s in parsed.sections if s.name == ".reloc")
    cursor = reloc_section.raw_offset + reloc_rva - reloc_section.virtual_address
    end = cursor + reloc_size
    delta = 0x10000000
    relocated = []
    while cursor < end:
        page, block = struct.unpack_from("<II", first, cursor)
        for at in range(cursor + 8, cursor + block, 2):
            value = struct.unpack_from("<H", first, at)[0]
            if not value:
                continue
            rva = page + (value & 0xFFF)
            section = next(
                s
                for s in parsed.sections
                if s.virtual_address <= rva < s.virtual_address + s.virtual_size
            )
            off = section.raw_offset + rva - section.virtual_address
            pointer = struct.unpack_from("<Q", first, off)[0]
            struct.pack_into("<Q", rebased, off, pointer + delta)
            relocated.append(section.name)
        cursor += block
    assert relocated and ".rxfimg" not in relocated
    assert (
        rebased[graph_section.raw_offset : graph_section.raw_offset + len(graph.data)]
        == graph.data
    )
    malformed = bytearray(first)
    struct.pack_into(
        "<Q", malformed, graph_section.raw_offset + 24, graph_section.raw_size + 1
    )
    with pytest.raises(PEFormatError, match="image_size"):
        extract_pe_rxf(bytes(malformed))
    # A valid RXF elsewhere is not authority: removing its descriptor refuses.
    malformed = bytearray(first)
    table = int.from_bytes(first[0x3C:0x40], "little") + 24 + 0xF0
    index = parsed.sections.index(graph_section)
    malformed[table + index * 40 : table + index * 40 + 8] = b".decoy\0\0"
    with pytest.raises(PEFormatError, match="exactly one"):
        extract_pe_rxf(bytes(malformed))
    assert parsed.entry_rva == bound.entry_address - parsed.image_base
    readobj = shutil.which("llvm-readobj-18")
    if not readobj:
        pytest.fail("llvm-readobj-18 is required for UEFI certification")
    efi = tmp_path / f"{arch}.efi"
    efi.write_bytes(first)
    report = subprocess.run(
        [readobj, "--file-headers", "--sections", "--coff-basereloc", efi],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Subsystem: IMAGE_SUBSYSTEM_EFI_APPLICATION" in report
    assert "Type: DIR64" in report


def test_x86_uefi_has_distinct_strict_target_authority() -> None:
    container = starter_template().build()
    from pymergetic.rxf.execution import preflight

    plan = preflight(container, STARTER_MAIN_FUNCTION_ID, X86_64_UEFI_TARGET_ID)
    assert plan.ok and len(plan.functions) == 4
    assert all(item.code_id for item in plan.functions)


def test_explicit_absolute_fixup_rebases_only_bound_code(tmp_path: Path) -> None:
    from pymergetic.rxf.executable.pe import _relocation_addresses
    from pymergetic.rxf.execution import preflight
    from pymergetic.rxf.execution.decode import decode_code
    from pymergetic.rxf.model.execution import (
        CallRole,
        RelocationKind,
        RelocationObject,
    )
    from pymergetic.rxf.model.refs import RefDef
    from pymergetic.rxf.output.cell import CELL_HEADER_SIZE
    from pymergetic.rxf.schema import RefKind

    container = starter_template().build()
    target = resolve_executable_target("aarch64_uefi_aapcs64")
    binding = preflight(container, STARTER_MAIN_FUNCTION_ID, target.target_id)
    source = container.node_by_id(binding.functions[0].code_id)
    assert source is not None
    source_bytes = source.data
    relocation_id = max(n.id for n in container.nodes) + 1
    relocation = RelocationObject(
        relocation_id,
        "explicit_pe_pointer",
        source.id,
        0,
        RelocationKind.ABSOLUTE,
        STARTER_MAIN_FUNCTION_ID,
        8,
    ).to_node()
    source.refs.append(
        RefDef(source.id, relocation.id, RefKind.DATA, to_off=int(CallRole.RELOCATION))
    )
    container.nodes.append(relocation)
    image = build_executable_image(container, STARTER_MAIN_FUNCTION_ID, target)
    bound = install_uefi_startup(image, _startup(tmp_path, "aarch64"))
    graph = next(s for s in bound.segments if s.kind is SegmentKind.RXF_IMAGE)
    layout = unpack_layout(graph.data)
    restored = layout_to_container(layout)
    restored_source = restored.node_by_id(source.id)
    assert restored_source is not None
    assert restored_source.data == source_bytes
    runtime = next(s for s in bound.segments if s.kind is SegmentKind.RUNTIME_TABLES)
    count = struct.unpack_from("<Q", runtime.data, 16)[0]
    functions = {
        struct.unpack_from("<Q", runtime.data, 144 + i * 24)[0]: struct.unpack_from(
            "<Q", runtime.data, 160 + i * 24
        )[0]
        for i in range(count)
    }
    code = decode_code(source)
    patch = functions[code.owner_function] - code.entry_offset
    addresses = _relocation_addresses(bound)
    graph_addresses = [
        a
        for a in addresses
        if graph.virtual_address <= a < graph.virtual_address + len(graph.data)
    ]
    assert graph_addresses == [patch]
    cell = next(c for c in layout.heap.cells if c.header.id == source.id)
    original_raw = (
        graph.virtual_address
        + layout.header.heap_off
        + cell.offset
        + CELL_HEADER_SIZE
        + len(source.data)
        - code.byte_count
    )
    assert original_raw not in addresses
    blob = format_pe32_plus(bound)
    assert extract_pe_rxf(blob) == graph.data


def test_checker_refuses_missing_relocations(tmp_path: Path) -> None:
    image = build_executable_image(
        starter_template().build(),
        STARTER_MAIN_FUNCTION_ID,
        resolve_executable_target("x86_64_uefi_sysv"),
    )
    blob = bytearray(
        format_pe32_plus(install_uefi_startup(image, _startup(tmp_path, "x86_64")))
    )
    pe = int.from_bytes(blob[0x3C:0x40], "little")
    blob[pe + 24 + 112 + 40 : pe + 24 + 112 + 48] = b"\0" * 8
    with pytest.raises(PEFormatError, match="reloc"):
        check_pe32_plus(bytes(blob))
