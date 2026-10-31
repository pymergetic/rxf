"""
The slim reader of an RXFB v7 image: zero-copy views of the mapped bytes.
"""

from collections.abc import Buffer


class RxfbError(Exception):
    status: int = -1

class BadImage(RxfbError, ValueError):
    status: int = 1

class BadHandle(RxfbError, LookupError):
    status: int = 2

class BadName(RxfbError, ValueError):
    status: int = 3

class Protected(RxfbError):
    status: int = 4

class NotFound(RxfbError, LookupError):
    status: int = 6

HANDLE_NONE: int = 4294967295

RUN_PATH: str = 'pymergetic.rxf.foundation.graph.run'

OBJ_MODULE: int = 1

OBJ_FUNC: int = 2

OBJ_HASH: int = 3

OBJ_SPEC: int = 4

OBJ_SYM: int = 5

OBJ_BLOB: int = 6

OBJ_FRAME: int = 7

OBJ_JOURNAL: int = 8

REF_CALL: int = 0

REF_DATA: int = 1

REF_ENTRY: int = 2

REF_TYPE: int = 3

REF_IMPORT: int = 4

REF_IMPLEMENTATION: int = 5

REF_OWNER: int = 6

REF_DOC: int = 7

class Spec:
    """The semantic row of an object (sys/specs)."""

    @property
    def type(self) -> int | None:
        """The handle of the Type object, or None when the object is untyped."""

    @property
    def flags(self) -> int:
        """The world's flags word: kind | state << 8 | owner << 14."""

    @property
    def index(self) -> int:
        """The position among ordered siblings; 0: ordered by name."""

    @property
    def generation(self) -> int:
        """The world's generation of the object."""

    def __repr__(self) -> str: ...

class Ref:
    """One reference row of an object (sys/relations)."""

    @property
    def target(self) -> int:
        """The handle of the target."""

    @property
    def role(self) -> int | None:
        """The handle of the role, or None."""

    @property
    def kind(self) -> int:
        """One of the REF_* kinds."""

    @property
    def binding(self) -> int:
        """0 mandatory, 1 optional."""

    @property
    def index(self) -> int:
        """The argument or leaf number."""

class Code:
    """The bytes of a function for one machine."""

    @property
    def view(self) -> memoryview:
        """The bytes, a read-only memoryview into the image."""

    @property
    def entry(self) -> int:
        """The offset of the first byte in the image."""

    @property
    def length(self) -> int:
        """The number of bytes."""

    @property
    def code(self) -> int:
        """The handle of the Code object."""

    @property
    def blob(self) -> int:
        """The handle of the CodeBlob object."""

    def __len__(self) -> int: ...

class ChildIterator:
    def __iter__(self) -> ChildIterator: ...

    def __next__(self) -> Object: ...

class Children:
    """The children of an object, in chain order."""

    def __iter__(self) -> ChildIterator: ...

    def __len__(self) -> int: ...

class Object:
    """An object of the image: its record, payload and neighbours."""

    @property
    def handle(self) -> int: ...

    @property
    def kind(self) -> int:
        """One of the OBJ_* kinds."""

    @property
    def generation(self) -> int: ...

    @property
    def flags(self) -> int: ...

    @property
    def name_id(self) -> int:
        """The interned symbol id (0: anonymous)."""

    @property
    def name(self) -> str | None:
        """The name of the object, or None when it is anonymous."""

    @property
    def parent(self) -> int | None:
        """The handle of the parent object, or None for the root."""

    @property
    def link(self) -> int | None:
        """The handle of the next sibling, or None."""

    @property
    def refcount(self) -> int: ...

    @property
    def payload_size(self) -> int: ...

    @property
    def payload(self) -> memoryview:
        """The payload: a read-only memoryview slice of the image, never a copy."""

    def path(self) -> str:
        """The dotted path from the root (the root's own is the empty string)."""

    def spec(self) -> Spec:
        """The semantic row of the object."""

    def children(self) -> Children:
        """The children in chain order, an iterable that can be walked again."""

    def refs(self) -> Refs:
        """The reference rows of the object."""

    def table(self) -> Refs:
        """
        The object read as a reference table: an object of sys/relations, named by the path of the object whose rows it holds. Any other object is a BadHandle.
        """

    def code(self, machine: str) -> Code:
        """The bytes of this function for a machine (e.g. 'wasm32')."""

    def __eq__(self, arg: object, /) -> bool: ...

    def __hash__(self) -> int: ...

    def __repr__(self) -> str: ...

class RefIterator:
    def __iter__(self) -> RefIterator: ...

    def __next__(self) -> Ref: ...

class Refs:
    """The reference rows of an object: a sequence of Ref."""

    def __len__(self) -> int: ...

    def __iter__(self) -> RefIterator: ...

    def __getitem__(self, arg: int, /) -> Ref: ...

    @property
    def view(self) -> memoryview:
        """The raw 24-byte rows: a read-only memoryview into the image."""

class Image:
    """An attached image: views onto the caller's bytes."""

    @property
    def view(self) -> memoryview:
        """The whole image: a read-only memoryview of the caller's buffer."""

    @property
    def size(self) -> int: ...

    @property
    def count(self) -> int:
        """The handles (index slots) the image holds."""

    @property
    def root(self) -> Object: ...

    @property
    def entry(self) -> Object | None:
        """The entry object, or None when the image has none."""

    def get(self, handle: int) -> Object:
        """The object a handle names."""

    def lookup(self, path: str | bytes) -> Object:
        """The object at a dotted path ('' is the root)."""

    def __getitem__(self, key: int | str | bytes, /) -> Object: ...

    def __len__(self) -> int: ...

    def symbol(self, id: int) -> str:
        """The text of an interned name."""

    def slot(self, slot: int, machine: str, run: Object | None = None) -> Code:
        """
        The bytes of function slot `slot` of the interpreter (RUN_PATH, or `run`) for a machine.
        """

def attach(buffer: Buffer) -> Image:
    """
    Validates an image and maps it in place: no byte is copied, the buffer stays exported for as long as the Image lives.
    """
