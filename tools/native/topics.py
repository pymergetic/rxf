"""The execution policy as topic objects (docs/22: "policy is documentation"; docs/20 S1..S11).

docs/20 stays the human editing format of the policy and this is the build step that lifts it
into the image (docs/22, "Handbooks are topic trees"): one ``DocKind.Topic`` object per policy
statement under the documentation module path the spec names -- ``pymergetic.rxf.docs.execution``
-- its docstring the policy text verbatim. The objects that implement a statement carry a
placement on its topic (``DocRole.Attaches``), so "which objects implement S5?" is a graph
query, not a grep. Everything is ordinary content through the Python API (``Image.doc_add``,
``Image.doc_place``, the delta the module path is made with): the ground grows nothing.

What implements a statement, and where that code lives, is said honestly. The run-cell machine
of S1 to S5 is ``tests/host/smp_sched.c``, the C2/C3 proof harness: host-side C that is not in
the image, so those topics carry no placement -- a standard nothing places on is a standard
nothing implements (docs/22). The in-image implementers are the graph/runner objects the
starter carries (the interpreter, the safe-point leaf, the handle and compaction faces) and the
imported corpus functions of docs/20's S6 flip paragraphs, which a run over an import attaches
wherever the image carries them.

Driven by tests and tools through the one call::

    report = topics.materialize(image)   # the eleven topics + their placements, one transaction
    texts = topics.policy_texts()        # S1..S11 as docs/20 renders them (the lift)

A second materialization of the same image is refused (one edge, said once), and a failure
rolls the image back to what it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pymergetic.rxftk.core.kinds import DocKind, DocRole

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs" / "20-EXECUTION-POLICY.md"
MODULE = "pymergetic.rxf.docs.execution"
STATEMENT_NAMES = tuple(f"S{n}" for n in range(1, 12))


@dataclass(frozen=True)
class Statement:
    """One policy statement (docs/20) and the objects that implement it.

    ``places`` are the implementing objects the image knows (the starter's graph/runner
    objects); ``imported`` are the implementing objects an import run adds (the corpus
    functions docs/20's S6 flips name), attached wherever an image carries them. ``host`` is
    the rest of the implementation and where it lives -- never placed, because it is not in
    the image.
    """

    name: str
    places: tuple[str, ...] = ()
    imported: tuple[str, ...] = ()
    host: str = ""


STATEMENTS: tuple[Statement, ...] = (
    Statement(
        "S1",
        host="tests/host/smp_sched.c -- the state machine, the run payload, and the checksum "
        "(the C2/C3 harness); the product's run machine is not an image object yet",
    ),
    Statement(
        "S2",
        host="tests/host/smp_sched.c -- suspension is the machine's data property; nothing in "
        "the image names a carrier either",
    ),
    Statement(
        "S3",
        host="tests/host/smp_sched.c -- the ready-ring cell (RXF_SMP_CELL_RING); "
        "tests/host/smp_arena.c refuses the ABA handle",
    ),
    Statement(
        "S4",
        host="tests/host/smp_sched.c -- the wake token and the park; the channel pairs of the "
        "proof are tests/host/smp_witness.c runs, not image objects",
    ),
    Statement(
        "S5",
        host="tests/host/smp_sched.c -- the seal (the step's closing duty before the "
        "RUNNING to PARKED CAS); the handoff itself is the ready-ring of S3",
    ),
    Statement(
        "S6",
        places=(
            "pymergetic.rxf.foundation.graph.run",  # "A product's program is one interpreter step (native/graph.c)"
            "pymergetic.rxf.foundation.compile.program",  # "or one safe-point interval of compiled code"
            "pymergetic.rxf.execution.basic.return",  # "The callee's Return copies its out word ..."
            "pymergetic.rxf.execution.basic.refuse",  # "Refuse gives the call its status"
        ),
        imported=(
            "obj.mp_obj_get_int_maybe",  # the out-cell flip: "its mp_int_t *value write becomes an i2 out-cell"
            "obj.mp_obj_is_true",  # the dispatch flip: "lowers now as an i3 slot-lookup program"
            "outparam.pair_out",  # "the out-parameter idiom of tests/import_corpus/outparam.c"
        ),
        host="tests/host/smp_witness.c -- the worker/channel/stale/mpvm witness programs",
    ),
    Statement(
        "S7",
        places=(
            "pymergetic.rxf.foundation.runtime.resolve",  # "resolve(handle) claims the cell ... and returns the payload"
            "pymergetic.rxf.foundation.runtime.resolve_typed",
            "pymergetic.rxf.foundation.runtime.release",  # "release(handle) drops the claim"
            "pymergetic.rxf.foundation.safe.point",  # "The end of a step is a safe point"
            "pymergetic.rxf.foundation.graph.run",  # "no raw pointer outlives a step" (the frame's code pointer)
        ),
        host="tests/host/smp_arena.c -- the claim word (CLAIM_MOVING) and the C_FORWARD record",
    ),
    Statement(
        "S8",
        places=(
            "pymergetic.rxf.foundation.heap.plan_compaction",  # "The pass slides cells down in offset order"
            "pymergetic.rxf.foundation.heap.apply_move_plan",  # "copy the whole cell"
            "pymergetic.rxf.foundation.heap.update_slot",  # "publish the new offset in the slot"
            "pymergetic.rxf.foundation.heap.pin",  # "a pinned cell keeps its source address"
            "pymergetic.rxf.foundation.heap.unpin",
            "pymergetic.rxf.foundation.runtime.publish",  # "Deltas are staged, applied, and journaled as whole operations"
            "pymergetic.rxf.foundation.runtime.rollback",
        ),
        host="tests/host/smp_arena.c -- the sliding pass, the poison tail, and the mutator thread",
    ),
    Statement(
        "S9",
        host="the edge shims (pthreads, asyncio, Metal) are the seats' own fills behind the "
        "three callbacks; no shim is an image object",
    ),
    Statement(
        "S10",
        host="the differential is tests/test_smp.py over the evidence words of "
        "tests/host/smp_witness.c",
    ),
    Statement(
        "S11",
        places=(
            "pymergetic.rxf.foundation.graph.run",  # "a safe point like any other (S7, S11: mp_handle_pending lowers here)"
            "pymergetic.rxf.foundation.safe.point",  # "mp_handle_pending() -- the pending-flag poll -> the safe point itself"
            "pymergetic.rxf.execution.basic.refuse",  # "mp_raise_* -- the raise leaf: the Refuse leaf"
        ),
        host="tests/host/smp_mpmodel.c -- the seam's model of the pending pair; mp_handle_pending "
        "itself is the needs list of S11, recorded there and not implemented",
    ),
)

_BY_NAME = {statement.name: statement for statement in STATEMENTS}


@dataclass(frozen=True)
class Report:
    """What one materialization wrote: the topics, the placements, and what is not here."""

    topics: dict[str, int]  # "S5" -> the topic's handle
    placed: dict[str, tuple[str, ...]]  # "S5" -> the implementing paths attached, sorted
    absent: dict[str, tuple[str, ...]]  # "S5" -> declared implementers this image does not carry


def policy_texts(text: str | None = None) -> dict[str, str]:
    """S1..S11 as docs/20 renders them: the heading line and the body under it, verbatim.

    A section runs from its ``##`` heading to the next ``##`` heading (trailing blank lines
    aside); nothing else of the file is a policy statement. The cast must be exactly the
    eleven -- a missing statement is refused and an extra one is too, never silently dropped
    or invented.
    """
    source = Path(POLICY).read_text(encoding="utf-8") if text is None else text
    cuts = [match.start() for match in re.finditer(r"^## ", source, re.MULTILINE)]
    out: dict[str, str] = {}
    for start, end in zip(cuts, cuts[1:] + [len(source)]):
        heading = re.match(r"## (S\d+)\.", source[start:])
        if heading:
            out[heading.group(1)] = source[start:end].rstrip()
    if set(out) != set(STATEMENT_NAMES):
        raise ValueError(
            "docs/20 carries its policy statements as "
            f"{sorted(out)}, not exactly {list(STATEMENT_NAMES)}"
        )
    return {name: out[name] for name in STATEMENT_NAMES}


def materialize(image) -> Report:
    """The eleven policy topics and their placements into `image`, through the Python API.

    The documentation module path is made the delta way where the image lacks it (below
    `pymergetic.rxf`, which the image must carry); each topic is a `DocKind.Topic` Doc whose
    text is its statement of docs/20; each implementing object the image carries gets one
    `DocRole.Attaches` placement on its topic. One transaction: a failure rolls back to the
    image as it was. Refused when the topics are already there (one edge, said once).
    """
    texts = policy_texts()
    if image.resolve(f"{MODULE}.S1") is not None:
        raise ValueError(f"{MODULE} already carries the policy topics (materialized once)")
    _ensure_module(image)
    parent = image.handle(MODULE)
    topics: dict[str, int] = {}
    placed: dict[str, tuple[str, ...]] = {}
    absent: dict[str, tuple[str, ...]] = {}
    image.begin()
    try:
        for name in STATEMENT_NAMES:
            topic = image.doc_add(parent, name, texts[name], kind=int(DocKind.Topic))
            topics[name] = topic
            here, gone = [], []
            statement = _BY_NAME[name]
            for path in statement.places + statement.imported:
                found = image.resolve(path)
                if found is None:
                    gone.append(path)
                    continue
                image.doc_place(found, topic, int(DocRole.Attaches))
                here.append(path)
            placed[name] = tuple(sorted(here))
            absent[name] = tuple(sorted(gone))
        image.commit()
    except BaseException:
        image.rollback()
        raise
    return Report(topics, placed, absent)


def _ensure_module(image) -> None:
    """The documentation module path of docs/22, made below the package it documents."""
    if image.resolve("pymergetic.rxf") is None:
        raise ValueError("the image carries no pymergetic.rxf module for the documentation to live under")
    if image.resolve(MODULE) is not None:
        return
    if image.resolve("pymergetic.rxf.docs") is None:
        delta = (
            '<delta xmlns:rxf="urn:pymergetic:rxf">'
            '<Module name="docs" rxf:parent="pymergetic.rxf"><Module name="execution"/></Module>'
            "</delta>"
        )
    else:
        delta = (
            '<delta xmlns:rxf="urn:pymergetic:rxf">'
            '<Module name="execution" rxf:parent="pymergetic.rxf.docs"/>'
            "</delta>"
        )
    image.apply(delta)
