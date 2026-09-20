"""Recover the authoritative RXF image from a native loading envelope.

Recovery uses validated load descriptors, never a search for magic inside code
or arbitrary trailing bytes. No sidecar, source JSON, or ELF symbols are needed.
"""

from __future__ import annotations

from pymergetic.rxf.output.engine import unpack_layout
from pymergetic.rxf.output.header import HEADER_SIZE, MAGIC, BinaryHeader


def rxf_segment_bytes(data: bytes) -> bytes:
    """Validate a mapped RXF segment and discard only envelope tail padding."""
    if len(data) < HEADER_SIZE or not data.startswith(MAGIC):
        raise ValueError("mapped segment does not contain an RXF header")
    header = BinaryHeader.from_wire(data[:HEADER_SIZE])
    if header.image_size < header.header_capacity or header.image_size > len(data):
        raise ValueError("RXF image exceeds its mapped segment")
    result = data[: header.image_size]
    unpack_layout(result)
    return result


def extract_rxf(blob: bytes) -> bytes:
    """Accept raw RXF or recover RXF from a supported executable envelope."""
    if blob.startswith(MAGIC):
        # Raw RXF validation remains the caller's normal unpack/check operation.
        return blob
    if blob.startswith(b"\x7fELF"):
        from pymergetic.rxf.executable.elf import extract_elf_rxf

        return extract_elf_rxf(blob)
    if blob.startswith(b"MZ"):
        from pymergetic.rxf.executable.pe import extract_pe_rxf

        return extract_pe_rxf(blob)
    if len(blob) >= 512 and blob[510:512] == b"\x55\xaa":
        from pymergetic.rxf.executable.bios import extract_bios_rxf

        return extract_bios_rxf(blob)
    raise ValueError("not an RXF image or supported RXF executable envelope")
