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

class ArenaError(RuntimeError):
    pass
