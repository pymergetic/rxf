"""The import lane over lwIP, slice 1 (docs/23 4.4): what imports, what links, what is refused.

``native/lwip/core_slice.c`` is the card's translation unit (the verbatim upstream files of
``third_party/lwip``, included, never edited). This tool runs ``tools/native/rximport.py`` over
it as far as it honestly goes and answers three tiers, each a fact about the importer and not a
judgement of this tool:

1. **lowered** -- ``rximport.graphs_of(text, MODULE, {name})``: the function lowers to a
   ``SemanticGraph`` or the importer raises ``Unsupported`` with its reason. One function at a
   time, so one refusal never hides another function. The importer stops at the FIRST thing it
   cannot lower inside a body: a refused body may hide a second reason behind the one named.
2. **linked** -- the lowered graphs applied to the starter as one delta. A call card whose callee
   is no program of the image (libc, the SLIP line's ``sio_*``, a refused sibling) is an
   unresolved reference and the delta does not link; the caller is dropped and the delta applied
   again until it links. What survives is the set of bodies that exist as imported programs.
3. **composed** -- ``compose.programs`` over the linked image: the program words of each body.

``python tools/native/lwip_import.py`` prints the tables; ``--write docs/35-LWIP-IMPORT-TABLE.md``
fills the generated block of that document (its prose is hand-written). The importer's own compile entrance is used as it is (``compile_c``: clang-18
-O1, ``RXF_IMPORT_FACES``); the include path rides the compiler's ``CPATH``, never a flag the
importer would have to grow.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import compose
import rximport

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.delta import DeltaError
from pymergetic.rxftk.core.object import Image

ROOT = Path(__file__).resolve().parents[2]
SLICE = ROOT / "native" / "lwip" / "core_slice.c"
INCLUDES = (ROOT / "native" / "lwip", ROOT / "third_party" / "lwip" / "src" / "include")
STARTER = ROOT / "starter"
MODULE = "lwip"

# the reference line of a delta that does not link:
#   "  lwip.<fn>.<fn>_graph.block[0].op7: reference to <name> does not resolve"
_UNRESOLVED = re.compile(r"^\s*(\S+): reference to (\S+) does not resolve\s*$")


@contextmanager
def include_path() -> Iterator[None]:
    """The compiler's include path for the card (``CPATH``): the port's options and the vendored
    headers. The importer compiles with a fixed command line, so the path travels in the
    environment the compiler reads anyway."""
    before = os.environ.get("CPATH")
    os.environ["CPATH"] = os.pathsep.join(str(path) for path in INCLUDES)
    try:
        yield
    finally:
        if before is None:
            del os.environ["CPATH"]
        else:
            os.environ["CPATH"] = before


def compile_slice(source: Path = SLICE) -> str:
    """The textual LLVM IR of a translation unit of the card (the card's own, or a corpus file
    that includes it), by the importer's own entrance."""
    with include_path():
        return rximport.compile_c(source)


@dataclass
class Report:
    """What the import lane did with the card."""

    functions: list[str]  # every body of the module, in the IR's order
    lowered: set[str] = field(default_factory=set)
    refused: dict[str, str] = field(
        default_factory=dict
    )  # tier 1: name -> the importer's reason
    unlinked: dict[str, list[str]] = field(
        default_factory=dict
    )  # tier 2: name -> callees
    linked: set[str] = field(default_factory=set)
    composed: set[str] = field(default_factory=set)
    # the callees no program answers, with the bodies that call them (tier 2)
    unresolved: dict[str, list[str]] = field(default_factory=dict)


def lower_one(
    text: str, name: str, report: Report, graphs: dict[str, rximport.LGraph]
) -> bool:
    """Tier 1 for one function: its graph, or the importer's refusal in the report."""
    try:
        graphs.update(rximport.graphs_of(text, MODULE, {name}))
    except (rximport.Unsupported, rximport.ParseError) as error:
        report.refused[name] = str(error)
        return False
    report.lowered.add(name)
    return True


def lower_each(
    text: str, names: set[str] | None = None
) -> tuple[Report, dict[str, rximport.LGraph]]:
    """Tier 1: every function of the module on its own (or just ``names`` and the module's
    boot: ``link`` lowers a callee the call cards name when it is asked for)."""
    parsed, _structs, _globals = rximport.parse_module(text)
    report = Report(functions=[function.name for function in parsed])
    graphs: dict[str, rximport.LGraph] = {}
    for name in report.functions:
        if names is None or name in names or name == rximport.WIRES_BOOT:
            lower_one(text, name, report, graphs)
    return report, graphs


def link(
    report: Report, graphs: dict[str, rximport.LGraph], text: str
) -> tuple[Image, dict[str, bytes]]:
    """Tier 2 and 3: apply the lowered graphs as one delta; drop what does not link; compose.

    A call card that names a body of the module not lowered yet (a run of ``lower_each`` over
    some names only) lowers it and goes on; a body the importer refuses is a refusal like any
    other and its callers are dropped."""
    live = {name: graph for name, graph in graphs.items() if name in report.lowered}
    while True:
        image = tree.load(str(STARTER))
        try:
            image.apply(rximport.emit_delta(list(live.values()), MODULE))
        except DeltaError as error:
            blocked: dict[str, set[str]] = {}
            for line in str(error).splitlines():
                found = _UNRESOLVED.match(line)
                if found is None:
                    continue
                path, callee = found.groups()
                parts = path.split(".")
                owner = parts[1] if parts[0] == MODULE and len(parts) > 1 else None
                if owner is None or owner not in live:
                    raise
                blocked.setdefault(owner, set()).add(callee)
            if not blocked:
                raise
            added: set[str] = (
                set()
            )  # lowered for a caller of this pass: it resolves on the next
            for owner, callees in blocked.items():
                missing = set(callees)
                for callee in sorted(callees):
                    bare = callee.removeprefix(f"{MODULE}.")
                    if bare in added and callee.startswith(f"{MODULE}."):
                        missing.discard(callee)
                    elif (
                        callee.startswith(f"{MODULE}.")
                        and bare in report.functions
                        and bare not in report.lowered
                        and bare not in report.refused
                        and lower_one(text, bare, report, graphs)
                    ):
                        live[bare] = graphs[bare]
                        added.add(bare)
                        missing.discard(callee)
                if missing:
                    report.unlinked[owner] = sorted(missing)
                    for callee in missing:
                        report.unresolved.setdefault(callee, []).append(owner)
                    del live[owner]
            continue
        break
    report.linked = set(live)
    programs = compose.programs(image)
    report.composed = {
        key.removeprefix(f"{MODULE}.")
        for key in programs
        if key.startswith(f"{MODULE}.")
    }
    return image, programs


def analyze(
    source: Path = SLICE, names: set[str] | None = None
) -> tuple[Report, Image, dict[str, bytes]]:
    """The whole lane over a translation unit: the report, the linked image, its program words.
    ``names`` limits tier 1 to those bodies and what their call cards reach."""
    text = compile_slice(source)
    report, graphs = lower_each(text, names)
    image, programs = link(report, graphs, text)
    return report, image, programs


# ---- the document ----------------------------------------------------------------------------


def classify(reason: str) -> str:
    """The class of an importer refusal, by the words the importer itself uses."""
    if "the intrinsic llvm." in reason:
        return "intrinsic"
    if "frame slot" in reason:
        return "local-address"
    if "a pointer to a value" in reason:
        return "out-pointer"
    if "an instruction outside the subset" in reason:
        return "constant-expression"
    return "other"


CLASS_TEXT = {
    "intrinsic": (
        "an LLVM intrinsic the importer has no lowering for",
        (
            "rximport `lower_intrinsic` knows umin/umax/smin/smax at i64 only, fshl/fshr, memcpy, "
            "memset, lifetime and the `*.with.overflow` family; clang -O1 turns lwIP's byte swaps "
            "(`lwip_htons`, `PP_HTONS`) into `llvm.bswap.iN` and its `LWIP_MIN`/`LWIP_MAX` "
            "clamps of narrow types into `llvm.umin.i16`/`llvm.smax.i32`, `abs()` into "
            "`llvm.abs.i32`"
        ),
    ),
    "local-address": (
        "address arithmetic on a local's own bytes",
        (
            "a local array (`u8_t` / `char` buffer on the C stack) indexed by a getelementptr: "
            "the pass does no arithmetic on a frame slot's address (docs/23 4.1)"
        ),
    ),
    "out-pointer": (
        "a write through a pointer the caller gave, out of line",
        (
            "the call-card frame cannot carry a raw pointer to a caller's value (docs/23 4.1: "
            "the out-param call card is the i2 lane's open gate)"
        ),
    ),
    "constant-expression": (
        "a constant expression the operand parser does not carry",
        (
            "`LWIP_MEM_ALIGN` of a static array is `and (sub (add (ptrtoint @g, N), 1), -16)` "
            "folded into one operand: integer arithmetic inside a constant expression"
        ),
    ),
    "other": ("other", ""),
}


def describe(name: str, reason: str) -> str:
    """The reason of a refusal, shortened to its own words (no repeated function prefix)."""
    text = reason.split(": ", 1)[1] if ": " in reason else reason
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= 240 else text[:237] + "..."


BEGIN = (
    "<!-- lwip-import:begin (generated by tools/native/lwip_import.py; do not edit) -->"
)
END = "<!-- lwip-import:end -->"


def render(report: Report) -> str:
    """The generated block of docs/35: the tables of what the import lane made of the card."""
    out: list[str] = []
    emit = out.append
    emit("| tier | what it asks | count |")
    emit("|---|---|---|")
    emit(f"| module | bodies in the module | {len(report.functions)} |")
    emit(
        f"| 1 lowered | `rximport.graphs_of` lowers the body to a `SemanticGraph` | "
        f"{len(report.lowered)} |"
    )
    emit(
        f"| 1 refused | the importer raises, with its reason (table below) | {len(report.refused)} |"
    )
    emit(
        f"| 2 linked | the lowered graphs apply as one delta: every call card resolves | "
        f"{len(report.linked)} |"
    )
    emit(
        f"| 2 unlinked | lowered, but a callee is no program of the image (table below) | "
        f"{len(report.unlinked)} |"
    )
    emit(
        f"| 3 composed | `compose.programs` makes program words of the linked body | {len(report.composed)} |"
    )
    emit("")
    emit(
        "A refused body is refused for the FIRST thing the importer cannot lower in it; a second "
        "reason may sit behind the one named. A body that inlines a refused callee carries that "
        "callee's refusal (`udp_send` is refused for the `htons` of `udp_sendto_if_src`)."
    )
    emit("")

    emit("### Tier 1: refused by the importer")
    emit("")
    by_class: dict[str, list[str]] = {}
    for name in report.functions:
        if name in report.refused:
            by_class.setdefault(classify(report.refused[name]), []).append(name)
    emit("| class | bodies | what it is |")
    emit("|---|---|---|")
    for key, (title, _detail) in CLASS_TEXT.items():
        if key in by_class:
            emit(f"| {key} | {len(by_class[key])} | {title} |")
    emit("")
    for key, (title, detail) in CLASS_TEXT.items():
        if key not in by_class:
            continue
        emit(f"#### {key}: {title}")
        emit("")
        if detail:
            emit(detail + ".")
            emit("")
        emit("| body | the importer's reason |")
        emit("|---|---|")
        for name in by_class[key]:
            emit(f"| `{name}` | {describe(name, report.refused[name])} |")
        emit("")

    emit("### Tier 2: lowered but not linked")
    emit("")
    emit("| body | callees no program of the image answers |")
    emit("|---|---|")
    for name in report.functions:
        if name in report.unlinked:
            emit(f"| `{name}` | {', '.join(f'`{c}`' for c in report.unlinked[name])} |")
    emit("")
    emit("The unresolved names, by what they are:")
    emit("")
    emit("| callee | what it is | called by |")
    emit("|---|---|---|")
    for callee in sorted(report.unresolved):
        callers = ", ".join(f"`{c}`" for c in sorted(report.unresolved[callee]))
        emit(f"| `{callee}` | {callee_kind(callee, report)} | {callers} |")
    emit("")

    emit("### Tier 3: imported and composed")
    emit("")
    emit(
        f"{len(report.composed)} bodies exist as programs of the image; each is one "
        "`lwip.<name>` of `compose.programs`:"
    )
    emit("")
    emit(
        ", ".join(f"`{name}`" for name in report.functions if name in report.composed)
        + "."
    )
    emit("")

    emit("### The echo path, named")
    emit("")
    emit(
        "One UDP datagram in and echoed out is `slipif_poll` (it carries the `static inline` "
        "`slipif_input` and its SLIP deframing) -> `netif_input` -> `ip4_input` -> `udp_input` -> "
        "the pcb's `recv` word -> `udp_sendto` -> `ip4_output_if_src` -> the netif's `output` "
        "word -> `slipif_output_v4` -> `sio_send`. The first body of the path that the importer "
        "refuses ends the imported path:"
    )
    emit("")
    emit("| body | status |")
    emit("|---|---|")
    for step in ECHO_PATH:
        emit(f"| `{step}` | {status_of(step, report)} |")
    emit("")
    return "\n".join(out) + "\n"


def splice(document: str, block: str) -> str:
    """The document with its generated block replaced (the prose around it is hand-written)."""
    head, found, rest = document.partition(BEGIN)
    if not found or END not in rest:
        raise ValueError(f"the document has no {BEGIN} ... {END} block to fill")
    _old, _end, tail = rest.partition(END)
    return f"{head}{BEGIN}\n\n{block}\n{END}{tail}"


ECHO_PATH = (
    "slipif_poll",
    "netif_input",
    "ip4_input",
    "udp_input",
    "udp_sendto",
    "ip4_output_if_src",
    "slipif_output_v4",
)


def status_of(name: str, report: Report) -> str:
    if name in report.composed:
        return "imported and composed"
    if name in report.unlinked:
        return "lowered, not linked: " + ", ".join(
            f"`{c}`" for c in report.unlinked[name]
        )
    if name in report.refused:
        return f"refused ({classify(report.refused[name])}): " + describe(
            name, report.refused[name]
        )
    return "not a body of the module"


def callee_kind(callee: str, report: Report) -> str:
    if "global_" in callee:
        return (
            "a module-level constant the boot wiring names but the delta does not place"
        )
    if callee.startswith(f"{MODULE}."):
        bare = callee.removeprefix(f"{MODULE}.")
        if bare in report.refused:
            return f"a body of the card that the importer refuses ({classify(report.refused[bare])})"
        return "a body of the card that did not link"
    if callee.startswith("sio_"):
        return "the SLIP line provider of `sio.h`: the per-seat fill (docs/25 §13), no card yet"
    if callee in ("strlen", "strncmp", "strtol", "strchr", "memcmp"):
        return "a libc face: the subset imports no libc; needs an IR body under `RXF_IMPORT_FACES`"
    return "no program of the image"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lwip_import", description=(__doc__ or "").splitlines()[0]
    )
    parser.add_argument(
        "--write",
        metavar="PATH",
        help="fill the generated block of this document (docs/35)",
    )
    args = parser.parse_args(argv)
    report, _image, _programs = analyze()
    block = render(report)
    if args.write:
        target = Path(args.write)
        target.write_text(splice(target.read_text(), block))
    else:
        sys.stdout.write(block)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
