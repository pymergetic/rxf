"""The live layer: frames, reads, staged deltas, dumps."""



class Area:
    """
    The live area of an image in host memory; `pump` runs the code of the native leaf.
    """

    def __init__(self, image: bytes, arena_capacity: int, log_capacity: int, stage_capacity: int, work_capacity: int, emit_capacity: int, ring_capacity: int, table_slots: int, arena_committed: int = 0) -> None: ...

    @staticmethod
    def required_size(arena_capacity: int, log_capacity: int, stage_capacity: int, work_capacity: int, emit_capacity: int, ring_capacity: int) -> int:
        """The bytes of memory an area with these capacities takes."""

    @staticmethod
    def shared(memory: object, image: bytes, arena_capacity: int, log_capacity: int, stage_capacity: int, work_capacity: int, emit_capacity: int, ring_capacity: int, table_slots: int, arena_committed: int = 0) -> Area:
        """
        An area laid out in memory that another process can map (a shared-memory segment).
        """

    @staticmethod
    def attach(memory: object) -> Area:
        """
        The area that `shared` laid out in this memory, seen from this process.
        """

    def rx_write(self, data: bytes) -> int:
        """Appends bytes to the ring the image reads; 0 when they do not fit."""

    def tx_read(self, limit: int = 1048576) -> bytes:
        """Takes up to `limit` bytes from the ring the image writes."""

    def pump(self, limit: int = 64) -> int:
        """Serves up to `limit` requests; returns how many."""

    def arena_bytes(self) -> bytes:
        """The bytes of the arena, header to end of the cell stream."""

    def emit_bytes(self) -> bytes:
        """The last dump."""

    def digest(self) -> bytes:
        """The digest of the last dump."""

    @property
    def grow_request(self) -> int: ...

    @property
    def arena_committed(self) -> int: ...

    @property
    def compactions(self) -> int: ...

    @property
    def frames(self) -> int: ...

    @property
    def commits(self) -> int: ...

    @property
    def rejects(self) -> int: ...

    @property
    def last_status(self) -> int: ...

    @property
    def stage_length(self) -> int: ...

    @property
    def break_request(self) -> int: ...

    @break_request.setter
    def break_request(self, arg: int, /) -> None: ...

    @property
    def index_state(self) -> int: ...

    @index_state.setter
    def index_state(self, arg: int, /) -> None: ...

def crc32(data: bytes) -> int:
    """CRC-32 (IEEE) as the frames use it."""
