"""Temporary probe: run the self-compile request of the image under a strict simulator memory.

The simulator's memory is sparse and reads zeros anywhere; the emulator faults. This runs the same
request with the same addresses as the live machine but a memory that only has what the machine
maps, and logs every memory_copy leaf call, so the wild address and its source show up fast.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "tools" / "native"))

import graph_header  # noqa: E402
import rxfc  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
import rxf_sim  # noqa: E402

ROOT = Path(__file__).parents[1]
FRONT = ROOT / "native" / "front" / "front.rxf"
SOURCES = sorted((ROOT / "native" / "front").glob("*.rxf"))

# the addresses of the live machine's run (printed by the failing test)
SOURCE_AT = 0x10A5D670
NAMES_AT = 0x10814F10
FILES_AT = 0x106CDC50
OUT_AT = 0x10815710
OUT_ROOM = 2 * len(rxfc.compile_file(FRONT))
REQUEST_AT = 0x11761000
CONSTS_AT = 0x11761100
FILES_ROWS_AT = 0x11762000
WORK_AT = 0x11861000
WORK_BYTES = 12 * (1 << 20) - 0x100000
CALLS: list[tuple] = []


class Strict(rxf_sim.Memory):
    """Zero until written, like Memory, but only inside the regions the machine maps."""

    def __init__(self, regions: list[tuple[int, int]]) -> None:
        super().__init__()
        self.regions = regions

    def allowed(self, address: int, size: int) -> bool:
        return any(b <= address and address + size <= b + s for b, s in self.regions)

    def read(self, address: int, size: int) -> bytes:
        if not self.allowed(address, size):
            raise RuntimeError(f"WILD READ {hex(address)} +{size} at call {len(ALL)}")
        return super().read(address, size)

    def write(self, address: int, data: bytes) -> None:
        if not self.allowed(address, len(data)):
            raise RuntimeError(f"WILD WRITE {hex(address)} +{len(data)} at call {len(ALL)}")
        super().write(address, data)


ALL: list[tuple] = []


def wrap(name: int, body):
    def run(args, memory):
        status, value = body(args, memory)
        ALL.append((name, tuple(args), status, value))
        return status, value

    return run


for name, body in list(rxf_sim.LEAF_RUN.items()):
    rxf_sim.LEAF_RUN[name] = wrap(name, body)


def main() -> None:
    files = {p.name: p.read_bytes() for p in SOURCES}
    text = files["emit.rxf"]
    before, after = (
        b"st = OUT_OF_MEMORY;\n        at = load64(work + G_LASTLINE);",
        b"at = load64(work + G_LASTLINE);\n        st = OUT_OF_MEMORY;",
    )
    head, _, tail = text.rpartition(before)
    files["emit.rxf"] = head + after + tail

    program = rxfc.compile_file(FRONT)
    memory = Strict(
        [
            (rxf_sim.FRAME_AT, 0x1000),
            (REQUEST_AT, 0x100),
            (CONSTS_AT, 0x2000),
            (FILES_ROWS_AT, 0x200),
            (WORK_AT, WORK_BYTES),
            (SOURCE_AT, 0x10000),
            (NAMES_AT, 0x2000),
            (FILES_AT, 0x40000),
            (OUT_AT, OUT_ROOM),
        ]
    )

    numbers = sorted(graph_header.numbers().items())
    names = b"".join(nm.encode() + b"\0" for nm, _ in numbers)
    rows = b""
    offset = 0
    for cname, value in numbers:
        encoded = cname.encode() + b"\0"
        rows += struct.pack("<4Q", NAMES_AT + offset, len(cname), value, 0)
        offset += len(encoded)
    table, spans = b"", []
    for filename, source in files.items():
        encoded = filename.encode()
        spans.append((len(table), len(encoded), len(table) + len(encoded), len(source)))
        table += encoded + source
    source = files["front.rxf"]
    memory.write(NAMES_AT, names)
    memory.write(FILES_AT, table)
    memory.write(SOURCE_AT, source)
    memory.write(CONSTS_AT, rows)
    memory.write(
        FILES_ROWS_AT,
        b"".join(struct.pack("<4Q", FILES_AT + a, b, FILES_AT + c, d) for a, b, c, d in spans),
    )
    memory.write(
        REQUEST_AT,
        struct.pack(
            "<12Q",
            SOURCE_AT,
            len(source),
            OUT_AT,
            OUT_ROOM,
            WORK_AT,
            WORK_BYTES,
            0,
            0,
            FILES_ROWS_AT,
            len(spans),
            CONSTS_AT,
            len(numbers),
        ),
    )
    try:
        status, _ = rxf_sim.run(program, struct.pack("<2Q", 0, REQUEST_AT), memory, limit=200_000_000)
        print("done", status, "leaf calls", len(ALL))
    except RuntimeError as error:
        print(error)
        wild = int(str(error).split()[2], 16)
        for entry in ALL[-24:]:
            print(entry)
        print("=== the wild value as a result or an argument ===")
        for entry in ALL[-4000:]:
            name, args, _status, value = entry
            if value == wild or any(a == wild for a in args if isinstance(a, int)):
                print(entry)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
