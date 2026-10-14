"""The composer: the programs in the starter are what its graphs compose to."""

import shutil
import struct
from pathlib import Path

import compose
import graph_header
import pytest

from pymergetic.rxf.core import tree

from .snippets import STARTER

RUN = "pymergetic.rxf.foundation.graph.run"
TINY = "pymergetic.rxf.foundation.tiny.compile"  # a program of `.rxf`, not of a graph


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


def stored(image) -> dict[str, bytes]:
    """The program each function carries, by the path of the function."""
    found = {}
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "Code":
            continue
        values = dict(image.fields(handle))
        if "runtime_target" not in values:
            found[image.path(image.get(handle)["parent"])] = bytes.fromhex(
                values["data"]
            )
    return found


def test_the_programs_in_the_starter_are_what_the_graphs_compose_to(image):
    made = stored(image)
    assert made.pop(TINY)
    assert compose.programs(image) == made


def test_a_program_is_whole_words_that_start_with_the_magic(image):
    magic = graph_header.numbers()["RXF_GRAPH_MAGIC"]
    for path, program in stored(image).items():
        words = compose.describe(program)
        assert len(program) % 8 == 0 and words[0] == magic, path
        assert (
            words[1] % 8 == 0 and words[1] <= graph_header.numbers()["RXF_GRAPH_FRAME"]
        ), path


def test_the_leaves_of_the_interpreter_are_the_ids_of_its_header(image):
    composer = compose.Composer(image)
    run = composer.target(RUN)
    leaves = {
        index: image.path(target) for index, target in composer.references(run, "Leaf")
    }
    assert sorted(leaves) == sorted(graph_header.leaves().values())
    for key, identity in graph_header.leaves().items():
        assert leaves[identity].endswith(key.split("_", 1)[-1]), (key, leaves[identity])
        assert dict(image.fields(composer.target(leaves[identity])))[
            "implementation"
        ].endswith("Native")


def test_the_numbers_of_the_header_are_read_whole():
    numbers = graph_header.numbers()
    assert numbers["RXF_GRAPH_MAGIC"] == int.from_bytes(b"RXG1", "little")
    assert numbers["RXF_GRAPH_NONE"] == 2**64 - 1
    assert sorted(graph_header.leaves().values()) == list(range(1, 49))


def test_a_call_that_is_no_leaf_is_found(tmp_path: Path):
    shutil.copytree(STARTER, tmp_path / "starter")
    path = tmp_path / "starter" / "pymergetic" / "rxf" / "foundation" / "heap.xml"
    path.write_text(
        path.read_text().replace('callee="@runtime.pin"', 'callee="@heap.pin"')
    )
    with pytest.raises(compose.ComposeError, match="is not a leaf of the interpreter"):
        compose.programs(tree.load(tmp_path / "starter"))


def test_the_composer_takes_the_new_arithmetic_leaves_for_calls(tmp_path: Path):
    """The composer accepts a graph that calls one of the multiply, divide and remainder
    leaves of native/graph.h (its leaf mirror reads that header whole): the only change in
    the program it composes is the id of the leaf the call names."""
    shutil.copytree(STARTER, tmp_path / "starter")
    root = tmp_path / "starter" / "pymergetic" / "rxf"
    leaves = graph_header.leaves()
    for path, was, now, old, new in (
        (
            "library.xml",
            "division.divide_uint32_t",
            "division.remainder_uint32_t",
            "divide_uint32_t",
            "remainder_uint32_t",
        ),
        (
            "algorithms.xml",
            "wrapping.add_uint64_t",
            "wrapping.multiply_uint64_t",
            "wrapping_add_uint64_t",
            "wrapping_multiply_uint64_t",
        ),
    ):
        file = root / path
        before = compose.programs(tree.load(tmp_path / "starter"))
        file.write_text(file.read_text().replace(was, now))
        after = compose.programs(tree.load(tmp_path / "starter"))
        changed = {
            name
            for name in after
            if before[name] != after[name]
        }
        assert changed, new
        for name in changed:
            old_words = struct.unpack(f"<{len(before[name]) // 8}Q", before[name])
            new_words = struct.unpack(f"<{len(after[name]) // 8}Q", after[name])
            moving = [(a, b) for a, b in zip(old_words, new_words, strict=True) if a != b]
            assert moving and all(
                a == leaves[old] and b == leaves[new] for a, b in moving
            ), (name, new)
