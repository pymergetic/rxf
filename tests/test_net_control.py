"""Pins for the declarative control plane (docs/25 §8): authored docs, plans, drift.

What is pinned here: the plan is one delta set and it is idempotent — re-planning
against the applied state yields an empty plan; `apply` refuses on a stale base (the
base word of the plan, never a silent overwrite); drift is exactly field-wise over
the device graph, `(path, expected, actual)` triples, and re-syncing clears it to
empty; `render` emits one canonical netplan shape that `parse` reads back whole; the
`include` semantics are document composition in the object; and a partial or foreign
shape is refused by name — never accepted and dropped.
"""

from __future__ import annotations

import pytest

from pymergetic.rxftk.net.control.netplan import (
    AddressSpec,
    DeviceSpec,
    Doc,
    Drift,
    DriftEntry,
    InvalidDocument,
    MalformedDocument,
    RouteSpec,
    StaleBase,
    apply,
    check,
    compile_desired,
    drift,
    drift_entries,
    parse,
    plan,
    render,
)
from pymergetic.rxftk.net.link.link import LinkKind, LinkPlane, MemoryGraph
from pymergetic.rxftk.net.schema import IPPrefix, LinkState, NotSupported, Plan


def _rich() -> Doc:
    """One authored doc exercising every section of the netplan shape (§8)."""
    return Doc(
        "net/plan/office.yaml",
        devices=(
            DeviceSpec(
                "eth0",
                LinkKind.NIC,
                mac=b"\x02\x00\x00\x00\x00\x01",
                mtu=9000,
                addresses=(
                    AddressSpec(b"\xc0\x00\x02\x01", 24),
                    AddressSpec(b"\x20\x01\x0d\xb8" + b"\x00" * 10 + b"\x00\x0a", 64),
                ),
                routes=(
                    RouteSpec(IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\xfe"),
                    RouteSpec(IPPrefix(b"\x20\x01\x0d\xb8\x00\x01" + b"\x00" * 10, 48), via=b"\x20\x01\x0d\xb8\x00\x01" + b"\x00" * 8 + b"\x00\xfe"),
                ),
            ),
            DeviceSpec("lan0", LinkKind.NIC),
            DeviceSpec("lan1", LinkKind.NIC),
            DeviceSpec("wan0", LinkKind.VETH, peer="wan1"),
            DeviceSpec("wan1", LinkKind.VETH),
            DeviceSpec("br0", LinkKind.BRIDGE, members=("lan0", "lan1")),
            DeviceSpec("bond0", LinkKind.BOND, members=("eth0",)),
            DeviceSpec("lan0.10", LinkKind.VLAN, vid=10, on="lan0"),
            DeviceSpec("dum0", LinkKind.DUMMY),
        ),
    )


GOLDEN = """\
network:
  version: 2
  ethernets:
    eth0:
      macaddress: 02:00:00:00:00:01
      mtu: 9000
      addresses:
        - 192.0.2.1/24
        - 2001:db8::a/64
      routes:
        - to: default
          via: 192.0.2.254
          metric: 100
        - to: 2001:db8:1::/48
          via: 2001:db8:1::fe
          metric: 100
    lan0:
    lan1:
  veths:
    wan0:
      peer: wan1
    wan1:
  bridges:
    br0:
      interfaces:
        - lan0
        - lan1
  bonds:
    bond0:
      interfaces:
        - eth0
  vlans:
    lan0.10:
      id: 10
      link: lan0
  dummies:
    dum0:
"""


def test_render_is_canonical_and_round_trips() -> None:
    """`render` emits one canonical shape (§8): deterministic order, both address
    families in one list, RFC 5952 literals — and `parse` reads the text back to the
    same doc, whole."""
    assert render(_rich()) == GOLDEN
    assert parse(GOLDEN, path="net/plan/office.yaml").composed() == _rich().composed()
    assert render(parse(GOLDEN, path="net/plan/office.yaml")) == GOLDEN  # canonical both ways


def test_include_is_document_composition() -> None:
    """The historical `include` semantics (§8) are document composition: the includes
    merge in order, this doc's own devices win per name — a device is authored whole,
    never field-merged behind the author's back — and `include=False` renders the
    doc's own block alone."""
    base = Doc(
        "net/plan/base.yaml",
        devices=(
            DeviceSpec("lan0", LinkKind.NIC, mtu=1400, addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24),)),
            DeviceSpec("lan1", LinkKind.NIC, addresses=(AddressSpec(b"\xc0\x00\x02\x0b", 24),)),
        ),
    )
    outer = Doc(
        "net/plan/office.yaml",
        devices=(DeviceSpec("lan1", LinkKind.NIC, mtu=9000, addresses=(AddressSpec(b"\xc0\x00\x02\x0c", 24),)),),
        includes=(base,),
    )
    composed = outer.composed()
    assert [spec.name for spec in composed.devices] == ["lan0", "lan1"]
    assert composed.devices[0] == base.devices[0]  # the include's device, whole
    assert composed.devices[1] == outer.devices[0]  # the outer doc wins per name, whole
    assert "lan0:" in render(outer, include=True)
    assert "lan0:" not in render(outer, include=False)  # the doc's own block alone
    assert "mtu: 1400" in render(outer, include=True)  # the include's lan0, as authored
    assert "mtu: 9000" in render(outer, include=True)  # the outer lan1, as authored
    assert "192.0.2.11/24" not in render(outer, include=True)  # a device is authored whole: no field-merge


def test_the_plan_is_one_delta_set_and_it_is_idempotent() -> None:
    """`plan(desired, current)` is the delta set (§8) — and re-planning against the
    applied state yields an empty plan: the doc is the whole truth, applied once."""
    desired = _rich()
    graph = MemoryGraph()
    staged = plan(desired, graph)
    assert staged == Plan(desired.path, graph.digest(), staged.creates, (), ())
    assert staged.creates and not staged.sets and not staged.deletes
    apply(staged, desired, graph)
    again = plan(desired, graph)
    assert again.creates == () and again.sets == () and again.deletes == ()
    assert again == Plan(desired.path, graph.digest(), (), (), ())
    assert drift(desired, graph) == Drift(desired.path, (), (), ())  # and no field stands apart


def test_the_plan_names_what_the_doc_does_not() -> None:
    """The doc is total desired state over the reconciled kinds (§8): what it does not
    name is staged as a delete — an interval closure, never a deletion of history."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("ghost0")
    plane.create_nic("lan0")
    desired = Doc("net/plan/office.yaml", devices=(DeviceSpec("lan0", LinkKind.NIC, mtu=1500),))
    staged = plan(desired, graph)
    assert staged.deletes == ("net/dev/ghost0", "net/port/ghost0")
    apply(staged, desired, graph)
    assert graph.get("net/dev/ghost0") is None
    assert graph.history("net/dev/ghost0")  # closed and kept (docs/24: nothing is deleted)
    assert plan(desired, graph).deletes == ()


def test_apply_refuses_on_a_stale_base() -> None:
    """No silent overwrite (§8): if the current state moved since the plan was
    computed, apply refuses `StaleBase` — the same spirit as the live-apply exclusion
    — and writes nothing of the plan."""
    desired = Doc("net/plan/office.yaml", devices=(DeviceSpec("lan0", LinkKind.NIC, addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24),)),))
    graph = MemoryGraph()
    staged = plan(desired, graph)
    plane = LinkPlane(graph)
    plane.create_nic("moved0")  # the state moved under the plan
    before = len(graph.writes)
    with pytest.raises(StaleBase):
        apply(staged, desired, graph)
    assert len(graph.writes) == before  # partial application is refused (§8): nothing landed
    assert graph.get("net/dev/lan0") is None
    # the plan against the moved state applies cleanly — the refusal is about the base
    apply(plan(desired, graph), desired, graph)
    assert graph.get("net/dev/lan0") is not None


def test_drift_is_exactly_field_wise_over_the_device_graph() -> None:
    """Drift (§8) is `(path, expected, actual)` triples — one per differing field,
    whole cells for what is missing, and nothing for the words that agree."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.set_state("lan0", LinkState.UP)  # §8's bring-up matches: no drift on link
    plane.set_mtu("lan0", 1500)
    plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    plane.add_route("lan0", IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\xfe")
    desired = Doc(
        "net/plan/office.yaml",
        devices=(
            DeviceSpec(
                "lan0",
                LinkKind.NIC,
                mtu=9000,
                addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24), AddressSpec(b"\xc0\x00\x02\x0b", 24)),
                routes=(RouteSpec(IPPrefix(b"\x00" * 4, 0), via=b"\xc0\x00\x02\x01"),),
            ),
        ),
    )
    entries = drift_entries(desired, graph)
    assert entries == (
        DriftEntry("net/dev/lan0.mtu", 9000, 1500),
        DriftEntry("net/if/lan0/addr/1", compile_desired(desired)[3][1], None),
        DriftEntry("net/route/0.via", b"\xc0\x00\x02\x01", b"\xc0\x00\x02\xfe"),
    )  # exactly: one triple per differing field, a whole cell for what is missing
    assert drift(desired, graph) == Drift(
        desired.path,
        ("net/if/lan0/addr/1",),
        ("net/dev/lan0.mtu", "net/route/0.via"),
        (),
        entries,
    )  # and the report carries the same triples in `Drift.entries`


def test_drift_names_extra_words_too() -> None:
    """What the graph holds and the doc does not name is an extra — one triple with
    `None` on the expected side (§8): field-wise, exactly."""
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    plane.set_state("lan0", LinkState.UP)
    plane.add_address("lan0", b"\xc0\x00\x02\x0a", 24)
    plane.add_address("lan0", b"\xc0\x00\x02\x63", 24)  # the doc never names this one
    desired = Doc("net/plan/office.yaml", devices=(DeviceSpec("lan0", LinkKind.NIC, addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24),)),))
    extra = DriftEntry("net/if/lan0/addr/1", None, graph.get("net/if/lan0/addr/1"))
    assert drift(desired, graph) == Drift(desired.path, (), (), ("net/if/lan0/addr/1",), (extra,))
    assert extra in drift_entries(desired, graph)


def test_resyncing_clears_drift_to_empty() -> None:
    """The fix side of the pair (§8): once the graph carries the doc — the next apply —
    the report is empty, and provably so."""
    desired = Doc(
        "net/plan/office.yaml",
        devices=(DeviceSpec("lan0", LinkKind.NIC, mtu=9000, addresses=(AddressSpec(b"\xc0\x00\x02\x0a", 24),)),),
    )
    graph = MemoryGraph()
    plane = LinkPlane(graph)
    plane.create_nic("lan0")
    assert drift_entries(desired, graph)  # there is drift to see
    apply(plan(desired, graph), desired, graph)
    assert drift(desired, graph) == Drift(desired.path, (), (), ())
    assert drift_entries(desired, graph) == ()


def test_a_document_that_names_no_coherent_state_is_refused() -> None:
    """Typed refusals, nothing applied (§8): duplicate names, a veth pair that is not
    whole, a vlan with no honest id or no carrying link, a member of two masters —
    each refused by name."""
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("lan0", LinkKind.NIC), DeviceSpec("lan0", LinkKind.NIC))))
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("wan0", LinkKind.VETH),)))  # a pair is authored whole
    with pytest.raises(InvalidDocument):
        check(
            Doc(
                "net/plan/x.yaml",
                devices=(DeviceSpec("wan0", LinkKind.VETH, peer="wan1"), DeviceSpec("wan1", LinkKind.VETH, peer="wan0")),
            )
        )  # claimed from both ends
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("lan0.10", LinkKind.VLAN, vid=4095, on="lan0"), DeviceSpec("lan0", LinkKind.NIC))))
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("lan0.10", LinkKind.VLAN, vid=10, on="eth9"),)))
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("br0", LinkKind.BRIDGE, members=("lan9",)),)))
    with pytest.raises(InvalidDocument):
        check(
            Doc(
                "net/plan/x.yaml",
                devices=(
                    DeviceSpec("lan0", LinkKind.NIC),
                    DeviceSpec("br0", LinkKind.BRIDGE, members=("lan0",)),
                    DeviceSpec("bond0", LinkKind.BOND, members=("lan0",)),
                ),
            )
        )  # one master only
    with pytest.raises(InvalidDocument):
        check(
            Doc(
                "net/plan/x.yaml",
                devices=(
                    DeviceSpec(
                        "lan0",
                        LinkKind.NIC,
                        routes=(RouteSpec(IPPrefix(b"\x00" * 4, 0), via=b"\x20\x01\x0d\xb8" + b"\x00" * 12 + b"\x01"),),
                    ),
                ),
            )
        )  # a gateway answers the family of its prefix
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("lan0", LinkKind.NIC, addresses=(AddressSpec(b"\xc0\x00\x02\x01", 33),)),)))
    with pytest.raises(InvalidDocument):
        check(Doc("net/plan/x.yaml", devices=(DeviceSpec("bad name", LinkKind.NIC),)))


def test_a_foreign_or_partial_shape_is_refused_not_guessed() -> None:
    """What the pinned subset does not cover is refused by name (§8): the `include`
    file key composes in the object instead, unknown keys and other versions stay
    out, and text that does not parse is refused `MalformedDocument` — nothing is
    accepted and dropped."""
    with pytest.raises(NotSupported):
        parse("network:\n  version: 2\n  include: extra.yaml\n")
    with pytest.raises(NotSupported):
        parse("network:\n  version: 2\n  tunnels:\n    tun0:\n")
    with pytest.raises(NotSupported):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      unknown-key: 1\n")
    with pytest.raises(NotSupported):
        parse("network:\n  version: 3\n")
    with pytest.raises(NotSupported):
        parse("---\nnetwork:\n  version: 2\n")  # one document per parse
    with pytest.raises(NotSupported):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      addresses: &anchor []\n")
    with pytest.raises(MalformedDocument):
        parse("")
    with pytest.raises(MalformedDocument):
        parse("network:\n\tversion: 2\n")  # indentation is spaces, never tabs
    with pytest.raises(MalformedDocument):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      macaddress: 02:00:00\n")
    with pytest.raises(MalformedDocument):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      routes:\n        - via: 192.0.2.254\n")  # a route names its destination
    with pytest.raises(InvalidDocument):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      routes:\n        - to: default\n")  # and its family through its gateway
    with pytest.raises(MalformedDocument):
        parse("network:\n  version: 2\n  ethernets:\n    eth0:\n      mtu: not-a-number\n")


def test_the_writes_are_durable_state_only() -> None:
    """The plan writes devices, ports, segments, vlans, addresses and routes (§14) —
    durable state, and nothing transient in it."""
    durable = {"net/dev/", "net/port/", "net/seg/", "net/vlan/", "net/if/", "net/route/"}
    for path, _ in compile_desired(_rich()):
        assert any(path.startswith(prefix) for prefix in durable), path
