"""The demo world: a real-shaped image for the checked-in demo scene and the tests.

The delta in ``demo_world.xml`` builds the world a runtime snapshot would show: named modules
under the root (monuments), heap blocks, run cells (Function/Code), a channel frame, and the
references between them. On top of that delta this module applies the traces a snapshot of a
running arena carries, so the scene shows every face of the schema:

* a **run payload** — ``system.runs.boot`` carries the real ``smp_run_payload`` shape
  (docs/20, S1: 28 concurrent + 36 handoff + 208 program + 8 checksum bytes), so the block
  view of the tower has the three regions and the checksum floor;
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

import sys

from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector
from pymergetic.rxf.world import WORLD_DIR
from pymergetic.rxf.world.export import POISON_BYTE, POISON_RUN, scene_json

DELTA_FILE = WORLD_DIR / "demo_world.xml"
POISON_PAYLOAD = bytes([POISON_BYTE]) * (POISON_RUN * 4)  # 256 bytes: one run, one void tile

RUN_PAYLOAD_BYTES = 28 + 36 + 208 + 8  # smp_run_payload (docs/20, S1)
RUN_PAYLOAD = bytes((i * 7 + 3) % 256 for i in range(RUN_PAYLOAD_BYTES))  # deterministic shape

STALE_TARGET = "system.net.timer"  # removed while watchdog and probe still name it
POISONED = "system.net.slag"  # filled with 0xA5, then removed: a rubble cell of poison
RUN_CELL = "system.runs.boot"  # the run whose payload is smp_run_payload


def demo_image() -> Image:
    """The demo world as an image: the delta, then the runtime traces."""
    image = Image.empty()
    image.apply(DELTA_FILE.read_text(encoding="utf-8"))
    image.set_payload(image.handle(RUN_CELL), RUN_PAYLOAD)
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
