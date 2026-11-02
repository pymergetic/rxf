"""The twin's write slice on the wasm32 seat, pinned against the host engine.

The wasm32 seat is the native twin (native/live.rxfs) running in node behind
the page's own modules (``tests/twin_seat.mjs``). What the twin answers for the
jobs behind the console's acts is what the engine answers for the same delta
bytes, and where the twin checks more than the engine, the check is real:

* COMPACT after a rename (delete + create) and an add answers the engine's
  56 bytes: the handle count, the body size, the digest and the reclaimed
  bytes. The compaction slides the image the pump's own program sits in, so it
  is the last request a seat answers until the host re-reads the program.
* RELINK answers its entries and function slots (the host engine holds no
  runtime context and refuses: ``Declined``). The wasm32 seat addresses its
  leaves by table index, so it sets no function slot.
* an ADD under a LEAF (a value that gains a child becomes a module, docs/32 1.2)
  commits with the engine's digest, and the next unrelated act applies;
* a SET of a module with its own record changes nothing: the patches of a delta are
  offsets into the object's VALUE, which sits behind a module's chain head (docs/32
  1.2, the writer's EMPTY_MODULE_HEAD), unset slots (the NONE patch) included;
* an apply that leaves a slot of a declared reference pointing at an object
  that is gone is refused (reason DANGLING) and the arena is rolled back: the
  digest stands and the next unrelated delta applies. The engine takes the
  same delta; its dump then unsets the slot (docs/32 5.4) - the finding is in
  night.log.
"""

from __future__ import annotations

import json
import struct
import subprocess
from pathlib import Path
from typing import Any

import link

from pymergetic.rxftk.live import harness, physical
from web import acts

from .test_web_console import artifacts, served  # noqa: F401  (fixtures)

HARNESS = Path(__file__).with_name("twin_seat.mjs")
COMPACT, RELINK = 0x23, 0x25
DANGLING = "something points at an object that is gone"
SET = {"act": "set", "path": "pymergetic.rxf.application.discount", "value": 45}
ADD = {
    "act": "add",
    "parent": "pymergetic.rxf.application",
    "name": "surcharge",
    "value": 7,
}
RENAME = {
    "act": "rename",
    "path": "pymergetic.rxf.application.shipping",
    "name": "freight",
}


def drive(base: str, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    done = subprocess.run(
        ["node", str(HARNESS), base, json.dumps(steps)],
        capture_output=True,
        text=True,
        check=True,
        timeout=900,
    )
    return json.loads(done.stdout)


def test_the_seats_compaction_is_the_engines_answer(served: str) -> None:  # noqa: F811
    steps = drive(served, [{"act": RENAME}, {"act": ADD}, {"op": COMPACT}])
    assert all("error" not in step for step in steps), steps
    engine = harness.HostImage(link.arena_of(acts.model()))
    for step in steps[:2]:
        assert engine.client.apply(bytes.fromhex(step["delta"])) == step["after"]
    wanted = engine.client.compact()
    answer = bytes.fromhex(steps[2]["payload"])
    assert steps[2]["status"] == 0 and len(answer) == 56
    handles, body, reclaimed = struct.unpack("<QQ", answer[:16]) + struct.unpack(
        "<Q", answer[48:56]
    )
    assert (handles, body, answer[16:48].hex(), reclaimed) == (
        wanted.handles,
        wanted.body_size,
        wanted.digest,
        wanted.reclaimed,
    )
    assert wanted.digest == steps[1]["after"], "a compaction keeps the canonical digest"


def test_the_seats_relink_answers_its_entries(served: str) -> None:  # noqa: F811
    steps = drive(served, [{"act": ADD}, {"op": RELINK}])
    assert "error" not in steps[0], steps
    assert steps[1]["status"] == 0, steps[1]
    entries, slots = struct.unpack("<II", bytes.fromhex(steps[1]["payload"])[:8])
    assert entries > 8, "the semantic objects are entries of the context"
    assert slots == 0, "wasm32 names its leaves by table index: no slot to set"


def test_a_slot_that_points_at_a_gone_object_refuses_and_rolls_back(
    served: str,  # noqa: F811
) -> None:
    builder = physical.Builder()
    builder.delete("uint8_t")  # every Field of that value type holds a handle to it
    steps = drive(
        served,
        [
            {"digest": True},
            {"raw": builder.build().hex()},
            {"digest": True},
            {"act": SET},
        ],
    )
    assert steps[1]["error"] == "delta refused: " + DANGLING, steps[1]
    assert steps[2]["before"] == steps[0]["before"], "the refusal changed nothing"
    assert "error" not in steps[3], "the next unrelated delta applies"
    assert steps[3]["after"] != steps[0]["before"]


def test_an_add_under_a_leaf_turns_it_into_a_module(served: str) -> None:  # noqa: F811
    leaf = "pymergetic.rxf.application.discount"
    under = {"act": "add", "parent": leaf, "name": "extra", "value": 3}
    after = {"act": "set", "path": "pymergetic.rxf.application.quantity", "value": 9}
    steps = drive(served, [{"act": under}, {"act": after}])
    assert all("error" not in step for step in steps), steps
    engine = harness.HostImage(link.arena_of(acts.model()))
    for step in steps:
        assert engine.client.apply(bytes.fromhex(step["delta"])) == step["after"]
    assert engine.client.resolve(leaf + ".extra") is not None


def test_a_set_of_a_module_with_its_own_record_changes_nothing(served: str) -> None:  # noqa: F811
    records, _entry = physical.records(acts.model())
    record = records["Type"]  # a module (it has Fields) whose value has unset slots
    assert any(target is None for _offset, target in record.patches)
    builder = physical.Builder()
    builder.set("Type", record)
    steps = drive(served, [{"digest": True}, {"raw": builder.build().hex()}])
    assert "error" not in steps[1], steps[1]
    assert steps[1]["after"] == steps[0]["before"]
