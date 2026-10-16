"""The shapes of the artifacts: what the tests read back from an ELF or a PE.

The builders hand the toolchain the code and the package; this reads the artifact
again so the proof does not trust the build. Only what a test pins is read: the
machine and entry of an ELF and its named sections, the machine and subsystem of a
PE/COFF and its named sections.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

EM_X86_64 = 62
PE_MACHINE_AMD64 = 0x8664
PE_SUBSYSTEM_EFI_APPLICATION = 10
PE32_PLUS = 0x20B
SHT_SYMTAB = 2


class FormatError(Exception):
    """The bytes are not the artifact they say they are."""


@dataclass(frozen=True)
class Elf:
    """An ELF64 executable: where it starts, its named sections and its symbols."""

    machine: int
    entry: int
    sections: dict[str, bytes]
    symbols: dict[str, int]


@dataclass(frozen=True)
class Pe:
    """A PE/COFF image: its machine, its subsystem and its named sections."""

    machine: int
    subsystem: int
    entry: int
    sections: dict[str, bytes]


def _strings(data: bytes, offset: int, size: int) -> bytes:
    return data[offset : offset + size]


def read_elf(data: bytes) -> Elf:
    """The sections and symbols of an ELF64 image."""
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise FormatError("not an ELF image")
    if data[4] != 2 or data[5] != 1:
        raise FormatError("not a 64-bit little-endian ELF")
    machine = struct.unpack_from("<H", data, 18)[0]
    entry = struct.unpack_from("<Q", data, 24)[0]
    shoff = struct.unpack_from("<Q", data, 40)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", data, 58)
    if shentsize != 64 or shoff + shnum * shentsize > len(data):
        raise FormatError("the section table is not in the image")

    def header(at: int) -> tuple[int, int, int, int, int, int, int, int]:
        return struct.unpack_from("<IIQQQQII", data, shoff + at * shentsize)

    strings = _strings(data, header(shstrndx)[4], header(shstrndx)[5])

    def name(at: int) -> str:
        end = strings.index(b"\0", at)
        return strings[at:end].decode()

    sections: dict[str, bytes] = {}
    for at in range(shnum):
        name_at, _, _, address, offset, size, _, _ = header(at)
        if name_at:
            sections[name(name_at)] = data[offset : offset + size] if address else b""
    symbols: dict[str, int] = {}
    for at in range(shnum):
        _, kind, _, _, offset, size, link, _ = header(at)
        if kind != SHT_SYMTAB:
            continue
        names = _strings(data, header(link)[4], header(link)[5])
        table = data[offset : offset + size]
        for each in range(size // 24):
            symbol = table[each * 24 : each * 24 + 24]
            at_name = struct.unpack_from("<I", symbol)[0]
            value = struct.unpack_from("<Q", symbol, 8)[0]
            end = names.index(b"\0", at_name)
            symbols[names[at_name:end].decode()] = value
    return Elf(machine=machine, entry=entry, sections=sections, symbols=symbols)


def read_pe(data: bytes) -> Pe:
    """The machine, subsystem and sections of a PE/COFF image."""
    if len(data) < 0x40 or data[:2] != b"MZ":
        raise FormatError("not a PE image")
    at = struct.unpack_from("<I", data, 0x3C)[0]
    if data[at : at + 4] != b"PE\0\0":
        raise FormatError("not a PE image")
    machine, count, _, _, _, size_of_optional, _ = struct.unpack_from("<HHIIIHH", data, at + 4)
    optional = at + 24
    if struct.unpack_from("<H", data, optional)[0] != PE32_PLUS:
        raise FormatError("not a PE32+ image")
    subsystem = struct.unpack_from("<H", data, optional + 68)[0]
    entry = struct.unpack_from("<I", data, optional + 16)[0]
    sections: dict[str, bytes] = {}
    for index in range(count):
        header = optional + size_of_optional + index * 40
        name = data[header : header + 8].rstrip(b"\0").decode()
        virtual_size = struct.unpack_from("<I", data, header + 8)[0]
        raw_size = struct.unpack_from("<I", data, header + 16)[0]
        raw_at = struct.unpack_from("<I", data, header + 20)[0]
        room = min(virtual_size or raw_size, raw_size)
        sections[name] = data[raw_at : raw_at + room]
    return Pe(machine=machine, subsystem=subsystem, entry=entry, sections=sections)
