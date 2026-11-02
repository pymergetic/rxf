"""RELINK end to end: the rebind of a runtime context's payload slots, pinned on every seat.

RELINK is the one write op that does not write the arena: it writes the payload slots of a
runtime context (native/stdlib_runtime.h) from the arena the live image holds. The walk is
the twin's ``m_relink`` (native/live.rxfs), the reference semantics every seat answers:

* one row per live object -- ``(id, type_id, payload address, payload size)`` where the
  payload address is the object's value behind the record's 24-byte head (and a module's
  8-byte chain head), past the fixed words of its type (the reference-layout prefix);
* the **object entries** at ``ctx.objects`` behind the 8 spare ones (``rxf_object_entry``,
  80 B: id, generation 1, type_id, payload, size, capacity, state LIVE) and the count word
  at ``ctx + 32``;
* the **function slots** (``rxf_native_slot``, 24 B) at ``ctx.functions``: id, generation,
  **address** -- the code address of each leaf row of ``pymergetic.rxf.foundation.graph.run``.
  A wasm32 leaf is a table index, so its slot carries no address (the per-seat fill may
  differ; the module and face may not).

A rebind is observed by wiping those payload slots (the slot addresses, the entries behind
the spares, the count) and calling RELINK: the context comes back **byte for byte** the one
it held before the wipe and the machine runs again.

Seat matrix (packages/REPO_LAYOUT.md) -- what each seat can honestly do TODAY:

+-------------------------------+----------------------------------------+-------------------------------+
| seat                          | runtime context                        | RELINK                        |
+===============================+========================================+===============================+
| host engine (HostImage)       | none (the bindings' pump passes none)  | refused UNBOUND 15            |
+-------------------------------+----------------------------------------+-------------------------------+
| native wasm32                 | real, leaves by table index            | rebinds entries, no slot      |
+-------------------------------+----------------------------------------+-------------------------------+
| native x86_64                 | real                                   | rebinds entries and slots     |
+-------------------------------+----------------------------------------+-------------------------------+
| native aarch64                | real                                   | rebinds entries and slots     |
+-------------------------------+----------------------------------------+-------------------------------+
| twin wasm32 (m_relink)        | real (tests/test_twin_seat.py)         | answers (8 + count, 0)        |
+-------------------------------+----------------------------------------+-------------------------------+

No rebind is faked where a seat cannot do one: the tests below assert either the rebind
taking effect byte for byte or the seat's honest refusal -- and a refusal leaves the wiped
context exactly as the wipe left it (no half-rebind).
"""

from __future__ import annotations

import struct

import link
import pytest

from pymergetic.rxftk.live import physical
from pymergetic.rxftk.live.client import Declined
from pymergetic.rxftk.live.harness import HostImage

from . import live_world as lw
from .support import digest_of, model_copy, resolved
from .test_live import WORLD, host, starter  # noqa: F401  (fixtures and model)
from .test_live_native import (  # noqa: F401  (fixtures and helpers)
    context_of,
    image,
    name,
    native,
)

NONE = 0xFFFFFFFFFFFFFFFF
UNBOUND = 15


def machine_words(channel: lw.Channel) -> bytes:
    """The 18 context words as the machine holds them (native/stdlib_runtime.h)."""
    return channel.machine.read(channel.machine.linked.context, 144)


def wipe(channel: lw.Channel) -> None:
    """The payload slots of the rebind are gone: no slot address, no entry behind the
    spares, no count. What is left is the context's words."""
    machine = channel.machine
    linked = machine.linked
    words = machine_words(channel)
    slot_at, entry_at = (
        struct.unpack_from("<Q", words, 24)[0],
        struct.unpack_from("<Q", words, 40)[0],
    )
    _words, slots, entries = context_of(channel)
    addressed = linked.machine != "wasm32"
    for k in range(len(slots) // link.SLOT_BYTES if addressed else 0):
        machine.write(slot_at + k * link.SLOT_BYTES + 16, bytes(8))
    spare = link.SPARE * link.ENTRY_BYTES
    machine.write(entry_at + spare, bytes(len(entries) - spare))
    machine.write(linked.context + 32, struct.pack("<Q", 0))


def expect_written(channel: lw.Channel) -> tuple[int, int]:
    """What a relink must answer for the context at hand: every entry, and every slot a
    seat addresses (a wasm32 leaf is a table index: no slot to set)."""
    _words, slots, entries = context_of(channel)
    addressed = channel.machine.linked.machine != "wasm32"
    return (
        len(entries) // link.ENTRY_BYTES,
        len(slots) // link.SLOT_BYTES if addressed else 0,
    )


# ---- the seat without a context: the honest refusal ----------------------------------------------


def test_the_host_engine_refuses_a_relink_with_its_unbound_reason(
    host: HostImage,  # noqa: F811
):
    """The host engine holds no runtime context (its bindings' pump passes none), so RELINK
    has nothing to rebind and says so: UNBOUND (15), the same reason the twin answers when
    it runs without a context. The refusal is honest - the test pins the refusal, not a
    rebind the engine cannot do."""
    digest = host.client.digest()
    with pytest.raises(Declined) as caught:
        host.client.relink()
    assert caught.value.reason == UNBOUND
    assert "context" in str(caught.value)
    assert host.client.digest() == digest, "the refusal changed nothing"


def test_the_unbound_refusal_is_stable_across_calls_and_after_an_apply(
    starter: bytes,  # noqa: F811
    host: HostImage,  # noqa: F811
):
    """The refusal is the seat's standing answer, not a one-shot state: the same reason comes
    again after a served write, and the write side around it works (the apply commits)."""
    for _ in range(2):
        with pytest.raises(Declined) as caught:
            host.client.relink()
        assert caught.value.reason == UNBOUND
    result = model_copy(WORLD)
    result.set_state(resolved(result, "pymergetic.rxf.application.main"), 9)
    compiled = physical.compile(model_copy(WORLD), result, check_base=False)
    digest = host.client.apply(compiled.data)
    assert digest == digest_of(result)
    with pytest.raises(Declined) as caught:
        host.client.relink()
    assert caught.value.reason == UNBOUND
    assert host.client.digest() == digest, "the refusal rolled nothing back"


# ---- the seats with a context: the rebind takes effect, or the refusal is honest -----------------


@pytest.mark.nightly
@pytest.mark.slow
def test_a_relink_rebinds_the_wiped_payload_slots(
    native: lw.Channel,  # noqa: F811
    name: str,  # noqa: F811
):
    """The before/after of a rebind: the context the linker wrote is wiped down to its words
    (no slot address, no entry behind the spares, no count) and RELINK brings it back byte
    for byte - the payload slots again name the values and code addresses of the arena. The
    answer counts what was written: every entry, and every slot a seat addresses."""
    machine = native.machine
    linked = machine.linked
    native.client.ping()
    words, slots, entries = context_of(native)
    assert words == linked.data[:144], "the context is the linker's, word for word"
    wanted = expect_written(native)
    entry_count = len(entries) // link.ENTRY_BYTES
    wipe(native)
    wiped = context_of(native, entry_count)
    assert wiped[2] != entries, "the wipe took"
    written, count = native.client.relink()
    assert (written, count) == wanted
    assert context_of(native) == (words, slots, entries)
    assert native.client.ping(b"after the rebind") == b"after the rebind"


@pytest.mark.nightly
@pytest.mark.slow
def test_a_relink_rebinds_the_context_an_apply_changed(
    native: lw.Channel,  # noqa: F811
    name: str,  # noqa: F811
):
    """End to end: a delta commits and the image relinks itself (native/live.rxfs: the apply
    walks m_relink), so the entries name the object the delta made - the count word moved by
    the one create. A relink after a wipe then rebinds the context the changed arena
    deserves: the rebind follows the arena, not the image the machine was linked with."""
    native.client.ping()
    first = struct.unpack_from("<Q", machine_words(native), 32)[0]
    result = model_copy(WORLD)
    result.add(
        resolved(result, "pymergetic.rxf.application"), "fresh", 8, NONE, b"payload"
    )
    compiled = physical.compile(model_copy(WORLD), result, check_base=False)
    applied = native.client.apply(compiled.data)
    assert applied == digest_of(result)
    # the self-rebind of the apply: the new object is an entry of the context
    assert struct.unpack_from("<Q", machine_words(native), 32)[0] == first + 1
    # and an explicit relink rebinds a wiped context to the same bytes
    words, slots, entries = context_of(native)
    wanted = expect_written(native)
    entry_count = len(entries) // link.ENTRY_BYTES
    wipe(native)
    wiped = context_of(native, entry_count)
    assert wiped[2] != entries, "the wipe took"
    written, count = native.client.relink()
    assert (written, count) == wanted
    assert context_of(native) == (words, slots, entries)
