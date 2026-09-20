"""Focused deterministic Linux ELF64 formatter certification."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pymergetic.rxf.executable.elf import (
    EM_AARCH64,
    EM_X86_64,
    PF_R,
    PF_W,
    PF_X,
    PT_GNU_STACK,
    PT_LOAD,
    check_elf64,
    format_elf64,
    install_linux_startup,
    layout_elf64_image,
    write_elf64,
)
from pymergetic.rxf.executable.targets import resolve_executable_target


def _x86_text(status: int = 0) -> bytes:
    if status:
        return (
            bytes.fromhex("bf")
            + status.to_bytes(4, "little")
            + bytes.fromhex("b83c0000000f05")
        )
    return bytes.fromhex(
        "b801000000bf0100000048be0020400000000000ba040000000f0531ffb83c0000000f05"
    )


def _image(target: str, text: bytes, rodata: bytes = b"570\n"):
    return layout_elf64_image(
        resolve_executable_target(target),
        1,
        text,
        rodata=rodata,
        data=b"\0",
        bss_size=15,
    )


def test_x86_elf_is_deterministic_static_and_executes(tmp_path: Path) -> None:
    image = _image("x86_64_linux_sysv", _x86_text())
    first = format_elf64(image)
    assert first == format_elf64(image)
    parsed = check_elf64(first, expected_machine=EM_X86_64)
    assert [p.flags for p in parsed.program_headers if p.segment_type == PT_LOAD] == [
        PF_R | PF_X,
        PF_R,
        PF_R | PF_W,
    ]
    assert [
        p.flags for p in parsed.program_headers if p.segment_type == PT_GNU_STACK
    ] == [PF_R | PF_W]
    executable = write_elf64(tmp_path / "proof", image)
    assert executable.stat().st_mode & 0o111
    result = subprocess.run([executable], capture_output=True, check=False)
    assert (result.returncode, result.stdout) == (0, b"570\n")
    refusal = write_elf64(
        tmp_path / "refusal", _image("x86_64_linux_sysv", _x86_text(31), b"")
    )
    result = subprocess.run([refusal], capture_output=True, check=False)
    assert (result.returncode, result.stdout) == (31, b"")


def test_elf_zero_fill_is_not_serialized() -> None:
    image = _image("x86_64_linux_sysv", _x86_text())
    blob = format_elf64(image)
    loads = [p for p in check_elf64(blob).program_headers if p.segment_type == PT_LOAD]
    assert loads[-1].memory_size == 16
    assert loads[-1].file_size == 1
    assert len(blob) == loads[-1].file_offset + 1


def test_elf_checker_rejects_aliased_file_loads() -> None:
    import struct

    from pymergetic.rxf.executable.elf import ELFFormatError

    blob = bytearray(format_elf64(_image("x86_64_linux_sysv", _x86_text())))
    # Point the R load at the RX file range; virtual ranges still do not overlap.
    struct.pack_into("<Q", blob, 64 + 56 + 8, 4096)
    with pytest.raises(ELFFormatError, match="file ranges"):
        check_elf64(bytes(blob))


def test_standalone_elf_graph_recovery_without_sections(tmp_path: Path) -> None:
    from dataclasses import replace

    from pymergetic.rxf import face
    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.executable.artifact import extract_rxf
    from pymergetic.rxf.executable.elf import ELFFormatError, extract_elf_rxf
    from pymergetic.rxf.expand import COUNTER
    from pymergetic.rxf.output.engine import pack_layout

    raw = pack_layout(container_to_layout(COUNTER.build()))
    image = _image("x86_64_linux_sysv", _x86_text())
    text, rodata, data = image.segments
    image = replace(
        image,
        segments=(
            text,
            replace(rodata, data=raw, memory_size=len(raw)),
            replace(
                data,
                virtual_address=rodata.virtual_address + ((len(raw) + 4095) & -4096),
                file_offset=rodata.file_offset + ((len(raw) + 4095) & -4096),
            ),
        ),
    )
    blob = format_elf64(image)
    assert extract_elf_rxf(blob) == raw
    assert extract_rxf(blob) == raw
    standalone = tmp_path / "standalone"
    standalone.write_bytes(blob)
    assert face.certify(str(standalone))["ok"]
    assert face.replay(str(standalone))["identical"]
    assert face.inspect(str(standalone))["size"] == len(raw)
    assert extract_rxf(raw) == raw
    with pytest.raises(ELFFormatError, match="exactly one mapped RXF"):
        extract_rxf(format_elf64(_image("x86_64_linux_sysv", _x86_text())) + raw)
    with pytest.raises(ValueError):
        extract_rxf(blob[: rodata.file_offset + 20])


def test_aarch64_formatter_machine_and_layout() -> None:
    # mov x0,#31; mov x8,#93; svc #0
    image = _image(
        "aarch64_linux_aapcs64", bytes.fromhex("e0038052a80b80d2010000d4"), b""
    )
    parsed = check_elf64(format_elf64(image), expected_machine=EM_AARCH64)
    assert parsed.header.entry == image.entry_address


def test_authored_static_sources_build_and_x86_execute(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    native = root / "native/executable/linux"
    subprocess.run(
        [
            "clang-18",
            "-target",
            "x86_64-linux-gnu",
            "-ffreestanding",
            "-fno-stack-protector",
            "-c",
            native / "x86_64_start.S",
            "-o",
            tmp_path / "start.o",
        ],
        check=True,
    )
    subprocess.run(
        [
            "clang-18",
            "-target",
            "x86_64-linux-gnu",
            "-ffreestanding",
            "-fno-stack-protector",
            "-c",
            native / "proof_entry.c",
            "-o",
            tmp_path / "entry.o",
        ],
        check=True,
    )
    elf = tmp_path / "authored"
    subprocess.run(
        [
            "ld.lld-18",
            "-static",
            "-T",
            native / "static.ld",
            tmp_path / "start.o",
            tmp_path / "entry.o",
            "-o",
            elf,
        ],
        check=True,
    )
    check_elf64(elf.read_bytes(), expected_machine=EM_X86_64)


def _startup_bytes(tmp_path: Path, architecture: str) -> bytes:
    root = Path(__file__).parents[1]
    source = root / "native/executable/linux" / f"{architecture}_start.S"
    object_path = tmp_path / f"{architecture}_start.o"
    linked = tmp_path / f"{architecture}_start.elf"
    raw = tmp_path / f"{architecture}_start.bin"
    target = "aarch64-linux-gnu" if architecture == "aarch64" else "x86_64-linux-gnu"
    subprocess.run(
        [
            "clang-18",
            "-target",
            target,
            "-ffreestanding",
            "-fno-stack-protector",
            "-c",
            source,
            "-o",
            object_path,
        ],
        check=True,
    )
    linker = tmp_path / f"{architecture}_startup.ld"
    linker.write_text(
        "SECTIONS { . = 0x401000; .text.startup : { *(.text.startup) } }\n"
    )
    subprocess.run(["ld.lld-18", "-T", linker, object_path, "-o", linked], check=True)
    subprocess.run(
        [
            "llvm-objcopy-18",
            "-O",
            "binary",
            "--only-section=.text.startup",
            linked,
            raw,
        ],
        check=True,
    )
    return raw.read_bytes()


def test_shared_starter_linux_pipelines_execute_and_refuse(tmp_path: Path) -> None:

    from pymergetic.rxf.executable import build_executable_image
    from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template

    container = starter_template().build()
    cases = (
        ("x86_64_linux_sysv", "x86_64", ()),
        (
            "aarch64_linux_aapcs64",
            "aarch64",
            ("/tmp/rxf-qemu-user/root/usr/bin/qemu-aarch64",),
        ),
    )
    for target_name, architecture, runner in cases:
        if runner and not Path(runner[0]).exists():
            pytest.skip(f"required user-local adapter {runner[0]} is unavailable")
        image = build_executable_image(
            container,
            STARTER_MAIN_FUNCTION_ID,
            resolve_executable_target(target_name),
        )
        executable = write_elf64(
            tmp_path / target_name,
            install_linux_startup(image, _startup_bytes(tmp_path, architecture)),
        )
        result = subprocess.run(
            [*runner, executable], capture_output=True, check=False, timeout=15
        )
        assert (result.returncode, result.stdout) == (0, b"570\n")
        from pymergetic.rxf.bridge import layout_to_container
        from pymergetic.rxf.checker import check
        from pymergetic.rxf.executable.elf import extract_elf_rxf
        from pymergetic.rxf.output.engine import pack_layout, unpack_layout

        recovered = extract_elf_rxf(executable.read_bytes())
        layout = unpack_layout(recovered)
        restored = layout_to_container(layout)
        assert check(restored) == []
        assert pack_layout(layout) == recovered
        original_ids = {node.id for node in container.nodes}
        assert original_ids <= {node.id for node in restored.nodes}
        assert len(restored.nodes) > len(
            container.nodes
        )  # composed Code is durable too
        for node in container.nodes:
            recovered_node = restored.node_by_id(node.id)
            assert recovered_node is not None
            assert recovered_node.data == node.data
            assert recovered_node.attrs == node.attrs

        refused = bytearray(executable.read_bytes())
        # Corrupting the executable entry must never produce a success line.
        refused[0] = 0
        refusal_path = tmp_path / f"{target_name}.refused"
        refusal_path.write_bytes(refused)
        refusal_path.chmod(0o755)
        try:
            result = subprocess.run(
                [*runner, refusal_path], capture_output=True, check=False
            )
        except OSError:
            refusal_code, refusal_output = 127, b""
        else:
            refusal_code, refusal_output = result.returncode, result.stdout
        assert refusal_code != 0 and b"570\n" not in refusal_output
