"""The composer: the programs in the starter are what its graphs compose to."""

import shutil
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
    assert sorted(graph_header.leaves().values()) == list(range(1, 39))


def test_a_call_that_is_no_leaf_is_found(tmp_path: Path):
    shutil.copytree(STARTER, tmp_path / "starter")
    path = tmp_path / "starter" / "pymergetic" / "rxf" / "foundation" / "heap.xml"
    path.write_text(
        path.read_text().replace('callee="@runtime.pin"', 'callee="@heap.pin"')
    )
    with pytest.raises(compose.ComposeError, match="is not a leaf of the interpreter"):
        compose.programs(tree.load(tmp_path / "starter"))
