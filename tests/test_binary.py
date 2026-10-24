"""`rxf binary check`: the starter as a multi-platform binary."""

import json
import shutil
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

import pytest

from pymergetic.rxftk import binary
from pymergetic.rxftk.cli import main
from pymergetic.rxftk.core import tree

STARTER = Path(__file__).parents[1] / "starter"
RXF = "urn:pymergetic:rxf"
ET.register_namespace("rxf", RXF)

TARGETS = [
    "aarch64_linux_aapcs64",
    "aarch64_uefi_aapcs64",
    "wasm32_wasm_c",
    "x86_64_bios_sysv",
    "x86_64_linux_sysv",
    "x86_64_uefi_sysv",
]


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    shutil.copytree(STARTER, tmp_path / "starter")
    return tmp_path / "starter"


def report(directory: Path) -> dict:
    return binary.check(tree.load(directory))


NATIVE = "pymergetic.rxf.kinds.FunctionImplementation.Native"


def is_native(image, handle: int) -> bool:
    """Whether a function is a native one (the glue of ``boot/`` is not)."""
    return dict(image.fields(handle)).get("implementation") == NATIVE


def edit(directory: Path, module: str, function: str, change) -> None:
    path = directory / (module.replace(".", "/") + ".rxfd")
    if not path.exists():
        path = directory / module.replace(".", "/") / "__init__.rxfd"
    document = ET.parse(path)
    for element in document.getroot().iter("Function"):
        if element.get("name") == function:
            change(element)
            document.write(path, encoding="UTF-8", xml_declaration=True)
            return
    raise AssertionError(f"{module}:{function}")


def test_the_starter_is_a_complete_binary(capsys):
    assert main(["binary", "check", str(STARTER)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["issues"] == []
    assert result["targets"] == TARGETS
    natives = {f["implementation"]: f["count"] for f in result["functions"]}
    assert natives["Native"] == 321


def test_every_native_function_has_code_for_bios_unix_uefi_and_wasm():
    """Every native function runs on every seat: one code per seat of it. The
    count is over the codes of native functions only -- the glue of ``boot/`` is
    not a native function (docs/28's glue rule: it is the platform's fill of the
    startup seam) and carries its own code objects."""
    image = tree.load(STARTER)
    machines = {"bios": 0, "linux": 0, "uefi": 0, "wasm": 0}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Code":
            continue
        values = dict(image.fields(handle))
        owner = image.resolve(values.get("owner_function", ""))
        if owner is None or not is_native(image, owner):
            continue
        target = values.get("runtime_target")
        if target is None:
            continue  # a program, the same for every target
        target = target.rsplit(".", 1)[-1]
        for name in machines:
            if f"_{name}_" in target:
                machines[name] += 1
    assert machines == {"bios": 321, "linux": 642, "uefi": 642, "wasm": 321}


def test_the_seats_of_a_function_share_one_blob_per_machine():
    """The dedup of the starter: six seats, three blobs, one per machine. Two
    codes of one function that name one machine always name one blob (every
    function, glue included); the three-blob economy is the claim of the native
    functions, whose machine code is the same on every seat of a machine. The
    glue of ``boot/`` is per seat by design -- the BIOS sector and the UEFI stub
    are two artifacts of one machine -- and is not part of that economy."""
    image = tree.load(STARTER)
    seats = {
        "x86_64_linux": "x86_64",
        "x86_64_bios": "x86_64",
        "x86_64_uefi": "x86_64",
        "aarch64_linux": "aarch64",
        "aarch64_uefi": "aarch64",
        "wasm32": "wasm32",
    }
    shared: dict[tuple[int, str], int] = {}
    per_function: dict[int, set[int]] = defaultdict(set)
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Code":
            continue
        values = dict(image.fields(handle))
        body = image.resolve(values.get("body", ""))
        if body is None or image.type_name(body) != "CodeBlob":
            continue  # a program or bytecode code keeps its bytes inline
        machine = seats[image.path(handle).rsplit(".", 1)[-1]]
        parent = image.get(handle)["parent"]
        if (parent, machine) in shared:
            assert shared[parent, machine] == body, image.path(handle)
        shared[parent, machine] = body
        if is_native(image, parent):
            per_function[parent].add(body)
    assert per_function, "the starter has no native code"
    assert all(len(bodies) == 3 for bodies in per_function.values())
    natives = sum(1 for parent, _ in shared if is_native(image, parent))
    assert natives == 3 * len(per_function)


def test_a_missing_code_is_found_and_so_is_every_caller(copy):
    def drop(function):
        for child in list(function):
            if child.get("name") in ("wasm32", "wasm_c_status_out"):
                function.remove(child)

    edit(copy, "pymergetic.rxf.foundation.runtime", "begin_private", drop)
    result = report(copy)
    messages = {
        (i["path"].rsplit(".", 2)[-2:][-1], i["message"]) for i in result["issues"]
    }
    assert not result["ok"]
    assert ("begin_private", "no code for wasm32_wasm_c") in messages
    callers = {i["path"] for i in result["issues"] if "not complete" in i["message"]}
    assert "pymergetic.rxf.foundation.heap.pin" in callers


def test_a_code_that_is_not_its_length_is_found(copy):
    def cut(function):
        for child in function:
            if child.tag == "Code" and child.get("name") == "x86_64_bios":
                child.set("byte_count", "1")

    edit(copy, "pymergetic.rxf.numeric.checked", "add_int8_t", cut)
    issues = report(copy)["issues"]
    assert [i["message"] for i in issues] == [
        "byte_count is not the length of the data"
    ]


def test_a_code_in_the_wrong_format_is_found(copy):
    def wrong(function):
        for child in function:
            if child.tag == "Code" and child.get("name") == "wasm32":
                child.set("format", "@kinds.CodeFormat.Machine")

    edit(copy, "pymergetic.rxf.numeric.checked", "add_int8_t", wrong)
    messages = [i["message"] for i in report(copy)["issues"]]
    assert messages == ["the format is Machine, wasm32_wasm_c wants Wasm"]


def test_the_structure_the_composer_must_make_is_named():
    structural = {s["opcode"] for s in binary.check(tree.load(STARTER))["structural"]}
    assert {"ReturnValue", "ReadTag", "ConstructOption"} <= structural
    assert "Copy" not in structural  # a call, so a snippet does it


def test_every_composed_function_has_one_program_and_its_interpreter():
    image = tree.load(STARTER)
    programs = [
        h
        for h in range(image.handle_count)
        if image.live(h)
        and image.type_name(h) == "Code"
        and "runtime_target" not in dict(image.fields(h))
    ]
    semantic = next(
        f["count"]
        for f in binary.check(image)["functions"]
        if f["implementation"] == "Semantic"
    )
    assert len(programs) == semantic == 59  # 58 composed from graphs, and tiny.compile
    assert {image.type_name(image.get(p)["parent"]) for p in programs} == {"Function"}


def program_of(function):
    return next(c for c in function if c.tag == "Code" and c.get("name") == "bytecode")


def messages(directory: Path) -> list[str]:
    return [i["message"] for i in report(directory)["issues"]]


def test_a_composed_function_without_its_program_is_found(copy):
    edit(copy, "pymergetic.rxf.algorithms", "clamp", lambda f: f.remove(program_of(f)))
    assert messages(copy) == ["it is semantic and has 0 Code objects, not one"]


def test_a_program_that_is_not_its_length_is_found(copy):
    edit(
        copy,
        "pymergetic.rxf.algorithms",
        "clamp",
        lambda f: program_of(f).set("byte_count", "8"),
    )
    assert messages(copy) == [
        "byte_count is not the length of the data, in whole words"
    ]


def test_a_program_that_names_a_target_is_found(copy):
    def name(function):
        program_of(function).set("runtime_target", "@targets.x86_64_linux_sysv")

    edit(copy, "pymergetic.rxf.algorithms", "clamp", name)
    assert messages(copy) == ["a program is the same for every target and names none"]


def test_a_program_in_the_wrong_format_is_found(copy):
    def wrong(function):
        program_of(function).set("format", "@kinds.CodeFormat.Machine")

    edit(copy, "pymergetic.rxf.algorithms", "clamp", wrong)
    assert messages(copy) == ["the format is Machine, not Bytecode"]


def test_a_program_without_its_interpreter_is_found(copy):
    def drop(function):
        program = program_of(function)
        for child in list(program):
            if child.get("role", "").endswith("Interpreter"):
                program.remove(child)

    edit(copy, "pymergetic.rxf.algorithms", "clamp", drop)
    assert messages(copy) == ["it does not name its interpreter"]


def test_an_interpreter_that_lacks_code_for_a_target_is_found(copy):
    def drop(function):
        for child in list(function):
            if child.get("name") in ("wasm32", "wasm_c_status_out"):
                function.remove(child)

    edit(copy, "pymergetic.rxf.foundation.graph", "run", drop)
    found = messages(copy)
    assert "no code for wasm32_wasm_c" in found
    assert any("graph.run is not complete for every target" in m for m in found)
