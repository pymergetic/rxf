"""Read-only views of an RXFB 6 image for the inspector (no web framework in here)."""

from __future__ import annotations

import enum
import struct
from typing import Any

from pymergetic.rxf.core.arena import HANDLE_NONE, HANDLE_ROOT
from pymergetic.rxf.core.kinds import Binding, NodeKind, RefKind
from pymergetic.rxf.core.object import Image


def _word(words: type[enum.IntEnum], value: int) -> str:
    """The name of a value of one of the core enums; a value the core does not know stays a number."""
    try:
        return words(value).name
    except ValueError:
        return str(value)


_NONE = HANDLE_NONE
REFERRER_LIMIT = 200


class Inspector:
    """Answers the questions of the inspector from one image."""

    def __init__(self, image: Image) -> None:
        self.image = image
        self._referrers: dict[int, list[tuple[int, str]]] | None = None
        self._slots: dict[int, list[int]] = {}
        self._uses: tuple[dict[int, list[int]], dict[int, list[int]]] | None = None

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
