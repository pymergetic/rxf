"""The front end as a program on every program there is (docs/19).

Every source of the corpus is compiled twice: by ``tools/native/rxfc.py``, and by
``native/front/front.rxf`` run on the interpreter of ``native/graph.c`` (``rxf_native``). The two
write the same bytes. ``tests/rxf_sim.py`` stays as a dev oracle, not as the engine of this run.

The corpus is the samples of ``tests/rxfc``, the front end of ``native/front`` itself and the
compiler of ``native/tiny`` -- the last two of many files, where an ``include`` names the files of
the request's table (docs/19). The table carries the sources beside the file, each by its name.
"""

from pathlib import Path

import pytest
import rxfc

from .test_front import FRONT, run_front

ROOT = Path(__file__).parents[1]
CORPUS = [
    ROOT / "tests" / "rxfc" / "arith.rxf",
    ROOT / "tests" / "rxfc" / "branches.rxf",
    ROOT / "tests" / "rxfc" / "procs.rxf",
    ROOT / "tests" / "rxfc" / "table.rxf",
    ROOT / "native" / "tiny" / "tiny.rxf",
    ROOT / "native" / "front" / "front.rxf",
]
LIMIT = 2_000_000  # thousand steps are a second of the simulator; this is a bound, not a budget
STEPS_PER_BYTE = 1_500  # a source byte compiles in 320-470 steps; three times that stops a runaway


def files_of(path: Path) -> dict[str, bytes]:
    """The table of the files an `include` can name: the sources beside the file, by their names."""
    return {p.name: p.read_bytes() for p in sorted(path.parent.glob("*.rxf"))}


@pytest.fixture(scope="module")
def front() -> bytes:
    return rxfc.compile_file(FRONT)


@pytest.mark.parametrize("path", CORPUS, ids=[str(p.relative_to(ROOT)) for p in CORPUS])
def test_the_front_end_writes_what_rxfc_writes(front, path):
    files = files_of(path)
    limit = max(LIMIT, STEPS_PER_BYTE * sum(len(text) for text in files.values()))
    status, actual, at = run_front(front, path.read_text(), files, limit=limit)
    assert status == 0, f"refused with status {status} at line {at}"
    assert actual == rxfc.compile_file(path)
