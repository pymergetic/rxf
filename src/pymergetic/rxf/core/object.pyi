"""The host image: paths, names, compaction."""

from collections.abc import Sequence
from typing import Any


DEFAULT_LIMIT: int = 1073741824

def image_state(mobility: int = 0) -> int:
    """
    The state word of an object that comes from an image (IMAGE, RETAIN, CLEAN, the given mobility).
    """

class Image:
    @staticmethod
    def create(limit: int = 1073741824, index_capacity: int = 0) -> Image: ...

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

    def doc_text(self, handle: int) -> str:
        """The text of a Doc object, decompressed on demand (docs/22)."""

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

    def apply(self, delta: str) -> None:
        """
        Applies a delta in XML. The image changes only when the whole delta applies and links.
        """

    def apply_all(self, files: Sequence[tuple[str, str]]) -> None:
        """
        Applies several deltas (name, XML) as one: the operations run in the order given, atomically.
        """

    def export(self, module: str | None = None) -> str:
        """
        The canonical delta of the image as XML; with `module`, only that Module object without its sub-modules ('' is the root).
        """

    def delta_digest(self) -> str:
        """SHA-256 of the canonical delta text."""
