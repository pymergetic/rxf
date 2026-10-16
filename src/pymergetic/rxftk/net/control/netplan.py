"""The declarative control plane (docs/25 §8): netplan is desired state, authored.

A declarative document — a `Doc`, the §8 semantic block — compiles against the base
into a staged plan: `check` (typed refusals, nothing applied) → `plan` → `apply` as
**one successor** for the whole bring-up (link up, addresses, routes, in the plan's
compile order — partial application is refused). Rollback is the delta chain: supersede
with the previous plan, never a rewrite.

`drift` is a view plus an explicit repair: desired versus current, field-wise over the
device graph as `(path, expected, actual)` triples — it changes nothing, and re-syncing
(the next apply) clears it to empty. `apply` refuses a stale base by name: if the
current state moved since the plan was computed, nothing is overwritten (the same
spirit as the live lane's base-digest refusal).

`render` emits the netplan-shape YAML and `parse` reads that shape back to the `Doc`.
The shape is the interface and the code is ours (the §10 rule): this is a pinned subset
of the shape — what it supports it round-trips exactly, and what it does not is refused
by name (`NotSupported`), never accepted and dropped. No YAML library: a tiny subset
parser and emitter live here (stdlib-free by construction).

Composition (the historical `include` semantics) is document composition: a `Doc` may
carry other `Doc`s in `includes`, and `render(..., include=True)` — the default — emits
the composed document (a device is authored whole; the outer doc wins per name).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..link.link import (
    Graph,
    LinkKind,
    NetCell,
    Write,
    address_path,
    device_path,
    iface_path,
    port_path,
    route_path,
    schema_kind,
    segment_path,
    vlan_path,
)
from ..schema import (
    IP,
    Address,
    Device,
    Drift,
    DriftEntry,
    Interval,
    IPPrefix,
    LinkState,
    Mac,
    MalformedMessage,
    NotSupported,
    Plan,
    Port,
    Refusal,
    Route,
    RouteOrigin,
    Segment,
    Vlan,
)

# ---- typed refusals (the §15 set's pattern, on top like the §12 family) -------


class StaleBase(Refusal):
    """The plan's base moved under it: the base digest is not the digest of the
    current graph. Apply refuses by name — no silent overwrite (the same spirit as the
    live lane's base-digest refusal)."""


class MalformedDocument(MalformedMessage):
    """The document text does not parse into the pinned YAML subset (§8) — a refusal,
    never a wrong parse half-applied."""


class InvalidDocument(Refusal):
    """The document parses but does not describe one coherent desired state (§8) —
    refused at `check`, before anything is staged."""


# ---- the desired state: the §8 semantic block -------------------------------


@dataclass(frozen=True)
class AddressSpec:
    """One desired address: the `addresses:` entry of a device block (§5)."""

    ip: IP
    prefix: int


@dataclass(frozen=True)
class RouteSpec:
    """One desired route: the `routes:` entry of a device block (§5, §9).

    `to` is the destination prefix; `via` is the gateway or None for an on-link
    route. A `to: default` spelling resolves its family through the gateway at parse
    (a default with no gateway has no family to choose — refused by name there).
    """

    to: IPPrefix
    via: IP | None
    metric: int = 100


@dataclass(frozen=True)
class DeviceSpec:
    """One desired link: the §7 kind set, its knobs, and its addresses and routes.

    `members` are the bridge/bond members, `peer` the other end of a veth pair,
    `vid`/`on` the VLAN's tag and carrying link. `mtu` and `mac` are optional in the
    shape (the compile defaults mtu to 1500 and leaves mac unset).
    """

    name: str
    kind: LinkKind
    mac: Mac | None = None
    mtu: int | None = None
    members: tuple[str, ...] = ()
    peer: str | None = None
    vid: int | None = None
    on: str | None = None
    addresses: tuple[AddressSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()


@dataclass(frozen=True)
class Doc:
    """The authored desired state (§8's semantic block), one document.

    This is the netplan document's shape today; when the docs/22 `Doc` lane exports
    its Python face this type gains that identity (provenance and authorship ride the
    delta chain either way — schema seam request in the report). `includes` is
    document composition: the historical `include` semantics, composed in the object.
    """

    path: str  # net/plan/office.yaml — its authored name (docs/22's Doc, later)
    devices: tuple[DeviceSpec, ...] = ()
    includes: tuple[Doc, ...] = ()

    def composed(self) -> Doc:
        """The document composition (include=True semantics): the includes merge in
        order and this doc's own devices win per name — a device is authored whole,
        never field-merged behind the author's back. One name keeps its last value,
        and the composed document stands in the shape's own section order, so
        `parse(render(d))` is `d.composed()` for every document."""
        merged: dict[str, DeviceSpec] = {}
        for part in self.includes:
            for spec in part.composed().devices:
                merged[spec.name] = spec
        for spec in self.devices:
            merged[spec.name] = spec
        ordered = tuple(spec for _, kind in _SECTIONS for spec in merged.values() if spec.kind is kind)
        return Doc(self.path, ordered, ())


# ---- field-wise drift: (path, expected, actual) triples ----------------------
# The triple is the frozen set's `DriftEntry` word (§3) — the view rides schema
# words end to end, from `drift_entries` to `Drift.entries`.


_RECONCILED: tuple[type, ...] = (Device, Port, Segment, Vlan, Address, Route)


@dataclass(frozen=True)
class _Diff:
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    extra: tuple[str, ...]
    entries: tuple[DriftEntry, ...]


def _config(cell: NetCell) -> dict[str, object]:
    """The config words of a cell — exactly the words the desired state authors.

    `valid` is interval bookkeeping (docs/24: *when* a cell held), never a config
    word: diffs compare what is authored, so re-syncing clears to empty.
    """
    if isinstance(cell, Device):
        return {"kind": cell.kind, "mac": cell.mac, "mtu": cell.mtu, "link": cell.link}
    if isinstance(cell, Port):
        return {"device": cell.device, "segment": cell.segment}
    if isinstance(cell, Segment):
        return {"ports": cell.ports}
    if isinstance(cell, Vlan):
        return {"vid": cell.vid, "on": cell.on}
    if isinstance(cell, Address):
        return {"iface": cell.iface, "ip": cell.ip, "prefix": cell.prefix}
    if isinstance(cell, Route):
        return {
            "prefix": cell.prefix,
            "via": cell.via,
            "iface": cell.iface,
            "metric": cell.metric,
            "origin": cell.origin,
        }
    raise NotSupported(f"a {type(cell).__name__} carries no config words this view diffs")


# ---- compile: the doc to cells, in bring-up order ---------------------------


def compile_desired(desired: Doc, at: int = 0) -> tuple[tuple[str, NetCell], ...]:
    """The cells the doc wants, in bring-up order (§8: link up, addresses, routes —
    the plan's compile order, and nothing reorders behind the image's back).

    `at` stamps the intervals of the addresses it authors (docs/24: the write stamps
    when it was made); no diff ever looks at intervals.
    """
    check(desired)
    doc = desired.composed()
    cells: list[tuple[str, NetCell]] = []
    members_of = {
        member: spec.name
        for spec in doc.devices
        if spec.kind in _MASTER_KINDS
        for member in spec.members
    }
    for spec in doc.devices:
        link = Device(
            device_path(spec.name),
            schema_kind(spec.kind),
            spec.mac,
            spec.mtu if spec.mtu is not None else 1500,
            LinkState.UP,  # §8's bring-up: a named link comes up in the plan
        )
        cells.append((device_path(spec.name), link))
        segment = segment_path(members_of[spec.name]) if spec.name in members_of else None
        cells.append((port_path(spec.name), Port(port_path(spec.name), link.path, segment)))
        if spec.kind is LinkKind.VLAN:
            cells.append(
                (vlan_path(spec.name), Vlan(vlan_path(spec.name), _vid(spec), device_path(_on(spec))))
            )
    for spec in doc.devices:
        if spec.kind in _MASTER_KINDS and spec.members:
            ports = tuple(port_path(member) for member in spec.members)
            cells.append((segment_path(spec.name), Segment(segment_path(spec.name), ports)))
    for spec in doc.devices:
        for index, address in enumerate(spec.addresses):
            cells.append(
                (
                    address_path(spec.name, index),
                    Address(
                        address_path(spec.name, index),
                        iface_path(spec.name),
                        address.ip,
                        address.prefix,
                        Interval(at, None),
                    ),
                )
            )
    index = 0
    for spec in doc.devices:
        for route in spec.routes:
            cell = Route(route_path(index), route.to, route.via, iface_path(spec.name), route.metric, RouteOrigin.STATIC)
            cells.append((route_path(index), cell))
            index += 1
    return tuple(cells)


# ---- check: typed refusals, nothing applied ---------------------------------


def check(desired: Doc) -> None:
    """Typed refusals, nothing applied (§8): the document must name one coherent
    desired state before anything is staged."""
    _check_unique(desired)
    _check_doc(desired.composed())


def _check_unique(doc: Doc) -> None:
    """One document names each link once; across documents composition is the point
    (the outer doc wins per name) and that is not a duplication."""
    seen: set[str] = set()
    for spec in doc.devices:
        if spec.name in seen:
            raise InvalidDocument(f"the document names {spec.name!r} twice: one name, one link")
        seen.add(spec.name)
    for part in doc.includes:
        _check_unique(part)


def _check_doc(doc: Doc) -> None:
    names = {spec.name for spec in doc.devices}
    member_of: dict[str, str] = {}
    claims: list[tuple[str, str]] = []
    for spec in doc.devices:
        _check_name(spec.name)
        if spec.mtu is not None and _not_word(spec.mtu, 1):
            raise InvalidDocument(f"the link {spec.name!r} carries no honest mtu: an mtu is a positive word")
        if spec.kind is LinkKind.VLAN:
            _vid(spec)
            if _on(spec) not in names:
                raise InvalidDocument(f"the vlan {spec.name!r} hangs on {_on(spec)!r}: no such link in the document")
        if spec.kind is LinkKind.VETH and spec.peer is not None:
            if spec.peer not in names:
                raise InvalidDocument(f"the veth {spec.name!r} peers with {spec.peer!r}: no such link in the document")
            other = next(part for part in doc.devices if part.name == spec.peer)
            if other.kind is not LinkKind.VETH:
                raise InvalidDocument(f"the veth {spec.name!r} peers with {spec.peer!r}, which is no veth")
            claims.append((spec.name, spec.peer))
        if spec.kind in _MASTER_KINDS:
            for member in spec.members:
                if member not in names:
                    raise InvalidDocument(f"the {spec.kind.value} {spec.name!r} members {member!r}: no such link in the document")
                if member in member_of:
                    raise InvalidDocument(f"{member!r} is a member of {member_of[member]!r} and {spec.name!r}: one master only")
                member_of[member] = spec.name
        for address in spec.addresses:
            _check_prefix(address.ip, address.prefix)
        for route in spec.routes:
            if _not_word(route.metric, 0):
                raise InvalidDocument("a metric is a word of digits: this one is not")
            if route.via is not None and len(route.via) != len(route.to.network):
                raise InvalidDocument("a gateway answers the family of its prefix: this route mixes two")
    _check_pairs(doc, claims)


def _check_pairs(doc: Doc, claims: list[tuple[str, str]]) -> None:
    """A veth pair is authored whole (§7): each veth stands in exactly one pair, and
    the pair is named from exactly one end."""
    for source, target in claims:
        if (target, source) in claims:
            raise InvalidDocument(f"the veth pair {source!r}/{target!r} is claimed from both ends: exactly one end names the peer")
    for spec in doc.devices:
        if spec.kind is not LinkKind.VETH:
            continue
        standing = sum(1 for source, target in claims if spec.name in (source, target))
        if standing == 0:
            raise InvalidDocument(f"the veth {spec.name!r} stands in no pair: a veth pair is authored whole")
        if standing > 1:
            raise InvalidDocument(f"the veth {spec.name!r} stands in two pairs: one pair per end")


def _vid(spec: DeviceSpec) -> int:
    if not isinstance(spec.vid, int) or _not_word(spec.vid, 1) or spec.vid > 4094:
        raise InvalidDocument(f"the vlan {spec.name!r} carries no honest id: a vid is a word from 1 to 4094")
    return spec.vid


def _on(spec: DeviceSpec) -> str:
    if not isinstance(spec.on, str) or not spec.on:
        raise InvalidDocument(f"the vlan {spec.name!r} names no carrying link")
    return spec.on


def _check_name(name: str) -> None:
    if not name or "/" in name or any(char.isspace() for char in name):
        raise InvalidDocument(f"a link name is one path word, no slash, no space: {name!r} is not one")


def _check_prefix(ip: IP, prefix: int) -> None:
    width = 32 if len(ip) == 4 else 128 if len(ip) == 16 else 0
    if width == 0:
        raise InvalidDocument("an address is 4 or 16 bytes: this one is neither")
    if _not_word(prefix, 0) or prefix > width:
        raise InvalidDocument(f"a prefix is a word from 0 to {width} for this family: {prefix!r} is not one")


def _not_word(value: object, low: int) -> bool:
    return not isinstance(value, int) or isinstance(value, bool) or value < low


# ---- plan, drift, apply ------------------------------------------------------


def plan(desired: Doc, current: Graph) -> Plan:
    """The delta set the doc stages against the current base (§8): creates, sets, and
    deletes — each in the plan's compile order, the deletes as interval closures.

    Idempotent by construction: against the state an apply leaves behind, the plan is
    empty. The doc is total desired state over the reconciled kinds (devices, ports,
    segments, vlans, addresses, routes) — what it does not name is closed on apply.
    Link events and everything outside those kinds are out of scope.
    """
    ordered = compile_desired(desired)
    wanted = dict(ordered)
    view = _reconciled(current)
    creates = tuple(path for path, _ in ordered if path not in view)
    sets = tuple(path for path, cell in ordered if path in view and _config(cell) != _config(view[path]))
    deletes = tuple(path for path, _ in sorted(view.items()) if path not in wanted)
    return Plan(desired.path, current.digest(), creates, sets, deletes)


def drift_entries(desired: Doc, current: Graph) -> tuple[DriftEntry, ...]:
    """The field-wise view (§8): the schema's `(path, expected, actual)` triples over
    the device graph — missing and extra cells whole, changed cells one triple per
    differing field. Changes nothing."""
    return _diff(desired, current).entries


def drift(desired: Doc, current: Graph) -> Drift:
    """The schema's drift view (§8): the same field-wise truth as `drift_entries` —
    the path words projected onto `missing`/`changed`/`extra` (changed as
    `path.field` words) and the triples themselves carried whole in
    `Drift.entries`."""
    view = _diff(desired, current)
    return Drift(desired.path, view.missing, view.changed, view.extra, view.entries)


def apply(staged: Plan, desired: Doc, current: Graph) -> None:
    """One successor for the whole bring-up (§8): the staged plan's writes through the
    transaction seam, in the plan's compile order (link up, addresses, routes), then
    the deletions as interval closures — one commit, partial application refused.

    A stale base is refused `StaleBase` by name: if the current state moved since the
    plan was computed, nothing is overwritten — no silent overwrite, ever.
    """
    if staged.base != current.digest():
        raise StaleBase(
            "the plan's base digest is not the digest of the current graph: "
            "the state moved under it and nothing is overwritten"
        )
    ordered = compile_desired(desired, at=current.clock)
    touched = set(staged.creates) | set(staged.sets)
    writes: list[Write] = [Write(path, cell) for path, cell in ordered if path in touched]
    for path in sorted(staged.deletes):
        cell = current.get(path)
        if cell is None:
            continue
        if isinstance(cell, Address):
            cell = replace(cell, valid=Interval(cell.valid.valid_from, current.clock))
        writes.append(Write(path, cell, closed=True))
    current.commit(tuple(writes))


def _reconciled(current: Graph) -> dict[str, NetCell]:
    return {path: cell for path, cell in current.cells() if isinstance(cell, _RECONCILED)}


def _diff(desired: Doc, current: Graph) -> _Diff:
    ordered = compile_desired(desired)
    view = _reconciled(current)
    missing: list[str] = []
    changed: list[str] = []
    entries: list[DriftEntry] = []
    for path, cell in ordered:
        if path not in view:
            missing.append(path)
            entries.append(DriftEntry(path, cell, None))
            continue
        expected = _config(cell)
        actual = _config(view[path])
        for word in expected:
            if expected[word] != actual.get(word):
                changed.append(f"{path}.{word}")
                entries.append(DriftEntry(f"{path}.{word}", expected[word], actual.get(word)))
    wanted = dict(ordered)
    extra: list[str] = []
    for path, cell in sorted(view.items()):
        if path not in wanted:
            extra.append(path)
            entries.append(DriftEntry(path, None, cell))
    return _Diff(tuple(missing), tuple(changed), tuple(extra), tuple(entries))


# ---- the netplan-shape YAML: one pinned subset, emitted and parsed by hand ----

_SECTIONS: tuple[tuple[str, LinkKind], ...] = (
    ("ethernets", LinkKind.NIC),
    ("veths", LinkKind.VETH),
    ("bridges", LinkKind.BRIDGE),
    ("bonds", LinkKind.BOND),
    ("vlans", LinkKind.VLAN),
    ("dummies", LinkKind.DUMMY),
)
_MASTER_KINDS = (LinkKind.BRIDGE, LinkKind.BOND)
_DEVICE_KEYS = frozenset({"macaddress", "mtu", "interfaces", "peer", "id", "link", "addresses", "routes"})
_ROUTE_KEYS = frozenset({"to", "via", "metric"})


def render(desired: Doc, *, include: bool = True) -> str:
    """The netplan-shape YAML of the doc — the shape is the interface, the code is
    ours (§10).

    `include=True` (the default) is document composition: the includes merge in and
    the render yields one composed document. `include=False` renders the doc's own
    block alone. The output is canonical: `render(parse(render(d)))` is `render(d)`.
    """
    check(desired)
    doc = desired.composed() if include else replace(desired, includes=())
    lines = ["network:", "  version: 2"]
    for section, kind in _SECTIONS:
        specs = [spec for spec in doc.devices if spec.kind is kind]
        if not specs:
            continue
        lines.append(f"  {section}:")
        for spec in specs:
            lines.append(f"    {spec.name}:")
            if spec.mac is not None:
                lines.append(f"      macaddress: {format_mac(spec.mac)}")
            if spec.mtu is not None:
                lines.append(f"      mtu: {spec.mtu}")
            if spec.kind in _MASTER_KINDS and spec.members:
                lines.append("      interfaces:")
                lines.extend(f"        - {member}" for member in spec.members)
            if spec.peer is not None:
                lines.append(f"      peer: {spec.peer}")
            if spec.kind is LinkKind.VLAN:
                lines.append(f"      id: {spec.vid}")
                lines.append(f"      link: {spec.on}")
            if spec.addresses:
                lines.append("      addresses:")
                lines.extend(
                    f"        - {format_prefix(IPPrefix(address.ip, address.prefix))}"
                    for address in spec.addresses
                )
            if spec.routes:
                lines.append("      routes:")
                for route in spec.routes:
                    if route.to.length == 0 and route.via is not None:
                        lines.append("        - to: default")
                    else:
                        lines.append(f"        - to: {format_prefix(route.to)}")
                    if route.via is not None:
                        lines.append(f"          via: {format_ip(route.via)}")
                    lines.append(f"          metric: {route.metric}")
    return "\n".join(lines) + "\n"


def parse(text: str, *, path: str = "") -> Doc:
    """The YAML shape back to the `Doc` (§8): the pinned subset only.

    What parses, parses exactly; what the subset does not cover — anchors, tags,
    multi-document files, block scalars, inline mappings, the `include` file key, and
    unknown keys — is refused `NotSupported` by name, and text that does not parse is
    refused `MalformedDocument`. Nothing is accepted and dropped.
    """
    root = _parse_yaml(text)
    if set(root) != {"network"}:
        raise NotSupported(f"this shape has one top-level key, network: {sorted(root)} is not that")
    network = root["network"]
    if not isinstance(network, dict):
        raise MalformedDocument("network is a mapping: this one is not")
    if "include" in network:
        raise NotSupported(
            "this shape composes documents in the object, not by file name: the include key is refused by name"
        )
    unknown = set(network) - {"version"} - {section for section, _ in _SECTIONS}
    if unknown:
        raise NotSupported(f"this shape has no {sorted(unknown)} key under network: refused by name")
    if network.get("version") != 2:
        raise NotSupported("this shape takes version 2: any other version is refused by name")
    devices: list[DeviceSpec] = []
    for section, kind in _SECTIONS:
        block = network.get(section)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise MalformedDocument(f"{section} is a mapping of name to device: this one is not")
        for name, raw in block.items():
            devices.append(_load_device(name, kind, raw))
    return Doc(path, tuple(devices), ())


# -- parse: the tiny subset parser --------------------------------------------


@dataclass(frozen=True)
class _Line:
    indent: int
    content: str


def _logical_lines(text: str) -> list[_Line]:
    out: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "---":
            raise NotSupported("multi-document files are refused by name: one document per parse")
        leading = raw[: len(raw) - len(raw.lstrip())]
        if "\t" in leading:
            raise MalformedDocument(f"line {number}: indentation is spaces, never tabs")
        out.append(_Line(len(leading), _strip_comment(stripped)))
    return out


def _strip_comment(content: str) -> str:
    quote: str | None = None
    for index, char in enumerate(content):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or content[index - 1].isspace()):
            return content[:index].rstrip()
    return content


def _parse_yaml(text: str) -> dict[str, object]:
    lines = _logical_lines(text)
    if not lines:
        raise MalformedDocument("the document is empty")
    value, rest = _parse_block(lines, 0, lines[0].indent)
    if rest != len(lines):
        raise MalformedDocument("the document has words past its top block: indentation is off")
    if not isinstance(value, dict):
        raise MalformedDocument("the document is a mapping at the top: this one is not")
    return value


def _parse_block(lines: list[_Line], pos: int, indent: int) -> tuple[object, int]:
    if pos >= len(lines):
        raise MalformedDocument("a key needs a value: this block ends before one")
    if lines[pos].indent != indent:
        raise MalformedDocument("the block's lines share one indentation: this one does not")
    if _is_item(lines[pos].content):
        return _parse_sequence(lines, pos, indent)
    return _parse_mapping(lines, pos, indent)


def _parse_mapping(lines: list[_Line], pos: int, indent: int) -> tuple[dict[str, object], int]:
    out: dict[str, object] = {}
    while pos < len(lines) and lines[pos].indent == indent and not _is_item(lines[pos].content):
        entry = _split_key(lines[pos].content)
        if entry is None:
            raise MalformedDocument(f"every mapping line is a key and a value: {lines[pos].content!r} is not")
        key, rest = entry
        if key in out:
            raise MalformedDocument(f"the mapping names {key!r} twice: one key, one word")
        if rest == "":
            if pos + 1 < len(lines) and lines[pos + 1].indent > indent:
                value, pos = _parse_block(lines, pos + 1, lines[pos + 1].indent)
            else:
                value = {}  # a key with no nested block is an empty mapping (a bare device block)
                pos += 1
        else:
            value = _parse_value(rest)
            pos += 1
        out[key] = value
    return out, pos


def _parse_sequence(lines: list[_Line], pos: int, indent: int) -> tuple[list[object], int]:
    out: list[object] = []
    while pos < len(lines) and lines[pos].indent == indent and _is_item(lines[pos].content):
        content = lines[pos].content
        if content == "-":
            if pos + 1 >= len(lines) or lines[pos + 1].indent <= indent:
                raise MalformedDocument("a list item needs a value: this one has none")
            value, pos = _parse_block(lines, pos + 1, lines[pos + 1].indent)
            out.append(value)
            continue
        item = content[1:].lstrip()
        if _is_key(item):
            lines[pos] = _Line(indent + len(content) - len(item), item)  # the item's mapping starts here
            value, pos = _parse_mapping(lines, pos, lines[pos].indent)
        else:
            value = _parse_value(item)
            pos += 1
        out.append(value)
    return out, pos


def _is_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _is_key(text: str) -> bool:
    return _split_key(text) is not None


def _split_key(content: str) -> tuple[str, str] | None:
    """A mapping line is `key: value` or `key:` — the colon stands before a space or
    at the line's end (YAML's own rule: `2001:db8::a/64` is one scalar, not a key)."""
    head, marker, tail = content.partition(": ")
    if marker:
        key, rest = head, tail.strip()
    elif content.endswith(":"):
        key, rest = content[:-1].rstrip(), ""
    else:
        return None
    if not key or not all(char.isalnum() or char in "_.-" for char in key):
        return None
    return key, rest


def _parse_value(text: str) -> object:
    if text.startswith("["):
        if not text.endswith("]"):
            raise MalformedDocument("an inline list closes on the same line: this one does not")
        body = text[1:-1].strip()
        if not body:
            return []
        return [_parse_scalar(part.strip()) for part in _split_inline(body)]
    return _parse_scalar(text)


def _split_inline(body: str) -> list[str]:
    parts: list[str] = []
    quote: str | None = None
    start = 0
    for index, char in enumerate(body):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == ",":
            parts.append(body[start:index])
            start = index + 1
        elif char in "[]{}":
            raise MalformedDocument("inline lists hold scalars here: nested structures are refused")
    parts.append(body[start:])
    return parts


def _parse_scalar(text: str) -> object:
    if text.startswith(("&", "*", "!", "|", ">", "{")):
        raise NotSupported(f"this parser refuses {text[0]!r} words by name: anchors, tags, and block scalars stay out")
    if text.startswith(("'", '"')):
        if len(text) < 2 or not text.endswith(text[0]):
            raise MalformedDocument("a quoted scalar closes on the same line: this one does not")
        return text[1:-1]
    return int(text) if _is_int(text) else text


def _is_int(text: str) -> bool:
    body = text.removeprefix("-")
    return bool(body) and body.isdigit() and not (len(body) > 1 and body.startswith("0"))


# -- parse: the loader onto the semantic block ---------------------------------


def _load_device(name: str, kind: LinkKind, raw: object) -> DeviceSpec:
    if not isinstance(raw, dict):
        raise MalformedDocument(f"{name} is a mapping of keys: this one is not")
    unknown = set(raw) - _DEVICE_KEYS
    if unknown:
        raise NotSupported(f"this shape has no {sorted(unknown)} key under {name}: refused by name")
    if "interfaces" in raw and kind not in _MASTER_KINDS:
        raise NotSupported(f"interfaces is the master keys' word: {name} is a {kind.value} and refused it by name")
    if "peer" in raw and kind is not LinkKind.VETH:
        raise NotSupported(f"peer is the veths' word: {name} is a {kind.value} and refused it by name")
    if ("id" in raw or "link" in raw) and kind is not LinkKind.VLAN:
        raise NotSupported(f"id and link are the vlans' words: {name} is a {kind.value} and refused them by name")
    return DeviceSpec(
        name,
        kind,
        mac=_load_mac(raw.get("macaddress")),
        mtu=_load_word(raw.get("mtu"), "mtu"),
        members=_load_names(raw.get("interfaces")),
        peer=_load_name(raw.get("peer")),
        vid=_load_word(raw.get("id"), "id"),
        on=_load_name(raw.get("link")),
        addresses=tuple(_load_address(word) for word in _load_list(raw.get("addresses"))),
        routes=tuple(_load_route(item) for item in _load_maps(raw.get("routes"))),
    )


def _load_list(raw: object) -> list[object]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MalformedDocument("this key holds a list: this one does not")
    return raw


def _load_maps(raw: object) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for item in _load_list(raw):
        if not isinstance(item, dict):
            raise MalformedDocument("a route is a mapping of to, via, metric: this one is not")
        unknown = set(item) - _ROUTE_KEYS
        if unknown:
            raise NotSupported(f"this shape has no {sorted(unknown)} key in a route: refused by name")
        if "to" not in item:
            raise MalformedDocument("a route names its destination: this one does not")
        out.append(item)
    return out


def _load_names(raw: object) -> tuple[str, ...]:
    names: list[str] = []
    for word in _load_list(raw):
        name = _load_name(word)
        if name is None:
            raise MalformedDocument(f"a member name is a word: {word!r} is not one")
        names.append(name)
    return tuple(names)


def _load_name(raw: object) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise MalformedDocument(f"a name is a word: {raw!r} is not one")
    return raw


def _load_word(raw: object, key: str) -> int | None:
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise MalformedDocument(f"{key} is a word of digits: {raw!r} is not one")
    return raw


def _load_mac(raw: object) -> Mac | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise MalformedDocument("a macaddress is six hex bytes: this one is not")
    return parse_mac(raw)


def _load_address(raw: object) -> AddressSpec:
    if not isinstance(raw, str):
        raise MalformedDocument(f"an address is an address/prefix word: {raw!r} is not one")
    text, _, prefix = raw.rpartition("/")
    if not text or not _is_int(prefix):
        raise MalformedDocument(f"an address carries its prefix: {raw!r} does not")
    return AddressSpec(parse_ip(text), int(prefix))


def _load_route(item: Mapping[str, object]) -> RouteSpec:
    to_raw = item["to"]
    if not isinstance(to_raw, str):
        raise MalformedDocument("a route destination is a prefix word: this one is not")
    via_raw = item.get("via")
    if via_raw is not None and not isinstance(via_raw, str):
        raise MalformedDocument("a gateway is an address word: this one is not")
    via = parse_ip(via_raw) if isinstance(via_raw, str) else None
    if to_raw == "default":
        if via is None:
            raise InvalidDocument("a default route names its family through its gateway: this one has none")
        to = IPPrefix(b"\x00" * len(via), 0)
    else:
        to = parse_prefix(to_raw)
    metric = _load_word(item.get("metric"), "metric")
    return RouteSpec(to, via, 100 if metric is None else metric)


# -- address words: parsed and formatted by hand (both families, one model) ----


def parse_ip(text: str) -> IP:
    """An address literal to the schema's `IP` word (§9): 4 bytes for inet, 16 for
    inet6. V4-mapped v6 is refused by name (§17) — one length is one family."""
    if "%" in text:
        raise NotSupported("scoped literals are refused by name: an address word carries no zone")
    if text.count(".") == 3 and ":" not in text:
        return _parse_v4(text)
    if "." in text:
        raise NotSupported("v4-mapped v6 is refused by name (§17): one length is one family")
    return _parse_v6(text)


def format_ip(ip: IP) -> str:
    """The canonical literal of an address word (§9): RFC 5952 for inet6."""
    if len(ip) == 4:
        return ".".join(str(octet) for octet in ip)
    return _format_v6(ip)


def parse_prefix(text: str) -> IPPrefix:
    """`address/length` to the schema's `IPPrefix` word; the family rides the prefix."""
    head, _, length = text.rpartition("/")
    if not head or not _is_int(length):
        raise MalformedDocument(f"a prefix carries its length: {length!r} is not one")
    network = parse_ip(head)
    width = 32 if len(network) == 4 else 128
    if int(length) > width:
        raise MalformedDocument(f"a /{length} is no prefix of a {len(network)}-byte family")
    return IPPrefix(network, int(length))


def format_prefix(prefix: IPPrefix) -> str:
    return f"{format_ip(prefix.network)}/{prefix.length}"


def parse_mac(text: str) -> Mac:
    """`xx:xx:xx:xx:xx:xx` to the schema's `Mac` word."""
    parts = text.split(":")
    if len(parts) != 6 or any(len(part) != 2 or not _is_hex(part) for part in parts):
        raise MalformedDocument(f"a mac is six hex bytes in colons: {text!r} is not one")
    return bytes(int(part, 16) for part in parts)


def format_mac(mac: Mac) -> str:
    return ":".join(f"{octet:02x}" for octet in mac)


def _is_hex(text: str) -> bool:
    return bool(text) and all(char in "0123456789abcdefABCDEF" for char in text)


def _parse_v4(text: str) -> IP:
    parts = text.split(".")
    if len(parts) != 4 or any(not _is_int(part) or int(part) > 255 for part in parts):
        raise MalformedDocument(f"an inet address is four octets: {text!r} is not one")
    return bytes(int(part) for part in parts)


def _parse_v6(text: str) -> IP:
    if text.count("::") > 1:
        raise MalformedDocument(f"an inet6 address compresses once: {text!r} is not one")
    head, marker, tail = text.partition("::")
    groups = [group for part in (head, tail) for group in (part.split(":") if part else [])]
    for group in groups:
        if len(group) > 4 or not _is_hex(group):
            raise MalformedDocument(f"an inet6 address is hex groups: {text!r} is not one")
    words = [int(group, 16) for group in groups]
    if marker:
        zeros = 8 - len(words)
        if zeros < 1:
            raise MalformedDocument(f"an inet6 compression stands for a zero run: {text!r} has none")
        head_words = len(head.split(":")) if head else 0
        words = words[:head_words] + [0] * zeros + words[head_words:]
    elif len(words) != 8:
        raise MalformedDocument(f"an inet6 address has eight groups: {text!r} has {len(words)}")
    return b"".join(word.to_bytes(2, "big") for word in words)


def _format_v6(ip: IP) -> str:
    """RFC 5952: lowercase, no leading zeros, the longest zero run compressed (the
    leftmost on a tie), and a single zero group never compressed."""
    words = [int.from_bytes(ip[index : index + 2], "big") for index in range(0, 16, 2)]
    best_start, best_len = -1, 0
    start = -1
    for index, word in enumerate(words + [1]):
        if word == 0:
            if start < 0:
                start = index
        elif start >= 0:
            if index - start > best_len:
                best_start, best_len = start, index - start
            start = -1
    if best_len < 2:
        best_start = -1
    parts: list[str] = []
    index = 0
    while index < 8:
        if index == best_start:
            if index == 0:
                parts.append("")
            parts.append("")
            index += best_len
            if index == 8:
                parts.append("")
            continue
        parts.append(f"{words[index]:x}")
        index += 1
    return ":".join(parts)
