"""The world a composed program runs in: memory, leaves, arguments, and how each machine runs it.

One *case* is a program with the arguments it is called with. Its memory is the
runtime context of the leaves (objects, journal, heap), the program as an
object, and the 24-byte payload of every object the programs name. Everything a
run leaves in the first ``SIZE`` bytes is compared across machines; the
function table lies behind it, because its addresses are a machine's own.
"""

from __future__ import annotations

import json
import struct
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import compose
import graph_header

from pymergetic.rxftk.core.object import Image

from . import scenario
from .emulator import Blob, Emulator, Integer
from .graph_reference import Reference
from .wasm import module_of

HARNESS = Path(__file__).with_name("wasm_steps.js")
RUN = "pymergetic.rxf.foundation.graph.run"
MACHINES = {"x86_64": "x86_64_linux", "aarch64": "aarch64_linux", "wasm32": "wasm32"}

BASE = scenario.BASE
SIZE = 0x8000
CONTEXT = BASE
JOURNAL = BASE + 0x100
OBJECTS = BASE + 0x400
PAYLOADS = BASE + 0x3100
PROGRAM = BASE + 0x4000
ARGS = BASE + 0x5800
OUT = BASE + 0x5900
OUT_BYTES = 0x100
TEXT = BASE + 0x5A00
SET_HEADER = BASE + 0x5B00
SET_BUCKETS = BASE + 0x5B40
MAP_HEADER = BASE + 0x5C00
MAP_BUCKETS = BASE + 0x5C40
AREA = BASE + 0x5E00  # an idle live area (272 bytes) and the table of one capability
CAPABILITIES = BASE + 0x5F40
HEAP = BASE + 0x6000
HEAP_SIZE = 0x2000
FUNCTIONS = BASE + SIZE  # behind the compared memory
HANDLE_COPY = (
    BASE + SIZE + 0x600
)  # the handle a wasm caller passes by value, by address
FRAME = BASE + SIZE + 0x800  # the run cell of the reference interpreter
# the flat memory ends with the run cell of the reference interpreter: the frames of
# the active call chain are carved (base, size) from its payload (the cell
# rxf_graph_run keeps on its stack), so a nested call card writes its frame where it
# is mapped
MAPPED = SIZE + 0x800 + 0x2000
PROGRAM_ID = 900001
# Behind everything above: what the compiler (docs/19, WP12) is given and writes.
REQUEST_AT = BASE + MAPPED
CODE_AT = REQUEST_AT + 0x100
CODE_BYTES = 0x10000
TABLES_AT = CODE_AT + CODE_BYTES
TABLE_BYTES = 0x2000
REACH = 0x100 + CODE_BYTES + TABLE_BYTES  # the memory the compiler needs beyond MAPPED
PROGRAM_BYTES = 0x1000
LIVE = scenario.LIVE
JOURNAL_CAPACITY = 4

Letters = dict[str, str]
NUMERIC = {
    "wrapping_add_uint64_t": "qqp",
    "less_uint64_t": "qqp",
    "bit_not_uint64_t": "qp",
    "checked_add_uint32_t": "ddp",
    "checked_subtract_uint32_t": "ddp",
    "checked_multiply_uint32_t": "ddp",
    "divide_uint32_t": "ddp",
    "wrapping_subtract_uint64_t": "qqp",
    "bit_and_uint64_t": "qqp",
    "bit_or_uint64_t": "qqp",
    "shift_left_uint64_t": "qdp",
    "shift_right_uint64_t": "qdp",
    "equal_uint64_t": "qqp",
    "wrapping_multiply_uint64_t": "qqp",
    "checked_multiply_uint64_t": "qqp",
    "checked_multiply_int64_t": "qqp",
    "divide_uint64_t": "qqp",
    "remainder_uint64_t": "qqp",
    "divide_int64_t": "qqp",
    "remainder_int64_t": "qqp",
    "remainder_uint32_t": "ddp",
    "divide_int32_t": "ddp",
    "remainder_int32_t": "ddp",
    "live_pump": "pp",  # no program calls it; tests/test_live_native.py does
    "uart_pump": "pp",  # tests/test_live_uart.py does
    "safe_point": "pp",  # compiled code calls it at an edge that goes back (tests/test_safe_points.py)
    "compile_program": "pp",  # tests/test_compile.py does
    "graph_run": "pHqqp",  # a call card's own; the callee runs in a run of its own
}


def letters() -> Letters:
    """The parameters of every leaf, as scenario.SPEC writes them."""
    found = {
        key: scenario.SPEC[key] for key in graph_header.leaves() if key in scenario.SPEC
    }
    found.update(NUMERIC)
    assert set(found) == set(graph_header.leaves())
    return found


def wasm_type(letter: str) -> str:
    return "i64" if letter == "q" else "i32"


def call_types() -> list[list[str]]:
    """The wasm parameters of the leaves in the order of their ids."""
    spec = letters()
    return [
        [wasm_type(letter) for letter in spec[key]]
        for key, _ in sorted(graph_header.leaves().items(), key=lambda item: item[1])
    ]


@dataclass(frozen=True)
class Code:
    """The machine code of the interpreter and the leaves, per machine."""

    run: dict[str, bytes]
    leaves: dict[str, dict[int, bytes]]


def code_of(image: Image) -> Code:
    composer = compose.Composer(image)
    run = composer.target(RUN)

    def bytes_of(function: int) -> dict[str, bytes]:
        found = {}
        for child in composer.children(function, "Code"):
            name = image.path(child).rsplit(".", 1)[-1]
            for machine, wanted in MACHINES.items():
                if name == wanted:
                    found[machine] = bytes.fromhex(
                        composer.fields(child).get("data", "")
                    )
        assert set(found) == set(MACHINES), (image.path(function), set(found))
        return found

    leaves: dict[str, dict[int, bytes]] = {machine: {} for machine in MACHINES}
    for index, target in composer.references(run, "Leaf"):
        for machine, data in bytes_of(target).items():
            leaves[machine][index] = data
    return Code({m: d for m, d in bytes_of(run).items()}, leaves)


def bindings(image: Image) -> dict[int, bytes]:
    """The durable objects the programs name, with the payload each one has."""
    composer = compose.Composer(image)
    found: dict[int, bytes] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) == "Object" and "object_id" in values:
            found[int(values["object_id"])] = bytes.fromhex(values.get("data", ""))
    return dict(sorted(found.items()))


def arguments(program: bytes, variant: int, binding: int) -> tuple[bytes, int]:
    """Values for the parameters of a program, and their number of words."""
    count = struct.unpack_from("<Q", program, 16)[0]
    out = b""
    for index in range(count):
        width = struct.unpack_from("<Q", program, 48 + 16 * index)[0]
        if width == 24:
            handles = [(1, 1, 0), (binding, 1, 0), (2, 1, 8)]
            value = struct.pack("<3Q", *handles[variant % 3])
        elif width <= 8:
            number = [3, 0, 100][variant % 3]
            value = number.to_bytes(8, "little")[:width]
        else:
            value = bytes(width)
        out += value.ljust((width + 7) // 8 * 8, b"\0")
    return out, len(out) // 8


Handle = tuple[int, int, int]


@dataclass(frozen=True)
class Case:
    name: str
    program: bytes
    args: bytes
    objects: dict[int, bytes]
    handle: Handle = (PROGRAM_ID, 1, 0)  # how the run finds its program
    absent: frozenset[int] = frozenset()  # leaves missing from the function table
    channel: bool = False  # the context has a Channel (an idle area): compiled loops get safe points


def cases(image: Image) -> list[Case]:
    found = []
    objects = bindings(image)
    first = next(iter(objects))
    for function, program in compose.programs(image).items():
        for variant in range(3):
            args, _ = arguments(program, variant, first)
            found.append(Case(f"{function}#{variant}", program, args, objects))
    return found


def channeled(found: list[Case]) -> list[Case]:
    """The same cases in a context that has a Channel: an edge that goes back is a safe point there."""
    return [replace(c, name=f"{c.name} +channel", channel=True) for c in found]


def entry(identity: int, payload: int, size: int) -> bytes:
    return scenario.entry(identity, 1, 0, payload, size, size, LIVE)


def memory(case: Case, slots: list[tuple[int, int]]) -> bytes:
    """The memory a run starts from, and behind it the function table."""
    data = bytearray(MAPPED)

    def put(address: int, content: bytes) -> None:
        data[address - BASE : address - BASE + len(content)] = content

    entries = [
        entry(1, TEXT, 64),
        entry(2, TEXT + 64, 64),
        entry(3, TEXT + 128, 64),
        scenario.entry(4),  # free, for an allocation
        scenario.entry(5, 1, scenario.HASH_SET, SET_HEADER, 56, 56, LIVE),
        scenario.entry(6, 1, scenario.SET_BUCKET_TYPE, SET_BUCKETS, 192, 192, LIVE),
        scenario.entry(7, 1, scenario.HASH_MAP, MAP_HEADER, 56, 56, LIVE),
        scenario.entry(8, 1, scenario.MAP_BUCKET_TYPE, MAP_BUCKETS, 384, 384, LIVE),
        entry(PROGRAM_ID, PROGRAM, len(case.program)),
    ]
    put(SET_HEADER, struct.pack("<7Q", 6, 1, 0, 8, 2, 0, 0))
    put(MAP_HEADER, struct.pack("<7Q", 8, 1, 0, 8, 2, 0, 0))
    for index, (stored, key) in {3: (3, 100), 4: (3, 200)}.items():
        put(SET_BUCKETS + index * 24, struct.pack("<IIQQ", 1, 0, stored, key))
        put(
            MAP_BUCKETS + index * 48,
            struct.pack("<IIQQ3Q", 1, 0, stored, key, 3, 1, index * 16),
        )
    where = PAYLOADS
    for identity, payload in case.objects.items():
        put(where, payload)
        entries.append(entry(identity, where, len(payload)))
        where += (len(payload) + 7) // 8 * 8
    assert where <= PROGRAM
    entries.sort(
        key=lambda row: struct.unpack_from("<Q", row)[0]
    )  # the table is sorted by id
    put(OBJECTS, b"".join(entries))
    put(
        CONTEXT,
        struct.pack(
            "<18Q",
            2, 144,  # version, size
            len(slots), FUNCTIONS,
            len(entries), OBJECTS,
            int(case.channel), CAPABILITIES,  # capabilities
            HEAP, HEAP_SIZE, HEAP_SIZE, 0,  # base, committed, limit, frontier
            0, JOURNAL_CAPACITY, JOURNAL,
            0, 0, 0,  # cleanup
        ),
    )  # fmt: skip
    put(CAPABILITIES, struct.pack("<3Q", 1, 1, AREA))  # RXF_CAPABILITY_CHANNEL
    put(TEXT, scenario.TEXT.ljust(64, b"."))
    put(TEXT + 64, bytes(range(1, 65)))
    put(PROGRAM, case.program)
    put(ARGS, case.args)
    put(
        FUNCTIONS,
        b"".join(
            struct.pack("<3Q", identity, 1, address) for identity, address in slots
        ),
    )
    assert len(case.program) <= PROGRAM_BYTES and len(slots) * 24 <= 0x600
    return bytes(data)


@dataclass(frozen=True)
class Outcome:
    status: int
    out: bytes
    memory: bytes


def outcome(status: int, out: bytes, snapshot: bytes) -> Outcome:
    return Outcome(status, out, snapshot[:SIZE])


def emulated(machine: str, code: Code, case: Case) -> Outcome:
    """The interpreter of one machine, and its leaves, as machine code in an emulator."""
    emulator = Emulator(machine)
    emulator.map(BASE, MAPPED)
    addresses = {
        identity: emulator.load(data) for identity, data in code.leaves[machine].items()
    }
    run = emulator.load(code.run[machine])
    present = sorted((i, a) for i, a in addresses.items() if i not in case.absent)
    emulator.write(BASE, memory(case, present))
    status = emulator.call(
        run,
        [
            Integer(CONTEXT),
            Blob(struct.pack("<3Q", *case.handle)),
            Integer(ARGS),
            Integer(len(case.args) // 8),
            Integer(OUT),
        ],
    )
    return outcome(status, emulator.read(OUT, OUT_BYTES), emulator.read(BASE, SIZE))


@dataclass(frozen=True)
class Compiled:
    """What the compiler said about a program, and what its code did."""

    status: int
    at: int
    size: int
    outcome: Outcome | None  # None when the compiler refused


def request_of(case: Case) -> bytes:
    """The request of the compiler: the program in the memory of the case, room for code and tables."""
    return struct.pack(
        "<8Q",
        PROGRAM,
        len(case.program),
        CODE_AT,
        CODE_BYTES,
        TABLES_AT,
        TABLE_BYTES,
        0,
        0,
    )


def compiled(machine: str, code: Code, case: Case) -> Compiled:
    """The compiler of one machine run on the program of the case, and then the code it wrote."""
    emulator = Emulator(machine)
    emulator.map(BASE, MAPPED + REACH)
    addresses = {
        identity: emulator.load(data) for identity, data in code.leaves[machine].items()
    }
    present = sorted((i, a) for i, a in addresses.items() if i not in case.absent)
    emulator.write(BASE, memory(case, present))
    emulator.write(REQUEST_AT, request_of(case))
    status = emulator.call(
        addresses[graph_header.leaves()["compile_program"]],
        [Integer(CONTEXT), Integer(REQUEST_AT)],
    )
    *_, size, at = struct.unpack("<8Q", emulator.read(REQUEST_AT, 64))
    if status:
        return Compiled(status, at, size, None)
    result = emulator.call(
        CODE_AT,
        [
            Integer(CONTEXT),
            Integer(ARGS),
            Integer(len(case.args) // 8),
            Integer(OUT),
        ],
    )
    return Compiled(
        0,
        at,
        size,
        outcome(result, emulator.read(OUT, OUT_BYTES), emulator.read(BASE, SIZE)),
    )


def referenced(code: Code, case: Case) -> tuple[Outcome, list[tuple[str, int]]]:
    """The same program run by the Python interpreter, calling the x86_64 leaves.

    The outcome, and the leaves it called with the status each one returned.
    """
    emulator = Emulator("x86_64")
    emulator.map(BASE, MAPPED)
    addresses = {
        identity: emulator.load(data)
        for identity, data in code.leaves["x86_64"].items()
    }
    present = {i: a for i, a in addresses.items() if i not in case.absent}
    emulator.write(BASE, memory(case, sorted(present.items())))
    interpreter = Reference(emulator, CONTEXT, FRAME, present, letters())
    status, out = interpreter.run(case.program, case.args, len(case.args) // 8)
    emulator.write(OUT, out)
    snapshot = outcome(status, emulator.read(OUT, OUT_BYTES), emulator.read(BASE, SIZE))
    return snapshot, interpreter.calls


def in_node(code: Code, batch: list[Case]) -> list[Outcome]:
    """The wasm32 interpreter and leaves in node, every case on a fresh image."""
    names = sorted(code.leaves["wasm32"])
    spec = letters()
    by_id = {value: key for key, value in graph_header.leaves().items()}
    module = module_of(
        [
            *(
                (
                    f"leaf{i}",
                    [wasm_type(c) for c in spec[by_id[i]]],
                    code.leaves["wasm32"][i],
                )
                for i in names
            ),
            ("run", ["i32", "i32", "i64", "i64", "i32"], code.run["wasm32"]),
        ],
        call_types(),
    )
    steps = []
    for case in batch:
        # the address of a leaf is its index in the table
        slots = [(i, i) for i in names if i not in case.absent]
        steps.append(
            {
                "fn": "run",
                "args": [
                    ["i32", str(CONTEXT)],
                    ["i32", str(HANDLE_COPY)],
                    ["i64", str(ARGS)],
                    ["i64", str(len(case.args) // 8)],
                    ["i32", str(OUT)],
                ],
                "writes": [
                    [BASE, memory(case, slots).hex()],
                    [HANDLE_COPY, struct.pack("<3Q", *case.handle).hex()],
                ],
            }
        )
    request = {
        "module": module.hex(),
        "base": BASE,
        "image": "",
        "snapshot": MAPPED,
        "table": {str(i): f"leaf{i}" for i in names},
        "steps": steps,
    }
    process = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    found = []
    for result in json.loads(process.stdout):
        snapshot = bytes.fromhex(result["memory"])
        found.append(
            outcome(
                result["status"],
                snapshot[OUT - BASE : OUT - BASE + OUT_BYTES],
                snapshot,
            )
        )
    return found


def node_module(code: Code, compiled_bodies: list[bytes] | None = None) -> bytes:
    """The wasm32 interpreter and leaves, and the compiled functions `c0`, `c1`, ... of a batch."""
    names = sorted(code.leaves["wasm32"])
    spec = letters()
    by_id = {value: key for key, value in graph_header.leaves().items()}
    return module_of(
        [
            *(
                (
                    f"leaf{i}",
                    [wasm_type(c) for c in spec[by_id[i]]],
                    code.leaves["wasm32"][i],
                )
                for i in names
            ),
            ("run", ["i32", "i32", "i64", "i64", "i32"], code.run["wasm32"]),
            *(
                (f"c{i}", ["i32", "i32", "i64", "i32"], body)
                for i, body in enumerate(compiled_bodies or [])
            ),
        ],
        call_types(),
    )


def node_steps(module: bytes, names: list[int], steps: list[dict]) -> list[dict]:
    pages = (BASE + MAPPED + REACH + 0xFFFF) // 0x10000
    request = {
        "module": module.hex(),
        "base": BASE,
        "image": "",
        "snapshot": MAPPED + REACH,
        "pages": pages,
        "table": {str(i): f"leaf{i}" for i in names},
        "steps": steps,
    }
    process = subprocess.run(
        ["node", str(HARNESS)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(process.stdout)


def compiled_in_node(code: Code, batch: list[Case]) -> list[Compiled]:
    """The compiler of wasm32 in node on every program of the batch, then every compiled function."""
    names = sorted(code.leaves["wasm32"])
    compile_id = graph_header.leaves()["compile_program"]
    first = node_steps(
        node_module(code),
        names,
        [
            {
                "fn": f"leaf{compile_id}",
                "args": [["i32", str(CONTEXT)], ["i32", str(REQUEST_AT)]],
                "writes": [
                    [
                        BASE,
                        memory(
                            case, [(i, i) for i in names if i not in case.absent]
                        ).hex(),
                    ],
                    [REQUEST_AT, request_of(case).hex()],
                ],
            }
            for case in batch
        ],
    )
    bodies: list[bytes] = []
    found: list[Compiled | None] = []
    for result in first:
        snapshot = bytes.fromhex(result["memory"])
        *_, size, at = struct.unpack_from("<8Q", snapshot, REQUEST_AT - BASE)
        if result["status"]:
            found.append(Compiled(result["status"], at, size, None))
            bodies.append(b"\x00\x41\x00\x0b")  # a function that is never called
        else:
            found.append(None)
            start = CODE_AT - BASE
            bodies.append(snapshot[start : start + size])
    second = node_steps(
        node_module(code, bodies),
        names,
        [
            {
                "fn": f"c{i}",
                "args": [
                    ["i32", str(CONTEXT)],
                    ["i32", str(ARGS)],
                    ["i64", str(len(case.args) // 8)],
                    ["i32", str(OUT)],
                ],
                "writes": [
                    [
                        BASE,
                        memory(
                            case, [(i, i) for i in names if i not in case.absent]
                        ).hex(),
                    ]
                ],
            }
            for i, case in enumerate(batch)
            if found[i] is None
        ],
    )
    results = iter(second)
    done: list[Compiled] = []
    for i, entry in enumerate(found):
        if entry is not None:
            done.append(entry)
            continue
        result = next(results)
        snapshot = bytes.fromhex(result["memory"])
        done.append(
            Compiled(
                0,
                0,
                len(bodies[i]),
                outcome(
                    result["status"],
                    snapshot[OUT - BASE : OUT - BASE + OUT_BYTES],
                    snapshot,
                ),
            )
        )
    return done
