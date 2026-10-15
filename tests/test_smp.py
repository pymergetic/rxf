"""The SMP proof (docs/20, C2): the execution policy of docs/20-EXECUTION-POLICY.md on the host.

The harness is tests/host/smp_*.c: N pthread carriers over one dispatcher loop (smp_sched.c), run
and data cells in a real arena that a concurrent mutator compacts and edits (smp_arena.c,
smp_witness.c), and one JSON door (smp_prove.c). This file compiles it in the standard mode and
under ThreadSanitizer and AddressSanitizer/UBSan, and drives the runs the policy names:

- the differential of S10: the result words are byte-identical for every carrier count and every
  flag set (compaction, delta, deferral), because the result of a program is a function of the
  seed alone;
- the migration of S5: all carriers step, runs are seen on more than one carrier, and a park on
  one carrier is resumed on another;
- the ABA refusal of S3 and S7: the stale handle is refused and its fresh sibling resolves;
- the compaction accounting of S8: every moved cell is read back at its old place or covered by
  the geometry (forward_ok + forward_lost == moved_cells), the vacated tail stays poison, and no
  step ever finds a torn run cell or a wrong data cell;
- the MicroPython seam of S11 (smp_mpmodel.c, the MPVM programs): the mp_handle_pending poll is
  the safe point, pending raises and scheduled callbacks are wake-token ops between runs, and the
  seam's accounting identities hold — won == delivered + pending_end, unwinds == delivered, and
  won + refused == raise events.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).parent
HOST = HERE / "host"
BUILD = HOST / "build"
SOURCES = [HOST / name for name in (
    "smp_prove.c", "smp_witness.c", "smp_sched.c", "smp_arena.c", "smp_mpmodel.c",
)]

# The flags of smp_witness.h: 2 compaction, 4 delta, 8 force cross-carrier resume.
RELOC, DELTA, FORCE_CROSS = 2, 4, 8
ALL_FLAGS = RELOC | DELTA | FORCE_CROSS

# The evidence words of smp_witness.h this file reads.
DIGEST, SUM, XOR = 0, 1, 2
STEPS, CROSS = 3, 4
DEFERRED, STATE_REFUSED = 5, 6
CHECKSUM_BAD, RING_STALE, HANDLE_STALE = 7, 8, 9
CLAIM_SPINS, MOVED_CELLS = 11, 13
PARKED_MOVED, MIDFLIGHT_MOVED = 17, 18
MOVED_RUN_CELLS, MOVED_RING = 19, 21
COMPACT_PASSES, PARKS, WAKES = 22, 36, 37
WATCHDOG_WAKES = 39
DONE_RUNS, MOVED_RUNS, CARRIERS_MAX = 27, 28, 29
FORWARD_OK, POISON_OK = 32, 33
CARRIER_STEPS, CARRIER_POPS = 40, 72
ABA_OK, PAIR_PARK, PAIR_RESUME, PAIR_WAKER = 104, 105, 106, 113
VERIFY_BAD, POISON_BAD, STALLED = 107, 109, 110
FORWARD_LOST = 114
# The MicroPython seam (S11).
MP_EVENTS, MP_RAISE_EVENTS, MP_RAISE_WON, MP_RAISE_REFUSED = 115, 116, 117, 118
MP_DELIVERED, MP_PENDING_END, MP_SCHED, MP_UNWINDS, MP_POLLS = 119, 120, 121, 122, 123
MP_RAISE_DEPOSITS = 124


def _compiler() -> str:
    """The host C compiler: the clang the native sources are pinned to, else the system one."""
    return "clang-18" if shutil.which("clang-18") else "cc"


def _binary(mode: str = "") -> Path:
    """The proof binary for a mode ("" standard, "tsan", "asan"), compiled when sources changed."""
    compiler = _compiler()
    flags = ["-std=c11", "-O2", "-g", "-pthread",
             "-Wall", "-Wextra", "-Werror", "-Wimplicit-function-declaration"]
    if mode == "tsan":
        flags += ["-fsanitize=thread"]
    elif mode == "asan":
        flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                  "-DRXF_SMP_ASAN"]
    digest = hashlib.sha256()
    for source in [*SOURCES, Path(compiler), Path(mode)]:
        digest.update(source.name.encode())
        if source.is_file():
            digest.update(source.read_bytes())
    digest.update(" ".join(flags).encode())
    BUILD.mkdir(parents=True, exist_ok=True)
    out = BUILD / f"smp_prove-{digest.hexdigest()[:16]}{('.' + mode) if mode else ''}"
    if not out.exists():
        made = out.with_suffix(f".{os.getpid()}")
        subprocess.run([compiler, *flags, *map(str, SOURCES), "-o", str(made)], check=True)
        os.replace(made, out)  # a build of another process is replaced whole, never half
    return out


def prove(mode: str = "", **knobs) -> tuple[dict, str]:
    """One witness run: the JSON report and the stderr of the binary."""
    args = [str(_binary(mode))]
    for name, value in knobs.items():
        args.append(f"--{name}={value}")
    answer = subprocess.run(args, capture_output=True, text=True, timeout=600)
    assert answer.returncode == 0, answer.stderr[-2000:]
    return json.loads(answer.stdout), answer.stderr


def out(report: dict, word: int) -> int:
    return report["out"][word]


def clean(report: dict) -> None:
    """What no configuration may ever show: a torn run cell, a wrong data cell, a stall."""
    assert out(report, CHECKSUM_BAD) == 0, report
    assert out(report, VERIFY_BAD) == 0, report
    assert out(report, POISON_BAD) == 0, report
    assert out(report, STALLED) == 0, report


def test_the_result_words_are_byte_identical_across_carriers_and_flags():
    """S10: the answer is a function of the seed alone; the interleaving is evidence only."""
    for seed in (1, 2, 3):
        base = None
        for carriers in (1, 2, 4, 8):
            for flags in (0, RELOC, DELTA, RELOC | DELTA, FORCE_CROSS, ALL_FLAGS):
                report, _ = prove(runs=24, carriers=carriers, seed=seed, steps=1000,
                                  messages=4, flags=flags)
                clean(report)
                result = (out(report, DIGEST), out(report, SUM), out(report, XOR))
                if base is None:
                    base = result
                else:
                    assert result == base, (seed, carriers, flags, result, base)


def test_every_carrier_steps_and_runs_migrate_across_carriers():
    """S5: the handoff is the ready-ring, and the runs are seen on more than one carrier."""
    report, _ = prove(runs=48, carriers=8, seed=7, steps=1500, messages=4, flags=ALL_FLAGS)
    clean(report)
    assert out(report, STEPS) > 40000
    for carrier in range(8):
        assert out(report, CARRIER_STEPS + carrier) > 0, carrier
        assert out(report, CARRIER_POPS + carrier) > 0, carrier
    assert out(report, MOVED_RUNS) > 0  # runs whose steps were seen on more than one carrier
    assert out(report, CARRIERS_MAX) >= 2
    assert out(report, CROSS) > 0  # parked by one carrier, resumed by another
    # The pair evidence (S4): the recorded handoff crossed carriers and the waker is the sender.
    assert out(report, PAIR_PARK) != 0 and out(report, PAIR_RESUME) != 0
    assert out(report, PAIR_PARK) != out(report, PAIR_RESUME)
    assert out(report, PAIR_WAKER) % 2 == 1  # the sender (its index + 1) woke the receiver
    assert out(report, PARKS) > 0 and out(report, WAKES) > 0


def test_the_stale_handle_is_refused_and_its_fresh_sibling_resolves():
    """S3 and S7: a handle whose slot generation moved names nothing, never the new cell."""
    report, _ = prove(runs=16, carriers=4, seed=5, steps=800, messages=4, flags=ALL_FLAGS)
    clean(report)
    assert out(report, ABA_OK) == 3  # refused and re-resolved, on the same slot index
    assert out(report, RING_STALE) >= 1  # refused at the ring pop
    assert out(report, HANDLE_STALE) >= 1  # refused at the resolve
    assert out(report, STATE_REFUSED) == 0  # the protocol never enqueues a run twice (S3)


def test_compaction_accounts_for_every_moved_cell_and_the_tail_stays_poison():
    """S8: concurrent compaction under live runs, with the C_FORWARD accounting identity."""
    report, _ = prove(runs=64, carriers=4, seed=9, steps=1500, messages=4,
                      flags=RELOC | DELTA)
    clean(report)
    assert out(report, COMPACT_PASSES) > 0
    assert out(report, MOVED_CELLS) > 0
    assert out(report, MOVED_RUN_CELLS) > 0
    assert out(report, MOVED_RING) >= 0  # the ready-ring is a cell and may be relocated
    # Every moved cell is either read back at its old place or covered by the geometry.
    assert out(report, FORWARD_OK) + out(report, FORWARD_LOST) == out(report, MOVED_CELLS)
    assert out(report, FORWARD_OK) > 0
    # A run that found its data cell at a new offset: the relocation tracking of S7/S8.
    assert out(report, PARKED_MOVED) > 0  # cells moved under parked runs are legal
    assert out(report, MIDFLIGHT_MOVED) > 0  # moved under live runs between safe points
    assert out(report, POISON_OK) == 1  # nothing touched what the passes vacated
    assert out(report, CLAIM_SPINS) >= 0  # the compactor's bounded wait for claims


def test_the_stress_run_finishes_every_run_under_full_churn():
    """C2: hundreds of runs, randomized parks and wakes, deltas and passes alongside: all end."""
    report, _ = prove(runs=256, carriers=8, seed=11, steps=1000, messages=8, flags=ALL_FLAGS)
    clean(report)
    assert out(report, DONE_RUNS) == 256
    # 239 workers each run their full thousand steps; the channel pairs and the stale run add
    # their attempts on top.
    assert out(report, STEPS) >= 239 * 1000
    assert out(report, MOVED_RUNS) > 0
    assert out(report, CARRIERS_MAX) >= 2
    assert out(report, FORWARD_OK) + out(report, FORWARD_LOST) == out(report, MOVED_CELLS)
    assert out(report, POISON_OK) == 1


def test_the_differential_matches_the_single_carrier_reference_under_compaction():
    """S10 against the reference run: the one-carrier, no-flag run is the byte anchor."""
    reference, _ = prove(runs=32, carriers=1, seed=11, steps=1200, messages=4, flags=0)
    clean(reference)
    for flags in (RELOC, DELTA, RELOC | DELTA, ALL_FLAGS):
        for carriers in (2, 4, 8):
            report, _ = prove(runs=32, carriers=carriers, seed=11, steps=1200,
                              messages=4, flags=flags)
            clean(report)
            assert (out(report, DIGEST), out(report, SUM), out(report, XOR)) == (
                out(reference, DIGEST), out(reference, SUM), out(reference, XOR))


def test_the_watchdog_wakes_parked_workers_so_no_run_depends_on_its_partner():
    """S9: timeout liveness is the edge's job; worker runs have no partner to wake them."""
    report, _ = prove(runs=32, carriers=2, seed=13, steps=1000, messages=4, flags=0)
    clean(report)
    assert out(report, PARKS) > 0
    assert out(report, WATCHDOG_WAKES) > 0  # the shim woke parked workers with the same wake


def test_the_micropython_seam_parks_and_wakes_like_run_cells():
    """S11: the mp_handle_pending poll is the safe point of a bytecode run.

    The MPVM pairs play the MicroPython shape against the run-cell machine (smp_mpmodel.c is the
    standalone C model of the seam, not MicroPython): VM-loop iterations with the poll at every
    back edge, raises and scheduled callbacks deposited by the partner run (wake-token ops, never
    signals into the run), an nlr-shaped unwind to the frame checkpoint on every raise, and
    blocking calls that park. The identities are the seam's contract:

    - won + refused == deposits (the single pending slot and its Refuse path, exercised
      guaranteed by the exit burst: two deposits at a run that will never poll again);
    - won == delivered + pending_end (a raise is delivered exactly once or is still pending when
      the VM run ended);
    - unwinds == delivered (a raise always lands after work and rewinds its frame).

    The ready-ring order makes a source's first event precede its VM's first step, so a raise is
    delivered and a callback consumed in every configuration. The result words of these runs are
    part of the differential above: the seam never leaks timing into the answer (S10).
    """
    report, _ = prove(runs=32, carriers=4, seed=17, steps=1000, messages=8, flags=ALL_FLAGS)
    clean(report)
    assert out(report, MP_POLLS) > 0
    assert out(report, MP_RAISE_WON) >= 1
    assert out(report, MP_RAISE_WON) == out(report, MP_DELIVERED) + out(report, MP_PENDING_END)
    assert out(report, MP_UNWINDS) == out(report, MP_DELIVERED)
    assert out(report, MP_DELIVERED) >= 1
    assert out(report, MP_SCHED) >= 1
    assert out(report, MP_RAISE_WON) + out(report, MP_RAISE_REFUSED) == out(report, MP_RAISE_DEPOSITS)
    assert out(report, MP_RAISE_REFUSED) >= 1  # the exit burst: a deposit refused at teardown
    assert out(report, MP_RAISE_DEPOSITS) > out(report, MP_RAISE_EVENTS)  # the burst deposits more
    assert out(report, MP_RAISE_EVENTS) * 2 == out(report, MP_EVENTS)  # raises and callbacks
    assert out(report, WAKES) > 0  # a pending event is a wake of the VM run (S4)


@pytest.mark.parametrize("mode", ["tsan", "asan"])
def test_the_harness_is_clean_under_the_sanitizers(mode):
    """C2: ThreadSanitizer and AddressSanitizer/UBSan over the same witness, clean both ways.

    The ASAN build is also the poison proof (S8): the vacated tail is ASAN-poisoned by the
    passes, so a stale raw address that reads it faults in the sanitizer, and alloc taking the
    bytes back unpoisons them first.
    """
    report, errors = prove(mode, runs=32, carriers=4, seed=3, steps=600, messages=4,
                           flags=ALL_FLAGS)
    assert "ThreadSanitizer" not in errors
    assert "AddressSanitizer" not in errors
    assert "runtime error" not in errors  # UBSan
    clean(report)
    assert out(report, FORWARD_OK) + out(report, FORWARD_LOST) == out(report, MOVED_CELLS)
    if mode == "asan":
        assert out(report, POISON_OK) == 1
