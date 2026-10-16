"""Deltas: replay by path and canonical export."""

import pymergetic.rxftk.core.object


class DeltaError(RuntimeError):
    pass

class Edit:
    """
    One edit of the object world (docs/27): operations collect in the currency of the text form (paths, never handles) and commit is one delta — all of it or none of it — written down as one .rxfd, the history entry of the delta chain. Until commit the image does not change; rollback (or an abandoned `with`) drops the operations.
    """

    def __init__(self, image: pymergetic.rxftk.core.object.Image) -> None: ...

    def create(self, type: str, parent: str, name: str) -> str:
        """
        A new cell of the named Type below the named parent ('' is the root). Its path: the handle comes with the commit.
        """

    def rename(self, path: str, name: str) -> None: ...

    def move(self, path: str, parent: str) -> None: ...

    def delete(self, path: str) -> None: ...

    def put(self, path: str, field: str, value: object) -> None: ...

    def ref(self, path: str, field: str, target: str) -> None:
        """
        A reference is written by its target path (it may point forward in the edit).
        """

    def unset(self, path: str, field: str) -> None: ...

    def entry(self, path: str) -> None:
        """Names the entry Function of the image (the empty path: none)."""

    def commit(self) -> str:
        """
        ONE .rxfd: the operations replay as a whole — all of it or none of it — and the text that spells them is the answer.
        """

    def rollback(self) -> None:
        """The operations are dropped; the image is as it was."""

    @property
    def open(self) -> bool: ...

    @property
    def size(self) -> int: ...

    def __enter__(self) -> Edit: ...

    def __exit__(self, *args) -> bool: ...
