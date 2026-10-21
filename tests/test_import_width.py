"""The value-width policy of the importer (docs/23 6 q1) and the refusals it earns.

One module's words are one data model's: ``target datalayout`` decides whether a
pointer of the flat memory is 4 or 8 bytes (ILP32 or LP64), every cell and slot takes
the width that model gives its type, and the function words a dispatch compares are the
model's word of the body's id. What the model cannot decide honestly refuses rather
than guesses: a pointer of a module that states no width, two pointer widths in one
layout, a width that is neither 4 nor 8, and the alignment of a 64-bit scalar in memory
under ILP32 (i386 and arm32 disagree -- an ABI decision, not the model's).

The pins run the corpus file ``width32.c`` through both models of the same source and
compare each against its own host C build: the ILP32 one as a 32-bit executable
(``width32_driver.c`` -- a 64-bit process cannot dlopen a 32-bit library), the LP64 one
as the shared library ctypes loads. The relocation refusals that never had a wire (a
runtime symbol of the C++ machinery, a slot the model's word cannot sit at) are pinned
on the messages below -- the refusal story, never a dropped relocation.

The pins live here rather than in ``tests/test_import.py`` so the width policy is one
file's story; the relocation flip itself (use_reloc, lowered now) is pinned there,
beside the REFUSED history it flips."""

from __future__ import annotations

import ctypes
import struct
import subprocess
from pathlib import Path

import compose
import pytest
import rxemit
import rximport

from pymergetic.rxftk.core import tree

from . import rxf_native, seed_object
from .snippets import STARTER

CORPUS = Path(__file__).parent / "import_corpus"
CLANG = "clang-18"
OK = 0
WANTED = {"use_gate", "gate_tag", "enter", "__wires_boot"}

# the datalayouts of the two compilers' models, and the ones the model refuses
LP64_LAYOUT = (
    "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-f80:128-n8:16:32:64-S128"
)  # clang's x86_64 default: the address-space specs are not the C pointer
ILP32_LAYOUT = (
    "e-m:e-p:32:32-p270:32:32-p271:32:32-p272:64:64-i128:128-f64:32:64-f80:32-n8:16:32-S128"
)  # clang -m32


def compiled(flags: tuple[str, ...]) -> str:
    """The IR of width32.c under one model (``-m32`` is the ILP32 half)."""
    out = Path("/tmp") / f"width32_{'ilp32' if flags else 'lp64'}.ll"
    subprocess.run(
        [CLANG, "-S", "-emit-llvm", "-O1", *flags, str(CORPUS / "width32.c"), "-o", str(out)],
        check=True,
    )
    return out.read_text()


def placed(ir: str, module: str = "width32", wanted: set[str] | None = None):
    """The imported image of one model, its programs and its cells at the ids the run
    resolves handles to (the host runner's table: the cells and the programs the call
    cards name)."""
    wanted = set(WANTED if wanted is None else wanted) | {rximport.WIRES_BOOT}
    image = tree.load(STARTER)
    imported = rximport.import_into(image, ir, module=module, only=wanted)
    assert set(imported) == wanted, sorted(imported)
    assert image.check() == []
    programs = compose.programs(image)
    composer = compose.Composer(image)
    cells: dict[str, bytearray] = {}
    objects: dict[int, bytes | bytearray] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
            continue
        name = image.path(handle).rsplit(".", 1)[-1]
        if not name.startswith("global_"):
            continue
        cell = bytearray(bytes.fromhex(values.get("data", "")))
        cells[name[len("global_") :]] = cell
        objects[int(values["object_id"])] = cell
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "SemanticOperation":
            continue
        callee = composer.fields(handle).get("callee")
        target = image.resolve(callee) if callee else None
        if target is not None and image.path(target) in programs:
            objects[compose.object_id(image, target)] = programs[image.path(target)]
    return image, programs, cells, objects


def run_all(programs, objects, module: str = "width32"):
    """Boot the module's wires and answer the cases, one world of cells across them."""
    with rxf_native.Memory(1 << 16) as memory:

        def call(name: str, words: tuple[int, ...]) -> tuple[int, int]:
            return rxf_native.run(
                programs[f"{module}.{name}"],
                struct.pack(f"<{len(words)}Q", *words),
                memory,
                objects=objects,
            )

        status, _ = call("__wires_boot", ())
        assert status == OK, f"__wires_boot: the imported graph refused the boot ({status})"
        tag = call("gate_tag", ())
        calls = [call("use_gate", (value,)) for value in (5, 7, 2)]
    return tag, calls


def test_the_width_model_of_a_module_is_its_datalayout():
    """What one word of the flat memory is comes from the module's own datalayout: the
    pointer width of its default address space (``p:``/``p0:``), never the address-space
    specs beside it (``p270:32:32`` of clang's x86_64 default is not the C pointer). A
    module that states no pointer width is UNDECIDED -- its integers still know their
    widths, its pointer words refuse wherever one would be sized -- and a layout whose
    pointer widths disagree, or one that is neither 4 nor 8, refuses the import."""
    assert rximport.module_model(f'target datalayout = "{LP64_LAYOUT}"') == rxemit.LP64
    assert rximport.module_model(f'target datalayout = "{ILP32_LAYOUT}"') == rxemit.ILP32
    assert rximport.module_model('target datalayout = "e-p:32:32"') == rxemit.ILP32
    assert rximport.module_model('target datalayout = "e-p0:32:32"') == rxemit.ILP32
    assert rximport.module_model("define i64 @f() {\nentry:\n  ret i64 0\n}") == rxemit.UNDECIDED
    with pytest.raises(rximport.Unsupported, match="16-bit"):
        rximport.module_model('target datalayout = "e-p:16:16"')
    with pytest.raises(rximport.Unsupported, match="cannot be decided honestly"):
        rximport.module_model('target datalayout = "e-p:32:32-p0:64:64"')


def test_cells_and_slots_take_the_widths_of_the_model():
    """The sizes and alignments a store writes and a getelementptr walks are the
    model's: a pointer is 8 bytes in LP64 and 4 in ILP32, an i32 is 4 in both (a type's
    width is a type fact, not the model's), and a struct places its fields the way the
    ABI of that model does. Where the model cannot decide, it refuses: a pointer of an
    UNDECIDED module (no default ever guesses 8), and the alignment of a 64-bit scalar
    in ILP32 memory -- i386 gives it 4 and arm32 gives it 8, so a layout that needs one
    refuses instead of picking."""
    assert rximport.type_size("ptr", {}, rxemit.LP64) == 8
    assert rximport.type_size("ptr", {}, rxemit.ILP32) == 4
    assert rximport.type_size("i32", {}, rxemit.ILP32) == 4
    assert rximport.type_size("i64", {}, rxemit.ILP32) == 8
    assert rximport.type_align("ptr", {}, rxemit.LP64) == 8
    assert rximport.type_align("ptr", {}, rxemit.ILP32) == 4
    # one struct of an i32 and a function pointer: the pointer word sits at byte 8 in
    # LP64 and at byte 4 in ILP32, and the cell takes the size of that model's layout
    assert rximport.struct_layout(["i32", "ptr"], {}, model=rxemit.LP64) == (16, 8)
    assert rximport.struct_layout(["i32", "ptr"], {}, model=rxemit.ILP32) == (8, 4)
    # a caller that threads no model gets the undecided one, never an LP64 guess
    with pytest.raises(rximport.Unsupported, match="not decided"):
        rximport.type_size("ptr", {})
    with pytest.raises(rximport.Unsupported, match="not decided"):
        rximport.type_align("ptr", {})
    assert rximport.type_size("i64", {}) == 8  # integers size whatever the model knows
    # the alignment of a 64-bit scalar in memory is an ABI decision, not the model's
    with pytest.raises(rximport.Unsupported, match="not this model's to decide"):
        rximport.type_align("i64", {}, rxemit.ILP32)
    assert rximport.type_align("i64", {}, rxemit.LP64) == 8
    with pytest.raises(rximport.Unsupported, match="unknown to the subset"):
        rximport.type_size("x86_fp80", {}, rxemit.LP64)


def test_a_relocation_the_models_word_cannot_sit_at_refuses_with_the_reason():
    """The relocation refusals of the width model (docs/23 6 q4), on the messages: a
    slot that does not start at a word of the cell (its wire store would tear the word
    it places), a slot narrower than the model's word, and a module that states no
    pointer width at all. Each refuses the cell with its reason -- never a relocation
    dropped and never a slot left silently wrong."""

    def place(slot: int, slot_type: str, model: rxemit.DataModel) -> dict[str, str]:
        cell = rxemit.Global("odd", slot_type, b"\0" * 8)
        return rximport.place_relocations(
            {"@odd": cell},
            {"@odd": [rximport.Relocation(slot, "ptr @enter", "@enter", slot_type)]},
            {"enter": rximport.Function("enter", [], "i64", [])},
            set(),
            {},
            {},
            model,
        )

    assert place(0, "ptr", rxemit.ILP32) == {}  # a word of the cell: placed, and wired
    assert place(4, "ptr", rxemit.ILP32) == {}
    assert place(8, "ptr", rxemit.LP64) == {}
    assert "byte 2" in place(2, "ptr", rxemit.ILP32)["@odd"]
    assert "byte 4" in place(4, "ptr", rxemit.LP64)["@odd"]
    assert "a i16 slot of 2 bytes" in place(0, "i16", rxemit.ILP32)["@odd"]
    assert "not decided" in place(0, "ptr", rxemit.UNDECIDED)["@odd"]


def test_a_pointer_to_another_global_wires_the_cells_address(tmp_path: Path):
    """The second shape of a static initializer's relocation (docs/23 6 q4): @here holds
    the address of @other's cell, not a body's word. The cell places as its byte image
    (the slot zero) and the wire stores the Object the initializer names -- the payload
    address the run resolves that handle to, exactly the `&other` the native static's
    loader places. The run reads it back by dereferencing: peek loads the pointer out of
    @here and the i64 it names, which is @other's initializer."""
    ir = f"""
target datalayout = "{LP64_LAYOUT}"

@other = global i64 5
@here = global ptr @other

define i64 @peek() {{
entry:
  %slot = load ptr, ptr @here
  %word = load i64, ptr %slot
  ret i64 %word
}}
"""
    _image, programs, cells, objects = placed(ir, module="cells", wanted={"peek"})
    assert cells["here"] == b"\0" * 8, "the relocation slot is zero before the boot"
    with rxf_native.Memory(1 << 16) as memory:

        def call(name: str, words: tuple[int, ...]) -> tuple[int, int]:
            return rxf_native.run(
                programs[f"cells.{name}"],
                struct.pack(f"<{len(words)}Q", *words),
                memory,
                objects=objects,
            )

        status, _ = call("__wires_boot", ())
        assert status == OK, f"__wires_boot: the imported graph refused the boot ({status})"
        status, made = call("peek", ())
    assert (status, made) == (OK, 5), (
        f"peek answered ({status}, {made}) after the boot: the wire places @other's "
        "address word (the Object the run resolves), and @other holds 5"
    )


def test_a_relocation_of_a_runtime_symbol_refuses_the_cell_with_the_reason():
    """The relocation forms that have no wire in this model at all: a symbol of the C++
    exception/RTTI machinery (``_ZTV*``/``_ZTI*`` and kin), which names runtime
    machinery the interpreter has no card for. The cell refuses at the use with the
    reason in the message -- the refusal story of the static initializers, not a word
    silently left zero."""
    image = tree.load(STARTER)
    ir = f"""
target datalayout = "{LP64_LAYOUT}"

@_ZTV1A = external global ptr
@table = global [1 x ptr] [ptr @_ZTV1A]

define i64 @peek() {{
entry:
  %slot = getelementptr inbounds [1 x ptr], ptr @table, i64 0, i64 0
  %word = load ptr, ptr %slot
  %as = ptrtoint ptr %word to i64
  ret i64 %as
}}
"""
    with pytest.raises(rximport.Unsupported, match="runtime symbol"):
        rximport.import_into(image, ir, module="cpp", only={"peek"})


def test_the_32_bit_module_wires_and_runs_like_the_native_32_bit_build(tmp_path: Path):
    """The ILP32 half end to end (width32.c compiled ``-m32``): the datalayout states
    p:32:32, so @door is one cell of 8 bytes -- the i32 tag at byte 0 and the function
    pointer's 4-byte word at byte 4, where the LP64 twin of this source gives it 16 and
    puts the word at byte 8 (the twin's pin below). The initializer is the relocation of
    the function pointer: the cell is placed as its byte image (the tag, the slot zero)
    and the module's boot wires enter's 32-bit word into the slot before anything runs.
    The differential is the host C build of the same file in its own model: a 32-bit
    executable (width32_driver.c) whose loader places that relocation the native way
    answers the same numbers the imported graphs do -- the dispatch routes through the
    word the slot carries to enter (x + 1), and the tag reads back both ways."""
    ir = compiled(("-m32",))
    assert rximport.module_model(ir) == rxemit.ILP32
    _image, programs, cells, objects = placed(ir)
    door = cells["door"]
    assert len(door) == 8, f"@door is {len(door)} bytes, the model's cell is 8"
    assert struct.unpack_from("<I", door, 0)[0] == 7, "the tag is placed as its byte image"
    assert struct.unpack_from("<I", door, 4)[0] == 0, "the relocation slot is zero first"

    # every value word of the delta states its width (rxemit's vocabulary), and the
    # cell's payload states its bytes: the model's 8 here, the twin's 16 below
    delta = rxemit.emit_delta(
        list(rximport.graphs_of(ir, "width32", set(WANTED)).values()), "width32"
    )
    values = [
        line for line in delta.splitlines() if "<SemanticValue" in line or "<SemanticObject" in line
    ]
    assert values and all('value_type="' in line for line in values)
    sized = [line for line in values if "Literal" in line or "SemanticObject" in line]
    assert sized and all("literal_size=" in line for line in sized)
    assert any('literal_size="8"' in line and "SemanticObject" in line for line in values)

    tag, calls = run_all(programs, objects)
    # the wire: the boot stored enter's word in the slot, the model's word (32 bits)
    assert struct.unpack_from("<I", door, 4)[0] == rximport.object_id_of("width32.enter") & 0xFFFFFFFF
    assert tag == (OK, 7), f"gate_tag answered {tag}, the cell's tag is 7"
    assert calls == [(OK, 6), (OK, 8), (OK, 3)], f"use_gate answered {calls}"

    # the same numbers from the host C build of this same source, in the same model
    driver = tmp_path / "width32_driver"
    subprocess.run(
        [CLANG, "-m32", "-O1", str(CORPUS / "width32.c"), str(CORPUS / "width32_driver.c"),
         "-o", str(driver)],
        check=True,
    )  # fmt: skip
    held = subprocess.run([str(driver)], capture_output=True, text=True, check=True).stdout.split()
    assert held == ["6", "8", "3", "7"], f"the native 32-bit build answered {held}"


def test_the_64_bit_twin_of_the_same_source_places_the_pointer_word_at_its_own_slot(
    tmp_path: Path,
):
    """The LP64 half of the same source: no ``-m32``, so the datalayout is the 64-bit
    default and @door grows to 16 bytes -- the tag at byte 0, the function pointer's
    8-byte word at byte 8. The wire of the boot stores enter's word there (the full
    object id this time), and the dispatch runs the same answers the native shared
    library of this source does through ctypes. Same C, same runs, two models: the
    widths move with the model and the behavior never does."""
    ir = compiled(())
    assert rximport.module_model(ir) == rxemit.LP64
    _image, programs, cells, objects = placed(ir)
    door = cells["door"]
    assert len(door) == 16, f"@door is {len(door)} bytes, the model's cell is 16"
    assert struct.unpack_from("<I", door, 0)[0] == 7, "the tag is placed as its byte image"
    assert struct.unpack_from("<Q", door, 8)[0] == 0, "the relocation slot is zero first"

    delta = rxemit.emit_delta(
        list(rximport.graphs_of(ir, "width32", set(WANTED)).values()), "width32"
    )
    assert any('literal_size="16"' in line and "SemanticObject" in line for line in delta.splitlines())

    tag, calls = run_all(programs, objects)
    assert struct.unpack_from("<Q", door, 8)[0] == rximport.object_id_of("width32.enter")
    assert tag == (OK, 7), f"gate_tag answered {tag}, the cell's tag is 7"
    assert calls == [(OK, 6), (OK, 8), (OK, 3)], f"use_gate answered {calls}"

    shared = tmp_path / "libwidth32.so"
    subprocess.run(
        [CLANG, "-O1", "-shared", "-fPIC", str(CORPUS / "width32.c"),
         str(seed_object.write([CORPUS / "width32.c"])), "-o", str(shared)],
        check=True,
    )  # fmt: skip
    native = ctypes.CDLL(str(shared))
    native.use_gate.argtypes = [ctypes.c_uint32]
    native.use_gate.restype = ctypes.c_uint64
    native.gate_tag.restype = ctypes.c_uint32
    assert (native.gate_tag(), [native.use_gate(v) for v in (5, 7, 2)]) == (7, [6, 8, 3])
