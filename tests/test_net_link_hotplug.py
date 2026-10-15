"""Pins for the hotplug plane (docs/25 §7): device events as the machine handles them.

What is pinned here: a device appearing, changing, or disappearing is an event written
as a cell (the `LinkEvent` word of the frozen set) — hotplug is a writer of ordinary
cells, not a whisper into a side channel; the udev-shaped matcher's tags and commands
are *config*, recorded on the event and executed by nobody here; and hotplug cannot
race the control plane (§8): a device event during an apply lands before or after it,
never inside — one whole successor per commit, no mixed order.
"""

from __future__ import annotations

import pytest

from pymergetic.rxf.net.control.netplan import (
    AddressSpec,
    DeviceSpec,
    Doc,
    RouteSpec,
    apply,
    plan,
)
from pymergetic.rxf.net.link.link import (
    EventAction,
    Hotplug,
    LinkEvent,
    LinkKind,
    LinkPlane,
    MemoryGraph,
    Port,
    PropMatch,
    Rulebook,
    UdevRule,
    Write,
)
from pymergetic.rxf.net.schema import (
    AddressInUse,
    Device,
    IPPrefix,
    LinkState,
    NotSupported,
    StaleHandle,
)


def test_appear_matches_properties_and_emits_one_event() -> None:
    """A device appearing runs the rulebook (§7): a rule matches on the property words
    given and the event carries the tags and commands the rules assign — exactly."""
    graph = MemoryGraph()
    rules = Rulebook(
        (
            UdevRule((PropMatch("kind", "==", "ethernet"),), tags=frozenset({"net-port"}), commands=("if-up",)),
            UdevRule((PropMatch("kind", "==", "loopback"),), tags=frozenset({"loopback"})),
        )
    )
    hotplug = Hotplug(graph, rules, by="operator")
    event = hotplug.appear("eth0", mac=b"\x02" * 6, link=LinkState.DOWN, props={"kind": "ethernet"})
    assert isinstance(event, LinkEvent)
    assert event.action is EventAction.ADD
    assert event.device == "net/dev/eth0"
    assert event.tags == frozenset({"net-port"})
    assert event.commands == ("if-up",)
    assert graph.get(event.path) is event  # the event is a cell, and it is in the graph
    assert isinstance(graph.get("net/dev/eth0"), Device)
    assert isinstance(graph.get("net/port/eth0"), Port)


def test_appear_without_a_matching_rule_still_emits_the_event() -> None:
    """The event is real whether or not a rule names it (§7): matching is config; the
    event is the machine."""
    graph = MemoryGraph()
    hotplug = Hotplug(graph, Rulebook())
    event = hotplug.appear("eth0", props={"kind": "ethernet"})
    assert event.tags == frozenset()
    assert event.commands == ()
    assert isinstance(graph.get("net/event/link/0"), LinkEvent)


def test_a_match_is_on_the_exact_words_given() -> None:
    """The matcher is a property matcher, not a fuzzy one: `==` and `!=` compare
    exactly (an absent property differs from every value), and any other operator is
    refused by name — a matcher that half-matches is worse than one that says no."""
    assert PropMatch("kind", "==", "ethernet").holds({"kind": "ethernet"})
    assert not PropMatch("kind", "==", "ethernet").holds({"kind": "loopback"})
    assert not PropMatch("kind", "==", "ethernet").holds({})  # absent is not equal
    assert PropMatch("kind", "!=", "ethernet").holds({})
    assert not PropMatch("kind", "!=", "ethernet").holds({"kind": "ethernet"})
    with pytest.raises(NotSupported):
        PropMatch("kind", "~=", "ethernet")  # a glob or a half-match stays refused


def test_a_rule_needs_every_match_and_rules_union_their_words() -> None:
    """udev's shape (§7): a rule's conditions are ANDed, and every matching rule
    contributes its tags and commands — in authored order, nothing else."""
    rules = Rulebook(
        (
            UdevRule((PropMatch("kind", "==", "ethernet"), PropMatch("action", "==", "add")), tags=frozenset({"net-port"})),
            UdevRule((PropMatch("kind", "==", "ethernet"),), tags=frozenset({"hot"}), commands=("if-up",)),
            UdevRule((PropMatch("kind", "==", "ethernet"),), commands=("if-up", "log-event")),
        )
    )
    hit = rules.evaluate({"kind": "ethernet", "action": "add", "name": "eth0"})
    assert hit.tags == frozenset({"net-port", "hot"})
    assert hit.commands == ("if-up", "log-event")  # in order, no repeats
    assert rules.evaluate({"kind": "ethernet", "action": "change"}) == rules.evaluate({"kind": "ethernet", "action": "change"})
    assert "net-port" not in rules.evaluate({"kind": "ethernet", "action": "change"}).tags


def test_the_commands_are_config_words_and_nothing_else() -> None:
    """The rule's tags and commands are *config* (§7): recorded on the event, executed
    by nobody here — the whole delta is the device, its port, and the event."""
    graph = MemoryGraph()
    rules = Rulebook((UdevRule((PropMatch("kind", "==", "ethernet"),), commands=("if-up", "route-apply")),))
    Hotplug(graph, rules).appear("eth0", props={"kind": "ethernet"})
    assert [write.path for write in graph.writes] == ["net/dev/eth0", "net/port/eth0", "net/event/link/0"]
    assert graph.get("net/event/link/0").commands == ("if-up", "route-apply")  # type: ignore[union-attr]


def test_a_second_appear_of_a_live_device_refuses() -> None:
    """An appearance is not idempotent (§7): the path is already taken and the second
    one refuses `AddressInUse` — never a rewrite of the device already standing."""
    graph = MemoryGraph()
    hotplug = Hotplug(graph, Rulebook())
    hotplug.appear("eth0")
    with pytest.raises(AddressInUse):
        hotplug.appear("eth0")
    assert len([write for write in graph.writes if write.path == "net/dev/eth0"]) == 1


def test_change_carries_the_new_words_and_a_second_event() -> None:
    """A change is one new `LinkEvent` cell with the words that changed on the device
    cell — history is appended, never rewritten (docs/24)."""
    graph = MemoryGraph()
    hotplug = Hotplug(graph, Rulebook())
    hotplug.appear("eth0", mtu=1500)
    event = hotplug.change("eth0", link=LinkState.UP, mtu=9000)
    assert event.action is EventAction.CHANGE
    assert event.path == "net/event/link/1"
    device = graph.get("net/dev/eth0")
    assert device.link is LinkState.UP  # type: ignore[union-attr]
    assert device.mtu == 9000  # type: ignore[union-attr]
    assert isinstance(graph.get("net/event/link/0"), LinkEvent)  # the earlier event stays
    assert isinstance(graph.get("net/event/link/1"), LinkEvent)


def test_change_on_a_gone_device_refuses_stalehandle() -> None:
    """§7: a device that vanished is why `StaleHandle` exists — a change never
    conjures one."""
    graph = MemoryGraph()
    with pytest.raises(StaleHandle):
        Hotplug(graph, Rulebook()).change("eth9", link=LinkState.UP)


def test_disappear_closes_the_device_and_the_face_under_it() -> None:
    """A device disappearing is a closure, not a deletion (§7, §14): its port and the
    addresses and routes on its face close — history stays — and the event records
    the departure."""
    graph = MemoryGraph()
    hotplug = Hotplug(graph, Rulebook())
    hotplug.appear("eth0")
    plane = LinkPlane(graph)
    plane.add_address("eth0", b"\xc0\x00\x02\x0a", 24)
    plane.add_route("eth0", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01")
    event = hotplug.disappear("eth0")
    assert event.action is EventAction.REMOVE
    assert event.path == "net/event/link/1"
    assert graph.get("net/dev/eth0") is None
    assert graph.get("net/port/eth0") is None
    assert graph.get("net/if/eth0/addr/0") is None
    assert graph.get("net/route/0") is None
    for path in ("net/dev/eth0", "net/port/eth0", "net/if/eth0/addr/0", "net/route/0"):
        assert graph.history(path), path  # closed and kept, never deleted (docs/24)
    assert isinstance(graph.get("net/event/link/1"), LinkEvent)


def test_disappear_of_an_unknown_device_refuses() -> None:
    """A departure of a device this graph never knew refuses `StaleHandle` (§7): there
    is nothing to close, and the refusal is never a fabricated event."""
    graph = MemoryGraph()
    with pytest.raises(StaleHandle):
        Hotplug(graph, Rulebook()).disappear("eth9")


def test_hotplug_cannot_race_the_control_plane() -> None:
    """An event during an apply lands before or after it, never inside (§8): the apply
    is one commit — one successor of one base — and the event's commit serializes
    behind it. No successor is a half-applied mix of the two."""
    desired = Doc(
        "net/plan/office.yaml",
        devices=(
            DeviceSpec(
                "lan0",
                LinkKind.NIC,
                addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24),),
                routes=(RouteSpec(IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01"),),
            ),
        ),
    )
    holder: list[Hotplug] = []
    fired: list[str] = []

    def during(write: Write) -> None:
        # a device event lands in the middle of the apply's writes
        if not fired:
            fired.append(write.path)
            holder[0].appear("eth0", props={"kind": "ethernet"})

    graph = MemoryGraph(on_write=during)
    holder.append(Hotplug(graph, Rulebook((UdevRule((PropMatch("kind", "==", "ethernet"),),),))))
    apply(plan(desired, graph), desired, graph)

    paths = [write.path for write in graph.writes]
    # the apply landed whole — its four cells contiguous — and the event's delta sits
    # entirely after them: never inside the apply
    assert paths[:4] == ["net/dev/lan0", "net/port/lan0", "net/if/lan0/addr/0", "net/route/0"]
    assert paths[4:] == ["net/dev/eth0", "net/port/eth0", "net/event/link/0"]
    assert isinstance(graph.get("net/dev/lan0"), Device)  # both truths stand
    assert isinstance(graph.get("net/dev/eth0"), Device)
