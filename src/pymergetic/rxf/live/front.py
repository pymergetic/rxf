"""The front end of a running image driven over its channel (docs/19).

The console types ``.rxf``; the image compiles it. The source travels as an object of the
arena (one physical delta over the rings, ``rxf:data`` like every ByteBuffer), the request of
``native/front/words.rxf`` points into its payload, the words come back as ``read`` frames, and
one delta installs the Function they name. Nothing here compiles: ``tools/native/rxfc.py`` is
the same compiler on the host, and this path never calls it.

A run is not a frame: whoever runs the image writes the request memory and calls the
interpreter (the harness provider, docs/19 "who runs when"). That host is the `Machine` below.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from xml.etree import ElementTree as ET

from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live import physical
from pymergetic.rxf.live.client import Client

RXF = "urn:pymergetic:rxf"
DATA = f"{{{RXF}}}data"
REF = f"{{{RXF}}}ref"

MODULE = "pymergetic.rxf.selfedit"
FRONT_FUNCTION = "front"
BYTECODE = "bytecode"
INTERPRETER = "pymergetic.rxf.foundation.graph.run"  # where a run of words goes (roles.call)
MAGIC = 0x31475852  # "RXG1": the header of every program of the interpreter (native/graph.h)

CELL_BYTES = 64  # the fixed part of a cell of the arena (the ground layout)
REQUEST_BYTES = 96  # the request of one run: 12 words (native/front/words.rxf)
ROW_BYTES = 32  # CONST_ROW == FILE_ENTRY
ROWS_AT = 0x100  # the rows of a request come after the request itself
REQUEST_HEADROOM = 0x100000  # and before the work memory of the run
FRONT_WORK = 8_963_144  # WORK_BYTES of native/front/words.rxf
OUT_ROOM = 0x10000  # the room the words of the program are written into

# the words of the request (native/front/words.rxf)
R_SOURCE = 0
R_SOURCE_SIZE = 8
R_OUT = 16
R_OUT_CAPACITY = 24
R_WORK = 32
R_WORK_CAPACITY = 40
R_OUT_SIZE = 48
R_AT = 56
R_FILES = 64
R_FILE_COUNT = 72
R_CONSTS = 80
R_CONST_COUNT = 88


class NotReady(ValueError):
    """The image has no front end to run."""


@dataclass(frozen=True)
class Region:
    """Where a region of the live block lies in the memory of the machine (docs/19)."""

    at: int
    capacity: int


class Machine(Protocol):
    """Whoever runs the image: its memory and its Functions.

    The channel carries everything the console says and reads. A run is not a frame: the host
    of the image (the harness provider) writes the request memory and starts the interpreter.
    """

    def read(self, address: int, size: int) -> bytes: ...

    def write(self, address: int, data: bytes) -> None: ...

    def run(self, identity: int, arguments: bytes = b"") -> tuple[int, int]: ...


@dataclass(frozen=True)
class Verdict:
    """What one run of the front end answers."""

    ok: bool
    words: bytes
    at: int  # the line it stopped at, 0 when it did not
    status: int  # the refusal it stopped with, 0 when the words are written


@dataclass(frozen=True)
class Installed:
    """One Function a delta installed."""

    path: str
    identity: int  # the object id of the Function
    code_identity: int  # the object id of its words: what a run names (docs/19)
    byte_count: int
    digest: str  # of the image after the delta


@dataclass(frozen=True)
class Entry:
    """A Function of the image: its path and the id the runtime finds it by."""

    path: str
    identity: int


def identity(image: Image, handle: int) -> int:
    """The id the runtime finds an object by: no delta holds a handle.

    The ``object_id`` of a `SemanticObject`, else the first 8 bytes of the SHA-256 of the path.
    """
    if image.type_name(handle) == "SemanticObject":
        values = dict(image.fields(handle))
        if "object_id" in values:
            return int(values["object_id"])
    return int.from_bytes(hashlib.sha256(image.path(handle).encode()).digest()[:8], "little")


def value_of(image: Image, handle: int) -> bytes:
    """The bytes of an object: its payload behind the fixed part of its type (what `place`
    addresses in the memory of the machine)."""
    return bytes(image.get(handle)["payload"])[_value_prefix(image, handle) :]


def _value_prefix(image: Image, handle: int) -> int:
    """How far the value of an object lies behind its payload: the fixed part of its type."""
    info = image.get(handle)
    values = dict(image.fields(info["type"]))
    variable = "Variable" in values.get("flags", "")
    return image.layout(info["type"])["prefix"] if variable else 0


def entries(image: Image, module: str = MODULE) -> list[Entry]:
    """The Functions of `module` and the ids they run under (read-only facts)."""
    parent = image.resolve(module)
    if parent is None:
        return []
    found = []
    for handle in image.children(parent):
        if image.type_name(handle) != "Function":
            continue
        found.append(Entry(image.path(handle), identity(image, handle)))
    return found


def numbers(names: bytes, values: bytes) -> dict[str, int]:
    """The numbers the source may use, as the image keeps them (its names and values objects)."""
    count = len(values) // 8
    split = names.split(b"\0")[:count]
    return {
        name.decode(): value
        for name, (value,) in zip(split, struct.iter_unpack("<Q", values), strict=True)
    }


class Front:
    """The front end of a running image driven over its channel.

    `boot` installs the compiler (the words of the front end, seeded once by the host: the fixpoint
    of docs/19), `compile` sends one program of source through it and answers its words or the
    line it stopped at, `install` puts the words in the image as a Function.
    """

    def __init__(
        self,
        client: Client,
        machine: Machine,
        arena: Region,
        work: Region,
        module: str = MODULE,
    ) -> None:
        if work.capacity < REQUEST_HEADROOM + FRONT_WORK:
            raise ValueError(
                f"the work region of a request takes {REQUEST_HEADROOM + FRONT_WORK} bytes, "
                f"the machine gives {work.capacity}"
            )
        self._client = client
        self._machine = machine
        self._arena = arena
        self._work = work
        self._module = module
        self._state: Image | None = None
        self._live: tuple[Image, dict[int, dict]] | None = None

    # -- what the image has ---------------------------------------------------------------

    def installed(self) -> list[Entry]:
        """The Functions of the module of the console and the ids they run under."""
        return entries(self._image(), self._module)

    def numbers(self) -> dict[str, int]:
        """The numbers the source may use (the const table the request names)."""
        names, values = self._table()
        return numbers(names, values)

    # -- one program through the image ------------------------------------------------------

    def boot(
        self,
        words: bytes,
        source: bytes,
        consts: Mapping[str, int],
        out_room: int = OUT_ROOM,
    ) -> Installed:
        """Install the front end from the words the host seeds it with, and the objects its
        request names: the source, the names and values of the numbers the source may use, and
        the room the words of a program are written into."""
        self._check_words(words)
        names = b"".join(name.encode() + b"\0" for name in consts)
        values = b"".join(struct.pack("<Q", value) for value in consts.values())
        self._send(_boot_delta(self._module, words, source, names, values, out_room))
        return self._report(f"{self._module}.{FRONT_FUNCTION}")

    def compile(self, source: bytes, files: Mapping[str, bytes] | None = None) -> Verdict:
        """One program through the front end of the image: the words it writes, or the line it
        stopped at. The source travels as an object of the arena; the request points into its
        payload."""
        code = self._front_code()
        self._send(_set_bytes(f"{self._module}.source", source))
        names, values = self._table()
        table = numbers(names, values)
        where = self._place(
            f"{self._module}.source", f"{self._module}.names", f"{self._module}.out"
        )
        source_at = where[f"{self._module}.source"]
        names_at = where[f"{self._module}.names"]
        out_at = where[f"{self._module}.out"]

        consts_at = self._work.at + ROWS_AT
        rows = _const_rows(names_at, names, values)
        files_at = consts_at + len(rows)
        blob_at = files_at + ROW_BYTES * len(files or {})
        file_rows, file_bytes = _file_rows(files or {}, blob_at)
        work_at = self._work.at + REQUEST_HEADROOM
        work_capacity = self._work.capacity - REQUEST_HEADROOM
        if blob_at + len(file_bytes) > work_at:
            raise ValueError("the rows of the request do not fit before its work memory")

        image = self._image()
        out_room = len(image.get(image.handle(f"{self._module}.out"))["payload"])
        request = struct.pack(
            "<12Q",
            source_at,
            len(source),
            out_at,
            out_room,
            work_at,
            work_capacity,
            0,
            0,
            files_at,
            len(files or {}),
            consts_at,
            len(table),
        )
        self._machine.write(consts_at, rows + file_rows + file_bytes)
        self._machine.write(self._work.at, request)

        status, _ = self._machine.run(
            identity(self._image(), code), struct.pack("<2Q", 0, self._work.at)
        )
        out_size, at = struct.unpack(
            "<2Q", self._machine.read(self._work.at + R_OUT_SIZE, 16)
        )
        live = self._read_out(out_room)
        self._mirror_out(live)
        words = live[:out_size] if status == 0 else b""
        return Verdict(status == 0, words, at, status)

    def install(self, name: str, words: bytes, source: bytes) -> Installed:
        """The words of a program as a Function of the image, with the digest of what they mean:
        the source they were compiled from."""
        parameters = self._check_words(words)
        self._send(_install_delta(self._module, name, words, source, parameters))
        return self._report(f"{self._module}.{name}")

    # -- the memory of the machine ----------------------------------------------------------

    def place(self, path: str) -> int:
        """Where the value of an object lies in the memory of the machine: the arena is mapped
        where the image keeps it, and the value is the payload behind the fixed part of its
        type."""
        image, cells = self._live_arena()
        handle = image.handle(path)
        return self._arena.at + cells[handle]["offset"] + CELL_BYTES + _value_prefix(
            image, handle
        )

    # -- internals ---------------------------------------------------------------------------

    def _image(self) -> Image:
        """The state of the image as the host mirrors it (the deltas sent, and what the image
        wrote itself)."""
        if self._state is None:
            self._state = Image.from_bytes(self._client.emit()[0])
        return self._state

    def _live_arena(self) -> tuple[Image, dict[int, dict]]:
        """The arena as it lies in the machine, and every cell of its stream by handle."""
        if self._live is None:
            header = self._machine.read(self._arena.at, 192)
            body = struct.unpack_from("<Q", header, 16)[0]
            arena = self._machine.read(self._arena.at, 192 + body)
            image = Image.from_bytes(arena)
            self._live = (image, {cell["handle"]: cell for cell in image.arena_cells()})
        return self._live

    def _place(self, *paths: str) -> dict[str, int]:
        return {path: self.place(path) for path in paths}

    def _front_code(self) -> int:
        """The words of the front end of the image: what `boot` installs and a run names."""
        image = self._image()
        try:
            return image.handle(f"{self._module}.{FRONT_FUNCTION}.{BYTECODE}")
        except KeyError:
            raise NotReady(
                f"no front end at {self._module}.{FRONT_FUNCTION}: boot it first"
            ) from None

    def _table(self) -> tuple[bytes, bytes]:
        """The names and the values of the numbers the source may use (the const table)."""
        image = self._image()
        return (
            value_of(image, image.handle(f"{self._module}.names")),
            value_of(image, image.handle(f"{self._module}.values")),
        )

    def _read_out(self, size: int) -> bytes:
        """The words the image wrote, read as frames on its own object: the handle is the
        client's, whose table is its own."""
        handle = self._client.resolve(f"{self._module}.out")
        if handle is None:
            raise NotReady(f"no out object at {self._module}.out: boot it first")
        return self._client.read(handle, 0, size)

    def _mirror_out(self, live: bytes) -> None:
        """What the image writes itself (the run of the front end, refused or not) the host
        mirrors before the next delta: the base of a delta that is not the image is refused."""
        image = self._image()
        image.set_payload(image.handle(f"{self._module}.out"), live)

    def _send(self, text: str) -> None:
        """One delta over the rings: compiled against the state the host mirrors, and accepted
        as the image agrees with it."""
        state = self._image()
        compiled = physical.from_xml(Image.from_bytes(state.to_bytes()), ("delta.xml", text))
        state.apply(text)
        digest = self._client.apply(compiled.data)
        if digest != state.digest():
            raise RuntimeError("the image and its mirror disagree")
        self._live = None

    def _report(self, path: str) -> Installed:
        image = self._image()
        code = image.handle(f"{path}.{BYTECODE}")
        return Installed(
            path=path,
            identity=identity(image, image.handle(path)),
            code_identity=identity(image, code),
            byte_count=len(value_of(image, code)),
            digest=image.digest(),
        )

    @staticmethod
    def _check_words(words: bytes) -> int:
        """The header of a program: how many parameters it takes. Junk is refused here."""
        if len(words) < 5 * 8 or len(words) % 8:
            raise ValueError("not program words: the header of a program is 5 words")
        if struct.unpack_from("<Q", words)[0] != MAGIC:
            raise ValueError("not program words: the magic of the interpreter is RXG1")
        return struct.unpack_from("<Q", words, 16)[0]


def _const_rows(names_at: int, names: bytes, values: bytes) -> bytes:
    """The rows of the const table of a request: name, its length, value, 0 (words.rxf)."""
    rows = b""
    offset = 0
    for name, (value,) in zip(
        names.split(b"\0")[: len(values) // 8],
        struct.iter_unpack("<Q", values),
        strict=True,
    ):
        rows += struct.pack("<4Q", names_at + offset, len(name), value, 0)
        offset += len(name) + 1
    return rows


def _file_rows(files: Mapping[str, bytes], blob_at: int) -> tuple[bytes, bytes]:
    """The table of the files an `include` can name: rows of name and text, then their bytes at
    `blob_at` (words.rxf, FILE_ENTRY)."""
    rows = b""
    blob = b""
    at = 0
    for name, text in files.items():
        encoded = name.encode()
        rows += struct.pack(
            "<4Q", blob_at + at, len(encoded), blob_at + at + len(encoded), len(text)
        )
        blob += encoded + text
        at += len(encoded) + len(text)
    return rows, blob


def _code(function: ET.Element, path: str, words: bytes) -> ET.Element:
    """The words of a program as the `bytecode` Code of a Function (tools/native/graph_sync.py
    makes the same element): what it runs as is its Interpreter ref, without one the words are
    not a program to the runtime."""
    code = ET.Element(
        "Code",
        {
            "name": BYTECODE,
            "owner_function": path,
            "signature": str(function.get("signature")),
            "format": "pymergetic.rxf.kinds.CodeFormat.Bytecode",
            "semantic_version": str(function.get("semantic_version")),
            "byte_count": str(len(words)),
            "semantic_digest": str(function.get("semantic_digest")),
            DATA: words.hex(),
        },
    )
    ET.SubElement(
        code,
        REF,
        {
            "role": "pymergetic.rxf.roles.call.Interpreter",
            "kind": "RefKind.Data",
            "target": INTERPRETER,
        },
    )
    return code


def _function(
    module: str,
    name: str,
    words: bytes,
    source: bytes,
    parameters: int,
    parent: str | None,
) -> ET.Element:
    """A Function of one module with its words: the shape the image links (tests/selfedit)."""
    path = f"{module}.{name}"
    attributes = {
        "name": name,
        "implementation": "pymergetic.rxf.kinds.FunctionImplementation.Semantic",
        "layer": "pymergetic.rxf.kinds.FunctionLayer.Application",
        "semantic_version": "1",
        "signature": f"{path}.signature",
        "semantic_digest": hashlib.sha256(source).hexdigest(),
    }
    if parent is not None:
        attributes[f"{{{RXF}}}parent"] = parent
    function = ET.Element("Function", attributes)
    signature = ET.SubElement(
        function,
        "Signature",
        {
            "name": "signature",
            "return_type": "uint64_t",
            "parameter_count": str(parameters),
            "result_count": "1",
            "refusal_set": "pymergetic.rxf.foundation.StdlibRefusals",
        },
    )
    for index in range(parameters):
        slot = ET.SubElement(
            signature, "Parameter", {"name": f"argument[{index}]", "value_type": "uint64_t"}
        )
        if index:
            slot.set("index", str(index))
    ET.SubElement(
        signature, "Result", {"name": "value", "value_type": "uint64_t"}
    )
    function.append(_code(function, path, words))
    return function


def _install_delta(module: str, name: str, words: bytes, source: bytes, parameters: int) -> str:
    """The delta that installs one Function: what the words of the program name."""
    ET.register_namespace("rxf", RXF)
    delta = ET.Element("delta")
    delta.append(_function(module, name, words, source, parameters, module))
    return ET.tostring(delta, encoding="unicode")


def _boot_delta(
    module: str,
    words: bytes,
    source: bytes,
    names: bytes,
    values: bytes,
    out_room: int,
) -> str:
    """The delta that boots the console: the front end of the image and the objects its request
    names. The words are seeded once by the host; the image compiles the rest from then on
    (the fixpoint of docs/19)."""
    ET.register_namespace("rxf", RXF)
    delta = ET.Element("delta")
    holder = ET.SubElement(
        delta,
        "Module",
        {"name": module.rsplit(".", 1)[-1], f"{{{RXF}}}parent": module.rsplit(".", 1)[0]},
    )
    front = _function(
        module,
        FRONT_FUNCTION,
        words,
        source,
        parameters=2,
        parent=None,
    )
    front.set("signature", f"{module}.{FRONT_FUNCTION}.signature")
    signature = front.find("Signature")
    assert signature is not None
    signature.set("return_type", "void")
    for parameter, value_type in zip(
        signature.findall("Parameter"),
        ("pymergetic.rxf.model.Ref", "pymergetic.rxf.foundation.NativeTransaction"),
        strict=True,
    ):
        parameter.set("value_type", value_type)
    result = signature.find("Result")
    assert result is not None
    result.set("value_type", "void")
    holder.append(front)
    holder.append(_blob("source", source))
    holder.append(_blob("names", names))
    holder.append(_blob("values", values))
    holder.append(_blob("out", bytes(out_room)))
    return ET.tostring(delta, encoding="unicode")


def _blob(name: str, data: bytes) -> ET.Element:
    """An object of the arena that is only its bytes: `ByteBuffer` with what it does not
    describe (``rxf:data``, raw)."""
    return ET.Element("ByteBuffer", {"name": name, DATA: data.hex()})


def _set_bytes(path: str, data: bytes) -> str:
    """The delta that replaces the bytes of an object."""
    ET.register_namespace("rxf", RXF)
    delta = ET.Element("delta")
    item = ET.SubElement(delta, f"{{{RXF}}}set", {DATA: data.hex()})
    item.set("path", path)
    return ET.tostring(delta, encoding="unicode")
