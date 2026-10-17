"""The scene input of the 3D world renderer: one JSON document, built read-only from an Inspector.

The world IS the arena — a projection, never a second truth. Every fact in this document comes
from one image (``Inspector`` / its ``Image``); the scene JavaScript renders it as a navigable
world and invents nothing. Memory regions become districts (ground plots), cells become buildings,
references become cables, the vacated poison of compaction becomes rubble and void tiles.

SCHEMA ("rxf-world-2")
======================

Top level::

    {
      "schema":   "rxf-world-2",       # this document's version
      "arena":    { ... },             # the arena header, decoded
      "grid":     { ... },             # the layout constants: tile step, district span/scale
      "regions":  [ Region, ... ],     # districts: ground plots over the byte range
      "cells":    [ Cell, ... ],       # buildings: one per cell of the stream
      "blocks":   [ Block, ... ],      # city blocks: buildings grouped on the tile raster
      "voids":    [ Void, ... ],       # 0xA5 poison runs: rubble pits / void tiles
      "edges":    [ Edge, ... ],       # cables: handle references between cells
      "streets":  [ Street, ... ],     # the street grid in the gaps between the plots
      "ring":     { ... },             # the ready-ring as a ring road, with its run data
      "trams":    [ Tram, ... ],       # the runs in ring slots as units on the ring road
      "carriers": [],                  # SEAM: carrier lanes (SMP evidence, later stage)
      "metrics":  { ... },             # HUD numbers; null where the image carries no such word
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

Block (a city block — the massing of one tile-raster bin)::

    {
      "id": "b3",                      # stable within one export
      "region": "r11",                 # the district the block stands in
      "plot": {"x": 40.1, "z": 41.0, "w": 7.2, "d": 8.4},  # the bin's raster slot:
                                       # raster-fixed (x/z = min corner), so a row keeps its
                                       # extent whatever the buildings' size
      "height": 6.2,                   # the tallest building in the block (massing height)
      "count": 6,                      # buildings standing on this block
      "family": "block"                # the dominant family (tower > monument > ...)
    }

Street (one street of the city — a segment in a gap of the footprint grid)::

    {
      "kind": "avenue",                # "avenue" between the district rows, ring to ring
                                       # "cross"  between the district columns, avenue to avenue
                                       # "lane"   the street between the buildings of a district
                                       # "branch" the connector from a district to the grid
      "axis": "x" | "z",               # "x": the street runs along x (constant z)
      "at": 26.5,                      # the line it runs on, in world units
      "from": -9.5, "to": 65.0,        # the extent along the street's own axis
      "width": 3.2                     # world units (the gap, scaled and clamped)
    }

The street grid is derived from the plots and the building footprints themselves (``_network``):
avenues in the free bands between the rows of plots, cross streets in the gaps between the plots
of each band, the lane grid between the buildings of every district, and branches carrying each
district to the grid and out to the ring road. Every street is footprint-aware — its line lies in
a gap and its extent stops at the roads it meets — so no street crosses a building volume and the
map reads as one connected city plan: the ring road as the arterial, the grid as its streets.

Cell (a building)::

    {
      "id": "h41",                     # "h{handle}" for live cells, "c{offset}" otherwise
      "handle": 41,                    # the live handle, or null (block / dead cell)
      "path": "mp_state_ctx.heap",     # object path, or null
      "name": "heap",                  # cell name, or null
      "depth": 2,                      # module depth of the path: root 0, a 1, a.b 2, ...
                                       # (0 where the cell carries no path). The scene flies
                                       # every cable on the sheet of its deepest endpoint
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
      "run_state": "RUNNING",          # the S1 run state word (below), or null
      "claim": null,                   # SEAM: live claim window (resolve/release), later stage
      "checksum": "ok",                # the S1 run checksum verdict (below), or null
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

Run words (docs/20, S1 — read from the cell, never guessed)
-----------------------------------------------------------

A run cell's payload is ``smp_run_payload`` (docs/20, S1): the concurrent words first (the run's
``state`` word is the first four bytes), the handoff metadata, the program state, and the FNV-1a
checksum over the program state (prime ``0x100000001B3``, basis ``0xcbf29ce484222325`` — the
checksum the dispatcher rewrites at every seal). The exporter reads those words **only** when a
tower's payload is exactly ``smp_run_payload`` (``RUN_WORDS_BYTES``): then the state word at
payload offset 0 and the checksum at ``RUN_CHECKSUM_AT`` over ``[RUN_PROGRAM_AT, RUN_CHECKSUM_AT)``
are the real words of S1, and

* ``cell.run_state`` is the state word named (``FREE/READY/RUNNING/PARKED/TERMINAL``; a word the
  state machine does not name stays null — the image carried a number, not a state);
* ``cell.checksum`` is the checksum verdict (``"ok"`` when the stored word equals the FNV-1a the
  exporter recomputes over the program state, ``"bad"`` otherwise).

Any other payload shape carries no run words this document may name: both fields stay null, and no
number is invented to fill their place.

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
      "fresh": true,                  # false = stale/refused handle -> red ghost cable
      "route": [{"x": 1.2, "z": 3.0}, ...]  # the cable's run: on the streets, footprint-aware
    }

A fresh edge names two cells of ``cells``. A stale edge names a handle the image no longer
resolves (the slot is dead or out of range) — ``to`` is null and the scene draws the cable as
a red ghost to a grave marker. Freshness is exactly ``Image.live(to_handle)``.

``route`` is the cable's polyline in world units (``_routes``): it leaves the source's lot, runs
the street network between the two buildings, and enters the target's lot — the shortest path
over the streets (``_rails``), corner-cut like a routed trace. A cable never crosses a building
volume that is not its own endpoint.

Ring and trams (the ready-ring of the execution policy, docs/23 §4.5 — the tram metaphor)
-----------------------------------------------------------------------------------------

    "ring": {"center": {"x": 69.4, "z": 9.0}, "radius": 84.2, "lanes": 1,
             "state": {"FREE": 0, "READY": 1, "RUNNING": 1, "PARKED": 1, "TERMINAL": 0},
             "occupied": 2,
             "path": [{"x": -8, "z": -8}, {"x": 88, "z": -8}, ...]}   # closed ring road
    "trams": [{"id": "t0", "run": "h30", "angle": 0.785398, "state": "RUNNING"}]

``ring.path`` is the ring road as a closed path hugging the city: the plots' bounds at
``RING_MARGIN``, corners chamfered at 45°, and — where the road would cut through a building's
keep-out (footprint plus the sidewalk clearance) — detoured around it, merging the neighbours it
touches so the road bends around a block as one shape. The road never runs through a building
volume. What the scene draws and what the tram units ride (``angle`` is the tram's slot on the
loop) is that path. The streets carry the same rule: avenues through the free bands, crosses and
lanes in the gaps between the footprints, branches from every district out to the network, and
every cable's ``route`` runs along those streets from its source's lot to its target's lot.

The tram units are the metaphor of docs/23 §4.5, and they are drawn from the ring data the image
really carries (the S1 run words, above): **a tram is one run cell sitting in a ring slot** — a run
whose state word is ``READY`` or ``RUNNING`` — and a ``PARKED`` run is off the road (a building's
quiet floor, not a circulating tram). ``ring.occupied`` counts exactly those slots and the tram
list is that count, one unit per slot; ``ring.state`` is the census of the run state words the
image carries (null when it carries none). ``tram.state`` is the run's own state word. The world
never draws a run on the road that the image does not put in a ring slot, and never draws a second
queue the machine does not have.

Metrics (the top resource bar of the HUD)::

    {
      "runs": 4,             # run cells of the image — available, wired
      "carriers": null,      # SEAM: SMP carrier count (runtime evidence, no cell carries it)
      "steps_per_s": null,   # SEAM: live step rate (runtime evidence)
      "claim_spins": null,   # SEAM: compactor claim spins (docs/20, S7; a world counter)
      "moved_cells": 0,      # cells carrying a C_FORWARD compaction record (docs/20, S8)
      "refusals": null,      # SEAM: state_refused counts (docs/20, S3; a world counter)
      "checksum_status": "3 ok"  # the S1 checksum verdicts ("3 ok", "2 ok · 1 bad"), or null
    }

A number appears only where the image really carries it: ``moved_cells`` counts the C_FORWARD
records the cell headers carry (a compaction's own trace), ``checksum_status`` sums the run
checksum verdicts of the S1 words. The counters a run of the machine keeps outside any cell
(``state_refused``, claim spins, the step rate, the carrier count) are runtime evidence the
exporter cannot read from an image — those slots stay null, honestly empty.

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

import bisect
import enum
import json
import math
import struct
from collections.abc import Iterable, Iterator, Mapping, Sequence
from itertools import pairwise
from typing import Any

from pymergetic.rxftk.core.arena import HANDLE_NONE, KIND_BLOCK, KIND_DEAD
from pymergetic.rxftk.core.kinds import Binding, Mobility, NodeKind, RefKind
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector

SCHEMA = "rxf-world-2"

POISON_BYTE = 0xA5  # S8: the vacated tail of a compaction pass is filled with 0xA5
POISON_RUN = 64  # a run shorter than this is ordinary bytes, not poison
VOID_TILE = 512  # a poison run longer than this becomes several void tiles

# The layout of the world, all in world units. The arena is a strip of bytes: one district per
# DISTRICT_SPAN bytes at BYTE_SCALE units per byte, laid out as city blocks (DISTRICT_COLS per
# row, rows flowing along Z). Buildings sit on a deterministic tile raster inside their district
# (offset order, row major). The grid tightens around content: a district whose bytes carry no
# cell is laid out as a gutter (GUTTER_WIDTH x GUTTER_DEPTH) and an all-gutter row shrinks, so
# the city blocks of used memory read as one connected city while the empty stretches of the
# arena compress between them. Byte order and district membership are law; only the layout
# scale flexes — nothing in the document invents facts the image does not carry.
DISTRICT_SPAN = 4096
BYTE_SCALE = 4.5 / 1024.0
DISTRICT_DEPTH = 26.0
DISTRICT_GAP = 8.0
DISTRICT_COLS = 4  # districts per row of the city-block layout
GUTTER_WIDTH = 5.0  # an empty district: a gutter between the city blocks of used memory
GUTTER_DEPTH = 9.0
GRID_STEP = 3.9
GRID_MARGIN = 2.4
RING_MARGIN = 4.0  # the ring road keeps a wide breath around the plots' bounds
RING_WIDTH = 5.6  # the arterial's road width (the ready-ring is one queue, one wide road)
STREET_WIDTH = 3.2  # nominal avenue width; the actual width fits its gap
LANE_WIDTH = 1.6  # nominal lane width (the streets between the buildings of a district)
LANE_MIN = 0.8  # a gap narrower than this is a lot line, not a street
CLEARANCE = 1.6  # road half-width: a building's keep-out grows this far beyond its footprint
JUNCTION_SNAP = 0.25  # streets are wide: near-miss ends still meet on the routing graph
RING_BEVEL = 2.0  # the ring's corners are cut this clean, and its detour corridors keep the room
FP_RATIO = 0.5  # a building's footprint never exceeds this fraction of its tile step
BLOCK_BIN = 2  # tiles per city-block bin edge (block > building > floors)

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

# The run words of docs/20 S1, read only at the exact smp_run_payload shape (the region weights
# above are its real byte counts): the state word is the payload's first four bytes, the checksum
# its last eight, and it covers the program state in between. The state machine's names (S1) and
# the checksum's FNV-1a (S8: prime 0x100000001B3, basis 0xcbf29ce484222325).
RUN_WORDS_BYTES = sum(weight for _, weight in RUN_PAYLOAD_REGIONS)
RUN_STATES = ("FREE", "READY", "RUNNING", "PARKED", "TERMINAL")
ON_ROAD = ("READY", "RUNNING")  # docs/23 §4.5: a tram is one run cell in a ring slot
FNV_PRIME = 0x100000001B3
FNV_BASIS = 0xCBF29CE484222325

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
STREET_KINDS = ("avenue", "cross", "lane", "branch")


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


def _district_cols(count: int) -> int:
    """The columns of the city-block plan: at least DISTRICT_COLS, and for a big arena as many
    as make the plan square in world units. A fixed width made a large arena a strip — four
    blocks across and hundreds of rows deep — so the rows grow with the city instead."""
    if count <= DISTRICT_COLS * DISTRICT_COLS:
        return DISTRICT_COLS
    span_x = DISTRICT_SPAN * BYTE_SCALE + DISTRICT_GAP
    span_z = DISTRICT_DEPTH + DISTRICT_GAP
    return max(DISTRICT_COLS, math.ceil(math.sqrt(count * span_z / span_x)))


def _districts(header: Mapping[str, Any], occupied: Iterable[int]) -> list[dict[str, Any]]:
    """The bands of the arena (the same three the memory map labels), the cells band split into
    districts of at most DISTRICT_SPAN bytes. A cell belongs to the district its offset starts in.
    The districts are laid out as city blocks: a square plan of _district_cols columns, rows
    flowing along Z, so the arena reads as one connected world instead of a strip. A district
    whose bytes carry no cell from `occupied` is a gutter between the blocks (and an all-gutter
    row shrinks): the grid tightens around content instead of stretching empty arena across the
    map."""
    body = int(header["body_size"])
    offsets = sorted(occupied)
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

    def holds(start: int, end: int) -> bool:
        at = bisect.bisect_left(offsets, start)
        return at < len(offsets) and offsets[at] < end

    cols = _district_cols(len(chunks))
    out: list[dict[str, Any]] = []
    z = 0.0
    for row_start in range(0, len(chunks), cols):
        row = chunks[row_start : row_start + cols]
        depths = [
            DISTRICT_DEPTH if holds(start, end) else GUTTER_DEPTH for _, _, start, end in row
        ]
        x = 0.0
        for i, ((band, name, start, end), depth) in enumerate(
            zip(row, depths, strict=True), start=row_start
        ):
            here = holds(start, end)
            width = max(6.0, (end - start) * BYTE_SCALE) if here else GUTTER_WIDTH
            out.append(
                {
                    "id": f"r{i}",
                    "name": name,
                    "band": band,
                    "start": start,
                    "end": end,
                    "plot": {"x": _round(x), "z": _round(z), "w": _round(width), "d": _round(depth)},
                    "cell_count": 0,
                    "empty": True,
                }
            )
            x += width + DISTRICT_GAP
        z += max(depths) + DISTRICT_GAP
    return out


def _region_of(regions: Sequence[dict[str, Any]], offset: int) -> dict[str, Any]:
    """The district an offset starts in. The bands are contiguous and sorted by start, so one
    lookup is a binary search — however many districts the arena grows into."""
    index = bisect.bisect_right(regions, offset, key=lambda region: int(region["start"])) - 1
    if index >= 0 and offset < int(regions[index]["end"]):
        return regions[index]
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


# ---- run words: the S1 state word and checksum of a real run-cell payload -------------------------


def fnv1a(data: bytes) -> int:
    """FNV-1a 64 over `data` — the checksum algorithm of ``smp_run_checksum`` (docs/20, S8)."""
    word = FNV_BASIS
    for byte in data:
        word = ((word ^ byte) * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return word


def _run_words(family: str, payload: bytes) -> tuple[str | None, str | None]:
    """The run words a cell's payload really carries: ``(run_state, checksum verdict)``.

    Read only when the payload is exactly ``smp_run_payload`` (``RUN_WORDS_BYTES``) on a run
    cell (family "tower"): the state word is then the first four bytes, the checksum the last
    eight, and it covers the program state between the handoff metadata and the checksum
    (docs/20, S1/S8). A state word the machine does not name, or any other payload shape,
    carries nothing this document may call a run word: both answers stay ``None``.
    """
    if family != "tower" or len(payload) != RUN_WORDS_BYTES:
        return None, None
    (state,) = struct.unpack_from("<I", payload, 0)
    run_state = RUN_STATES[state] if state < len(RUN_STATES) else None
    if run_state is None:
        return None, None  # not a run record the machine names: no run word, no verdict
    program_at = sum(weight for _, weight in RUN_PAYLOAD_REGIONS[:2])
    checksum_at = RUN_WORDS_BYTES - 8
    (stored,) = struct.unpack_from("<Q", payload, checksum_at)
    verdict = "ok" if stored == fnv1a(payload[program_at:checksum_at]) else "bad"
    return run_state, verdict


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


# ---- streets: the city's connected street network ----------------------------------------------------


def _bands_of(runs: Iterable[tuple[float, float]]) -> list[list[float]]:
    """Merge overlapping [low, high] runs into disjoint bands."""
    merged: list[list[float]] = []
    for low, high in sorted(runs):
        if merged and low <= merged[-1][1] + 1e-6:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return merged


def _street(
    kind: str, axis: str, at: float, start: float, end: float, width: float
) -> dict[str, Any]:
    return {
        "kind": kind,
        "axis": axis,
        "at": _round(at),
        "from": _round(min(start, end)),
        "to": _round(max(start, end)),
        "width": _round(width),
    }


def _network(
    regions: Sequence[Mapping[str, Any]],
    cells: Sequence[Mapping[str, Any]],
    ring_rect: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """The street network of the city, derived from the plots and the building footprints:
    one connected plan of

    * avenues along x in every globally free z band (no plot stands there at any x), running
      from the ring road to the ring road — the ring's branches into the city;
    * cross streets along z in the x gaps between the plots of each z band, spanning from
      avenue to avenue (and the outer bands run out to the ring road);
    * lanes along every gap between the footprint bands of a district (its street grid), and
      along the district's margins — so the streets run BETWEEN the buildings, never over them;
    * branches: every district's lanes continued outward to the nearest avenue, cross street or
      the ring — a branch road into every district.

    Every street is footprint-aware: its line lies in a gap of the footprint grid and its extent
    stops at the roads it meets, so no street ever crosses a building volume."""
    ring_x0, ring_z0, ring_x1, ring_z1 = ring_rect

    def width(gap: float) -> float:
        return min(2 * STREET_WIDTH, max(1.8, gap * 0.7))

    # the buildings of every district, bucketed once (not rescanned per district)
    in_region: dict[str, list[Mapping[str, Any]]] = {}
    for cell in cells:
        in_region.setdefault(str(cell["region"]), []).append(cell)

    out: list[dict[str, Any]] = []
    z_bands = _bands_of(
        (float(region["plot"]["z"]), float(region["plot"]["z"]) + float(region["plot"]["d"]))
        for region in regions
    )
    # avenues: the free z bands between the rows of plots
    for first, second in pairwise(z_bands):
        gap = second[0] - first[1]
        if gap >= 1.2:
            out.append(_street("avenue", "x", (first[1] + second[0]) / 2, ring_x0, ring_x1, width(gap)))
    # cross streets: the x gaps between the plots of each z band
    for index, (low, high) in enumerate(z_bands):
        here = [
            region
            for region in regions
            if float(region["plot"]["z"]) < high - 1e-6
            and float(region["plot"]["z"]) + float(region["plot"]["d"]) > low + 1e-6
        ]
        z_from = ring_z0 if index == 0 else (z_bands[index - 1][1] + low) / 2
        z_to = ring_z1 if index == len(z_bands) - 1 else (high + z_bands[index + 1][0]) / 2
        for first, second in pairwise(
            _bands_of((float(r["plot"]["x"]), float(r["plot"]["x"]) + float(r["plot"]["w"])) for r in here)
        ):
            gap = second[0] - first[1]
            if gap >= 1.2:
                out.append(_street("cross", "z", (first[1] + second[0]) / 2, z_from, z_to, width(gap)))
    # lanes: the street grid between the buildings of every district, and along its margins
    avenues = sorted(s["at"] for s in out if s["kind"] == "avenue")
    crosses = sorted(s["at"] for s in out if s["kind"] == "cross")
    for region in regions:
        here = in_region.get(str(region["id"]), [])
        if not here:
            continue
        plot = region["plot"]
        px, pz = float(plot["x"]), float(plot["z"])
        pw, pd = float(plot["w"]), float(plot["d"])
        region_lanes: list[dict[str, Any]] = []
        for axis in ("x", "z"):
            lo, span = (pz, pd) if axis == "x" else (px, pw)
            # the footprint bands across the district, perpendicular to the street's axis
            runs = [
                (
                    (float(cell["plot"]["z"]) - float(cell["footprint"]) / 2)
                    if axis == "x"
                    else (float(cell["plot"]["x"]) - float(cell["footprint"]) / 2),
                    (float(cell["plot"]["z"]) + float(cell["footprint"]) / 2)
                    if axis == "x"
                    else (float(cell["plot"]["x"]) + float(cell["footprint"]) / 2),
                )
                for cell in here
            ]
            bands = _bands_of(runs)
            edges = [(lo, bands[0][0])] + [
                (first[1], second[0]) for first, second in pairwise(bands)
            ] + [(bands[-1][1], lo + span)]
            for low, high in edges:
                gap = high - low
                if gap < LANE_MIN:
                    continue
                at = (low + high) / 2
                lane_width = min(2 * LANE_WIDTH, max(0.9, gap - 0.5))
                if axis == "x":
                    lane = _street("lane", "x", at, px + 0.2, px + pw - 0.2, lane_width)
                else:
                    lane = _street("lane", "z", at, pz + 0.2, pz + pd - 0.2, lane_width)
                region_lanes.append(lane)
        out.extend(region_lanes)
        # branches: the district's median lanes continued to the nearest avenue / cross / ring
        x_lanes = sorted(s["at"] for s in region_lanes if s["axis"] == "z")
        z_lanes = sorted(s["at"] for s in region_lanes if s["axis"] == "x")
        if x_lanes:
            at = min(x_lanes, key=lambda value: abs(value - (px + pw / 2)))
            lane_width = next(s["width"] for s in region_lanes if s["axis"] == "z" and s["at"] == at)
            north = next((value for value in avenues if value > pz + pd), ring_z1)
            south = next((value for value in reversed(avenues) if value < pz), ring_z0)
            out.append(_street("branch", "z", at, pz + pd, north, lane_width))
            out.append(_street("branch", "z", at, south, pz, lane_width))
        if z_lanes:
            at = min(z_lanes, key=lambda value: abs(value - (pz + pd / 2)))
            lane_width = next(s["width"] for s in region_lanes if s["axis"] == "x" and s["at"] == at)
            east = next((value for value in crosses if value > px + pw), ring_x1)
            west = next((value for value in reversed(crosses) if value < px), ring_x0)
            out.append(_street("branch", "x", at, px + pw, east, lane_width))
            out.append(_street("branch", "x", at, west, px, lane_width))
    return sorted(out, key=lambda street: (str(street["kind"]), str(street["axis"]), float(street["at"])))


def _blocks(
    cells: Sequence[Mapping[str, Any]], regions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """The city blocks: every BLOCK_BIN x BLOCK_BIN bin of a district tile raster that holds at
    least one building, as the massing the wide views read (block > building > floors). The
    block's plot is its bin's slot on the raster: raster-fixed, so a row whose buildings are
    all small keeps the row's extent (the buildings stay on their tiles, the slots keep the
    grid). Its height is the tallest building; its family is the most prominent member (tower >
    monument > block > utility > rubble)."""
    region_by_id = {str(region["id"]): region for region in regions}

    def slot(region_id: str, bx: int, bz: int) -> tuple[float, float, float, float]:
        """One bin's world rect: the tile edges it spans on its district's raster (the same
        mapping _tiles places buildings with, so block slots and building tiles are one grid)."""
        region = region_by_id[region_id]
        raster = region["tiles"]
        cols, rows = int(raster["cols"]), int(raster["rows"])
        step_x, step_z = float(raster["step_x"]), float(raster["step_z"])
        plot = region["plot"]
        x0 = float(plot["x"]) + (float(plot["w"]) - step_x * cols) / 2
        z0 = float(plot["z"]) + (float(plot["d"]) - step_z * rows) / 2
        return (
            x0 + bx * BLOCK_BIN * step_x,
            z0 + bz * BLOCK_BIN * step_z,
            x0 + min((bx + 1) * BLOCK_BIN, cols) * step_x,
            z0 + min((bz + 1) * BLOCK_BIN, rows) * step_z,
        )

    bins: dict[tuple[str, int, int], list[Mapping[str, Any]]] = {}
    for cell in cells:
        key = (
            str(cell["region"]),
            int(cell["tile"]["x"]) // BLOCK_BIN,
            int(cell["tile"]["z"]) // BLOCK_BIN,
        )
        bins.setdefault(key, []).append(cell)
    out: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(bins)):
        members = bins[key]
        x0, z0, x1, z1 = slot(*key)
        family = min((str(member["family"]) for member in members), key=FAMILIES.index)
        out.append(
            {
                "id": f"b{index}",
                "region": key[0],
                "plot": {
                    "x": _round(x0),
                    "z": _round(z0),
                    "w": _round(x1 - x0),
                    "d": _round(z1 - z0),
                },
                "height": _round(max(float(member["height"]) for member in members)),
                "count": len(members),
                "family": family,
            }
        )
    return out


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


def _keep_out(cell: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """A building's keep-out on the ground: its footprint plus the sidewalk a road keeps clear."""
    half = float(cell["footprint"]) / 2
    x, z = float(cell["plot"]["x"]), float(cell["plot"]["z"])
    return (x - half - CLEARANCE, z - half - CLEARANCE, x + half + CLEARANCE, z + half + CLEARANCE)


def _segment_hits(
    a: tuple[float, float], b: tuple[float, float], rect: tuple[float, float, float, float]
) -> tuple[float, float] | None:
    """The [t0, t1] parameter range where segment a->b crosses the axis-aligned rect, if any."""
    x0, z0, x1, z1 = rect
    t0, t1 = 0.0, 1.0
    for p, q, low, high in (
        (a[0], b[0] - a[0], x0, x1),
        (a[1], b[1] - a[1], z0, z1),
    ):
        if abs(q) < 1e-12:
            if p < low or p > high:
                return None
            continue
        ta, tb = (low - p) / q, (high - p) / q
        if ta > tb:
            ta, tb = tb, ta
        t0, t1 = max(t0, ta), min(t1, tb)
        if t1 <= t0:
            return None
    return (t0, t1)


def _merge_rects(
    rects: Sequence[tuple[float, float, float, float]], touch: float = 0.6
) -> list[tuple[float, float, float, float]]:
    """Union every keep-out that touches or overlaps its neighbours: a street bends around a
    block of buildings as one shape, not a zigzag between their individual corridors."""
    boxes = [list(rect) for rect in sorted(rects)]
    merged = True
    while merged:
        merged = False
        out: list[list[float]] = []
        # Neighbours come from a coarse grid of the touch-grown boxes: two boxes that can
        # merge share a cell, so the scan meets the few near boxes instead of pairing every
        # box with every box before it (17k keep-outs would square up otherwise).
        cell = max(1.0, 2.0 * touch)
        near: dict[tuple[int, int], list[int]] = {}

        def cells_of(box: Sequence[float], cell: float = cell) -> Iterator[tuple[int, int]]:
            for gx in range(
                math.floor((box[0] - touch) / cell), math.floor((box[2] + touch) / cell) + 1
            ):
                for gz in range(
                    math.floor((box[1] - touch) / cell), math.floor((box[3] + touch) / cell) + 1
                ):
                    yield (gx, gz)

        for box in boxes:
            candidates = sorted(
                {index for key in cells_of(box) for index in near.get(key, ())}
            )
            for index in candidates:
                other = out[index]
                apart = (
                    box[0] > other[2] + touch
                    or other[0] > box[2] + touch
                    or box[1] > other[3] + touch
                    or other[1] > box[3] + touch
                )
                if not apart:
                    other[0] = min(other[0], box[0])
                    other[1] = min(other[1], box[1])
                    other[2] = max(other[2], box[2])
                    other[3] = max(other[3], box[3])
                    merged = True
                    for key in cells_of(other):  # the grown box reaches further now
                        near.setdefault(key, []).append(index)
                    break
            else:
                out.append(box)
                index = len(out) - 1
                for key in cells_of(box):
                    near.setdefault(key, []).append(index)
        boxes = out
    return [(box[0], box[1], box[2], box[3]) for box in boxes]


def _detour(
    loop: Sequence[tuple[float, float]],
    rects: Sequence[tuple[float, float, float, float]],
    center: tuple[float, float],
    road_half: float,
) -> list[tuple[float, float]]:
    """Route the closed ring around every keep-out it would cut through: where the road enters a
    building's corridor (its keep-out plus the road's own half width), the path detours along the
    corridor's far side — outward, away from the city — so the road never runs through a building
    volume and reads as routing around the footprint, through the streetscape."""
    points = list(loop)
    for rect in sorted(rects):
        corridor = (
            rect[0] - road_half - RING_BEVEL - 0.3,
            rect[1] - road_half - RING_BEVEL - 0.3,
            rect[2] + road_half + RING_BEVEL + 0.3,
            rect[3] + road_half + RING_BEVEL + 0.3,
        )
        detect = (
            corridor[0] + 0.2,
            corridor[1] + 0.2,
            corridor[2] - 0.2,
            corridor[3] - 0.2,
        )
        for _ in range(2):  # repair passes: a detour may step into another corridor
            # start the walk at a point the corridor does not reach, so hits pair up linearly
            def outside(
                p: tuple[float, float], detect: tuple[float, float, float, float] = detect
            ) -> bool:
                return not (detect[0] <= p[0] <= detect[2] and detect[1] <= p[1] <= detect[3])

            start = next((i for i, p in enumerate(points) if outside(p)), None)
            if start is None or len(points) > 1200:
                break
            points = points[start:] + points[:start]
            hits: list[tuple[int, float, bool]] = []  # (segment, t, entering)
            for i in range(len(points)):
                a, b = points[i], points[(i + 1) % len(points)]
                span = math.hypot(b[0] - a[0], b[1] - a[1])
                hit = _segment_hits(a, b, detect) if span > 1e-9 else None
                if hit:
                    hits.append((i, hit[0], True))
                    hits.append((i, hit[1], False))
            if len(hits) < 2 or len(hits) % 2:
                break
            for pair in range(len(hits) // 2 - 1, -1, -1):
                enter_i, enter_t, _ = hits[2 * pair]
                exit_i, exit_t, _ = hits[2 * pair + 1]
                if exit_i < enter_i or (exit_i == enter_i and exit_t <= enter_t):
                    continue

                def lerp(
                    i: int, t: float, points: list[tuple[float, float]] = points
                ) -> tuple[float, float]:
                    a, b = points[i], points[(i + 1) % len(points)]
                    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

                ex = lerp(enter_i, enter_t)
                xi = lerp(exit_i, exit_t)

                # the detour hugs the corridor's boundary on the short side: the corners between
                # the entry and the exit on that side, and never re-enter the corridor
                width = corridor[2] - corridor[0]
                depth = corridor[3] - corridor[1]
                half = 2 * (width + depth)
                corners = [
                    ((corridor[0], corridor[1]), 0.0),
                    ((corridor[2], corridor[1]), width),
                    ((corridor[2], corridor[3]), width + depth),
                    ((corridor[0], corridor[3]), 2 * width + depth),
                ]

                def perimeter(
                    point: tuple[float, float],
                    corridor: tuple[float, float, float, float] = corridor,
                    width: float = width,
                    depth: float = depth,
                    half: float = half,
                ) -> float:
                    choices = (
                        (abs(point[1] - corridor[1]), point[0] - corridor[0]),
                        (abs(point[0] - corridor[2]), width + point[1] - corridor[1]),
                        (abs(point[1] - corridor[3]), width + depth + corridor[2] - point[0]),
                        (abs(point[0] - corridor[0]), 2 * width + depth + corridor[3] - point[1]),
                    )
                    return min(choices)[1] % half

                s_ex, s_xi = perimeter(ex), perimeter(xi)
                forward = [
                    (corner, s)
                    for corner, s in corners
                    if (s - s_ex) % half < (s_xi - s_ex) % half - 1e-9
                ]
                backward = [(corner, s) for corner, s in corners if (corner, s) not in forward]
                if len(forward) <= len(backward):
                    forward.sort(key=lambda item: (item[1] - s_ex) % half)
                    arc = [corner for corner, _ in forward]
                else:
                    backward.sort(key=lambda item: (s_ex - item[1]) % half)
                    arc = [corner for corner, _ in backward]
                tail = [] if exit_t >= 1 - 1e-9 else [xi]
                head = [] if enter_t <= 1e-9 else [ex]
                points = (
                    points[: enter_i + 1]
                    + head
                    + arc
                    + tail
                    + points[exit_i + 1 :]
                )
    return points


def _lean(points: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """The road's turns without its stutters: drop duplicate points, the near-collinear ones
    (a point its neighbours do not miss), and the micro legs (a corner shorter than a leg is
    noise, not a bend). Dropping a point moves the polyline by less than its leg — the detour
    corridors keep that room."""
    pts = [(x, z) for x, z in points]
    out: list[tuple[float, float]] = []
    for point in pts:
        if not out or math.hypot(point[0] - out[-1][0], point[1] - out[-1][1]) > 0.05:
            out.append(point)
    pts = out
    for _ in range(8):
        count = len(pts)
        if count <= 4:
            break
        dropped = False
        kept: list[tuple[float, float]] = []
        for index in range(count):
            previous, point, following = pts[index - 1], pts[index], pts[(index + 1) % count]
            leg = min(
                math.hypot(point[0] - previous[0], point[1] - previous[1]),
                math.hypot(following[0] - point[0], following[1] - point[1]),
            )
            segment = (following[0] - previous[0], following[1] - previous[1])
            length = math.hypot(*segment) or 1.0
            off = abs(
                segment[0] * (point[1] - previous[1]) - segment[1] * (point[0] - previous[0])
            ) / length
            if leg < 0.6 or off < 0.08:
                dropped = True
                continue
            kept.append(point)
        if not dropped or len(kept) < 4:
            break
        pts = kept
    return pts


def _chamfer_closed(
    points: Sequence[tuple[float, float]], radius: float
) -> list[tuple[float, float]]:
    """Cut every corner of a closed polyline clean at 45°, the way a ring road is chamfered:
    first the stutters go (``_lean``), then every real turn loses its tip to a bevel of an
    adaptive radius (never longer than the legs allow), and a near-straight run loses its
    point entirely — the path keeps its turns, and only its turns."""
    pts = _lean(points)
    out: list[tuple[float, float]] = []
    count = len(pts)
    for index in range(count):
        previous, point, following = pts[index - 1], pts[index], pts[(index + 1) % count]
        ux, uz = point[0] - previous[0], point[1] - previous[1]
        vx, vz = following[0] - point[0], following[1] - point[1]
        u_length = math.hypot(ux, uz) or 1.0
        v_length = math.hypot(vx, vz) or 1.0
        if (ux * vx + uz * vz) / (u_length * v_length) > math.cos(math.radians(12)):
            continue  # a straight run: the point is the line, not a bend
        r = min(radius, 0.45 * u_length, 0.45 * v_length)
        out.append((point[0] - (ux / u_length) * r, point[1] - (uz / u_length) * r))
        out.append((point[0] + (vx / v_length) * r, point[1] + (vz / v_length) * r))
    return out if len(out) >= 4 else pts


def _corner_cut(
    cells: Sequence[Mapping[str, Any]],
    ring_rect: tuple[float, float, float, float],
) -> float:
    """One uniform 45° corner cut for the ring's octagon: the largest cut (capped by the
    rectangle's size) whose diagonal keeps the road's width and the sidewalk clear of every
    building. A chamfer that outruns its corner slices the edge towers of a district and
    forces the ragged detours the ring must never show."""
    x0, z0, x1, z1 = ring_rect
    reach = RING_WIDTH / 2 + CLEARANCE
    cut = min(18.0, 0.34 * min(x1 - x0, z1 - z0))
    for corner in ((x0, z0), (x1, z0), (x1, z1), (x0, z1)):
        for cell in cells:
            u = abs(float(cell["plot"]["x"]) - corner[0])
            v = abs(float(cell["plot"]["z"]) - corner[1])
            cut = min(cut, u + v - math.sqrt(2) * (float(cell["footprint"]) / 2 + reach))
    return max(1.5, cut)


def _ring_path(
    regions: Sequence[Mapping[str, Any]],
    cells: Sequence[Mapping[str, Any]],
    ring_rect: tuple[float, float, float, float],
) -> list[dict[str, float]]:
    """The ring road as a closed path around the city: the plots' bounds at RING_MARGIN, corners
    chamfered at 45° (``_corner_cut``: one cut that clears every building), detoured around every
    building whose mass still reaches the road (``_detour``) — where the ring passes a tower it
    routes AROUND the footprint, never through it. On that path the tram units ride. Eight points
    before the detours, deterministic from the world bounds."""
    x0, z0, x1, z1 = ring_rect
    corner = _corner_cut(cells, ring_rect)
    basis = [
        (x0 + corner, z0),
        (x1 - corner, z0),
        (x1, z0 + corner),
        (x1, z1 - corner),
        (x1 - corner, z1),
        (x0 + corner, z1),
        (x0, z1 - corner),
        (x0, z0 + corner),
    ]
    rects = _merge_rects([_keep_out(cell) for cell in cells], touch=1.2)
    center = ((x0 + x1) / 2, (z0 + z1) / 2)
    routed = list(basis)
    for _ in range(5):  # until the road detours around every footprint it would cut
        if all(
            _segment_hits(first, second, rect) is None
            for rect in rects
            for first, second in zip(routed, routed[1:] + routed[:1])
        ):
            break
        routed = _detour(routed, rects, center, RING_WIDTH / 2)

    return [{"x": _round(x), "z": _round(z)} for x, z in _chamfer_closed(routed, RING_BEVEL)]


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


# ---- cables: routes on the street network --------------------------------------------------------------

GRAVE_REACH = 3.4  # a stale handle's grave stands this far beside its source


def _chamfer(points: Sequence[tuple[float, float]], radius: float) -> list[tuple[float, float]]:
    """Cut every corner of an open polyline at 45°, the way a PCB router does."""
    if len(points) < 3:
        return list(points)
    out = [points[0]]
    for i in range(1, len(points) - 1):
        previous, point, following = points[i - 1], points[i], points[i + 1]
        ux, uz = point[0] - previous[0], point[1] - previous[1]
        vx, vz = following[0] - point[0], following[1] - point[1]
        u_length = math.hypot(ux, uz) or 1.0
        v_length = math.hypot(vx, vz) or 1.0
        r = min(radius, u_length / 2, v_length / 2)
        out.append((point[0] - (ux / u_length) * r, point[1] - (uz / u_length) * r))
        out.append((point[0] + (vx / v_length) * r, point[1] + (vz / v_length) * r))
    out.append(points[-1])
    return out


def _rails(
    streets: Sequence[Mapping[str, Any]], ring_path: Sequence[Mapping[str, float]]
) -> list[tuple[str, float, float, float]]:
    """Every street centerline and every straight side of the ring road as routing rails, each
    span clipped to the city bounds so any two crossing rails really do meet."""
    rails = [
        (str(street["axis"]), float(street["at"]), float(street["from"]), float(street["to"]))
        for street in streets
    ]
    for i, point in enumerate(ring_path):
        a, b = point, ring_path[(i + 1) % len(ring_path)]
        if abs(float(a["x"]) - float(b["x"])) < 1e-6:
            rails.append(("z", float(a["x"]), min(float(a["z"]), float(b["z"])), max(float(a["z"]), float(b["z"]))))
        elif abs(float(a["z"]) - float(b["z"])) < 1e-6:
            rails.append(("x", float(a["z"]), min(float(a["x"]), float(b["x"])), max(float(a["x"]), float(b["x"]))))
    x_lo = min(min(r[2], r[3]) for r in rails if r[0] == "x")
    x_hi = max(max(r[2], r[3]) for r in rails if r[0] == "x")
    z_lo = min(min(r[2], r[3]) for r in rails if r[0] == "z")
    z_hi = max(max(r[2], r[3]) for r in rails if r[0] == "z")
    clipped: list[tuple[str, float, float, float]] = []
    for axis, at, low, high in rails:
        lo, hi = (z_lo, z_hi) if axis == "z" else (x_lo, x_hi)
        clipped.append((axis, at, max(low, lo), min(high, hi)))
    return [rail for rail in clipped if rail[2] < rail[3]]


def _on_rail(
    rail: tuple[str, float, float, float], x: float, z: float, tolerance: float = 1e-6
) -> bool:
    axis, at, low, high = rail
    if axis == "x":
        return abs(z - at) < tolerance and low - tolerance <= x <= high + tolerance
    return abs(x - at) < tolerance and low - tolerance <= z <= high + tolerance


def _project_rail(
    rails: Sequence[tuple[str, float, float, float]],
    x: float,
    z: float,
    grid: Mapping[tuple[int, int], list[int]] | None = None,
) -> tuple[float, float, int]:
    """The nearest point on the rail network to (x, z): (px, pz, rail index). With a rail grid
    (``_rail_grid``) only the buckets around the point are read — the same nearest rail, and the
    same tie for tie, as the scan of every rail."""
    if not rails:
        return (x, z, -1)
    if grid is None:
        scan: Iterable[int] = range(len(rails))
    else:
        scan = _near_rails(rails, grid, x, z)
    best: tuple[float, float, int] = (x, z, -1)
    best_squared = math.inf
    for r in scan:
        axis, at, low, high = rails[r]
        if axis == "x":
            px, pz = min(max(x, low), high), at
        else:
            px, pz = at, min(max(z, low), high)
        squared = (px - x) ** 2 + (pz - z) ** 2
        if squared < best_squared - 1e-12:
            best_squared = squared
            best = (px, pz, r)
    return best


PROJECT_BIN = 24.0  # world units per bucket of the nearest-rail lookup


def _rail_grid(
    rails: Sequence[tuple[str, float, float, float]]
) -> dict[tuple[int, int], list[int]]:
    """Every rail in the ground buckets its span covers, so a point can meet the few rails
    near it instead of the whole network."""
    bins: dict[tuple[int, int], list[int]] = {}
    for r, (axis, at, low, high) in enumerate(rails):
        x0, x1 = (low, high) if axis == "x" else (at, at)
        z0, z1 = (at, at) if axis == "x" else (low, high)
        for gx in range(math.floor(x0 / PROJECT_BIN), math.floor(x1 / PROJECT_BIN) + 1):
            for gz in range(math.floor(z0 / PROJECT_BIN), math.floor(z1 / PROJECT_BIN) + 1):
                bins.setdefault((gx, gz), []).append(r)
    return bins


def _near_rails(
    rails: Sequence[tuple[str, float, float, float]],
    grid: Mapping[tuple[int, int], list[int]],
    x: float,
    z: float,
) -> list[int]:
    """The rails that could be nearest to (x, z): ring after ring of buckets around the point
    until the best rail found is nearer than any bucket left behind."""
    home = (math.floor(x / PROJECT_BIN), math.floor(z / PROJECT_BIN))
    found: set[int] = set()
    out: list[int] = []
    best_squared = math.inf
    ring = 0
    while True:
        for gx in range(home[0] - ring, home[0] + ring + 1):
            for gz in range(home[1] - ring, home[1] + ring + 1):
                if ring and max(abs(gx - home[0]), abs(gz - home[1])) != ring:
                    continue
                for r in grid.get((gx, gz), ()):
                    if r in found:
                        continue
                    found.add(r)
                    out.append(r)
                    axis, at, low, high = rails[r]
                    if axis == "x":
                        px, pz = min(max(x, low), high), at
                    else:
                        px, pz = at, min(max(z, low), high)
                    squared = (px - x) ** 2 + (pz - z) ** 2
                    best_squared = min(best_squared, squared)
        ring += 1
        # a rail in a bucket the search has not read lies at least this far out
        if out and (max(0, ring - 1) * PROJECT_BIN) ** 2 > best_squared + 1e-9:
            break
    return out


def _rail_junction_pairs(
    rails: Sequence[tuple[str, float, float, float]]
) -> list[tuple[int, int]]:
    """Every rail pair that can share a junction, found through buckets instead of every pair
    of the network: collinear rails within ``JUNCTION_SNAP`` of each other, and crossing / T
    pairs whose meeting point lies on both spans. The pairs come back in (i, j) order, so the
    junction nodes number exactly as the scan of every pair numbered them."""
    snap = JUNCTION_SNAP
    pairs: set[tuple[int, int]] = set()
    same: dict[str, list[tuple[float, int]]] = {"x": [], "z": []}
    cross: dict[str, list[tuple[int, float, float, float]]] = {"x": [], "z": []}
    for i, (axis, at, low, high) in enumerate(rails):
        same[axis].append((at, i))
        cross[axis].append((i, at, low, high))
    # collinear: only rails within snap of each other can meet end to end
    step = max(2.0 * snap, 1e-9)
    for axis in ("x", "z"):
        entries = sorted(same[axis])
        bins: dict[int, list[int]] = {}
        for at, i in entries:
            bins.setdefault(math.floor(at / step), []).append(i)
        for at, i in entries:
            home = math.floor(at / step)
            for cell in (home - 1, home, home + 1):
                for j in bins.get(cell, ()):
                    if j > i and abs(rails[j][1] - at) <= snap:
                        pairs.add((i, j))
    # crossing and T: the meeting point is (a rail's at) on the other's span, both ways
    width = max(8.0, 4.0 * snap)
    index: dict[int, list[int]] = {}
    for j, at, low, high in cross["z"]:
        for cell in range(math.floor((low - snap) / width), math.floor((high + snap) / width) + 1):
            index.setdefault(cell, []).append(j)
    for i, at, low, high in cross["x"]:
        for cell in range(math.floor((low - snap) / width), math.floor((high + snap) / width) + 1):
            for j in index.get(cell, ()):
                _, bat, blow, bhigh = rails[j]
                if low - snap <= bat <= high + snap and blow - snap <= at <= bhigh + snap:
                    pairs.add((i, j) if i < j else (j, i))
    return sorted(pairs)


def _routes(
    cells: Sequence[Mapping[str, Any]],
    edges: list[dict[str, Any]],
    streets: Sequence[Mapping[str, Any]],
    ring_path: Sequence[Mapping[str, float]],
) -> None:
    """Give every cable its route: out of the source's lot, the street network across the city,
    into the target's lot — along the two rails of the endpoints and the crossings between them
    (at most two turns), corner-cut. A cable runs on the streets; it never crosses a building
    volume that is not its own endpoint."""
    by_id = {str(cell["id"]): cell for cell in cells}
    rails = _rails(streets, ring_path)

    # the crossing table: which rails meet and where. A cable then routes rail to rail — along
    # its own street, turning at crossings at most twice, into the target's street. The node
    # graph this replaces answered the same question with a shortest-path search per cable;
    # the crossings were what that search was really looking for.
    meet: dict[tuple[int, int], tuple[float, float]] = {}
    crossings_of: dict[int, set[int]] = {}
    for i, j in _rail_junction_pairs(rails):
        a, b = rails[i], rails[j]
        crossings_of.setdefault(i, set()).add(j)
        crossings_of.setdefault(j, set()).add(i)
        if a[0] == b[0]:
            # collinear streets meet end to end: a branch continues its lane out of the district
            if abs(a[1] - b[1]) > JUNCTION_SNAP:
                continue
            for first, second in ((a, b), (b, a)):
                for at in (first[2], first[3]):
                    if second[2] - JUNCTION_SNAP <= at <= second[3] + JUNCTION_SNAP:
                        corner = (at, first[1]) if first[0] == "x" else (first[1], at)
                        meet.setdefault((i, j), corner)
            continue
        # crossing and T junctions both node: where a rail's end lies on another rail's span,
        # the corner is shared or the graph fragments into islands and nothing routes
        x, z = (b[1], a[1]) if a[0] == "x" else (a[1], b[1])
        if _on_rail(a, x, z, JUNCTION_SNAP) and _on_rail(b, x, z, JUNCTION_SNAP):
            meet.setdefault((i, j), (x, z))
        for first, second in ((a, b), (b, a)):
            for at in (first[2], first[3]):
                corner = (at, first[1]) if first[0] == "x" else (first[1], at)
                if _on_rail(second, corner[0], corner[1], JUNCTION_SNAP):
                    meet.setdefault((i, j), corner)

    grid = _rail_grid(rails)
    plugs: dict[str, tuple[float, float, int]] = {
        str(cell["id"]): _project_rail(rails, float(cell["plot"]["x"]), float(cell["plot"]["z"]), grid)
        for cell in cells
    }

    def hop_path(
        s_rail: int, s: tuple[float, float], t_rail: int, t: tuple[float, float]
    ) -> list[tuple[float, float]] | None:
        """The street path between two plugs: the run on their own rails and the crossings
        between, at most two turns. None: the rails do not meet in two turns and the caller
        falls back to the direct run."""
        if s_rail < 0 or t_rail < 0:
            return None
        if s_rail == t_rail:
            return [s, t]
        direct = meet.get((s_rail, t_rail)) or meet.get((t_rail, s_rail))
        if direct is not None:
            return [s, direct, t]
        best: tuple[float, tuple[float, float], tuple[float, float]] | None = None
        firsts, seconds = crossings_of.get(s_rail), crossings_of.get(t_rail)
        shared = firsts & seconds if firsts and seconds else ()
        for m in sorted(shared):
            first = meet.get((s_rail, m)) or meet.get((m, s_rail))
            second = meet.get((m, t_rail)) or meet.get((t_rail, m))
            if first is None or second is None:
                continue
            length = math.dist(s, first) + math.dist(first, second) + math.dist(second, t)
            if best is None or length < best[0] - 1e-12:
                best = (length, first, second)
        return None if best is None else [s, best[1], best[2], t]

    def grave(source: Mapping[str, Any], handle: int) -> tuple[float, float]:
        mixed = (int(handle) * 2654435761) % 4294967296
        angle = (mixed / 4294967296.0) * 2 * math.pi
        return (
            float(source["plot"]["x"]) + math.cos(angle) * GRAVE_REACH,
            float(source["plot"]["z"]) + math.sin(angle) * GRAVE_REACH,
        )

    routed: dict[tuple[str, str], list[tuple[float, float]] | None] = {}
    for edge in edges:
        source = by_id.get(str(edge["from"]))
        if source is None:
            edge["route"] = []
            continue
        start = (float(source["plot"]["x"]), float(source["plot"]["z"]))
        target = by_id.get(str(edge["to"])) if edge["to"] else None
        if target is not None:
            finish = (float(target["plot"]["x"]), float(target["plot"]["z"]))
            key = (str(source["id"]), str(target["id"]))
            if key not in routed:
                s, t = plugs[key[0]], plugs[key[1]]
                routed[key] = hop_path(s[2], (s[0], s[1]), t[2], (t[0], t[1]))
            rail_path = routed[key]
            path = [start] + (rail_path or []) + [finish]
        else:
            path = [start, grave(source, int(edge["to_handle"]))]
        if len(path) < 3:  # no rail network (a bare ground image): the direct Manhattan run
            middle = round((start[0] + path[-1][0]) / 2 / GRID_STEP) * GRID_STEP
            path = [start, (middle, start[1]), (middle, path[-1][1]), path[-1]]
        straight = [path[0]]
        for point in path[1:]:
            if abs(point[0] - straight[-1][0]) + abs(point[1] - straight[-1][1]) > 1e-9:
                straight.append(point)
        edge["route"] = [
            {"x": _round(x), "z": _round(z)} for x, z in _chamfer(straight, 0.8)
        ]


# ---- the document ------------------------------------------------------------------------------------


def export_world(inspector: Inspector) -> dict[str, Any]:
    """The scene input of one image, deterministic: the same image always yields the same document."""
    image = inspector.image
    header = inspector.header()
    stream = inspector.cells()
    body = int(header["body_size"])
    raw = image.to_bytes()

    regions = _districts(header, (int(cell["offset"]) for cell in stream))
    by_handle: dict[int, dict[str, Any]] = {
        int(cell["handle"]): cell for cell in stream if cell["handle"] is not None
    }
    # One walk of the stream: every cell's district, counted and bucketed for the placement
    # pass below (the buckets replace a rescan of the stream per district).
    in_region: dict[str, list[dict[str, Any]]] = {}
    for cell in stream:
        region = _region_of(regions, int(cell["offset"]))
        region["cell_count"] = int(region["cell_count"]) + 1
        region["empty"] = False
        in_region.setdefault(str(region["id"]), []).append(cell)

    poison_runs = _poison_runs(raw, body)
    poisoned_at = _poison_hosts(stream, poison_runs)

    # One building per district, offset order, on the district's tile raster.
    placements: dict[int, tuple[float, float, int, int]] = {}
    for region in regions:
        here = sorted(
            in_region.get(str(region["id"]), ()),
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
            payload_bytes = b""
            identifier = f"c{offset}"
        else:
            handle = int(handle)
            got = image.get(handle)
            family = _family(kind, handle, int(got["parent"]))
            name = got["name"] or None
            path = got["path"]
            state = _state(int(cell["flags"]))
            payload_bytes = got["payload"]
            identifier = f"h{handle}"
            cell_id[handle] = identifier
        run_state, run_checksum = _run_words(family, payload_bytes)
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
            footprint, _round(FP_RATIO * min(float(raster["step_x"]), float(raster["step_z"])))
        )
        poisoned = offset in poisoned_at
        cells.append(
            {
                "id": identifier,
                "handle": None if handle is None else int(handle),
                "path": path,
                "name": name,
                "depth": 0 if not path else path.count(".") + 1,
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
                "run_state": run_state,
                "claim": None,
                "checksum": run_checksum,
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
    for start, length in poison_runs:
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
    ring_rect = (
        _round(min(float(region["plot"]["x"]) for region in regions) - RING_MARGIN),
        _round(min(float(region["plot"]["z"]) for region in regions) - RING_MARGIN),
        _round(
            max(float(region["plot"]["x"]) + float(region["plot"]["w"]) for region in regions)
            + RING_MARGIN
        ),
        _round(
            max(float(region["plot"]["z"]) + float(region["plot"]["d"]) for region in regions)
            + RING_MARGIN
        ),
    )
    ring_path = _ring_path(regions, cells, ring_rect)
    streets = _network(regions, cells, ring_rect)
    _routes(cells, edges, streets, ring_path)
    # docs/23 §4.5: a tram is one run cell sitting in a ring slot (READY/RUNNING) and a PARKED run
    # is off the road. The units are the ring's occupied slots — the run state words the image
    # really carries — never one per tower and never a run the image does not place in a slot.
    on_road = sorted(
        (cell for cell in towers if cell["run_state"] in ON_ROAD),
        key=lambda cell: int(cell["offset"]),
    )
    trams = [
        {
            "id": f"t{i}",
            "run": str(cell["id"]),
            "angle": _round(2 * math.pi * (i + 0.5) / max(1, len(on_road))),
            "state": str(cell["run_state"]),
        }
        for i, cell in enumerate(on_road)
    ]
    carried = [str(cell["run_state"]) for cell in towers if cell["run_state"] is not None]
    ring_state = {word: carried.count(word) for word in RUN_STATES} if carried else None
    verdicts = [str(cell["checksum"]) for cell in cells if cell["checksum"] is not None]
    if verdicts:
        bad = verdicts.count("bad")
        checksum_status = f"{len(verdicts) - bad} ok" + (f" · {bad} bad" if bad else "")
    else:
        checksum_status = None

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
            "district_cols": _district_cols(len(regions)),
            "byte_scale": _round(BYTE_SCALE),
            "street_width": _round(STREET_WIDTH),
        },
        "regions": regions,
        "cells": cells,
        "blocks": _blocks(cells, regions),
        "voids": voids,
        "edges": edges,
        "streets": streets,
        "ring": {
            "center": center,
            "radius": _round(0.5 * math.hypot(span_x, span_z) + RING_MARGIN),
            "lanes": 1,
            "state": ring_state,
            "occupied": len(trams),
            "path": ring_path,
        },
        "trams": trams,
        "carriers": [],
        "metrics": {
            "runs": len(towers),
            "carriers": None,
            "steps_per_s": None,
            "claim_spins": None,
            "moved_cells": sum(1 for cell in cells if int(cell["forward"])),
            "refusals": None,
            "checksum_status": checksum_status,
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
    "blocks",
    "voids",
    "edges",
    "streets",
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
    _need(scene["grid"], ("step", "margin", "district_span", "district_depth", "district_gap", "district_cols", "byte_scale", "street_width"), "grid")
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
                "id", "handle", "path", "name", "depth", "kind", "family", "offset", "size",
                "payload_size", "generation", "region", "plot", "tile", "footprint", "height",
                "state", "run_state", "claim", "checksum", "poisoned", "forward", "index",
                "ref_count", "anatomy",
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
        if not (isinstance(cell["depth"], int) and int(cell["depth"]) >= 0):
            raise SchemaError(f"cell {cell['id']!r}: depth {cell['depth']!r} is not a level")
        _need(cell["plot"], ("x", "z"), f"cell {cell['id']!r} plot")
        _need(cell["tile"], ("x", "z"), f"cell {cell['id']!r} tile")
        raster = regions_by_id[str(cell["region"])]["tiles"]
        if not (
            0 <= int(cell["tile"]["x"]) < int(raster["cols"])
            and 0 <= int(cell["tile"]["z"]) < int(raster["rows"])
        ):
            raise SchemaError(f"cell {cell['id']!r}: tile is off the district raster")
        if cell["run_state"] is not None and cell["run_state"] not in RUN_STATES:
            raise SchemaError(f"cell {cell['id']!r}: run state {cell['run_state']!r}")
        if cell["checksum"] is not None and cell["checksum"] not in ("ok", "bad"):
            raise SchemaError(f"cell {cell['id']!r}: checksum verdict {cell['checksum']!r}")
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

    block_ids: set[str] = set()
    for block in scene["blocks"]:
        _need(block, ("id", "region", "plot", "height", "count", "family"), "block")
        if block["id"] in block_ids:
            raise SchemaError(f"block {block['id']!r}: duplicate id")
        block_ids.add(block["id"])
        if block["region"] not in region_ids:
            raise SchemaError(f"block {block['id']!r}: unknown region {block['region']!r}")
        if block["family"] not in FAMILIES:
            raise SchemaError(f"block {block['id']!r}: family {block['family']!r}")
        _need(block["plot"], ("x", "z", "w", "d"), f"block {block['id']!r} plot")
        if int(block["count"]) < 1 or float(block["height"]) <= 0:
            raise SchemaError(f"block {block['id']!r}: massing with no buildings")
        if not (float(block["plot"]["w"]) > 0 and float(block["plot"]["d"]) > 0):
            raise SchemaError(f"block {block['id']!r}: plot has no area")

    for street in scene["streets"]:
        _need(street, ("kind", "axis", "at", "from", "to", "width"), "street")
        if street["kind"] not in STREET_KINDS:
            raise SchemaError(f"street: kind {street['kind']!r}")
        if street["axis"] not in ("x", "z"):
            raise SchemaError(f"street: axis {street['axis']!r}")
        if float(street["width"]) <= 0 or not float(street["from"]) < float(street["to"]):
            raise SchemaError("street: no extent")

    edge_ids: set[str] = set()
    for edge in scene["edges"]:
        _need(
            edge,
            ("id", "from", "to", "from_handle", "to_handle", "how", "kind", "binding",
             "index", "field", "fresh", "route"),
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
        if not isinstance(edge["route"], list) or len(edge["route"]) < 2:
            raise SchemaError(f"edge {edge['id']!r}: route is not a path of points")
        for point in edge["route"]:
            _need(point, ("x", "z"), f"edge {edge['id']!r} route point")

    ring = scene["ring"]
    if ring is not None:
        _need(ring, ("center", "radius", "lanes", "state", "occupied", "path"), "ring")
        _need(ring["center"], ("x", "z"), "ring center")
        if not isinstance(ring["path"], list) or len(ring["path"]) < 4:
            raise SchemaError("ring: path is not a closed path of points")
        for point in ring["path"]:
            _need(point, ("x", "z"), "ring path point")
    for tram in scene["trams"]:
        _need(tram, ("id", "run", "angle", "state"), "tram")
        if tram["run"] not in cell_ids:
            raise SchemaError(f"tram {tram['id']!r}: unknown run {tram['run']!r}")
        if tram["state"] not in ON_ROAD:
            raise SchemaError(f"tram {tram['id']!r}: state {tram['state']!r} is off the road")
    if ring is not None and int(ring["occupied"]) != len(scene["trams"]):
        raise SchemaError("ring: the occupied slots and the tram count disagree")
    if not isinstance(scene["carriers"], list):
        raise SchemaError("scene: carriers is not a list")
    _need(scene["metrics"], _REQUIRED_METRICS, "metrics")
    for count in ("cells", "edges", "stale_edges", "voids"):
        if count not in scene["counts"]:
            raise SchemaError(f"counts: missing {count!r}")
