"""Read-only views of an RXFB 6 image for the inspector (no web framework in here)."""

from __future__ import annotations

import enum
import struct
from bisect import bisect_right
from typing import Any
from urllib.parse import quote

from pymergetic.rxftk.core.arena import (
    BLOCK_INDEX,
    BLOCK_NAMES,
    CELL_ALIGN,
    CELL_SIZE,
    HANDLE_NONE,
    HANDLE_ROOT,
    KIND_BLOCK,
    KIND_DEAD,
)
from pymergetic.rxftk.core.kinds import Binding, Mobility, NodeKind, OwnerKind, RefKind
from pymergetic.rxftk.core.object import Image


def _word(words: type[enum.IntEnum], value: int) -> str:
    """The name of a value of one of the core enums; a value the core does not know stays a number."""
    try:
        return words(value).name
    except ValueError:
        return str(value)


def _state_word(names: tuple[str, ...], value: int) -> str:
    """The name of a state word of ground.h (the core keeps these bits unnamed); an unknown value stays a number."""
    return names[value] if value < len(names) else str(value)


_NONE = HANDLE_NONE
REFERRER_LIMIT = 200
HEX_LIMIT = 4096  # payload bytes a hex dump shows before it is truncated

# The state words of ground.h, six bits: provenance 1, disposition 2, cleanliness 1, mobility 2.
_PROVENANCE = ("Runtime", "Image")
_DISPOSITION = ("Retain", "Transient", "Tombstone")
_CLEANLINESS = ("Clean", "Dirty")

# The Model documentation (templates/model.html, served at /model): every anchor named here must
# exist as an id there (tests/test_model_doc.py pins the two together).
_MODEL = "/model"


def _model_link(anchor: str, label: str, cite: str, why: str) -> dict[str, str]:
    return {"anchor": anchor, "label": label, "cite": cite, "why": why}


def _model_links(
    path: str,
    handle: int,
    kind: str,
    parent_handle: int | None,
    generation: int,
    forward: int,
) -> list[dict[str, str]]:
    """Which sections of the Model documentation explain this cell.

    The first link is the cell's face in the model (run cell, object cell, named cell, channel
    frame); the rest follow from the cell's words (a nonzero generation is a stale name waiting to
    happen, a nonzero `forward` is a compaction record). Every link carries the docs/20 section
    it is grounded in."""
    here = f"{_MODEL}?path={quote(path, safe='')}#"
    if kind in ("Fn", "Code"):
        face = _model_link(
            "run-cells",
            "Run cell face — the program a run cell carries",
            "S1 · S6",
            "a Function or Code cell is what a run cell executes between two safe points",
        )
    elif handle == HANDLE_ROOT or parent_handle == HANDLE_ROOT or kind in ("Module", "Namespace"):
        face = _model_link(
            "named-cells",
            "Named cell — one name under the root",
            "S3 · S11",
            "names under the root are the named cells (mp_state_ctx, qstr pools, the heap)",
        )
    elif kind == "Channel":
        face = _model_link(
            "park-wake",
            "Channel frame — where two runs wait on each other",
            "S4",
            "a channel frame is a cell the pair resolves and releases around every park",
        )
    else:
        face = _model_link(
            "object-cells",
            "Object cell — REPR A and the Python-heap face",
            "S11",
            "a heap object is an arena cell named by handle, never by address",
        )
    links = [
        face,
        _model_link(
            "cell-anatomy",
            "Cell anatomy — the 64-byte header behind these words",
            "docs/17",
            "each row of the Cell card is one header word at its real offset",
        ),
        _model_link(
            "handles",
            f"How this cell is named — index {int(handle)} | generation {int(generation)}",
            "S7",
            "runs hold handles; an address is valid for exactly one claim window",
        ),
        _model_link(
            "step-loop",
            "Run cells at work — the step that touches this cell",
            "S1 · S5",
            "resolve, work, rewrite the checksum, release — then the safe point",
        ),
    ]
    if int(generation) > 0:
        links.append(
            _model_link(
                "aba",
                f"Generation {int(generation)} — this slot has had earlier lives",
                "S3 · S7",
                "a stale handle is refused at the pop and never names the slot's new cell",
            )
        )
    if int(forward):
        links.append(
            _model_link(
                "compaction",
                "C_FORWARD — this cell slid and its old place records where",
                "S8",
                "the forwarding word is written before the copy and travels with the cell",
            )
        )
    for entry in links:
        entry["href"] = here + str(entry["anchor"])
    return links


def _cell_kind(cell: dict[str, Any]) -> str:
    """What a cell of the stream is: an object kind, a block name, or Dead."""
    kind = int(cell["kind"])
    if kind == KIND_BLOCK:
        return {BLOCK_INDEX: "Index", BLOCK_NAMES: "Names"}.get(int(cell["block_kind"]), "Block")
    if kind == KIND_DEAD:
        return "Dead"
    return _word(NodeKind, kind)


class Inspector:
    """Answers the questions of the inspector from one image."""

    def __init__(self, image: Image) -> None:
        self.image = image
        self._referrers: dict[int, list[tuple[int, str]]] | None = None
        self._slots: dict[int, list[int]] = {}
        self._uses: tuple[dict[int, list[int]], dict[int, list[int]]] | None = None
        self._cells: list[dict[str, Any]] | None = None
        self._cell_by_handle: dict[int, dict[str, Any]] | None = None

    # ---- navigation ----------------------------------------------------------------------------

    def handle(self, path: str) -> int:
        if path in ("", "."):
            return HANDLE_ROOT
        handle = self.image.resolve(path)
        if handle is None:
            raise KeyError(path)
        return handle

    def info(self) -> dict[str, Any]:
        image = self.image
        issues = image.check()
        live = sum(1 for h in range(image.handle_count) if image.live(h))
        return {
            "objects": live,
            "handles": image.handle_count,
            "body_size": image.body_size,
            "digest": image.digest(),
            "delta_digest": image.delta_digest(),
            "entry": None if image.entry is None else image.path(image.entry),
            "issues": [{"path": p, "message": m} for p, m in issues[:200]],
            "issue_count": len(issues),
        }

    def members(self, handle: int) -> list[dict[str, Any]]:
        """The relations the type of the object declares (Children, Inverse), each list in order."""
        image = self.image
        return [
            {
                "field": image.get(field)["name"],
                "type": image.type_name(items[0]) if items else None,
                "count": len(items),
                "paths": [image.path(h) for h in items[:REFERRER_LIMIT]],
            }
            for field, items in image.members(handle)
        ]

    def children(self, path: str, offset: int = 0, limit: int = 500) -> dict[str, Any]:
        image = self.image
        handles = image.ordered(image.children(self.handle(path)))
        rows: list[dict[str, Any]] = []
        for handle in handles[offset : offset + limit]:
            got = image.get(handle)
            rows.append(
                {
                    "name": got["name"],
                    "path": got["path"],
                    "type": image.type_name(handle),
                    "kind": _word(NodeKind, got["kind"]),
                    "children": len(image.children(handle)),
                }
            )
        return {"path": path, "total": len(handles), "offset": offset, "items": rows}

    def search(self, text: str, limit: int = 100) -> list[dict[str, Any]]:
        needle = text.lower()
        out: list[dict[str, Any]] = []
        for handle in range(self.image.handle_count):
            if not self.image.live(handle) or handle == HANDLE_ROOT:
                continue
            path = self.image.path(handle)
            if needle in path.rsplit(".", 1)[-1].lower():
                out.append({"path": path, "type": self.image.type_name(handle)})
                if len(out) >= limit:
                    break
        return out

    # ---- one object ----------------------------------------------------------------------------

    def _reference_slots(self, type_handle: int) -> list[int]:
        if type_handle not in self._slots:
            self._slots[type_handle] = (
                [
                    leaf["offset"]
                    for leaf in self.image.layout(type_handle)["leaves"]
                    if leaf["reference"]
                ]
                if type_handle != _NONE and self.image.type_name(type_handle) == "Type"
                else []
            )
        return self._slots[type_handle]

    def _targets(self, handle: int, got: dict[str, Any]) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for target, role, _, _, _ in got["refs"]:
            if target != _NONE:
                found.append((target, "ref"))
            if role is not None:
                found.append((role, "role"))
        for offset in self._reference_slots(got["type"]):
            (target,) = struct.unpack_from("<Q", got["payload"], offset)
            if target != _NONE:
                found.append((target, "field"))
        return found

    def _alias_uses(self) -> tuple[dict[int, list[int]], dict[int, list[int]]]:
        """(alias -> the objects written through it, object -> the aliases it is written through)."""
        if self._uses is None:
            users: dict[int, list[int]] = {}
            aliases: dict[int, list[int]] = {}
            for alias, user in self.image.alias_uses():
                users.setdefault(alias, []).append(user)
                aliases.setdefault(user, []).append(alias)
            self._uses = (users, aliases)
        return self._uses

    def referrers_of(self, handle: int) -> list[dict[str, Any]]:
        if self._referrers is None:
            index: dict[int, list[tuple[int, str]]] = {}
            for source in range(self.image.handle_count):
                if not self.image.live(source) or source == HANDLE_ROOT:
                    continue
                for target, how in self._targets(source, self.image.get(source)):
                    index.setdefault(target, []).append((source, how))
            self._referrers = index
        found = [
            {"path": self.image.path(source), "how": how}
            for source, how in self._referrers.get(handle, [])
            if source != handle
        ]
        found += [
            {"path": self.image.path(user), "how": "alias"}
            for user in self._alias_uses()[0].get(handle, [])
        ]
        return found

    def object(self, path: str) -> dict[str, Any]:
        image = self.image
        handle = self.handle(path)
        referrers = self.referrers_of(handle)
        got = image.get(handle)
        is_root = handle == HANDLE_ROOT
        return {
            "path": got["path"],
            "name": got["name"],
            "handle": handle,
            "type": None if is_root else image.type_name(handle),
            "type_path": None
            if is_root or got["type"] == _NONE
            else image.path(got["type"]),
            "kind": _word(NodeKind, got["kind"]),
            "parent": None if got["parent"] == _NONE else image.path(got["parent"]),
            "generation": got["generation"],
            "payload_size": len(got["payload"]),
            "fields": []
            if is_root
            else [{"name": n, "value": v} for n, v in image.fields(handle)],
            "refs": [
                {
                    "target": None if t == _NONE else image.path(t),
                    "role": None if role is None else image.path(role),
                    "index": index,
                    "kind": _word(RefKind, kind),
                    "binding": _word(Binding, binding),
                }
                for t, role, index, kind, binding in got["refs"]
            ],
            "index": got["index"],
            "members": []
            if is_root or got["kind"] == NodeKind.Module
            else self.members(handle),
            "aliases": [
                self.image.path(a) for a in self._alias_uses()[1].get(handle, [])
            ],
            "referrers": referrers[:REFERRER_LIMIT],
            "referrer_count": len(referrers),
        }

    def delta(self) -> str:
        return self.image.export()

    # ---- documentation (docs/22) ----------------------------------------------------------------

    def _doc_row(self, handle: int, role: int | None, source: int | None) -> dict[str, Any]:
        """One doc edge: the doc it names, the role it carries, and the doc's text face."""
        image = self.image
        fields = dict(image.fields(handle))
        return {
            "doc": image.path(handle),
            "doc_handle": handle,
            "role": None if role is None else image.path(role),
            "source": None if source is None else image.path(source),
            "kind": fields.get("kind", "DocKind.Narrates"),
            "status": fields.get("status", "DocStatus.Open"),
            "codec": fields.get("codec", "DocCodec.Lz4"),
            "text": image.doc_text(handle),
        }

    def docs(self, path: str) -> list[dict[str, Any]]:
        """The docs an object carries: its RefKind.Doc placements, with the text of each doc
        decompressed on demand. The text face reads; it never writes (see `set_doc_text`)."""
        image = self.image
        handle = self.handle(path)
        got = image.get(handle)
        return [
            self._doc_row(target, role, handle)
            for target, role, _, kind, _ in got["refs"]
            if kind == int(RefKind.Doc) and target != _NONE and image.live(target)
        ]

    def placements(self, path: str) -> list[dict[str, Any]]:
        """The placements of a doc or topic: every RefKind.Doc reference that names it, with the
        object or topic it sits on and the role that says why (docs/22)."""
        image = self.image
        handle = self.handle(path)
        found: list[dict[str, Any]] = []
        for source in range(image.handle_count):
            if not image.live(source) or source == HANDLE_ROOT:
                continue
            for target, role, _, kind, _ in image.get(source)["refs"]:
                if kind == int(RefKind.Doc) and target == handle:
                    found.append(
                        {
                            "source": image.path(source),
                            "role": None if role is None else image.path(role),
                        }
                    )
        return found

    def todos(self) -> list[dict[str, Any]]:
        """Every open DocKind.Todo across the image, with its text and where it is placed. A todo
        is a fact with a state: resolving one is a mutation of the image, not an edit here."""
        image = self.image
        out: list[dict[str, Any]] = []
        for handle in range(image.handle_count):
            if not image.live(handle) or image.type_name(handle) != "Doc":
                continue
            fields = dict(image.fields(handle))
            if fields.get("kind", "DocKind.Narrates") != "DocKind.Todo":
                continue
            status = fields.get("status", "DocStatus.Open")
            if status != "DocStatus.Open":
                continue
            out.append(
                {
                    "path": image.path(handle),
                    "status": status,
                    "text": image.doc_text(handle),
                    "placed_on": [
                        image.path(source)
                        for source in range(image.handle_count)
                        if image.live(source)
                        and source != HANDLE_ROOT
                        and any(
                            kind == int(RefKind.Doc) and target == handle
                            for target, _, _, kind, _ in image.get(source)["refs"]
                        )
                    ],
                }
            )
        return out

    def set_doc_text(self, path: str, text: str) -> None:
        """Docs are edited where every fact is edited: by a delta applied to the image. The
        inspector reads and refuses to write (docs/22: "a wrong docstring is a wrong fact")."""
        raise NotImplementedError(
            f"the inspector is read-only: edit the text of {path} with a delta "
            "(<rxf:set> of the doc payload, or the editor's doc face)"
        )

    # ---- the arena ------------------------------------------------------------------------------

    def header(self) -> dict[str, Any]:
        """The arena header decoded (ground.h): sizes, region offsets, and counts."""
        return self.image.arena_header()

    def cells(self) -> list[dict[str, Any]]:
        """Every cell of the stream in order; cached, so the memory map and the details read one walk."""
        if self._cells is None:
            self._cells = self.image.arena_cells()
        return self._cells

    def _cells_by_handle(self) -> dict[int, dict[str, Any]]:
        if self._cell_by_handle is None:
            self._cell_by_handle = {
                int(cell["handle"]): cell for cell in self.cells() if cell["handle"] is not None
            }
        return self._cell_by_handle

    @staticmethod
    def _regions(header: dict[str, Any]) -> list[dict[str, Any]]:
        """The bands of the arena outside the cell stream. The name and index blocks are cells of
        the stream and get their own colors; these bands label the rest."""
        return [
            {"name": "header", "start": 0, "end": header["header_size"]},
            {"name": "index", "start": header["index_off"], "end": header["cells_off"]},
            {"name": "cells", "start": header["cells_off"], "end": header["body_size"]},
        ]

    def memory(self, start: int | None = None, end: int | None = None, bins: int = 256) -> dict[str, Any]:
        """The arena as bins for the memory map.

        `start`/`end` narrow the view (the zoom); `bins` is the grid size, clamped to 16..4096. Each
        bin carries its bytes by cell kind (`kinds`, `dominant_kind`) and its `occupancy`: payload
        bytes over bin width, so cell headers, reference rows, and dead bytes leave the fill pale. A
        bin names the object when exactly one live object reaches it (`path`/`handle`); otherwise
        `dominant` is the object with the most bytes in it and `objects` counts the rest.
        """
        header = self.header()
        body = max(int(header["body_size"]), 1)
        lo = 0 if start is None else max(0, min(int(start), body - 1))
        hi = body if end is None else max(lo + 1, min(int(end), body))
        count = max(16, min(int(bins), 4096))
        span = hi - lo
        regions = self._regions(header)

        def region_of(offset: int) -> str:
            for band in regions:
                if int(band["start"]) <= offset < int(band["end"]):
                    return str(band["name"])
            return "cells"

        rows: list[dict[str, Any]] = [
            {
                "start": lo + i * span // count,
                "end": lo + (i + 1) * span // count,
                "region": region_of(lo + i * span // count),
                "used": 0,
                "payload": 0,
                "count": 0,
                "kinds": {},
                "owners": {},
            }
            for i in range(count)
        ]
        # The rows are not uniform (span is not whole in count): an index from x * count // span
        # is not the inverse of a bound at i * span // count, and a cell can lose a byte at a
        # boundary that way. The bounds themselves say which rows a cell reaches.
        starts = [int(row["start"]) for row in rows]
        kinds: dict[str, int] = {}
        for cell in self.cells():
            offset, size = int(cell["offset"]), int(cell["size"])
            if size <= 0 or offset + size <= lo or offset >= hi:
                continue
            name = _cell_kind(cell)
            handle = cell["handle"]
            # A dead cell's payload is garbage left until compaction; only live and block payloads count.
            live = handle is not None or int(cell["kind"]) == KIND_BLOCK
            payload_start = offset + CELL_SIZE
            payload_size = (int(cell["payload_size"]) + CELL_ALIGN - 1) // CELL_ALIGN * CELL_ALIGN
            first = bisect_right(starts, max(offset, lo)) - 1
            last = bisect_right(starts, min(offset + size, hi) - 1) - 1
            for index in range(first, last + 1):
                row = rows[index]
                row_lo, row_hi = int(row["start"]), int(row["end"])
                used = max(0, min(offset + size, row_hi) - max(offset, row_lo))
                if used == 0:
                    continue
                row["used"] = int(row["used"]) + used
                row["count"] = int(row["count"]) + 1
                row["kinds"][name] = int(row["kinds"].get(name, 0)) + used
                kinds[name] = kinds.get(name, 0) + used
                if live:
                    row["payload"] = int(row["payload"]) + max(
                        0, min(payload_start + payload_size, row_hi) - max(payload_start, row_lo)
                    )
                if handle is not None:
                    row["owners"][int(handle)] = int(row["owners"].get(int(handle), 0)) + used
        by_handle = self._cells_by_handle()
        out: list[dict[str, Any]] = []
        for row in rows:
            width = max(1, int(row["end"]) - int(row["start"]))
            owners: dict[int, int] = row.pop("owners")
            ranked = sorted(owners.items(), key=lambda item: -int(item[1]))
            dominant: dict[str, Any] | None = None
            if ranked:
                top = by_handle.get(ranked[0][0], {})
                dominant = {
                    "handle": ranked[0][0],
                    "path": self.image.path(ranked[0][0]),
                    "kind": _word(NodeKind, int(top.get("kind", 0))),
                    "payload_size": top.get("payload_size"),
                    "ref_count": top.get("ref_count"),
                }
            single = ranked[0][0] if len(ranked) == 1 else None
            alone = by_handle.get(single, {}) if single is not None else {}
            row["occupancy"] = min(1.0, int(row["payload"]) / width)
            row["dominant_kind"] = (
                max(row["kinds"], key=lambda name: int(row["kinds"][name])) if row["kinds"] else None
            )
            row["objects"] = len(ranked)
            row["path"] = None if single is None else self.image.path(single)
            row["handle"] = single
            row["payload_size"] = alone.get("payload_size")
            row["ref_count"] = alone.get("ref_count")
            row["dominant"] = dominant
            out.append(row)
        return {
            "arena": header,
            "regions": regions,
            "start": lo,
            "end": hi,
            "bins": out,
            "kinds": kinds,
        }

    # ---- one object, decoded ----------------------------------------------------------------------

    def _hex_rows(self, payload: bytes, slots: list[int]) -> dict[str, Any]:
        """The payload as 16-byte rows: offset, hex bytes, ASCII gutter. The bytes inside a
        reference slot the type layout declares (one handle, 8 bytes) are highlighted. A dump
        shows at most HEX_LIMIT bytes and says so when it truncates."""
        highlights: set[int] = set()
        for slot in slots:
            if slot + 8 <= len(payload):
                highlights.update(range(slot, slot + 8))
        shown = payload[:HEX_LIMIT]
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(shown), 16):
            chunk = shown[offset : offset + 16]
            rows.append(
                {
                    "offset": offset,
                    "bytes": [f"{byte:02x}" for byte in chunk],
                    "ascii": "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk),
                    "highlighted": [offset + i in highlights for i in range(len(chunk))],
                }
            )
        return {
            "size": len(payload),
            "displayed": len(shown),
            "truncated": len(payload) > len(shown),
            "rows": rows,
        }

    def detail(self, path: str) -> dict[str, Any]:
        """The introspection of one object: the decoded cell and flags, the payload hex, every
        reference row, and who points at the object (`used_by`: reference targets and roles,
        handles in payloads, alias uses, the children that name it parent, the objects that name
        it type). Read-only; every link is a path. `model` names the sections of the Model
        documentation (/model) that explain this cell's face and its words."""
        image = self.image
        handle = self.handle(path)
        got = image.get(handle)
        cell = self._cells_by_handle()[handle]
        flags = int(cell["flags"])
        kind = int(got["kind"])
        state = int(got["state"])
        owner = int(got["owner_kind"])
        payload: bytes = got["payload"]
        refs = [
            {
                "target": None if target == _NONE else image.path(target),
                "target_handle": None if target == _NONE else target,
                "role": None if role is None else image.path(role),
                "role_handle": role,
                "kind": _word(RefKind, ref_kind),
                "binding": _word(Binding, binding),
                "index": ref_index,
            }
            for target, role, ref_index, ref_kind, binding in got["refs"]
        ]
        used = self.referrers_of(handle)
        # The two cell header links name an object too: a child's parent field points here, and so
        # does the type field of every object of this type.
        used = [
            *used,
            *(
                {"path": image.path(child), "how": "parent"}
                for child in image.children(handle)
                if child != handle
            ),
            *(
                {"path": image.path(int(cell["handle"])), "how": "type"}
                for cell in self.cells()
                if cell["handle"] is not None
                and int(cell["handle"]) != handle
                and int(cell["type"]) == handle
            ),
        ]
        return {
            "path": got["path"],
            "name": got["name"],
            "handle": handle,
            "generation": got["generation"],
            "kind": _word(NodeKind, kind),
            "kind_value": kind,
            "parent": None if got["parent"] == _NONE else image.path(got["parent"]),
            "parent_handle": None if got["parent"] == _NONE else int(got["parent"]),
            "type": None if got["type"] == _NONE else image.path(got["type"]),
            "type_handle": None if got["type"] == _NONE else int(got["type"]),
            "flags": {
                "raw": flags,
                "kind": _word(NodeKind, kind),
                "owner": _word(OwnerKind, owner),
                "state": {
                    "value": state,
                    "provenance": _state_word(_PROVENANCE, state & 0x1),
                    "disposition": _state_word(_DISPOSITION, (state >> 1) & 0x3),
                    "cleanliness": _state_word(_CLEANLINESS, (state >> 3) & 0x1),
                    "mobility": _word(Mobility, (state >> 4) & 0x3),
                },
            },
            "cell": {
                "offset": cell["offset"],
                "size": cell["size"],
                "forward": cell["forward"],
                "name_off": cell["name_off"],
                "index": cell["index"],
                "flags": flags,
                "ref_count": cell["ref_count"],
                "payload_size": cell["payload_size"],
            },
            "payload": self._hex_rows(payload, self._reference_slots(got["type"])),
            "refs": refs,
            "used_by": used[:REFERRER_LIMIT],
            "used_by_count": len(used),
            "model": _model_links(
                got["path"],
                handle,
                _word(NodeKind, kind),
                None if got["parent"] == _NONE else int(got["parent"]),
                int(got["generation"]),
                int(cell["forward"]),
            ),
        }
