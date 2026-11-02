"""Running the compiled modules of the Python strand on the host seat (``tests/host/runner.c``).

One harness for every rxpy differential: the modules are imported into one image (the image is
the module tree), the programs of the image's Functions run on the real interpreter of
``native/graph.c`` with the out cells live, and a call is one run. The shape of a function (its
words, its out cells) is read off the image's own Signature (``rxpy.Resolver``), never off the
source, so the harness runs whatever any strand put in the image.
"""

import struct
from collections.abc import Iterator
from contextlib import contextmanager

import compose
import rximport
import rxpy

from . import rxf_native

U64 = (1 << 64) - 1


def signed(word: int) -> int:
    return word if word < (1 << 63) else word - (1 << 64)


class Run:
    """The compiled modules of an image, ready to be called."""

    def __init__(self, image, prefixes: tuple[str, ...]) -> None:
        self.image = image
        self.resolver = rxpy.Resolver(image)
        self.programs = compose.programs(image)
        composer = compose.Composer(image)
        self.cells: dict[int, bytearray] = {}
        self.objects: dict[int, bytes | bytearray] = {}
        for handle in range(image.handle_count):
            if not image.live(handle):
                continue
            values = composer.fields(handle)
            if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
                continue
            if not image.path(handle).startswith(tuple(f"{name}." for name in prefixes)):
                continue
            identity = int(values["object_id"])
            data = bytearray(bytes.fromhex(values.get("data", "")))
            self.cells[identity] = data
            self.objects[identity] = data
        for handle in range(image.handle_count):
            if not image.live(handle) or image.type_name(handle) != "SemanticOperation":
                continue
            callee = composer.fields(handle).get("callee")
            target = image.resolve(callee) if callee else None
            if target is not None and image.path(target) in self.programs:
                self.objects[compose.object_id(image, target)] = self.programs[image.path(target)]
        self.memory = rxf_native.Memory(1 << 16)

    def call(self, module: str, entry: str, words: tuple[int, ...]) -> tuple[int, int, list[int]]:
        """One call: the status, the answer word, and the out words of a tuple return."""
        arity = self.resolver.signature_of(module, entry).outs
        given = [word & U64 for word in words]
        outs = []
        for index in range(arity):
            identity = rximport.object_id_of(f"{module}.{entry}.out_{index}")
            cell = self.cells[identity]
            cell[:] = bytes(len(cell))  # a clean out cell per case
            outs.append(cell)
            given += [identity, 24]  # the handle run-entry ARGS takes (identity, offset)
        status, value = rxf_native.run(
            self.programs[f"{module}.{entry}"],
            struct.pack(f"<{len(given)}Q", *given),
            self.memory,
            objects=self.objects,
        )
        answered = [signed(int.from_bytes(cell[24:32], "little", signed=True)) for cell in outs]
        return status, signed(value), answered

    def cell_of(self, module: str, name: str) -> bytearray:
        return self.cells[rximport.object_id_of(f"{module}.{name}")]


@contextmanager
def running(image, prefixes: tuple[str, ...]) -> Iterator[Run]:
    """The harness of an image whose modules are named by ``prefixes``; frees the memory."""
    run = Run(image, prefixes)
    try:
        yield run
    finally:
        run.memory.close()



def split_entry(names: tuple[str, ...], dotted: str) -> tuple[str, str]:
    """A dotted entry as the (module, Function name) the image holds it under: ``m.f`` is the
    function ``f`` of module ``m`` (the longest module prefix wins, as a package's submodule
    does), and ``m.a.bump`` is the method ``bump`` compiled for the instance ``a``: the Function
    ``a__bump`` of ``m``."""
    for name in sorted(names, key=len, reverse=True):
        if dotted.startswith(name + "."):
            attributes = dotted[len(name) + 1 :].split(".")
            if len(attributes) == 1:
                return name, attributes[0]
            if len(attributes) == 2:
                return name, f"{attributes[0]}__{attributes[1]}"
    raise ValueError(f"no module of {dotted}")


def answer(run: Run, module: str, name: str, words: tuple[int, ...]) -> tuple[int | str, ...]:
    """What a call answers, in the words of the reference's line: the answer word (or a tuple's
    out words), or ``("None",)`` for a procedure -- a def whose Signature has no Result."""
    status, value, outs = run.call(module, name, words)
    assert status == 0, f"{module}.{name}{words}: the run refused ({status})"
    if run.resolver.signature_of(module, name).procedure:
        return ("None",)
    return tuple(outs) if outs else (value,)


def parse_answer(line: str) -> str | tuple[int | str, ...]:
    """One line of a reference driver: ``!ExceptionName``, ``None``, or the answer words."""
    if line.startswith("!"):
        return line
    if line == "None":
        return ("None",)
    return tuple(int(word) for word in line.split())
