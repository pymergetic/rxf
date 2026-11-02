"""The boot build lock: one writer at a time in ``boot/build`` (tools/native/boot_lock.py).

Every test uses a lock file of its own: the real one is held, rightly, by whichever boot test
builds in another worker at the time.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import boot_lock
import pytest


@pytest.fixture(autouse=True)
def private_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "build.lock"
    monkeypatch.setattr(boot_lock, "LOCK", path)
    return path


def test_the_lock_is_reentrant_within_a_thread():
    with boot_lock.writing(), boot_lock.writing():
        pass
    with boot_lock.writing():
        pass


def test_a_second_process_cannot_write_while_the_lock_is_held(private_lock: Path):
    probe = textwrap.dedent(
        f"""
        import fcntl, sys
        with open({str(private_lock)!r}, "a") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sys.exit(7)
        """
    )
    with boot_lock.writing():
        held = subprocess.run([sys.executable, "-c", probe], check=False)
    assert held.returncode == 7, "another process took the lock while it was held"
    free = subprocess.run([sys.executable, "-c", probe], check=False)
    assert free.returncode == 0, "the lock was not released"


def test_the_shell_and_python_writers_exclude_each_other(private_lock: Path):
    """run_smoke.sh takes the same file with flock(1): held here, it must wait."""
    command = ["flock", "--nonblock", str(private_lock), "true"]
    with boot_lock.writing():
        waiting = subprocess.run(command, check=False)
    assert waiting.returncode == 1
    assert subprocess.run(command, check=False).returncode == 0
