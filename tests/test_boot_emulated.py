"""The wrapper body under the emulator: the entry convention with no machine behind it.

The wrapper (native/wrap/wrap.c) runs as flat code in unicorn with the package in
memory beside it and the block at the base the header names. What the entry of the
starter answers through the wrapper must be what the interpreter run of the same entry
answers (tests/image_world.py), byte for byte in the report line the seats print. The
wrappers do not compute; they load, activate and report.
"""

from pathlib import Path

import pytest

from pymergetic.rxf.binary import builder, formats, package
from pymergetic.rxf.core import tree

from . import image_world as run
from .emulator import Emulator, Integer
from .snippets import STARTER

MAIN = "pymergetic.rxf.application.main"
PACKAGE_AT = 0x1000000
RESULT_AT = 0xC00000


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def built(image) -> bytes:
    return package.build(image)


@pytest.fixture(scope="module")
def flat(tmp_path_factory) -> dict:
    return builder.build_flat(tmp_path_factory.mktemp("flat"))


@pytest.fixture(scope="module")
def reference(image):
    """What the interpreter of the image answers for the entry: the pin of every seat."""
    linked = package.link_module().link(image, "x86_64", package.BASE)
    # The entry of the starter takes no arguments (the application.main shape): the
    # three argument variants of the run helper are one run of it.
    entry = run.runs(image, linked, [MAIN])[0]
    result = run.referenced(linked, image, entry)
    out = int.from_bytes(result.out[:8], "little")
    return result.status, out


def report_line(status: int, out: int) -> bytes:
    """The one line every seat reports (native/wrap/wrap.c: `rxf result <status> <out>`)."""
    return f"rxf result {status:08x} {out:016x}\n".encode()


def entered(built: bytes, flat: dict):
    """The wrapper called with the package, under the emulator; its answer and result."""
    parsed = package.parse(built)
    emulator = Emulator("x86_64")
    assert emulator.load(Path(flat["binary"]).read_bytes()) == flat["base"]
    emulator.map(PACKAGE_AT, len(built))
    emulator.write(PACKAGE_AT, built)
    emulator.map(package.BASE, parsed.image_bytes)
    emulator.map(RESULT_AT, 0x1000)
    answer = emulator.call(
        flat["enter"],
        [
            Integer(PACKAGE_AT),
            Integer(len(built)),
            Integer(package.BASE),
            Integer(parsed.image_bytes),
            Integer(RESULT_AT),
        ],
    )
    raw = emulator.read(RESULT_AT, 16)
    result = (int.from_bytes(raw[8:12], "little"), int.from_bytes(raw[:8], "little"))
    return emulator, answer, result


def test_the_wrapper_enters_the_image_and_reports_what_the_interpreter_reports(
    built, flat, reference
):
    _, answer, result = entered(built, flat)
    assert answer == 0  # RXF_WRAP_ANSWER_OK
    assert result == reference


def test_the_report_line_of_the_wrapper_is_the_line_of_the_seats(built, flat, reference):
    emulator, _, result = entered(built, flat)
    line = formats.read_elf(Path(flat["binary"]).with_suffix(".elf").read_bytes()).symbols[
        "rxf_wrap_report_line"
    ]
    length = emulator.call(
        line,
        [Integer(RESULT_AT + 0x100), Integer(RESULT_AT)],
    )
    assert length == 37
    assert emulator.read(RESULT_AT + 0x100, 38) == report_line(*result) + bytes(1)


def test_the_report_line_of_a_refused_run_still_reads_one_line(flat):
    """The wrapper itself could not run the image: the line still names the answer."""
    emulator = Emulator("x86_64")
    emulator.load(Path(flat["binary"]).read_bytes())
    line = formats.read_elf(Path(flat["binary"]).with_suffix(".elf").read_bytes()).symbols[
        "rxf_wrap_report_line"
    ]
    emulator.map(RESULT_AT, 0x1000)
    # a result of 0xF2 (the wrapper placed no block) with an output word of 0x2A
    emulator.write(RESULT_AT, (0x2A).to_bytes(8, "little") + (0xF2).to_bytes(4, "little"))
    length = emulator.call(line, [Integer(RESULT_AT + 0x100), Integer(RESULT_AT)])
    assert length == 37
    assert emulator.read(RESULT_AT + 0x100, 38) == report_line(0xF2, 0x2A) + bytes(1)
