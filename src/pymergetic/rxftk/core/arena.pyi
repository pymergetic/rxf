"""The ground layout."""



ARENA_VERSION: int = 6

CELL_ALIGN: int = 16

INDEX_CHUNK_ENTRIES: int = 4096

HANDLE_NONE: int = 18446744073709551615

HANDLE_ROOT: int = 0

HANDLE_TYPE: int = 1

HANDLE_FIELD: int = 2

ARENA_HEADER_SIZE: int = 192

CELL_SIZE: int = 64

REF_SIZE: int = 24

TYPE_SIZE: int = 48

FIELD_SIZE: int = 48

KIND_BLOCK: int = 255

KIND_DEAD: int = 254

BLOCK_INDEX: int = 1

BLOCK_NAMES: int = 2

NAME_BLOCK_PAYLOAD: int = 4096

SUPERBLOCK_SIZE: int = 4096

SUPERBLOCK_VERSION: int = 1

OBJECT_HEADER_SIZE: int = 24

OBJECT_ALIGN: int = 8

HANDLE32_NONE: int = 4294967295

HANDLE32_INDEX_BITS: int = 20

HANDLE32_GEN_BITS: int = 12

OBJ_MODULE: int = 1

OBJ_FUNC: int = 2

OBJ_HASH: int = 3

OBJ_SPEC: int = 4

OBJ_SYM: int = 5

OBJ_BLOB: int = 6

OBJ_FRAME: int = 7

OBJ_FREE: int = 65535

OBJ_JOURNAL: int = 8

OFLAG_RO: int = 1

OFLAG_ROOT: int = 2

OFLAG_DIRTY: int = 4

OFLAG_JOURNALED: int = 8

FREE_PREV: int = 0

FREE_NEXT: int = 8

FREE_LINKS: int = 16

FREE_MIN_SPAN: int = 40

INDEX_OFFSET: int = 4096

INDEX_SELF: int = 0

INDEX_SYMS: int = 1

SLOT_SIZE: int = 16

SLOTS_HEAD_SIZE: int = 8

MODULE_SIZE: int = 8

SYMS_HEAD_SIZE: int = 8

SYMS_ANONYMOUS: int = 0

UNDO_SIZE: int = 16

JOURNAL_HEAD_SIZE: int = 24

JOURNAL_GRANULE: int = 8

class ObjectArena:
    @staticmethod
    def create(limit: int, fixed: bool = False) -> ObjectArena: ...

    @staticmethod
    def attach(data: bytes, fixed: bool = False) -> ObjectArena: ...

    def to_bytes(self) -> bytes: ...

    def s_allocate(self, room: int) -> int: ...

    def s_release(self, offset: int) -> None: ...

    def s_ensure(self, at: int, length: int) -> None: ...

    def verify(self) -> None: ...

    def bootstrap(self, name: str, capacity: int = 1024) -> None: ...

    def new_object(self, parent: int, name: str | None, kind: int, room: int) -> int: ...

    def find(self, parent: int, name: str) -> int: ...

    def load(self, handle: int) -> tuple: ...

    def release(self, handle: int) -> None: ...

    def intern(self, text: str) -> int: ...

    def symbol(self, id: int) -> str: ...

    def journal_commit(self) -> None: ...

    def journal_rollback(self) -> None: ...

    def journal_records(self) -> int: ...

    @property
    def root_handle(self) -> int: ...

    def record(self, offset: int) -> tuple: ...

    def links(self, offset: int) -> tuple: ...

    @property
    def committed(self) -> int: ...

    @property
    def limit(self) -> int: ...

    @property
    def need(self) -> int: ...

class ArenaError(RuntimeError):
    pass
