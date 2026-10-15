"""The scene input of the 3D world renderer: one JSON document, built read-only from an Inspector.

The world IS the arena — a projection, never a second truth. Every fact in this document comes
from one image (``Inspector`` / its ``Image``); the scene JavaScript renders it as a navigable
world and invents nothing. Memory regions become districts (ground plots), cells become buildings,
references become cables, the vacated poison of compaction becomes rubble and void tiles.

SCHEMA ("rxf-world-1")
======================

Top level::

    {
      "schema":   "rxf-world-1",       # this document's version
      "arena":    { ... },             # the arena header, decoded
      "grid":     { ... },             # the layout constants: tile step, district span/scale
      "regions":  [ Region, ... ],     # districts: ground plots over the byte range
      "cells":    [ Cell, ... ],       # buildings: one per cell of the stream
      "voids":    [ Void, ... ],       # 0xA5 poison runs: rubble pits / void tiles
      "edges":    [ Edge, ... ],       # cables: handle references between cells
      "ring":     { ... },             # the ready-ring as a ring road (geometry only)
      "trams":    [ Tram, ... ],       # run cells as units on the ring road
      "carriers": [],                  # SEAM: carrier lanes (SMP evidence, later stage)
      "metrics":  { ... },             # HUD numbers; null where no live data exists yet
      "counts":   { ... }              # totals for the HUD and the tests
    }

Region (a district — a ground plot over one band of the arena)::

    {
      "id":    "r7",                   # stable within one export
      "name":  "cells 4",              # "header", "index", or "cells N"
      "band":  "header" | "index" | "cells",
      "start": 16384, "end": 20480,    # byte range [start, end) of the arena
      "plot":  {"x": 42.0, "z": 0.0, "w": 12.0, "d": 18.0},  # world units, x/z = min corner
      "tiles": {"cols": 5, "rows": 4, "step_x": 3.0, "step_z": 3.4},  # the tile raster of the plot
      "cell_count": 12,                # cells whose offset starts in this district
      "empty": false                   # cell_count == 0 (an empty region stays a plot)
    }

``tiles`` is the placement raster: the floor of the district is tiled on it and every building
stands on one of its tiles (``cell.tile``). An empty district still gets a raster, so its floor
tiles like every other.

Cell (a building)::

    {
      "id": "h41",                     # "h{handle}" for live cells, "c{offset}" otherwise
      "handle": 41,                    # the live handle, or null (block / dead cell)
      "path": "mp_state_ctx.heap",     # object path, or null
      "name": "heap",                  # cell name, or null
      "kind": "Data",                  # NodeKind name, or "Index"/"Names"/"Block"/"Dead"
      "family": "block",               # what the world draws it as:
                                       #   "tower"    run cells (Fn, Code)      -> control towers
                                       #   "monument" named cells (under the root, Module, Namespace)
                                       #   "block"    data cells               -> blocks, sized by payload
                                       #   "utility"  stream bookkeeping cells (Index/Names blocks)
                                       #   "rubble"   dead cells               -> rubble mounds
      "offset": 41008, "size": 160,    # where the cell sits in the arena (bytes)
      "payload_size": 48,              # building bulk: footprint and height grow with it
      "generation": 0,                 # the generation of the handle slot
      "region": "r7",                  # the district the cell's offset falls into
      "plot": {"x": 44.2, "z": 9.1},   # world position of the building's center
      "tile": {"x": 2, "z": 1},        # the tile of the district raster the building stands on
      "footprint": 1.1, "height": 1.7, # world units
      "state": {"provenance": "Image", "disposition": "Retain",
                "cleanliness": "Clean", "mobility": "Movable"},  # the state word of ground.h
      "claim": null,                   # SEAM: live claim window (resolve/release), later stage
      "checksum": null,                # SEAM: run-cell checksum status, later stage
      "poisoned": false,               # a 0xA5 run of POISON_RUN bytes or more inside this cell
      "forward": 0,                    # C_FORWARD compaction record (0 = none)
      "index": 0, "ref_count": 2,      # the header words the walk decoded
      "anatomy": [ ... ]               # the block-stack floors, below
    }

Anatomy (the block-stack floors of a building — the memory-canvas / cell-anatomy view of
model.html, figure 1 and 2, as floors)::

    "anatomy": [
      {"role": "type",       "offset": 0,   "size": 8},   # the ten 64-byte header words ...
      {"role": "generation", "offset": 24,  "size": 8},   # ... the hot ones keep their name
      {"role": "forward",    "offset": 48,  "size": 8},   # C_FORWARD record (0 = ordinary)
      {"role": "concurrent", "offset": 64,  "size": 5},   # the S1 payload regions (run cells)
      {"role": "handoff",    "offset": 69,  "size": 6},
      {"role": "program",    "offset": 75,  "size": 36},
      {"role": "checksum",   "offset": 111, "size": 1},   # FNV-1a over the program state
      {"role": "refrows",    "offset": 112, "size": 48}   # 24 B per reference row
    ]

Every band is contiguous, starts at 0, and the sizes sum to ``size`` — one floor per band, colored
by role. The header words are the ten words of the 64-byte header (``type`` .. ``reserved``).
A run cell (family "tower") with a payload of 32 bytes or more splits it into the three regions of
S1 (docs/20) plus the checksum: ``concurrent`` (atomics), ``handoff`` (evidence), ``program``
(one writer between two safe points), ``checksum`` (FNV-1a over the program state), proportionally
to the real ``smp_run_payload``. A smaller payload, or any other cell, is one ``payload`` band.
A poisoned cell's payload is one ``poison`` band (rubble, void). ``refrows`` are the reference rows
and ``pad`` the alignment padding. ``ANATOMY_ROLES`` pins the vocabulary.

Void (a poison tile — the vacated tail of compaction reads 0xA5 until it is reused)::

    {
      "id": "v3", "offset": 47152, "size": 256,   # bytes of the run this tile covers
      "cell": "c47088",              # the cell the run starts in, or null (cell-less bytes)
      "region": "r11",               # district of the run's start
      "plot": {"x": 91.2, "z": 4.5}, "footprint": 2.7   # world units
    }

Edge (a cable — a handle reference)::

    {
      "id": "e12",
      "from": "h41", "to": "h8",      # cell ids; "to" is null when the handle is stale
      "from_handle": 41, "to_handle": 8,
      "how": "ref",                   # "ref"  a reference row of the cell header
                                      # "role" the Role handle of a reference row
                                      # "field" a declared reference slot in the payload
                                      # "alias" an object written through an Alias
      "kind": "Data",                 # RefKind name of the row (null for field/alias/role)
      "binding": "Mandatory",         # Binding name of the row (null likewise)
      "index": 2,                     # role position when the role repeats (null when none)
      "field": "next",                # the field name for "field" edges (null otherwise)
      "fresh": true                   # false = stale/refused handle -> red ghost cable
    }

A fresh edge names two cells of ``cells``. A stale edge names a handle the image no longer
resolves (the slot is dead or out of range) — ``to`` is null and the scene draws the cable as
a red ghost to a grave marker. Freshness is exactly ``Image.live(to_handle)``.

Ring and trams (the ready-ring of the execution policy, v1 geometry only)::

    "ring": {"center": {"x": 69.4, "z": 9.0}, "radius": 84.2, "lanes": 1, "state": null,
             "path": [{"x": -8, "z": -8}, {"x": 88, "z": -8}, ...]}   # closed ring road
    "trams": [{"id": "t0", "run": "h30", "angle": 0.785398, "state": null}]

``ring.path`` is the ring road as a closed chamfered rectangle around the arena bounds — what
the scene draws and what the tram units ride (``angle`` is the tram's slot on the loop).
``ring.state`` and every ``tram.state`` are SEAMs: the live READY/RUNNING/PARKED state of the
ready-ring is runtime evidence (docs/20, S3), not image data, and arrives in a later stage.
Each run cell (family "tower") places one tram on the ring; the scene drives the markers around
the ring as a deterministic visual — the run state they carry is the seam, not the motion.

Metrics (the top resource bar of the HUD)::

    {
      "runs": 4,             # run cells of the image — available, wired
      "carriers": null,      # SEAM: SMP carrier count
      "steps_per_s": null,   # SEAM: live step rate
      "claim_spins": null,   # SEAM: compactor claim spins (docs/20, S7)
      "moved_cells": null,   # SEAM: compaction evidence (docs/20, S8)
      "refusals": null,      # SEAM: state_refused counts (docs/20, S3)
      "checksum_status": null  # SEAM: run-cell checksum verdicts
    }

Determinism
===========

The document is a pure function of the image: no clock, no randomness, no map iteration order.
``scene_json`` renders it canonically (sorted keys, indent 1, trailing newline) so tests can pin
byte-exact goldens. Layout is derived from arena offsets alone (see the constants below), so the
same image always builds the same world.

What this exporter reads (all read-only): ``Inspector.header``, ``Inspector.cells``, and the
``Image`` behind ``Inspector.image`` for names, reference rows, declared reference slots
(``Image.layout``), alias uses, and liveness. It never mutates the image.
"""

from __future__ import annotations

import enum
import json
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pymergetic.rxf.core.arena import HANDLE_NONE, KIND_BLOCK, KIND_DEAD
from pymergetic.rxf.core.kinds import Binding, Mobility, NodeKind, RefKind
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector

SCHEMA = "rxf-world-1"

POISON_BYTE = 0xA5  # S8: the vacated tail of a compaction pass is filled with 0xA5
POISON_RUN = 64  # a run shorter than this is ordinary bytes, not poison
VOID_TILE = 512  # a poison run longer than this becomes several void tiles

# The layout of the world, all in world units. The arena is a strip of bytes: one district per
# DISTRICT_SPAN bytes at BYTE_SCALE units per byte, laid out as city blocks (DISTRICT_COLS per
# row, rows flowing along Z). Buildings sit on a deterministic tile raster inside their district
# (offset order, row major).
DISTRICT_SPAN = 4096
BYTE_SCALE = 4.5 / 1024.0
DISTRICT_DEPTH = 22.0
DISTRICT_GAP = 4.5
DISTRICT_COLS = 4  # districts per row of the city-block layout
GRID_STEP = 3.4
GRID_MARGIN = 1.5

# Cell anatomy (model.html, figure 1): the 64-byte header word for word, then the payload padded
# to 16-byte alignment, then one 24-byte reference row per reference.
CELL_BYTES = 64
CELL_ALIGN = 16
REF_BYTES = 24
HEADER_WORDS = (
    ("type", 8),
    ("parent", 8),
    ("payload_size", 8),
    ("generation", 8),
    ("name", 8),
    ("flags", 4),
    ("ref_count", 4),
    ("forward", 8),
    ("index", 4),
    ("reserved", 4),
)

# The run-cell payload (docs/20, S1): three regions whose boundary is the law, then the FNV-1a
# checksum over the program state. The weights are the real byte counts of smp_run_payload.
RUN_PAYLOAD_REGIONS = (
    ("concurrent", 28),
    ("handoff", 36),
    ("program", 208),
    ("checksum", 8),
)

ANATOMY_ROLES = tuple(word for word, _ in HEADER_WORDS) + (
    "payload",
    "concurrent",
    "handoff",
    "program",
    "checksum",
    "poison",
    "refrows",
    "pad",
)

FAMILIES = ("tower", "monument", "block", "utility", "rubble")
HOWS = ("ref", "role", "field", "alias")
BANDS = ("header", "index", "cells")


class SchemaError(ValueError):
    """A scene document does not match the schema this module documents."""


def _word(words: type[enum.IntEnum], value: int) -> str:
    """The name of an enum value; a value the enum does not know stays a number."""
    try:
        return words(value).name
    except ValueError:
        return str(value)


# The state words of ground.h: provenance 1, disposition 2, cleanliness 1, mobility 2.
_PROVENANCE = ("Runtime", "Image")
_DISPOSITION = ("Retain", "Transient", "Tombstone")
_CLEANLINESS = ("Clean", "Dirty")


def _state(flags: int) -> dict[str, str]:
    state = (flags >> 8) & 0x3F
    return {
        "provenance": _PROVENANCE[state & 0x1],
        "disposition": _DISPOSITION[(state >> 1) & 0x3],
        "cleanliness": _CLEANLINESS[(state >> 3) & 0x1],
        "mobility": _word(Mobility, (state >> 4) & 0x3),
    }


def _round(value: float) -> float:
    return round(value, 3)


# ---- regions: the arena as districts ---------------------------------------------------------------


def _districts(header: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The bands of the arena (the same three the memory map labels), the cells band split into
    districts of at most DISTRICT_SPAN bytes. A cell belongs to the district its offset starts in.
    The districts are laid out as city blocks: up to DISTRICT_COLS in a row, rows flowing along Z,
    so the arena reads as one connected world instead of a strip."""
    body = int(header["body_size"])
    chunks: list[tuple[str, str, int, int]] = [
        ("header", "header", 0, int(header["header_size"])),
        ("index", "index", int(header["index_off"]), int(header["cells_off"])),
    ]
    at = int(header["cells_off"])
    index = 0
    while at < body:
        end = min(at + DISTRICT_SPAN, body)
        chunks.append(("cells", f"cells {index}", at, end))
        at = end
        index += 1

    cols = DISTRICT_COLS
    out: list[dict[str, Any]] = []
    x = z = 0.0
    for i, (band, name, start, end) in enumerate(chunks):
        width = max(6.0, (end - start) * BYTE_SCALE)
        if i % cols == 0 and i > 0:
            x = 0.0
            z += DISTRICT_DEPTH + DISTRICT_GAP
        out.append(
            {
                "id": f"r{i}",
                "name": name,
                "band": band,
                "start": start,
                "end": end,
                "plot": {"x": _round(x), "z": _round(z), "w": _round(width), "d": DISTRICT_DEPTH},
                "cell_count": 0,
                "empty": True,
            }
        )
        x += width + DISTRICT_GAP
    return out


def _region_of(regions: Sequence[dict[str, Any]], offset: int) -> dict[str, Any]:
    for region in regions:
        if int(region["start"]) <= offset < int(region["end"]):
            return region
    return regions[-1]


# ---- cells: buildings ------------------------------------------------------------------------------


def _family(kind: int, handle: int | None, parent: int) -> str:
    if kind == KIND_DEAD:
        return "rubble"
    if kind == KIND_BLOCK:
        return "utility"
    name = _word(NodeKind, kind)
    if name in ("Fn", "Code"):
        return "tower"
    if handle == 0 or parent == 0 or name in ("Module", "Namespace"):
        return "monument"
    return "block"


_MASS = 512.0  # payload bytes that count as one unit of building bulk


def _mass(payload_size: int) -> float:
    return min(4.0, payload_size / _MASS)


def _bulk(family: str, payload_size: int) -> tuple[float, float]:
    """Footprint and height of a building: run cells are tall control towers, data cells are
    blocks that grow with their payload, rubble stays low. Height is the mass signal: the world
    reads bulk at any zoom, the footprint stays inside one floor tile."""
    mass = _mass(payload_size)
    if family == "tower":
        return _round(1.9 + 0.35 * mass), _round(8.0 + 5.0 * mass)
    if family == "monument":
        return _round(1.8 + 0.5 * mass), _round(4.0 + 3.0 * mass)
    if family == "utility":
        return 1.3, 1.6
    if family == "rubble":
        return _round(1.5 + 0.5 * mass), _round(0.4 + 0.3 * mass)
    return _round(1.3 + 0.9 * mass), _round(1.8 + 5.0 * mass)


# ---- anatomy: the block-stack floors of a building ---------------------------------------------------


def _split_regions(offset: int, size: int) -> list[dict[str, Any]]:
    """The S1 regions of a run-cell payload, partitioned exactly: proportional to the real byte
    counts of smp_run_payload (largest remainder, ties by order), so the floors cover the payload
    byte for byte."""
    weights = [weight for _, weight in RUN_PAYLOAD_REGIONS]
    exact = [size * weight / sum(weights) for weight in weights]
    parts = [int(part) for part in exact]
    left = size - sum(parts)
    for i in sorted(range(len(parts)), key=lambda i: (-(exact[i] - parts[i]), i))[:left]:
        parts[i] += 1
    rows = [(word, part) for (word, _), part in zip(RUN_PAYLOAD_REGIONS, parts, strict=True)]
    return [
        {"role": word, "offset": at, "size": part}
        for (word, part), at in _zip_offsets(rows, offset)
        if part > 0
    ]


def _anatomy(
    cell_size: int,
    payload_size: int,
    ref_count: int,
    family: str,
    poisoned: bool,
) -> list[dict[str, Any]]:
    """One floor per band of the cell: the 64-byte header word for word, the payload split by the
    regions of S1 when it is a run cell (or one band, or one poison band), the reference rows, and
    the alignment padding. The bands are contiguous and sum to the cell size."""
    payload_band = CELL_ALIGN * math.ceil(payload_size / CELL_ALIGN)
    payload_offset = CELL_BYTES

    def bands() -> list[dict[str, Any]]:
        out = [
            {"role": word, "offset": offset, "size": size}
            for (word, size), offset in _zip_offsets(HEADER_WORDS, 0)
        ]
        at = payload_offset
        if poisoned:
            out.append({"role": "poison", "offset": at, "size": payload_size})
        elif family == "tower" and payload_size >= 32:
            out.extend(_split_regions(at, payload_size))
        elif payload_size:
            out.append({"role": "payload", "offset": at, "size": payload_size})
        at += payload_size
        if payload_band > payload_size:  # the 16-byte alignment of the payload
            out.append({"role": "pad", "offset": at, "size": payload_band - payload_size})
            at += payload_band - payload_size
        ref_bytes = REF_BYTES * ref_count
        if ref_bytes:
            out.append({"role": "refrows", "offset": at, "size": ref_bytes})
            at += ref_bytes
        tail = CELL_ALIGN * math.ceil(at / CELL_ALIGN) - at  # the alignment to the cell size
        if tail:
            out.append({"role": "pad", "offset": at, "size": tail})
        return out

    rows = bands()
    end = rows[-1]["offset"] + rows[-1]["size"]
    if end < cell_size:
        # A dead cell's recorded size outranks the arithmetic (it was cut short by its death).
        rows.append({"role": "pad", "offset": end, "size": cell_size - end})
    elif end > cell_size:
        overflow = end - cell_size
        for band in reversed(rows):
            take = min(int(band["size"]), overflow)
            band["size"] = int(band["size"]) - take
            overflow -= take
            if overflow == 0:
                break
        rows = [band for band in rows if int(band["size"]) > 0]
    return rows


def _zip_offsets(
    rows: Sequence[tuple[str, int]], start: int
) -> list[tuple[tuple[str, int], int]]:
    out: list[tuple[tuple[str, int], int]] = []
    at = start
    for row in rows:
        out.append((row, at))
        at += int(row[1])
    return out


def _tiles(count: int, width: float, depth: float) -> tuple[dict[str, Any], list[tuple[float, float, int, int]]]:
    """The placement raster of a district and the tile of each of `count` buildings on it
    (offset order, row major, deterministic and centered). The raster comes from the plot itself
    (tiles of at most GRID_STEP), so an empty district's floor tiles like every other; when the
    buildings do not fit, the raster grows by whole rows/columns."""
    cols = max(1, int((width - 2 * GRID_MARGIN) // GRID_STEP))
    rows = max(1, int((depth - 2 * GRID_MARGIN) // GRID_STEP))
    while cols * rows < count:
        if cols <= rows:
            cols += 1
        else:
            rows += 1
    step_x = min(GRID_STEP, max(1.1, (width - 2 * GRID_MARGIN) / cols))
    step_z = min(GRID_STEP, max(1.1, (depth - 2 * GRID_MARGIN) / rows))
    raster = {
        "cols": cols,
        "rows": rows,
        "step_x": _round(step_x),
        "step_z": _round(step_z),
    }
    if count <= 0:
        return raster, []
    span_x, span_z = step_x * cols, step_z * rows
    used_cols = min(cols, count)
    used_rows = math.ceil(count / used_cols)
    start_col = (cols - used_cols) // 2
    start_row = (rows - used_rows) // 2
    plots = []
    for i in range(count):
        col, row = start_col + i % used_cols, start_row + i // used_cols
        plots.append(
            (
                _round((width - span_x) / 2 + (col + 0.5) * step_x),
                _round((depth - span_z) / 2 + (row + 0.5) * step_z),
                col,
                row,
            )
        )
    return raster, plots


# ---- voids: the 0xA5 poison of compaction -----------------------------------------------------------


def _poison_runs(raw: bytes, body: int) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    at = 0
    while at < body:
        if raw[at] != POISON_BYTE:
            at += 1
            continue
        end = at + 1
        while end < body and raw[end] == POISON_BYTE:
            end += 1
        if end - at >= POISON_RUN:
            runs.append((at, end - at))
        at = end
    return runs


def _ring_path(span_x: float, span_z: float) -> list[dict[str, float]]:
    """The ring road as a closed path around the arena: a rectangle with chamfered corners, on
    which the tram units ride. Eight points, deterministic from the world bounds."""
    margin = 8.0
    x0, z0 = -margin, -margin
    x1, z1 = span_x + margin, span_z + margin
    corner = 14.0
    points = (
        (x0 + corner, z0),
        (x1 - corner, z0),
        (x1, z0 + corner),
        (x1, z1 - corner),
        (x1 - corner, z1),
        (x0 + corner, z1),
        (x0, z1 - corner),
        (x0, z0 + corner),
    )
    return [{"x": _round(x), "z": _round(z)} for x, z in points]


def _poison_hosts(stream: Sequence[Mapping[str, Any]], runs: Sequence[tuple[int, int]]) -> set[int]:
    """The offsets of the cells a poison run starts in (or falls into) — the buildings that stand
    on rubble."""
    hosts: set[int] = set()
    for start, _ in runs:
        host = next(
            (
                cell
                for cell in stream
                if int(cell["offset"]) == start
                or int(cell["offset"]) <= start < int(cell["offset"]) + int(cell["size"])
            ),
            None,
        )
        if host is not None:
            hosts.add(int(host["offset"]))
    return hosts


# ---- edges: handle references ------------------------------------------------------------------------


def _reference_slots(image: Image, type_handle: int) -> list[dict[str, Any]]:
    """The declared reference slots of a type's payload layout (the same walk the inspector's
    `used_by` card makes): a slot holds one handle, 8 bytes."""
    if type_handle == HANDLE_NONE or image.type_name(type_handle) != "Type":
        return []
    return [leaf for leaf in image.layout(type_handle)["leaves"] if leaf["reference"]]


def _edges(image: Image, cells_by_handle: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every handle reference of the image as cables: reference rows, the Role handle of each row,
    declared reference slots in payloads, and objects written through an Alias. A handle the image
    no longer resolves (dead slot or out of range) makes a stale edge — the red ghost cable."""

    def fresh(target: int) -> bool:
        return target < image.handle_count and image.live(target)

    found: list[dict[str, Any]] = []
    for handle in sorted(cells_by_handle):
        got = image.get(handle)
        payload: bytes = got["payload"]
        for target, role, index, kind, binding in got["refs"]:
            if target != HANDLE_NONE:
                found.append(
                    {
                        "from": handle,
                        "to": target,
                        "how": "ref",
                        "kind": _word(RefKind, kind),
                        "binding": _word(Binding, binding),
                        "index": index,
                        "field": None,
                        "fresh": fresh(target),
                    }
                )
            if role is not None:
                found.append(
                    {
                        "from": handle,
                        "to": role,
                        "how": "role",
                        "kind": None,
                        "binding": None,
                        "index": index,
                        "field": None,
                        "fresh": fresh(role),
                    }
                )
        for leaf in _reference_slots(image, int(got["type"])):
            offset = int(leaf["offset"])
            if offset + 8 > len(payload):
                continue
            (target,) = struct.unpack_from("<Q", payload, offset)
            if target == HANDLE_NONE:
                continue
            found.append(
                {
                    "from": handle,
                    "to": target,
                    "how": "field",
                    "kind": None,
                    "binding": None,
                    "index": None,
                    "field": leaf["name"],
                    "fresh": fresh(target),
                }
            )
    for alias, user in image.alias_uses():
        found.append(
            {
                "from": user,
                "to": alias,
                "how": "alias",
                "kind": None,
                "binding": None,
                "index": None,
                "field": None,
                "fresh": fresh(alias),
            }
        )
    found = [edge for edge in found if edge["from"] != edge["to"]]
    found.sort(
        key=lambda edge: (
            int(edge["from"]),
            HOWS.index(str(edge["how"])),
            int(edge["to"]),
            -1 if edge["index"] is None else int(edge["index"]),
            str(edge["field"] or ""),
        )
    )
    for number, edge in enumerate(found):
        edge["id"] = f"e{number}"
    return found


# ---- the document ------------------------------------------------------------------------------------


def export_world(inspector: Inspector) -> dict[str, Any]:
    """The scene input of one image, deterministic: the same image always yields the same document."""
    image = inspector.image
    header = inspector.header()
    stream = inspector.cells()
    body = int(header["body_size"])
    raw = image.to_bytes()

    regions = _districts(header)
    by_handle: dict[int, dict[str, Any]] = {
        int(cell["handle"]): cell for cell in stream if cell["handle"] is not None
    }
    for cell in stream:
        region = _region_of(regions, int(cell["offset"]))
        region["cell_count"] = int(region["cell_count"]) + 1
        region["empty"] = False

    poison_runs = _poison_runs(raw, body)
    poisoned_at = _poison_hosts(stream, poison_runs)

    # One building per district, offset order, on the district's tile raster.
    placements: dict[int, tuple[float, float, int, int]] = {}
    for region in regions:
        here = sorted(
            (cell for cell in stream if _region_of(regions, int(cell["offset"])) is region),
            key=lambda cell: int(cell["offset"]),
        )
        plot = region["plot"]
        raster, plots = _tiles(len(here), float(plot["w"]), float(plot["d"]))
        region["tiles"] = raster
        for (x, z, col, row), cell in zip(plots, here, strict=True):
            placements[int(cell["offset"])] = (
                _round(float(plot["x"]) + x),
                _round(float(plot["z"]) + z),
                col,
                row,
            )

    cells: list[dict[str, Any]] = []
    cell_id: dict[int, str] = {}
    for cell in stream:
        offset, size = int(cell["offset"]), int(cell["size"])
        handle = cell["handle"]
        kind = int(cell["kind"])
        payload_size = int(cell["payload_size"])
        if handle is None:
            family = _family(kind, None, -1)
            name = path = None
            state = None
            identifier = f"c{offset}"
        else:
            handle = int(handle)
            got = image.get(handle)
            family = _family(kind, handle, int(got["parent"]))
            name = got["name"] or None
            path = got["path"]
            state = _state(int(cell["flags"]))
            identifier = f"h{handle}"
            cell_id[handle] = identifier
        if kind == KIND_BLOCK:
            word = {1: "Index", 2: "Names"}.get(int(cell["block_kind"]), "Block")
        elif kind == KIND_DEAD:
            word = "Dead"
        else:
            word = _word(NodeKind, kind)
        x, z, col, row = placements[offset]
        footprint, height = _bulk(family, payload_size)
        region = _region_of(regions, offset)
        raster = region["tiles"]
        footprint = min(
            footprint, _round(0.92 * min(float(raster["step_x"]), float(raster["step_z"])))
        )
        poisoned = offset in poisoned_at
        cells.append(
            {
                "id": identifier,
                "handle": None if handle is None else int(handle),
                "path": path,
                "name": name,
                "kind": word,
                "family": family,
                "offset": offset,
                "size": size,
                "payload_size": payload_size,
                "generation": int(cell["generation"]),
                "region": str(region["id"]),
                "plot": {"x": x, "z": z},
                "tile": {"x": col, "z": row},
                "footprint": footprint,
                "height": height,
                "state": state,
                "claim": None,
                "checksum": None,
                "poisoned": poisoned,
                "forward": int(cell["forward"]),
                "index": int(cell["index"]),
                "ref_count": int(cell["ref_count"]),
                "anatomy": _anatomy(
                    size, payload_size, int(cell["ref_count"]), family, poisoned
                ),
            }
        )
    cell_by_offset = {int(cell["offset"]): cell for cell in cells}

    voids: list[dict[str, Any]] = []
    for start, length in _poison_runs(raw, body):
        host = cell_by_offset.get(start)
        if host is None:
            host = next(
                (
                    cell
                    for cell in cells
                    if int(cell["offset"]) <= start < int(cell["offset"]) + int(cell["size"])
                ),
                None,
            )
        region = _region_of(regions, start)
        if host is not None:
            anchor = host["plot"]
            host["poisoned"] = True
            host_id = host["id"]
        else:
            plot = region["plot"]
            anchor = {
                "x": _round(float(plot["x"]) + float(plot["w"]) / 2),
                "z": _round(float(plot["d"]) / 2),
            }
            host_id = None
        tiles = [min(VOID_TILE, length - at) for at in range(0, length, VOID_TILE)]
        cols = max(1, math.ceil(math.sqrt(len(tiles))))
        rows = math.ceil(len(tiles) / cols)
        for number, chunk in enumerate(tiles):
            col, row = number % cols, number // cols
            voids.append(
                {
                    "id": f"v{len(voids)}",
                    "offset": start + sum(tiles[:number]),
                    "size": chunk,
                    "cell": host_id,
                    "region": str(region["id"]),
                    "plot": {
                        "x": _round(float(anchor["x"]) + (col - (cols - 1) / 2) * 2.2),
                        "z": _round(float(anchor["z"]) + (row - (rows - 1) / 2) * 2.2),
                    },
                    "footprint": _round(min(10.0, max(1.2, chunk / 96.0))),
                }
            )

    edges = _edges(image, by_handle)
    for edge in edges:
        source = cell_id.get(int(edge["from"]))
        edge["from_handle"] = edge.pop("from")
        edge["to_handle"] = edge.pop("to")
        edge["from"] = source
        edge["to"] = (
            cell_id.get(int(edge["to_handle"])) if edge["fresh"] else None
        )

    towers = [cell for cell in cells if cell["family"] == "tower"]
    span_x = max(
        (float(region["plot"]["x"]) + float(region["plot"]["w"]) for region in regions),
        default=1.0,
    )
    span_z = max(
        (float(region["plot"]["z"]) + float(region["plot"]["d"]) for region in regions),
        default=1.0,
    )
    center = {"x": _round(span_x / 2), "z": _round(span_z / 2)}
    ring_path = _ring_path(span_x, span_z)
    trams = [
        {
            "id": f"t{i}",
            "run": str(cell["id"]),
            "angle": _round(2 * math.pi * (i + 0.5) / max(1, len(towers))),
            "state": None,
        }
        for i, cell in enumerate(sorted(towers, key=lambda cell: int(cell["offset"])))
    ]

    counts = {
        "cells": len(cells),
        "live": len(by_handle),
        "towers": len(towers),
        "monuments": sum(1 for cell in cells if cell["family"] == "monument"),
        "blocks": sum(1 for cell in cells if cell["family"] == "block"),
        "utilities": sum(1 for cell in cells if cell["family"] == "utility"),
        "rubble": sum(1 for cell in cells if cell["family"] == "rubble"),
        "voids": len(voids),
        "edges": len(edges),
        "stale_edges": sum(1 for edge in edges if not edge["fresh"]),
    }
    return {
        "schema": SCHEMA,
        "arena": {
            "version": int(header["version"]),
            "body_size": body,
            "cells_off": int(header["cells_off"]),
            "frontier": int(header["frontier"]),
            "handle_count": int(header["handle_count"]),
            "index_chunks": int(header["index_chunks"]),
            "names_size": int(header["names_size"]),
        },
        "grid": {
            "step": _round(GRID_STEP),
            "margin": _round(GRID_MARGIN),
            "district_span": DISTRICT_SPAN,
            "district_depth": _round(DISTRICT_DEPTH),
            "district_gap": _round(DISTRICT_GAP),
            "district_cols": DISTRICT_COLS,
            "byte_scale": _round(BYTE_SCALE),
        },
        "regions": regions,
        "cells": cells,
        "voids": voids,
        "edges": edges,
        "ring": {
            "center": center,
            "radius": _round(0.5 * math.hypot(span_x, span_z) + 12.0),
            "lanes": 1,
            "state": None,
            "path": ring_path,
        },
        "trams": trams,
        "carriers": [],
        "metrics": {
            "runs": len(towers),
            "carriers": None,
            "steps_per_s": None,
            "claim_spins": None,
            "moved_cells": None,
            "refusals": None,
            "checksum_status": None,
        },
        "counts": counts,
    }


def scene_json(inspector: Inspector) -> str:
    """The scene document as canonical text (sorted keys, indent 1, trailing newline): what the
    tests pin byte for byte and what /world/scene.json serves."""
    return json.dumps(export_world(inspector), indent=1, sort_keys=True) + "\n"


# ---- validation ---------------------------------------------------------------------------------------

_REQUIRED_TOP = (
    "schema",
    "arena",
    "grid",
    "regions",
    "cells",
    "voids",
    "edges",
    "ring",
    "trams",
    "carriers",
    "metrics",
    "counts",
)
_REQUIRED_METRICS = (
    "runs",
    "carriers",
    "steps_per_s",
    "claim_spins",
    "moved_cells",
    "refusals",
    "checksum_status",
)


def _need(row: Mapping[str, Any], fields: Iterable[str], where: str) -> None:
    for field in fields:
        if field not in row:
            raise SchemaError(f"{where}: missing {field!r}")


def _anatomy_ok(cell: Mapping[str, Any]) -> None:
    """The block-stack floors: contiguous bands from byte 0 that cover the cell exactly."""
    at = 0
    for band in cell["anatomy"]:
        _need(band, ("role", "offset", "size"), f"cell {cell['id']!r} anatomy")
        if band["role"] not in ANATOMY_ROLES:
            raise SchemaError(f"cell {cell['id']!r}: anatomy role {band['role']!r}")
        if int(band["offset"]) != at or int(band["size"]) <= 0:
            raise SchemaError(f"cell {cell['id']!r}: anatomy bands are not contiguous")
        at += int(band["size"])
    if at != int(cell["size"]):
        raise SchemaError(f"cell {cell['id']!r}: anatomy covers {at} of {cell['size']} bytes")


def validate_scene(scene: Mapping[str, Any]) -> None:
    """Raises SchemaError when the document is not a scene of SCHEMA; the tests pin this too."""
    _need(scene, _REQUIRED_TOP, "scene")
    if scene["schema"] != SCHEMA:
        raise SchemaError(f"scene: schema {scene['schema']!r} is not {SCHEMA!r}")
    _need(scene["grid"], ("step", "margin", "district_span", "district_depth", "district_gap", "district_cols", "byte_scale"), "grid")
    if float(scene["grid"]["step"]) <= 0 or float(scene["grid"]["margin"]) < 0:
        raise SchemaError("grid: a tile raster with no step")
    regions = scene["regions"]
    if not isinstance(regions, list) or not regions:
        raise SchemaError("scene: regions is empty")
    region_ids: set[str] = set()
    for region in regions:
        _need(
            region,
            ("id", "name", "band", "start", "end", "plot", "tiles", "cell_count", "empty"),
            "region",
        )
        if region["id"] in region_ids:
            raise SchemaError(f"region {region['id']!r}: duplicate id")
        region_ids.add(region["id"])
        if region["band"] not in BANDS:
            raise SchemaError(f"region {region['id']!r}: band {region['band']!r}")
        if not int(region["start"]) < int(region["end"]):
            raise SchemaError(f"region {region['id']!r}: empty byte range")
        _need(region["plot"], ("x", "z", "w", "d"), f"region {region['id']!r} plot")
        _need(region["tiles"], ("cols", "rows", "step_x", "step_z"), f"region {region['id']!r} tiles")
        if int(region["tiles"]["cols"]) < 1 or int(region["tiles"]["rows"]) < 1:
            raise SchemaError(f"region {region['id']!r}: tile raster with no tiles")
        if float(region["tiles"]["step_x"]) <= 0 or float(region["tiles"]["step_z"]) <= 0:
            raise SchemaError(f"region {region['id']!r}: tile raster with no step")
        if bool(region["empty"]) != (int(region["cell_count"]) == 0):
            raise SchemaError(f"region {region['id']!r}: empty and cell_count disagree")

    cells = scene["cells"]
    if not isinstance(cells, list):
        raise SchemaError("scene: cells is not a list")
    regions_by_id = {str(region["id"]): region for region in regions}
    cell_ids: set[str] = set()
    for cell in cells:
        _need(
            cell,
            (
                "id", "handle", "path", "name", "kind", "family", "offset", "size",
                "payload_size", "generation", "region", "plot", "tile", "footprint", "height",
                "state", "claim", "checksum", "poisoned", "forward", "index", "ref_count",
                "anatomy",
            ),
            "cell",
        )
        if cell["id"] in cell_ids:
            raise SchemaError(f"cell {cell['id']!r}: duplicate id")
        cell_ids.add(cell["id"])
        if cell["family"] not in FAMILIES:
            raise SchemaError(f"cell {cell['id']!r}: family {cell['family']!r}")
        if cell["region"] not in region_ids:
            raise SchemaError(f"cell {cell['id']!r}: unknown region {cell['region']!r}")
        if not (float(cell["footprint"]) > 0 and float(cell["height"]) > 0):
            raise SchemaError(f"cell {cell['id']!r}: building has no bulk")
        _need(cell["plot"], ("x", "z"), f"cell {cell['id']!r} plot")
        _need(cell["tile"], ("x", "z"), f"cell {cell['id']!r} tile")
        raster = regions_by_id[str(cell["region"])]["tiles"]
        if not (
            0 <= int(cell["tile"]["x"]) < int(raster["cols"])
            and 0 <= int(cell["tile"]["z"]) < int(raster["rows"])
        ):
            raise SchemaError(f"cell {cell['id']!r}: tile is off the district raster")
        _anatomy_ok(cell)

    void_ids: set[str] = set()
    for void in scene["voids"]:
        _need(void, ("id", "offset", "size", "cell", "region", "plot", "footprint"), "void")
        if void["id"] in void_ids:
            raise SchemaError(f"void {void['id']!r}: duplicate id")
        void_ids.add(void["id"])
        if int(void["size"]) <= 0:
            raise SchemaError(f"void {void['id']!r}: no bytes")
        if void["region"] not in region_ids:
            raise SchemaError(f"void {void['id']!r}: unknown region {void['region']!r}")
        if void["cell"] is not None and void["cell"] not in cell_ids:
            raise SchemaError(f"void {void['id']!r}: unknown cell {void['cell']!r}")

    edge_ids: set[str] = set()
    for edge in scene["edges"]:
        _need(
            edge,
            ("id", "from", "to", "from_handle", "to_handle", "how", "kind", "binding",
             "index", "field", "fresh"),
            "edge",
        )
        if edge["id"] in edge_ids:
            raise SchemaError(f"edge {edge['id']!r}: duplicate id")
        edge_ids.add(edge["id"])
        if edge["how"] not in HOWS:
            raise SchemaError(f"edge {edge['id']!r}: how {edge['how']!r}")
        if edge["from"] not in cell_ids:
            raise SchemaError(f"edge {edge['id']!r}: unknown source {edge['from']!r}")
        if bool(edge["fresh"]) != (edge["to"] is not None):
            raise SchemaError(f"edge {edge['id']!r}: fresh and target disagree")
        if edge["to"] is not None and edge["to"] not in cell_ids:
            raise SchemaError(f"edge {edge['id']!r}: unknown target {edge['to']!r}")

    ring = scene["ring"]
    if ring is not None:
        _need(ring, ("center", "radius", "lanes", "state", "path"), "ring")
        _need(ring["center"], ("x", "z"), "ring center")
        if not isinstance(ring["path"], list) or len(ring["path"]) < 4:
            raise SchemaError("ring: path is not a closed path of points")
        for point in ring["path"]:
            _need(point, ("x", "z"), "ring path point")
    for tram in scene["trams"]:
        _need(tram, ("id", "run", "angle", "state"), "tram")
        if tram["run"] not in cell_ids:
            raise SchemaError(f"tram {tram['id']!r}: unknown run {tram['run']!r}")
    if not isinstance(scene["carriers"], list):
        raise SchemaError("scene: carriers is not a list")
    _need(scene["metrics"], _REQUIRED_METRICS, "metrics")
    for count in ("cells", "edges", "stale_edges", "voids"):
        if count not in scene["counts"]:
            raise SchemaError(f"counts: missing {count!r}")
