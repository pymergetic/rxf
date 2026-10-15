"""The link and hardware plane (docs/25 §7) and its device seam.

`net.link.link` is the plane itself — `ip link` / `ip addr` / `ip route` as durable
cell writes over the graph, the udev-shaped device matcher, and link events as cells.
`net.link.device` is the `cap.net.driver` seam — the ethtool-shaped honest read and the
honest knob set — modeled as a provider interface exactly like `net.transport.stream`
models streams: the interface is the truth, a provider is a fill, and with no fill
installed the seam refuses `NotSupported` by name.

One contract, one honest in-memory fill each (`MemoryGraph`, `MemoryDriver`): the
fills are for tests and seats with nothing under them yet, never a second face.
"""

from .device import (
    DeviceDriver,
    Knob,
    MemoryDriver,
    hardware,
    install_driver,
    set_knob,
)
from .link import (
    DURABLE_KINDS,
    EventAction,
    Graph,
    Hotplug,
    LinkEvent,
    LinkKind,
    LinkPlane,
    MemoryGraph,
    NetCell,
    PropMatch,
    Rulebook,
    RuleHit,
    UdevRule,
    Write,
    address_path,
    check_durable,
    device_path,
    event_path,
    iface_path,
    port_path,
    route_path,
    schema_kind,
    segment_path,
    vlan_path,
)

__all__ = [
    "DURABLE_KINDS",
    "DeviceDriver",
    "EventAction",
    "Graph",
    "Hotplug",
    "Knob",
    "LinkEvent",
    "LinkKind",
    "LinkPlane",
    "MemoryDriver",
    "MemoryGraph",
    "NetCell",
    "PropMatch",
    "RuleHit",
    "Rulebook",
    "UdevRule",
    "Write",
    "address_path",
    "check_durable",
    "device_path",
    "event_path",
    "hardware",
    "iface_path",
    "install_driver",
    "port_path",
    "route_path",
    "schema_kind",
    "segment_path",
    "set_knob",
    "vlan_path",
]
