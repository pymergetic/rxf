"""The front end as a program: ``native/front/front.rxfs`` run by the interpreter of
``native/graph.c`` (``rxf_native``), and what it writes for a source. Every test compiles the same
source with ``tools/native/rxfc.py`` and with the front end as a program, and holds the two to the
same bytes.

The request is the one of ``native/front/words.rxfs``: source text, the numbers the source may use
(the ``RXF_*`` of ``native/graph.h``), and where the words of the program go.
"""

import struct
from pathlib import Path

import graph_header
import pytest
import rxfc

from . import rxf_native

FRONT = Path(__file__).parents[1] / "native" / "front" / "front.rxfs"
N = graph_header.numbers()

# memory of a run: the request and what it names, below the frame of a run (0x100000)
REQUEST_AT = 0x10000
SOURCE_AT = 0x20000
CONSTS_AT = 0x30000
NAMES_AT = 0x38000
FILES_AT = 0x3A000
FILE_BYTES_AT = 0x50000  # the names and the texts of the files of the table
OUT_AT = 0x200000
WORK_AT = 0x600000
OUT_CAPACITY = 0x400000  # 4 MB of output: the front end of native/front, compiled, is over 1 MB
WORK_CAPACITY = 0x2000000  # 32 MB of work memory


def build_consts(memory: rxf_native.Memory) -> int:
    """The numbers of graph.h as rows of the request's const table. Returns how many."""
    entries = b""
    names = b""
    offset = 0
    for name, value in sorted(N.items()):
        encoded = name.encode() + b"\0"
        entries += struct.pack("<4Q", memory.at(NAMES_AT + offset), len(name), value, 0)
        names += encoded
        offset += len(encoded)
    memory.write(CONSTS_AT, entries)
    memory.write(NAMES_AT, names)
    return len(N)


def write_files(memory: rxf_native.Memory, files: dict[str, bytes]) -> int:
    """The table of the files an `include` can name: FILE_ENTRY rows of name and text. How many."""
    rows = b""
    blob = b""
    at = FILE_BYTES_AT
    for name, text in files.items():
        encoded = name.encode()
        rows += struct.pack(
            "<4Q", memory.at(at), len(encoded), memory.at(at + len(encoded)), len(text)
        )
        blob += encoded + text
        at += len(encoded) + len(text)
    memory.write(FILES_AT, rows)
    memory.write(FILE_BYTES_AT, blob)
    return len(files)


def make_request(memory: rxf_native.Memory, source_len: int, nconsts: int, nfiles: int = 0) -> bytes:
    return struct.pack(
        "<12Q",
        memory.at(SOURCE_AT),
        source_len,  # R_SOURCE, R_SOURCE_SIZE
        memory.at(OUT_AT),
        OUT_CAPACITY,  # R_OUT, R_OUT_CAPACITY
        memory.at(WORK_AT),
        WORK_CAPACITY,  # R_WORK, R_WORK_CAPACITY
        0,
        0,  # R_OUT_SIZE, R_AT (written by the run)
        memory.at(FILES_AT),
        nfiles,  # R_FILES, R_FILE_COUNT
        memory.at(CONSTS_AT),
        nconsts,  # R_CONSTS, R_CONST_COUNT
    )


@pytest.fixture(scope="module")
def front() -> bytes:
    return rxfc.compile_file(FRONT)


def run_front(
    front: bytes,
    source: str,
    files: dict[str, bytes] | None = None,
    limit: int = 50_000_000,
) -> tuple[int, bytes, int]:
    """Run the front end as a program on ``source``: (status, output, line it stopped at)."""
    encoded = source.encode()
    memory = rxf_native.Memory(WORK_AT + WORK_CAPACITY)
    nconsts = build_consts(memory)
    memory.write(SOURCE_AT, encoded)
    nfiles = write_files(memory, files or {})
    memory.write(REQUEST_AT, make_request(memory, len(encoded), nconsts, nfiles))
    status, _ = rxf_native.run(
        front, struct.pack("<2Q", 0, memory.at(REQUEST_AT)), memory, limit=limit
    )
    at = memory.u64(REQUEST_AT + 56)
    if status != 0:
        memory.close()
        return status, b"", at
    size = memory.u64(REQUEST_AT + 48)
    out = memory.read(OUT_AT, size)
    memory.close()
    return status, out, at


def check(front: bytes, source: str) -> None:
    """The front end writes what rxfc.py writes, byte for byte."""
    expected = rxfc.compile_source(source)
    status, actual, at = run_front(front, source)
    assert status == 0, f"refused with status {status} at line {at}"
    assert actual == expected


# ---- what a source says ------------------------------------------------------------------------

def test_nothing_but_a_program(front):
    check(front, "program test() {\n    return 0;\n}\n")


def test_a_constant(front):
    check(front, "const X = 42;\nprogram test() {\n    return X;\n}\n")


def test_a_parameter(front):
    check(front, "program test(p) {\n    return p;\n}\n")


def test_two_parameters(front):
    check(front, "program test(a, b) {\n    return a + b;\n}\n")


def test_a_variable(front):
    check(front, "program test(p) {\n    var x;\n    x = p + 1;\n    return x;\n}\n")


def test_an_expression(front):
    check(front, "program test(a, b) {\n    return (a + b) * 4;\n}\n")


def test_a_comparison(front):
    check(front, "program test(a, b) {\n    return a < b;\n}\n")


def test_if_and_else(front):
    check(
        front,
        "program test(p) {\n    if p {\n        return 1;\n    } else {\n        return 2;\n    }\n}\n",
    )


def test_a_while_loop(front):
    check(
        front,
        "program test(p) {\n    var i;\n    i = 0;\n    while i < p {\n        i = i + 1;\n    }\n    return i;\n}\n",
    )


def test_break_and_continue(front):
    check(
        front,
        "program test(n) {\n    var i, acc;\n    i = 0;\n    acc = 0;\n    while 1 {\n"
        "        if i == n {\n            break;\n        }\n"
        "        if (i & 1) == 1 {\n            i = i + 1;\n            continue;\n        }\n"
        "        acc = acc + i;\n        i = i + 1;\n    }\n    return acc;\n}\n",
    )


def test_a_while_in_a_while(front):
    check(
        front,
        "program test(n) {\n    var i, j, acc;\n    i = 0;\n    acc = 0;\n    while i < n {\n"
        "        j = 0;\n        while j < n {\n            acc = acc + 1;\n            j = j + 1;\n        }\n"
        "        i = i + 1;\n    }\n    return acc;\n}\n",
    )


def test_a_switch(front):
    check(
        front,
        "program test(p) {\n    switch p {\n        case 1: {\n            return 10;\n        }\n"
        "        case 2, 3: {\n            return 20;\n        }\n"
        "        default: {\n            return 30;\n        }\n    }\n}\n",
    )


def test_a_macro(front):
    check(
        front,
        "macro inc(x) {\n    x = x + 1;\n}\n"
        "program test(p) {\n    var v;\n    v = p;\n    inc(v);\n    return v;\n}\n",
    )


def test_a_fn(front):
    check(
        front,
        "fn double(x) = x * 2;\nprogram test(p) {\n    return double(p);\n}\n",
    )


def test_two_fn_uses_in_one_expression(front):
    check(
        front,
        "fn double(x) = x * 2;\n"
        "fn triple(n) = ((n << 1) + n);\n"
        "program test(a, b) {\n    return double(a) + triple(b);\n}\n",
    )


def test_an_fn_in_the_arguments_of_an_fn(front):
    check(
        front,
        "fn quintuple(n) = ((n << 2) + n);\n"
        "fn have(a, w) = (((a) <= count) & ((w) <= (count - (a))));\n"
        "program test() {\n    var count, ecn, eat, y;\n"
        "    count = 10;\n    ecn = 3;\n    eat = 4;\n"
        "    y = have(eat + 2, quintuple(ecn));\n    return y;\n}\n",
    )


def test_a_proc_and_a_call(front):
    check(
        front,
        "program test() {\n    var sp;\n    sp = 0x10000;\n    call foo;\n    return 0;\n}\n"
        "proc foo {\n    ret;\n}\n",
    )


def test_two_procs(front):
    check(
        front,
        "program test() {\n    var sp;\n    sp = 0x10000;\n    call a;\n    return 0;\n}\n"
        "proc a {\n    call b;\n    ret;\n}\n"
        "proc b {\n    ret;\n}\n",
    )


def test_a_proc_that_calls_itself(front):
    check(
        front,
        "program test() {\n    var sp, depth;\n    sp = 0x10000;\n    depth = 0;\n    call down;\n"
        "    return depth;\n}\n"
        "proc down {\n    depth = depth + 1;\n    if depth < 5 {\n        call down;\n    }\n"
        "    ret;\n}\n",
    )


def test_procs_whose_calls_interleave(front):
    check(
        front,
        "program test() {\n    var sp, x;\n    sp = 0x10000;\n    x = 0;\n    call a;\n    call b;\n"
        "    call a;\n    return x;\n}\n"
        "proc a {\n    x = x + 1;\n    ret;\n}\n"
        "proc b {\n    x = x + 2;\n    call a;\n    ret;\n}\n",
    )


def test_memory_statements(front):
    check(
        front,
        "program test(p) {\n    var q;\n    q = p;\n    store64(q, 42);\n"
        "    put(q, 7, 8);\n    return 0;\n}\n",
    )


def test_load(front):
    check(front, "program test(p) {\n    return load64(p);\n}\n")


def test_loads_that_nest(front):
    check(front, "program test(p) {\n    return load64(load64(p) + 8) + 1;\n}\n")


def test_a_store_of_an_expression(front):
    check(
        front,
        "program test(p) {\n    var i;\n    i = 1;\n    store32(p, i * 4 + 1);\n    return 0;\n}\n",
    )


def test_a_store_whose_address_holds_a_load(front):
    check(
        front,
        "program test(p) {\n    store64(load64(p) + 8, load64(load64(p) + 16));\n    return 0;\n}\n",
    )


def test_refuse(front):
    check(front, "program test() {\n    refuse 255;\n}\n")
