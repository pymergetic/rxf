"""Build the live pump (``native/live.rxfs``) with the seed compiler (docs/29, docs/114 §12).

``native/live.rxfs`` is the ONE defining source of the live pump: the faithful port of
``native/live.cpp`` (the leaf ``rxf_live_pump``) over ``src/pymergetic/rxftk/core/live/live.cpp``
and ``src/pymergetic/rxftk/core/arena/arena.cpp`` in the Seed ``fn`` dialect. This helper is
the one build path: it runs the seed CLI (``native/seed/seed.c``) over that file and returns
the code of ``live_pump`` per machine -- the object-ABI body of
``f(params..., out) -> status``, here spelled ``!pp``:
``live_pump(context, area)``, a status back (every exit is a ``refuse``; the C ABI has no
store-through-out).

    python tools/native/live_build.py            # hex sizes of the three machines
    python tools/native/live_build.py --json     # {machine: hex}

The wasm32 body's ``call_indirect`` names a leaf's function type by the position of the
leaf's signature in the type section of whoever composes the snippets (the composer,
``tests/wasm.py module_of``), so the ``@`` argv is ``seed_build.call_type_letters()``,
one entry per leaf in the order of its id -- asserted here against that table.

The native bodies must fit the log region of a live area (``LiveSpec.log``, 1 << 18): a
compaction that would move the pump's own code makes it copy itself there and tail-call
the copy (``native/live.cpp``, docs/114 §12). The size of every native body is checked
against that budget here, so an overgrown port fails the build instead of the
relocation test.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import seed_build

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[1] / "native" / "live.rxfs"
SELF = "!pp"  # live_pump(context, area): 2 C arguments / 2 words
STOREW = 0  # no store through an out parameter: every exit is a `refuse <status>`
FN = "live_pump"
LOG_BUDGET = 1 << 18  # LiveSpec.log: the log region a self-copy must fit in


def source() -> str:
    """The pump source, and that it names no second safe_point (its own file does)."""
    text = SOURCE.read_text()
    if re.search(r"^fn safe_point\(", text, re.M):
        raise RuntimeError(
            "native/live.rxfs defines safe_point: its one defining source is "
            "native/safe/safe.rxfs (seed_build.SOURCES)"
        )
    return text


def call_shapes(text: str) -> list[str]:
    """The ``call_<shape>`` conventions the source uses, in order."""
    seen: list[str] = []
    for shape in re.findall(r"call_([A-Za-z]+)\(", text):
        if shape not in seen:
            seen.append(shape)
    return seen


def code(machine: str) -> str:
    """The hex of the object-ABI code of ``live_pump`` on ``machine``."""
    argv = [
        str(seed_build.seed_bin()),
        "codegen",
        seed_build.TARGET[machine],
        "object",
        str(STOREW),
        f"#{FN}",
        SELF,
        *(f"@{letters}" for letters in seed_build.call_type_letters()),
    ]
    proc = subprocess.run(
        argv, input=source(), capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def build() -> dict[str, str]:
    """``{machine: hex}`` of ``live_pump`` for every machine of ``seed_build.TARGET``."""
    out = {machine: code(machine) for machine in seed_build.TARGET}
    for machine, data in out.items():
        if machine == "wasm32":
            continue  # wasm32 holds its code outside the arena: no self-copy, no budget
        size = len(data) // 2
        if size > LOG_BUDGET:
            raise RuntimeError(
                f"{machine} body is {size} bytes: the log region is {LOG_BUDGET} and "
                "the relocation path copies the body there"
            )
    return out


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--json":
        print(json.dumps(build(), sort_keys=True))
        return
    if len(sys.argv) != 1:
        raise SystemExit(__doc__)
    text = source()
    print("call shapes:", " ".join(call_shapes(text)) or "-")
    for machine, data in build().items():
        print(machine, len(data) // 2)


if __name__ == "__main__":
    main()
