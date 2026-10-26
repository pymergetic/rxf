#!/bin/bash
# The test lanes. Marker discipline lives in pyproject.toml (slow, nightly, live).
#   fast    the pre-commit lane: no slow, no nightly, no live. Seconds, not minutes.
#   slow    the slow slice tests: kept out of pre-commit, still xdist-parallel.
#   live    the socket, server, and race tests: serial, never under xdist.
#   nightly the emulator and per-test cold-start matrices: xdist per test.
#   slice   what a slice gate needs: fast + slow parallel, then live serial.
#   full    everything: slice, then nightly.
# Any extra argument after the lane is passed to pytest as-is.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTEST:-.venv/bin/python}
lane=${1:-fast}
shift || true

run() {
  echo "== pytest $*"
  "$PY" -m pytest "$@"
}

case "$lane" in
  fast)
    run -n auto -m "not slow and not nightly and not live" --durations=10 "$@"
    ;;
  slow)
    run -n auto -m "slow and not nightly and not live" --durations=10 "$@"
    ;;
  live)
    run -m live --durations=10 "$@"
    ;;
  nightly)
    run -n auto -m nightly --durations=10 "$@"
    ;;
  slice)
    run -n auto -m "not nightly and not live" --durations=10 "$@"
    run -m live --durations=10 "$@"
    ;;
  full)
    run -n auto -m "not nightly and not live" --durations=10 "$@"
    run -m live --durations=10 "$@"
    run -n auto -m nightly --durations=10 "$@"
    ;;
  *)
    echo "unknown lane: $lane (fast | slow | live | nightly | slice | full)" >&2
    exit 2
    ;;
esac
