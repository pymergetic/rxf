"""The device hardware seam (docs/25 §7): `cap.net.driver` as a provider interface.

What `ethtool` reads today is a device's honest capability picture — link state, speed,
duplex, offloads, ring sizes, WOL. That read comes through this seam, modeled exactly
like `net.transport.stream` models streams: **the interface is the truth, a provider is
a fill**. No real netlink, no ioctl, no sockets live in the library; a seat's fill (or
the one in-memory fill shipped here) implements `DeviceDriver`. With no provider
installed the seam refuses `NotSupported` by name — the same pattern as `wrap()` (§11):
a hardware question is never answered by a silent empty page.

Hardware knobs are honest capabilities (§7): the fill declares what the device can do,
and setting a knob it cannot refuses `NotSupported` by name — never a silent no-op that
reports success. This module is the only place a kernel call would be; everything else
in the link plane is a durable cell write over the graph.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import replace
from enum import Enum

from ..schema import Hardware, LinkState, NotSupported, StaleHandle


class Knob(Enum):
    """What `ip link` and `ethtool` set on a device (§7) — the knobs a fill may refuse.

    `speed` and `duplex` are read words (carrier facts), not knobs; autoneg and
    interrupt coalescing have no `Hardware` field to land in yet (schema seam request
    in the report) and stay out of this set rather than pretending.
    """

    MTU = "mtu"
    MAC = "mac"
    LINK = "link"
    OFFLOADS = "offloads"
    RINGS = "rings"
    WOL = "wol"


class DeviceDriver(ABC):
    """The `cap.net.driver` provider contract (§2, §7): the ethtool-shaped read and the
    honest knob set.

    A seat fills this (tap, the in-image stack's link, a hardware park, …). The link
    plane never reaches past it, and the body never sees a host error code — providers
    map their errors to the §15 refusal set.
    """

    @abstractmethod
    def hardware(self, path: str) -> Hardware:
        """The device's honest capability picture at `path` (§7).

        A device that is gone refuses `StaleHandle` — §7: a port that vanishes is why
        `StaleHandle` exists, never a silent empty picture.
        """

    @abstractmethod
    def set_knob(self, path: str, knob: Knob, value: object) -> None:
        """Apply one knob. A knob this device cannot do refuses `NotSupported` by name
        (§7) — never a silent no-op that reports success."""


_driver: DeviceDriver | None = None


def install_driver(driver: DeviceDriver | None) -> None:
    """Install a seat's fill of the device seam (§7), or remove it with None.

    Until a seat installs one, `hardware` and `set_knob` refuse `NotSupported` by name
    — a hardware question is never answered by a silent empty page.
    """
    global _driver
    _driver = driver


def hardware(path: str) -> Hardware:
    """The device's honest capability picture — through the installed fill, or refused.

    The ethtool-shaped read of §7. With no provider installed this refuses
    `NotSupported` (the `wrap()` pattern of §11): the seam is the only place a device
    call would be, and it never pretends to have made one.
    """
    if _driver is None:
        raise NotSupported("no driver fill is installed: the hardware read is a device call and this seam refuses by name")
    return _driver.hardware(path)


def set_knob(path: str, knob: Knob, value: object) -> None:
    """Apply one hardware knob through the installed fill (§7).

    With no provider installed this refuses `NotSupported`; with one, a knob the fill
    cannot do refuses `NotSupported` there. Either way a knob never silently succeeds.
    """
    if _driver is None:
        raise NotSupported("no driver fill is installed: a knob set is a device call and this seam refuses by name")
    _driver.set_knob(path, knob, value)


class MemoryDriver(DeviceDriver):
    """The one honest in-memory fill of the device seam — for tests, and for seats with
    no device of their own yet.

    What a device can do is declared by its record, not pretended: `mac=None` is a
    point-to-point line with no address to set, `rings=None` a single-queue seat that
    says so instead of faking rings (§7). Reads answer from the table; knob sets are
    checked against the declared capability and recorded in `applied`. The only kernel
    behind this fill is a dictionary.

    `Hardware.offloads` is the fill's declared honest set (§3), so the `OFFLOADS`
    knob takes any subset of it and lands in `Hardware.offloads_enabled` — the
    enabled mask beside the declared set, which stands still — and `applied`
    records the same intent.
    """

    def __init__(self, devices: Mapping[str, Hardware] | None = None) -> None:
        self._devices: dict[str, Hardware] = dict(devices or {})
        self.applied: list[tuple[str, Knob, object]] = []

    def appear(self, path: str, device: Hardware) -> None:
        """A device arrives (the test's wire): it enters the table as it is."""
        self._devices[path] = device

    def remove(self, path: str) -> None:
        """The device is gone (the test's wire): reads refuse `StaleHandle` from here."""
        self._devices.pop(path, None)

    def hardware(self, path: str) -> Hardware:
        try:
            return self._devices[path]
        except KeyError:
            raise StaleHandle(f"the device is gone: no hardware picture for {path}") from None

    def set_knob(self, path: str, knob: Knob, value: object) -> None:
        record = self.hardware(path)  # a gone device refuses StaleHandle first
        capable = {
            Knob.MTU: True,
            Knob.MAC: record.mac is not None,
            Knob.LINK: True,
            Knob.OFFLOADS: True,
            Knob.RINGS: record.rings is not None,
            Knob.WOL: record.wol is not None,
        }[knob]
        if not capable:
            raise NotSupported(f"this device cannot do the {knob.value} knob: its honest picture says so")
        updated = self._apply(record, knob, value)
        self._devices[path] = updated
        self.applied.append((path, knob, value))

    @staticmethod
    def _apply(record: Hardware, knob: Knob, value: object) -> Hardware:
        """The knob's effect on the record — refused by name when the value is not one
        this knob takes (a wrong shape is not a silent success either)."""
        if knob is Knob.MTU:
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return replace(record, mtu=value)
            raise NotSupported("the mtu knob takes a positive word: this value is not one")
        if knob is Knob.MAC:
            if isinstance(value, bytes) and len(value) == 6:
                return replace(record, mac=value)
            raise NotSupported("the mac knob takes a 6-byte address: this value is not one")
        if knob is Knob.LINK:
            if isinstance(value, LinkState):
                return replace(record, link=value)
            raise NotSupported("the link knob takes a link state: this value is not one")
        if knob is Knob.OFFLOADS:
            if isinstance(value, frozenset) and all(isinstance(name, str) for name in value):
                if value <= record.offloads:
                    return replace(record, offloads_enabled=value)  # the enabled mask, honest now
                raise NotSupported("this fill declares its offloads honestly: one named here is not among them")
            raise NotSupported("the offloads knob takes a name set: this value is not one")
        if knob is Knob.RINGS:
            if (
                isinstance(value, tuple)
                and len(value) == 2
                and all(isinstance(size, int) and not isinstance(size, bool) and size > 0 for size in value)
            ):
                return replace(record, rings=value)
            raise NotSupported("the rings knob takes a (rx, tx) pair of positive words: this value is not one")
        if isinstance(value, bool):
            return replace(record, wol=value)
        raise NotSupported("the wol knob takes a yes or no: this value is not one")
