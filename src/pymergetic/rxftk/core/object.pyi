"""The host image: paths, names, compaction."""

from collections.abc import Sequence
from typing import Any

import pymergetic.rxftk.core.delta


DEFAULT_LIMIT: int = 1073741824

def image_state(mobility: int = 0) -> int:
    """
    The state word of an object that comes from an image (IMAGE, RETAIN, CLEAN, the given mobility).
    """

class Image:
    @staticmethod
    def arena(limit: int = 1073741824, index_capacity: int = 0) -> Image:
        """
        A fresh, empty arena: an image without the ground (the constructor of the arena layer, Image::create in C++; `create` on an image is the edit verb of docs/27).
        """

    @staticmethod
    def from_bytes(data: bytes, limit: int = 1073741824) -> Image: ...

    def bootstrap(self) -> None: ...

    def add(self, parent: int | None, name: str, kind: int = 0, type: int = 18446744073709551615, payload: bytes = ..., refs: Sequence[tuple[int, int | None, int, int, int]] = [], state: int = 0, owner_kind: int = 6, generation: int = 0, index: int = 0) -> int: ...

    def remove(self, arg: int, /) -> None: ...

    def set_payload(self, arg0: int, arg1: bytes, /) -> None: ...

    def set_refs(self, arg0: int, arg1: Sequence[tuple[int, int | None, int, int, int]], /) -> None: ...

    def set_parent(self, arg0: int, arg1: int, /) -> None: ...

    def set_type(self, arg0: int, arg1: int, /) -> None: ...

    def set_state(self, arg0: int, arg1: int, /) -> None: ...

    def get(self, arg: int, /) -> dict[str, Any]: ...

    def live(self, arg: int, /) -> bool: ...

    def arena_header(self) -> dict:
        """The arena header decoded: sizes, region offsets, and counts."""

    def arena_cells(self) -> list[dict]:
        """
        Every cell of the stream in order: arena offset, size, decoded header, and the live handle it holds (none for a block or dead cell).
        """

    def resolve(self, arg: str, /) -> int | None: ...

    def handle(self, arg: str, /) -> int:
        """
        The handle at a path; KeyError when there is none (resolve returns None).
        """

    def path(self, arg: int, /) -> str: ...

    def children(self, arg: int, /) -> list[int]: ...

    def verify(self) -> None: ...

    def begin(self, log_limit: int = 0) -> None:
        """
        Starts a transaction: what follows is logged until commit() or rollback(). `log_limit` bounds the undo log (0: the limit of the image).
        """

    def commit(self) -> None:
        """Ends the transaction and keeps the changes."""

    def rollback(self) -> None:
        """
        Ends the transaction; the image is as it was at begin(), byte for byte.
        """

    def to_bytes(self) -> bytes: ...

    @property
    def handle_count(self) -> int: ...

    @property
    def body_size(self) -> int: ...

    @property
    def frontier(self) -> int: ...

    @property
    def index_chunks(self) -> int: ...

    @property
    def free_head(self) -> int: ...

    @property
    def limit(self) -> int: ...

    @property
    def entry_path(self) -> str:
        """The path of the entry Function, empty when there is none."""

    @property
    def entry(self) -> int | None:
        """The entry Function of the image."""

    @entry.setter
    def entry(self, arg: int | None, /) -> None: ...

    def members(self, handle: int) -> list[tuple[int, list[int]]]:
        """
        The declared relations of an object (Children and Inverse fields) as (field, objects in order).
        """

    def ordered(self, handles: Sequence[int]) -> list[int]:
        """
        The objects in the order of siblings: by position, then by name with [n] read as a number.
        """

    def compact(self, index_capacity: int = 0) -> Image:
        """A successor: retained objects only, in path order, handles 0 to n-1."""

    def check(self) -> list[tuple[str, str]]:
        """Structural problems as (path, message); empty when the image links."""

    def alias_uses(self) -> list[tuple[int, int]]:
        """
        The objects whose written paths go through an Alias, as (alias, user) handles; each pair once.
        """

    def paths(self, handle: int) -> list[str]:
        """
        The paths an object holds that the text form writes (and an alias may shorten), absolute.
        """

    def fields(self, handle: int) -> list[tuple[str, str]]:
        """
        The fields of an object as (name, text); zero and unset values are left out.
        """

    def resolve_module(self, name: str) -> str:
        """
        The module tree's trichotomy for one name: "package", "module" or "none" — the answer mp_import_stat gives. A name that spells no one clear module is refused (TreeError).
        """

    def module_children(self, name: str) -> list[str]:
        """
        The sub-module names of a package (what makes it one), in name order; empty for a leaf module and for none.
        """

    def module_roots(self) -> list[str]:
        """
        The Quad: the four roots of the module tree (px, pm, pt, py) and nothing else.
        """

    def doc_text(self, handle: int) -> str:
        """The text of a Doc object, decompressed on demand (docs/22)."""

    def doc_add(self, parent: int, name: str, text: str, kind: int = 0, status: int = 0, codec: int = 0) -> int:
        """
        A Doc object below `parent` carrying `text` (DocKind/DocStatus/DocCodec words), made by the rules of the ground. Refused below the ground's floor: an image cannot hold a kind it does not know.
        """

    def doc_place(self, source: int, doc: int, role: int = 0) -> None:
        """
        A RefKind.Doc placement of `doc` on `source` (DocRole word). Stored docs make zero runtime refs (the image check), so the placement is editorial.
        """

    def lift_comment(self, target: int, name: str, text: str, kind: int = 0, status: int = 0, role: int = 0) -> int:
        """
        A comment/docstring lifted to a Narrates Doc object (Lz4 by default) and placed on `target` — the import lane's single call. Refused below the floor as any other doc creation.
        """

    def doc_origin(self, parent: int, name: str, tier: str, upstream: str, repository: str, commit: str, license: str = '', importer: str = '') -> int:
        """
        The origin record of an import (a Topic Doc): upstream, repository, commit, license, importer. Made at every provenance tier (docs/22); `tier` is the run's per-import tier.
        """

    def doc_source(self, parent: int, name: str, tier: str, source: str, origin: int | None = None) -> int:
        """
        The imported source itself as one stored payload (a Narrative Doc, Lz4), its origin in the tail first. The 'full' tier's payload — place() it on every object it explains: one payload, N placements.
        """

    def doc_hash(self, parent: int, name: str, tier: str, sha256: str) -> int:
        """
        The file's content SHA as one hash Document (a Topic Doc, Raw): exactly 64 lowercase hex digits or refused. The 'hash' tier's payload.
        """

    def doc_cite(self, parent: int, name: str, tier: str, placed_on: Sequence[int], provenance: Sequence[int], path: str, first_line: int, last_line: int, sha256: str = '') -> int:
        """
        A citation (a Narrative Doc with a locator in its tail) placed on each object of `placed_on`, citing each fact of `provenance` (the import's origin record, the file's source Doc at full). First and last line are 1-based, or refused. Made at the 'cite' and 'full' tiers.
        """

    def doc_locator(self, doc: int) -> dict[str, Any]:
        """
        The locator of a citation: {path, first_line, last_line, sha256} parsed out of the Doc's text tail (the seam, docs/22). Refused on a Doc without one.
        """

    def doc_permalink(self, doc: int) -> str:
        """
        A citation's permalink built from its locator: repo at commit, path, the line range.
        """

    def type_name(self, arg: int, /) -> str | None: ...

    def layout(self, type: int) -> dict[str, Any]:
        """
        How the payload of a Type object is laid out: leaves with offset, size, kind, reference.
        """

    def digest(self) -> str:
        """SHA-256 of the canonical bytes (the image after compaction)."""

    @staticmethod
    def empty(limit: int = 1073741824, index_capacity: int = 0) -> Image:
        """
        An image with the ground (root, Type, Field, uint32_t, uint64_t) and nothing else.
        """

    def apply(self, delta: str, links: bool = True) -> None:
        """
        Applies a delta in XML. The image changes only when the whole delta applies and links; with `links=False` the operations must still hold but the result is not required to link, which is what the physical compiler needs on a live image: the image checks the links when it takes the delta.
        """

    def apply_all(self, files: Sequence[tuple[str, str]], links: bool = True) -> None:
        """
        Applies several deltas (name, XML) as one: the operations run in the order given, atomically. With `links=False` the result is not required to link (see `apply`).
        """

    def export(self, module: str | None = None) -> str:
        """
        The canonical delta of the image as XML; with `module`, only that Module object without its sub-modules ('' is the root).
        """

    def delta_digest(self) -> str:
        """SHA-256 of the canonical delta text."""

    def create(self, type: object, parent: object | None = None, name: str) -> int:
        """
        A new cell of `type` below `parent` (none: the root); size and align come from the Type. Paths name things, handles identify them: either. The handle of the new cell.
        """

    def rename(self, target: object, name: str) -> None:
        """
        The name of an object changes; the name is an identifier and the schema checks it.
        """

    def move(self, target: object, parent: object) -> None:
        """
        The object sits under another parent; the place is the sibling order (docs/15).
        """

    def delete(self, target: object) -> None:
        """
        Removes the object and everything below it; refused while anything else points at it.
        """

    def put(self, target: object, field: str, value: object) -> None:
        """
        One leaf is written: width, sign and enum come from the Field. Derived and declared lists take no value and no place; a reference is written by `ref`.
        """

    def ref(self, target: object, field: str, to: object) -> None:
        """
        The ONLY place a handle may be written: the reference field of one object points at another.
        """

    def unset(self, target: object, field: str) -> None:
        """An OPTIONAL field goes back to unset; anything else refuses."""

    def edit(self) -> pymergetic.rxftk.core.delta.Edit:
        """
        The transaction (docs/27): the verbs collect operations in the currency of the text form — paths, never handles — and commit is ONE delta: all of it or none of it.
        """

    def mount_names(self) -> list[str]:
        """
        Every module name of the image (the path of every Module cell), sorted: the truth the guest's import walk and the host's mount both answer from.
        """
