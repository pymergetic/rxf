"""The honors face (docs/36): who made this system, what its code carries, and the
ledger that stays open.

The data lives in the image itself: `honors/roles`, `honors/people`, `honors/works`
(the Genesis, read-only as data) and the append-only `honors/ledger`, with every ref
as a table under `sys/relations` (docs/32 2). This face is the reader of one arena
image plus the one write the ledger takes - `append`, which only ever adds. The laws
of docs/36 are enforced here and not left to callers: a name the ledger already took
refuses, and an entry that is on the ledger is final.

`who()` lists names and texts of the roles, people, works and ledger entries;
`about()` adds the refs of one person or work and the entries vouching for it.
`verify()` re-checks what the writer promised (docs/36 3 and 5): every ref lands on
a live object, an `embodies` lands on code inside the image, and the Genesis carries
its read-only flag.
"""

from __future__ import annotations

import struct
from typing import Any

from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    OBJ_BLOB,
    OBJ_SPEC,
    OFLAG_RO,
    ArenaError,
    ObjectArena,
)

HONORS = "honors"
ROLES = "roles"
PEOPLE = "people"
WORKS = "works"
LEDGER = "ledger"
SYS = "sys"
RELATIONS = "relations"
EMBODIES = "embodies"
PYMERGETIC = "pymergetic"

REF_RECORD = struct.Struct("<QQHHI")  # target, role, kind, binding, index (ground.h)
HANDLE_NONE_64 = (1 << 64) - 1

NAME = "name"
TEXT = "text"
REFS = "refs"
ROLE = "role"
TARGET = "target"
INDEX = "index"
KIND = "kind"
VOUCHED = "vouched"
LEDGER_NAME = "ledger"

ENTRY = "entry_%06d"  # the ordinal of a ledger entry IS the image time of its append


class HonorsError(Exception):
    """The honors refuse: no such person or work, a name the ledger already took."""


class Honors:
    """One image's honors: the founding core and the open ledger, answered like the
    image answers (docs/36 4) and written the one way the ledger allows."""

    def __init__(self, arena: ObjectArena) -> None:
        self.arena = arena

    @classmethod
    def attach(cls, data: bytes) -> Honors:
        """The honors of an existing image (`ObjectArena.to_bytes` is that image)."""
        return cls(ObjectArena.attach(data, True))

    def who(self) -> dict[str, Any]:
        """The founding core and the ledger as it stands: names and texts."""
        return {
            ROLES: [self._role(name) for name in self._names(ROLES)],
            PEOPLE: [self._member(PEOPLE, name) for name in self._names(PEOPLE)],
            WORKS: [self._member(WORKS, name) for name in self._names(WORKS)],
            LEDGER_NAME: [
                self._member(LEDGER, name) for name in self._names(LEDGER)
            ],
        }

    def about(self, name: str) -> dict[str, Any]:
        """One person or one work, with its refs and the entries vouching for it."""
        for group, kind in ((PEOPLE, "person"), (WORKS, "work")):
            if name in self._names(group):
                found = self._member(group, name)
                found[KIND] = kind
                found[VOUCHED] = self._vouched(self._path(group, name))
                return found
        raise HonorsError(self._no_such(name))

    def append(
        self,
        text: str,
        *,
        about: str,
        role: str,
        by: str = "console",
        name: str | None = None,
    ) -> str:
        """One entry onto the ledger: TEXT about ABOUT, vouched with ROLE, appended
        BY whoever says it. The entry takes the next ordinal as its name (its image
        time) unless NAME names it. The ledger never rewrites: a name that is taken
        refuses, and nothing already written is ever touched again."""
        self._role(role)  # a role is an object, and only the roles carry one
        handle = self._member_handle(about)
        ledger = self._resolve(self._path_group(LEDGER, ""))
        entry = name or ENTRY % (len(self._children(ledger)) + 1)
        if self.arena.find(ledger, entry) != HANDLE32_NONE:
            raise HonorsError(f"the ledger never rewrites: {entry!r} is already on it")
        record = f"by {by}\n{text}".encode()
        written = self.arena.new_object(ledger, entry, OBJ_BLOB, _round8(len(record)))
        self.arena.set_payload(written, record)
        table = self._table(
            f"{HONORS}.{LEDGER}.{entry}",
            [(handle, self._resolve(self._path(ROLES, role)), 0)],
        )
        self.arena.set_flags(written, OFLAG_RO)  # an entry is final once it is on
        self.arena.set_flags(table, OFLAG_RO)
        return f"{HONORS}/{LEDGER}/{entry}"

    def verify(self) -> dict[str, Any]:
        """The laws of docs/36 re-checked on the image as it stands (3 and 5): every
        ref lands on a live object, an `embodies` lands on code inside the image, and
        the Genesis carries its read-only flag."""
        problems: list[str] = []
        for group in (ROLES, PEOPLE, WORKS):
            for name in self._names(group):
                path = self._path(group, name)
                for ref in self._refs(path):
                    problems += self._law_ref(path, ref)
                if not self._read_only(self._resolve(path)):
                    problems.append(f"{path}: the Genesis carries no read-only flag")
        for name in self._names(LEDGER):
            entry = self._path(LEDGER, name)
            for ref in self._refs(entry):
                problems += self._law_ref(entry, ref)
            if not self._read_only(self._resolve(entry)):
                problems.append(f"{entry}: an entry that is not final")
        return {"ok": not problems, "problems": problems}

    # -- the laws -----------------------------------------------------------

    def _law_ref(self, path: str, ref: dict[str, Any]) -> list[str]:
        problems: list[str] = []
        target, role = ref[TARGET], ref[ROLE]
        if target is None:
            problems.append(f"{path}: a ref whose target is gone")
        if role is None:
            problems.append(f"{path}: a ref whose role is gone")
        if ref[ROLE] == EMBODIES and target is not None and not self._is_code(target):
            problems.append(f"{path}: an embodies ref that lands on no code")
        return problems

    # -- reading ------------------------------------------------------------

    def _names(self, group: str) -> list[str]:
        parent = self._resolve(self._path_group(group, ""))
        return sorted(self._name(child) for child in self._children(parent))

    def _role(self, name: str) -> dict[str, Any]:
        handle = self._resolve(self._path(ROLES, name), want=f"no role named {name!r}")
        return {NAME: name, TEXT: self._text(handle)}

    def _member(self, group: str, name: str) -> dict[str, Any]:
        handle = self._resolve(self._path(group, name))
        return {
            NAME: name,
            TEXT: self._text(handle),
            REFS: self._refs(self._path(group, name)),
        }

    def _member_handle(self, name: str) -> int:
        for group in (PEOPLE, WORKS):
            if name in self._names(group):
                return self._resolve(self._path(group, name))
        raise HonorsError(self._no_such(name))

    def _no_such(self, name: str) -> str:
        everyone = self._names(PEOPLE) + self._names(WORKS)
        return f"no person or work named {name!r}; the founding core is {', '.join(everyone)}"

    def _refs(self, path: str) -> list[dict[str, Any]]:
        """The ref table of a path (docs/32 2), with roles and targets as names."""
        table = self._find(f"{SYS}/{RELATIONS}", path.replace("/", "."))
        if table is None:
            return []
        data = self.arena.payload(table)
        found = []
        for at in range(0, len(data), REF_RECORD.size):
            target, role, _kind, _binding, index = REF_RECORD.unpack_from(data, at)
            found.append(
                {
                    ROLE: self._name_or_none(role),
                    TARGET: self._path_or_none(target),
                    INDEX: index,
                }
            )
        return found

    def _vouched(self, path: str) -> list[str]:
        """The ledger entries whose refs name PATH (docs/36 6: the late arrivals)."""
        ledger = self._resolve(self._path_group(LEDGER, ""))
        return sorted(
            self._name(entry)
            for entry in self._children(ledger)
            if any(
                ref[TARGET] == path
                for ref in self._refs(f"{HONORS}/{LEDGER}/{self._name(entry)}")
            )
        )

    def _name_or_none(self, handle: int) -> str | None:
        try:
            return None if handle == HANDLE32_NONE else self._name(handle)
        except ArenaError:  # a ref whose handle is dead: verify() names it as a problem
            return None

    def _path_or_none(self, handle: int) -> str | None:
        try:
            return None if handle == HANDLE32_NONE else self._path_of(handle)
        except ArenaError:  # a ref whose handle is dead: verify() names it as a problem
            return None

    def _path_of(self, handle: int) -> str:
        segments: list[str] = []
        node = handle
        while node != self.arena.root_handle:
            segments.append(self._name(node))
            node = self.arena.load(node)[4]  # parent
        return "/".join(reversed(segments))

    def _path(self, group: str, name: str) -> str:
        return self._path_group(group, name)

    def _path_group(self, group: str, name: str) -> str:
        return f"{HONORS}/{group}" + (f"/{name}" if name else "")

    def _name(self, handle: int) -> str:
        record = self.arena.load(handle)
        return self.arena.symbol(record[3]) if record[3] else ""

    def _text(self, handle: int) -> str:
        return self.arena.payload(handle).rstrip(b"\0").decode()

    def _children(self, parent: int) -> list[int]:
        return list(self.arena.children(parent))

    def _read_only(self, handle: int) -> bool:
        return bool(self.arena.load(handle)[2] & OFLAG_RO)

    def _is_code(self, target: str) -> bool:
        """Code inside the image (docs/36 3): an `embodies` lands under `pymergetic/`,
        never on the honors or the system tree."""
        return target.startswith(f"{PYMERGETIC}/")

    def _find(self, group: str, name: str) -> int | None:
        node = self._resolve(group)
        found = self.arena.find(node, name)
        return None if found == HANDLE32_NONE else found

    def _resolve(self, path: str, want: str | None = None) -> int:
        node = self.arena.root_handle
        for segment in path.split("/"):
            if not segment:
                continue
            node = self.arena.find(node, segment)
            if node == HANDLE32_NONE:
                raise HonorsError(want or f"no object at {path!r}")
        return node

    def _table(self, key: str, refs: list[tuple[int, int, int]]) -> int:
        """One relation table under sys/relations (docs/32 2), named by its key."""
        relations = self._resolve(f"{SYS}/{RELATIONS}")
        table = b"".join(
            REF_RECORD.pack(target, role, 0, 0, index) for target, role, index in refs
        )
        handle = self.arena.new_object(relations, key, OBJ_SPEC, _round8(len(table)))
        self.arena.set_payload(handle, table)
        return handle


def _round8(size: int) -> int:
    """Every payload is 8-aligned room (ground.h: a record's payload length is)."""
    return (size + 7) & ~7
