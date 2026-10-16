"""The fourth anchor: the execution policy as topics in the image (docs/22, "policy is documentation").

The anti-drift pins that compare strings across places -- the tutor's FACTS block and chips
against the C constants and the S1..S11 headings of docs/20 (``tests/test_tutor.py``,
``tests/test_model_doc.py``) -- gain the topic object of the image. The check is docs/22's own
sentence: **the topic exists, its text is pinned, and its placements cover the implementing
objects**. The builder that writes the topics is ``tools/native/topics.py`` (the lift of
docs/20's sections into topic payloads); everything here walks the image afterwards and pins
what is in it -- the policy texts' digests are pinned beside docs/20 (bump the two together,
never one alone), and the placements are pinned as the graph facts they are.

The tutor's ``data-cite="S5"`` chips are checked against the topics read-only: the tutor files
belong to the server lane, so this suite never writes them. The chip-to-topic reference in the
tutor's FACTS block (a chip resolving to the topic's handle) is the one half of the anchor this
suite cannot wire from here -- it is left to that lane.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import pytest
import rximport
import topics

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.kinds import DocKind, DocStatus, NodeKind, RefKind
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.server.inspector import Inspector

ROOT = Path(__file__).parents[1]
STARTER = ROOT / "starter"
POLICY = ROOT / "docs" / "20-EXECUTION-POLICY.md"
TUTOR_PAGE = ROOT / "src" / "pymergetic" / "rxftk" / "server" / "templates" / "tutor.html"
CORPUS = Path(__file__).parent / "import_corpus"
MODULE = "pymergetic.rxf.docs.execution"
NAMES = tuple(f"S{n}" for n in range(1, 12))

# The policy texts as the topics carry them: one digest per statement. Pinned with docs/20 --
# bump these two together with its S sections, never one alone.
POLICY_TEXT_SHA256 = {
    "S1": "ed1502d87f92a0674529e82d41f80767aac749fa9dc1b643604b0a88310d3324",
    "S2": "0b7874ebd1c793bdb39d85be78b12fcd16c858a0d94f1ef79a57611c87653580",
    "S3": "cfdb5599d928d5ab23ec3a244e0bfd9f82975f57d513efe1854b8b2423be90c3",
    "S4": "c998ff5d7b85d6b373237eaadd7d70345bf8b5e63cf14ab597751fab38b2eabc",
    "S5": "4bd516d7b002f289a89d086fc1721b60d6fd14ecc02c63d456b102a065a43880",
    "S6": "28f7dd35262510c73fb2099712ac6cbacb05b2373beb96f5ccc61a41899d7c53",
    "S7": "003d58b5d7ed768e5ae0ce23a012e294698fcf860c6f0916eac8bada3a903aa2",
    "S8": "6b05de11931bbcd6bc6a285963afcd4a72e21306fc0ea17304eb38a1d3c3548d",
    "S9": "c8c437dc94fe714d22550d3a510b12685e3ba106f4247b498a73fbc2f0db0ea6",
    "S10": "936760d858604ad724a0856cb0d611719718190b6aa7a27e2bd579c7023cc792",
    "S11": "025e7a1359257d8c2afecf0d916fe65632f0d986994c522d7b727de6cfe33889",
}

# The placements pinned on the starter image: topic -> the implementing objects that carry
# its DocRole.Attaches placement. The statements of the run-cell machine (S1..S5), the edge
# shim (S9) and the differential (S10) are implemented by host-side C outside the image --
# they carry no placement, and the graph says so.
PLACED = {
    "S1": frozenset(),
    "S2": frozenset(),
    "S3": frozenset(),
    "S4": frozenset(),
    "S5": frozenset(),
    "S6": frozenset(
        {
            "pymergetic.rxf.foundation.graph.run",
            "pymergetic.rxf.foundation.compile.program",
            "pymergetic.rxf.execution.basic.return",
            "pymergetic.rxf.execution.basic.refuse",
        }
    ),
    "S7": frozenset(
        {
            "pymergetic.rxf.foundation.runtime.resolve",
            "pymergetic.rxf.foundation.runtime.resolve_typed",
            "pymergetic.rxf.foundation.runtime.release",
            "pymergetic.rxf.foundation.safe.point",
            "pymergetic.rxf.foundation.graph.run",
        }
    ),
    "S8": frozenset(
        {
            "pymergetic.rxf.foundation.heap.plan_compaction",
            "pymergetic.rxf.foundation.heap.apply_move_plan",
            "pymergetic.rxf.foundation.heap.update_slot",
            "pymergetic.rxf.foundation.heap.pin",
            "pymergetic.rxf.foundation.heap.unpin",
            "pymergetic.rxf.foundation.runtime.publish",
            "pymergetic.rxf.foundation.runtime.rollback",
        }
    ),
    "S9": frozenset(),
    "S10": frozenset(),
    "S11": frozenset(
        {
            "pymergetic.rxf.foundation.graph.run",
            "pymergetic.rxf.foundation.safe.point",
            "pymergetic.rxf.execution.basic.refuse",
        }
    ),
}

# The statements nothing in the image implements, exactly (a standard nothing places on is a
# standard nothing implements -- docs/22).
NO_PLACEMENT = ("S1", "S2", "S3", "S4", "S5", "S9", "S10")


def sections() -> dict[str, str]:
    """docs/20's policy sections, sliced here on purpose: the pin is not the builder's own.

    A section runs from its ``##`` heading to the next ``##`` heading, trailing blank lines
    aside -- the bytes the topic carries are the section as docs/20 renders it.
    """
    source = POLICY.read_text(encoding="utf-8")
    heads = [
        (match.start(), match.group(1))
        for match in re.finditer(r"^## (S\d+)\.", source, re.MULTILINE)
    ]
    out: dict[str, str] = {}
    for start, name in heads:
        end = len(source)
        for later in re.finditer(r"^## ", source, re.MULTILINE):
            if later.start() > start:
                end = later.start()
                break
        out[name] = source[start:end].rstrip()
    return out


def words(image: Image, path: str) -> tuple[int, int, int]:
    """The three words of a doc payload: kind, status, codec (ground.h)."""
    import struct

    return struct.unpack("<III", bytes(image.get(image.handle(path))["payload"])[:12])


def attachments(image: Image) -> dict[str, set[str]]:
    """The DocRole.Attaches placements of the image: topic path -> the sources carrying it."""
    role = image.handle("DocRole.Attaches")
    out: dict[str, set[str]] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        source = image.path(handle)
        for target, target_role, _index, kind, _binding in image.get(handle)["refs"]:
            if kind != int(RefKind.Doc) or target_role != role:
                continue
            out.setdefault(image.path(target), set()).add(source)
    return out


@pytest.fixture(scope="module")
def materialized() -> tuple[Image, topics.Report]:
    """The starter image with the policy topics materialized (the builder, one call)."""
    image = tree.load(STARTER)
    return image, topics.materialize(image)


# ---- the topic exists, its text is pinned --------------------------------------------------------


def test_the_eleven_topics_are_the_policy_texts_of_docs_20(materialized):
    image, report = materialized
    expected = sections()
    assert sorted(expected) == sorted(NAMES)  # docs/20 carries the eleven statements
    assert sorted(image.path(child) for child in image.children(image.handle(MODULE))) == sorted(
        f"{MODULE}.{name}" for name in NAMES
    )
    for name in NAMES:
        path = f"{MODULE}.{name}"
        handle = image.handle(path)
        assert report.topics[name] == handle
        # An ordinary Doc object of the built-in Doc type, DocKind.Topic, and nothing else.
        assert image.type_name(handle) == "Doc"
        kind, status, _codec = words(image, path)
        assert (kind, status) == (int(DocKind.Topic), int(DocStatus.Open))
        assert image.get(handle)["kind"] == int(NodeKind.Data)  # carried, not run (docs/22)
        assert image.get(handle)["refs"] == []  # a topic links by placement, never by itself
        # The text is the policy text of docs/20 verbatim, and its digest is pinned.
        text = image.doc_text(handle)
        assert text == expected[name]
        assert hashlib.sha256(text.encode()).hexdigest() == POLICY_TEXT_SHA256[name]


def test_the_policy_texts_are_exactly_the_eleven_sections():
    """The lift refuses a cast that moved: a missing statement is an error, an extra one too."""
    assert sorted(topics.policy_texts()) == sorted(NAMES)
    with pytest.raises(ValueError, match="not exactly"):
        topics.policy_texts("# 20\n\n## S1. One\nbody\n")
    with pytest.raises(ValueError, match="not exactly"):
        topics.policy_texts("# 20\n\n## S1. One\nbody\n## S12. Twelve\nbody\n")


def test_a_second_materialization_is_refused_and_changes_nothing(materialized):
    image, _ = materialized
    before = image.digest(), image.export()
    with pytest.raises(ValueError, match="materialized once"):
        topics.materialize(image)
    assert (image.digest(), image.export()) == before


# ---- its placements cover the implementing objects -----------------------------------------------


def test_the_placements_cover_the_implementing_objects(materialized):
    image, report = materialized
    walked = attachments(image)
    assert set(walked) == {f"{MODULE}.{name}" for name in NAMES if PLACED[name]}
    for name in NAMES:
        statement = next(s for s in topics.STATEMENTS if s.name == name)
        # the builder's declaration, the image's graph, and the pin agree, exactly
        assert set(statement.places) == set(PLACED[name]), name
        assert set(report.placed[name]) == set(PLACED[name]), name
        assert walked.get(f"{MODULE}.{name}", set()) == set(PLACED[name]), name
        for path in PLACED[name]:
            assert image.resolve(path) is not None, f"{path} is declared but not in the image"
        # a placement is editorial and honest: one Attaches edge per implementing object
        for source in PLACED[name]:
            handle = image.handle(source)
            edges = [
                ref
                for ref in image.get(handle)["refs"]
                if ref[1] == image.handle("DocRole.Attaches")
            ]
            assert [image.path(ref[0]) for ref in edges].count(f"{MODULE}.{name}") == 1


def test_a_standard_nothing_places_on_is_a_standard_nothing_implements(materialized):
    image, report = materialized
    assert tuple(name for name in NAMES if not PLACED[name]) == NO_PLACEMENT
    walked = attachments(image)
    for name in NAMES:
        statement = next(s for s in topics.STATEMENTS if s.name == name)
        assert statement.host, f"{name} says nothing about where its implementation lives"
        if name in NO_PLACEMENT:
            assert not statement.places and not statement.imported
            assert not report.placed[name]
            assert f"{MODULE}.{name}" not in walked  # nothing pretends to implement it
    # what is declared but not in this image is said, not hidden: the corpus flips of S6
    assert report.absent["S6"] == ("obj.mp_obj_get_int_maybe", "obj.mp_obj_is_true", "outparam.pair_out")
    assert all(not report.absent[name] for name in NAMES if name != "S6")


# ---- the topics survive the text form, and the tutor cites them ----------------------------------


def test_the_topics_and_placements_survive_the_text_form(materialized):
    image, _ = materialized
    text = image.export()
    again = Image.empty()
    again.apply(text)
    assert again.check() == []
    assert again.digest() == image.digest()
    for name in NAMES:
        assert again.doc_text(again.handle(f"{MODULE}.{name}")) == image.doc_text(
            image.handle(f"{MODULE}.{name}")
        )
    assert attachments(again) == attachments(image)


def test_every_cited_section_of_the_tutor_is_a_topic(materialized):
    """The fourth anchor's page side, read-only: a chip cites a section, the section is a topic --
    and the chip is a reference to that topic (docs/22): its href resolves the topic's own path."""
    image, _ = materialized
    page = TUTOR_PAGE.read_text()
    cited = {f"S{number}" for number in re.findall(r'data-cite="S(\d+)"', page)}
    assert cited, "the tutor cites no section of docs/20"
    linked = {
        f"S{number}"
        for number in re.findall(r'href="/\?path=pymergetic\.rxf\.docs\.execution\.S(\d+)"', page)
    }
    assert linked == cited, "every chip must link to its topic (chips without topic href: a reference that resolves nowhere)"
    expected = sections()
    inspector = Inspector(image)
    for name in sorted(cited, key=lambda s: int(s[1:])):
        path = f"{MODULE}.{name}"
        assert image.resolve(path) is not None, f"the tutor cites {name}, which is no topic"
        assert image.doc_text(image.handle(path)) == expected[name]
        rows = [row["source"] for row in inspector.placements(path) if row["role"] == "DocRole.Attaches"]
        assert sorted(rows) == sorted(PLACED[name])


def test_an_imported_corpus_function_carries_its_policy_placement():
    """The corpus flips of S6 (docs/20's dispatch and out-cell passages) are placed where an
    import put them: the builder attaches declared implementers wherever the image carries
    them, and the out-parameter idiom of tests/import_corpus/outparam.c is one."""
    if shutil.which(rximport.CLANG) is None:
        pytest.skip(f"{rximport.CLANG} is not installed")
    image = tree.load(STARTER)
    known = rximport.import_into(
        image, rximport.compile_source(CORPUS / "qstr.c"), module="qstr",
        only={"qstr_hash", "qstr_port_boot", "qstr_init"},
    )  # fmt: skip
    rximport.import_into(
        image, rximport.compile_source(CORPUS / "outparam.c"), module="outparam",
        only={"outparam_port_boot", "pair_out", "sum_pair_card"}, known=known,
    )  # fmt: skip
    report = topics.materialize(image)
    assert image.check() == []
    assert "outparam.pair_out" in report.placed["S6"]
    assert "outparam.pair_out" in attachments(image)[f"{MODULE}.S6"]
    # the starter's implementers are placed the same in an imported image ...
    for name in NAMES:
        assert set(report.placed[name]) == set(PLACED[name]) | (
            {"outparam.pair_out"} if name == "S6" else set()
        )
    # ... and what the image still does not carry is the rest of the S6 flips (obj.c's closure,
    # which tests/test_import.py drives) -- said, not hidden.
    assert report.absent["S6"] == ("obj.mp_obj_get_int_maybe", "obj.mp_obj_is_true")
