"""The delta in its physical form: what a running image applies (docs/19, decision 1).

A text delta says *what to do* and needs the whole compiler of the host to be read. The physical
form says *what is there afterwards*, object by object, with every handle written as the path of
the object it points at: the image only looks paths up, writes bytes and checks that the result
links. It is compiled on the host from the image before and the image after, so it carries exactly
the difference, whatever tool changed the image (text delta, UI, a script).

Layout (little-endian, see live.h): a 64-byte header, a block of NUL-terminated strings, then the
operations: CREATE and SET (the whole state of one object), DELETE, ENTRY.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field

from pymergetic.rxftk.core.object import Image

MAGIC = 0x44465852
VERSION = 1
CHECK_BASE = 1
NONE_STRING = 0xFFFFFFFF
NONE = 0xFFFFFFFFFFFFFFFF
CREATE, SET, DELETE, ENTRY = 1, 2, 3, 4

OBJECT = struct.Struct(
    "<IIIIIIQIIII"
)  # op path type flags index reserved generation payload refs patches reserved
PATCH = struct.Struct("<II")
REF = struct.Struct("<IIHHI")
PATH_OP = struct.Struct("<II")
HEADER = struct.Struct("<IIIII I 32s 8s")

assert (
    OBJECT.size == 48
    and PATCH.size == 8
    and REF.size == 16
    and PATH_OP.size == 8
    and HEADER.size == 64
)


@dataclass
class Record:
    """What one object is, with no handle in it."""

    type: str | None
    flags: int
    index: int
    generation: int
    payload: bytes
    patches: tuple[tuple[int, str | None], ...]
    refs: tuple[tuple[str | None, str | None, int, int, int], ...]


def _flags(g: dict) -> int:
    return g["kind"] | (g["state"] << 8) | (g["owner_kind"] << 14)


def records(image: Image) -> tuple[dict[str, Record], str | None]:
    """The objects of an image by path, and the path of its entry."""
    leaves: dict[int, list[tuple[int, bool]]] = {}

    def references(type_handle: int) -> list[tuple[int, bool]]:
        if type_handle not in leaves:
            layout = image.layout(type_handle)
            found = [] if layout["error"] or layout["raw"] else layout["leaves"]
            leaves[type_handle] = [
                (leaf["offset"], leaf["optional"])
                for leaf in found
                if leaf["reference"]
            ]
        return leaves[type_handle]

    def path_of(handle: int | None) -> str | None:
        if handle is None or handle == NONE:
            return None
        return image.path(handle)

    def gone(handle: int | None) -> bool:
        return handle is not None and handle != NONE and not image.live(handle)

    out: dict[str, Record] = {}
    for h in range(image.handle_count):
        if not image.live(h):
            continue
        g = image.get(h)
        payload = bytearray(g["payload"])
        patches: list[tuple[int, str | None]] = []
        if g["type"] != NONE and payload:
            for offset, optional in references(g["type"]):
                if offset + 8 <= len(payload):
                    target = struct.unpack_from("<Q", payload, offset)[0]
                    # An optional handle to an object that is gone is unset in the canonical image (compaction does the same).
                    if gone(target) and not optional:
                        raise ValueError(
                            f"{g['path']}: a mandatory handle points at an object that is gone"
                        )
                    patches.append((offset, None if gone(target) else path_of(target)))
                    struct.pack_into("<Q", payload, offset, 0)
        refs: list[tuple[str | None, str | None, int, int, int]] = []
        for target, role, index, kind, binding in g[
            "refs"
        ]:  # the tuples of Image.get: index before kind
            if gone(role) or (gone(target) and binding == 0):
                raise ValueError(
                    f"{g['path']}: a reference points at an object that is gone"
                )
            if gone(target):
                continue  # an optional reference to an object that is gone is dropped by compaction
            refs.append((path_of(target), path_of(role), kind, binding, index))
        out[g["path"]] = Record(
            path_of(g["type"]),
            _flags(g),
            g["index"],
            g["generation"],
            bytes(payload),
            tuple(patches),
            tuple(refs),
        )
    entry = image.entry
    return out, (None if entry is None else image.path(entry))


def _order(path: str) -> list[str]:
    return path.split(".") if path else []


@dataclass
class Compiled:
    data: bytes
    creates: int = 0
    sets: int = 0
    deletes: int = 0
    entry: bool = False
    paths: list[str] = field(default_factory=list)


class Builder:
    """Writes a physical delta operation by operation (also for deltas that no pair of images would give)."""

    def __init__(self) -> None:
        self._strings = bytearray()
        self._index: dict[str, int] = {}
        self._ops = bytearray()
        self.count = 0

    def string(self, text: str | None) -> int:
        if text is None:
            return NONE_STRING
        if text not in self._index:
            self._index[text] = len(self._strings)
            self._strings.extend(text.encode() + b"\0")
        return self._index[text]

    def object(self, op: int, path: str, r: Record) -> None:
        padded = r.payload + b"\0" * (-len(r.payload) % 8)
        self._ops += OBJECT.pack(
            op,
            self.string(path),
            self.string(r.type),
            r.flags,
            r.index,
            0,
            r.generation,
            len(r.payload),
            len(r.refs),
            len(r.patches),
            0,
        )
        self._ops += padded
        self._ops += b"".join(
            PATCH.pack(offset, self.string(target)) for offset, target in r.patches
        )
        self._ops += b"".join(
            REF.pack(self.string(t), self.string(role), kind, binding, idx)
            for (t, role, kind, binding, idx) in r.refs
        )
        self.count += 1

    def create(self, path: str, r: Record) -> None:
        self.object(CREATE, path, r)

    def set(self, path: str, r: Record) -> None:
        self.object(SET, path, r)

    def delete(self, path: str) -> None:
        self._ops += PATH_OP.pack(DELETE, self.string(path))
        self.count += 1

    def entry(self, path: str | None) -> None:
        self._ops += PATH_OP.pack(ENTRY, self.string(path))
        self.count += 1

    def build(self, base_digest: str | None = None) -> bytes:
        digest = bytes.fromhex(base_digest) if base_digest else bytes(32)
        pad = b"\0" * (-len(self._strings) % 8)
        head = HEADER.pack(
            MAGIC,
            VERSION,
            self.count,
            len(self._strings),
            CHECK_BASE if base_digest else 0,
            0,
            digest,
            bytes(8),
        )
        return head + bytes(self._strings) + pad + bytes(self._ops)


def compile(base: Image, result: Image, *, check_base: bool = True) -> Compiled:
    """The delta that turns `base` into `result`."""
    before, before_entry = records(base)
    after, after_entry = records(result)
    deletes = sorted(
        (p for p in before if p not in after), key=lambda p: (-len(_order(p)), p)
    )
    creates = sorted((p for p in after if p not in before), key=_order)
    sets = sorted(
        (p for p in after if p in before and after[p] != before[p]), key=_order
    )
    builder = Builder()
    for path in deletes:
        builder.delete(path)
    for path in creates:
        builder.create(path, after[path])
    for path in sets:
        builder.set(path, after[path])
    if before_entry != after_entry:
        builder.entry(after_entry)
    return Compiled(
        builder.build(base.digest() if check_base else None),
        creates=len(creates),
        sets=len(sets),
        deletes=len(deletes),
        entry=before_entry != after_entry,
        paths=creates + sets + deletes,
    )


def digest_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def from_xml(base: Image, *texts: tuple[str, str], check_base: bool = True) -> Compiled:
    """The physical delta of text deltas (name, XML): they are applied to a copy of the base, the difference is compiled.

    The copy is replayed with `links=False`: the operations must hold, but the result is not
    required to link. The physical form says what is there afterwards, whatever left the image
    in that state (text, UI, a poke) — the image checks that it links when it takes the delta.
    """
    result = Image.from_bytes(base.to_bytes())
    result.apply_all(list(texts), links=False)
    return compile(base, result, check_base=check_base)


def decode(data: bytes) -> list[dict]:
    """The operations of a physical delta as plain dicts (for tools and tests)."""
    magic, version, count, string_bytes, flags, _r, base, _r2 = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        raise ValueError("not a delta")
    strings = data[64 : 64 + string_bytes]

    def text(offset: int) -> str | None:
        if offset == NONE_STRING:
            return None
        return strings[offset : strings.index(b"\0", offset)].decode()

    at = (64 + string_bytes + 7) & ~7
    out: list[dict] = [
        {
            "op": "header",
            "base": base.hex() if flags & CHECK_BASE else None,
            "operations": count,
        }
    ]
    for _ in range(count):
        (op,) = struct.unpack_from("<I", data, at)
        if op in (CREATE, SET):
            (
                _o,
                path,
                type_,
                flags_,
                index_,
                _r1,
                generation,
                plen,
                rcount,
                pcount,
                _r3,
            ) = OBJECT.unpack_from(data, at)
            at += OBJECT.size
            payload = data[at : at + plen]
            at += (plen + 7) & ~7
            patches = [
                (
                    PATCH.unpack_from(data, at + 8 * k)[0],
                    text(PATCH.unpack_from(data, at + 8 * k)[1]),
                )
                for k in range(pcount)
            ]
            at += 8 * pcount
            refs = []
            for k in range(rcount):
                t, role, kind, binding, idx = REF.unpack_from(data, at + 16 * k)
                refs.append((text(t), text(role), kind, binding, idx))
            at += 16 * rcount
            out.append(
                {
                    "op": "create" if op == CREATE else "set",
                    "path": text(path),
                    "type": text(type_),
                    "flags": flags_,
                    "index": index_,
                    "generation": generation,
                    "payload": payload,
                    "patches": patches,
                    "refs": refs,
                }
            )
        else:
            _o, path = PATH_OP.unpack_from(data, at)
            at += 8
            out.append(
                {"op": "delete" if op == DELETE else "entry", "path": text(path)}
            )
    return out
