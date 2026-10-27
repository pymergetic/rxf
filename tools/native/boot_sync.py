"""The glue of ``boot/`` as code objects of the starter (docs/31 §4).

One built code object per artifact: the glue that hands control to the image
and the entry code it calls are defined by the files of ``boot/`` their family
names (its link script rides with them), and the image carries that fact with
the same provenance as everything else -- the code names its source
(``Code.source``, the edge the source side computes the other direction of),
and the pin says what the build makes: the byte count and the ``sha256`` of the
artifact. The bytes themselves are derived data like every other body: ``make
-C boot`` builds them from the source and the recipe the image carries, so the
starter keeps source, recipe and pin and not the artifact.

The glue is the platform's fill of the startup seam (docs/28's glue rule), not a
library function that runs everywhere: ``implementation=Capability`` is the
model's word for that, and the codes are the build history of the fill. The
recipe that made them is the ``boot/Makefile`` recipe, carried as its own
``CompilerProvenance`` (``boot.recipe``): the glue is not a Seed build and
claims no Seed recipe.

``sync.py`` calls ``write_codes`` on its working copy (the pins of the previous
run are carried; running it twice writes the same tree). The pins come from a
real build:

    python tools/native/boot_sync.py build STARTER   # make -C boot, fresh pins
    python tools/native/boot_sync.py STARTER         # the objects, pins carried

``tests/test_boot_codes.py`` pins that what ``make`` builds IS what the pins
name -- the same rule ``tests/test_program_sources.py`` pins for the programs.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Mapping

RXF = "urn:pymergetic:rxf"
ET.register_namespace("rxf", RXF)

ROOT = Path(__file__).resolve().parents[2]
BOOT = ROOT / "boot"
MODULE = Path("pymergetic/rxf/boot.rxfd")

# The glue of each family: the Function it is, the files that define it (its
# meaning, as ``live_sync.semantic_digest`` spells it), and the Code objects it
# builds -- one per artifact, each code naming its source and the body the
# build makes. One artifact per family because the build makes one: the x86_64
# machine boots its sector and its UEFI stub as two different artifacts.
#
# What may be pinned is everything that carries no part of the starter. The
# packed images (build/image_*.bin) are the linked v7 image of the starter
# (world_v7.h, docs/32 1) with the carrier and the entry code -- they carry the
# starter, hence these pins, so a pin of them (or of a container that carries
# them, like the aarch64 seat's boot ELF) would name itself and
# ``boot_sync.py build`` could never settle. That is the linked image's one pin
# story: none in here, ``tests/test_image.py`` pins the arena byte for byte.
# What the image pins instead is each machine's glue and entry code: the glue
# without the image it runs (``body_aarch64`` is the bytes boot_aarch64.elf
# carries at 0x40080000 beside the image), and the entry code of the seats
# (the two x86_64 seats run one entry, so their families fold).
FAMILIES = (
    {
        "function": "entry_aarch64",
        "sources": (
            "boot/image_stub/entry.c",
            "boot/image_stub/entry_v7.ld",
            "boot/image_stub/uart_aarch64.c",
            "boot/image_stub/uart.h",
            "boot/image_stub/world_v7.h",
        ),
        "codes": ("aarch64_linux",),
        "body": "body_entry_aarch64",
        "artifact": "build/entry_v7_aarch64.bin",
        "format": "Machine",
        "signature": "run",
    },
    {
        "function": "entry_x86_64",
        "sources": (
            "boot/image_stub/entry.c",
            "boot/image_stub/entry_v7.ld",
            "boot/image_stub/uart_x86_64.c",
            "boot/image_stub/uart.h",
            "boot/image_stub/world_v7.h",
        ),
        # one entry for both x86_64 seats: the BIOS and the UEFI seat load the
        # same packed image and call the same rxf_graph_run at the map's row
        "codes": ("x86_64_bios", "x86_64_uefi"),
        "body": "body_entry_x86_64",
        "artifact": "build/entry_v7_x86_64.bin",
        "format": "Machine",
        "signature": "run",
    },
    {
        "function": "start_aarch64",
        "sources": ("boot/aarch64.S", "boot/aarch64.ld"),
        "codes": ("aarch64_linux",),
        "body": "body_aarch64",
        "artifact": "build/boot_aarch64.bin",
        "format": "Machine",
    },
    {
        "function": "start_uefi",
        "sources": (
            "boot/uefi_stub.c",
            "boot/image_stub/uart_x86_64.c",
            "boot/image_stub/uart.h",
            "boot/image_stub/world_v7.h",
        ),
        "codes": ("x86_64_uefi",),
        "body": "body_uefi",
        "artifact": "build/BOOTX64.EFI",
        "format": "Machine",
    },
    {
        "function": "start_wasm32",
        "sources": (
            "boot/wasm_shim.c",
            "boot/image_stub/entry.c",
            "boot/image_stub/uart_wasm32.c",
            "boot/image_stub/uart.h",
            "boot/image_stub/world_v7.h",
        ),
        # the wasm32 module is its glue AND its entry (its `run` is the face),
        # so the entry's code on this seat is this body, not a family of its own
        "codes": ("wasm32",),
        "body": "body_wasm32",
        "artifact": "build/rxf_boot.wasm",
        "format": "Wasm",
    },
    {
        "function": "start_x86_64",
        "sources": ("boot/x86_64.S", "boot/x86_64.ld"),
        "codes": ("x86_64_bios",),
        "body": "body_bios",
        "artifact": "build/boot_x86_64.bin",
        "format": "Machine",
    },
)

# The entry convention (boot/README.md): ``run(context, handle, args, argc,
# out) -> status`` -- the glue resolves the handle to the program object and the
# world's entry executes it, writing the answer to ``out``. The ABI shapes of
# the call are per seat; this is the one face they all speak.
RUN_PARAMETERS = (
    ("context", "uint64_t"),
    ("handle", "uint64_t"),
    ("args", "uint64_t"),
    ("argc", "uint64_t"),
    ("out", "uint64_t"),
)

# The runtime target a Code names (the seat's ABI, the one the glue speaks to the
# entry it calls).
TARGETS = {
    "aarch64_linux": "aarch64_linux_aapcs64",
    "aarch64_uefi": "aarch64_uefi_aapcs64",
    "wasm32": "wasm32_wasm_c",
    "x86_64_bios": "x86_64_bios_sysv",
    "x86_64_uefi": "x86_64_uefi_sysv",
}


def family_of(name: str) -> dict:
    return next(f for f in FAMILIES if f["function"] == name)


# What ``CompilerProvenance`` records as ``compiler_major``: the boot recipe's
# major (the meaning ``native_build.SEED_MAJOR`` has for the Seed recipes).
BOOT_MAJOR = "1"


def recipe() -> dict[str, object]:
    """Everything that turns the glue sources into the artifacts of its seats."""
    return {
        "compiler": "make",
        "version": BOOT_MAJOR,
        "seats": {
            str(seat): TARGETS[str(seat)]
            for family in FAMILIES
            for seat in family["codes"]
        },
        "artifacts": {
            str(family["function"]): str(family["artifact"]) for family in FAMILIES
        },
    }


def recipe_digest() -> str:
    """SHA-256 of the recipe and the sources it names (the shape
    ``native_build.recipe_digest`` gives the Seed recipes): a changed source or
    seat map moves the pin."""
    digest = hashlib.sha256()
    digest.update(
        json.dumps(recipe(), sort_keys=True, separators=(",", ":")).encode()
    )
    for path in (BOOT / "Makefile", *(ROOT / s for f in FAMILIES for s in f["sources"])):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def semantic_digest(family: dict) -> str:
    """What the glue of a family means: its name and the files that define it."""
    path = f"pymergetic.rxf.boot.{family['function']}"
    text = "\n".join((ROOT / source).read_text() for source in family["sources"])
    return hashlib.sha256(f"{path}\n{text}".encode()).hexdigest()


def source_name(source: str) -> str:
    """``boot/x86_64.S`` is ``boot_x86_64_S`` (``source_sync.boot_name_of``)."""
    path = Path(source)
    tail = [*path.parts[1:-1], path.stem]
    if path.suffix:
        tail.append(path.suffix[1:])
    return "_".join(("boot", *tail))


def build() -> dict[str, bytes]:
    """``make -C boot``: the bytes of each body, by body name (the toolchain's)."""
    done = subprocess.run(
        ["make", "--no-print-directory", "all"],
        cwd=BOOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise ValueError(f"make -C boot failed:\n{done.stdout}\n{done.stderr}")
    return {
        str(family["body"]): (BOOT / str(family["artifact"])).read_bytes()
        for family in FAMILIES
    }


def pins_of(starter: Path) -> dict[str, tuple[int, str]]:
    """The pins the tree carries, by body name: ``{body: (byte_count, digest)}``."""
    path = starter / MODULE
    if not path.exists():
        return {}
    document = ET.parse(path)
    pins: dict[str, tuple[int, str]] = {}
    for blob in document.getroot().iter("CodeBlob"):
        pins[str(blob.get("name"))] = (
            int(str(blob.get("byte_count"))),
            str(blob.get("content_digest")),
        )
    return pins


def insert_sorted(module: ET.Element, element: ET.Element) -> None:
    """The order the canonical writer keeps: children sorted by name."""
    name = element.get("name", "")
    for position, sibling in enumerate(module):
        if (sibling.get("name") or "") > name:
            module.insert(position, element)
            return
    module.append(element)


def signature_of(family: Mapping[str, object]) -> ET.Element:
    """The Signature the family's codes speak.

    The glue is entered and calls nothing (``void()``); the world the glue runs
    is entered through the entry convention, so its signature is
    ``run(context, handle, args, argc, out) -> status``.
    """
    run = family.get("signature") == "run"
    made = ET.Element(
        "Signature",
        {
            "name": "signature",
            "return_type": "uint64_t" if run else "void",
            "parameter_count": str(len(RUN_PARAMETERS)) if run else "0",
            "result_count": "0",
        },
    )
    if run:
        for index, (name, value_type) in enumerate(RUN_PARAMETERS, start=1):
            ET.SubElement(
                made,
                "Parameter",
                {"name": name, "value_type": value_type, "index": str(index)},
            )
    return made


def write_codes(
    starter: Path, pins: Mapping[str, tuple[int, str]] | None = None
) -> dict[str, tuple[int, str]]:
    """``pymergetic.rxf.boot``: the glue of every arch as Function, Codes and pins.

    With ``pins`` (from ``build``) the pins are what the build made; without them
    the pins the tree already carries move over untouched. Returns the pins the
    tree now holds.
    """
    carried = pins if pins is not None else pins_of(starter)
    missing = [f["body"] for f in FAMILIES if f["body"] not in carried]
    if missing:
        raise ValueError(f"no pin for {sorted(missing)}: run boot_sync.py build")
    root = ET.Element("delta")
    module = ET.SubElement(
        root,
        "Module",
        {"name": "boot", "category": "ModuleCategory.Library"},
    )
    for alias, target in (
        ("boot", "pymergetic.rxf.boot"),
        ("call", "pymergetic.rxf.roles.call"),
        ("kinds", "pymergetic.rxf.kinds"),
        ("sources", "pymergetic.rxf.sources"),
        ("targets", "pymergetic.rxf.targets"),
    ):
        ET.SubElement(module, "Alias", {"name": alias, "target": target})
    ET.SubElement(
        module,
        "CompilerProvenance",
        {"name": "recipe", "compiler_major": BOOT_MAJOR, "recipe_digest": recipe_digest()},
    )
    for family in sorted(FAMILIES, key=lambda f: str(f["function"])):
        function = str(family["function"])
        digest = semantic_digest(family)
        made = ET.Element(
            "Function",
            {
                "name": function,
                "implementation": "@kinds.FunctionImplementation.Capability",
                "layer": "@kinds.FunctionLayer.Platform",
                "semantic_version": "1",
                "signature": f"@boot.{function}.signature",
                "semantic_digest": digest,
            },
        )
        for seat in sorted(family["codes"]):
            code = ET.Element(
                "Code",
                {
                    "name": str(seat),
                    "owner_function": f"@boot.{function}",
                    "runtime_target": f"@targets.{TARGETS[str(seat)]}",
                    "signature": f"@boot.{function}.signature",
                    "format": f"@kinds.CodeFormat.{family['format']}",
                    "semantic_version": "1",
                    "byte_count": str(carried[str(family["body"])][0]),
                    "semantic_digest": digest,
                    "source": f"@sources.{source_name(str(family['sources'][0]))}",
                    "body": f"@boot.{function}.{family['body']}",
                },
            )
            ET.SubElement(
                code,
                f"{{{RXF}}}ref",
                {
                    "role": "@call.Provenance",
                    "kind": "RefKind.Data",
                    "target": "@boot.recipe",
                },
            )
            insert_sorted(made, code)
        insert_sorted(
            made,
            ET.Element(
                "CodeBlob",
                {
                    "name": str(family["body"]),
                    "byte_count": str(carried[str(family["body"])][0]),
                    "content_digest": carried[str(family["body"])][1],
                },
            ),
        )
        insert_sorted(made, signature_of(family))
        module.append(made)
    ET.ElementTree(root).write(
        starter / MODULE, encoding="UTF-8", xml_declaration=True
    )
    return dict(carried)


def main() -> None:
    arguments = sys.argv[1:]
    refresh = "build" in arguments
    rest = [a for a in arguments if a != "build"]
    starter = Path(rest[0]) if rest else ROOT / "starter"
    pins = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in build().items()} if refresh else None
    held = write_codes(starter, pins)
    for name in sorted(held):
        print(f"{name}: {held[name][0]} bytes {held[name][1][:16]}")


if __name__ == "__main__":
    main()
