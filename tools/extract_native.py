#!/usr/bin/env python3
"""Strict stdlib ELF64 extractor for relocation-free RXF native leaves."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from native_symbols import EXPECTED

EM = {"x86_64": 62, "aarch64": 183}
ELF_HEADER_SIZE = 64
SECTION_HEADER_SIZE = 64
SYMBOL_SIZE = 24
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_RELA = 4
SHT_REL = 9
SHF_ALLOC = 2
SHF_EXECINSTR = 4
STT_FUNC = 2
ET_REL = 1


def checked_slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise ValueError(f"{label} is outside ELF extent")
    return data[offset : offset + size]


def c_string(table: bytes, offset: int, label: str) -> str:
    if offset < 0 or offset >= len(table):
        raise ValueError(f"{label} string offset is outside table")
    end = table.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"{label} string is unterminated")
    try:
        return table[offset:end].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} string is not ASCII") from error


def extract(path: Path, machine: str) -> dict[str, dict[str, object]]:
    data = path.read_bytes()
    checked_slice(data, 0, ELF_HEADER_SIZE, "ELF header")
    if data[:4] != b"\x7fELF" or data[4:7] != b"\x02\x01\x01":
        raise ValueError("expected ELF64 little-endian current-version object")
    e_type, e_machine, e_version = struct.unpack_from("<HHI", data, 16)
    shoff = struct.unpack_from("<Q", data, 40)[0]
    ehsize, _phentsize, _phnum, shentsize, shnum, shstrndx = struct.unpack_from(
        "<HHHHHH", data, 52
    )
    if (e_type, e_machine, e_version) != (ET_REL, EM[machine], 1):
        raise ValueError(
            f"wrong ELF type/machine/version: {e_type}/{e_machine}/{e_version}"
        )
    if ehsize != ELF_HEADER_SIZE or shentsize != SECTION_HEADER_SIZE or shnum < 2:
        raise ValueError("invalid ELF or section header geometry")
    if shstrndx == 0 or shstrndx >= shnum:
        raise ValueError("invalid section-name string table index")
    checked_slice(data, shoff, shnum * shentsize, "section header table")
    sections = [
        struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shentsize)
        for i in range(shnum)
    ]

    def section(index: int) -> bytes:
        if index < 0 or index >= shnum:
            raise ValueError("section index is outside table")
        item = sections[index]
        if item[1] == 8:
            return b""  # SHT_NOBITS has no file extent
        return checked_slice(data, item[4], item[5], f"section {index}")

    names_blob = section(shstrndx)
    names = [
        c_string(names_blob, item[0], f"section {i}") for i, item in enumerate(sections)
    ]
    occupied: list[tuple[int, int, str]] = []
    for i, item in enumerate(sections):
        if item[1] == 8 or item[5] == 0:
            continue
        start, end = item[4], item[4] + item[5]
        checked_slice(data, start, item[5], f"section {names[i]}")
        for old_start, old_end, old_name in occupied:
            if max(start, old_start) < min(end, old_end):
                raise ValueError(f"sections {names[i]} and {old_name} overlap")
        occupied.append((start, end, names[i]))
    relocation_targets = set()
    for i, item in enumerate(sections):
        if item[1] not in (SHT_RELA, SHT_REL):
            continue
        if item[6] >= shnum or item[7] >= shnum:
            raise ValueError(f"{names[i]} has invalid link/info")
        expected = 24 if item[1] == SHT_RELA else 16
        if item[9] != expected or item[5] % expected:
            raise ValueError(f"{names[i]} has invalid relocation entry geometry")
        if item[5]:
            relocation_targets.add(item[7])
    symtabs = [i for i, item in enumerate(sections) if item[1] == SHT_SYMTAB]
    if len(symtabs) != 1:
        raise ValueError("expected exactly one symbol table")
    symi = symtabs[0]
    symsec = sections[symi]
    if symsec[6] >= shnum or sections[symsec[6]][1] != 3:
        raise ValueError("symbol table has invalid linked string table")
    if symsec[9] != SYMBOL_SIZE or symsec[5] % SYMBOL_SIZE:
        raise ValueError("invalid symbol table entry geometry")
    symbols_blob, strings = section(symi), section(symsec[6])
    output: dict[str, dict[str, object]] = {}
    extents: dict[int, list[tuple[int, int, str]]] = {}
    for offset in range(0, len(symbols_blob), SYMBOL_SIZE):
        name_offset, info, _other, index, value, size = struct.unpack_from(
            "<IBBHQQ", symbols_blob, offset
        )
        if info & 15 != STT_FUNC or index == 0 or size == 0:
            continue
        name = c_string(strings, name_offset, f"symbol {offset // SYMBOL_SIZE}")
        if not name.startswith("rxf_"):
            continue
        if index >= shnum:
            raise ValueError(f"{name} has invalid section index")
        sec = sections[index]
        if sec[1] != SHT_PROGBITS or sec[2] & (SHF_ALLOC | SHF_EXECINSTR) != (
            SHF_ALLOC | SHF_EXECINSTR
        ):
            raise ValueError(
                f"{name} is not in an allocated executable PROGBITS section"
            )
        if index in relocation_targets:
            raise ValueError(f"{name} is not relocation-free")
        if value > sec[5] or size > sec[5] - value:
            raise ValueError(f"{name} extent is outside section")
        for old_start, old_end, old_name in extents.setdefault(index, []):
            if max(value, old_start) < min(value + size, old_end):
                raise ValueError(f"symbols {name} and {old_name} overlap")
        extents[index].append((value, value + size, name))
        public = name[4:]
        if public in output:
            raise ValueError(f"duplicate exported symbol {public}")
        raw = section(index)[value : value + size]
        output[public] = {
            "hex": raw.hex(),
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "section": names[index],
        }
    actual = set(output)
    if actual != EXPECTED:
        raise ValueError(
            f"native symbol set mismatch; missing={sorted(EXPECTED - actual)}, extra={sorted(actual - EXPECTED)}"
        )
    return dict(sorted(output.items()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("machine", choices=EM)
    parser.add_argument("elf", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    payload = {
        "schema": 2,
        "machine": args.machine,
        "functions": extract(args.elf, args.machine),
    }
    args.output.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    )


if __name__ == "__main__":
    main()
