"""The declarative control plane (docs/25 §8): desired state, planned and applied.

`netplan` is the face: a `Doc` (the §8 semantic block) is authored state, `check`
refuses incoherence by name before anything is staged, `plan` compiles the delta set
against the base, `apply` turns it into one successor through the transaction seam
(partial application refused, a stale base refused), and `drift` is the field-wise
view — `(path, expected, actual)` triples over the device graph that change nothing.
`render`/`parse` move the document through the pinned netplan-shape YAML subset.

All of it is composable on paper: the lane writes the plan/apply logic against the
`net.link.link.Graph` seam, and the World lane fills that seam from the delta store.
"""

from .netplan import (
    AddressSpec,
    DeviceSpec,
    Doc,
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
    format_ip,
    format_mac,
    format_prefix,
    parse,
    parse_ip,
    parse_mac,
    parse_prefix,
    plan,
    render,
)

__all__ = [
    "AddressSpec",
    "DeviceSpec",
    "Doc",
    "DriftEntry",
    "InvalidDocument",
    "MalformedDocument",
    "RouteSpec",
    "StaleBase",
    "apply",
    "check",
    "compile_desired",
    "drift",
    "drift_entries",
    "format_ip",
    "format_mac",
    "format_prefix",
    "parse",
    "parse_ip",
    "parse_mac",
    "parse_prefix",
    "plan",
    "render",
]
