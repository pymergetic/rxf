"""The demo world: a real-shaped image for the checked-in demo scene and the tests.

The delta in ``demo_world.rxfd`` builds the world a runtime snapshot would show: named modules
under the root (monuments), heap blocks, run cells (Function/Code), a channel frame, and the
references between them. On top of that delta this module applies the traces a snapshot of a
running arena carries, so the scene shows every face of the schema:

* **run payloads** — ``system.runs.boot``, ``.tick`` and ``.sched`` each carry a real
  ``smp_run_payload`` record (docs/20, S1: 28 concurrent + 36 handoff + 208 program + 8 checksum
  bytes) with a real state word and a real FNV-1a checksum, so the block view of each tower has
  the three regions and the checksum floor, and the ring data the exporter reads is genuine:
  ``boot`` is RUNNING and ``tick`` READY (both on the road, one tram each) and ``sched`` is
  PARKED (docs/23 §4.5: off the road — a building's quiet floor);
* a **stale handle** — ``system.net.timer`` is removed while ``system.net.watchdog`` (a payload
  reference slot) and ``system.net.probe`` (a reference row) still name it: two red ghost cables;
* the **poison of compaction** (0xA5, S8) — ``system.net.slag`` is filled with poison and removed,
  leaving a dead cell full of 0xA5: rubble mounds and void tiles.

Everything is deterministic: the same delta and the same traces always build the same
image, so ``static/scene/demo-scene.json`` is a byte-exact golden (see tests/test_world3d.py).
Regenerate it with::

    python -m pymergetic.rxf.world.demo > src/pymergetic/rxf/world/static/scene/demo-scene.json
"""

from __future__ import annotations

import struct
import sys

from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.world import WORLD_DIR
from pymergetic.rxf.world.export import (
    POISON_BYTE,
    POISON_RUN,
    RUN_PAYLOAD_REGIONS,
    fnv1a,
    scene_json,
)

DELTA_FILE = WORLD_DIR / "demo_world.rxfd"
POISON_PAYLOAD = bytes([POISON_BYTE]) * (POISON_RUN * 4)  # 256 bytes: one run, one void tile

RUN_PAYLOAD_BYTES = sum(weight for _, weight in RUN_PAYLOAD_REGIONS)  # smp_run_payload (S1)
RUN_PROGRAM_AT = sum(weight for _, weight in RUN_PAYLOAD_REGIONS[:2])  # the program state
RUN_CHECKSUM_AT = RUN_PAYLOAD_BYTES - 8  # the FNV-1a word over the program state (S8)

# The run state words of docs/20, S1 — the demo snapshot's three runs, one word each.
RUN_STATE_READY = 1
RUN_STATE_RUNNING = 2
RUN_STATE_PARKED = 3
RUNS = {
    "system.runs.boot": RUN_STATE_RUNNING,  # the boot run: executing
    "system.runs.tick": RUN_STATE_READY,  # in the ready-ring: a tram on the road
    "system.runs.sched": RUN_STATE_PARKED,  # asleep on its wait: off the road (docs/23 §4.5)
}

STALE_TARGET = "system.net.timer"  # removed while watchdog and probe still name it
POISONED = "system.net.slag"  # filled with 0xA5, then removed: a rubble cell of poison


def run_payload(state: int, seed: int) -> bytes:
    """A real ``smp_run_payload`` record (docs/20, S1): the state word first, a deterministic
    body, and the FNV-1a checksum over the program state — the words the exporter verifies."""
    payload = bytearray((i * 7 + seed) % 256 for i in range(RUN_PAYLOAD_BYTES))
    struct.pack_into("<I", payload, 0, state)
    struct.pack_into(
        "<Q", payload, RUN_CHECKSUM_AT, fnv1a(bytes(payload[RUN_PROGRAM_AT:RUN_CHECKSUM_AT]))
    )
    return bytes(payload)


def demo_image() -> Image:
    """The demo world as an image: the delta, then the runtime traces."""
    image = Image.empty()
    image.apply(DELTA_FILE.read_text(encoding="utf-8"))
    for index, (path, state) in enumerate(sorted(RUNS.items())):
        image.set_payload(image.handle(path), run_payload(state, 3 + 4 * index))
    image.remove(image.handle(STALE_TARGET))
    slag = image.handle(POISONED)
    image.set_payload(slag, POISON_PAYLOAD)
    image.remove(slag)
    return image


def demo_inspector() -> Inspector:
    return Inspector(demo_image())


def main() -> None:
    sys.stdout.write(scene_json(demo_inspector()))


if __name__ == "__main__":
    main()
