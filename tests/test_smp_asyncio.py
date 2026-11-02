"""The uasyncio binding proof (docs/23 4.5, docs/20 S3/S4/S9/S10/S11): asyncio on the run-cell machine.

The model is ``tests/host/smp_asyncio.c``: a Task is a run cell, ``create_task``/``call_soon`` push the
S3 ready-ring (THE loop's ready deque, not a second queue), ``await`` on a Task or an Event is
PARKED, ``Task`` end and ``Event.set`` wake the waiting RUNS (never a carrier), ``sleep`` is the
edge timer (S9) on a virtual clock that advances only at quiescence, and ``Task.cancel`` is a raise
deposit into ``mp_raise_slot`` plus a wake, consumed at the poll of the next step (S11). The
scheduler is ``smp_sched.c`` unchanged; N pthread carriers run the one dispatcher loop.

The corpus (``tests/asyncio_corpus/programs.py``) is DATA, run by two executors: MicroPython's real
``asyncio`` (``reference.py``, the reference of docs/23 decision 10) and the model. What is pinned:

- **the reference differential**: at ONE carrier the model's report (every task's end state and
  result fold, and the shared trace in order) equals MicroPython's, program for program -- the
  ring's FIFO order IS uasyncio's queue order, timers included;
- **the S10 differential extended to task level** (docs/23 6, decision 7): at 1, 2, 4 and 8 carriers,
  with and without the sliding compaction pass under the live tasks, every task's end state and
  result are byte-identical to the one-carrier run; the trace's multiset and each task's own
  sequence are equal (the total order across tasks is the interleaving: evidence, not result);
- **the seam's mapping as evidence**: the ring is the only queue (pops == ring pops), parks
  and wakes balance, a cancel is one raise deposit plus a wake, the clock moves only by
  edge-timer fires, a deadlock is reported (never waited on), refusals are named.

Seats (all-platforms): host C only -- this is a model proof, like ``smp_mpmodel.c``. The browser,
firmware and unix-uPy seats run the real interpreter lane (the import lane of docs/23 4.3) and bind
the same words when that lands; none is claimed here.
"""

import subprocess
from collections import Counter
from pathlib import Path

import pytest

from . import host_build
from .asyncio_corpus.programs import PROGRAMS
from .mp_reference import micropythons, seat_of

HERE = Path(__file__).parent
HOST = HERE / "host"
CORPUS = HERE / "asyncio_corpus"
SOURCES = [HOST / name for name in (
    "smp_asyncio_prove.c", "smp_asyncio.c", "smp_sched.c", "smp_arena.c", "smp_mpmodel.c",
)]
COMPACT = 1  # AIO_FLAG_COMPACT of smp_asyncio.h
MPYS = micropythons()
NAMES = list(PROGRAMS)


# ---- the program text and the report ----------------------------------------------------------

def program_text(program: dict) -> str:
    """The text form of smp_asyncio.h: ``events N``, then ``task I`` and ``op NAME [ARG]`` lines."""
    lines = [f"events {program['events']}"]
    for index, ops in enumerate(program["tasks"]):
        lines.append(f"task {index}")
        for op in ops:
            lines.append("op " + " ".join(str(word) for word in op))
    return "\n".join(lines) + "\n"


class Report:
    """One run's report: the result lines, the trace entries, the evidence words."""

    def __init__(self, text: str):
        self.tasks: list[str] = []
        self.trace: list[str] = []
        self.evidence: dict[str, int] = {}
        for line in text.splitlines():
            if line.startswith("task "):
                self.tasks.append(line)
            elif line.startswith("trace"):
                self.trace = line.split()[1:]
            elif line.startswith("evidence "):
                self.evidence = {k: int(v) for k, v in (w.split("=") for w in line.split()[1:])}

    def own_sequences(self) -> dict[str, list[str]]:
        """Each task's own emits, in order: the part of the trace that is a result at N carriers."""
        out: dict[str, list[str]] = {}
        for entry in self.trace:
            task, value = entry.split(":")
            out.setdefault(task, []).append(value)
        return out


def run_text(text: str, carriers: int = 1, flags: int = 0, mode: str = ""):
    binary = host_build.binary(SOURCES, "smp_asyncio_prove", mode)
    return subprocess.run(
        [str(binary), f"--carriers={carriers}", f"--flags={flags}"],
        input=text, capture_output=True, text=True, timeout=120, check=False,
    )


def model(name: str, carriers: int = 1, flags: int = 0, mode: str = "") -> tuple[Report, str]:
    made = run_text(program_text(PROGRAMS[name]), carriers, flags, mode)
    assert made.returncode == 0, made.stderr[-2000:]
    return Report(made.stdout), made.stderr


@pytest.fixture(scope="module", params=MPYS or [None], ids=lambda p: seat_of(p) if p else "no-mpy")
def reference(request) -> dict[str, Report]:
    """MicroPython's real asyncio over the whole corpus, one process: program -> its report."""
    if request.param is None:
        pytest.skip("no MicroPython unix binary in the workspace (set RXPY_MPY): the reference "
                    "differential needs real uasyncio; the model's own pins still stand")
    made = subprocess.run(
        [request.param, str(CORPUS / "reference.py"), str(CORPUS / "programs.py"), *NAMES],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert made.returncode == 0, f"{request.param}: the reference did not run: {made.stderr}"
    blocks = made.stdout.split("program ")[1:]
    answers = {block.splitlines()[0]: Report(block) for block in blocks}
    assert list(answers) == NAMES, "the reference ran a different corpus"
    return answers


def results_of(report: Report) -> tuple[list[str], int]:
    """What is a RESULT: every task's end state and fold, and the final virtual clock."""
    return report.tasks, report.evidence["clock_end"]


# ---- the corpus itself -------------------------------------------------------------------------

def test_the_corpus_is_well_formed():
    """Every task is spawned exactly once (a program is a function of its text), and the data the
    two executors read has the shape both of them parse."""
    for name, program in PROGRAMS.items():
        spawned = Counter(op[1] for ops in program["tasks"] for op in ops if op[0] == "spawn")
        assert spawned == Counter(range(1, len(program["tasks"]))), name
        for ops in program["tasks"]:
            for op in ops:
                assert len(op) == (1 if op[0] == "now" else 2), (name, op)


# ---- the reference differential ----------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_at_one_carrier_the_model_is_what_micropython_asyncio_does(reference, name):
    """The ring's FIFO order is uasyncio's own queue order: results AND the whole trace equal."""
    want = reference[name]
    got, _ = model(name, carriers=1)
    assert got.tasks == want.tasks, name
    assert got.trace == want.trace, name


# ---- the S10 differential, extended to task-level results --------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_task_results_are_byte_identical_at_every_carrier_count(name):
    base, _ = model(name, carriers=1)
    assert base.evidence["deadlock"] == 0, name
    base_own = base.own_sequences()
    for carriers in (2, 4, 8):
        for flags in (0, COMPACT):
            got, _ = model(name, carriers=carriers, flags=flags)
            where = (name, carriers, flags)
            assert got.evidence["deadlock"] == 0, where
            assert results_of(got) == results_of(base), where
            assert sorted(got.trace) == sorted(base.trace), where
            assert got.own_sequences() == base_own, where
            for word in ("checksum_fails", "state_refused", "refused_spawns"):
                assert got.evidence[word] == 0, (where, word)


@pytest.mark.parametrize("name", NAMES)
def test_n_carriers_agree_with_micropython_on_every_result(reference, name):
    """The same claim against the reference itself, not only against the model's own one-carrier
    run: task end states, result folds, and the multiset of the trace."""
    want = reference[name]
    got, _ = model(name, carriers=4, flags=COMPACT)
    assert got.tasks == want.tasks, name
    assert sorted(got.trace) == sorted(want.trace), name


def test_runs_migrate_across_carriers_and_cells_move_under_live_tasks():
    """Evidence the differential above is not vacuous: with several carriers the tasks are
    stepped on more than one of them, and the sliding pass moves cells (the run cells and the
    ring itself) while the tasks run."""
    stepping, moved = 0, 0
    for _ in range(10):
        got, _ = model("churn_yields", carriers=4, flags=COMPACT)
        stepping = max(stepping, got.evidence["carriers_stepping"])
        moved += got.evidence["moved_cells"]
        if stepping >= 2 and moved > 0:
            break
    assert stepping >= 2
    assert moved > 0


# ---- the mapping of docs/23 4.5 as evidence ----------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_the_ring_is_the_only_queue_and_parks_and_wakes_balance(name):
    got, _ = model(name, carriers=1)
    e = got.evidence
    pending = sum(1 for line in got.tasks if line.split()[2] == "P")
    assert e["ring_popped"] == e["pops"]  # every step is a pop of THE ring
    assert e["ring_pushed"] >= e["ring_popped"]  # nothing runs that was not pushed
    assert e["raise_polls"] > 0  # every step is a poll: the safe point (S11)
    assert e["parks"] == e["wakes"] + pending, name  # one wake per park; a pending task still sleeps
    assert e["carriers_stepping"] == 1


def test_a_wake_is_a_hint_the_condition_is_data():
    """The wake token of S4 stays set after a SUCCESSFUL wake (smp_wake stores it before the
    PARKED to READY attempt and nobody takes it back), so the run's next park returns at once: a
    spurious resume. Found by this corpus (``pipeline``: a stage woken by an event parks again for
    its timer). The binding's rule is the condvar's: a blocked op re-checks its registration (the
    waker clears it under the lock before it wakes) and parks again when it has not been served --
    and the results above stay equal to MicroPython's."""
    got, _ = model("pipeline")
    assert got.evidence["spurious_resumes"] > 0
    # a spurious resume is a step, never a park: the park/wake books still balance (above)
    assert got.evidence["parks"] == got.evidence["wakes"]


def test_a_sleep_is_an_edge_timer_fire_and_the_clock_moves_only_by_them():
    got, _ = model("timers_in_due_order")
    # four sleepers on three distinct deadlines (1, 2, 2, 3): the two at 2 fire together
    assert got.evidence["timer_fires"] == 3
    assert got.evidence["clock_end"] == 3
    # a program with no sleep never touches the clock
    got, _ = model("spawn_join")
    assert got.evidence["timer_fires"] == 0
    assert got.evidence["clock_end"] == 0


@pytest.mark.parametrize("name", ["cancel_sleeper", "cancel_waiter", "cancel_forwarded"])
def test_a_cancel_is_one_raise_deposit_and_a_wake(name):
    got, _ = model(name)
    assert got.evidence["cancels"] == 1
    assert any(line.split()[2] == "C" for line in got.tasks)  # the victim ended cancelled
    # the deposit was a wake of a parked run, not a signal: it shows in the wake count
    assert got.evidence["wakes"] >= 1


def test_a_task_that_is_never_woken_is_a_reported_deadlock_not_a_hang():
    program = {"events": 1, "tasks": [[("spawn", 1), ("join", 1)], [("wait", 0)]]}
    made = run_text(program_text(program), carriers=2)
    assert made.returncode == 0, made.stderr
    got = Report(made.stdout)
    assert got.evidence["deadlock"] == 1
    assert [line.split()[2] for line in got.tasks] == ["P", "P"]


# ---- refusals, by name -------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("events 0\ntask 0\nop gather 1\n", "unknown name"),
        ("events 0\ntask 0\nop spawn 0\n", "or itself"),
        ("events 0\ntask 0\nop join 5\n", "does not exist"),
        ("events 1\ntask 0\nop wait 3\n", "names no event"),
        ("events 0\ntask 1\n", "count up from 0"),
        ("events 99\ntask 0\n", "AIO_MAX_EVENTS"),
        ("events 0\n", "needs a main task"),
        ("events 0\ntask 0\nop sleep\n", "one argument"),
        ("events 0\ntask 0\nop now 3\n", "takes no argument"),
        ("events 0\nretire 3\n", "unknown line"),
    ],
)
def test_what_the_model_does_not_run_is_refused_by_name(text, reason):
    made = run_text(text)
    assert made.returncode == 3, (made.returncode, made.stderr)
    assert made.stdout == ""
    assert made.stderr.startswith("refused: ")
    assert reason in made.stderr, made.stderr


# ---- the sanitizers ----------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["tsan", "asan"])
def test_the_model_is_clean_under_the_sanitizers(mode):
    """ThreadSanitizer and AddressSanitizer/UBSan over the whole corpus at 4 carriers with the
    sliding pass running: no race, no memory error, the same results as the plain build."""
    for name in NAMES:
        plain, _ = model(name, carriers=1)
        got, errors = model(name, carriers=4, flags=COMPACT, mode=mode)
        assert "ThreadSanitizer" not in errors, (name, errors[-1500:])
        assert "AddressSanitizer" not in errors, (name, errors[-1500:])
        assert "runtime error" not in errors, (name, errors[-1500:])
        assert results_of(got) == results_of(plain), name
